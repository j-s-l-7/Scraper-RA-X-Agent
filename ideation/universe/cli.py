"""``ideation universe build --anchors june,monthly`` (05 §10.3 P1). Reads the latest snapshot through PitStore,
writes ``IDEATION_DATA_DIR/outputs/<snap_id>/universe_*.parquet`` and ``ideation.universe_member`` /
``universe_coverage`` / ``run_manifest``."""

from __future__ import annotations

import hashlib
import logging
import sys
import time
from datetime import date
from pathlib import Path
from typing import Annotated, Any

import pandas as pd
import typer
from rich.console import Console
from rich.table import Table

from ideation.config import canonical_json, get_settings, load_methodology
from ideation.pit.calendar import parse_anchors
from ideation.pit.contract import PitStore, records_to_rows
from ideation.universe.build import UniverseConfig, build_universe
from ideation.universe.coverage import REFERENCE_02, coverage_02

app = typer.Typer(no_args_is_help=True, help="PIT universes U_score / U_score_T / U1000 (05 §1).")
console = Console(width=None if sys.stdout.isatty() else 200)
log = logging.getLogger("ideation.universe")

UM_COLS = (
    "anchor", "security_id", "symbol", "series", "isin", "company_id", "adv63_lakh", "n_days63", "rank_adv",
    "mcap_pit_cr", "mcap_src", "mcap_flag", "listed_sessions", "scorable", "exclusion_reason", "traded_ok",
    "last_trade", "in_u1000", "in_u500", "in_u_score", "in_u_score_adv05", "in_u_score_t", "u_score_t_reason",
    "has_fy_at_anchor", "mcap_a_cr", "mcap_b1_cr", "mcap_b2_cr", "feature_complete",
)  # fmt: skip


def outputs_dir(data_dir: Path, snap_id: str) -> Path:
    d = data_dir / "outputs" / snap_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _py(v: Any) -> Any:
    if v is None or (isinstance(v, float) and pd.isna(v)) or v is pd.NA:
        return None
    if hasattr(v, "item"):
        return v.item()
    return v


def load_feature_complete(path: Path) -> dict[date, pd.Series]:
    """``feature_complete`` by anchor, indexed by ``security_id``, from ``ideation features build`` (P2). Anchors
    without features get no entry, so ``features_incomplete`` (05 §1.1) is only applied where features exist."""
    if not path.is_file():
        return {}
    df = pd.read_parquet(path, columns=["anchor", "security_id", "feature_complete"])
    df["anchor"] = pd.to_datetime(df["anchor"]).dt.date
    return {a: g.set_index("security_id")["feature_complete"] for a, g in df.groupby("anchor")}


def file_sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12] if path.is_file() else None


def build_all(
    store: PitStore, cfg: UniverseConfig, anchors: list[date], feature_complete: dict[date, pd.Series] | None = None
) -> tuple[pd.DataFrame, list[dict], list]:
    members, covs, records = [], [], []
    feature_complete = feature_complete or {}
    ref_anchors = {date.fromisoformat(a) for a in REFERENCE_02}
    for a in anchors:
        view = store.as_of(a)
        mem, cov = build_universe(view, cfg, feature_complete=feature_complete.get(a))
        if a in ref_anchors:
            cov["coverage_02"] = coverage_02(store, a, cfg.raw)
        members.append(mem)
        covs.append(cov)
        records.extend(view.records)
    return pd.concat(members, ignore_index=True), covs, records


def write_db(conn: Any, run_id: int, members: pd.DataFrame, covs: list[dict], records: list) -> None:
    anchors = sorted({c["anchor"] for c in covs})
    with conn.transaction():
        conn.execute("delete from ideation.universe_member where anchor = any(%s)", (anchors,))
        conn.execute("delete from ideation.universe_coverage where anchor = any(%s)", (anchors,))
        rows = ((*(_py(v) for v in r), run_id) for r in members[list(UM_COLS)].itertuples(index=False))
        conn.copy_rows("ideation.universe_member", [*UM_COLS, "run_id"], rows)
        for c in covs:
            r = c["reasons_u_score"]
            u1000_shares = (c["share_u_score_in_u1000"],)
            conn.execute(
                "insert into ideation.universe_coverage (anchor, n_traded, n_universe, n_scorable, n_no_company,"
                " n_no_fundamentals, n_mcap_below_floor, n_be_series, n_too_new, scorable_share_top500,"
                " scorable_share_top1000, adv_marginal_lakh, mcap_marginal_cr, extra, run_id, n_u_score,"
                " n_u_score_adv05, n_u_score_t, n_u1000, n_thin_trading, n_adv_below_floor, n_mcap_unknown,"
                " n_mcap_below_floor_100) values (" + ", ".join(["%s"] * 13 + ["%s::jsonb"] + ["%s"] * 9) + ")",
                (
                    c["anchor"], c["n_traded"], c["n_universe"], c["n_scorable"], r.get("no_company", 0),
                    r.get("no_fundamentals_at_anchor", 0), c["u1000_mcap_below_500"], c["u_score_be_series"],
                    r.get("too_new", 0), c["share_u_score_in_u500"], *u1000_shares, c["adv_marginal_lakh"],
                    c["mcap_marginal_cr"], canonical_json(c), run_id, c["n_u_score"],
                    c["n_u_score_adv05"], c["n_u_score_t"], c["n_u1000"], r.get("thin_trading", 0),
                    r.get("adv_below_floor", 0), r.get("mcap_unknown", 0), r.get("mcap_below_floor_100", 0),
                ),
            )  # fmt: skip
        conn.execute("delete from ideation.run_manifest where run_id = %s", (run_id,))
        conn.copy_rows(
            "ideation.run_manifest",
            ["run_id", "anchor", "item", "n_rows", "sha256", "max_available_from"],
            ((run_id, *r) for r in records_to_rows(records)),
        )


def coverage_table(covs: list[dict], title: str) -> Table:
    t = Table("anchor", "traded", "U1000", "U_score", "≥0.5cr", "U_score_T", "no_co", "thin", "adv<", "new",
              "mcap<100", "mcap?", "src A/B1/B2", title=title)  # fmt: skip
    for c in covs:
        r, s = c["reasons_u_score"], c["mcap_src_u_score"]
        t.add_row(
            str(c["anchor"]), str(c["n_traded"]), str(c["n_u1000"]), str(c["n_u_score"]), str(c["n_u_score_adv05"]),
            str(c["n_u_score_t"]), str(r.get("no_company", 0)), str(r.get("thin_trading", 0)),
            str(r.get("adv_below_floor", 0)), str(r.get("too_new", 0)), str(r.get("mcap_below_floor_100", 0)),
            str(r.get("mcap_unknown", 0)), f"{s.get('A', 0)}/{s.get('B1', 0)}/{s.get('B2', 0)}",
        )  # fmt: skip
    return t


@app.command("build")
def build_cmd(
    anchors: Annotated[str, typer.Option(help="june,march,monthly and/or YYYY-MM-DD dates.")] = "june,monthly",
    force: Annotated[bool, typer.Option("--force", help="Rebuild even if an identical run succeeded.")] = False,
) -> None:
    """Build the PIT universes for the anchors and store them (parquet + ideation schema)."""
    from ideation.db.writer import Writer
    from ideation.logs import setup_logging
    from ideation.runs import RunRegistry

    settings = get_settings()
    setup_logging(settings.logs_dir, settings.log_level, console=False)
    meth = load_methodology()
    store = PitStore.latest(settings.data_dir)
    cfg = UniverseConfig.from_methodology(meth.data)
    anchor_list = parse_anchors(store.calendar, anchors)
    t0 = time.perf_counter()
    with Writer.from_settings(settings).connect() as conn:
        reg = RunRegistry(conn)
        feat_file = outputs_dir(settings.data_dir, store.snap_id) / "features_fundamental.parquet"
        params = {"anchors": anchors, "n_anchors": len(anchor_list), "derived": store.derived_sha(),
                  "features": file_sha(feat_file)}  # fmt: skip
        with reg.track("universe", config_hash=meth.config_hash, snap_id=store.snap_id, params=params,
                       force=force) as run:  # fmt: skip
            if run.skipped:
                console.print(f"skipped: identical run {run.record.stats} already ok (use --force)")
                return
            fc = load_feature_complete(feat_file)
            members, covs, records = build_all(store, cfg, anchor_list, fc)
            out = outputs_dir(settings.data_dir, store.snap_id)
            members.to_parquet(out / "universe_member.parquet", index=False)
            pd.DataFrame([{**c, "anchor": c["anchor"]} for c in covs]).assign(
                payload=[canonical_json(c) for c in covs]
            )[["anchor", "payload"]].to_parquet(out / "universe_coverage.parquet", index=False)
            write_db(conn, run.run_id, members, covs, records)
            run.stats.update({"anchors": len(anchor_list), "rows": len(members), "anchors_with_features":
                              sum(a in fc for a in anchor_list),
                              "seconds": round(time.perf_counter() - t0, 1)})  # fmt: skip
    june = [c for c in covs if c["anchor"].month == 6]
    console.print(coverage_table(june, f"U_score / U_score_T per June anchor (snapshot {store.snap_id})"))
    for c in covs:
        if "coverage_02" in c:
            console.print(f"02 §0.1 reproduction {c['anchor']}: {c['coverage_02']['delta_pp_02map']} pp")
    console.print(f"[green]universe built[/green]: {len(anchor_list)} anchors, {len(members)} rows,"
                  f" {time.perf_counter() - t0:.1f} s")  # fmt: skip
