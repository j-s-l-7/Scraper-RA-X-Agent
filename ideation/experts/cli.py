# ruff: noqa: E501
"""``ideation experts ingest | ledger | status`` (04 §8). LIVE-ONLY, UNVALIDATED."""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from typing import Annotated

import typer
from rich.console import Console

from ideation.config import get_settings, load_methodology

app = typer.Typer(no_args_is_help=True, help="Expert signals from X (live-only, unvalidated).")
console = Console(width=None if sys.stdout.isatty() else 200)


def _cfg():  # type: ignore[no-untyped-def]
    from ideation.experts.settings import ExpertsConfig

    return ExpertsConfig.from_methodology(load_methodology().data)


@app.command("ingest")
def ingest_cmd(
    since: Annotated[str, typer.Option(help="YYYY-MM-DD; default = now minus extraction.since_months.")] = "",
    max_posts: Annotated[int, typer.Option(help="Cap on posts sent to the model (0 = config).")] = 0,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Print the plan and cost estimate; call nothing.")] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Spend: without it the estimate is printed and nothing is called.")] = False,
) -> None:
    """Read xagent.db (read-only), extract uncached posts with OpenAI, write calls, resolve symbols."""
    from ideation.db.writer import Writer
    from ideation.experts import service
    from ideation.experts.extract import estimate, http_transport
    from ideation.experts.reader import read_posts
    from ideation.lake.adapter import LakeMode, LakeReader

    settings = get_settings()
    cfg = _cfg()
    ex = cfg.extraction
    model = settings.openai_model or ex.model
    start = datetime.fromisoformat(since).replace(tzinfo=UTC) if since else datetime.now(UTC) - timedelta(days=30 * ex.since_months)
    posts = read_posts(settings.xagent_db, since=start, handles=set(cfg.authors))
    stats = service.IngestStats(posts_read=len(posts))
    lake = LakeReader.from_settings(settings, LakeMode.LIVE)
    with Writer.from_settings(settings).connect() as conn:
        todo, hits, media = service.plan(conn, posts, cfg, model, max_posts or ex.max_posts)
        stats.media_only, stats.cached = media, len(hits)
        est = estimate(todo, ex)
        console.print(f"read {len(posts)} posts since {start.date()}; {len(hits)} cached, {media} media-only (no text); model {model}")
        console.print(est.line())
        if todo and (dry_run or not yes):
            console.print("[yellow]nothing spent. Re-run with --yes to extract.[/yellow]" if not dry_run else "dry run: nothing called")
        elif todo:
            key = settings.openai_api_key
            if key is None:
                console.print("[red]no OPENAI_API_KEY in the secrets file / environment[/red]")
                raise typer.Exit(1)
            service.run_extraction(conn, todo, cfg, model, key.get_secret_value(), http_transport, stats)
        if not dry_run:
            resolver = service.build_resolver(conn, lake)
            service.materialise_calls(conn, posts, cfg, model, resolver, stats)
            re = service.re_resolve_unresolved(conn, resolver)
            console.print(f"extracted {stats.extracted}, failed {stats.failed}, calls written {stats.calls_written}, "
                          f"re-resolved {re}; tokens {stats.tokens_in:,} in / {stats.tokens_out:,} out")  # fmt: skip
            for e in stats.errors[:5]:
                console.print(f"[yellow]{e}[/yellow]")


@app.command("ledger")
def ledger_cmd(refresh: Annotated[bool, typer.Option("--refresh/--no-refresh", help="Recompute entries/returns first.")] = True) -> None:
    """Forward record: update the ledger from PIT prices and print the scorecard."""
    from ideation.db.writer import Writer
    from ideation.experts import ledger as led
    from ideation.experts import service
    from ideation.lake.adapter import LakeMode, LakeReader

    settings = get_settings()
    cfg = _cfg()
    lake = LakeReader.from_settings(settings, LakeMode.LIVE)
    with Writer.from_settings(settings).connect() as conn:
        st = service.update_ledger(conn, lake, cfg) if refresh else {"before_start": 0}
        df = service.ledger_frame(conn)
        console.print(led.render_report(df, cfg.ledger.horizons_months, cfg.ledger.min_matured, cfg.ledger_start, cfg.label,
                                        st.get("before_start", 0)))  # fmt: skip


@app.command("status")
def status_cmd() -> None:
    """Counts: calls, resolution, cache, ledger, regime tags."""
    from ideation.db.writer import Writer
    from ideation.experts.overlay import LiveExpertOverlay

    settings = get_settings()
    cfg = _cfg()
    with Writer.from_settings(settings).connect() as conn:
        n_call = conn.execute("select author, kind, count(*) from ideation.expert_call group by 1, 2 order by 1, 2").fetchall()
        res = conn.execute("select resolution_method, count(*) from ideation.expert_call_symbol group by 1 order by 1").fetchall()
        cache = conn.execute("select count(*), coalesce(sum(tokens_in),0), coalesce(sum(tokens_out),0) from ideation.expert_extraction_cache").fetchone()
        ledger_n = conn.execute("select count(*), count(entry_px) from ideation.expert_signal_ledger").fetchone()
        console.print(f"[bold]{cfg.label}[/bold]; ledger scored from {cfg.ledger_start}")
        console.print("calls:", {f"{a}/{k}": n for a, k, n in n_call} or "none")
        console.print("resolution:", dict(res) or "none")
        console.print(f"extraction cache: {cache[0]} posts, {cache[1]:,} in / {cache[2]:,} out tokens")
        console.print(f"ledger rows: {ledger_n[0]} ({ledger_n[1]} with an entry price)")
        ov = LiveExpertOverlay.from_conn(conn, cfg)
        today = datetime.now(UTC).date()
        tags = ov.regime_tags(today)
        console.print(f"regime narrative tags (speculative): {len(tags)}")
        for t in tags[:10]:
            console.print(f"  - {t['tag']} ({t['direction']}, {t['person']}, {t['posted_at']})")
