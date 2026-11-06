"""Per-document feature extraction, dedupe, availability and the parquet output (05 §7.1-7.2).

Deterministic and rule-based: no network, no LLM, no clock, no randomness. ``extract_document`` is a pure function of
(pages, doc_type, doc_date, period_label). ``build`` adds the catalog dedupe, the incremental cache keyed by
(pdf_sha256, doc_date, lexicon hash, extractor version), the §7.2 availability rule and the output files. It writes
only under the ``out_dir`` it is given (``IDEATION_DATA_DIR/text``).
"""

from __future__ import annotations

import math
import re
import statistics
from collections.abc import Callable, Iterable
from concurrent.futures import ProcessPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd

from ideation.pit import lags
from ideation.text import clean
from ideation.text import lexicon as lex
from ideation.text.reader import read_pages

EXTRACT_VERSION: Final = "pt1.1"
"""Bump when any rule in clean.py / this file changes: it is part of the cache key."""
SOURCE_PRIORITY: Final = ("bse", "nse", "company_site", "other", "wayback", "commoncrawl", "tijori")
EXCHANGE_SOURCES: Final = frozenset({"bse", "nse"})
CATS: Final = ("pos", "neg", "unc", "lit", "g_up", "g_down", "capex")
NAN: Final = float("nan")

_GUIDE_RE = re.compile(
    r"(revenue|sales|volume|ebitda|margin)[^.]{0,40}?(\d{1,2}(?:\.\d)?)(?:\s*(?:-|to)\s*(\d{1,2}(?:\.\d)?))?"
    r"\s*(?:%|per\s*cent|percent)"
)
_GUIDE_VERB_RE = re.compile(r"\b(guid(?:e|es|ed|ing|ance)|expect\w*|target\w*|aim\w*)\b")


# ── counting ─────────────────────────────────────────────────────────────────────────────────────────────────
def count_tokens(segments: Iterable[list[str]]) -> dict[str, int]:
    """Lexicon hits per category. A negator within 3 tokens before a hit flips pos<->neg and g_up<->g_down and
    cancels capex; unc and lit are never flipped. Negation does not cross a sentence (one segment per sentence)."""
    c = dict.fromkeys(CATS, 0)
    c["n"] = 0
    flip = {"pos": "neg", "neg": "pos", "g_up": "g_down", "g_down": "g_up"}
    tone, index, window = lex.TONE_WORDS, lex.PHRASE_INDEX, lex.NEGATION_WINDOW
    for toks in segments:
        c["n"] += len(toks)
        last_neg = -100
        for i, t in enumerate(toks):
            negated = 0 < i - last_neg <= window
            cats = tone.get(t)
            if cats:
                for cat in cats:
                    c[flip[cat] if negated and cat in flip else cat] += 1
            if t in index:
                for p in index[t]:
                    if tuple(toks[i : i + len(p)]) == p:
                        cat = lex.PHRASES[p]
                        if cat == "capex":
                            c["capex"] += 0 if negated else 1
                        else:
                            c[flip[cat] if negated else cat] += 1
                        break
            if lex.is_negator(t):
                last_neg = i
    return c


def rate_features(c: dict[str, int], prefix: str = "") -> dict[str, float]:
    n = c["n"]

    def r(k: str) -> float:
        return 1000.0 * c[k] / n if n else NAN

    return {
        f"{prefix}tone_net": (c["pos"] - c["neg"]) / (c["pos"] + c["neg"] + 1) if n else NAN,
        f"{prefix}pos_r": r("pos"),
        f"{prefix}neg_r": r("neg"),
        f"{prefix}unc_r": r("unc"),
        f"{prefix}lit_r": r("lit"),
        f"{prefix}gup_r": r("g_up"),
        f"{prefix}gdn_r": r("g_down"),
        f"{prefix}guide_net": (c["g_up"] - c["g_down"]) / (c["g_up"] + c["g_down"] + 1) if n else NAN,
        f"{prefix}capex_r": r("capex"),
    }


def numeric_guidance(text: str) -> tuple[float, int]:
    """(median midpoint %, hit count) of ``revenue|sales|volume|ebitda|margin ... N[-M]%`` in a sentence that also
    carries a guidance verb. NaN, 0 when none. ``text`` is lowercase, whitespace-collapsed."""
    mids: list[float] = []
    for m in _GUIDE_RE.finditer(text):
        start = text.rfind(". ", 0, m.start()) + 1
        end = text.find(". ", m.end())
        sent = text[start : end if end >= 0 else len(text)]
        if not _GUIDE_VERB_RE.search(sent):
            continue
        lo = float(m.group(2))
        hi = float(m.group(3)) if m.group(3) else lo
        mids.append((lo + hi) / 2)
    return (statistics.median(mids), len(mids)) if mids else (NAN, 0)


# ── one document ─────────────────────────────────────────────────────────────────────────────────────────────
def extract_document(
    pages: list[str], doc_type: str, doc_date: date, period_label: str | None = None
) -> dict[str, Any]:
    """All text-derived columns of one document (no availability: that needs the lake's event dates)."""
    filed, filed_src = clean.parse_filed_date(pages, doc_date)
    q_end, q_src = clean.parse_q_end("\n".join(pages[:3]), period_label, doc_date)
    body_pages = clean.strip_cover(pages)
    body = "\n".join(body_pages)
    prep_raw, qa_raw = clean.split_qa(body)
    prep_s = clean.clean_sentences(prep_raw)
    qa_s = clean.clean_sentences(qa_raw) if qa_raw is not None else []
    prep_t, qa_t = clean.tokenize_sentences(prep_s), clean.tokenize_sentences(qa_s)
    c_prep, c_qa = count_tokens(prep_t), count_tokens(qa_t)
    c_all = {k: c_prep[k] + c_qa[k] for k in c_prep}
    qa_found = qa_raw is not None
    qa_ok = qa_found and c_prep["n"] >= clean.QA_MIN_TOKENS and c_qa["n"] >= clean.QA_MIN_TOKENS
    out: dict[str, Any] = {
        "filed_date": filed,
        "filed_src": filed_src,
        "q_end": q_end,
        "q_end_src": q_src,
        "n_pages": len(pages),
        "n_cover_pages": len(pages) - len(body_pages),
        "qa_found": qa_found,
        "qa_ok": int(qa_ok),
        "n_tok": c_all["n"],
        "n_tok_prep": c_prep["n"],
        "n_tok_qa": c_qa["n"],
        **rate_features(c_all),
    }
    for prefix, c in (("prep_", c_prep), ("qa_", c_qa)):
        out.update(rate_features(c, prefix) if qa_ok else {k: NAN for k in rate_features(c, prefix)})
    out["qa_minus_prep_tone"] = out["qa_tone_net"] - out["prep_tone_net"] if qa_ok else NAN
    guide_text = " ".join(prep_s if qa_found else [*prep_s, *qa_s])
    out["guide_pct_mid"], out["n_guide_pct"] = numeric_guidance(guide_text)
    return out


NAN_ROW_KEYS: Final = tuple(
    extract_document(["x"], "concall_transcript", date(2020, 1, 1)).keys()
)  # column order for failed reads


def _work(args: tuple[str, str, str, date, str | None, str]) -> dict[str, Any]:
    root, text_path, doc_type, doc_date, period_label, key = args
    try:
        pages = read_pages(Path(root), text_path)
        row = extract_document(pages, doc_type, doc_date, period_label)
        row["read_ok"] = True
        row["read_err"] = ""
    except (OSError, ValueError, KeyError) as exc:
        row = dict.fromkeys(NAN_ROW_KEYS, None)
        row["read_ok"], row["read_err"] = False, f"{type(exc).__name__}: {exc}"[:200]
    row["cache_key"] = key
    return row


# ── catalog ──────────────────────────────────────────────────────────────────────────────────────────────────
def dedupe_catalog(cat: pd.DataFrame) -> pd.DataFrame:
    """One row per (company_id, doc_type, doc_date): source priority bse > nse > company_site > other > wayback >
    commoncrawl > tijori, then larger char_count, then pdf_sha256 order (05 §7.1). Deterministic."""
    rank = {s: i for i, s in enumerate(SOURCE_PRIORITY)}
    df = cat.dropna(subset=["doc_date", "text_path"]).copy()
    df["_pri"] = df["source"].map(rank).fillna(len(rank)).astype(int)
    df["_cc"] = -df["char_count"].fillna(0)
    df["_sha"] = df["pdf_sha256"].fillna("")
    df = df.sort_values(["company_id", "doc_type", "doc_date", "_pri", "_cc", "_sha", "text_path"], kind="stable")
    df = df.drop_duplicates(["company_id", "doc_type", "doc_date"], keep="first")
    return df.drop(columns=["_pri", "_cc", "_sha"]).reset_index(drop=True)


def cache_key(sha: str | None, text_path: str, doc_date: date) -> str:
    return f"{sha or 'path:' + text_path}|{doc_date.isoformat()}"


# ── availability (05 §7.2) ───────────────────────────────────────────────────────────────────────────────────
def first_of_next_month(d: date) -> date:
    return date(d.year + (d.month == 12), d.month % 12 + 1, 1)


def available_from(
    doc_date: date,
    source: str | None,
    filed_date: date | None,
    q_end: date,
    event_dates: Iterable[date],
    *,
    fy_month: int = 3,
    today: date,
) -> date:
    """``max`` of: first day of the month after ``doc_date`` (it is month-granular); ``filed_date + 1``; the matched
    results event + 1 (reported events in [doc_date - 75 d, doc_date + 45 d] matching ``q_end``, 02 §1.2); and, for
    sources other than bse/nse, the statutory results deadline of ``q_end`` + 1. Never before the quarter's results.
    ``today`` is the data observation date (events after it are schedules)."""
    cands = [first_of_next_month(doc_date)]
    if filed_date is not None:
        cands.append(filed_date + timedelta(days=1))
    near = [e for e in event_dates if doc_date - timedelta(days=75) <= e <= doc_date + timedelta(days=45)]
    ev = lags.match_event(q_end, near, today=today, period_type="Q", fy_month=fy_month) if near else None
    if ev is not None:
        cands.append(ev + timedelta(days=1))
    if (source or "").lower() not in EXCHANGE_SOURCES:
        cands.append(lags.fact_available_from(q_end, "Q", fy_month, None, today=today))
    return max(cands)


# ── build ────────────────────────────────────────────────────────────────────────────────────────────────────
def output_paths(out_dir: Path) -> tuple[Path, Path]:
    core = f"core_{lex.LEX_V1}_{lex.lexicon_hash()}_{EXTRACT_VERSION}.parquet"
    return out_dir / f"doc_features_{lex.LEX_V1}.parquet", out_dir / "cache" / core


def build(
    catalog: pd.DataFrame,
    events: pd.DataFrame,
    *,
    documents_root: Path,
    out_dir: Path,
    fy_months: dict[int, int] | None = None,
    workers: int = 4,
    use_cache: bool = True,
    limit: int | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Dedupe, extract (cache-aware), add availability, write the output parquet. Returns (frame, stats)."""
    uniq = dedupe_catalog(catalog)
    stats: dict[str, Any] = {
        "catalog_rows": len(catalog),
        "deduped": uniq.groupby("doc_type").size().to_dict(),
        "dropped_dupes": len(catalog.dropna(subset=["doc_date", "text_path"])) - len(uniq),
    }
    if limit:
        uniq = uniq.head(limit)
    uniq["cache_key"] = [
        cache_key(s, p, d) for s, p, d in zip(uniq["pdf_sha256"], uniq["text_path"], uniq["doc_date"], strict=True)
    ]
    out_path, cache_path = output_paths(out_dir)
    cached = pd.DataFrame()
    if use_cache and cache_path.exists():
        cached = pd.read_parquet(cache_path)
    have = set(cached["cache_key"]) if len(cached) else set()
    todo = uniq[~uniq["cache_key"].isin(have) & ~uniq["cache_key"].duplicated()]
    stats["from_cache"] = int(uniq["cache_key"].isin(have).sum())
    stats["extracted"] = len(todo)
    new_rows: list[dict[str, Any]] = []
    jobs = [
        (str(documents_root), r.text_path, r.doc_type, r.doc_date, r.period_label, r.cache_key)
        for r in todo.itertuples(index=False)
    ]
    if jobs:
        if workers > 1:
            with ProcessPoolExecutor(max_workers=workers) as ex:
                for i, row in enumerate(ex.map(_work, jobs, chunksize=32), 1):
                    new_rows.append(row)
                    if progress and i % 2000 == 0:
                        progress(i, len(jobs))
        else:
            for i, job in enumerate(jobs, 1):
                new_rows.append(_work(job))
                if progress and i % 2000 == 0:
                    progress(i, len(jobs))
    fresh = pd.DataFrame(new_rows)
    core = pd.concat([cached, fresh], ignore_index=True) if len(fresh) else cached
    if len(fresh):
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        core.drop_duplicates("cache_key").to_parquet(cache_path, index=False)
    core = core.drop_duplicates("cache_key")
    df = uniq.merge(core, on="cache_key", how="left", validate="many_to_one")
    ev_map = {int(cid): sorted(g) for cid, g in events[events["has_eps_reported"]].groupby("company_id")["event_date"]}
    today = max((e for v in ev_map.values() for e in v), default=date(1970, 1, 1))
    fym = fy_months or {}
    df["available_from"] = [
        available_from(
            r.doc_date, r.source, None if pd.isna(r.filed_date) else r.filed_date, r.q_end,
            ev_map.get(int(r.company_id), ()), fy_month=fym.get(int(r.company_id), 3), today=today,
        )
        if r.read_ok is True or r.read_ok == 1
        else None
        for r in df.itertuples(index=False)
    ]  # fmt: skip
    df["lex_ver"] = lex.LEX_V1
    df["lex_hash"] = lex.lexicon_hash()
    df["extract_ver"] = EXTRACT_VERSION
    df = df.drop(columns=["cache_key"]).sort_values(["company_id", "doc_type", "doc_date"]).reset_index(drop=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not limit:
        df.to_parquet(out_path, index=False)
    stats["unreadable"] = int((df["read_ok"] != True).sum())  # noqa: E712
    stats["out_path"] = str(out_path)
    return df, stats


# ── reporting ────────────────────────────────────────────────────────────────────────────────────────────────
def coverage_by_year(df: pd.DataFrame) -> pd.DataFrame:
    d = df[df["read_ok"] == True].copy()  # noqa: E712
    d["year"] = pd.to_datetime(d["doc_date"]).dt.year
    d["is_bse"] = d["source"].eq("bse")
    d["filed"] = d["filed_date"].notna()
    rows = []
    for (typ, yr), g in d.groupby(["doc_type", "year"]):
        bse = g[g["is_bse"]]
        rows.append(
            {
                "doc_type": typ, "year": int(yr), "docs": len(g), "companies": g["company_id"].nunique(),
                "bse_docs": len(bse), "filed_parsed": float(bse["filed"].mean()) if len(bse) else NAN,
                "qa_ok": float(g["qa_ok"].mean()), "mean_tok": float(g["n_tok"].mean()),
            }
        )  # fmt: skip
    return pd.DataFrame(rows)


def add_deltas(df: pd.DataFrame) -> pd.DataFrame:
    """Transcripts with ``d_tone`` / ``d_guide`` vs the latest transcript 60-400 days earlier (05 §7.3 diagnostic
    only; the PIT features themselves are PT2's)."""
    t = df[(df["doc_type"] == "concall_transcript") & (df["read_ok"] == True)].copy()  # noqa: E712
    t["dd"] = pd.to_datetime(t["doc_date"])
    t = t.sort_values(["company_id", "dd"])
    d_tone = np.full(len(t), np.nan)
    d_guide = np.full(len(t), np.nan)
    pos = 0
    for _, g in t.groupby("company_id", sort=False):
        days = g["dd"].to_numpy("datetime64[D]").astype("int64")
        tone, guide = g["tone_net"].to_numpy(), g["guide_net"].to_numpy()
        for i in range(len(g)):
            lo = np.searchsorted(days, days[i] - 400, side="left")
            hi = np.searchsorted(days, days[i] - 60, side="right")
            if hi > lo:
                d_tone[pos + i] = tone[i] - tone[hi - 1]
                d_guide[pos + i] = guide[i] - guide[hi - 1]
        pos += len(g)
    t["d_tone"], t["d_guide"] = d_tone, d_guide
    return t


def tone_table(t: pd.DataFrame) -> pd.DataFrame:
    """Mean tone_net / d_tone by the calendar quarter the call discusses (``q_end``)."""
    t = t.copy()
    t["q"] = pd.to_datetime(t["q_end"]).dt.to_period("Q").astype(str)
    g = t.groupby("q")
    out = pd.DataFrame(
        {
            "calls": g.size(), "mean_tone_net": g["tone_net"].mean(), "mean_d_tone": g["d_tone"].mean(),
            "share_d_tone_neg": g["d_tone"].apply(lambda s: float((s.dropna() < 0).mean()) if s.notna().any() else NAN),
            "n_d_tone": g["d_tone"].count(), "mean_d_guide": g["d_guide"].mean(),
        }
    )  # fmt: skip
    return out.reset_index()


def nan_to_none(x: float) -> float | None:
    return None if isinstance(x, float) and math.isnan(x) else x
