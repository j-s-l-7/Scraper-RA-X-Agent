"""05 §0.1: doctor checks libomp (LightGBM/XGBoost link it on macOS) and that both learners import."""

from __future__ import annotations

from pathlib import Path

from ideation.doctor import Status, check_native_libs


def test_libomp_present_ok(tmp_path: Path) -> None:
    (tmp_path / "libomp" / "lib").mkdir(parents=True)
    check = check_native_libs((tmp_path / "libomp",), platform="darwin")
    assert check.status is Status.OK
    assert "xgboost 3.4" in check.detail


def test_libomp_missing_fails_on_macos(tmp_path: Path) -> None:
    assert check_native_libs((tmp_path / "nope",), platform="darwin").status is Status.FAIL


def test_libomp_not_required_elsewhere(tmp_path: Path) -> None:
    assert check_native_libs((tmp_path / "nope",), platform="linux").status is Status.OK
