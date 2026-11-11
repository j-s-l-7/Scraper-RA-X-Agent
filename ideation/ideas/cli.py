"""``ideation ideas [--horizon both] [--top 25] [--avoid 15] [--llm-summary]`` (01 §5, 01 §8.1).

Reads the latest ok ``predict`` run, applies the idea-stage filters, selects the lists, assigns conviction, joins the
(stub) expert overlay, computes continuity against the previous ideas run, persists to ``ideation.idea`` (the
``v_latest_ideas`` view reads the newest ok ideas run, so the default is both horizons in one run) and writes
``IDEATION_DATA_DIR/reports/<as_of>/ideas_<horizon>.md``.
"""

from __future__ import annotations

import sys
import time
from datetime import date
from pathlib import Path
from typing import Annotated, Any

import pandas as pd
import typer
from rich.console import Console

from ideation.config import get_settings, load_methodology

app = typer.Typer(invoke_without_command=True, no_args_is_help=False, help="Ideas engine (P6).")
console = Console(width=None if sys.stdout.isatty() else 200)
HORIZONS = {"both": ("1Y", "3M"), "1y": ("1Y",), "3m": ("3M",), "tactical": ("3M",)}


def concall_excerpts(settings: Any, store: Any, anchor: date, company_ids: list[int]) -> dict[int, tuple[str, str | None]]:
    """Latest and prior transcript text per company (PIT: ``available_from <= anchor``), page-joined, cover pages kept."""
    from ideation.text.reader import read_pages

    view = store.as_of(anchor, record=False)
    docs = view.text_docs("concall_transcript", columns=["company_id", "doc_date", "text_path"])
    docs = docs[docs["company_id"].isin(company_ids)].sort_values(["company_id", "doc_date"], ascending=[True, False])
    out: dict[int, tuple[str, str | None]] = {}
    for cid, g in docs.groupby("company_id"):
        texts = []
        for p in g["text_path"].head(2):
            try:
                texts.append(" ".join(read_pages(settings.documents_root, str(p))))
            except (OSError, ValueError):
                texts.append("")
        if texts and texts[0]:
            out[int(cid)] = (texts[0], texts[1] if len(texts) > 1 and texts[1] else None)
    return out


@app.callback(invoke_without_command=True)
def ideas_cmd(
    horizon: Annotated[str, typer.Option(help="both | 1y | 3m (tactical)")] = "both",
    top: Annotated[int, typer.Option(help="Long ideas per horizon.")] = 0,
    avoid: Annotated[int, typer.Option(help="Avoid-list size.")] = 0,
    llm_summary: Annotated[bool, typer.Option("--llm-summary", help="OPTIONAL: LLM call summaries (key from env; live, unvalidated).")] = False,
    force: Annotated[bool, typer.Option("--force", help="Recompute even if an identical run succeeded.")] = False,
) -> None:
    from dataclasses import replace

    from ideation.db.writer import Writer
    from ideation.ideas import llm_summary as llm_mod
    from ideation.ideas import persist, render, select
    from ideation.ideas.overlay import NullOverlay
    from ideation.logs import setup_logging
    from ideation.pit.contract import PitStore
    from ideation.predict.persist import read_predictions
    from ideation.runs import RunRegistry

    if horizon not in HORIZONS:
        raise typer.BadParameter(f"horizon must be one of {sorted(HORIZONS)}")
    settings = get_settings()
    setup_logging(settings.logs_dir, settings.log_level, console=False)
    meth = load_methodology()
    cfg = select.IdeasConfig.from_methodology(meth.data)
    cfg = replace(cfg, top_n=top or cfg.top_n, avoid_n=avoid or cfg.avoid_n)
    overlay = NullOverlay()
    t0 = time.perf_counter()
    with Writer.from_settings(settings).connect() as conn:
        found = persist.latest_predict_run(conn)
        if found is None:
            console.print("[red]no ok predict run: run `ideation predict` first[/red]")
            raise typer.Exit(1)
        pred_run, as_of, pstats = found
        horizons = [h for h in HORIZONS[horizon] if h in pstats.get("horizons", {})]
        params = {"predict_run": pred_run, "horizons": horizons, "top": cfg.top_n, "avoid": cfg.avoid_n,
                  "llm": bool(llm_summary), "overlay": overlay.name}  # fmt: skip
        with RunRegistry(conn).track("ideas", config_hash=meth.config_hash, as_of=as_of, snap_id=pstats.get("snap_id"),
                                     params=params, force=force) as run:  # fmt: skip
            if run.skipped:
                console.print(f"skipped: identical ideas run {run.record.stats} already ok (use --force)")
                return
            regime = pstats.get("regime", {})
            reports: dict[str, Path] = {}
            counts: dict[str, Any] = {}
            llm_all: dict[int, dict[str, Any]] = {}
            for h in horizons:
                pred = read_predictions(conn, pred_run, h)
                eff = overlay.effects(as_of, [int(c) for c in pred["company_id"]])
                sel = select.select_ideas(pred, h, cfg, as_of, regime, eff)
                prev = persist.previous_ideas(conn, h, run.run_id)
                cur = pd.concat([sel["longs"], sel["avoid"]])
                cont, pid, dropped = select.continuity(cur, prev)
                cur["continuity"], cur["prev_idea_id"] = cont, pid
                sel["longs"], sel["avoid"] = cur[cur["side"] == "long"], cur[cur["side"] == "avoid"]
                llm: dict[int, dict[str, Any]] | None = None
                if llm_summary:
                    lc = llm_mod.LlmConfig.from_methodology(meth.data)
                    snap = PitStore.latest(settings.data_dir)
                    store = PitStore(snap.snapshot_dir, mode="live", text_dir=settings.data_dir / "text")
                    ex = concall_excerpts(settings, store, as_of, [int(c) for c in sel["longs"]["company_id"]])
                    items = [{"company_id": r.company_id, "symbol": r.symbol, "latest": ex[int(r.company_id)][0],
                              "prior": ex[int(r.company_id)][1]} for r in sel["longs"].itertuples() if int(r.company_id) in ex]  # fmt: skip
                    try:
                        llm = llm_mod.summarise(items, lc)
                    except llm_mod.LlmUnavailable as exc:
                        console.print(f"[yellow]LLM summary skipped: {exc}[/yellow]")
                    llm_all.update(llm or {})
                rows = persist.idea_rows(cur, run.run_id, as_of, h, regime, {}, llm)
                persist.write_ideas(conn, rows)
                model = str(pred["model"].iloc[0]) if len(pred) else "?"
                calib = pstats["horizons"][h]["calibration"]
                md = render.render_horizon(h, as_of, model, sel, dropped if len(dropped) else None, regime, calib, llm, pred_run)
                out = settings.data_dir / "reports" / as_of.isoformat() / f"ideas_{'1y' if h == '1Y' else 'tactical'}.md"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(md, encoding="utf-8")
                reports[h] = out
                counts[h] = {"long": len(sel["longs"]), "avoid": len(sel["avoid"]), "ood": len(sel["ood"]),
                             "new": int((cont == "new").sum()), "dropped": len(dropped), "rows": len(rows)}  # fmt: skip
            run.stats.update({"predict_run": pred_run, "counts": counts, "reports": {k: str(v) for k, v in reports.items()},
                              "seconds": round(time.perf_counter() - t0, 1), "llm_calls": len(llm_all)})  # fmt: skip
    console.print(f"[green]ideas ok[/green] run {run.run_id} (predict run {pred_run}, as of {as_of}): {counts}")
    for h, p in reports.items():
        console.print(f"  {h}: {p}")
