"""PIT data contract (02 §1.1): ``PitStore`` over a parquet snapshot, immutable per-anchor ``PitView``.

* Every dataset row carries ``available_from`` and ``role`` (see ``ideation/lake/snapshot.py``).
* ``PitView`` accessors return only rows with ``available_from <= anchor``. Each frame passes :meth:`PitView._guard`,
  which raises :class:`LeakageError`. Each frame is also logged as a manifest record ``(anchor, item, n_rows,
  sha256, max_available_from)``, which the caller writes to ``ideation.run_manifest``.
* Frames are copy-on-write (pandas 3). Mutating one never reaches the store, and the arrays they expose are
  read-only.
* Two documented exemptions from the guard:
  - ``basis_conversion_factors()`` returns split and bonus factors dated after the anchor. B1/B2 market cap uses them
    only to convert units of an already-observable quantity (03 §C2).
  - ``securities()`` exposes identity rows (stitching hindsight) with ``valid_to`` clipped at the anchor.
* ``earnings_vendor()`` is gated (05 O5): only ``ideation.outcomes.analyst`` and ``ideation.features.events_vendor``
  may call it.
* ``PitStore.poison_after(d, mode)`` (seam S5, 02 T-01) returns a store in which every ``data`` row with
  ``available_from > d`` is poisoned. Numbers become NaN, are shuffled or inflated; date columns are shifted at
  random; fake future rows are appended. Rows with role ``reference``, ``identity`` or ``basis_conversion`` are left
  alone.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from ideation.pit.calendar import TradingCalendar

Horizon = Literal["1Y", "3M", "1M"]
PoisonMode = Literal["nan", "shuffle", "inflate"]

DERIVED = ("calendar", "security_map", "prices", "factors", "rights", "face_value_now")
EXEMPT_ROLES = frozenset({"reference", "identity", "basis_conversion"})
VENDOR_CALLERS = frozenset({"ideation.outcomes.analyst", "ideation.features.events_vendor"})
QUARTERLY_FROM = date(2023, 6, 30)
"""02 §1.2: quarterly facts are usable in backtests only for anchors from 2023-06-30 (broad coverage from 2023)."""
TEXT_DOCS = "text_docs"
TEXT_DOCS_FILE = "doc_features_LEX_V1.parquet"
"""PT1 output (``ideation text extract``): one row per concall transcript / presentation with ``available_from``
(05 §7.2). It is served as the ``text_docs`` dataset so that the same guard and ``poison_after`` apply to it."""
ANNUAL_DOCS = "annual_docs"
ANNUAL_DOCS_FILE = "annual_features_LEX_AR_V1.parquet"
"""PT3 output (``ideation text annual``): one row per annual report with ``available_from`` (fiscal-year end + lag, or
a parsed filing / AGM date if later). Same ``text_dir`` as ``text_docs``; same guard and ``poison_after``."""
_FUTURE_IDENTITY_COLS = {"security_map": ("series_last",)}


class LeakageError(AssertionError):
    """A frame handed to a feature builder contains a row with ``available_from > anchor``."""


class VendorGateError(PermissionError):
    """``PitView.earnings_vendor`` was called from a module outside the 05 O5 allow-list."""


@dataclass(frozen=True)
class Anchor:
    date: date
    """NSE session; the decision is taken after its close."""
    horizon: Horizon


@dataclass(frozen=True)
class ManifestRecord:
    anchor: date
    item: str
    n_rows: int
    sha256: str
    max_available_from: date | None


def frame_hash(df: pd.DataFrame) -> str:
    """Content hash of a frame (column names, row order and values). Python ``date`` columns are hashed as day
    numbers, which is much faster than hashing their string form."""
    h = hashlib.sha256(json.dumps([str(c) for c in df.columns]).encode())
    if len(df):
        cols = {}
        for c in df.columns:
            col = df[c]
            if col.dtype == object and isinstance(col.iloc[0], date):
                cols[c] = pd.to_datetime(col).to_numpy(dtype="datetime64[D]").astype("int64")
            else:
                cols[c] = col
        h.update(pd.util.hash_pandas_object(pd.DataFrame(cols), index=False).to_numpy().tobytes())
    return h.hexdigest()


def _freeze(df: pd.DataFrame) -> pd.DataFrame:
    """A detached frame whose numpy-backed arrays are not writeable (copy-on-write keeps the store immutable)."""
    out = df.reset_index(drop=True)
    for c in out.columns:
        arr = out[c].array
        nd = getattr(arr, "_ndarray", None)
        if isinstance(nd, np.ndarray):
            nd.flags.writeable = False
    return out


def _prepare_text_docs(tbl: pa.Table) -> pa.Table:
    """Unreadable documents carry no ``available_from`` and are dropped; every remaining row gets ``role='data'`` so
    that ``poison_after`` treats the table like any other dataset."""
    if "available_from" not in tbl.column_names:
        raise LeakageError("dataset 'text_docs' has no available_from column")
    keep = pc.and_(pc.is_valid(tbl["available_from"]), pc.fill_null(tbl["read_ok"], False))
    tbl = tbl.filter(keep)
    if tbl["available_from"].type != pa.date32():
        tbl = tbl.set_column(
            tbl.column_names.index("available_from"), "available_from", pc.cast(tbl["available_from"], pa.date32())
        )
    for c in ("doc_date", "filed_date", "q_end", "fy_end", "agm_date", "parsed_date"):
        if c in tbl.column_names and tbl[c].type != pa.date32():
            tbl = tbl.set_column(tbl.column_names.index(c), c, pc.cast(tbl[c], pa.date32()))
    if "role" not in tbl.column_names:
        tbl = tbl.append_column("role", pa.array(["data"] * tbl.num_rows, pa.string()))
    return tbl


class PitStore:
    """Local parquet snapshot with ``available_from`` on every row: the only entry point for features."""

    def __init__(
        self,
        snapshot_dir: Path,
        mode: Literal["backtest", "live"] = "backtest",
        *,
        outputs_dir: Path | None = None,
        text_dir: Path | None = None,
        _tables: dict[str, pd.DataFrame] | None = None,
        _parent: PitStore | None = None,
        _poison: tuple[date, PoisonMode, int] | None = None,
    ) -> None:
        self.snapshot_dir = Path(snapshot_dir)
        self.mode = mode
        self.outputs_dir = outputs_dir
        self.text_dir = text_dir
        self._tables: dict[str, pd.DataFrame] = dict(_tables or {})
        self._arrow: dict[str, pa.Table] = {}
        self._parent = _parent
        self._poison = _poison
        self._calendar: TradingCalendar | None = None
        self._af: dict[str, np.ndarray] = {}
        manifest = self.snapshot_dir / "manifest.json"
        self.manifest: dict[str, Any] = json.loads(manifest.read_text()) if manifest.is_file() else {}

    @classmethod
    def latest(cls, data_dir: Path, **kwargs: Any) -> PitStore:
        """The most recent finished snapshot under ``data_dir/snapshots`` (by observation date, then mtime)."""
        root = Path(data_dir) / "snapshots"
        found = []
        for d in root.iterdir() if root.is_dir() else []:
            m = d / "manifest.json"
            if (
                d.is_dir()
                and not d.name.startswith(".")
                and m.is_file()
                and (d / "derived" / "manifest.json").is_file()
            ):
                found.append((json.loads(m.read_text())["observation_date"], m.stat().st_mtime, d))
        if not found:
            raise FileNotFoundError(f"no finished snapshot under {root}; run `ideation snapshot --full`")
        return cls(max(found)[2], **kwargs)

    def derived_sha(self) -> str:
        """Content id of the derived datasets (they change with code even when the raw snapshot does not)."""
        m = self.snapshot_dir / "derived" / "manifest.json"
        files = json.loads(m.read_text())["files"] if m.is_file() else {}
        return hashlib.sha256(json.dumps({k: v["sha256"] for k, v in sorted(files.items())}).encode()).hexdigest()[:12]

    @property
    def snap_id(self) -> str:
        return str(self.manifest.get("snap_id", self.snapshot_dir.name))

    # ── raw tables (internal) ───────────────────────────────────────────────────────────────────────────────
    def _path(self, name: str) -> Path:
        if name in DERIVED:
            return self.snapshot_dir / "derived" / f"{name}.parquet"
        if name == "universe" and self.outputs_dir is not None:
            return self.outputs_dir / "universe_member.parquet"
        if name in (TEXT_DOCS, ANNUAL_DOCS):
            if self.text_dir is None:
                raise FileNotFoundError("no text_dir configured on this PitStore (run `ideation text extract`)")
            return self.text_dir / (TEXT_DOCS_FILE if name == TEXT_DOCS else ANNUAL_DOCS_FILE)
        return self.snapshot_dir / f"{name}.parquet"

    def arrow(self, name: str) -> pa.Table:
        """Dataset as Arrow, sorted by ``available_from`` (stable; poisoned first when this is a poisoned store)."""
        if name not in self._arrow:
            if self._parent is not None:
                assert self._poison is not None
                tbl = _poison_arrow(name, self._parent.arrow(name), *self._poison)
            else:
                tbl = pq.read_table(self._path(name))
                if name in (TEXT_DOCS, ANNUAL_DOCS):
                    tbl = _prepare_text_docs(tbl)
                if "available_from" not in tbl.column_names:
                    raise LeakageError(f"dataset {name!r} has no available_from column")
            idx = pc.sort_indices(tbl["available_from"])  # stable
            self._arrow[name] = tbl.take(idx)
        return self._arrow[name]

    def table(self, name: str) -> pd.DataFrame:
        """Full dataset sorted by ``available_from``. Internal: views slice it; never hand it to features."""
        if name not in self._tables:
            self._tables[name] = self.arrow(name).to_pandas()
        return self._tables[name]

    def af_array(self, name: str) -> np.ndarray:
        if name not in self._af:
            days = pc.cast(self.arrow(name)["available_from"], pa.int32()).to_numpy(zero_copy_only=False)
            self._af[name] = days.astype("datetime64[D]")
        return self._af[name]

    def subtable(self, name: str, key: tuple[Any, ...], mask_fn: Callable[[pd.DataFrame], pd.Series]) -> str:
        """Cache a row subset of ``name`` (kept in availability order) under a derived name; returns that name."""
        sub = f"{name}::{key}"
        if sub not in self._tables:
            t = self.table(name)
            m = mask_fn(t).to_numpy()
            self._tables[sub] = t[m].reset_index(drop=True)
            self._af[sub] = self.af_array(name)[m]
        return sub

    def has(self, name: str) -> bool:
        return name in self._tables or self._path(name).is_file()

    @property
    def calendar(self) -> TradingCalendar:
        if self._calendar is None:
            self._calendar = TradingCalendar.from_frame(self.table("calendar"))
        return self._calendar

    def as_of(self, anchor: date, *, record: bool = True) -> PitView:
        return PitView(self, anchor, record=record)

    def poison_after(self, d: date, mode: PoisonMode = "inflate", *, seed: int = 0) -> PitStore:
        """S5 future-poison fixture: a new store whose ``data`` rows with ``available_from > d`` are poisoned."""
        return PitStore(
            self.snapshot_dir,
            self.mode,
            outputs_dir=self.outputs_dir,
            text_dir=self.text_dir,
            _parent=self,
            _poison=(d, mode, seed),
        )


_POISON_KEEP = frozenset(
    {"available_from", "role", "security_id", "company_id", "symbol", "code", "metric_id", "kind", "apply", "series",
     "period_type", "basis", "frequency", "category", "anchor"}
)  # fmt: skip


def _poison_arrow(name: str, tbl: pa.Table, d: date, mode: PoisonMode, seed: int) -> pa.Table:
    """Poison every ``data`` row with ``available_from > d``: numbers → NaN / shuffled / N(0,1)·1e6, dates shifted
    by up to ±400 days; then append 50 fake rows available after ``d`` but dated up to 30 days BEFORE ``d``."""
    if "role" not in tbl.column_names or name == "calendar":
        return tbl  # the calendar is the exchange schedule (known in advance); it is not poisoned
    rng = np.random.default_rng(int.from_bytes(hashlib.sha256(f"{name}|{seed}".encode()).digest()[:4], "little"))
    n = tbl.num_rows
    d32 = pa.scalar(d, pa.date32())
    future = pc.and_(
        pc.greater(tbl["available_from"], d32), pc.invert(pc.is_in(tbl["role"], pa.array(sorted(EXEMPT_ROLES))))
    )
    future = pc.fill_null(future, False)
    cols = {}
    for c in tbl.column_names:
        col = tbl[c]
        typ = col.type
        if c in _POISON_KEEP:
            cols[c] = col
        elif pa.types.is_floating(typ) or pa.types.is_integer(typ):
            if mode == "nan":
                new = pa.array(np.full(n, np.nan), pa.float64())
            elif mode == "shuffle":
                vals = pc.cast(col, pa.float64()).to_numpy(zero_copy_only=False)
                new = pa.array(rng.permutation(vals) * rng.choice([-3.0, 7.0]), pa.float64())
            else:
                new = pa.array(rng.normal(size=n) * 1e6, pa.float64())
            cols[c] = pc.if_else(future, new, pc.cast(col, pa.float64()))
        elif pa.types.is_date32(typ):
            days = pc.cast(col, pa.int32())
            shifted = pc.cast(
                pc.cast(pc.add(days, pa.array(rng.integers(-400, 400, size=n), pa.int32())), pa.int32()), pa.date32()
            )
            cols[c] = pc.if_else(future, shifted, col)
        else:
            cols[c] = col
    out = pa.table(cols)
    base_idx = np.flatnonzero(~pc.is_in(out["role"], pa.array(sorted(EXEMPT_ROLES))).to_numpy(zero_copy_only=False))
    if len(base_idx):
        pick = rng.choice(base_idx, size=min(50, len(base_idx)), replace=False)
        fake = out.take(pa.array(np.sort(pick)))
        m = fake.num_rows
        fcols = {}
        for c in fake.column_names:
            typ = fake[c].type
            if c == "available_from":
                fcols[c] = pa.array([d + timedelta(days=int(x)) for x in rng.integers(1, 400, size=m)], pa.date32())
            elif c in _POISON_KEEP:
                fcols[c] = fake[c]
            elif pa.types.is_floating(typ):
                fcols[c] = pa.array(rng.normal(size=m) * 1e6, pa.float64())
            elif pa.types.is_date32(typ):
                fcols[c] = pa.array([d - timedelta(days=int(x)) for x in rng.integers(0, 30, size=m)], pa.date32())
            else:
                fcols[c] = fake[c]
        out = pa.concat_tables([out, pa.table(fcols).cast(out.schema)])
    return out


class PitView:
    """Immutable per-anchor view; every accessor returns frames with ``max(available_from) <= anchor``."""

    def __init__(self, store: PitStore, anchor: date, *, record: bool = True) -> None:
        if store.mode == "backtest" and not store.calendar.is_good(anchor):
            raise LeakageError(f"anchor {anchor} is not a good NSE session (snap it with the calendar first)")
        self._store = store
        self.anchor = anchor
        self.records: list[ManifestRecord] = []
        self._record = record

    # ── guard + bookkeeping ─────────────────────────────────────────────────────────────────────────────────
    def _guard(self, df: pd.DataFrame, item: str, *, exempt_roles: frozenset[str] = frozenset()) -> pd.DataFrame:
        if "available_from" not in df.columns:
            raise LeakageError(f"{item}: frame has no available_from")
        checked = df if not exempt_roles else df[~df["role"].isin(exempt_roles)]
        mx = checked["available_from"].max() if len(checked) else None
        if mx is not None and mx > self.anchor:
            raise LeakageError(f"{item}: available_from {mx} > anchor {self.anchor}")
        out = _freeze(df)
        if self._record:
            self.records.append(ManifestRecord(self.anchor, item, len(out), frame_hash(out), mx))
        return out

    def _available(self, name: str) -> pd.DataFrame:
        """Rows with ``available_from <= anchor``: a prefix of the availability-sorted table (no date shortcut)."""
        t = self._store.table(name)
        pos = int(np.searchsorted(self._store.af_array(name), np.datetime64(self.anchor, "D"), side="right"))
        return t.iloc[:pos]

    @property
    def calendar(self) -> TradingCalendar:
        """The full exchange calendar (a schedule known in advance). Use :meth:`sessions` for data windows."""
        return self._store.calendar

    def sessions(self, lookback: int) -> np.ndarray:
        """The last ``lookback`` good sessions up to and including the anchor."""
        return self._store.calendar.lookback(self.anchor, lookback)

    # ── prices ──────────────────────────────────────────────────────────────────────────────────────────────
    def prices_raw(self, lookback_sessions: int | None = None, columns: Iterable[str] | None = None) -> pd.DataFrame:
        """Stitched bhav rows (EQ/BE/BZ, raw closes) with date in the last ``lookback_sessions`` sessions."""
        df = self._available("prices")
        if lookback_sessions is not None:
            start = self.sessions(lookback_sessions)[0].astype(object)
            df = df[df["date"] >= start]
        if columns is not None:
            df = df[[*dict.fromkeys([*columns, "available_from", "role"])]]
        return self._guard(df, f"prices_raw[{lookback_sessions}]")

    def factors(self, kinds: Iterable[str] = ("split", "bonus", "jump", "dividend")) -> pd.DataFrame:
        """Applied price factors known at the anchor (``available_from <= anchor``)."""
        df = self._available("factors")
        df = df[df["apply"].astype(bool) & df["kind"].isin(list(kinds))]
        return self._guard(df, f"factors[{','.join(kinds)}]")

    def prices_adj(
        self, lookback_sessions: int | None = None, *, kinds: Iterable[str] = ("split", "bonus", "jump", "dividend")
    ) -> pd.DataFrame:
        """Raw rows plus ``close_adj`` on the anchor's basis, from factors with ``available_from <= anchor`` only
        (02 §1.1, T-20). ``kinds`` without 'dividend' gives a split-only level series."""
        from ideation.lake.prices import back_adjust

        kinds = tuple(kinds)
        raw = self.prices_raw(lookback_sessions)
        fac = self.factors(kinds)
        adj = raw.assign(close_adj=back_adjust(raw, fac, self.anchor, kinds=kinds))
        return self._guard(adj, f"prices_adj[{lookback_sessions}|{','.join(kinds)}]")

    def price_panel(self, lookback_sessions: int) -> Any:
        """Dense sessions x securities panel (raw and back-adjusted close, value, delivery) of the last
        ``lookback_sessions`` good sessions up to the anchor: same rules as ``prices_adj``, vectorised (P3)."""
        from ideation.pit.panel import build_panel

        panel = build_panel(self, lookback_sessions)
        if panel.sessions[-1] != np.datetime64(self.anchor, "D"):
            raise LeakageError("price_panel: last session is not the anchor")
        if self._record:
            self.records.append(
                ManifestRecord(self.anchor, f"price_panel[{lookback_sessions}]", int(panel.close_raw.size),
                               panel.sha256, self.anchor)
            )  # fmt: skip
        return panel

    def basis_conversion_factors(self) -> pd.DataFrame:
        """03 §C2 exemption: split/bonus factors effective AFTER the anchor (role ``basis_conversion``), used only
        to put an anchor-date price on today's share basis for B1/B2. Every row is checked to carry that role."""
        t = self._store.table("factors")
        df = t[t["apply"].astype(bool) & (t["role"] == "basis_conversion") & (t["available_from"] > self.anchor)]
        if len(df) and not (df["role"] == "basis_conversion").all():
            raise LeakageError("basis_conversion_factors: a future row without the basis_conversion role")
        return self._guard(df, "basis_conversion_factors", exempt_roles=frozenset({"basis_conversion"}))

    def face_value_now(self) -> pd.DataFrame:
        """03 §C2 exemption (B2 only): today's face value per security, role ``basis_conversion``. B2 uses it only
        when no split/bonus took effect between the FY end and today (checked with basis_conversion_factors)."""
        df = self._store.table("face_value_now")
        if len(df) and not (df["role"] == "basis_conversion").all():
            raise LeakageError("face_value_now: a row without the basis_conversion role")
        return self._guard(df, "face_value_now", exempt_roles=frozenset({"basis_conversion"}))

    def rights(self) -> pd.DataFrame:
        return self._guard(self._available("rights"), "rights")

    # ── identity / reference ────────────────────────────────────────────────────────────────────────────────
    def securities(self) -> pd.DataFrame:
        """Security map rows that had started by the anchor; ``valid_to`` is clipped at the anchor so the frame says
        nothing about later delistings (identity hindsight is limited to the company link, disclosed)."""
        df = self._available("security_map")
        df = df.drop(columns=[c for c in _FUTURE_IDENTITY_COLS["security_map"] if c in df.columns])
        df = df.assign(valid_to=[min(v, self.anchor) for v in df["valid_to"]])
        return self._guard(df, "securities")

    def company(self) -> pd.DataFrame:
        return self._guard(self._available("company"), "company")

    def metric(self) -> pd.DataFrame:
        return self._guard(self._available("metric"), "metric")

    # ── fundamentals and other dated data ───────────────────────────────────────────────────────────────────
    def fundamentals(self, period_type: str = "A", metric_ids: Iterable[int] | None = None) -> pd.DataFrame:
        if period_type not in ("A", "Q"):
            raise LeakageError(f"period_type {period_type!r} has no backtest availability rule")
        if period_type == "Q" and self._store.mode == "backtest" and self.anchor < QUARTERLY_FROM:
            raise LeakageError(f"quarterly facts are not usable before {QUARTERLY_FROM} (02 §1.2)")
        ids = tuple(sorted(int(i) for i in metric_ids)) if metric_ids is not None else None
        sub = self._store.subtable(
            "financial_fact",
            (period_type, ids),
            lambda t: (t["period_type"] == period_type) & (t["metric_id"].isin(ids) if ids is not None else True),
        )
        return self._guard(self._available(sub), f"fundamentals[{period_type}|{ids}]")

    def shares(self) -> pd.DataFrame:
        return self._guard(self._available("shares_outstanding"), "shares")

    def shareholding(self) -> pd.DataFrame:
        return self._guard(self._available("shareholding"), "shareholding")

    def earnings_dates(self) -> pd.DataFrame:
        return self._guard(self._available("earnings_dates"), "earnings_dates")

    def text_docs(self, doc_type: str, columns: Iterable[str] | None = None) -> pd.DataFrame:
        """Concall documents of one type with ``available_from <= anchor`` (PT1 features; 05 §7.2). The date a
        document may be used is ``available_from`` only; ``doc_date`` is month-granular and is for windows."""
        if doc_type not in ("concall_transcript", "concall_ppt"):
            raise LeakageError(f"unknown text doc_type {doc_type!r}")
        sub = self._store.subtable(TEXT_DOCS, (doc_type,), lambda t: t["doc_type"] == doc_type)
        df = self._available(sub)
        if columns is not None:
            df = df[[*dict.fromkeys([*columns, "available_from", "role"])]]
        return self._guard(df, f"text_docs[{doc_type}]")

    def annual_docs(self, columns: Iterable[str] | None = None) -> pd.DataFrame:
        """Annual reports with ``available_from <= anchor`` (PT3 features). ``fiscal_year`` is only a label; the
        date a report may be used is ``available_from``; windows use ``fy_end``."""
        df = self._available(ANNUAL_DOCS)
        if columns is not None:
            df = df[[*dict.fromkeys([*columns, "available_from", "role"])]]
        return self._guard(df, "annual_docs")

    def issue_size(self) -> pd.DataFrame:
        return self._guard(self._available("issue_size"), "issue_size")

    def corporate_actions(self) -> pd.DataFrame:
        return self._guard(self._available("corporate_action"), "corporate_actions")

    def macro(self, codes: Iterable[str]) -> pd.DataFrame:
        codes = list(codes)
        df = self._available("series_daily")
        df = df[df["code"].isin(codes)]
        return self._guard(df, f"macro[{','.join(sorted(codes))}]")

    def macro_monthly(self, codes: Iterable[str]) -> pd.DataFrame:
        codes = list(codes)
        df = self._available("series_monthly")
        return self._guard(df[df["code"].isin(codes)], f"macro_monthly[{','.join(sorted(codes))}]")

    def fpi_sector(self) -> pd.DataFrame:
        return self._guard(self._available("fpi_sector"), "fpi_sector")

    def universe(self) -> pd.DataFrame:
        """The PIT universe at this anchor (``ideation universe build`` output; ``available_from = anchor``)."""
        df = self._available("universe")
        return self._guard(df[df["anchor"] == self.anchor], "universe")

    def has_universe(self) -> bool:
        """True when a stored universe table is attached to the store."""
        return self._store.has("universe")

    def earnings_vendor(self) -> pd.DataFrame:
        """05 O5 gate: vendor estimates, only for ``outcomes/analyst.py`` and ``features/events_vendor.py``."""
        caller = sys._getframe(1).f_globals.get("__name__", "")
        if caller not in VENDOR_CALLERS:
            raise VendorGateError(f"earnings_vendor() is not available to {caller!r} (05 O5)")
        return self._guard(self._available("earnings_vendor"), "earnings_vendor")


def records_to_rows(records: Iterable[ManifestRecord]) -> list[tuple[Any, ...]]:
    """De-duplicated ``(anchor, item, n_rows, sha256, max_available_from)`` rows for ``ideation.run_manifest``."""
    seen: dict[tuple[date, str], ManifestRecord] = {}
    for r in records:
        seen[(r.anchor, r.item)] = r
    return [(r.anchor, r.item, r.n_rows, r.sha256, r.max_available_from) for r in seen.values()]


SinkFn = Callable[[list[ManifestRecord]], None]
