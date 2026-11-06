"""Cleaning and parsing of one document's pages (05 §7.1). Pure functions: no I/O, no clock.

Order of operations: drop the Reg-30 cover letter pages, join the pages, split prepared remarks from Q&A on the raw
text (the operator line that announces Q&A would otherwise be dropped first), then per part: drop operator lines,
drop safe-harbour sentences, collapse whitespace, lowercase, tokenize per sentence.
"""

from __future__ import annotations

import re
from calendar import monthrange
from datetime import date, timedelta

COVER_RE = re.compile(r"(Regulation\s*30|Dear\s+Sirs?|Dear\s+Madam|Scrip\s+Code|Listing\s+Obligations)", re.IGNORECASE)
MODERATOR_RE = re.compile(r"^\s*Moderator\s*:", re.IGNORECASE | re.MULTILINE)
OPERATOR_LINE_RE = re.compile(r"^[ \t]*Moderator[ \t]*:.*$", re.IGNORECASE | re.MULTILINE)
QA_RE = re.compile(
    r"(question[\s-]+and[\s-]+answer|q\s*&\s*a\s+session|first\s+question|begin\s+the\s+question)", re.IGNORECASE
)
QA_MIN_TOKENS = 500

SAFE_RE = re.compile(r"(forward[\s-]*looking\s+statements?|safe\s+harbou?r)", re.IGNORECASE)
_LEADIN_RE = re.compile(
    r"(disclaimer|before\s+we\s+(begin|start|proceed)|please\s+note|at\s+the\s+outset)", re.IGNORECASE
)
_DISCLAIM_RE = re.compile(
    r"(risks?|uncertaint|actual\s+results|materially|assum|undertak|obligation|updat|revis|publicly|expectations?|"
    r"projections?|statements?|forward|safe|harbou?r|disclaimer|factors|filings|annual\s+report|regulat|"
    r"competition|reliance|guarantee|performance)",
    re.IGNORECASE,
)
SAFE_MAX_FOLLOW = 12
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")
_WS = re.compile(r"\s+")
TOKEN_RE = re.compile(r"[a-z][a-z\-']+")

# ── dates ───────────────────────────────────────────────────────────────────────────────────────────────────
MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december")  # fmt: skip
_MON = "(" + "|".join(m.title() for m in MONTHS) + ")"
_MON_RE = "(?:" + "|".join(MONTHS) + ")"
_DATE_PATTERNS = (
    re.compile(rf"\b{_MON}\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(20\d\d)\b"),  # October 14, 2024
    re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+{_MON},?\s+(20\d\d)\b"),  # 14th October 2024
    re.compile(r"\bDate\s*:\s*(20\d\d)\.(\d\d)\.(\d\d)\b"),  # Date: 2024.10.14 (digital signature)
    re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-](20\d\d)\b"),  # 14/10/2024 (day first)
)
# a date introduced by one of these is a period end or meeting date, never the filing date
_NOT_FILED_RE = re.compile(
    r"(ended|ending|period|quarter|year|held\s+on|meeting|conducted|as\s+on|as\s+at|as\s+of|results\s+for|scheduled|"
    r"call\s+on)\W*$",
    re.IGNORECASE,
)
FILED_WINDOW_BEFORE = timedelta(days=45)
FILED_WINDOW_AFTER = timedelta(days=75)


def _mk(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def date_candidates(text: str) -> list[tuple[int, date, str]]:
    """Every date written in ``text`` as (offset, date, pattern id), by offset, skipping period-end contexts."""
    out: list[tuple[int, date, str]] = []
    for pid, pat in enumerate(_DATE_PATTERNS):
        for m in pat.finditer(text):
            g = m.groups()
            if pid == 0:
                dt = _mk(int(g[2]), MONTHS.index(g[0].lower()) + 1, int(g[1]))
            elif pid == 1:
                dt = _mk(int(g[2]), MONTHS.index(g[1].lower()) + 1, int(g[0]))
            elif pid == 2:
                dt = _mk(int(g[0]), int(g[1]), int(g[2]))
            else:
                dt = _mk(int(g[2]), int(g[1]), int(g[0]))
            if dt is None:
                continue
            if pid != 2 and _NOT_FILED_RE.search(text[max(0, m.start() - 40) : m.start()].replace("\n", " ")):
                continue
            out.append((m.start(), dt, ("month_d_y", "d_month_y", "sig_date", "dmy_numeric")[pid]))
    return sorted(out)


def parse_filed_date(front_pages: list[str], doc_date: date) -> tuple[date | None, str | None]:
    """05 §7.1: the first date on pages 1-2, accepted only inside [doc_date - 45 d, doc_date + 75 d]."""
    text = "\n".join(front_pages[:2])
    lo, hi = doc_date - FILED_WINDOW_BEFORE, doc_date + FILED_WINDOW_AFTER
    for _, dt, pid in date_candidates(text):
        if lo <= dt <= hi:
            return dt, pid
    return None, None


_QEND_A = re.compile(
    rf"(?:quarter|period|year)(?:\s+and\s+[^.]{{0,60}}?)?\s+ended\s+(?:on\s+)?{_MON}\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(20\d\d)"
)
_QEND_B = re.compile(
    rf"(?:quarter|period|year)(?:\s+and\s+[^.]{{0,60}}?)?\s+ended\s+(?:on\s+)?(\d{{1,2}})(?:st|nd|rd|th)?\s+{_MON},?\s+(20\d\d)"
)
_LABEL_Q = re.compile(r"\bq([1-4])\s*[- ]?\s*fy\s*'?\s*(\d{2}|\d{4})\b", re.IGNORECASE)


def quarter_end_before(d: date) -> date:
    """The last calendar quarter end strictly before ``d``."""
    y, m = d.year, ((d.month - 1) // 3) * 3  # months of quarters completed before d's own quarter
    if m == 0:
        y, m = y - 1, 12
    return date(y, m, monthrange(y, m)[1])


def parse_q_end(front_text: str, period_label: str | None, doc_date: date) -> tuple[date, str]:
    """Quarter discussed: 'quarter ... ended <date>' text, else the 'Qn FYyy' label, else the last quarter end."""
    flat = _WS.sub(" ", front_text)
    lo, hi = doc_date - timedelta(days=250), doc_date + timedelta(days=10)
    for pat, order in ((_QEND_A, 0), (_QEND_B, 1)):
        for m in pat.finditer(flat):
            g = m.groups()
            mon = MONTHS.index((g[0] if order == 0 else g[1]).lower()) + 1
            day = int(g[1] if order == 0 else g[0])
            dt = _mk(int(g[2]), mon, day)
            if dt and day == monthrange(dt.year, dt.month)[1] and lo <= dt <= hi:
                return dt, "text"
    if period_label:
        m = _LABEL_Q.search(period_label)
        if m:
            q, yy = int(m.group(1)), int(m.group(2))
            fy = yy + 2000 if yy < 100 else yy
            year = fy if q == 4 else fy - 1  # Q1 Jun, Q2 Sep, Q3 Dec of fy-1; Q4 Mar of fy
            mon = {1: 6, 2: 9, 3: 12, 4: 3}[q]
            return date(year, mon, monthrange(year, mon)[1]), "label"
    return quarter_end_before(doc_date), "default"


# ── cover letter, safe harbour, Q&A, tokens ─────────────────────────────────────────────────────────────────
def strip_cover(pages: list[str]) -> list[str]:
    """Drop page 1 if it matches the cover-letter regex, and page 2 too if page 1 went and page 2 still matches
    (a letter that runs over). A page that carries an operator line (``Moderator:``) is transcript, never cover."""
    kept = list(pages)
    drop = 0
    for i in (0, 1):
        if i >= len(pages) or MODERATOR_RE.search(pages[i]) or not COVER_RE.search(pages[i]):
            break
        drop = i + 1
    return kept[drop:]


def qa_offsets(text: str) -> list[int]:
    """Start offsets of every Q&A marker, in order (05 §7.1 regex)."""
    return [m.start() for m in QA_RE.finditer(text)]


def split_qa(text: str) -> tuple[str, str | None]:
    """(prepared remarks, Q&A) split at the FIRST Q&A marker, or (text, None). ``extract_document`` may prefer a
    later marker when the first one sits in the moderator's introduction (see ``choose_qa_split``)."""
    offs = qa_offsets(text)
    return (text[: offs[0]], text[offs[0] :]) if offs else (text, None)


def _strip_safe_harbour(sentences: list[str]) -> list[str]:
    drop: set[int] = set()
    i = 0
    n = len(sentences)
    while i < n:
        if SAFE_RE.search(sentences[i]):
            drop.add(i)
            if i > 0 and _LEADIN_RE.search(sentences[i - 1]) and len(sentences[i - 1]) < 400:
                drop.add(i - 1)
            j = i + 1
            while j < n and j <= i + SAFE_MAX_FOLLOW and _DISCLAIM_RE.search(sentences[j]):
                drop.add(j)
                j += 1
            i = j
        else:
            i += 1
    return [s for k, s in enumerate(sentences) if k not in drop]


def clean_sentences(raw: str) -> list[str]:
    """Operator lines and safe-harbour sentences removed, whitespace collapsed, lowercased; one string per sentence."""
    no_op = OPERATOR_LINE_RE.sub(" ", raw.replace("’", "'").replace("‘", "'"))
    flat = _WS.sub(" ", no_op).strip()
    sents = [s for s in _SENT_SPLIT.split(flat) if s]
    return [s.lower() for s in _strip_safe_harbour(sents)]


def tokenize_sentences(sentences: list[str]) -> list[list[str]]:
    return [t for s in sentences if (t := TOKEN_RE.findall(s))]
