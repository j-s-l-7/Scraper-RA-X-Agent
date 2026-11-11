"""Read-only reader of X-Agent's ``xagent.db`` (sqlite URI ``mode=ro``). We never write it and never store tweet text.

The ``raw`` JSON column is parsed in memory only to find the parent post id of a reply/quote; nothing from it is kept.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

MEDIA_ONLY_PLACEHOLDER = ""


@dataclass(frozen=True)
class Post:
    tweet_id: int
    handle: str
    posted_at: datetime  # UTC
    text: str
    parent_text: str

    @property
    def text_hash(self) -> str:
        return hash_text(self.text, self.parent_text)


def hash_text(text: str, parent_text: str = "") -> str:
    return hashlib.sha256(f"{text}\x1f{parent_text}".encode()).hexdigest()


def open_readonly(path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(str(path))}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.execute("pragma query_only = on")
    return conn


def _parent_ids(raw: str | None) -> list[int]:
    if not raw:
        return []
    try:
        obj = json.loads(raw)
    except (TypeError, ValueError):
        return []
    refs = obj.get("referenced_tweets") if isinstance(obj, dict) else None
    out: list[int] = []
    for r in refs or []:
        try:
            out.append(int(r["id"]))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _is_media_only(text: str) -> bool:
    """True when nothing but t.co links remains (an image/video post: the model has no text to read)."""
    import re

    return not re.sub(r"https?://\S+", "", text).strip()


def read_posts(path: Path, *, since: datetime | None = None, handles: set[str] | None = None) -> list[Post]:
    """Posts with a known handle, oldest first. ``since``/``handles`` filter in SQL."""
    conn = open_readonly(path)
    try:
        q = (
            "select t.id, u.username, t.created_at, t.text, t.raw from tweets t join users u on u.id = t.author_id"
            " order by t.created_at, t.id"
        )
        rows = conn.execute(q).fetchall()
        refs = {int(i): (t or "") for i, t in conn.execute("select id, text from refs")}
    finally:
        conn.close()
    out: list[Post] = []
    for tid, handle, created, text, raw in rows:
        if handles is not None and handle not in handles:
            continue
        ts = datetime.fromisoformat(str(created).replace("Z", "+00:00")).astimezone(UTC)
        if since is not None and ts < since:
            continue
        parent = " ".join(refs[p] for p in _parent_ids(raw) if p in refs)
        out.append(Post(int(tid), str(handle), ts, text or "", parent))
    return out


def is_media_only(post: Post) -> bool:
    return _is_media_only(post.text)
