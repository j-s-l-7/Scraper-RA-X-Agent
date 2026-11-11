# ruff: noqa: E501
"""Expert-signal module (04): reader, extraction (fake transport only), resolution, ledger, overlay, bans, secrets."""

from __future__ import annotations

import ast
import json
import logging
import re
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from ideation.config import REPO_ROOT, Settings, load_methodology
from ideation.experts import LABEL, extract, ledger, overlay, reader, resolve
from ideation.experts.settings import ExpertsConfig, ExtractionCfg
from ideation.ideas.overlay import ACTIONS, EFFECT_COLUMNS, validate
from ideation.lake.adapter import BannedSourceError, LakeMode, check_query
from ideation.lake.bans import assert_allowed
from ideation.lake.queries import EXPERT_BHAV_CLOSES, NamedQuery
from ideation.pit.calendar import TradingCalendar

CFG = ExpertsConfig.from_methodology(load_methodology(REPO_ROOT / "config").data)
EXPERTS_PKG = REPO_ROOT / "ideation" / "experts"


# ── config ───────────────────────────────────────────────────────────────────────────────────────────────────
def test_config_roles() -> None:
    assert CFG.authors == {"Gautam__Baid": "filter_boost", "aravind": "regime_tags"}
    assert CFG.ledger_start == date(2026, 11, 1)
    assert CFG.label == LABEL == "expert overlay, live, unvalidated"


# ── reader: read-only, hash only ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def xdb(tmp_path: Path) -> Path:
    p = tmp_path / "x.db"
    c = sqlite3.connect(p)
    c.executescript(
        "create table users(username text primary key, id text, name text, profile text, fetched_at text);"
        "create table tweets(id integer primary key, author_id text, created_at text, text text, lang text, raw text);"
        "create table refs(id integer primary key, author text, text text, fetched_at text);"
        "insert into users values ('Gautam__Baid','1','n','p','t'), ('aravind','2','n','p','t'), ('other','3','n','p','t');"
        "insert into refs values (900, 'a', 'PARENT-CONTEXT', 't');"
        "insert into tweets values (10,'1','2026-10-30T20:00:00.000Z','synthetic text one','en',null),"
        " (11,'2','2026-11-02T04:00:00.000Z','synthetic text two','en',null),"
        " (12,'1','2026-11-03T04:00:00.000Z','https://t.co/abc','en',null),"
        " (13,'3','2026-11-03T04:00:00.000Z','ignored author','en',null);"
    )
    c.execute("update tweets set raw=? where id=10", (json.dumps({"referenced_tweets": [{"type": "quoted", "id": "900"}]}),))
    c.commit()
    c.close()
    return p


def test_reader_filters_and_parent(xdb: Path) -> None:
    posts = reader.read_posts(xdb, handles={"Gautam__Baid", "aravind"})
    assert [p.tweet_id for p in posts] == [10, 11, 12]
    assert posts[0].parent_text == "PARENT-CONTEXT" and posts[1].parent_text == ""
    assert reader.is_media_only(posts[2]) and not reader.is_media_only(posts[0])
    since = datetime(2026, 11, 1, tzinfo=UTC)
    assert [p.tweet_id for p in reader.read_posts(xdb, since=since, handles={"aravind"})] == [11]
    assert posts[0].text_hash == reader.hash_text("synthetic text one", "PARENT-CONTEXT") != reader.hash_text("synthetic text one")


def test_sqlite_is_read_only_at_runtime(xdb: Path) -> None:
    conn = reader.open_readonly(xdb)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("delete from tweets")
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("create table t(x)")
    conn.close()


def test_sqlite_connect_is_read_only_statically() -> None:
    """Every sqlite3.connect under ideation/experts opens a ``mode=ro`` URI with uri=True."""
    seen = 0
    for path in EXPERTS_PKG.rglob("*.py"):
        src = path.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Call) and ast.unparse(node.func) == "sqlite3.connect":
                seen += 1
                assert any(k.arg == "uri" and ast.unparse(k.value) == "True" for k in node.keywords), path
                assert "mode=ro" in src, path
    assert seen >= 1


def test_no_text_column_in_expert_migrations() -> None:
    """Only the hash of a post is stored: no text/raw/parent_text column in 060-064."""
    sql = "\n".join(p.read_text() for p in sorted((REPO_ROOT / "sql" / "ideation").glob("06[0-4]_*.sql")))
    cols = set(re.findall(r"^\s{2}(\w+)\s+(?:text|jsonb|bigint|int|date|timestamptz|double)", sql, re.M))
    assert not ({"text", "raw", "parent_text", "tweet_text", "body"} & cols)
    assert "text_hash" in cols
    assert not (set(re.findall(r"create (?:table|or replace view|view) (?:if not exists )?ideation\.(\w+)", sql)) - {
        "expert_call", "expert_call_symbol", "expert_extraction_cache", "expert_signal_ledger", "v_expert_active_calls"})  # fmt: skip


# ── extraction (fake transport; never the API) ───────────────────────────────────────────────────────────────
def walk_strict(schema: Any, path: str = "$") -> list[str]:
    """OpenAI strict mode: every object has additionalProperties false and lists all its keys in required."""
    bad: list[str] = []
    if isinstance(schema, dict):
        if schema.get("type") == "object" or "properties" in schema:
            if schema.get("additionalProperties") is not False:
                bad.append(f"{path}: additionalProperties")
            if set(schema.get("required", [])) != set(schema.get("properties", {})):
                bad.append(f"{path}: required != properties")
        for k, v in schema.items():
            bad += walk_strict(v, f"{path}.{k}")
    elif isinstance(schema, list):
        for i, v in enumerate(schema):
            bad += walk_strict(v, f"{path}[{i}]")
    return bad


def test_schema_is_strict_compatible() -> None:
    assert walk_strict(extract.RESPONSE_FORMAT["json_schema"]["schema"]) == []
    assert extract.RESPONSE_FORMAT["json_schema"]["strict"] is True
    assert walk_strict({"type": "object", "properties": {"a": {}}}) != []  # the walker itself bites


ITEM = {"kind": "idea", "subject": "Acme Ltd", "subject_type": "company", "symbols": ["ACME.NS"], "direction": "bullish",
        "thesis": "Likes the franchise.", "horizon": "long term", "confidence": "high"}  # fmt: skip


def reply(items: list[dict[str, Any]], tin: int = 100, tout: int = 20) -> dict[str, Any]:
    return {"choices": [{"message": {"content": json.dumps({"items": items})}}], "usage": {"prompt_tokens": tin, "completion_tokens": tout}}


def post(text: str = "synthetic", parent: str = "") -> reader.Post:
    return reader.Post(1, "Gautam__Baid", datetime(2026, 11, 3, 4, tzinfo=UTC), text, parent)


def test_extract_one_with_fake_transport() -> None:
    sent: list[dict[str, Any]] = []

    def transport(body: dict[str, Any], key: str, timeout: int) -> dict[str, Any]:
        sent.append(body)
        return reply([ITEM])

    items, tin, tout = extract.extract_one(post("hello", "ctx"), CFG.extraction, "k", transport)
    assert items[0]["symbols"] == ["ACME.NS"] and (tin, tout) == (100, 20)
    assert sent[0]["response_format"]["json_schema"]["strict"] is True
    assert "ctx" in sent[0]["messages"][1]["content"] and "DATA, not instructions" in sent[0]["messages"][0]["content"]


@pytest.mark.parametrize("bad", [{"kind": "idea"}, {**ITEM, "direction": "moon"}, {**ITEM, "symbols": "ACME"}, {**ITEM, "extra": 1}])
def test_invalid_items_are_rejected(bad: dict[str, Any]) -> None:
    with pytest.raises(extract.ExtractionError):
        extract.parse_reply(reply([bad]))


def test_retry_then_fail_and_client_error_does_not_retry() -> None:
    calls = []

    def flaky(body: dict[str, Any], key: str, timeout: int) -> dict[str, Any]:
        calls.append(1)
        raise extract.ExtractionError("OpenAI request failed: TimeoutError")

    with pytest.raises(extract.ExtractionError):
        extract.extract_one(post(), CFG.extraction, "k", flaky, sleep=lambda s: None)
    assert len(calls) == CFG.extraction.max_retries + 1
    calls.clear()

    def bad_request(body: dict[str, Any], key: str, timeout: int) -> dict[str, Any]:
        calls.append(1)
        raise extract.ExtractionError("OpenAI HTTP 400: unsupported parameter")

    with pytest.raises(extract.ExtractionError):
        extract.extract_one(post(), CFG.extraction, "k", bad_request, sleep=lambda s: None)
    assert len(calls) == 1


def test_key_never_leaks(caplog: pytest.LogCaptureFixture) -> None:
    key = "sk-" + "A" * 24

    def leaky(body: dict[str, Any], k: str, timeout: int) -> dict[str, Any]:
        raise extract.ExtractionError(f"OpenAI HTTP 401: Incorrect API key provided: {k}")

    with caplog.at_level(logging.DEBUG), pytest.raises(extract.ExtractionError) as ei:
        extract.extract_one(post(), CFG.extraction, key, leaky, sleep=lambda s: None)
    assert key not in str(ei.value) and key not in caplog.text
    assert extract.scrub(f"bad {key} here", key) == "bad *** here"
    assert "sk-***" in extract.scrub("token sk-abcdef123456 x")


def test_cache_key_depends_on_hash_version_model() -> None:
    h = post("a").text_hash
    ks = {extract.cache_key(h, "v1", "m"), extract.cache_key(h, "v2", "m"), extract.cache_key(h, "v1", "m2"),
          extract.cache_key(post("a", "ctx").text_hash, "v1", "m")}  # fmt: skip
    assert len(ks) == 4


def test_estimate_scales_with_posts_and_uses_config_prices() -> None:
    cfg = ExtractionCfg(price_in_per_mtok=1.0, price_out_per_mtok=2.0, prompt_overhead_tokens=100, est_out_tokens_per_post=10)
    e = extract.estimate([post("x" * 400)] * 3, cfg)
    assert e.n_posts == 3 and e.tokens_in == 3 * (100 + 100) and e.tokens_out == 30
    assert e.usd == pytest.approx(600 / 1e6 + 60 / 1e6)
    assert "3 posts" in e.line()
    assert extract.estimate([], cfg).usd == 0


# ── secrets via the existing settings mechanism ──────────────────────────────────────────────────────────────
def test_openai_key_loads_from_secrets_file_and_is_secret(tmp_path: Path) -> None:
    f = tmp_path / "secrets.env"
    f.write_text("OPENAI_API_KEY=sk-test-placeholder-0000\nOPENAI_MODEL=gpt-test\nUNRELATED=1\n")
    s = Settings(_env_file=f)
    assert s.openai_api_key is not None and s.openai_api_key.get_secret_value() == "sk-test-placeholder-0000"
    assert s.openai_model == "gpt-test"
    assert "sk-test-placeholder" not in repr(s) and "sk-test-placeholder" not in str(s)


# ── resolution ───────────────────────────────────────────────────────────────────────────────────────────────
SM = pd.DataFrame({
    "symbol": ["ACME", "OLDCO", "TWIN"], "security_id": [1, 2, 3], "company_id": [10, 20, 30],
    "valid_from": [date(2010, 1, 1)] * 3, "valid_to": [date(2026, 11, 5), date(2020, 1, 1), date(2026, 11, 5)],
})  # fmt: skip
CO = pd.DataFrame({
    "company_id": [10, 20, 30, 31, 40], "nse_symbol": ["ACME", "OLDCO", "TWIN", "TWIN2", "NOSYM"],
    "bse_code": ["500001", "", "", "", ""], "name": ["Acme Industries Limited", "Old Co Ltd", "Twin Corp", "Twin Corp", "Nosym Holdings"],
})  # fmt: skip
R = resolve.Resolver(SM, CO)
D = date(2026, 11, 3)


@pytest.mark.parametrize(
    ("symbols", "subject", "stype", "want"),
    [
        (["ACME.NS"], "x", "company", [("ACME", 1, 10, "symbol")]),
        (["NSE:acme"], "x", "company", [("ACME", 1, 10, "symbol")]),
        (["OLDCO"], "x", "company", [("OLDCO", 2, 20, "symbol_latest_segment")]),  # segment ended before the post
        (["500001"], "x", "company", [("500001", 1, 10, "name")]),  # BSE code via company master
        (["ZZZ"], "x", "company", [("ZZZ", None, None, "unresolved")]),
        ([], "Acme Industries", "company", [("Acme Industries", 1, 10, "name")]),
        ([], "Nosym Holdings", "company", [("Nosym Holdings", None, None, "unresolved")]),  # no security_map row
        ([], "Twin Corp", "company", [("Twin Corp", None, None, "unresolved")]),  # ambiguous name never guessed
        ([], "Crude oil", "macro", []),
    ],
)
def test_resolution_table(symbols: list[str], subject: str, stype: str, want: list[tuple[Any, ...]]) -> None:
    got = [(r.symbol, r.security_id, r.company_id, r.method) for r in R.resolve(symbols, subject, stype, D)]
    assert got == want


# ── ledger ───────────────────────────────────────────────────────────────────────────────────────────────────
def cal(days: list[str]) -> TradingCalendar:
    return TradingCalendar([date.fromisoformat(d) for d in days], [False] * len(days))


SESS = ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-09", "2026-11-10"]  # 11-06/07/08: holiday + weekend


@pytest.mark.parametrize(
    ("posted", "entry"),
    [
        (datetime(2026, 11, 3, 4, 0, tzinfo=UTC), date(2026, 11, 4)),  # IST morning on a session: next session
        (datetime(2026, 11, 3, 20, 0, tzinfo=UTC), date(2026, 11, 5)),  # 01:30 IST on 11-04: IST date is 11-04
        (datetime(2026, 11, 5, 10, 0, tzinfo=UTC), date(2026, 11, 9)),  # before a holiday + weekend
        (datetime(2026, 11, 7, 10, 0, tzinfo=UTC), date(2026, 11, 9)),  # Saturday
        (datetime(2026, 11, 10, 10, 0, tzinfo=UTC), None),  # calendar does not reach yet
    ],
)
def test_entry_is_next_session_after_ist_date(posted: datetime, entry: date | None) -> None:
    assert ledger.entry_session(cal(SESS), posted) == entry


def test_forward_returns_adjust_and_wait_for_maturity() -> None:
    days = pd.bdate_range("2026-11-02", "2027-03-31").date.tolist()
    c = cal([d.isoformat() for d in days])
    px = pd.Series({d: 100.0 for d in days})
    px[date(2026, 12, 4)] = 110.0
    bench = pd.Series({d: 200.0 for d in days})
    bench[date(2026, 12, 4)] = 202.0
    facs = pd.DataFrame({"ex_date": [date(2026, 11, 20)], "factor": [0.5]})
    for d in days:
        if d >= date(2026, 11, 20):
            px[d] = px[d] * 0.5  # a 1:1 bonus halves the raw close
    r = ledger.forward_returns(datetime(2026, 11, 3, 4, tzinfo=UTC), px, facs, bench, c, (1, 3, 12))
    assert r["entry_date"] == date(2026, 11, 4) and r["entry_px"] == 100.0
    assert r["ret_1m"] == pytest.approx(0.10) and r["bench_1m"] == pytest.approx(0.01)  # exit 2026-12-04
    assert r["ret_3m"] == pytest.approx(0.0) and r["ret_12m"] is None and r["bench_12m"] is None  # 12M not reached


def test_wilson_and_insufficient_track_record() -> None:
    lo, hi = ledger.wilson(8, 10)
    assert 0.5 < lo < 0.8 < hi < 0.97
    assert all(pd.isna(x) for x in ledger.wilson(0, 0))
    df = pd.DataFrame({"author": ["a"] * 3, "direction": ["bullish"] * 3, "ret_1m": [0.1, -0.05, None], "bench_1m": [0.0, 0.0, None],
                       "ret_3m": [None] * 3, "bench_3m": [None] * 3, "ret_12m": [None] * 3, "bench_12m": [None] * 3})  # fmt: skip
    cards = {(c.direction, c.horizon): c for c in ledger.scorecards(df, (1, 3, 12))}
    assert cards[("bullish", 1)].n_matured == 2 and cards[("bullish", 1)].hits == 1 and cards[("bullish", 3)].n_matured == 0
    rep = ledger.render_report(df, (1, 3, 12), 30, date(2026, 11, 1), LABEL, 4)
    assert "forward record since 2026-11" in rep and "Insufficient track record" in rep and LABEL in rep
    assert "(4)" in rep and "n/a (insufficient)" in rep


def test_bearish_hit_means_trailing_the_benchmark() -> None:
    df = pd.DataFrame({"author": ["a"] * 2, "direction": ["avoid"] * 2, "ret_1m": [-0.1, 0.1], "bench_1m": [0.0, 0.0]})
    (c,) = ledger.scorecards(df, (1,))
    assert c.hits == 1 and c.n_matured == 2


# ── overlay ──────────────────────────────────────────────────────────────────────────────────────────────────
def calls(rows: list[tuple[Any, ...]]) -> pd.DataFrame:
    cols = ["call_id", "author", "posted_at", "subject", "subject_type", "thesis", "direction", "confidence", "company_id"]
    return pd.DataFrame(rows, columns=cols).astype({"company_id": "float"})


def ts(d: str) -> datetime:
    return datetime.fromisoformat(d).replace(tzinfo=UTC)


B = "Gautam__Baid"
ASOF = date(2026, 11, 10)


def eff(rows: list[tuple[Any, ...]], ids: tuple[int, ...] = (10, 20, 30)) -> pd.DataFrame:
    return overlay.LiveExpertOverlay(calls(rows), CFG).effects(ASOF, list(ids))


@pytest.mark.parametrize(
    ("direction", "conf", "action"),
    [("bearish", "low", "exclude"), ("avoid", "high", "exclude"), ("bullish", "high", "boost"), ("bullish", "medium", "boost"),
     ("bullish", "low", "annotate"), ("watch", "high", "annotate")],
)  # fmt: skip
def test_overlay_actions(direction: str, conf: str, action: str) -> None:
    e = eff([("1:0", B, ts("2026-11-03"), "Acme", "company", "t", direction, conf, 10)])
    assert list(e["action"]) == [action] and e["company_id"].iloc[0] == 10
    assert LABEL in e["thesis"].iloc[0] and e["label"].iloc[0] == LABEL
    assert e["url"].iloc[0] == f"https://x.com/{B}/status/1"
    assert validate(e) is e and set(e["action"]) <= set(ACTIONS) and list(e.columns[: len(EFFECT_COLUMNS)]) == list(EFFECT_COLUMNS)
    assert e["weight"].iloc[0] == (1.15 if action == "boost" else 1.0) and e["age_days"].iloc[0] == 7


def test_speculative_author_never_acts_on_companies() -> None:
    e = eff([("2:0", "aravind", ts("2026-11-03"), "Acme", "company", "t", "bearish", "high", 10)])
    assert len(e) == 0 and list(e.columns[: len(EFFECT_COLUMNS)]) == list(EFFECT_COLUMNS)


def test_window_supersession_and_universe_filter() -> None:
    rows = [
        ("1:0", B, ts("2026-07-01"), "Acme", "company", "old", "bearish", "high", 10),  # older than 90 days
        ("2:0", B, ts("2026-10-01"), "Beta", "company", "t", "bearish", "high", 20),
        ("3:0", B, ts("2026-11-02"), "Beta", "company", "newer", "bullish", "high", 20),  # supersedes the exclude
        ("4:0", B, ts("2026-11-02"), "Gamma", "company", "t", "bearish", "high", 30),
        ("5:0", B, ts("2026-11-11"), "Acme", "company", "future", "bearish", "high", 10),  # posted after as_of
    ]
    e = eff(rows, ids=(10, 20))
    assert dict(zip(e["company_id"], e["action"], strict=True)) == {20: "boost"}


def test_overlay_empty_and_sector_calls_ignored() -> None:
    assert len(overlay.LiveExpertOverlay(calls([]), CFG).effects(ASOF, [10])) == 0
    assert len(eff([("1:0", B, ts("2026-11-03"), "Defence", "sector", "t", "bullish", "high", None)], ids=(10,))) == 0


def test_regime_tags_only_from_speculative_author_and_labelled() -> None:
    ov = overlay.LiveExpertOverlay(calls([
        ("1:0", "aravind", ts("2026-11-03"), "Crude oil", "macro", "t", "bullish", "low", None),
        ("2:0", "aravind", ts("2026-11-04"), "Gold", "asset", "t", "bullish", "low", None),
        ("3:0", B, ts("2026-11-04"), "Rupee", "macro", "t", "bearish", "low", None),
        ("4:0", "aravind", ts("2026-11-04"), "Acme", "company", "t", "bearish", "low", 10),
    ]), CFG)  # fmt: skip
    tags = ov.regime_tags(ASOF)
    assert [t["tag"] for t in tags] == ["Crude oil", "Gold"] and all("speculative" in t["label"] and LABEL in t["label"] for t in tags)
    assert len(ov.effects(ASOF, [10])) == 0


def test_exclude_beats_boost_and_records_conflict() -> None:
    cfg = ExpertsConfig.from_methodology({**load_methodology(REPO_ROOT / "config").data})
    cfg2 = ExpertsConfig(**{**cfg.__dict__, "authors": {B: "filter_boost", "second": "filter_boost"}})
    ov = overlay.LiveExpertOverlay(calls([
        ("1:0", B, ts("2026-11-02"), "Acme", "company", "t", "bearish", "high", 10),
        ("2:0", "second", ts("2026-11-03"), "Acme", "company", "t", "bullish", "high", 10),
    ]), cfg2)  # fmt: skip
    e = ov.effects(ASOF, [10])
    assert sorted(e["action"]) == ["conflict", "exclude"]


def test_overlay_feeds_the_ideas_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """The engine decides conflict (boost on a bottom-decile name) and the financials cap; the overlay only emits boost."""
    from ideation.ideas.overlay import ExpertOverlay

    ov: ExpertOverlay = overlay.LiveExpertOverlay(calls([]), CFG)
    assert len(ov.effects(ASOF, [])) == 0


# ── bans (runtime) ───────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("name", ["expert_call", "expert_call_symbol", "expert_extraction_cache", "expert_signal_ledger", "v_expert_active_calls"])
def test_expert_tables_banned_in_backtest(name: str) -> None:
    with pytest.raises(BannedSourceError, match=name):
        assert_allowed(f"select * from ideation.{name}")
    q = NamedQuery("x", f"select 1 from ideation.{name}", sources=(f"ideation.{name}",))
    with pytest.raises(BannedSourceError):
        check_query(q, None, LakeMode.BACKTEST)
    with pytest.raises(BannedSourceError):
        check_query(q, None, LakeMode.SNAPSHOT)


def test_expert_price_query_is_not_banned_but_is_lake_named() -> None:
    check_query(EXPERT_BHAV_CLOSES, {"symbols": ["A"], "since": date(2026, 1, 1)}, LakeMode.LIVE)
