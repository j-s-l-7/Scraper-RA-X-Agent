# ruff: noqa: E501
"""Live-only implementation of the P6 ``ExpertOverlay`` protocol (04 §5). LIVE, UNVALIDATED: every row says so.

* Company actions exist only for authors whose role is ``filter_boost``: bearish/avoid => ``exclude``; bullish at or above
  ``boost_min_confidence`` => ``boost``; watch => ``annotate``. The ideas engine owns what a boost does (one conviction
  notch, financials capped at Medium, c_i x 1.15, boost on a bottom-decile name becomes a displayed conflict, Q never
  changes), so the financial conviction cap still applies.
* A newer call by the same author on the same company supersedes older ones; calls older than ``window_days`` do not act.
* If an author's exclude and boost collide on a company, exclude wins and a ``conflict`` row records the clash.
* ``regime_role`` authors (speculative) never produce company rows: ``regime_tags`` returns narrative tags for the regime
  context, labelled speculative.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from ideation.experts import LABEL
from ideation.experts.settings import ExpertsConfig
from ideation.ideas.overlay import C_BOOST, EFFECT_COLUMNS

CONF_RANK = {"low": 0, "medium": 1, "high": 2}
CALL_COLUMNS = ("call_id", "author", "posted_at", "subject", "subject_type", "thesis", "direction", "confidence",
                "company_id")  # fmt: skip


def tweet_url(call_id: str, author: str) -> str:
    return f"https://x.com/{author}/status/{call_id.split(':')[0]}"


class LiveExpertOverlay:
    name = "experts-live"
    label = LABEL

    def __init__(self, calls: pd.DataFrame, cfg: ExpertsConfig) -> None:
        self.cfg = cfg
        missing = [c for c in CALL_COLUMNS if c not in calls.columns]
        if missing:
            raise ValueError(f"calls frame is missing {missing}")
        self.calls = calls.copy()

    @classmethod
    def from_conn(cls, conn: Any, cfg: ExpertsConfig) -> LiveExpertOverlay:
        rows = conn.execute(
            "select call_id, author, posted_at, subject, subject_type, thesis, direction, confidence, company_id"
            " from ideation.v_expert_active_calls"
        ).fetchall()
        return cls(pd.DataFrame(rows, columns=list(CALL_COLUMNS)), cfg)

    def _live(self, as_of: date) -> pd.DataFrame:
        c = self.calls
        if c.empty:
            return c
        c = c.dropna(subset=["company_id"])
        if c.empty:
            return c
        end = datetime.combine(as_of + timedelta(days=1), datetime.min.time(), tzinfo=c["posted_at"].iloc[0].tzinfo)
        start = end - timedelta(days=self.cfg.overlay.window_days + 1)
        c = c[(c["posted_at"] < end) & (c["posted_at"] >= start)]
        return c.sort_values("posted_at").drop_duplicates(["author", "company_id"], keep="last")

    def effects(self, as_of: date, company_ids: Sequence[int]) -> pd.DataFrame:
        ov = self.cfg.overlay
        actors = set(self.cfg.with_role("filter_boost"))
        want = {int(c) for c in company_ids}
        c = self._live(as_of)
        rows: list[dict[str, Any]] = []
        if len(c):
            c = c[c["author"].isin(actors) & c["company_id"].isin(want) & (c["subject_type"] == "company")]
        for r in c.itertuples():
            if r.direction in ov.exclude_directions:
                action = "exclude"
            elif r.direction in ov.boost_directions and CONF_RANK.get(str(r.confidence), 0) >= CONF_RANK[ov.boost_min_confidence]:
                action = "boost"
            else:
                action = "annotate"
            rows.append(self._row(r, action, as_of))
        df = pd.DataFrame(rows, columns=[*EFFECT_COLUMNS, "label"])
        # exclude beats boost on the same company; record the clash
        ex_ids = set(df.loc[df["action"] == "exclude", "company_id"]) if len(df) else set()
        clash = df[(df["action"] == "boost") & df["company_id"].isin(ex_ids)] if len(df) else df
        if len(clash):
            df = df[~((df["action"] == "boost") & df["company_id"].isin(ex_ids))]
            cl = clash.copy()
            cl["action"] = "conflict"
            df = pd.concat([df, cl], ignore_index=True)
        return df.reset_index(drop=True)

    def _row(self, r: Any, action: str, as_of: date) -> dict[str, Any]:
        posted = r.posted_at
        return {
            "company_id": int(r.company_id), "action": action, "person": r.author, "direction": r.direction,
            "subject": r.subject, "thesis": f"[{LABEL}] {r.thesis}", "url": tweet_url(r.call_id, r.author),
            "posted_at": posted, "age_days": (as_of - posted.date()).days,
            "weight": C_BOOST if action == "boost" else 1.0, "label": LABEL,
        }  # fmt: skip

    def regime_tags(self, as_of: date) -> list[dict[str, Any]]:
        """Speculative-author macro/asset/sector/theme calls as narrative tags for the regime context only."""
        tagged = set(self.cfg.with_role("regime_tags"))
        c = self.calls
        if not len(c):
            return []
        d = c["posted_at"].dt.date
        c = c[c["author"].isin(tagged) & c["subject_type"].isin(["macro", "asset", "sector", "theme"])
              & (d <= as_of) & (d >= as_of - timedelta(days=self.cfg.overlay.window_days))]  # fmt: skip
        c = c.drop_duplicates("call_id")
        return [{"tag": r.subject, "direction": r.direction, "person": r.author, "label": f"{LABEL}; speculative source",
                 "posted_at": r.posted_at.date().isoformat()} for r in c.sort_values("posted_at").itertuples()]  # fmt: skip
