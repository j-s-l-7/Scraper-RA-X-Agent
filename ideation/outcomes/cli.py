"""``ideation outcomes build`` and ``ideation outcomes check`` (05 §5, §10.2.c). Post-anchor outcomes persisted to
``ideation.outcome`` (the only table this package writes), keyed on the (anchor, security) pairs that have labels.

Anchors: June 2016-2024 (horizon 1Y: kinds ``analyst`` and ``fundamental``) and every month-end 2014-01 .. 2025-06
(horizon 3M: ``analyst``; ``fundamental_q`` from 2023-06). Labels are read only through ``load_labels`` after
``unsealed_only``; sealed anchors are never requested.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import date
from typing import Annotated, Any

import pandas as pd
import typer
import yaml
from rich.console import Console
from rich.table import Table

from ideation.config import get_settings, load_methodology
from ideation.labels.cli import labels_path
from ideation.labels.loader import load_labels, unsealed_only
from ideation.pit.calendar import parse_anchors
from ideation.pit.contract import PitStore

app = typer.Typer(no_args_is_help=True, help="Post-anchor 'exceeds expectations' outcomes (05 §5).")
console = Console(width=None if sys.stdout.isatty() else 200)
OUT_DB_COLS = ["anchor", "security_id", "horizon", "kind", "n_events", "out_beat", "out_surp", "out_fs_sales",
                "out_fs_pat", "out_accel", "extra"]  # fmt: skip
QUARTERLY_FROM = date(2023, 6, 30)


def _store(settings: Any) -> PitStore:
    base = PitStore.latest(settings.data_dir)
    return PitStore(base.snapshot_dir, outputs_dir=settings.data_dir / "outputs" / base.snap_id)


def select_anchors(
    store: PitStore, june_from: int, june_to: int, monthly_from: date, monthly_to: date
) -> tuple[list[date], list[date]]:
    """(June 1Y anchors, monthly 3M anchors), sealed ones dropped."""
    june = [a for a in parse_anchors(store.calendar, "june") if june_from <= a.year <= june_to]
    monthly = [a for a in parse_anchors(store.calendar, "monthly") if monthly_from <= a <= monthly_to]
    return unsealed_only(june, "1Y"), unsealed_only(monthly, "3M")


def label_members(store: PitStore, path: Any, anchors: list[date], horizon: str) -> pd.DataFrame:
    """(anchor, security_id, company_id, in_u_score, in_u_score_t) for every labelled name (labels via the loader)."""
    lab = load_labels(unsealed_only(anchors, horizon), horizon, "report", source=path, unsealed=set())
    frames = []
    for a in sorted(set(lab["anchor"])):
        u = store.as_of(a, record=False).universe()[["security_id", "company_id", "in_u_score", "in_u_score_t"]]
        keep = lab.loc[lab["anchor"] == a, ["security_id"]].merge(u, on="security_id", how="left")
        frames.append(keep.assign(anchor=a))
    return pd.concat(frames, ignore_index=True)


def _py(v: Any) -> Any:
    if v is None or v is pd.NA or (isinstance(v, float) and v != v):
        return None
    if isinstance(v, dict):
        return json.dumps(v)
    return v.item() if hasattr(v, "item") else v


def write_db(conn: Any, run_id: int, rows: pd.DataFrame) -> None:
    with conn.transaction():
        for (h, k), g in rows.groupby(["horizon", "kind"]):
            conn.execute(
                "delete from ideation.outcome where horizon = %s and kind = %s and anchor = any(%s)",
                (h, k, sorted(set(g["anchor"]))),
            )
        data = ((*(_py(v) for v in r), run_id) for r in rows[OUT_DB_COLS].itertuples(index=False))
        conn.copy_rows("ideation.outcome", [*OUT_DB_COLS, "run_id"], data)


def check_announcement(store: PitStore, events: pd.DataFrame, late: Any) -> dict[str, Any]:
    from ideation.features.events_vendor import ear_3d_events
    from ideation.outcomes.analyst import announcement_check

    par = yaml.safe_load((get_settings().config_dir / "features_vendor.yaml").read_text())["params"]
    ev = ear_3d_events(late, events, par["gate_from"], par["gate_to"])
    res = announcement_check(ev)
    res.update(window=f"{par['gate_from']}..{par['gate_to']}", snap_id=store.snap_id)
    return res


def print_check(res: dict[str, Any]) -> None:
    t = Table("measure", "value", title=f"Does the surprise sign agree with the announcement return? ({res['window']})")
    for k in ("n_events", "n_quarters", "mean_ear_beat", "mean_ear_miss", "beat_minus_miss", "t_beat_minus_miss",
              "sign_agreement", "spearman_rho", "t_spearman"):  # fmt: skip
        v = res[k]
        t.add_row(k, f"{v:.4f}" if isinstance(v, float) else str(v))
    console.print(t)
    console.print(
        f"surprise sign vs ear_3d: beat minus miss = {res['beat_minus_miss']:.4f}, quarter-clustered "
        f"t = {res['t_beat_minus_miss']:.1f}; sign agreement {100 * res['sign_agreement']:.1f}%"
    )


@app.command("check")
def check_cmd() -> None:
    """The owner-facing check alone: the surprise sign vs the announcement return (quarter-clustered t)."""
    from ideation.outcomes.analyst import load_events

    store = _store(get_settings())
    late = store.as_of(store.calendar.good[-1].astype(object), record=False)
    print_check(check_announcement(store, load_events(late), late))


@app.command("build")
def build_cmd(
    june_from: Annotated[int, typer.Option(help="First June anchor year (1Y).")] = 2016,
    june_to: Annotated[int, typer.Option(help="Last June anchor year (1Y).")] = 2024,
    monthly_from: Annotated[str, typer.Option(help="First monthly anchor (3M).")] = "2014-01-01",
    monthly_to: Annotated[str, typer.Option(help="Last monthly anchor (3M).")] = "2025-06-30",
    force: Annotated[bool, typer.Option("--force", help="Rebuild even if an identical run succeeded.")] = False,
) -> None:
    """Build the outcome rows, persist them to ideation.outcome, print coverage and the surprise-sign check."""
    from ideation.db.writer import Writer
    from ideation.logs import setup_logging
    from ideation.outcomes import analyst, fundamental
    from ideation.runs import RunRegistry

    settings = get_settings()
    setup_logging(settings.logs_dir, settings.log_level, console=False)
    meth = load_methodology()
    store = _store(settings)
    t0 = time.perf_counter()
    june, monthly = select_anchors(store, june_from, june_to, date.fromisoformat(monthly_from),
                                   date.fromisoformat(monthly_to))  # fmt: skip
    lpath = labels_path(settings.data_dir, store.snap_id)
    observed_to = store.calendar.good[-1].astype(object)
    late = store.as_of(observed_to, record=False)
    params = {"june": [str(june[0]), str(june[-1]), len(june)],
              "monthly": [str(monthly[0]), str(monthly[-1]), len(monthly)],
              "derived": store.derived_sha()}  # fmt: skip
    with (
        Writer.from_settings(settings).connect() as conn,
        RunRegistry(conn).track(
            "outcomes", config_hash=meth.config_hash, snap_id=store.snap_id, params=params, force=force
        ) as run,  # fmt: skip
    ):
        if run.skipped:
            console.print(f"skipped: identical run {run.record.stats} already ok (use --force)")
            return
        events = analyst.load_events(late)
        panel_a, panel_q = fundamental.build_panel(late, "A"), fundamental.build_panel(late, "Q")
        m1y = label_members(store, lpath, june, "1Y")
        m3m = label_members(store, lpath, monthly, "3M")
        frames, cov = [], []
        for a, g in m1y.groupby("anchor"):
            an = analyst.build_analyst(events, g, a, "1Y", observed_to)
            fu = fundamental.build_fundamental(panel_a, g, a)
            frames += [an, fu]
            cov.append(_cov(a, "1Y", g, an, fu))
        for a, g in m3m.groupby("anchor"):
            an = analyst.build_analyst(events, g, a, "3M", observed_to)
            frames.append(an)
            fq = None
            if a >= QUARTERLY_FROM:
                fq = fundamental.build_fundamental_q(panel_q, g, a)
                frames.append(fq)
            cov.append(_cov(a, "3M", g, an, fq))
        rows = pd.concat(frames, ignore_index=True)
        write_db(conn, run.run_id, rows)
        check = check_announcement(store, events, late)
        counts = rows.groupby(["horizon", "kind"]).size()
        run.stats.update({"rows": len(rows), "counts": {f"{h}/{k}": int(n) for (h, k), n in counts.items()},
                          "check": check, "seconds": round(time.perf_counter() - t0, 1)})  # fmt: skip
    _print_summary(rows, pd.DataFrame(cov))
    print_check(check)
    console.print(f"[green]outcomes built[/green]: {len(rows)} rows, {time.perf_counter() - t0:.1f} s")


def _cov(a: date, h: str, members: pd.DataFrame, an: pd.DataFrame, other: pd.DataFrame | None) -> dict[str, Any]:
    n = len(members)
    in_u = members["in_u_score"].fillna(False).astype(bool) if h == "1Y" else members["in_u_score_t"].fillna(False)
    u_ids = set(members.loc[in_u, "security_id"])
    return {"anchor": a, "horizon": h, "n_labelled": n, "n_universe": len(u_ids),
            "analyst": len(an), "analyst_in_u": len(u_ids & set(an["security_id"])),
            "other": 0 if other is None else len(other),
            "other_in_u": 0 if other is None else len(u_ids & set(other["security_id"]))}  # fmt: skip


def _print_summary(rows: pd.DataFrame, cov: pd.DataFrame) -> None:
    t = Table("horizon", "kind", "rows", "anchors", title="Outcome rows persisted")
    for (h, k), g in rows.groupby(["horizon", "kind"]):
        t.add_row(h, k, str(len(g)), str(g["anchor"].nunique()))
    console.print(t)
    june = cov[cov["horizon"] == "1Y"]
    t2 = Table("anchor", "U_score", "analyst cov", "fundamental cov", title="June 1Y coverage of U_score")
    pct = lambda a, n: "n/a" if not n else f"{100 * a / n:.1f}%"  # noqa: E731
    for r in june.itertuples():
        t2.add_row(str(r.anchor), str(r.n_universe), pct(r.analyst_in_u, r.n_universe),
                   pct(r.other_in_u, r.n_universe))  # fmt: skip
    console.print(t2)
    m = cov[cov["horizon"] == "3M"].assign(year=lambda d: pd.to_datetime(d["anchor"]).dt.year)
    y = m.groupby("year")[["n_universe", "analyst_in_u", "other_in_u"]].mean()
    t3 = Table("year", "mean U_score_T", "analyst cov (3M)", "fund_q cov", title="Monthly 3M coverage of U_score_T")
    for yr, r in y.iterrows():
        t3.add_row(str(yr), f"{r.n_universe:.0f}", pct(r.analyst_in_u, r.n_universe), pct(r.other_in_u, r.n_universe))
    console.print(t3)
