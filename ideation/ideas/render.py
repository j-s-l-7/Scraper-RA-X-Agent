# ruff: noqa: E501, N806
"""Markdown report for the ideas run (01 §5 step 7): ``IDEATION_DATA_DIR/reports/<as_of>/ideas_<horizon>.md``."""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from ideation.ideas.select import HORIZON_LABEL

LLM_LABEL = "live, unvalidated, LLM-generated"  # same string as llm_summary.LABEL (unit-tested)
CHALLENGER = {"1Y": "P1_UNIFIED_XS", "3M": "ENS_RANKAVG_T"}
FOOTER = (
    "Personal research tool; not investment advice. Live output: nothing here is a validated forecast. No model was "
    "promoted: the composite baseline ships for both horizons and is itself weak (see the research report). Expected "
    "returns are excess over the universe median, calibrated on the counted unsealed out-of-sample anchors only "
    "(few, overlapping, serially dependent), with 80% conformal bands whose exchangeability assumption is violated. "
    "Residual biases: look-ahead-free by construction but survivorship and vendor-restatement effects remain in the "
    "history; labels use the 03 C3 timing. The tactical score carries a low-beta / low-volatility tilt; use the "
    "neutral rank when that tilt is not wanted."
)


def pct(x: Any, d: int = 1) -> str:
    return "n/a" if x is None or pd.isna(x) else f"{100 * float(x):.{d}f}%"


def num(x: Any, d: int = 2) -> str:
    return "n/a" if x is None or pd.isna(x) else f"{float(x):.{d}f}"


def chal_cell(r: Any, key: str) -> str:
    c = getattr(r, "challenger", None)
    if not isinstance(c, dict) or c.get(key) is None:
        return "n/a"
    return f"{float(c[key]):.0f}" if key == "is" else f"{100 * float(c[key]):.0f}"


def idea_table(df: pd.DataFrame, horizon: str) -> list[str]:
    ch = CHALLENGER[horizon]
    head = (f"| # | Symbol | Group | IS | Conviction | Excess vs median ({HORIZON_LABEL[horizon]}) | 80% band | Decile hit | "
            f"Beta | Vol | Neutral rank | Flags | vs prior | {ch} IS (research challenger, unpromoted) | "
            f"{ch} neutral rank (research challenger, unpromoted) |")  # fmt: skip
    rows = [head, "|" + "---|" * 15]
    for r in df.itertuples():
        rows.append(
            f"| {r.rank} | {r.symbol} | {r.industry_group} | {int(r.ideation_score)} | {r.conviction} | {pct(r.alpha)} | "
            f"{pct(r.lo, 0)} to {pct(r.hi, 0)} | {pct(r.hit_rate, 0)} | {num(r.beta)} | {pct(r.vol, 0)} | "
            f"{round(100 * r.neutral_p_bvls)} | {', '.join(r.flags) or '-'} | {r.continuity} | {chal_cell(r, 'is')} | "
            f"{chal_cell(r, 'neutral_p_bvls')} |"
        )  # fmt: skip
    return rows


def idea_detail(r: Any, llm: dict[int, dict[str, Any]] | None) -> list[str]:
    out = [f"### {r.rank}. {r.symbol} ({r.industry_group}) - IS {int(r.ideation_score)}, {r.conviction}"]
    d = r.drivers
    if d:
        out.append(
            "- Drivers: " + "; ".join(f"{x['feature']} [{x['category']}] {x['contribution']:+.3f}" for x in d["top"])
        )
        out.append("- By category: " + ", ".join(f"{k} {v:+.2f}" for k, v in d["by_category"].items()))
    out.append("- Risks: " + ("; ".join(r.risk_list) or "none flagged"))
    out.append("- Invalidation: " + "; ".join(r.invalidation))
    q = r.quadrant if isinstance(r.quadrant, str) else "n/a"
    out.append(f"- Sector: RRG {q} (RS-ratio {num(r.rs_ratio, 1)}, RS-momentum {num(r.rs_mom, 1)})")
    if llm and int(r.company_id) in llm:
        s = llm[int(r.company_id)]
        out.append(f"- Latest call summary [{LLM_LABEL}]: " + (s.get("error") or
                   f"guidance: {'; '.join(s.get('guidance', []))}. risks: {'; '.join(s.get('risks', []))}. "
                   f"changes vs prior: {'; '.join(s.get('changes_vs_prior', [])) or 'n/a'}"))  # fmt: skip
    return out


def render_horizon(
    horizon: str, as_of: date, model: str, sel: dict[str, pd.DataFrame], dropped: pd.DataFrame | None,
    regime: dict[str, str], calib: dict[str, Any], llm: dict[int, dict[str, Any]] | None, predict_run: int,
) -> str:  # fmt: skip
    longs, avoid = sel["longs"], sel["avoid"]
    L = [f"# Ideas - {HORIZON_LABEL[horizon]} - as of {as_of.isoformat()} (live)", "",
         f"Shipped model: **{model}** (baseline; no model promoted). Predict run {predict_run}. Ideation Score (IS) = "
         "100 x percentile of the score within the scored universe. Excess return is relative to the universe median "
         f"over {horizon}, calibrated on {len(calib.get('anchors', []))} counted OOS anchors "
         f"({calib.get('n_rows', 0)} name-observations).", "",
         f"Regime: trend {regime.get('trend')}, volatility {regime.get('vol')}, breadth {regime.get('breadth')}"
         f"{', narrow rally' if regime.get('narrow_rally') == 'true' else ''}"
         f"{'; ' + regime['macro'] if regime.get('macro') else ''}.", "",
         f"## Long ideas ({len(longs)})", ""]  # fmt: skip
    L += idea_table(longs, horizon) if len(longs) else ["No ideas passed the filters."]
    L += [
        "",
        f"The last two columns are the **{CHALLENGER[horizon]}** research challenger (unpromoted): shown for "
        "comparison only; it does not influence rank, conviction or the band.",
        "",
    ]
    for r in longs.itertuples():
        L += [*idea_detail(r, llm), ""]
    L += [
        f"## Avoid / review if held ({len(avoid)})",
        "",
        "Expected to underperform peers (bottom decile among liquid names).",
        "",
    ]
    L += idea_table(avoid, horizon) if len(avoid) else ["None."]
    if dropped is not None and len(dropped):
        L += ["", "## Dropped since the previous run", ""] + [
            f"- {r.symbol} ({r.side}, prior rank {r.rank})" for r in dropped.itertuples()
        ]
    ood = sel["ood"]
    L += ["", f"## Unscorable / novel (OOD, size 0): {len(ood)}", ""]
    L += [", ".join(sorted(ood["symbol"].astype(str)))] if len(ood) else ["None."]
    ex = sel["excluded"]
    if len(ex):
        L += ["", "## Expert-flagged (excluded from long ideas)", "", ", ".join(sorted(ex["symbol"].astype(str)))]
    if llm:
        L += ["", f"_LLM summaries above are {LLM_LABEL}; they never influence scores._"]
    L += ["", "---", FOOTER, ""]
    return "\n".join(L)
