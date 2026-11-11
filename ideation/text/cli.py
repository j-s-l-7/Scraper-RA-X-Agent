"""``ideation text extract --types concall_transcript,concall_ppt`` (05 §7.1).

Reads the documents catalog and results-event dates from the lake (adapter, named queries), reads page text from the
Scraper checkout (read-only, ``reader.py``), and writes ``IDEATION_DATA_DIR/text/doc_features_LEX_V1.parquet``.
Nothing is written to the lake.
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path
from typing import Annotated

import pandas as pd
import typer
from rich.console import Console
from rich.table import Table

from ideation.config import get_settings
from ideation.lake import queries as Q
from ideation.lake.adapter import LakeMode, LakeReader
from ideation.text import annual, extract
from ideation.text import lexicon as lex

app = typer.Typer(no_args_is_help=True, help="Text extraction from concall transcripts and presentations.")
console = Console(width=None if sys.stdout.isatty() else 200)
log = logging.getLogger("ideation.text")
VALID_TYPES = ("concall_transcript", "concall_ppt")


def _fy_months(data_dir: Path) -> dict[int, int]:
    """Fiscal-year-end month per company from the latest snapshot, if one exists (default 3 otherwise)."""
    try:
        from ideation.lake.snapshot import fy_month_map
        from ideation.pit.contract import PitStore

        return fy_month_map(PitStore.latest(data_dir).arrow("financial_fact"))
    except Exception as exc:  # optional input: report and fall back to March
        log.warning("fy_month map unavailable (%s); using fy_month=3", exc)
        return {}


def _print_df(title: str, df: pd.DataFrame, fmt: dict[str, str] | None = None) -> None:
    table = Table(*[str(c) for c in df.columns], title=title)
    for row in df.itertuples(index=False):
        cells = []
        for col, v in zip(df.columns, row, strict=True):
            if isinstance(v, float):
                cells.append("" if v != v else (fmt or {}).get(col, "{:.3f}").format(v))
            else:
                cells.append(str(v))
        table.add_row(*cells)
    console.print(table)


@app.command("extract")
def extract_cmd(
    types: Annotated[str, typer.Option(help="Comma list of doc types.")] = "concall_transcript,concall_ppt",
    workers: Annotated[int, typer.Option(help="Worker processes.")] = 4,
    no_cache: Annotated[bool, typer.Option("--no-cache", help="Ignore and rebuild the extraction cache.")] = False,
    limit: Annotated[int | None, typer.Option(help="Smoke test: first N deduped docs; writes no output file.")] = None,
) -> None:
    """Dedupe, clean, score and date every document; write the doc-features parquet and print the QA tables."""
    settings = get_settings()
    wanted = tuple(t.strip() for t in types.split(",") if t.strip())
    bad = [t for t in wanted if t not in VALID_TYPES]
    if bad:
        raise typer.BadParameter(f"unknown doc types {bad}; choose from {VALID_TYPES}")
    t0 = time.perf_counter()
    reader = LakeReader.from_settings(settings, LakeMode.BACKTEST)
    with reader.snapshot() as s:
        catalog = s.copy_pandas(Q.DOCUMENTS_CATALOG)
        events = s.copy_pandas(Q.EARNINGS_DATES)
    catalog = catalog[catalog["doc_type"].isin(wanted)].reset_index(drop=True)
    out_dir = settings.data_dir / "text"
    df, stats = extract.build(
        catalog,
        events,
        documents_root=settings.documents_root,
        out_dir=out_dir,
        fy_months=_fy_months(settings.data_dir),
        workers=workers,
        use_cache=not no_cache,
        limit=limit,
        progress=lambda i, n: console.print(f"  extracted {i:,}/{n:,}"),
    )
    elapsed = time.perf_counter() - t0
    console.print(
        f"catalog rows {stats['catalog_rows']:,}; duplicates dropped {stats['dropped_dupes']:,}; "
        f"docs after dedupe {stats['deduped']}; extracted {stats['extracted']:,}, from cache {stats['from_cache']:,}; "
        f"unreadable {stats['unreadable']:,}; {lex.LEX_V1} ({lex.term_count()} terms, {lex.lexicon_hash()})"
    )
    ok = df[df["read_ok"] == True]  # noqa: E712
    bse_t = ok[(ok["doc_type"] == "concall_transcript") & (ok["source"] == "bse")]
    if len(bse_t):
        console.print(
            f"filed_date parse rate, BSE transcripts: {bse_t['filed_date'].notna().mean():.1%} (n={len(bse_t):,})"
        )
    for typ, g in ok.groupby("doc_type"):
        console.print(
            f"qa_ok rate {typ}: {g['qa_ok'].mean():.1%}; qa marker found {g['qa_found'].mean():.1%}; "
            f"filed_date parsed (all sources) {g['filed_date'].notna().mean():.1%}; "
            f"numeric guidance present {(g['n_guide_pct'] > 0).mean():.1%}"
        )
    _print_df(
        "coverage by year",
        extract.coverage_by_year(df),
        {"filed_parsed": "{:.1%}", "qa_ok": "{:.1%}", "mean_tok": "{:,.0f}"},
    )
    t = extract.add_deltas(df)
    tt = extract.tone_table(t)
    tt = tt[tt["q"] >= "2018Q1"]
    _print_df("tone by quarter discussed (transcripts; d_tone vs the latest call 60-400 days earlier)", tt)
    console.print(f"output: {stats['out_path']}{' (not written: --limit)' if limit else ''}; {elapsed:.1f} s")


@app.command("annual")
def annual_cmd(
    workers: Annotated[int, typer.Option(help="Worker processes.")] = 4,
    no_cache: Annotated[bool, typer.Option("--no-cache", help="Ignore and rebuild the extraction cache.")] = False,
    limit: Annotated[int | None, typer.Option(help="Smoke test: first N deduped reports; writes no output file.")] = None,
    lag: Annotated[int, typer.Option(help="Fixed availability lag after the fiscal-year end (days).")] = annual.AVAIL_LAG_DAYS,
) -> None:
    """PT3: extract annual-report sections and features (LEX_AR_V1), parse availability, write the parquet."""
    settings = get_settings()
    t0 = time.perf_counter()
    reader = LakeReader.from_settings(settings, LakeMode.BACKTEST)
    with reader.snapshot() as s:
        catalog = s.copy_pandas(Q.ANNUAL_CATALOG)
    df, stats = annual.build(
        catalog,
        documents_root=settings.documents_root,
        out_dir=settings.data_dir / "text",
        fy_months=_fy_months(settings.data_dir),
        workers=workers,
        use_cache=not no_cache,
        limit=limit,
        lag=lag,
        progress=lambda i, n: console.print(f"  extracted {i:,}/{n:,}"),
    )
    elapsed = time.perf_counter() - t0
    console.print(
        f"catalog rows {stats['catalog_rows']:,}; duplicates dropped {stats['dropped_dupes']:,}; reports after dedupe "
        f"{stats['deduped']:,}; extracted {stats['extracted']:,}, from cache {stats['from_cache']:,}; unreadable "
        f"{stats['unreadable']:,}; {annual.lx.LEX_AR_V1} ({annual.lx.term_count()} terms, {annual.lx.lexicon_hash()})"
    )
    ok = df[df["read_ok"] == True]  # noqa: E712
    pr = annual.parse_rates(df)
    console.print(
        f"availability (lag {lag} d after FY end, later of parsed date and lag): cover-letter filing date parsed "
        f"{pr['filed_parsed']:.1%}, AGM date parsed {pr['agm_parsed']:.1%}, either {pr['either_parsed']:.1%}; basis of "
        f"available_from: lag {pr['basis_lag']:.1%}, filed {pr['basis_filed']:.1%}, agm {pr['basis_agm']:.1%}; FY end taken "
        f"from text {pr['fy_end_from_text']:.1%} (n={int(pr['n']):,})"
    )
    q = ok[["n_words", "garble_rate", "frag_rate"]].quantile([0.01, 0.05, 0.5, 0.95, 0.99]).reset_index()
    _print_df("document quality quantiles (filter: words >= %d, garble <= %.2f, frag <= %.2f)"
              % (annual.MIN_WORDS, annual.MAX_GARBLE, annual.MAX_FRAG), q, {"n_words": "{:,.0f}"})
    console.print(
        f"doc_ok {ok['doc_ok'].mean():.1%}; mda_ok {ok['mda_ok'].mean():.1%}; aud_ok {ok['aud_ok'].mean():.1%}; "
        f"usable (doc_ok & section) mda {((ok['doc_ok'] == 1) & (ok['mda_ok'] == 1)).mean():.1%}, aud "
        f"{((ok['doc_ok'] == 1) & (ok['aud_ok'] == 1)).mean():.1%}"
    )
    _print_df(
        "coverage by fiscal year (shares of readable reports)",
        annual.coverage_by_fy(df),
        {c: "{:.1%}" for c in ("doc_ok", "mda_ok", "aud_ok", "filed", "agm", "qual", "eom", "gc")},
    )
    console.print(f"output: {stats['out_path']}{' (not written: --limit)' if limit else ''}; {elapsed:.1f} s")


from ideation.features.text_cli import register as _register_pt2  # noqa: E402

_register_pt2(app)
