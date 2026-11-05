"""Snapshot the lake to parquet (01 §1.2; 02 §1.1; 05 §10.3 P1).

``take_snapshot`` runs two steps:

1. **Pull.** One ``REPEATABLE READ READ ONLY`` transaction in ``LakeMode.SNAPSHOT``. The backtest ban list applies,
   except the narrow ``snapshot_exempt`` of ``EARNINGS_VENDOR_FULL`` (05 O5). Each dataset is COPY-streamed to a raw
   parquet file. Nothing else happens inside the transaction, because the session has a 10-minute
   idle-in-transaction limit.
2. **Finalise.** Runs after commit. Each dataset is sorted deterministically and given an ``available_from`` column
   (the first date a decision taken after that day's close may use the row), plus a ``role`` column. The finished
   parquet is hashed. ``snap_id = <observation date>-<sha8 of the file hashes>`` is content-addressed: an unchanged
   lake gives the same id.

Roles drive the future-poison fixture (S5, T-01):

* ``reference``: undated master data (company, metric, macro dictionary). ``available_from`` = 1900-01-01.
* ``identity``: listing and rename history used only to stitch securities (02 §2.2). The security map is built once
  from the clean snapshot (hindsight identity, disclosed in the bias block).
* ``basis_conversion``: split and bonus factors. B1/B2 market cap uses them only to convert an observable quantity's
  units (03 §C2).
* ``data``: everything else, with ``available_from`` from :mod:`ideation.pit.lags`.

These three roles are exempt from poisoning; every ``data`` row after the poison date is poisoned.

The observation date ``today`` is the lake's last bhav session (no clock read): results events after it are
schedules, not reported results (02 §1.2).
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from ideation.config import canonical_json
from ideation.lake import queries as Q
from ideation.lake.adapter import LakeMode, LakeReader
from ideation.lake.queries import NamedQuery
from ideation.pit import lags

log = logging.getLogger(__name__)

EPOCH = date(1900, 1, 1)
"""``available_from`` of undated reference/identity rows."""
NEVER = date(9999, 12, 31)
"""``available_from`` of rows with no backtest availability rule (half-year 'H' facts; 02 has no rule)."""
BHAV_SINCE = date(2010, 1, 1)
DELTA_OVERLAP_DAYS = 45
ROLES = ("reference", "identity", "basis_conversion", "data", "vendor", "crosscheck")
POISON_EXEMPT_ROLES = frozenset({"reference", "identity", "basis_conversion"})
_PARQUET = {"compression": "zstd", "use_dictionary": True, "write_statistics": True}


@dataclass(frozen=True)
class Dataset:
    name: str
    query: NamedQuery
    role: str
    sort_by: tuple[str, ...]
    rule: str
    """Human-readable ``available_from`` rule, recorded in the manifest."""
    params: dict[str, Any] | None = None
    delta_col: str | None = None
    """Date column for ``--delta`` (incremental pull merged with the base snapshot)."""


DATASETS: tuple[Dataset, ...] = (
    Dataset("company", Q.COMPANY, "reference", ("company_id",), "1900-01-01 (reference)"),
    Dataset("metric", Q.METRIC, "reference", ("metric_id",), "1900-01-01 (reference)"),
    Dataset("macro_series", Q.MACRO_SERIES, "reference", ("code",), "1900-01-01 (reference)"),
    Dataset("listing_history", Q.LISTING_HISTORY, "identity", ("symbol", "series", "isin"), "first_seen (identity)"),
    Dataset(
        "symbol_change",
        Q.SYMBOL_CHANGE,
        "identity",
        ("effective_date", "old_symbol", "new_symbol"),
        "effective_date (identity)",
    ),
    Dataset("name_change", Q.NAME_CHANGE, "identity", ("effective_date", "symbol", "old_name"), "effective_date"),
    Dataset("bhav_dates", Q.BHAV_DATES, "data", ("date",), "date (EOD)", {"since": BHAV_SINCE}),
    Dataset(
        "bhav",
        Q.BHAV,
        "data",
        ("date", "symbol", "series"),
        "date (EOD; 02 §1.2)",
        {"since": BHAV_SINCE},
        delta_col="date",
    ),
    Dataset(
        "adj_factor",
        Q.ADJ_FACTOR,
        "data",
        ("symbol", "ex_date", "kind"),
        "ex_date; split/bonus rows role=basis_conversion (03 §C2)",
    ),
    Dataset("corporate_action", Q.CORPORATE_ACTION, "data", ("company_id", "action_type", "ex_date"), "ex_date"),
    Dataset("shares_outstanding", Q.SHARES_OUTSTANDING, "data", ("company_id", "as_of"), "as_of + 1 (02 §1.2)"),
    Dataset("earnings_dates", Q.EARNINGS_DATES, "data", ("company_id", "event_date"), "event_date + 1"),
    Dataset(
        "earnings_vendor",
        Q.EARNINGS_VENDOR_FULL,
        "vendor",
        ("company_id", "event_date"),
        "event_date + 1 (05 §10.3; gated by PitView.earnings_vendor)",
    ),
    Dataset(
        "financial_fact",
        Q.FINANCIAL_FACT,
        "data",
        ("company_id", "period_type", "period_end", "basis", "metric_id"),
        "A/Q: lags.fact_available_from with first matched results event (event_window_end); H: never",
    ),
    Dataset(
        "shareholding",
        Q.SHAREHOLDING,
        "data",
        ("company_id", "frequency", "period_end", "category"),
        "lags.shp_available_from(period_end)",
    ),
    Dataset("series_daily", Q.SERIES_DAILY, "data", ("code", "date"), "lags.macro_available_from"),
    Dataset("series_monthly", Q.SERIES_MONTHLY, "data", ("code", "period"), "lags.macro_available_from"),
    Dataset("series_annual", Q.SERIES_ANNUAL, "data", ("code", "year"), "lags.macro_available_from (Dec-31 + 270d)"),
    Dataset("fpi_sector", Q.FPI_SECTOR, "data", ("report_date", "sector", "measure", "route"), "report_date + 7"),
    Dataset("issue_size", Q.ISSUE_SIZE, "data", ("symbol", "date", "series"), "date"),
    Dataset(
        "yf_adj_close",
        Q.YF_ADJ_CLOSE,
        "crosscheck",
        ("symbol", "date"),
        "date (T-22 cross-check only)",
        {"since": BHAV_SINCE},
        delta_col="date",
    ),
)
DATASET_BY_NAME = {d.name: d for d in DATASETS}


# ── availability (vectorised on Arrow / pandas) ─────────────────────────────────────────────────────────────
def _days(col: pa.ChunkedArray | pa.Array, k: int = 0) -> pa.Array:
    """date32 column shifted by ``k`` days (exact integer arithmetic on the day count)."""
    as_int = pc.cast(col, pa.int32())
    shifted = pc.cast(pc.add(as_int, pa.scalar(k, pa.int32())), pa.int32()) if k else as_int
    return pc.cast(shifted, pa.date32())


def _const(n: int, d: date) -> pa.Array:
    return pa.array(np.full(n, (d - date(1970, 1, 1)).days, dtype=np.int32), pa.int32()).cast(pa.date32())


def _from_python(values: list[date]) -> pa.Array:
    return pa.array(values, pa.date32())


def fy_month_map(facts: pa.Table) -> dict[int, int]:
    """Fiscal-year-end month per company: the most common month among its last 3 annual period_ends (ties: the
    latest). There is no ``fy_month`` column in the lake (02 §0.5: Mar 2,595, Dec 40, Jun 21 companies)."""
    ann = facts.filter(pc.equal(facts["period_type"], "A")).select(["company_id", "period_end"])
    df = ann.to_pandas().drop_duplicates()
    if df.empty:
        return {}
    df["period_end"] = pd.to_datetime(df["period_end"])
    df = df.sort_values(["company_id", "period_end"]).groupby("company_id").tail(3)
    df["m"] = df["period_end"].dt.month
    out: dict[int, int] = {}
    for cid, g in df.groupby("company_id"):
        counts = g["m"].value_counts()
        best = counts[counts == counts.max()].index
        out[int(cid)] = int(g.loc[g["m"].isin(best), "m"].iloc[-1])
    return out


def fact_availability(facts: pa.Table, events: pa.Table, today: date) -> tuple[pa.Array, dict[str, Any]]:
    """``available_from`` for every financial_fact row (02 §1.2), computed per (company, period_type, period_end).

    The first results event (``eps_reported`` not null, ``event_date <= today``) inside
    ``(period_end, lags.event_window_end]`` moves availability later, never earlier. 'H' rows have no rule
    (lags raises) and get :data:`NEVER`.
    """
    fy = fy_month_map(facts)
    keys = (
        facts.select(["company_id", "period_type", "period_end"])
        .group_by(["company_id", "period_type", "period_end"])
        .aggregate([])
        .to_pandas()
    )
    ev = events.filter(pc.field("has_eps_reported")).select(["company_id", "event_date"]).to_pandas()
    ev = ev[ev["event_date"] <= today]
    by_co: dict[int, np.ndarray] = {
        int(c): np.sort(np.asarray(g["event_date"].tolist(), dtype="datetime64[D]"))
        for c, g in ev.groupby("company_id")
    }
    avail: list[date] = []
    n_event = n_rule = n_never = 0
    for cid, pt, pe in keys.itertuples(index=False):
        if pt not in ("A", "Q"):
            avail.append(NEVER)
            n_never += 1
            continue
        fym = fy.get(int(cid), 3)
        evs = by_co.get(int(cid))
        matched = None
        if evs is not None:
            hi = lags.event_window_end(pe, pt, fym)
            i = int(np.searchsorted(evs, np.datetime64(pe, "D"), side="right"))
            if i < len(evs) and evs[i] <= np.datetime64(hi, "D"):
                matched = evs[i].astype(object)
        a = lags.fact_available_from(pe, pt, fym, matched, today=today)
        statutory = lags.results_deadline(pe, pt, fym) + lags.ONE_DAY
        n_event, n_rule = (n_event + 1, n_rule) if a > statutory else (n_event, n_rule + 1)
        avail.append(a)
    keys["available_from"] = avail
    key_tbl = pa.Table.from_pandas(keys, preserve_index=False).cast(
        pa.schema(
            [
                ("company_id", pa.int64()),
                ("period_type", pa.string()),
                ("period_end", pa.date32()),
                ("available_from", pa.date32()),
            ]
        )
    )
    joined = facts.select(["company_id", "period_type", "period_end"]).append_column(
        "_row", pa.array(np.arange(facts.num_rows))
    )
    joined = joined.join(key_tbl, ["company_id", "period_type", "period_end"], join_type="left outer")
    joined = joined.sort_by("_row")
    stats = {
        "keys": len(keys),
        "keys_event_later": n_event,
        "keys_statutory": n_rule,
        "keys_never_H": n_never,
        "fy_month_counts": pd.Series(list(fy.values())).value_counts().sort_index().to_dict(),
    }
    return joined["available_from"].combine_chunks(), stats


def _per_unique(tbl: pa.Table, cols: list[str], fn: Callable[..., date]) -> pa.Array:
    keys = tbl.select(cols).to_pandas()
    uniq = keys.drop_duplicates().reset_index(drop=True)
    uniq["_af"] = [fn(*row) for row in uniq.itertuples(index=False)]
    merged = keys.merge(uniq, on=cols, how="left", validate="many_to_one")
    return _from_python(merged["_af"].tolist())


def add_availability(name: str, tbl: pa.Table, ctx: dict[str, Any]) -> tuple[pa.Table, dict[str, Any]]:
    """Append ``available_from`` (date32) and ``role`` (string) to a dataset."""
    n = tbl.num_rows
    stats: dict[str, Any] = {}
    role = DATASET_BY_NAME[name].role
    roles: pa.Array | None = None
    if name in ("company", "metric", "macro_series"):
        af = _const(n, EPOCH)
    elif name == "listing_history":
        af = _days(tbl["first_seen"])
    elif name in ("symbol_change", "name_change"):
        af = _days(tbl["effective_date"])
    elif name in ("bhav", "bhav_dates", "issue_size", "yf_adj_close"):
        af = _days(tbl["date"])
    elif name == "adj_factor":
        af = _days(tbl["ex_date"])
        is_basis = pc.is_in(tbl["kind"], pa.array(["split", "bonus"]))
        roles = pc.if_else(is_basis, "basis_conversion", "data")
    elif name == "corporate_action":
        af = _days(tbl["ex_date"])
    elif name == "shares_outstanding":
        af = _days(tbl["as_of"], 1)
    elif name in ("earnings_dates", "earnings_vendor"):
        af = _days(tbl["event_date"], 1)
    elif name == "financial_fact":
        af, stats = fact_availability(tbl, ctx["earnings_dates"], ctx["today"])
    elif name == "shareholding":
        af = _per_unique(tbl, ["period_end"], lags.shp_available_from)
    elif name == "series_daily":
        af = _per_unique(tbl, ["code", "date"], lags.macro_available_from)
    elif name == "series_monthly":
        af = _per_unique(tbl, ["code", "period"], lags.macro_available_from)
    elif name == "series_annual":
        af = _per_unique(tbl, ["code", "year"], lambda code, year: lags.macro_available_from(code, date(year, 1, 1)))
    elif name == "fpi_sector":
        af = _days(tbl["report_date"], lags.FPI_SECTOR_LAG_DAYS)
    else:  # pragma: no cover - every dataset must have a rule
        raise KeyError(f"no availability rule for dataset {name!r}")
    if pc.any(pc.is_null(af)).as_py():
        raise ValueError(f"{name}: null available_from")
    tbl = tbl.append_column("available_from", af)
    tbl = tbl.append_column("role", roles if roles is not None else pa.array([role] * n, pa.string()))
    return tbl, stats


# ── files ───────────────────────────────────────────────────────────────────────────────────────────────────
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_parquet(tbl: pa.Table, path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(tbl.replace_schema_metadata(None), path, **_PARQUET)
    return sha256_file(path)


def sort_table(tbl: pa.Table, keys: tuple[str, ...]) -> pa.Table:
    """Deterministic order: the declared keys, then every remaining column as a tie-break."""
    rest = [c for c in tbl.column_names if c not in keys and pa.types.is_primitive(tbl.schema.field(c).type)]
    rest += [c for c in tbl.column_names if c not in keys and pa.types.is_string(tbl.schema.field(c).type)]
    return tbl.take(pc.sort_indices(tbl, sort_keys=[(k, "ascending") for k in (*keys, *rest)]))


@dataclass
class SnapshotResult:
    snap_id: str
    path: Path
    manifest: dict[str, Any]
    reused: bool = False
    timings: dict[str, float] = field(default_factory=dict)


def snapshots_root(data_dir: Path) -> Path:
    return data_dir / "snapshots"


def latest_snapshot(data_dir: Path) -> Path | None:
    """Most recent finished snapshot directory (by manifest ``created_order``)."""
    root = snapshots_root(data_dir)
    if not root.is_dir():
        return None
    found = []
    for d in root.iterdir():
        m = d / "manifest.json"
        if d.is_dir() and not d.name.startswith(".") and m.is_file():
            found.append((json.loads(m.read_text())["observation_date"], m.stat().st_mtime, d))
    return max(found)[2] if found else None


def read_manifest(path: Path) -> dict[str, Any]:
    return json.loads((path / "manifest.json").read_text())


def fetch_lake_state(reader: LakeReader) -> dict[str, Any]:
    with reader.session() as s:
        row = s.fetch_one(Q.LAKE_STATE)
    return dict(zip(_LAKE_STATE_KEYS, row, strict=True))


_LAKE_STATE_KEYS = (
    "max_bhav",
    "max_nsei",
    "max_fact_ts",
    "n_fact",
    "max_shares",
    "n_shares",
    "max_event",
    "n_event",
    "max_factor_ts",
    "n_factor",
    "n_listing",
    "max_symchg_ts",
    "n_company",
    "n_shp",
)


def take_snapshot(
    reader: LakeReader,
    data_dir: Path,
    *,
    git_sha: str | None,
    config_hash: str,
    delta: bool = False,
    datasets: tuple[Dataset, ...] = DATASETS,
) -> SnapshotResult:
    """Pull every dataset in one consistent transaction, finalise, hash and publish under ``snapshots/<snap_id>``."""
    if reader.mode is not LakeMode.SNAPSHOT:
        raise ValueError("snapshots run in LakeMode.SNAPSHOT (ban list on, vendor exemption only)")
    root = snapshots_root(data_dir)
    tmp = root / f".tmp-{uuid.uuid4().hex[:12]}"
    (tmp / "raw").mkdir(parents=True)
    timings: dict[str, float] = {}
    base = latest_snapshot(data_dir) if delta else None
    if delta and base is None:
        raise FileNotFoundError("--delta needs an earlier full snapshot")
    base_manifest = read_manifest(base) if base else None
    try:
        t0 = time.perf_counter()
        with reader.snapshot() as s:
            state = dict(zip(_LAKE_STATE_KEYS, s.fetch_one(Q.LAKE_STATE), strict=True))
            for ds in datasets:
                t = time.perf_counter()
                params = dict(ds.params or {})
                if base_manifest is not None and ds.delta_col:
                    params["since"] = date.fromisoformat(base_manifest["datasets"][ds.name]["max_date"]) - timedelta(
                        days=DELTA_OVERLAP_DAYS
                    )
                tbl = s.copy_arrow(ds.query, params or None)
                pq.write_table(tbl, tmp / "raw" / f"{ds.name}.parquet", compression="lz4")
                timings[f"pull_{ds.name}"] = round(time.perf_counter() - t, 2)
                log.info("snapshot pull", extra={"dataset": ds.name, "rows": tbl.num_rows})
                del tbl
        timings["pull_total"] = round(time.perf_counter() - t0, 2)
        today = state["max_bhav"]
        ctx: dict[str, Any] = {"today": today}
        entries: dict[str, dict[str, Any]] = {}
        order = sorted(datasets, key=lambda d: d.name != "earnings_dates")  # facts need the events first
        for ds in order:
            t = time.perf_counter()
            tbl = pq.read_table(tmp / "raw" / f"{ds.name}.parquet")
            if base is not None and ds.delta_col:
                since = date.fromisoformat(base_manifest["datasets"][ds.name]["max_date"]) - timedelta(  # type: ignore[index]
                    days=DELTA_OVERLAP_DAYS
                )
                old = pq.read_table(base / f"{ds.name}.parquet").drop_columns(["available_from", "role"])
                old = old.filter(pc.less(old[ds.delta_col], pa.scalar(since, pa.date32())))
                tbl = pa.concat_tables([old, tbl.cast(old.schema)])
            if ds.name == "earnings_dates":
                ctx["earnings_dates"] = tbl
            tbl = sort_table(tbl, ds.sort_by)
            tbl, stats = add_availability(ds.name, tbl, ctx)
            sha = write_parquet(tbl, tmp / f"{ds.name}.parquet")
            af = tbl["available_from"]
            date_cols = [
                c for c in tbl.column_names if c != "available_from" and pa.types.is_date32(tbl.schema.field(c).type)
            ]
            entries[ds.name] = {
                "file": f"{ds.name}.parquet",
                "rows": tbl.num_rows,
                "sha256": sha,
                "role": ds.role,
                "query": ds.query.name,
                "rule": ds.rule,
                "min_available_from": str(pc.min(af).as_py()),
                "max_available_from": str(pc.max(af).as_py()),
                "max_date": str(pc.max(tbl[date_cols[0]]).as_py()) if date_cols else None,
                **({"stats": stats} if stats else {}),
            }
            timings[f"final_{ds.name}"] = round(time.perf_counter() - t, 2)
            del tbl
        shutil.rmtree(tmp / "raw")
        content = hashlib.sha256(canonical_json({k: v["sha256"] for k, v in entries.items()}).encode()).hexdigest()
        snap_id = f"{today:%Y%m%d}-{content[:8]}"
        manifest = {
            "snap_id": snap_id,
            "observation_date": str(today),
            "mode": "delta" if delta else "full",
            "base_snap_id": base_manifest["snap_id"] if base_manifest else None,
            "lake_state": state,
            "git_sha": git_sha,
            "config_hash": config_hash,
            "content_sha256": content,
            "datasets": entries,
            "poison_exempt_roles": sorted(POISON_EXEMPT_ROLES),
            "timings_s": timings,
        }
        (tmp / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str, sort_keys=True))
        final = root / snap_id
        if final.exists():  # identical content already published: keep the old one (immutable)
            shutil.rmtree(tmp)
            return SnapshotResult(snap_id, final, read_manifest(final), reused=True, timings=timings)
        tmp.rename(final)
        return SnapshotResult(snap_id, final, manifest, timings=timings)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


# ── derived datasets (02 §2.2, §2.3, §3.2): built from the raw snapshot, never from the lake ─────────────────
DERIVED_DIR = "derived"


def load(snapshot_dir: Path, name: str, columns: list[str] | None = None) -> pd.DataFrame:
    return pq.read_table(snapshot_dir / f"{name}.parquet", columns=columns).to_pandas()


def build_derived(snapshot_dir: Path, *, git_sha: str | None = None) -> dict[str, Any]:
    """calendar, security_map, prices (stitched bhav), factors (security level) and rights under ``derived/``.

    Every derived row carries ``available_from`` and ``role``. The derived manifest records file hashes and the code
    version, because a code change can change derived content while the raw snapshot (``snap_id``) stays the same.
    """
    from ideation.lake.prices import build_factors
    from ideation.lake.symbols import assign_security, build_calendar, stitch
    from ideation.pit.calendar import TradingCalendar

    t0 = time.perf_counter()
    out_dir = snapshot_dir / DERIVED_DIR
    tmp = snapshot_dir / f".derived-{uuid.uuid4().hex[:8]}"
    tmp.mkdir()
    stats: dict[str, Any] = {}
    try:
        bd = load(snapshot_dir, "bhav_dates", ["date", "n_rows", "n_eq"])
        idx = load(snapshot_dir, "series_daily", ["code", "date"])
        cal_df = build_calendar(bd, idx.loc[idx["code"] == "^NSEI", "date"])
        cal = TradingCalendar.from_frame(cal_df)
        stats["calendar"] = {
            "sessions": len(cal_df),
            "bhav_missing": [str(d) for d in cal_df.loc[cal_df["bhav_missing"], "session"]],
            "first": str(cal_df["session"].iloc[0]),
            "last": str(cal_df["session"].iloc[-1]),
        }
        lh = load(snapshot_dir, "listing_history", ["symbol", "isin", "series", "first_seen", "last_seen"])
        sc = load(snapshot_dir, "symbol_change", ["old_symbol", "new_symbol", "effective_date"])
        co = load(snapshot_dir, "company", ["company_id", "nse_symbol", "isin"])
        st = stitch(lh, sc, co, cal)
        stats["stitch"] = st.stats
        seg = st.segments.assign(available_from=st.segments["valid_from"], role="identity")
        bhav = load(snapshot_dir, "bhav", [
            "symbol", "series", "date", "open", "high", "low", "close", "prev_close", "ttl_trd_qnty", "turnover_lacs",
            "no_of_trades", "deliv_per",
        ])  # fmt: skip
        prices, astats = assign_security(bhav, st.segments)
        del bhav
        stats["prices"] = astats
        prices = prices.assign(available_from=prices["date"], role="data")
        fr = build_factors(
            prices,
            load(snapshot_dir, "adj_factor", ["symbol", "ex_date", "factor", "kind"]),
            load(snapshot_dir, "corporate_action", ["company_id", "action_type", "ex_date", "detail"]),
            st.segments,
            co,
        )
        stats["factors"] = fr.stats
        files = {
            "calendar": cal_df.assign(available_from=cal_df["session"], role="data"),
            "security_map": seg,
            "prices": prices,
            "factors": fr.factors,
            "rights": fr.rights.assign(role="data"),
        }
        hashes = {}
        for name, df in files.items():
            tbl = pa.Table.from_pandas(df.reset_index(drop=True), preserve_index=False)
            hashes[name] = {"rows": tbl.num_rows, "sha256": write_parquet(tbl, tmp / f"{name}.parquet")}
        manifest = {"git_sha": git_sha, "files": hashes, "stats": stats,
                    "build_s": round(time.perf_counter() - t0, 1)}  # fmt: skip
        (tmp / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str, sort_keys=True))
        if out_dir.exists():
            shutil.rmtree(out_dir)
        tmp.rename(out_dir)
        return manifest
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
