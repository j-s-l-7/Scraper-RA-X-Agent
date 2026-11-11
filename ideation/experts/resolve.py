"""Resolve extracted symbols/names to ``security_id`` via ``ideation.security_map`` (04 §4). Pure: tables in, rows out.

Order: ticker (``X.NS`` / ``NSE:X`` stripped) -> the symbol's segment covering the post date ('symbol'), else its latest
segment ('symbol_latest_segment', recorded because the map may end before the post) -> BSE numeric code via the company
master -> exact normalized company name with a unique hit ('name'). Anything else is 'unresolved': kept and shown,
never applied.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

import pandas as pd

_SUFFIX = re.compile(r"\.(NS|BO|BSE|NSE)$", re.IGNORECASE)
_PREFIX = re.compile(r"^(NSE|BSE)\s*:\s*", re.IGNORECASE)
_NOISE = re.compile(r"\b(limited|ltd|india|indian|the|co|corporation|corp|company|inc)\b", re.IGNORECASE)


def norm_symbol(s: str) -> str:
    return _SUFFIX.sub("", _PREFIX.sub("", s.strip())).strip().upper()


def norm_name(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", _NOISE.sub(" ", s.lower().replace("&", " and ")))).strip()


@dataclass(frozen=True)
class Resolution:
    symbol: str
    security_id: int | None
    company_id: int | None
    method: str


class Resolver:
    """``security_map``: symbol, security_id, company_id, valid_from, valid_to. ``company``: company_id, nse_symbol,
    bse_code, name (today's master; used for names/BSE codes only)."""

    def __init__(self, security_map: pd.DataFrame, company: pd.DataFrame) -> None:
        sm = security_map.sort_values("valid_to")
        self._latest = {str(r.symbol).upper(): r for r in sm.itertuples()}
        self._by_company = {int(r.company_id): r for r in sm.dropna(subset=["company_id"]).itertuples()}
        self._bse = {str(r.bse_code): int(r.company_id) for r in company.itertuples() if r.bse_code}
        names: dict[str, set[int]] = {}
        for r in company.itertuples():
            if r.name:
                names.setdefault(norm_name(str(r.name)), set()).add(int(r.company_id))
        self._names = {k: next(iter(v)) for k, v in names.items() if len(v) == 1}

    def _row(self, sym: str, on: date) -> Resolution | None:
        r = self._latest.get(sym)
        if r is None:
            return None
        covered = r.valid_from <= on <= r.valid_to
        cid = None if pd.isna(r.company_id) else int(r.company_id)
        return Resolution(sym, int(r.security_id), cid, "symbol" if covered else "symbol_latest_segment")

    def _by_company_id(self, cid: int, label: str) -> Resolution | None:
        r = self._by_company.get(cid)
        return None if r is None else Resolution(label, int(r.security_id), cid, "name")

    def resolve(self, symbols: list[str], subject: str, subject_type: str, on: date) -> list[Resolution]:
        out: list[Resolution] = []
        for raw in symbols:
            sym = norm_symbol(raw)
            if not sym:
                continue
            hit = self._row(sym, on)
            if hit is None and sym.isdigit() and sym in self._bse:
                hit = self._by_company_id(self._bse[sym], sym)
            out.append(hit or Resolution(sym, None, None, "unresolved"))
        if not out and subject_type == "company" and subject.strip():
            cid = self._names.get(norm_name(subject))
            hit = self._by_company_id(cid, subject.strip()[:60]) if cid is not None else None
            out.append(hit or Resolution(subject.strip()[:60], None, None, "unresolved"))
        return out
