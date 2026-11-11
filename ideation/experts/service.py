# ruff: noqa: E501
"""Database-facing steps of the expert module: cache, call upsert, resolution, ledger update. Writes only ``ideation.*``
through a ``GuardedConnection``; reads the lake only through named queries. Live-only; never a backtest path."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from ideation.experts import ledger as led
from ideation.experts.extract import ExtractionError, Transport, cache_key, estimate, extract_one
from ideation.experts.reader import Post, is_media_only
from ideation.experts.resolve import Resolver
from ideation.experts.settings import ExpertsConfig
from ideation.lake import queries as q
from ideation.pit.calendar import TradingCalendar


@dataclass
class IngestStats:
    posts_read: int = 0
    media_only: int = 0
    cached: int = 0
    extracted: int = 0
    failed: int = 0
    calls_written: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    errors: list[str] = field(default_factory=list)


def load_cache(conn: Any, keys: list[str]) -> dict[str, list[dict[str, Any]]]:
    if not keys:
        return {}
    rows = conn.execute("select cache_key, result from ideation.expert_extraction_cache where cache_key = any(%s)", (keys,)).fetchall()
    return {k: r["items"] for k, r in rows}


def store_cache(conn: Any, key: str, post: Post, version: str, model: str, items: list[dict[str, Any]], tin: int, tout: int) -> None:
    conn.execute(
        "insert into ideation.expert_extraction_cache (cache_key, text_hash, prompt_version, model, result, tokens_in, tokens_out)"
        " values (%s, %s, %s, %s, %s, %s, %s) on conflict (cache_key) do nothing",
        (key, post.text_hash, version, model, json.dumps({"items": items}), tin, tout),
    )


def plan(conn: Any, posts: list[Post], cfg: ExpertsConfig, model: str, max_posts: int) -> tuple[list[Post], dict[str, list[dict[str, Any]]], int]:
    """(posts still to extract, cache hits by key, media-only count). Most recent ``max_posts`` first-run cap."""
    ex = cfg.extraction
    text_posts = [p for p in posts if not is_media_only(p)]
    keys = {p.tweet_id: cache_key(p.text_hash, ex.prompt_version, model) for p in text_posts}
    hits = load_cache(conn, list(keys.values()))
    todo = [p for p in text_posts if keys[p.tweet_id] not in hits]
    return todo[-max_posts:] if max_posts > 0 else todo, hits, len(posts) - len(text_posts)


def run_extraction(conn: Any, todo: list[Post], cfg: ExpertsConfig, model: str, key: str, transport: Transport,
                   stats: IngestStats, sleep: Callable[[float], None] | None = None) -> None:  # fmt: skip
    ex = cfg.extraction
    for p in todo:
        try:
            kw = {"sleep": sleep} if sleep else {}
            items, tin, tout = extract_one(p, type(ex)(**{**ex.__dict__, "model": model}), key, transport, **kw)
        except ExtractionError as exc:
            stats.failed += 1
            stats.errors.append(f"tweet {p.tweet_id}: {exc}")  # never the text
            continue
        store_cache(conn, cache_key(p.text_hash, ex.prompt_version, model), p, ex.prompt_version, model, items, tin, tout)
        stats.extracted += 1
        stats.tokens_in += tin
        stats.tokens_out += tout


def materialise_calls(conn: Any, posts: list[Post], cfg: ExpertsConfig, model: str, resolver: Resolver, stats: IngestStats) -> None:
    """Write ``expert_call`` / ``expert_call_symbol`` for every post whose extraction is cached (idempotent)."""
    ex = cfg.extraction
    keys = {p.tweet_id: cache_key(p.text_hash, ex.prompt_version, model) for p in posts if not is_media_only(p)}
    cache = load_cache(conn, list(keys.values()))
    have = {r[0]: (r[1], r[2], r[3]) for r in conn.execute(
        "select tweet_id, text_hash, prompt_version, model from ideation.expert_call").fetchall()}  # fmt: skip
    for p in posts:
        k = keys.get(p.tweet_id)
        if k is None or k not in cache:
            continue
        if have.get(p.tweet_id) == (p.text_hash, ex.prompt_version, model):
            continue
        conn.execute("delete from ideation.expert_call where tweet_id = %s", (p.tweet_id,))
        for n, it in enumerate(cache[k]):
            cid = f"{p.tweet_id}:{n}"
            res = resolver.resolve(it["symbols"], it["subject"], it["subject_type"], led.ist_date(p.posted_at)) if it["kind"] == "idea" else []
            conn.execute(
                "insert into ideation.expert_call (call_id, tweet_id, author, posted_at, text_hash, kind, subject, subject_type,"
                " thesis, symbols, direction, horizon, confidence, model, prompt_version)"
                " values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (cid, p.tweet_id, p.handle, p.posted_at, p.text_hash, it["kind"], it["subject"], it["subject_type"], it["thesis"],
                 [r.symbol for r in res] or [s.upper() for s in it["symbols"]], it["direction"], it["horizon"], it["confidence"],
                 model, ex.prompt_version),
            )  # fmt: skip
            for r in res:
                conn.execute(
                    "insert into ideation.expert_call_symbol (call_id, symbol, security_id, company_id, resolution_method)"
                    " values (%s,%s,%s,%s,%s) on conflict (call_id, symbol) do nothing",
                    (cid, r.symbol, r.security_id, r.company_id, r.method),
                )
            stats.calls_written += 1


def build_resolver(conn: Any, lake: Any) -> Resolver:
    sm = pd.DataFrame(
        conn.execute("select symbol, security_id, company_id, valid_from, valid_to from ideation.security_map").fetchall(),
        columns=["symbol", "security_id", "company_id", "valid_from", "valid_to"],
    )
    rows = lake.fetch(q.COMPANY)
    company = pd.DataFrame(rows, columns=["company_id", "nse_symbol", "bse_code", "isin", "name", "sector", "industry_group",
                                          "industry", "sub_industry", "industry_code", "has_consolidated"])  # fmt: skip
    return Resolver(sm, company)


def re_resolve_unresolved(conn: Any, resolver: Resolver) -> int:
    rows = conn.execute(
        "select s.call_id, s.symbol, c.subject, c.subject_type, c.posted_at from ideation.expert_call_symbol s"
        " join ideation.expert_call c using (call_id) where s.resolution_method = 'unresolved'"
    ).fetchall()
    n = 0
    for cid, sym, subject, st, posted in rows:
        r = resolver.resolve([sym], subject or "", st or "", led.ist_date(posted))
        if r and r[0].method != "unresolved":
            conn.execute(
                "update ideation.expert_call_symbol set security_id=%s, company_id=%s, resolution_method=%s, resolved_at=now()"
                " where call_id=%s and symbol=%s", (r[0].security_id, r[0].company_id, r[0].method, cid, sym))  # fmt: skip
            n += 1
    return n


def update_ledger(conn: Any, lake: Any, cfg: ExpertsConfig) -> dict[str, int]:
    """Create/refresh ledger rows for scored (>= ledger_start, filter_boost author) resolved company calls."""
    authors = cfg.with_role("filter_boost")
    rows = conn.execute(
        "select call_id, author, posted_at, direction, symbol, security_id, company_id from ideation.v_expert_active_calls"
        " where security_id is not null and subject_type = 'company' and author = any(%s) and direction is not null",
        (authors,),
    ).fetchall()
    scored = [r for r in rows if led.ist_date(r[2]) >= cfg.ledger_start]
    stats = {"eligible": len(scored), "before_start": len(rows) - len(scored), "entered": 0, "ret_filled": 0}
    if not scored:
        return stats
    since = min(led.ist_date(r[2]) for r in scored)
    syms = sorted({r[4] for r in scored})
    px = pd.DataFrame(lake.fetch(q.EXPERT_BHAV_CLOSES, {"symbols": syms, "since": since}), columns=["symbol", "date", "close"])
    fac = pd.DataFrame(lake.fetch(q.EXPERT_ADJ_FACTOR, {"symbols": syms, "since": since}), columns=["symbol", "ex_date", "factor"])
    bench = pd.DataFrame(lake.fetch(q.EXPERT_BENCH, {"code": cfg.ledger.benchmark_code, "since": since}), columns=["date", "value"])
    bser = bench.set_index("date")["value"]
    cal_rows = conn.execute("select session, bhav_missing from ideation.calendar where session >= %s", (since,)).fetchall()
    cal = pd.DataFrame(cal_rows, columns=["session", "bhav_missing"])
    last = cal["session"].max() if len(cal) else since
    extra = sorted(d for d in bser.index if d > last)
    if extra:  # the calendar table lags the lake; extend with index sessions
        cal = pd.concat([cal, pd.DataFrame({"session": extra, "bhav_missing": False})], ignore_index=True)
    tcal = TradingCalendar.from_frame(cal)
    hs = cfg.ledger.horizons_months
    for cid, author, posted, direction, sym, sec, comp in scored:
        s = px[px["symbol"] == sym].set_index("date")["close"]
        f = fac[fac["symbol"] == sym]
        r = led.forward_returns(posted, s, f, bser, tcal, hs)
        stats["entered"] += r["entry_px"] is not None
        stats["ret_filled"] += any(r.get(f"ret_{h}m") is not None for h in hs)
        conn.execute(
            "insert into ideation.expert_signal_ledger (call_id, security_id, company_id, author, direction, posted_at, entry_date,"
            " entry_px, ret_1m, ret_3m, ret_12m, bench_1m, bench_3m, bench_12m, updated_at)"
            " values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())"
            " on conflict (call_id, security_id) do update set entry_date=excluded.entry_date, entry_px=excluded.entry_px,"
            " ret_1m=excluded.ret_1m, ret_3m=excluded.ret_3m, ret_12m=excluded.ret_12m, bench_1m=excluded.bench_1m,"
            " bench_3m=excluded.bench_3m, bench_12m=excluded.bench_12m, updated_at=now()",
            (cid, sec, comp, author, direction, posted, r["entry_date"], r["entry_px"], r.get("ret_1m"), r.get("ret_3m"),
             r.get("ret_12m"), r.get("bench_1m"), r.get("bench_3m"), r.get("bench_12m")),
        )  # fmt: skip
    return stats


def ledger_frame(conn: Any) -> pd.DataFrame:
    cols = ["author", "direction", "ret_1m", "ret_3m", "ret_12m", "bench_1m", "bench_3m", "bench_12m"]
    rows = conn.execute(f"select {', '.join(cols)} from ideation.expert_signal_ledger").fetchall()
    return pd.DataFrame(rows, columns=cols)


def now_utc() -> datetime:
    return datetime.now(UTC)


__all__ = ["estimate"]
