"""R7 (05 §2, 01 §5, 03 §A8): conviction from the percentile, with notches.

Base: High = P95 (p ≥ 0.95), Medium = top decile (p ≥ 0.90), else Low. One notch down for each of: data confidence
c_i < 0.6, a risk flag, regime misalignment, a financial (FIN) name. One notch up (max High) for X-Agent consensus,
live only. Financials are then capped at Medium (03 §A8), so a consensus notch cannot lift them to High. Floor Low.
An out-of-distribution name is "unscorable".
"""

from __future__ import annotations

LEVELS = ("Low", "Medium", "High")
UNSCORABLE = "unscorable"


def base_conviction(p: float) -> str:
    return "High" if p >= 0.95 else "Medium" if p >= 0.90 else "Low"


def conviction(
    p: float,
    *,
    c: float = 1.0,
    risk_flag: bool = False,
    regime_misaligned: bool = False,
    financial: bool = False,
    xagent_consensus: bool = False,
    ood: bool = False,
) -> str:
    if ood:
        return UNSCORABLE
    level = LEVELS.index(base_conviction(p))
    level -= sum((c < 0.6, risk_flag, regime_misaligned, financial))
    level = max(level, 0)
    if xagent_consensus:
        level = min(level + 1, len(LEVELS) - 1)
    if financial:
        level = min(level, LEVELS.index("Medium"))
    return LEVELS[level]
