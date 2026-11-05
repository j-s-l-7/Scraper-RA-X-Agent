"""Every real macro.series code resolves to a release-lag family consistent with its source (02 §1.2)."""

from __future__ import annotations

from ideation.config import Settings
from ideation.lake.adapter import LakeMode, LakeReader
from ideation.lake.queries import NamedQuery
from ideation.pit.lags import macro_kind

SERIES = NamedQuery("macro_series", "select code, source, freq from macro.series order by code", ("macro.series",))
EXPECTED = {
    ("mospi_esankhyiki", "M"): {"CPI", "IIP", "WPI"},
    ("worldbank", "A"): {"WORLD_BANK"},
    ("yfinance", "D"): {"DAILY"},
}


def test_every_macro_code_has_a_lag_rule(settings: Settings) -> None:
    rows = LakeReader.from_settings(settings, LakeMode.LIVE).fetch(SERIES)
    assert rows
    wrong = [
        (code, src, freq, macro_kind(code)) for code, src, freq in rows if macro_kind(code) not in EXPECTED[(src, freq)]
    ]
    assert wrong == []
