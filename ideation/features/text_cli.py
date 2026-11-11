# ruff: noqa: E501
"""``ideation text features`` and ``ideation text vendor-gate`` (05 §7.3, §6.3; PT2).

Registered onto the ``text`` sub-app by ``ideation/text/cli.py`` (one call). The commands live here because
``ideation/text`` may not import the database layer. Outputs, under ``IDEATION_DATA_DIR/outputs/<snap_id>/``:
``features_text.parquet`` (+ ``features_text_coverage.parquet``) and ``vendor_gate.json``; plus a ``run`` row and the
per-accessor ``run_manifest`` records. Nothing is written to the lake data tables.
"""

from __future__ import annotations

import json
import sys
import time
from typing import Annotated

import pandas as pd
import typer
import yaml
from rich.console import Console
from rich.table import Table

from ideation.config import get_settings, load_methodology
from ideation.pit.calendar import parse_anchors
from ideation.pit.contract import TEXT_DOCS_FILE, PitStore, records_to_rows

console = Console(width=None if sys.stdout.isatty() else 200)
FEATURES_FILE = "features_text.parquet"
COVERAGE_FILE = "features_text_coverage.parquet"
GATE_FILE = "vendor_gate.json"


def _store(settings) -> PitStore:  # type: ignore[no-untyped-def]
    base = PitStore.latest(settings.data_dir)
    return PitStore(
        base.snapshot_dir,
        outputs_dir=settings.data_dir / "outputs" / base.snap_id,
        text_dir=settings.data_dir / "text",
    )


def coverage_tables(cov: pd.DataFrame) -> None:
    c = cov.assign(anchor=pd.to_datetime(cov["anchor"]))
    june = c[c["anchor"].dt.month == 6]
    t = Table("anchor", "U_score", "cur", "cur+prev", "ppt", "U_score_T", "cur", "cur+prev", "ppt",
              title="Text coverage, universe-restricted, June anchors (U_score | U_score_T)")  # fmt: skip
    pct = lambda a, n: "n/a" if not n else f"{100 * a / n:.1f}%"  # noqa: E731
    for r in june.itertuples():
        t.add_row(str(r.anchor.date()), str(r.n_u), pct(r.u_cur, r.n_u), pct(r.u_cur_prev, r.n_u), pct(r.u_ppt, r.n_u),
                  str(r.n_ut), pct(r.ut_cur, r.n_ut), pct(r.ut_cur_prev, r.n_ut), pct(r.ut_ppt, r.n_ut))  # fmt: skip
    console.print(t)
    mon = c.assign(year=c["anchor"].dt.year).groupby("year").agg(
        anchors=("anchor", "size"), n_ut=("n_ut", "mean"), ut_cur=("ut_cur", "mean"),
        ut_cur_prev=("ut_cur_prev", "mean"), ut_ppt=("ut_ppt", "mean"),
    )  # fmt: skip
    t2 = Table("year", "anchors", "mean U_score_T", "cur", "cur+prev", "ppt",
               title="Text coverage, all anchors of the year (mean of month-end anchors; U_score_T)")  # fmt: skip
    for y, r in mon.iterrows():
        t2.add_row(str(y), str(int(r.anchors)), f"{r.n_ut:.0f}", pct(r.ut_cur, r.n_ut), pct(r.ut_cur_prev, r.n_ut),
                   pct(r.ut_ppt, r.n_ut))  # fmt: skip
    console.print(t2)


ANNUAL_FILE = "features_annual_text.parquet"
ANNUAL_COVERAGE_FILE = "features_annual_text_coverage.parquet"
TREND_FILE = "features_text_trend.parquet"
TREND_COVERAGE_FILE = "features_text_trend_coverage.parquet"


def merge_coverage(cov_a: pd.DataFrame, cov_t: pd.DataFrame) -> pd.DataFrame:
    """Trend coverage restricted to the annual coverage anchors; robust to ``date`` vs ``Timestamp`` anchors."""
    a = cov_a[["anchor"]].assign(anchor=pd.to_datetime(cov_a["anchor"]))
    t = cov_t.assign(anchor=pd.to_datetime(cov_t["anchor"]))
    return t.merge(a.drop_duplicates(), on="anchor")


def pt3_coverage_tables(ann: pd.DataFrame, trend: pd.DataFrame) -> None:
    a = ann.assign(anchor=pd.to_datetime(ann["anchor"]))
    a = a[a["anchor"].dt.month == 6]
    t = trend.assign(anchor=pd.to_datetime(trend["anchor"]))
    t = t[t["anchor"].dt.month == 6].set_index("anchor")
    pct = lambda x, n: "n/a" if not n else f"{100 * x / n:.1f}%"  # noqa: E731
    tab = Table("anchor", "U_score", "AR cur", "AR cur+prev", "ar_tone", "ar_unc", "ar_aud", "ar_rpt", "ar_cl", "d_ar_tone",
                "d_ar_aud", "calls>=3", title="PT3 coverage on U_score, June anchors (annual reports; multi-call trends)")  # fmt: skip
    for r in a.itertuples():
        tr = t.loc[r.anchor] if r.anchor in t.index else None
        tab.add_row(str(r.anchor.date()), str(r.n_u), pct(r.u_cur, r.n_u), pct(r.u_cur_prev, r.n_u), pct(r.nn_ar_tone, r.n_u),
                    pct(r.nn_ar_unc, r.n_u), pct(r.nn_ar_aud, r.n_u), pct(r.nn_ar_rpt, r.n_u), pct(r.nn_ar_cl, r.n_u),
                    pct(r.nn_d_ar_tone, r.n_u), pct(r.nn_d_ar_aud, r.n_u),
                    "n/a" if tr is None else pct(tr["u_trend"], tr["n_u"]))  # fmt: skip
    console.print(tab)


def register(app: typer.Typer) -> None:
    @app.command("features")
    def features_cmd(
        anchors: Annotated[
            str, typer.Option(help="june,march,monthly and/or YYYY-MM-DD dates.")
        ] = "june,march,monthly",
        force: Annotated[bool, typer.Option("--force", help="Rebuild even if an identical run succeeded.")] = False,
    ) -> None:
        """Build the PIT text features (05 §7.3) for the anchors and print universe-restricted coverage."""
        from ideation.db.writer import Writer
        from ideation.features.registry import load_registry
        from ideation.features.text import build_text, coverage_row, load_params
        from ideation.logs import setup_logging
        from ideation.runs import RunRegistry

        settings = get_settings()
        setup_logging(settings.logs_dir, settings.log_level, console=False)
        meth = load_methodology()
        reg = load_registry(settings.config_dir)
        store = _store(settings)
        params = load_params(settings.config_dir)
        anchor_list = parse_anchors(store.calendar, anchors)
        docs = store.text_dir / TEXT_DOCS_FILE  # type: ignore[operator]
        from ideation.universe.cli import file_sha

        t0 = time.perf_counter()
        with Writer.from_settings(settings).connect() as conn:
            runs = RunRegistry(conn)
            rp = {"anchors": anchors, "n_anchors": len(anchor_list), "derived": store.derived_sha(),
                  "docs_sha": file_sha(docs), "universe_sha": file_sha(store.outputs_dir / "universe_member.parquet")}  # type: ignore[operator]  # fmt: skip
            with runs.track("features_text", config_hash=meth.config_hash, snap_id=store.snap_id, params=rp,
                            force=force) as run:  # fmt: skip
                if run.skipped:
                    console.print(f"skipped: identical run {run.record.stats} already ok (use --force)")
                    return
                frames, records, cov = [], [], []
                for a in anchor_list:
                    view = store.as_of(a)
                    uni = view.universe()
                    members = uni[uni["traded_ok"] & uni["company_id"].notna()]
                    f = build_text(view, members, reg, params)
                    frames.append(f)
                    cov.append(coverage_row(a, uni, f))
                    records.extend(view.records)
                feats, covdf = pd.concat(frames, ignore_index=True), pd.DataFrame(cov)
                out = store.outputs_dir / FEATURES_FILE  # type: ignore[operator]
                feats.to_parquet(out, index=False)
                covdf.to_parquet(store.outputs_dir / COVERAGE_FILE, index=False)  # type: ignore[operator]
                conn.execute("delete from ideation.run_manifest where run_id = %s", (run.run_id,))
                conn.copy_rows("ideation.run_manifest",
                               ["run_id", "anchor", "item", "n_rows", "sha256", "max_available_from"],
                               ((run.run_id, *r) for r in records_to_rows(records)))  # fmt: skip
                run.stats.update({"anchors": len(anchor_list), "rows": len(feats),
                                  "seconds": round(time.perf_counter() - t0, 1),
                                  "coverage": [{k: (str(v) if k == "anchor" else int(v)) for k, v in r.items()}
                                               for r in cov]})  # fmt: skip
        coverage_tables(covdf)
        console.print(f"[green]text features built[/green]: {len(anchor_list)} anchors, {len(feats)} rows, "
                      f"{time.perf_counter() - t0:.1f} s -> {out}")  # fmt: skip

    @app.command("pt3-features")
    def pt3_features_cmd(
        anchors: Annotated[
            str, typer.Option(help="june,march,monthly and/or YYYY-MM-DD dates.")
        ] = "june,march,monthly",
        force: Annotated[bool, typer.Option("--force", help="Rebuild even if an identical run succeeded.")] = False,
    ) -> None:
        """PT3: PIT annual-report features (levels + YoY deltas) and multi-call text trends; prints coverage on U_score."""
        from ideation.db.writer import Writer
        from ideation.features.annual_text import build_annual, load_params
        from ideation.features.annual_text import coverage_row as ann_cov
        from ideation.features.registry import load_registry
        from ideation.features.text import build_text_trend, load_trend_params
        from ideation.logs import setup_logging
        from ideation.pit.contract import ANNUAL_DOCS_FILE
        from ideation.runs import RunRegistry

        settings = get_settings()
        setup_logging(settings.logs_dir, settings.log_level, console=False)
        meth = load_methodology()
        reg = load_registry(settings.config_dir)
        store = _store(settings)
        params, tparams = load_params(settings.config_dir), load_trend_params(settings.config_dir)
        anchor_list = parse_anchors(store.calendar, anchors)
        from ideation.universe.cli import file_sha

        t0 = time.perf_counter()
        annual_df, cov_a, cov_t = pd.DataFrame(), pd.DataFrame(), pd.DataFrame()  # bound on every path
        skipped = False
        with Writer.from_settings(settings).connect() as conn:
            runs = RunRegistry(conn)
            rp = {"anchors": anchors, "n_anchors": len(anchor_list), "derived": store.derived_sha(),
                  "annual_sha": file_sha(store.text_dir / ANNUAL_DOCS_FILE),  # type: ignore[operator]
                  "text_sha": file_sha(store.text_dir / TEXT_DOCS_FILE),  # type: ignore[operator]
                  "universe_sha": file_sha(store.outputs_dir / "universe_member.parquet")}  # type: ignore[operator]  # fmt: skip
            with runs.track("features_annual_text", config_hash=meth.config_hash, snap_id=store.snap_id, params=rp,
                            force=force) as run:  # fmt: skip
                if run.skipped:
                    console.print(f"skipped: identical run {run.record.stats} already ok (use --force)")
                    skipped = True
                    return
                fa, ft, ca, ct, records = [], [], [], [], []
                for a in anchor_list:
                    view = store.as_of(a)
                    uni = view.universe()
                    members = uni[uni["traded_ok"] & uni["company_id"].notna()]
                    fr = build_annual(view, members, reg, params)
                    tr = build_text_trend(view, members, reg, tparams)
                    fa.append(fr)
                    ft.append(tr)
                    ca.append(ann_cov(a, uni, fr))
                    ids = uni.loc[uni["in_u_score"].fillna(False).astype(bool), "security_id"]
                    g = tr.set_index("security_id").reindex(ids)
                    ct.append({"anchor": a, "n_u": len(ids), "u_trend": int((g["isna_trend"] == 0).sum())})
                    records.extend(view.records)
                annual_df, trend_df = pd.concat(fa, ignore_index=True), pd.concat(ft, ignore_index=True)
                annual_df.to_parquet(store.outputs_dir / ANNUAL_FILE, index=False)  # type: ignore[operator]
                trend_df.to_parquet(store.outputs_dir / TREND_FILE, index=False)  # type: ignore[operator]
                cov_a, cov_t = pd.DataFrame(ca), pd.DataFrame(ct)
                cov_a.to_parquet(store.outputs_dir / ANNUAL_COVERAGE_FILE, index=False)  # type: ignore[operator]
                cov_t.to_parquet(store.outputs_dir / TREND_COVERAGE_FILE, index=False)  # type: ignore[operator]
                conn.execute("delete from ideation.run_manifest where run_id = %s", (run.run_id,))
                conn.copy_rows("ideation.run_manifest",
                               ["run_id", "anchor", "item", "n_rows", "sha256", "max_available_from"],
                               ((run.run_id, *r) for r in records_to_rows(records)))  # fmt: skip
                run.stats.update({"anchors": len(anchor_list), "rows_annual": len(annual_df),
                                  "seconds": round(time.perf_counter() - t0, 1)})  # fmt: skip
        if skipped or cov_a.empty:
            return
        pt3_coverage_tables(cov_a, merge_coverage(cov_a, cov_t))
        console.print(f"[green]PT3 features built[/green]: {len(anchor_list)} anchors, {len(annual_df)} rows, "
                      f"{time.perf_counter() - t0:.1f} s -> {store.outputs_dir}")  # fmt: skip

    @app.command("vendor-gate")
    def vendor_gate_cmd(
        ear: Annotated[
            str, typer.Option(help="local (PT2's own ear_3d) | p3 (features/events.py via features_tech)")
        ] = "local",
    ) -> None:
        """05 §6.3 validity gate for the vendor-surprise challenger: Spearman(surprise, ear_3d), quarter-clustered t."""
        from ideation.features.events_vendor import validity_gate, validity_gate_p3

        settings = get_settings()
        store = _store(settings)
        params = yaml.safe_load((settings.config_dir / "features_vendor.yaml").read_text())["params"]
        t0 = time.perf_counter()
        view = store.as_of(store.calendar.good[-1].astype(object), record=False)
        if ear == "p3":
            ep = pd.read_parquet(store.outputs_dir / "features_tech.parquet",  # type: ignore[operator]
                                 columns=["anchor", "security_id", "ear_3d", "ear_days"])  # fmt: skip
            res = validity_gate_p3(view, params, ep, store.calendar.good)
            res["snap_id"] = store.snap_id
            (store.outputs_dir / "vendor_gate_p3.json").write_text(json.dumps(res, indent=1, default=str))  # type: ignore[operator]
            console.print({k: v for k, v in res.items()})
            console.print(
                "vendor validity gate with P3 ear_3d: [bold]" + ("PASS" if res["passed"] else "FAIL") + "[/bold]"
            )
            return
        res = validity_gate(view, params)
        res["snap_id"] = store.snap_id
        (store.outputs_dir / GATE_FILE).write_text(json.dumps(res, indent=1, default=str))  # type: ignore[operator]
        t = Table("year", "events", "spearman", "beat rate", "median |surprise| %", title="Vendor surprise vs ear_3d")
        for r in res["per_year"]:
            t.add_row(str(int(r["year"])), str(int(r["n"])), f"{r['rho']:.3f}", f"{r['beat_rate']:.3f}",
                      f"{r['median_abs_surp']:.1f}")  # fmt: skip
        console.print(t)
        console.print(f"estimator: {res['estimator']}; events {res['gate_from']} .. {res['gate_to']}")
        console.print(
            f"n_events={res['n_events']} n_quarters={res['n_quarters']} rho={res['rho']:.4f} "
            f"t_clustered={res['t_clustered']:.2f}; FM mean rho={res['fama_macbeth_mean_rho']:.4f} "
            f"FM t={res['fama_macbeth_t']:.2f}; positive quarters {res['share_positive_quarters']:.0%}"
        )
        verdict = "PASS" if res["passed"] else "FAIL (trials 19-20 to be logged failed, not run)"
        console.print(
            f"vendor validity gate (rho > 0 and t > 3): [bold]{verdict}[/bold]  ({time.perf_counter() - t0:.1f} s)"
        )
