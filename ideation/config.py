"""Settings (environment, prefix ``IDEATION_``) and the hashed YAML methodology config.

Two layers, deliberately separate:

* :class:`Settings` holds operational values (DSNs, paths, timeouts). They never change results, so they are not
  hashed.
* :func:`load_methodology` reads every ``config/*.yaml`` file. Its ``config_hash`` goes into every run's ``run_key``.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import yaml
from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_APP_ROLE = "ideation_rw"
DEFAULT_SECRETS_FILE = Path("~/Ideation-data/.secrets.env")
_LAKE = "localhost:5433/equity_lake"


class Settings(BaseSettings):
    """Operational settings. Env vars: ``IDEATION_LAKE_DSN``, ``IDEATION_DSN``, ``IDEATION_DATA_DIR`` …

    Sources, highest priority first: process environment, the repo's git-ignored ``.env``, then the private secrets
    file outside the repo (``IDEATION_SECRETS_FILE``, see :func:`env_files`). Missing files are skipped. Unknown
    keys are ignored (``extra='ignore'``), so unrelated entries in either file never break loading.
    """

    model_config = SettingsConfigDict(
        env_prefix="IDEATION_", env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    lake_dsn: str = f"postgresql://{DEFAULT_APP_ROLE}@{_LAKE}"
    """Read path (equity/macro). Session is forced read-only by the adapter."""
    dsn: str = f"postgresql://{DEFAULT_APP_ROLE}@{_LAKE}"
    """Write path (``ideation`` schema only; every statement passes ``writer.guard``)."""
    bootstrap_dsn: str = f"postgresql://{_LAKE}"
    """Superuser DSN, used only by ``ideation bootstrap-role``. No user ⇒ the OS user."""
    app_role: str = DEFAULT_APP_ROLE
    """Role the read and write paths must connect as; any other identity is refused."""

    data_dir: Path = Path("~/Ideation-data")
    xagent_db: Path = Path("~/X-Agent/data/xagent.db")
    library_db: Path = Path("~/Scraper - Screener.in/data/library.sqlite")
    config_dir: Path = REPO_ROOT / "config"
    secrets_file: Path = DEFAULT_SECRETS_FILE
    """Private env file outside the repo (API keys for later phases). Optional; never logged or printed."""

    statement_timeout_s: int = Field(default=120, ge=1, le=3600)
    connect_timeout_s: int = Field(default=5, ge=1, le=60)
    log_level: str = "INFO"

    anthropic_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("ANTHROPIC_API_KEY", "IDEATION_ANTHROPIC_API_KEY")
    )

    @field_validator("data_dir", "xagent_db", "library_db", "config_dir", "secrets_file", mode="after")
    @classmethod
    def _expand(cls, v: Path) -> Path:
        return v.expanduser()

    @field_validator("data_dir", "secrets_file", mode="after")
    @classmethod
    def _outside_repo(cls, v: Path) -> Path:
        if is_inside_repo(v):
            raise ValueError(f"{v} must be outside the repo: data and secrets are never committed")
        return v

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"


def is_inside_repo(path: Path) -> bool:
    resolved = path.expanduser().resolve()
    return resolved == REPO_ROOT or REPO_ROOT in resolved.parents


def env_files(environ: dict[str, str] | None = None) -> tuple[Path, ...]:
    """Env files to load, lowest priority first: the secrets file, then the repo ``.env``. Absent files are skipped.

    The secrets-file path itself comes only from the process environment (``IDEATION_SECRETS_FILE``).
    """
    env = os.environ if environ is None else environ
    secrets = Path(env.get("IDEATION_SECRETS_FILE") or DEFAULT_SECRETS_FILE).expanduser()
    if is_inside_repo(secrets):
        raise ValueError(f"IDEATION_SECRETS_FILE must be outside the repo (got {secrets})")
    return tuple(p for p in (secrets, REPO_ROOT / ".env") if p.is_file())


def load_settings(environ: dict[str, str] | None = None) -> Settings:
    """Build :class:`Settings` from the environment plus whichever env files exist (none is required)."""
    return Settings(_env_file=env_files(environ) or None)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()


def redact_dsn(dsn: str) -> str:
    """DSN safe to print or log: any password is replaced by ``***``."""
    parts = urlsplit(dsn)
    if parts.password is None:
        return dsn
    netloc = parts.netloc.replace(f":{parts.password}@", ":***@", 1)
    return urlunsplit(parts._replace(netloc=netloc))


@dataclass(frozen=True)
class Methodology:
    """Merged methodology config: ``data[<file stem>] = parsed YAML``."""

    data: dict[str, Any]
    config_hash: str
    files: tuple[str, ...]

    @property
    def short_hash(self) -> str:
        return self.config_hash[:12]


def canonical_json(obj: Any) -> str:
    """Stable JSON used for hashing (sorted keys, no whitespace, dates as ISO strings)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)


def load_methodology(config_dir: Path | None = None) -> Methodology:
    """Load and hash every ``*.yaml`` in ``config_dir``. ``default.yaml`` with an int ``version`` is required."""
    root = config_dir or get_settings().config_dir
    paths = sorted(root.glob("*.yaml"))
    data: dict[str, Any] = {}
    for p in paths:
        parsed = yaml.safe_load(p.read_text(encoding="utf-8"))
        data[p.stem] = parsed if parsed is not None else {}
    default = data.get("default")
    if not isinstance(default, dict) or not isinstance(default.get("version"), int):
        raise ValueError(f"{root}/default.yaml is missing or has no integer 'version'")
    digest = hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()
    return Methodology(data=data, config_hash=digest, files=tuple(p.name for p in paths))
