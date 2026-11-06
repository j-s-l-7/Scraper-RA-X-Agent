"""The P2 hook into the universe build: ``feature_complete`` by anchor from the features parquet, and the
``features_incomplete`` reason (05 §1.1, min_feature_complete from config)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from ideation.universe.cli import file_sha, load_feature_complete


def test_load_feature_complete_by_anchor(tmp_path: Path) -> None:
    p = tmp_path / "features_fundamental.parquet"
    df = pd.DataFrame(
        {
            "anchor": pd.to_datetime(["2019-06-28", "2019-06-28", "2020-06-30"]),
            "security_id": [10, 20, 10],
            "feature_complete": [0.9, 0.4, float("nan")],
        }
    )
    df.to_parquet(p, index=False)
    out = load_feature_complete(p)
    assert sorted(out) == [date(2019, 6, 28), date(2020, 6, 30)]
    assert out[date(2019, 6, 28)].to_dict() == {10: 0.9, 20: 0.4}
    assert out[date(2020, 6, 30)].isna().all()


def test_missing_file_means_no_hook(tmp_path: Path) -> None:
    p = tmp_path / "nope.parquet"
    assert load_feature_complete(p) == {}
    assert file_sha(p) is None


def test_file_sha_changes_with_content(tmp_path: Path) -> None:
    p = tmp_path / "f.parquet"
    p.write_bytes(b"a")
    a = file_sha(p)
    p.write_bytes(b"b")
    assert a != file_sha(p)
