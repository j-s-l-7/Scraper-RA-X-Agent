"""LEX_V1: hand-built finance-tone and India-guidance lexicon (05 §7.1).

The tone categories ``pos``/``neg``/``unc``/``lit`` are written in the spirit of Loughran-McDonald. The LM master
dictionary is licensed, so it is NOT vendored here: every term below is our own. Matching is on lowercase tokens
(``[a-z][a-z\\-']+``); inflections are listed explicitly (no stemming). Guidance categories are phrase-level
(tuples of tokens, length >= 1). A negator within 3 tokens before a match flips pos<->neg and g_up<->g_down, and
cancels capex (see ``extract.count_tokens``).
"""

from __future__ import annotations

import hashlib
import json
from typing import Final

LEX_V1: Final = "LEX_V1"


def _words(block: str) -> frozenset[str]:
    return frozenset(block.split())


POS: Final = _words("""
accelerate accelerated accelerating acceleration achieve achieved achievement achievements advantage advantages ahead
attractive beneficial benefit benefited benefits best better boost boosted breakthrough buoyant buoyancy comfortable
commendable compelling confidence confident constructive deliver delivered delighted desirable dynamic efficiencies
efficient enable enabled encouraging enhance enhanced enhancing enjoy enthusiastic excellent exceeded exceeding
exceptional exciting expand expanded expanding expansion favorable favourable gain gained gains good great greater
grew grow growing grown growth healthy highest improve improved improvement improvements improving innovative
leadership leading momentum opportunities opportunity optimism optimistic outperform outperformance outperformed
outstanding pleased pleasing positive positively profitability profitable progress progressed prosperity ramp-up
rebound rebounded recovered recovery record resilience resilient robust solid stability stable steady strength
strengthen strengthened strengthening strong stronger strongest succeed success successful successfully superior
surpass surpassed sustainable tailwind tailwinds thrilled traction turnaround upgrade upgraded upside upturn visibility
win wins won
""")

NEG: Final = _words("""
adverse adversely bad bottleneck bottlenecks burden challenge challenges challenging closure closures concern concerns
constrained constraint constraints contraction crisis curtail curtailed cut cuts damage damaged dampen dampened
decline declined declines declining decrease decreased decreasing default defaults deficit deficits delay delayed
delays depressed deteriorate deteriorated deterioration difficult difficulties difficulty diminish diminished
disappointed disappointing disruption disruptions disrupted dispute disputes downgrade downgraded downside downturn
drop dropped eroded erosion fail failed failure fall fallen falling fell fragile harm headwind headwinds hurt impair
impaired impairment impairments inability inflationary lag lagged lagging lose loss losses lost lower lowered muted
negative negatively obstacle overhang penalties penalty pressure pressured pressures problem problems recession reduce
reduced reduction reductions restructuring setback severe shortfall shrink shrinking shutdown slow slowdown slowed
slower slowing sluggish soft softer softness stagnant stagnation strain stress stressed subdued suffer suffered
tough trouble underperform underperformed underutilised underutilized unfavorable unfavourable unfortunate
unfortunately volatile waning weak weaken weakened weakening weaker weakness worse worsen worsened worsening worst
write-off write-offs writedown
""")

UNC: Final = _words("""
ambiguity ambiguous approximately assume assuming assumption assumptions caution cautious contingent depend dependent
depending depends doubt doubtful fluctuate fluctuating fluctuations fluid hinge hinges indefinite instability
could might may perhaps possible possibly probably risk risks risky roughly speculation speculative tentative
tentatively uncertain uncertainties uncertainty unclear unforeseen unknown unpredictability unpredictable unsettled
unsure variability variable varies vary volatile volatility
""")

LIT: Final = _words("""
adjudication alleged allegation allegations appeal appeals appellate arbitral arbitration breach claim claims court
courts defendant indemnify indemnity infringement injunction judgement judgment lawsuit lawsuits legal liable
litigation nclat nclt plaintiff prosecution settlement show-cause summons tribunal verdict violation violations
""")

TONE_WORDS: Final[dict[str, tuple[str, ...]]] = {}
for _cat, _words in (("pos", POS), ("neg", NEG), ("unc", UNC), ("lit", LIT)):
    for _w in _words:
        TONE_WORDS[_w] = (*TONE_WORDS.get(_w, ()), _cat)

# phrase-level guidance categories: token tuples (length >= 1)
_G_UP: Final = """
record order|record orders|record order book|order book at record|strong order|strong orders|robust order|healthy order
order inflow strong|order inflow robust|order inflows strong|order inflows robust|strong order book|robust order book
healthy order book|guidance raised|raised guidance|raise guidance|raising guidance|raised our guidance|guidance upgraded
upgraded guidance|upgrade guidance|guidance upwards|revised upward|revised upwards|upward revision
capacity expansion|capacity addition|capacity additions|expanding capacity|expand capacity|adding capacity
add capacity|added capacity|utilisation improving|utilization improving|utilisation improved|utilization improved
higher utilisation|higher utilization|improving utilisation|improving utilization|improved utilisation
improved utilization|margin expansion|margins expanded|margin expanded|margin improvement|margins improved
expanding margins|improving margins|expand margins|pricing power|price increases|price hikes|price hike
demand robust|robust demand|strong demand|healthy demand|demand remains strong|demand remains robust|demand is strong
demand is robust|market share gains|market share gain|gaining market share|gained market share|gain market share
increase market share|increasing market share|commissioning|commissioned|debottlenecking|debottlenecked
de-bottlenecking
"""
_G_DOWN: Final = """
guidance cut|guidance lowered|lowered guidance|lower guidance|lowering guidance|cut guidance|cutting guidance
guidance reduced|reduced guidance|revised downward|revised downwards|downward revision|headwind|headwinds
demand slowdown|slowdown in demand|weak demand|weaker demand|weakness in demand|demand weakness|demand weak
demand remains weak|demand is weak|soft demand|muted demand|subdued demand|pricing pressure|pricing pressures
price pressure|price erosion|destocking|de-stocking|inventory correction|inventory destocking|inventory overhang
margin pressure|margin pressures|margins under pressure|margin contraction|margins contracted|margin compression
delay|delays|delayed|deferment|postponed|postponement|input cost inflation|input cost pressure|input costs pressure
raw material inflation|raw material cost pressure|working capital stretch|working capital pressure
stretched working capital|elongated working capital|elongation in working capital|receivables stretch
capacity underutilisation|capacity underutilization
"""
_CAPEX: Final = """
capex|capexes|capital expenditure|greenfield|brownfield|new plant|new plants|new facility|new manufacturing
investment of rs|investment of inr|investing rs|investing inr
"""


def _phrases(block: str, cat: str) -> dict[tuple[str, ...], str]:
    return {tuple(p.split()): cat for p in block.replace("\n", "|").split("|") if p.strip()}


PHRASES: Final[dict[tuple[str, ...], str]] = {
    **_phrases(_G_UP, "g_up"),
    **_phrases(_G_DOWN, "g_down"),
    **_phrases(_CAPEX, "capex"),
}
PHRASE_INDEX: Final[dict[str, tuple[tuple[str, ...], ...]]] = {}
for _p in sorted(PHRASES, key=len, reverse=True):  # longest first
    PHRASE_INDEX[_p[0]] = (*PHRASE_INDEX.get(_p[0], ()), _p)

NEGATORS: Final = frozenset({"not", "no", "never", "without", "neither", "nor", "none", "cannot", "hardly"})
NEGATION_WINDOW: Final = 3


def is_negator(tok: str) -> bool:
    return tok in NEGATORS or tok.endswith("n't")


def term_count() -> int:
    return len(TONE_WORDS) + len(PHRASES)


def lexicon_hash() -> str:
    """Stable hash of every term and category (part of the extraction cache key)."""
    payload = {
        "ver": LEX_V1,
        "tone": sorted((w, list(c)) for w, c in TONE_WORDS.items()),
        "phrases": sorted((" ".join(p), c) for p, c in PHRASES.items()),
        "neg": sorted(NEGATORS),
        "win": NEGATION_WINDOW,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]
