"""Mandatory "Data and Bias" block (02 §8.2 plus the three 05 §8 lines): ``render_bias_block(run_id) -> str``.

Numbers come only from ``ideation.*`` tables, read through the guarded writer connection:

* ``ideation.run``: the run, its snapshot and code version;
* ``ideation.snapshot``: lake as-of and content hash;
* ``ideation.universe_coverage``: N per anchor and the 02 §0.1 reproduction;
* the ``bias_inputs`` of the latest ok ``labels`` run on the same snapshot: exits, rights, coverage bias, the 02
  §0.2 reproduction;
* ``ideation.holdout_unseal``.

Nothing is read from the lake. Fields owned by later phases (trial N, DSR, ΔIC, text coverage) print as
``n/a (P4)`` / ``n/a (PT)`` until those phases fill them. A run without coverage or labels raises
:class:`BiasBlockError`, because a report may not ship without the block (02 §8.2).
"""

from __future__ import annotations

from typing import Any

from ideation.labels.loader import SEALED_RANGES


class BiasBlockError(RuntimeError):
    """The numbers the block needs are missing: the report must not be generated."""


def _fmt_map(d: dict[str, Any], fmt: str = "{}", keep: int = 12) -> str:
    items = sorted(d.items())[-keep:]
    return ", ".join(f"{k[:7] if len(k) >= 7 else k} {fmt.format(v)}" for k, v in items) or "n/a"


def gather(conn: Any, run_id: int) -> dict[str, Any]:
    run = conn.execute(
        "select run_id, kind, snap_id, git_sha, config_hash, params, stats from ideation.run where run_id = %s",
        (run_id,),
    ).fetchone()
    if run is None:
        raise BiasBlockError(f"run {run_id} not found")
    _rid, kind, snap_id, git_sha, config_hash, params, stats = run
    snap = conn.execute(
        "select snap_id, lake_max_bhav, manifest->>'content_sha256', manifest->'lake_state'->>'max_fact_ts'"
        " from ideation.snapshot where snap_id = coalesce(%s, (select snap_id from ideation.snapshot"
        " order by created_at desc limit 1))",
        (snap_id,),
    ).fetchone()
    if snap is None:
        raise BiasBlockError("no registered snapshot")
    labels_stats = stats if kind == "labels" else None
    if labels_stats is None or "bias_inputs" not in (labels_stats or {}):
        row = conn.execute(
            "select stats from ideation.run where kind = 'labels' and status = 'ok' and snap_id = %s"
            " order by run_id desc limit 1",
            (snap[0],),
        ).fetchone()
        labels_stats = row[0] if row else None
    if not labels_stats or "bias_inputs" not in labels_stats:
        raise BiasBlockError(f"no ok labels run with bias inputs for snapshot {snap[0]}")
    cov = conn.execute(
        "select anchor, n_u_score, n_u_score_adv05, n_u_score_t, n_u1000, scorable_share_top1000, extra"
        " from ideation.universe_coverage order by anchor"
    ).fetchall()
    if not cov:
        raise BiasBlockError("ideation.universe_coverage is empty")
    unsealed = [r[0] for r in conn.execute("select family from ideation.holdout_unseal order by 1").fetchall()]
    return {
        "run_id": run_id, "kind": kind, "git_sha": git_sha, "config_hash": config_hash, "params": params or {},
        "stats": stats or {}, "snap_id": snap[0], "lake_max_bhav": snap[1], "content_sha": snap[2],
        "lake_fact_ts": snap[3], "bias": labels_stats["bias_inputs"], "coverage": cov, "unsealed": unsealed,
    }  # fmt: skip


def render_from_facts(f: dict[str, Any]) -> str:
    b = f["bias"]
    june = [r for r in f["coverage"] if r[0].month == 6 and r[0].year >= 2015]
    n_score = {str(r[0]): r[1] for r in june}
    n_05 = {str(r[0]): r[2] for r in june}
    n_t = {str(r[0]): r[3] for r in june}
    share = {str(r[0]): r[5] for r in june}
    repro = [r[6]["coverage_02"] for r in f["coverage"] if r[6] and "coverage_02" in r[6]]
    mix: dict[str, int] = {}
    flagged = 0
    for r in june:
        for k, v in (r[6] or {}).get("mcap_src_u_score", {}).items():
            mix[k] = mix.get(k, 0) + int(v)
        flagged += int((r[6] or {}).get("mcap_flag_u_score", 0))
    ex = b["exits_02"]
    p = f["params"]
    anchors = b.get("june_1y_anchors") or []
    first_a, last_a = (anchors[0], anchors[-1]) if anchors else ("n/a", "n/a")
    trials = p.get("trials_n", "n/a (P4)")
    dsr = p.get("dsr", "n/a (P4)")
    sealed = "; ".join(f"{h} {lo}..{'' if hi.year == 9999 else hi}" for h, (lo, hi) in SEALED_RANGES.items())
    lines = [
        f"DATA & BIAS  (run {f['run_id']}, code {str(f['git_sha'])[:12]}, snapshot {f['snap_id']} "
        f"[{str(f['content_sha'])[:12]}], lake as-of {f['lake_max_bhav']} / facts {str(f['lake_fact_ts'])[:19]})",
        f"Anchors: {p.get('family', '1Y-june')} {first_a}..{last_a};"
        f" counted tests {p.get('counted_tests', 'n/a (P4)')}; trials in family N={trials}; DSR={dsr}",
        "Universe: U_score (ADV63>=₹0.2cr, mcap>=₹100cr, >=126 listed sessions, mapped company) N by June anchor: "
        + _fmt_map(n_score),
        "  ₹0.5cr sensitivity N: " + _fmt_map(n_05) + "; U_score_T N: " + _fmt_map(n_t),
        "  U1000 slice reported (top-1000 ADV63, min 50/63 days); U_score share of U1000: " + _fmt_map(share, "{:.0%}"),
        "  02 §0.1 coverage reproduction (Δpp top500/top1000): "
        + ", ".join(
            f"{c['anchor'][:7]} {c['delta_pp_02map']['top500']:+.1f}/{c['delta_pp_02map']['top1000']:+.1f}"
            for c in repro
        ),
        "Coverage bias: covered-minus-uncovered median 1Y ret "
        + _fmt_map(b["covered_minus_uncovered_median_1y"], "{:+.1%}")
        + "; Δ IC(price-only, full vs covered) n/a (P4)",
        "Survivorship: scored names = survivors to 2026-11 by construction (fundamentals only for today's 2,618"
        " companies); identity stitching uses hindsight (later renames/ISIN changes map earlier rows to today's"
        f" company); 2015-2025 stopped symbols {ex['stopped']} (02: 1,053), true exits {ex['true_exit']} (02: 664),"
        f" last series BZ {ex['last_bz']} / BE {ex['last_be']}",
        "Restatement: values as restated Nov-2026; unmeasurable (no history before 2026-11-01)",
        "Sector labels: current (non-PIT)",
        "Availability: annual +60d / quarterly +45d (+event_date if later; event window to deadline+15d; COVID"
        " overrides verified against SEBI circulars, +30d blanket margin kept); quarterly unused before 2023-06-30",
        f"Market cap: A/B1/B2 mix in June U_score {mix}; methods disagreeing >25% (median used) {flagged};"
        " post-anchor split/bonus factors and today's face value used only for basis conversion (poison-exempt)",
        f"Labels: next-session close entry, exit = last good session <= A+h; total return (own factors; dividends"
        f" missing for {b['no_div_data_share_1y']:.0%} of 1Y June labels, {b['u_score_no_div_data_share_1y']:.0%} in"
        f" U_score); rights unadjusted ({b['rights_unadjusted_1y']} labels); exits: distress -100%"
        f" ({b['distress_1y']}), corp exit last price ({b['corp_exit_1y']}), stale last price ({b['stale_1y']});"
        f" jump-detector factors {b['jump_factors_applied']}",
        "  universe median 1Y (-100% rule vs last price): "
        + ", ".join(
            f"{k[:7]} {b['univ_median_1y_minus100'][k]:+.1%}/{b['univ_median_1y_lastprice'][k]:+.1%}"
            for k in sorted(b["univ_median_1y_minus100"])[-6:]
        ),
        "Benchmark: universe cap-weighted TR (own); Nifty 500 is price-only (div-yield gap ≈1-1.5%/yr)",
        "Macro: revised values, fixed release lags",
        "Text/events: survivor-only documents; coverage n/a (PT); vendor estimates (challenger only) yfinance,"
        " vintage unverifiable",
        f"Holdout: sealed {sealed}; unsealed {', '.join(f['unsealed']) if f['unsealed'] else 'no'}",
        "Live-only (not in backtest): analyst estimates, X-Agent, news, snapshots, FPI daily",
    ]
    return "\n".join(lines)


def render_bias_block(run_id: int, *, conn: Any = None) -> str:
    """The 02 §8.2 block for ``run_id`` (any run kind; P1 numbers come from its snapshot's labels/universe runs)."""
    if conn is not None:
        return render_from_facts(gather(conn, run_id))
    from ideation.config import get_settings
    from ideation.db.writer import Writer

    with Writer.from_settings(get_settings()).connect() as c:
        return render_from_facts(gather(c, run_id))
