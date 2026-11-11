# ruff: noqa: E501
"""Expert module against the real lake inside ``ddl_sandbox`` (migrations 060-064 applied, then rolled back)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime

import psycopg
import pytest

from ideation.config import REPO_ROOT, Settings, load_methodology
from ideation.experts import LABEL, service
from ideation.experts.extract import cache_key
from ideation.experts.overlay import LiveExpertOverlay
from ideation.experts.reader import Post
from ideation.experts.settings import ExpertsConfig
from ideation.lake.adapter import LakeMode, LakeReader

CFG = ExpertsConfig.from_methodology(load_methodology(REPO_ROOT / "config").data)
ITEM = {"kind": "idea", "subject": "Reliance Industries", "subject_type": "company", "symbols": ["RELIANCE.NS"],
        "direction": "avoid", "thesis": "Synthetic.", "horizon": None, "confidence": "high"}  # fmt: skip
PRINCIPLE = {"kind": "principle", "subject": "patience", "subject_type": "theme", "symbols": [], "direction": None,
             "thesis": "Synthetic principle.", "horizon": None, "confidence": "medium"}  # fmt: skip
MACRO = {"kind": "idea", "subject": "Crude oil", "subject_type": "macro", "symbols": [], "direction": "bullish",
         "thesis": "Synthetic.", "horizon": None, "confidence": "low"}  # fmt: skip


@pytest.fixture
def lake(settings: Settings) -> LakeReader:
    return LakeReader.from_settings(settings, LakeMode.LIVE)


def test_tables_and_constraints(ddl_sandbox: psycopg.Connection) -> None:
    c = ddl_sandbox
    for t in ("expert_call", "expert_call_symbol", "expert_extraction_cache", "expert_signal_ledger", "v_expert_active_calls"):
        assert c.execute("select to_regclass(%s)", (f"ideation.{t}",)).fetchone()[0] is not None
    with pytest.raises(psycopg.errors.CheckViolation), c.transaction():
        c.execute("insert into ideation.expert_call (call_id, tweet_id, author, posted_at, text_hash, kind, direction)"
                  " values ('1:0', 1, 'a', now(), 'h', 'idea', 'sideways')")  # fmt: skip
    with pytest.raises(psycopg.errors.ForeignKeyViolation), c.transaction():
        c.execute("insert into ideation.expert_call_symbol (call_id, symbol, resolution_method) values ('9:9', 'X', 'symbol')")


def test_ingest_resolve_overlay_ledger(ddl_sandbox: psycopg.Connection, lake: LakeReader) -> None:
    c = ddl_sandbox
    cfg = replace(CFG, ledger_start=date(2026, 10, 1))
    posts = [
        Post(101, "Gautam__Baid", datetime(2026, 10, 23, 4, tzinfo=UTC), "synthetic one", ""),
        Post(102, "aravind", datetime(2026, 10, 24, 4, tzinfo=UTC), "synthetic two", ""),
        Post(103, "Gautam__Baid", datetime(2026, 10, 25, 4, tzinfo=UTC), "https://t.co/x", ""),
    ]
    model = cfg.extraction.model
    for p, items in ((posts[0], [ITEM, PRINCIPLE]), (posts[1], [MACRO])):
        service.store_cache(c, cache_key(p.text_hash, cfg.extraction.prompt_version, model), p, cfg.extraction.prompt_version, model, items, 10, 5)
    todo, hits, media = service.plan(c, posts, cfg, model, 2000)
    assert todo == [] and len(hits) == 2 and media == 1  # nothing left to pay for; the media-only post is skipped

    stats = service.IngestStats()
    resolver = service.build_resolver(c, lake)
    service.materialise_calls(c, posts, cfg, model, resolver, stats)
    assert stats.calls_written == 3
    service.materialise_calls(c, posts, cfg, model, resolver, service.IngestStats())  # idempotent
    assert c.execute("select count(*) from ideation.expert_call").fetchone()[0] == 3
    # the stored row carries the hash and structured fields only
    cols = {r[0] for r in c.execute("select column_name from information_schema.columns where table_schema='ideation' and table_name='expert_call'")}
    assert "text" not in cols and "text_hash" in cols
    row = c.execute("select security_id, company_id, resolution_method from ideation.expert_call_symbol where symbol = 'RELIANCE'").fetchone()
    assert row[0] is not None and row[1] is not None and row[2] in ("symbol", "symbol_latest_segment")

    ov = LiveExpertOverlay.from_conn(c, cfg)
    e = ov.effects(date(2026, 11, 5), [int(row[1])])
    assert list(e["action"]) == ["exclude"] and LABEL in e["thesis"].iloc[0]
    assert [t["tag"] for t in ov.regime_tags(date(2026, 11, 5))] == ["Crude oil"]

    st = service.update_ledger(c, lake, cfg)
    assert st["eligible"] == 1 and st["entered"] == 1
    led = c.execute("select entry_date, entry_px, ret_1m from ideation.expert_signal_ledger").fetchone()
    assert led[0] == date(2026, 10, 23) or led[0] > date(2026, 10, 23)
    assert led[1] > 0 and led[2] is None  # one month has not elapsed: not matured
    assert service.update_ledger(c, lake, replace(cfg, ledger_start=date(2026, 11, 1)))["before_start"] == 1
