# ruff: noqa: E501
"""``ideation lab ...``: build monthly inputs, run the lab trials, write the report."""

from __future__ import annotations

import os
import sys
from typing import Annotated

import typer
from rich.console import Console

app = typer.Typer(no_args_is_help=True, help="Accuracy lab: monthly-anchor 1Y evaluation (no DB writes).")
console = Console(width=None if sys.stdout.isatty() else 200)


def _threads() -> None:
    for k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "MKL_NUM_THREADS"):
        os.environ[k] = "4"


@app.command("build")
def build_cmd(force: Annotated[bool, typer.Option("--force")] = False) -> None:
    """Monthly fundamental + quality rebuild into IDEATION_DATA_DIR/lab/<snap>/ (about 11 min)."""
    from ideation.lab.build_fund import build_monthly_fundamental, build_monthly_quality

    console.print(build_monthly_fundamental(force=force))
    console.print(build_monthly_quality(force=force))


@app.command("run")
def run_cmd(
    only: Annotated[str, typer.Option(help="Comma-separated trial ids (default all).")] = "",
    snap: Annotated[str, typer.Option()] = "",
) -> None:
    """Fit the registered 1Y-monthly-v2 trials in the monthly and June designs (checkpointed, resumable)."""
    _threads()
    from threadpoolctl import threadpool_limits

    from ideation.config import get_settings
    from ideation.lab.run import LabRun

    st = get_settings()
    snap_id = snap or sorted(p.name for p in (st.data_dir / "outputs").iterdir() if (p / "features_fundamental.parquet").exists())[-1]
    lr = LabRun(st.data_dir, snap_id, log=lambda m: console.print(m, highlight=False))
    with threadpool_limits(4):
        lr.run_all(only={t for t in only.split(",") if t} or None)


@app.command("report")
def report_cmd(snap: Annotated[str, typer.Option()] = "") -> None:
    """Write the E0/E1 result tables (markdown + json) under IDEATION_DATA_DIR/lab/<snap>/ and the verdict inputs."""
    _threads()
    from ideation.config import get_settings
    from ideation.lab.report import write_all

    st = get_settings()
    snap_id = snap or sorted(p.name for p in (st.data_dir / "outputs").iterdir() if (p / "features_fundamental.parquet").exists())[-1]
    console.print(write_all(st.data_dir, snap_id, log=lambda m: console.print(m, highlight=False)))
