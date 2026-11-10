"""``ideation tactical …``: the MS2 tactical bake-off (kept out of ``ideation/cli.py`` except one ``add_typer`` line).

* ``tactical wfo``    : monthly walk-forward of the fitted trials (resumable, checkpointed per (trial, fold)).
* ``tactical cpcv``   : CPCV for the top trials + baselines (resumable, checkpointed per (trial, split)).
* ``tactical report`` : metrics, paired arms, DSR/PBO/90-10, gates, persistence (``--persist``).
"""

from __future__ import annotations

import json
import logging
import sys
import time
from typing import Annotated

import pandas as pd
import typer
import yaml
from rich.console import Console

from ideation.config import get_settings
from ideation.logs import setup_logging

app = typer.Typer(no_args_is_help=True, help="MS2 tactical 1-3M bake-off (family tactical-3M).")
console = Console(width=None if sys.stdout.isatty() else 220)
log = logging.getLogger("ideation.tactical")
SNAP = "20261105-e33e7986"


def _ctx():  # type: ignore[no-untyped-def]
    from ideation.tactical.data import load_panel

    settings = get_settings()
    setup_logging(settings.logs_dir, settings.log_level, console=False)
    logging.getLogger("ideation.tactical").setLevel(logging.INFO)
    out = settings.data_dir / "outputs" / SNAP
    panel = load_panel(out, SNAP, settings.config_dir)
    return settings, out, panel


def _vendor_gate_ok(out) -> bool:  # type: ignore[no-untyped-def]
    f = out / "vendor_gate_p3.json"
    return f.exists() and bool(json.loads(f.read_text()).get("passed"))


def _frames(settings, out, panel, need_text: bool, need_vendor: bool):  # type: ignore[no-untyped-def]
    from ideation.tactical import data

    frames = {"base": panel.frame}
    if need_text:
        frames["text"] = data.attach_text(panel, out)
    if need_vendor:
        from ideation.tactical.vendor import build_or_load_vendor

        v = build_or_load_vendor(settings, panel.snap_id, sorted(panel.frame["anchor"].unique()))
        frames["vsurp"] = data.attach_vendor(panel.frame, v)
    return frames


def design_for(spec, frames, panel):  # type: ignore[no-untyped-def]
    from ideation.tactical.data import build_design

    cfg = spec.config
    fset = cfg.get("features", "T0")
    subset = cfg.get("subset")
    if cfg.get("gate") == "vsurp_validity":
        subset = "vsurp_covered"
    base = {"text_covered": frames.get("text"), "vsurp_covered": frames.get("vsurp")}.get(subset, frames["base"])
    return build_design(base, panel.reg, fset, adv_floor_cr=float(cfg.get("adv_floor_cr", 0.2)), subset=subset)


def control_spec(spec):  # type: ignore[no-untyped-def]
    """Paired control arm of #17-20: same learner/config, features T1 only (a protocol arm, not a trial)."""
    from dataclasses import replace

    cfg = {**spec.config, "features": "T1"}
    return replace(spec, trial_id=spec.trial_id + "_CTRL", config=cfg)


@app.command("wfo")
def wfo_cmd(
    only: Annotated[str, typer.Option(help="comma-separated trial ids (default: all fitted trials)")] = "",
    no_importance: Annotated[bool, typer.Option(help="skip clustered permutation importance (90/10 stability)")] = False,
) -> None:
    """Run the walk-forward for every fitted trial (resumable). 3M primary; 1M is scored later from the same scores."""
    from ideation.tactical.engine import run_wfo
    from ideation.trials.registry import load_family

    settings, out, panel = _ctx()
    specs = [s for s in load_family("tactical-3M") if "consensus" not in s.config]
    if only:
        keep = set(only.split(","))
        specs = [s for s in specs if s.trial_id in keep]
    gate_ok = _vendor_gate_ok(out)
    need_vendor = gate_ok and any(s.config.get("allow_vendor_estimates") for s in specs)
    need_text = any("text" in s.config.get("features", "").lower() for s in specs)
    frames = _frames(settings, out, panel, need_text, need_vendor)
    root = settings.data_dir / "tactical_ms2"
    (root / "scores").mkdir(parents=True, exist_ok=True)
    designs: dict = {}
    for spec in specs:
        t0 = time.perf_counter()
        cfg = spec.config
        if cfg.get("gate") == "vsurp_validity" and not gate_ok:
            console.print(f"[yellow]{spec.trial_id}: vendor gate not passed -> failed, not run[/yellow]")
            continue
        runs = [(spec, "trial")]
        if cfg.get("paired_with") and cfg.get("subset") or cfg.get("gate") == "vsurp_validity":
            runs.append((control_spec(spec), "control"))
        for sp, kind in runs:
            key = (sp.config.get("features", "T0"), sp.config.get("subset"), sp.config.get("adv_floor_cr", 0.2),
                   sp.config.get("gate", ""))  # fmt: skip
            if key not in designs:
                designs[key] = design_for(sp, frames, panel)
            des = designs[key]
            scores, infos = run_wfo(sp, des, panel.anchors, panel.label_end, root, importance=not no_importance and kind == "trial",
                                    progress=lambda m: console.print(m))  # fmt: skip
            scores.to_parquet(root / "scores" / f"{sp.num:02d}_{sp.trial_id}.parquet", index=False)
            (root / "scores" / f"{sp.num:02d}_{sp.trial_id}.json").write_text(json.dumps(infos))
        console.print(f"[green]{spec.trial_id} done[/green] {time.perf_counter() - t0:.0f}s")


@app.command("vendor-features")
def vendor_features_cmd() -> None:
    """Build the monthly vendor-surprise features (only if the P3-ear_3d vendor gate passed)."""
    settings, out, panel = _ctx()
    if not _vendor_gate_ok(out):
        console.print("[red]vendor gate (P3 ear_3d) has not passed: nothing built[/red]")
        raise typer.Exit(1)
    from ideation.tactical.vendor import build_or_load_vendor

    v = build_or_load_vendor(settings, panel.snap_id, sorted(panel.frame["anchor"].unique()))
    console.print(f"vendor features: {len(v)} rows")


@app.command("params")
def params_cmd() -> None:
    """Print the protocol constants (importance subsample, windows)."""
    from ideation.tactical import engine

    console.print(yaml.safe_dump({k: getattr(engine, k) for k in
                                  ("EMBARGO_3M", "N_TRAIN", "BLOCK", "IMP_REPEATS", "IMP_MAX_ROWS", "N_GROUPS", "K_TEST")}))  # fmt: skip


_ = pd
