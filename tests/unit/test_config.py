"""Settings (env + optional env files) and the hashed methodology config."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from ideation.config import REPO_ROOT, Settings, env_files, load_methodology, redact_dsn
from ideation.logs import setup_logging
from ideation.runs import compute_run_key


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    import os

    for key in list(os.environ):
        if key.startswith("IDEATION_") or key == "ANTHROPIC_API_KEY":
            monkeypatch.delenv(key)
    return monkeypatch


def test_defaults_connect_as_app_role(clean_env: pytest.MonkeyPatch) -> None:
    s = Settings(_env_file=None)
    for dsn in (s.lake_dsn, s.dsn):
        assert dsn.startswith("postgresql://ideation_rw@localhost:5433/equity_lake")
    assert s.data_dir == Path("~/Ideation-data").expanduser()
    assert s.statement_timeout_s == 120
    assert s.secrets_file == Path("~/Ideation-data/.secrets.env").expanduser()


def test_data_dir_inside_repo_is_refused(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("IDEATION_DATA_DIR", str(REPO_ROOT / "data"))
    with pytest.raises(ValueError, match="outside the repo"):
        Settings(_env_file=None)


def test_secrets_file_absent_is_fine(tmp_path: Path, clean_env: pytest.MonkeyPatch) -> None:
    missing = tmp_path / "nope.env"
    assert missing not in env_files({"IDEATION_SECRETS_FILE": str(missing)})


def test_secrets_file_unknown_keys_ignored(tmp_path: Path, clean_env: pytest.MonkeyPatch) -> None:
    f = tmp_path / "secrets.env"
    f.write_text("OPENAI_API_KEY=placeholder\nOPENAI_MODEL=some-model\nUNRELATED=1\nIDEATION_LOG_LEVEL=DEBUG\n")
    files = env_files({"IDEATION_SECRETS_FILE": str(f)})
    assert files[0] == f
    s = Settings(_env_file=files)
    assert s.log_level == "DEBUG"
    assert not hasattr(s, "openai_api_key")


def test_secrets_file_inside_repo_is_refused() -> None:
    with pytest.raises(ValueError, match="outside the repo"):
        env_files({"IDEATION_SECRETS_FILE": str(REPO_ROOT / ".secrets.env")})


def test_api_key_is_secret(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("ANTHROPIC_API_KEY", "placeholder-value")
    s = Settings(_env_file=None)
    assert s.anthropic_api_key is not None
    assert "placeholder-value" not in repr(s)


def test_redact_dsn() -> None:
    with_password = "postgresql://u:" + "pw" + "@h:1/db"  # assembled so the secret scan stays clean
    assert redact_dsn(with_password) == "postgresql://u:" + "***" + "@h:1/db"
    assert redact_dsn("postgresql://ideation_rw@h:1/db") == "postgresql://ideation_rw@h:1/db"


def test_methodology_hash_is_stable_and_sensitive(tmp_path: Path) -> None:
    m = load_methodology(REPO_ROOT / "config")
    assert m.config_hash == load_methodology(REPO_ROOT / "config").config_hash
    assert "default.yaml" in m.files
    assert m.data["default"]["version"] == 1
    (tmp_path / "default.yaml").write_text((REPO_ROOT / "config" / "default.yaml").read_text())
    same = load_methodology(tmp_path)
    assert same.config_hash == m.config_hash
    (tmp_path / "default.yaml").write_text("version: 1\nuniverse: {top_n: 999}\n")
    assert load_methodology(tmp_path).config_hash != m.config_hash


def test_methodology_requires_versioned_default(tmp_path: Path) -> None:
    (tmp_path / "default.yaml").write_text("universe: {}\n")
    with pytest.raises(ValueError, match="version"):
        load_methodology(tmp_path)


def test_run_key_is_canonical() -> None:
    a = compute_run_key("ideas", as_of=date(2026, 11, 5), snap_id="s", config_hash="h", params={"a": 1, "b": [1, 2]})
    b = compute_run_key("ideas", as_of=date(2026, 11, 5), snap_id="s", config_hash="h", params={"b": [1, 2], "a": 1})
    assert a == b
    assert len(a) == 64
    assert a != compute_run_key(
        "ideas", as_of=date(2026, 11, 6), snap_id="s", config_hash="h", params={"a": 1, "b": [1, 2]}
    )
    assert a != compute_run_key(
        "ideas", as_of=date(2026, 11, 5), snap_id="s", config_hash="h2", params={"a": 1, "b": [1, 2]}
    )


def test_json_logs(tmp_path: Path) -> None:
    import logging

    path = setup_logging(tmp_path / "logs", "INFO", console=False)
    logging.getLogger("ideation.test").info("hello", extra={"rows": 3})
    for h in logging.getLogger().handlers:
        h.flush()
    rec = json.loads(path.read_text().strip().splitlines()[-1])
    assert rec["msg"] == "hello"
    assert rec["rows"] == 3
    assert rec["level"] == "INFO"
    assert path.name.endswith(".jsonl")
