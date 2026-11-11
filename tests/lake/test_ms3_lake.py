"""MS3 on the live ledger (read-only): the holdout stays sealed and ``report research --holdout`` refuses."""

from __future__ import annotations

from datetime import date

import pytest

from ideation.db.writer import Writer
from ideation.report import store
from ideation.report.research import ReportRefused, build

pytestmark = pytest.mark.lake


def test_holdout_still_sealed_and_report_refuses(settings) -> None:
    with Writer.from_settings(settings).connect() as conn:
        assert store.holdout_status(conn) == {}, "the holdout must stay sealed until the owner confirms"
    with pytest.raises(ReportRefused, match="sealed"):
        build(settings, ["1Y-june"], holdout_report=True)


def test_report_label_reader_refuses_sealed_anchor(settings) -> None:
    import pandas as pd

    with pytest.raises(ValueError, match="sealed"):
        store.labels(settings.data_dir, "x", "1Y", [pd.Timestamp(date(2025, 6, 30))], "1Y-june")
