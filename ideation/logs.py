"""Logging: JSON lines to ``IDEATION_DATA_DIR/logs/<date>.jsonl`` plus a rich console on stderr (01 §8.3)."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

_STD_ATTRS = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {"message", "asctime"}
_MARK = "_ideation_handler"


class JsonFormatter(logging.Formatter):
    """One JSON object per line. Extra ``logger.info(..., extra={...})`` fields become top-level keys."""

    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STD_ATTRS and not key.startswith("_"):
                out[key] = value
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str, ensure_ascii=False)


def log_file_for(logs_dir: Path, when: datetime | None = None) -> Path:
    stamp = (when or datetime.now().astimezone()).strftime("%Y-%m-%d")
    return logs_dir / f"{stamp}.jsonl"


def setup_logging(logs_dir: Path, level: str = "INFO", *, console: bool = True) -> Path:
    """Configure the root logger once per process (re-calls replace this module's handlers). Returns the log file."""
    logs_dir.mkdir(parents=True, exist_ok=True)
    path = log_file_for(logs_dir)
    root = logging.getLogger()
    for h in [h for h in root.handlers if getattr(h, _MARK, False)]:
        root.removeHandler(h)
        h.close()
    file_handler = logging.FileHandler(path, encoding="utf-8")
    file_handler.setFormatter(JsonFormatter())
    setattr(file_handler, _MARK, True)
    root.addHandler(file_handler)
    if console:
        rich_handler = RichHandler(console=Console(stderr=True), show_path=False, rich_tracebacks=True)
        setattr(rich_handler, _MARK, True)
        root.addHandler(rich_handler)
    root.setLevel(level.upper())
    return path
