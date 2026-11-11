# ruff: noqa: E501
"""Annual-report text extraction (05 §12 item 2, PT3). Deterministic, rule-based: no network, no LLM, no clock.

One row per (company_id, fiscal_year). ``extract_annual`` is a pure function of the page texts: it finds the
Management Discussion & Analysis (MD&A), the Independent Auditor's Report(s), the related-party and contingent-liability
notes by heading lines, scores the MD&A with ``LEX_AR_V1`` (rates per 1,000 tokens), flags the auditor's report
(qualified / adverse / disclaimer, emphasis of matter, going-concern uncertainty, key-audit-matter count), measures
related-party and contingent-liability intensity, and applies a document-quality filter (word count, garble rate).
Features of a section that was not found are NaN (``mda_ok`` / ``aud_ok`` = 0): nothing is filled.

Availability (conservative, ``AVAIL_LAG_DAYS`` after the fiscal-year end, or the parsed date if later) is computed in
``build`` from the parsed cover-letter date or AGM date, not in the per-document function, so the lag can be changed
without re-reading 29k files. Only text *rates and flags* are written; no text is stored. Output files live under
``IDEATION_DATA_DIR/text`` and are never committed.
"""

from __future__ import annotations

import json
import math
import re
from calendar import monthrange
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Final

import pandas as pd

from ideation.text import clean
from ideation.text import lexicon as lex1
from ideation.text import lexicon_ar as lx
from ideation.text.reader import read_pages

EXTRACT_VERSION: Final = "pt3.1"
"""Bump when any rule here changes: it is part of the cache key."""
AVAIL_LAG_DAYS: Final = 180
"""Fixed lag after the fiscal-year end (05 §12: 'a fixed lag after FY end'). SEBI LODR: AR due ~ AGM, within 6 months."""
SOURCE_PRIORITY: Final = ("bse", "nse", "company_site", "other", "wayback", "commoncrawl", "tijori")
NAN: Final = float("nan")
MIN_WORDS: Final = 8000
MAX_GARBLE: Final = 0.02
MAX_FRAG: Final = 0.25
MIN_MDA_TOKENS: Final = 300
MDA_CAP: Final = 100_000
AUD_CAP: Final = 60_000
AUD_MIN: Final = 6_000
NOTE_CAP: Final = 40_000
FILED_WINDOW: Final = (30, 540)
AGM_WINDOW: Final = (60, 540)
"""Accepted [FY end + lo, FY end + hi] days for a parsed cover-letter / AGM date."""

_M = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_MI = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
_D1 = re.compile(rf"\b{_M}\.?\s*(\d{{1,2}})(?:st|nd|rd|th)?\s*,?\s*(20\d\d)\b", re.I)
_D2 = re.compile(rf"\b(\d{{1,2}})\s*(?:st|nd|rd|th)?\s*(?:day\s+of\s+)?{_M}\.?\s*,?\s*(20\d\d)\b", re.I)
_D3 = re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-](20\d\d)\b")
_FRAG = re.compile(r"(?<!\S)[b-hj-zB-HJ-Z](?!\S)")
_WS = re.compile(r"\s+")
_SENT = re.compile(r"(?<=[.!?])\s+")
_NOTICE = re.compile(r"notice\s+is\s+hereby\s+given", re.I)
_HELD = re.compile(r"(?:will|shall|is\s+to|are\s+to)\s+be\s+held", re.I)
_YEAR_ENDED = re.compile(r"(?:year|period)\s+ended(?:\s+on)?\s*:?\s*", re.I)
_H = r"[^\n.]{0,50}$"
MDA_START: Final = re.compile(
    r"^[ \t]*(?:annexure[^\n]{0,20}?[-–:]\s*)?(?:the\s+)?(?:report\s+on\s+)?management(?:’s|'s)?\s+discussion\s*(?:and|&)\s*"
    r"analys[ie]s(?:\s+(?:report|statement))?" + _H, re.M)  # fmt: skip
SECTION_END: Final = re.compile(
    r"^[ \t]*(?:report\s+on\s+corporate\s+governance|corporate\s+governance\s+report|report\s+of\s+corporate\s+governance|"
    r"directors?[’']?s?[’']?\s+report|board[’']?s?[’']?\s+report|report\s+of\s+(?:the\s+)?(?:board|directors)|"
    r"business\s+responsibility(?:\s+and\s+sustainability)?\s+report|independent\s+auditors?[’']?s?[’']?\s+report|"
    r"auditors?[’']?s?[’']?\s+report|secretarial\s+audit\s+report|notice\s+of\s+(?:the\s+)?(?:\w+\s+)?annual\s+general\s+meeting|"
    r"(?:standalone\s+|consolidated\s+)?balance\s+sheet\s+as\s+(?:at|on))" + _H, re.M)  # fmt: skip
AUD_START: Final = re.compile(
    r"^[ \t]*(?:independent\s+)?auditors?[’']?s?[’']?\s+report" + _H, re.M)
AUD_END: Final = re.compile(
    r"^[ \t]*(?:as\s+per\s+our\s+(?:separate\s+)?report\s+of\s+even\s+date|significant\s+accounting\s+policies|"
    r"notes\s+(?:to|forming\s+part\s+of)\s+(?:the\s+)?(?:standalone\s+|consolidated\s+)?financial\s+statements|"
    r"(?:standalone\s+|consolidated\s+)?balance\s+sheet\b(?:\s+as\s+(?:at|on))?|"
    r"(?:standalone\s+|consolidated\s+)?statements?\s+of\s+(?:profit|assets|income)[^\n.]{0,40})" + _H, re.M)  # fmt: skip
_NOTE_HEAD = r"^[ \t]*(?:note\s*)?\(?\d{1,3}(?:\.\d{1,2})?\)?[.:)]?\s+"
RPT_NOTE: Final = re.compile(_NOTE_HEAD + r"related\s+part(?:y|ies)\s+(?:disclosures?|transactions?)" + _H, re.M)
CL_NOTE: Final = re.compile(_NOTE_HEAD + r"contingent\s+liabilit(?:y|ies)" + _H, re.M)
NOTE_ANY: Final = re.compile(_NOTE_HEAD + r"[a-z][a-z ,&'’/()-]{3,80}$", re.M)
_AUD = {k: re.compile(v, re.M) for k, v in lx.AUDITOR_PATTERNS.items()}
_MDA_WORDS = re.compile(r"\b(economy|industry|global|outlook|overview|opportunit\w*|threats?|growth|demand|segment|sector)\b")
_AUD_BODY = re.compile(r"we\s+have\s+audited|true\s+and\s+fair|basis\s+for\s+(?:qualified\s+)?opinion")
_RPT_N = re.compile(r"related\s+part(?:y|ies)")
_CL_N = re.compile(r"contingent\s+liabilit")
_PUNCT_OK = tuple("’‘“”–—•₹…·°®©™‐−×±½¼¾§€£\u00a0")


# ── dates ────────────────────────────────────────────────────────────────────────────────────────────────────
def _mk(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def dates_in(text: str) -> list[tuple[int, date]]:
    """Every written date in ``text`` as (offset, date), by offset (day-first for numeric forms)."""
    out: list[tuple[int, date]] = []
    for m in _D1.finditer(text):
        dt = _mk(int(m.group(3)), _MI[m.group(1)[:3].lower()], int(m.group(2)))
        if dt:
            out.append((m.start(), dt))
    for m in _D2.finditer(text):
        dt = _mk(int(m.group(3)), _MI[m.group(2)[:3].lower()], int(m.group(1)))
        if dt:
            out.append((m.start(), dt))
    for m in _D3.finditer(text):
        dt = _mk(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        if dt:
            out.append((m.start(), dt))
    return sorted(out)


def cover_dates(front: str, limit: int = 6) -> list[date]:
    """Dates on the cover letter (first pages) that are not introduced by a period-end / meeting context."""
    res: list[date] = []
    for off, dt in dates_in(front):
        if clean._NOT_FILED_RE.search(front[max(0, off - 40) : off].replace("\n", " ")):
            continue
        if dt not in res:
            res.append(dt)
        if len(res) >= limit:
            break
    return res


def agm_dates(full: str, limit: int = 4) -> list[date]:
    """Meeting dates written in 'Notice is hereby given ... will be held on <date>'."""
    res: list[date] = []
    for m in list(_NOTICE.finditer(full))[:6]:
        win = full[m.end() : m.end() + 900]
        h = _HELD.search(win)
        if not h:
            continue
        for _, dt in dates_in(win[h.end() : h.end() + 300])[:1]:
            if dt not in res:
                res.append(dt)
        if len(res) >= limit:
            break
    return res


def year_end_dates(full: str, limit: int = 4) -> list[date]:
    """Dates that follow 'year ended' in the first ~300k chars and occur at least twice (latest first)."""
    c: Counter[date] = Counter()
    head = full[:300_000]
    for m in _YEAR_ENDED.finditer(head):
        for _, dt in dates_in(head[m.end() : m.end() + 40])[:1]:
            c[dt] += 1
    return sorted((d for d, n in c.items() if n >= 2), reverse=True)[:limit]


# ── sections ─────────────────────────────────────────────────────────────────────────────────────────────────
def _first_after(rx: re.Pattern[str], low: str, pos: int, stop: int) -> int:
    m = rx.search(low, pos, stop)
    return m.start() if m else stop


def mda_span(low: str) -> tuple[int, int] | None:
    """(start, end) of the longest span that starts at an MD&A heading line and runs to the next major section
    heading (or ``MDA_CAP`` chars). Table-of-contents hits give short spans and lose to the real section."""
    best: tuple[int, int] | None = None
    for m in MDA_START.finditer(low):
        s = m.start()
        if len(_MDA_WORDS.findall(low[m.end() : m.end() + 3000])) < 4:
            continue  # table-of-contents line or a cross-reference, not the section
        stop = min(len(low), s + MDA_CAP)
        e = _first_after(SECTION_END, low, min(stop, m.end() + 300), stop)
        if best is None or e - s > best[1] - best[0]:
            best = (s, e)
    return best if best and best[1] - best[0] >= 2000 else None


def auditor_spans(low: str, max_spans: int = 3) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    last_end = -1
    for m in AUD_START.finditer(low):
        s = m.start()
        if s < last_end or not _AUD_BODY.search(low, m.end(), m.end() + 6000):
            continue  # table-of-contents line or a cross-reference: a real report opens with 'we have audited'
        stop = min(len(low), s + AUD_CAP)
        e = _first_after(AUD_END, low, min(stop, s + AUD_MIN), stop)
        if e - s >= AUD_MIN:  # (an end heading earlier than AUD_MIN chars is a wrapped sentence fragment)
            spans.append((s, e))
            last_end = e
            if len(spans) >= max_spans:
                break
    return spans


def note_span(rx: re.Pattern[str], low: str) -> tuple[int, int] | None:
    """Longest region from a numbered-note heading matching ``rx`` to the next numbered note heading (cap)."""
    best: tuple[int, int] | None = None
    for m in rx.finditer(low):
        stop = min(len(low), m.end() + NOTE_CAP)
        n = NOTE_ANY.search(low, m.end() + 200, stop)
        e = n.start() if n else stop
        if best is None or e - m.start() > best[1] - best[0]:
            best = (m.start(), e)
    return best if best and best[1] - best[0] >= 600 else None


# ── counting ─────────────────────────────────────────────────────────────────────────────────────────────────
def sentences_tokens(text: str, *, drop_safe: bool = True) -> list[list[str]]:
    flat = _WS.sub(" ", text.replace("’", "'").replace("‘", "'")).strip()
    sents = [s for s in _SENT.split(flat) if s]
    if drop_safe:
        sents = clean._strip_safe_harbour(sents)
    return [t for s in sents if (t := clean.TOKEN_RE.findall(s))]


def count_ar(segments: list[list[str]]) -> dict[str, int]:
    """LEX_AR_V1 hits per category; a negator within 3 tokens flips pos<->neg (same sentence)."""
    c = dict.fromkeys(lx.CATS, 0)
    c["n"] = 0
    words, win = lx.TONE_WORDS, lx.NEGATION_WINDOW
    for toks in segments:
        c["n"] += len(toks)
        last_neg = -100
        for i, t in enumerate(toks):
            cats = words.get(t)
            if cats:
                negated = 0 < i - last_neg <= win
                for cat in cats:
                    c[lx.FLIP[cat] if negated and cat in lx.FLIP else cat] += 1
            if t in lex1.NEGATORS or t.endswith("n't"):
                last_neg = i
    return c


def rates(c: dict[str, int], prefix: str) -> dict[str, float]:
    n = c["n"]

    def r(k: str) -> float:
        return 1000.0 * c[k] / n if n else NAN

    return {
        f"{prefix}tone_net": (c["pos"] - c["neg"]) / (c["pos"] + c["neg"] + 1) if n else NAN,
        f"{prefix}pos_r": r("pos"), f"{prefix}neg_r": r("neg"), f"{prefix}unc_r": r("unc"),
        f"{prefix}lit_r": r("lit"), f"{prefix}risk_r": r("risk"), f"{prefix}dist_r": r("dist"),
    }  # fmt: skip


RATE_KEYS: Final = tuple(rates(dict.fromkeys((*lx.CATS, "n"), 0), "x_"))


def quality_stats(full: str, pages: list[str]) -> dict[str, Any]:
    words = full.split()
    n_chars = max(len(full), 1)
    non_ascii = len(full) - len(full.encode("ascii", "ignore")) - sum(full.count(ch) for ch in _PUNCT_OK)
    frag = len(_FRAG.findall(full))  # lone letters other than a/i (spaced-out or broken text)
    n_w = len(words)
    return {
        "n_pages": len(pages), "n_chars": len(full), "n_words": n_w,
        "garble_rate": non_ascii / n_chars, "frag_rate": frag / n_w if n_w else 1.0,
    }  # fmt: skip


def quality_ok(q: dict[str, Any]) -> bool:
    return bool(q["n_words"] >= MIN_WORDS and q["garble_rate"] <= MAX_GARBLE and q["frag_rate"] <= MAX_FRAG)


def aud_flags(text: str) -> dict[str, Any]:
    qual = bool(
        _AUD["qual_head"].search(text) or _AUD["qual_basis"].search(text) or _AUD["qual_except"].search(text)
    )
    return {
        "aud_qual": int(qual),
        "aud_eom": int(bool(_AUD["eom"].search(text))),
        "aud_gc": int(bool(_AUD["gc_head"].search(text))),
        "aud_other_matter": int(bool(_AUD["other_matter"].search(text))),
        "aud_kam": int(bool(_AUD["kam_head"].search(text))),
        "aud_kam_n": min(len(_AUD["kam_resp"].findall(text)), 20),
    }


# ── one document ─────────────────────────────────────────────────────────────────────────────────────────────
def _js(ds: list[date]) -> str:
    return json.dumps([d.isoformat() for d in ds])


def extract_annual(pages: list[str]) -> dict[str, Any]:
    """All text-derived columns of one annual report (no availability: that needs the fiscal-year end)."""
    full = "\n".join(pages)
    low = full.lower()
    q = quality_stats(full, pages)
    out: dict[str, Any] = {**q, "doc_ok": int(quality_ok(q))}
    out["cover_dates"] = _js(cover_dates("\n".join(pages[:2])))
    out["agm_dates"] = _js(agm_dates(full))
    out["fy_cands"] = _js(year_end_dates(full))
    # MD&A
    sp = mda_span(low)
    mc = {**dict.fromkeys(lx.CATS, 0), "n": 0}
    if sp:
        mc = count_ar(sentences_tokens(full[sp[0] : sp[1]]))
    mda_ok = bool(sp) and mc["n"] >= MIN_MDA_TOKENS
    out["mda_ok"] = int(mda_ok)
    out["mda_chars"] = (sp[1] - sp[0]) if sp else 0
    out["mda_n_tok"] = mc["n"]
    r = rates(mc, "mda_")
    out.update(r if mda_ok else dict.fromkeys(r, NAN))
    # auditor's report
    aspans = auditor_spans(low)
    out["aud_ok"] = int(bool(aspans))
    out["aud_chars"] = sum(e - s for s, e in aspans)
    flags = aud_flags("\n".join(low[s:e] for s, e in aspans)) if aspans else dict.fromkeys(
        ("aud_qual", "aud_eom", "aud_gc", "aud_other_matter", "aud_kam", "aud_kam_n"), None)
    out.update(flags)
    out["aud_flag"] = (flags["aud_qual"] + flags["aud_eom"] + flags["aud_gc"]) if aspans else None
    # related parties and contingent liabilities (document-wide mention rates + note sizes)
    nw = q["n_words"]
    out["rpt_r"] = 1000.0 * len(_RPT_N.findall(low)) / nw if nw else NAN
    out["cl_r"] = 1000.0 * len(_CL_N.findall(low)) / nw if nw else NAN
    rs, cs = note_span(RPT_NOTE, low), note_span(CL_NOTE, low)
    out["rpt_note_share"] = (rs[1] - rs[0]) / max(len(low), 1) if rs else NAN
    out["cl_note_share"] = (cs[1] - cs[0]) / max(len(low), 1) if cs else NAN
    if cs:
        cc = count_ar(sentences_tokens(full[cs[0] : cs[1]], drop_safe=False))
        out["cl_lit_r"] = 1000.0 * cc["lit"] / cc["n"] if cc["n"] else NAN
    else:
        out["cl_lit_r"] = NAN
    return out


NAN_ROW_KEYS: Final = tuple(extract_annual(["x"]).keys())


def _work(args: tuple[str, str, str]) -> dict[str, Any]:
    root, text_path, key = args
    try:
        row = extract_annual(read_pages(Path(root), text_path))
        row["read_ok"], row["read_err"] = True, ""
    except (OSError, ValueError, KeyError) as exc:
        row = dict.fromkeys(NAN_ROW_KEYS, None)
        row["read_ok"], row["read_err"] = False, f"{type(exc).__name__}: {exc}"[:200]
    row["cache_key"] = key
    return row


# ── catalog / availability ───────────────────────────────────────────────────────────────────────────────────
def dedupe_annual(cat: pd.DataFrame) -> pd.DataFrame:
    """One row per (company_id, fiscal_year): source priority bse > nse > ..., then larger char_count, then sha."""
    rank = {s: i for i, s in enumerate(SOURCE_PRIORITY)}
    df = cat.dropna(subset=["fiscal_year", "text_path"]).copy()
    df["fiscal_year"] = df["fiscal_year"].astype(int)
    df["_pri"] = df["source"].map(rank).fillna(len(rank)).astype(int)
    df["_cc"] = -df["char_count"].fillna(0)
    df["_sha"] = df["pdf_sha256"].fillna("")
    df = df.sort_values(["company_id", "fiscal_year", "_pri", "_cc", "_sha", "text_path"], kind="stable")
    df = df.drop_duplicates(["company_id", "fiscal_year"], keep="first")
    return df.drop(columns=["_pri", "_cc", "_sha"]).reset_index(drop=True)


def label_fy_end(fiscal_year: int, fy_month: int) -> date:
    return date(fiscal_year, fy_month, monthrange(fiscal_year, fy_month)[1])


def _load_dates(s: Any) -> list[date]:
    if not isinstance(s, str) or not s:
        return []
    return [date.fromisoformat(x) for x in json.loads(s)]


def resolve_fy_end(fiscal_year: int, fy_month: int, fy_cands: list[date]) -> tuple[date, str]:
    """Fiscal-year end = the LATER of the label-based end and a text-parsed 'year ended' date (the latest candidate
    occurring >= 2 times, accepted only inside [label - 400 d, label + 45 d]). Later = more conservative."""
    lab = label_fy_end(fiscal_year, fy_month)
    ok = [d for d in fy_cands if lab - timedelta(days=400) <= d <= lab + timedelta(days=45)]
    if ok and max(ok) > lab:
        return max(ok), "text"
    return lab, "label"


def availability(
    fiscal_year: int, fy_month: int, cover: list[date], agm: list[date], fy_cands: list[date], lag: int = AVAIL_LAG_DAYS
) -> dict[str, Any]:
    """``available_from = max(fy_end + lag, parsed + 1)``; parsed = cover-letter (filing) date if one lies in
    [fy_end + 30 d, fy_end + 540 d], else the AGM date from the notice if in [fy_end + 60 d, fy_end + 540 d]. If both
    parse, the later is used (the AGM follows the filing, so this is the conservative reading)."""
    fy_end, fy_src = resolve_fy_end(fiscal_year, fy_month, fy_cands)
    filed = next((d for d in cover if fy_end + timedelta(days=FILED_WINDOW[0]) <= d <= fy_end + timedelta(days=FILED_WINDOW[1])), None)
    agm_d = next((d for d in agm if fy_end + timedelta(days=AGM_WINDOW[0]) <= d <= fy_end + timedelta(days=AGM_WINDOW[1])), None)
    fixed = fy_end + timedelta(days=lag)
    parsed = max([d for d in (filed, agm_d) if d is not None], default=None)
    cands = [fixed] + ([parsed + timedelta(days=1)] if parsed else [])
    av = max(cands)
    basis = "lag" if (parsed is None or parsed + timedelta(days=1) <= fixed) else ("agm" if parsed == agm_d else "filed")
    return {"fy_end": fy_end, "fy_end_src": fy_src, "filed_date": filed, "agm_date": agm_d, "parsed_date": parsed,
            "available_from": av, "avail_basis": basis}  # fmt: skip


# ── build ────────────────────────────────────────────────────────────────────────────────────────────────────
def output_paths(out_dir: Path) -> tuple[Path, Path]:
    core = f"annual_core_{lx.LEX_AR_V1}_{lx.lexicon_hash()}_{EXTRACT_VERSION}.parquet"
    return out_dir / f"annual_features_{lx.LEX_AR_V1}.parquet", out_dir / "cache" / core


def build(
    catalog: pd.DataFrame,
    *,
    documents_root: Path,
    out_dir: Path,
    fy_months: dict[int, int] | None = None,
    workers: int = 4,
    use_cache: bool = True,
    limit: int | None = None,
    lag: int = AVAIL_LAG_DAYS,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Dedupe, extract (cache-aware, ``workers`` processes), add availability, write the output parquet."""
    uniq = dedupe_annual(catalog)
    stats: dict[str, Any] = {"catalog_rows": len(catalog), "deduped": len(uniq),
                             "dropped_dupes": len(catalog.dropna(subset=["fiscal_year", "text_path"])) - len(uniq)}  # fmt: skip
    if limit:
        uniq = uniq.head(limit)
    uniq["cache_key"] = [f"{s or 'path:' + p}" for s, p in zip(uniq["pdf_sha256"], uniq["text_path"], strict=True)]
    out_path, cache_path = output_paths(out_dir)
    cached = pd.read_parquet(cache_path) if (use_cache and cache_path.exists()) else pd.DataFrame()
    have = set(cached["cache_key"]) if len(cached) else set()
    todo = uniq[~uniq["cache_key"].isin(have) & ~uniq["cache_key"].duplicated()]
    stats["from_cache"], stats["extracted"] = int(uniq["cache_key"].isin(have).sum()), len(todo)
    jobs = [(str(documents_root), r.text_path, r.cache_key) for r in todo.itertuples(index=False)]
    new_rows: list[dict[str, Any]] = []
    if jobs:
        if workers > 1:
            with ProcessPoolExecutor(max_workers=workers) as ex:
                for i, row in enumerate(ex.map(_work, jobs, chunksize=16), 1):
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
    fym = fy_months or {}
    av = []
    for r in df.itertuples(index=False):
        if r.read_ok is True or r.read_ok == 1:
            av.append(availability(int(r.fiscal_year), fym.get(int(r.company_id), 3), _load_dates(r.cover_dates),
                                   _load_dates(r.agm_dates), _load_dates(r.fy_cands), lag))  # fmt: skip
        else:
            av.append({"fy_end": None, "fy_end_src": None, "filed_date": None, "agm_date": None, "parsed_date": None,
                       "available_from": None, "avail_basis": None})  # fmt: skip
    df = pd.concat([df.reset_index(drop=True), pd.DataFrame(av)], axis=1)
    df["lex_ver"], df["lex_hash"], df["extract_ver"], df["lag_days"] = lx.LEX_AR_V1, lx.lexicon_hash(), EXTRACT_VERSION, lag
    df = df.drop(columns=["cache_key", "cover_dates", "agm_dates", "fy_cands"])
    df = df.sort_values(["company_id", "fiscal_year"]).reset_index(drop=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not limit:
        df.to_parquet(out_path, index=False)
    stats["unreadable"] = int((df["read_ok"] != True).sum())  # noqa: E712
    stats["out_path"] = str(out_path)
    return df, stats


# ── reporting ────────────────────────────────────────────────────────────────────────────────────────────────
def parse_rates(df: pd.DataFrame) -> dict[str, float]:
    """How often a filing / AGM date could be parsed, and which basis set ``available_from``."""
    ok = df[df["read_ok"] == True]  # noqa: E712
    n = max(len(ok), 1)
    return {
        "n": float(len(ok)),
        "filed_parsed": float(ok["filed_date"].notna().sum() / n),
        "agm_parsed": float(ok["agm_date"].notna().sum() / n),
        "either_parsed": float(ok["parsed_date"].notna().sum() / n),
        "basis_lag": float((ok["avail_basis"] == "lag").sum() / n),
        "basis_filed": float((ok["avail_basis"] == "filed").sum() / n),
        "basis_agm": float((ok["avail_basis"] == "agm").sum() / n),
        "fy_end_from_text": float((ok["fy_end_src"] == "text").sum() / n),
    }


def coverage_by_fy(df: pd.DataFrame) -> pd.DataFrame:
    ok = df[(df["read_ok"] == True) & (df["fiscal_year"] >= 2012)]  # noqa: E712
    rows = []
    for fy, g in ok.groupby("fiscal_year"):
        rows.append({"fy": int(fy), "docs": len(g), "companies": g["company_id"].nunique(), "doc_ok": g["doc_ok"].mean(),
                     "mda_ok": g["mda_ok"].mean(), "aud_ok": g["aud_ok"].mean(), "filed": g["filed_date"].notna().mean(),
                     "agm": g["agm_date"].notna().mean(), "qual": g["aud_qual"].mean(), "eom": g["aud_eom"].mean(),
                     "gc": g["aud_gc"].mean(), "mean_mda_tone": g["mda_tone_net"].mean()})  # fmt: skip
    return pd.DataFrame(rows)


def nan_to_none(x: float) -> float | None:
    return None if isinstance(x, float) and math.isnan(x) else x
