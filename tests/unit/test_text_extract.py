"""PT1 unit tests on six synthetic transcripts (05 §10.3): cover letter, safe harbour, Q&A split, negation, numeric
guidance, filed-date parsing, plus dedupe, quarter-end parsing, availability (T-24) and the reader's path guard."""

from __future__ import annotations

import gzip
import json
import math
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from ideation.text import clean, extract, lexicon, reader

pytestmark = pytest.mark.unit

FILLER = "The company continued to serve customers across regions and the team reviewed operations in the quarter. "


def filler(n_sentences: int) -> str:
    return FILLER * n_sentences


COVER = (
    "Date: October 14, 2024\nTo, BSE Limited, Scrip Code 532540\nDear Sirs,\n"
    "Sub: Transcript of the earnings call for the "
    "quarter and six-month period ended September 30, 2024\nPursuant to Regulation 30 of the SEBI (Listing Obligations "
    "and Disclosure Requirements) Regulations, 2015, please find enclosed the transcript. "
    "The board meeting was held on "
    "October 10, 2024.\nYours faithfully,\n"
)
SAFE = (
    "Before we begin, a disclaimer. Some of the statements made in today's discussion may be "
    "forward-looking statements "
    "and the actual results could differ materially because of risks and uncertainties. The company undertakes no "
    "obligation to publicly update or revise any forward-looking statements. "
)


def transcript(body_prep: str, body_qa: str = "", *, cover: str = COVER) -> list[str]:
    pages = [cover] if cover else []
    pages.append("Moderator:\nLadies and gentlemen, good day and welcome to the earnings call.\n" + SAFE + body_prep)
    if body_qa:
        pages.append(
            "Moderator:\nWe will now begin the question-and-answer session. "
            "The first question is from the line of Mr. A.\n" + body_qa
        )
    return pages


DOC = date(2024, 10, 1)


def test_cover_letter_stripped_and_filed_date_parsed() -> None:  # synthetic transcript 1
    pages = transcript("Revenue grew strongly and margins improved. " + filler(60))
    out = extract.extract_document(pages, "concall_transcript", DOC)
    assert out["n_cover_pages"] == 1
    assert out["filed_date"] == date(2024, 10, 14)  # the letter date, not the Oct 10 meeting or Sep 30 period end
    assert out["q_end"] == date(2024, 9, 30)
    assert out["q_end_src"] == "text"
    kept = clean.tokenize_sentences(clean.clean_sentences("\n".join(clean.strip_cover(pages))))
    flat = {t for s in kept for t in s}
    assert "scrip" not in flat
    assert "regulation" not in flat
    assert "sirs" not in flat


def test_cover_strip_keeps_a_page_with_operator_lines() -> None:  # synthetic transcript 2
    page = "Moderator:\nWelcome. Listing Obligations are on our website. " + filler(5)
    assert clean.strip_cover([page, "Regulation 30 Dear Sir"]) == [page, "Regulation 30 Dear Sir"]
    assert clean.strip_cover([COVER, "Dear Sirs, continued: Regulation 30", "real content"]) == ["real content"]


def test_safe_harbour_stripped() -> None:  # synthetic transcript 3
    sents = clean.clean_sentences(transcript("Order inflow was strong this quarter. " + filler(3), cover="")[0])
    joined = " ".join(sents)
    assert "forward-looking" not in joined
    assert "safe harbour" not in joined
    assert "undertakes" not in joined
    assert "order inflow was strong" in joined
    assert "welcome to the earnings call" in joined
    with_safe = extract.count_tokens(clean.tokenize_sentences(clean.clean_sentences(SAFE + "Demand was strong.")))
    assert with_safe["unc"] == 0
    assert with_safe["pos"] >= 1


def test_qa_split_and_qa_ok() -> None:  # synthetic transcript 4
    prep = "Revenue grew and margins improved nicely. " + filler(60)
    qa = "Analyst: Could you comment on weak demand and pricing pressure? " + filler(60)
    out = extract.extract_document(transcript(prep, qa), "concall_transcript", DOC)
    assert out["qa_found"]
    assert out["qa_ok"] == 1
    assert out["n_tok_prep"] >= 500
    assert out["n_tok_qa"] >= 500
    assert out["prep_tone_net"] > 0 > out["qa_tone_net"]
    assert out["qa_minus_prep_tone"] == pytest.approx(out["qa_tone_net"] - out["prep_tone_net"])
    # no Q&A marker, or a Q&A part below 500 tokens: qa_ok = 0 and the part features are NaN
    short = extract.extract_document(transcript(prep, "Thank you. Any question?"), "concall_transcript", DOC)
    assert short["qa_found"]
    assert short["qa_ok"] == 0
    assert math.isnan(short["qa_tone_net"])
    none = extract.extract_document(transcript(prep), "concall_transcript", DOC)
    assert not none["qa_found"]
    assert none["qa_ok"] == 0
    assert math.isnan(none["prep_tone_net"])


def _count(text: str) -> dict[str, int]:
    return extract.count_tokens(clean.tokenize_sentences(clean.clean_sentences(text)))


def test_negation_flips_within_three_tokens() -> None:  # synthetic transcript 5
    assert _count("We saw strong demand.")["pos"] == 1
    c = _count("We did not see strong demand.")  # 'not' is 3 tokens before 'strong'
    assert (c["pos"], c["neg"]) == (0, 1)
    c = _count("There were no headwinds this quarter.")
    assert (c["neg"], c["pos"], c["g_down"], c["g_up"]) == (0, 1, 0, 1)
    c = _count("We have not yet seen any of the strong momentum.")  # 'not' is 7 tokens before 'strong': no flip
    assert (c["pos"], c["neg"]) == (2, 0)  # strong, momentum
    assert _count("We don't expect a weak quarter.")["pos"] == 1  # n't negator flips neg to pos
    assert _count("We have no capex planned. Capex is planned.")["capex"] == 1
    assert _count("We are not adding capacity.")["g_down"] == 1  # phrase flips g_up -> g_down
    assert _count("The order book is at record levels, but not the margins.")["neg"] == 0
    # negation never crosses a sentence boundary
    c = _count("We did not. Strong demand continued.")
    assert (c["pos"], c["neg"]) == (1, 0)
    # uncertainty words are not flipped
    assert _count("We are not sure what could happen.")["unc"] == 1


def test_numeric_guidance_needs_a_guidance_verb() -> None:  # synthetic transcript 6
    pages = transcript(
        "We expect revenue growth of 12-15% next year. EBITDA margin was 18% last quarter. "
        "We are targeting volume growth of 8 to 10 percent. " + filler(5),
        cover="",
    )
    out = extract.extract_document(pages, "concall_transcript", DOC)
    assert out["n_guide_pct"] == 2  # the 'margin was 18%' sentence has no guidance verb
    assert out["guide_pct_mid"] == pytest.approx((13.5 + 9.0) / 2)
    mid, n = extract.numeric_guidance("revenue grew 20% in the quarter.")
    assert math.isnan(mid)
    assert n == 0
    assert extract.numeric_guidance("we guide ebitda margin of 14.5% for the year.") == (14.5, 1)


def test_guidance_phrases_and_rates() -> None:
    c = _count("Order inflow strong, capacity expansion on track, margin pressure and delays. Capex of rs 500 crore.")
    assert (c["g_up"], c["g_down"], c["capex"]) == (2, 2, 1)
    f = extract.rate_features({**dict.fromkeys(extract.CATS, 0), "n": 1000, "pos": 3, "neg": 1, "g_up": 2})
    assert f["tone_net"] == pytest.approx(2 / 5)
    assert f["gup_r"] == pytest.approx(2.0)
    assert f["guide_net"] == pytest.approx(2 / 3)


@pytest.mark.parametrize(
    ("text", "expected", "src"),
    [
        ("Mumbai\nOctober 14, 2024\nDear Sirs", date(2024, 10, 14), "month_d_y"),
        ("14th October 2024\nTo BSE", date(2024, 10, 14), "d_month_y"),
        ("To BSE\nDigitally signed Date: 2024.10.14 18:54:18", date(2024, 10, 14), "sig_date"),
        ("Ref 14/10/2024 to BSE", date(2024, 10, 14), "dmy_numeric"),
        # period end and meeting dates are skipped; March 2023 is outside the window
        (
            "quarter ended September 30, 2024 board held on October 10, 2024 filed October 15, 2024",
            date(2024, 10, 15),
            None,
        ),
        ("Date: March 3, 2023", None, None),
    ],
)
def test_filed_date_patterns_and_window(text: str, expected: date | None, src: str | None) -> None:
    got, how = clean.parse_filed_date([text], DOC)
    assert got == expected
    if src:
        assert how == src


def test_q_end_text_label_default() -> None:
    d = date(2024, 7, 1)
    assert clean.parse_q_end("results for the quarter ended June 30, 2024", None, d) == (date(2024, 6, 30), "text")
    assert clean.parse_q_end("quarter and nine-month period ended 31st December 2023", None, date(2024, 2, 1))[
        0
    ] == date(2023, 12, 31)
    assert clean.parse_q_end("", "Q1 FY25 (tijori)", d) == (date(2024, 6, 30), "label")
    assert clean.parse_q_end("", "Q4 FY24", date(2024, 5, 1)) == (date(2024, 3, 31), "label")
    assert clean.parse_q_end("", "Q3 FY25", date(2025, 2, 1)) == (date(2024, 12, 31), "label")
    assert clean.parse_q_end("", "Oct 2024", date(2024, 10, 1)) == (date(2024, 9, 30), "default")
    assert clean.parse_q_end("", None, date(2025, 1, 1)) == (date(2024, 12, 31), "default")
    # a text date that is not a month end or lies far from the call is ignored
    assert clean.parse_q_end("quarter ended June 15, 2024", None, d)[1] == "default"


def test_dedupe_priority_and_determinism() -> None:
    rows = []
    for src, cc, sha in [("tijori", 90, "a"), ("bse", 50, "b"), ("other", 99, "c"), ("bse", 60, "d")]:
        rows.append(
            {"company_id": 1, "doc_type": "concall_transcript", "doc_date": date(2024, 10, 1), "source": src,
             "char_count": cc, "pdf_sha256": sha, "text_path": f"data/documents/X/{sha}.jsonl.gz"}
        )  # fmt: skip
    rows.append({**rows[0], "company_id": 2})
    rows.append({**rows[0], "doc_type": "concall_ppt"})
    out = extract.dedupe_catalog(pd.DataFrame(rows))
    assert len(out) == 3
    one = out[(out["company_id"] == 1) & (out["doc_type"] == "concall_transcript")].iloc[0]
    assert (one["source"], one["pdf_sha256"]) == ("bse", "d")  # bse beats the rest; larger char_count breaks the tie
    assert extract.dedupe_catalog(pd.DataFrame(rows[::-1])).equals(out)


def test_available_from_t24() -> None:  # T-24 test_text_availability
    today = date(2026, 11, 1)
    # BSE doc Oct-2024 filed 2024-10-14 -> first of the month after doc_date
    assert extract.available_from(
        date(2024, 10, 1), "bse", date(2024, 10, 14), date(2024, 9, 30), [], today=today
    ) == date(2024, 11, 1)
    # tijori Q1 FY25, doc_date 2024-07-01 -> statutory floor 2024-06-30 + 45 d + 1 = 2024-08-15
    assert extract.available_from(date(2024, 7, 1), "tijori", None, date(2024, 6, 30), [], today=today) >= date(
        2024, 8, 15
    )
    # an event on 2024-11-05 pushes availability to 2024-11-06
    assert extract.available_from(
        date(2024, 10, 1), "bse", date(2024, 10, 14), date(2024, 9, 30), [date(2024, 11, 5)], today=today
    ) == date(2024, 11, 6)
    # filed_date + 1 beats the month floor when the filing is on the last day of the month
    assert extract.available_from(
        date(2024, 10, 1), "bse", date(2024, 11, 3), date(2024, 9, 30), [], today=today
    ) == date(2024, 11, 4)
    # an event outside [doc_date - 75 d, doc_date + 45 d] is not paired; a scheduled (future) event never counts
    assert extract.available_from(
        date(2024, 10, 1), "nse", None, date(2024, 9, 30), [date(2025, 2, 1)], today=today
    ) == date(2024, 11, 1)
    assert extract.available_from(
        date(2024, 10, 1), "bse", None, date(2024, 9, 30), [date(2024, 11, 5)], today=date(2024, 11, 1)
    ) == date(2024, 11, 1)
    # December doc_date rolls the year
    assert extract.first_of_next_month(date(2024, 12, 1)) == date(2025, 1, 1)


def test_reader_roundtrip_and_path_guard(tmp_path: Path) -> None:
    docs = tmp_path / "data" / "documents" / "TCS" / "concall_transcript"
    docs.mkdir(parents=True)
    with gzip.open(docs / "x.jsonl.gz", "wt", encoding="utf-8") as fh:
        fh.write(json.dumps({"page": 2, "text": "two"}) + "\n" + json.dumps({"page": 1, "text": "one"}) + "\n")
    assert reader.read_pages(tmp_path, "data/documents/TCS/concall_transcript/x.jsonl.gz") == ["one", "two"]
    for bad in ("../secret.gz", "/etc/passwd", "data/other/x.gz", "data/documents/../../x.gz", "data/documents"):
        with pytest.raises(reader.UnsafeDocumentPathError):
            reader.resolve_text_path(tmp_path, bad)
    # a symlink under data/documents that points outside is refused
    outside = tmp_path / "outside.jsonl.gz"
    outside.write_bytes(b"")
    (docs / "link.jsonl.gz").symlink_to(outside)
    with pytest.raises(reader.UnsafeDocumentPathError):
        reader.resolve_text_path(tmp_path, "data/documents/TCS/concall_transcript/link.jsonl.gz")


def test_lexicon_shape_and_determinism() -> None:
    assert lexicon.LEX_V1 == "LEX_V1"
    assert 400 <= lexicon.term_count() <= 700
    assert lexicon.lexicon_hash() == lexicon.lexicon_hash()
    assert all(t == t.lower() for t in lexicon.TONE_WORDS)
    assert all(clean.TOKEN_RE.fullmatch(t) for t in lexicon.TONE_WORDS), "every term must survive the tokenizer"
    assert all(clean.TOKEN_RE.fullmatch(w) for p in lexicon.PHRASES for w in p)


def test_extraction_is_deterministic() -> None:
    pages = transcript("Revenue grew and we expect 10% growth. " + filler(40), "Weak demand? " + filler(40))
    a = extract.extract_document(pages, "concall_transcript", DOC)
    b = extract.extract_document(pages, "concall_transcript", DOC)
    assert repr(a) == repr(b)


def test_early_qa_mention_in_moderator_intro_is_skipped() -> None:
    intro = "Moderator:\nAfter the remarks we will have a question-and-answer session. " + SAFE
    prep = "Revenue grew and margins improved nicely. " + filler(60)
    qa = "Analyst: Could you comment on weak demand? " + filler(60)
    pages = [intro + prep, "Moderator:\nWe will now begin the question-and-answer session.\n" + qa]
    out = extract.extract_document(pages, "concall_transcript", DOC)
    assert out["qa_ok"] == 1
    assert out["n_tok_prep"] >= 500
    assert out["prep_tone_net"] > 0 > out["qa_tone_net"]
    # when no marker has a full prepared section before it, the first marker is kept and qa_ok is 0
    short = extract.extract_document(["Moderator:\nThe first question is from Mr. A. " + filler(80)], "x", DOC)
    assert short["qa_found"]
    assert short["qa_ok"] == 0


def test_documents_catalog_passes_the_backtest_ban_list() -> None:
    from ideation.lake import queries as Q
    from ideation.lake.adapter import LakeMode, check_query

    check_query(Q.DOCUMENTS_CATALOG, None, LakeMode.BACKTEST)
    check_query(Q.EARNINGS_DATES, None, LakeMode.BACKTEST)
