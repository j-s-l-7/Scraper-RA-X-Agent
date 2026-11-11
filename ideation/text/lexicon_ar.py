"""LEX_AR_V1: hand-built annual-report lexicon (05 §12 item 2, PT3). Own wording, no licensed dictionary.

Tone categories ``pos``/``neg``/``unc``/``lit`` reuse the LEX_V1 word sets (hand-built, in the repo). Two annual-report
categories are added: ``risk`` (risk and exposure language, as in a "Risks and concerns" section) and ``dist``
(financial-distress and governance-stress vocabulary: default, insolvency, impairment, going concern ...). Matching is on
lowercase tokens ``[a-z][a-z\\-']+``. A negator within 3 tokens before a pos/neg hit flips it (same rule as PT1).
The auditor-report flags are regexes in ``annual.py``; their patterns are part of ``lexicon_hash`` through
``AUDITOR_PATTERNS`` so any change invalidates the cache.
"""

from __future__ import annotations

import hashlib
import json
from typing import Final

from ideation.text import lexicon as lex1

LEX_AR_V1: Final = "LEX_AR_V1"


def _words(block: str) -> frozenset[str]:
    return frozenset(block.split())


RISK: Final = _words("""
risk risks risky threat threats exposure exposures exposed vulnerable vulnerability vulnerabilities hazard hazards
peril contingency contingencies downside fluctuation fluctuations volatility concentration sensitivity dependence
dependency obsolescence cyber cybersecurity counterparty liquidity-risk credit-risk market-risk interest-rate
regulatory compliance non-compliance penalty penalties sanction sanctions litigation
""")

DIST: Final = _words("""
default defaults defaulted insolvency insolvent bankruptcy liquidation winding-up wound moratorium nclt ibc
resolution-plan restructured restructuring reschedule rescheduled rescheduling impairment impaired write-off write-offs
written-off writedown doubtful npa npas delinquent delinquency overdue
stressed stress distressed going-concern default-notice invocation invoked recall recalled
fraud fraudulent misappropriation embezzlement whistle-blower whistleblower irregularities
irregularity non-payment nonpayment shortfall deficit erosion eroded net-worth-erosion negative-networth
""")

CATS: Final = ("pos", "neg", "unc", "lit", "risk", "dist")
_SETS: Final = {"pos": lex1.POS, "neg": lex1.NEG, "unc": lex1.UNC, "lit": lex1.LIT, "risk": RISK, "dist": DIST}

TONE_WORDS: Final[dict[str, tuple[str, ...]]] = {}
for _cat in CATS:
    for _w in _SETS[_cat]:
        TONE_WORDS[_w] = (*TONE_WORDS.get(_w, ()), _cat)

FLIP: Final = {"pos": "neg", "neg": "pos"}
NEGATION_WINDOW: Final = lex1.NEGATION_WINDOW

AUDITOR_PATTERNS: Final[dict[str, str]] = {
    "qual_head": r"^[ \t]*(?:basis\s+for\s+)?(?:qualified|adverse)\s+(?:opinion|conclusion)\b[^\n.]{0,60}$",
    "qual_basis": r"basis\s+for\s+(?:qualified|adverse)\s+(?:opinion|conclusion)|^[ \t]*basis\s+for\s+disclaimer\s+of\s+opinion|^[ \t]*disclaimer\s+of\s+opinion\b[^\n.]{0,40}$",
    "qual_except": r"except\s+for\s+(?:the\s+)?(?:possible\s+)?effects?\s+of\s+the\s+matters?|except\s+for\s+the\s+matters?\s+(?:described|referred)",
    "eom": r"emphasis\s+of\s+matters?",
    "gc_head": r"^[ \t]*material\s+uncertaint(?:y|ies)\s+(?:related|relating)\s+to\s+going\s+concern",
    "other_matter": r"^[ \t]*other\s+matters?\s*[:\-–]?[ \t]*$",
    "kam_resp": r"principal\s+audit\s+procedures|auditor.?s\s+(?:response|verification)|how\s+our\s+audit\s+addressed|how\s+the\s+(?:key\s+audit\s+)?matter\s+was\s+addressed|our\s+audit\s+procedures\s+(?:included|in\s+(?:this|these)\s+(?:area|respect))|following\s+audit\s+procedures",
    "kam_head": r"^[ \t]*key\s+audit\s+matters?\s*[:\-–]?[ \t]*$",
}


def term_count() -> int:
    return len(TONE_WORDS)


def lexicon_hash() -> str:
    payload = {
        "ver": LEX_AR_V1,
        "tone": sorted((w, list(c)) for w, c in TONE_WORDS.items()),
        "neg": sorted(lex1.NEGATORS),
        "win": NEGATION_WINDOW,
        "aud": sorted(AUDITOR_PATTERNS.items()),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]
