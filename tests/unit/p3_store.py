"""Synthetic PIT snapshot for the P3 builders (no lake): 40 securities in 4 industry groups / 2 sectors, a random-walk
price panel with one split, index and macro series, yearly and quarterly shareholding, earnings events, FPI sector rows
and a stored universe table."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from ideation.pit.contract import PitStore
from ideation.pit.lags import macro_available_from, month_end, shp_available_from
from ideation.regime.macro import CPI, IIP

DAYS = [d.date() for d in pd.bdate_range("2016-01-04", "2019-12-31")]
N_SEC = 40
GROUPS = ["Capital Goods", "Chemicals", "Healthcare", "Power"]
SEC_OF_GROUP = {"Capital Goods": "Industrials", "Chemicals": "Industrials", "Healthcare": "Health",
                "Power": "Utilities"}  # fmt: skip
SPLIT = (10, date(2018, 6, 15), 0.5)  # security 10 splits 1:2 on this date (factor 0.5 for earlier closes)
A = date(2019, 6, 28)
EVENT_SEC = 20  # company 2: a single reported event, see EVENTS


def _w(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), path)


def make_store(tmp: Path, *, seed: int = 0) -> PitStore:
    rng = np.random.default_rng(seed)
    cal = pd.DataFrame({"session": DAYS, "in_bhav": True, "in_index": True, "bhav_missing": False, "n_eq": 1})
    _w(tmp / "derived" / "calendar.parquet", cal.assign(available_from=cal["session"], role="data"))
    cids = list(range(1, N_SEC + 1))
    grp = {c: GROUPS[(c - 1) % 4] for c in cids}
    co = pd.DataFrame({"company_id": cids, "sector": [SEC_OF_GROUP[grp[c]] for c in cids],
                       "industry_group": [grp[c] for c in cids], "available_from": date(1900, 1, 1),
                       "role": "reference"})  # fmt: skip
    _w(tmp / "company.parquet", co)
    sm = pd.DataFrame({"security_id": [c * 10 for c in cids], "symbol": [f"S{c}" for c in cids], "isin": "",
                       "valid_from": DAYS[0], "valid_to": DAYS[-1], "company_id": [float(c) for c in cids],
                       "link_method": "seed", "company_link": "isin", "series_last": "EQ",
                       "available_from": DAYS[0], "role": "identity"})  # fmt: skip
    _w(tmp / "derived" / "security_map.parquet", sm)

    rows = []
    for c in cids:
        sid = c * 10
        drift = 0.0004 if grp[c] in ("Capital Goods", "Chemicals") else -0.0001
        r = rng.normal(drift, 0.015, size=len(DAYS))
        px = 100.0 * np.exp(np.cumsum(r))
        for i, d in enumerate(DAYS):
            close = px[i] * (0.5 if sid == SPLIT[0] and d >= SPLIT[1] else 1.0)
            rows.append({"symbol": f"S{c}", "series": "EQ", "date": d, "open": close, "high": close, "low": close,
                         "close": close, "prev_close": close, "ttl_trd_qnty": int(5e5),
                         "turnover_lacs": float(40 + 20 * rng.random()), "no_of_trades": 100.0,
                         "deliv_per": float(40 + 20 * rng.random()), "security_id": sid, "isin": "",
                         "available_from": d, "role": "data"})  # fmt: skip
    _w(tmp / "derived" / "prices.parquet", pd.DataFrame(rows))
    fac = pd.DataFrame({"security_id": [SPLIT[0]], "ex_date": [SPLIT[1]], "eff_date": [SPLIT[1]], "factor": [SPLIT[2]],
                        "kind": ["split"], "source": ["t"], "flag": [""], "apply": [True],
                        "available_from": [SPLIT[1]], "role": ["data"]})  # fmt: skip
    _w(tmp / "derived" / "factors.parquet", fac)

    sd = []
    for code, base, vol in (("^CRSLDX", 10000.0, 0.01), ("^INDIAVIX", 15.0, 0.03), ("INR=X", 70.0, 0.003),
                            ("CL=F", 60.0, 0.02), ("^TNX", 2.5, 0.01)):  # fmt: skip
        lv = base * np.exp(np.cumsum(rng.normal(0, vol, size=len(DAYS))))
        sd += [{"code": code, "date": d, "value": float(v), "available_from": d, "role": "data"}
               for d, v in zip(DAYS, lv, strict=True)]  # fmt: skip
    _w(tmp / "series_daily.parquet", pd.DataFrame(sd))
    sm_rows = []
    for code in (CPI, IIP):
        for y in range(2015, 2020):
            for m in range(1, 13):
                p = date(y, m, 1)
                sm_rows.append({"code": code, "period": p, "value": 4.0 + 0.1 * m, "base_year": "x",
                                "available_from": macro_available_from(code, p), "role": "data"})  # fmt: skip
    _w(tmp / "series_monthly.parquet", pd.DataFrame(sm_rows))

    sh = []
    for c in cids:
        for y in (2016, 2017, 2018, 2019):
            pe = date(y, 3, 31)
            for cat, v in (("promoters", 50.0 + (y - 2016) ** 2 + c % 3), ("fii", 10.0 + 0.5 * (y - 2016)),
                           ("dii", 8.0), ("num_shareholders", 1000.0 * (1 + 0.1 * (y - 2016)))):  # fmt: skip
                sh.append({"company_id": c, "frequency": "Y", "category": cat, "period_end": pe, "value": v,
                           "available_from": shp_available_from(pe), "role": "data"})  # fmt: skip
        for pe in (date(2018, 6, 30), date(2018, 9, 30), date(2019, 3, 31)):  # quarterly rows must never be used
            sh.append({"company_id": c, "frequency": "Q", "category": "promoters", "period_end": pe, "value": 1.0e3,
                       "available_from": shp_available_from(pe), "role": "data"})  # fmt: skip
    _w(tmp / "shareholding.parquet", pd.DataFrame(sh))

    ev = []
    for c in cids:
        for d in (date(2019, 2, 6), date(2019, 5, 8)):
            e = d + timedelta(days=c % 5)
            ev.append({"company_id": c, "event_date": e, "has_eps_reported": True, "source": "t",
                       "available_from": e + timedelta(days=1), "role": "data"})  # fmt: skip
        ev.append({"company_id": c, "event_date": date(2019, 8, 7), "has_eps_reported": False, "source": "t",
                   "available_from": date(2019, 8, 8), "role": "data"})  # scheduled, no EPS yet
    _w(tmp / "earnings_dates.parquet", pd.DataFrame(ev))

    fp = []
    for rd in pd.date_range("2018-10-15", "2019-12-15", freq="SME"):
        for sector in ("Capital Goods", "Chemicals"):
            fp.append({"report_date": rd.date(), "sector": sector, "measure": "net_curr", "route": "Equity",
                       "inr_cr": 100.0, "usd_mn": 1.0, "available_from": rd.date() + timedelta(days=7),
                       "role": "data"})  # fmt: skip
            fp.append({"report_date": rd.date(), "sector": sector, "measure": "auc_end", "route": "Equity",
                       "inr_cr": 10000.0, "usd_mn": 1.0, "available_from": rd.date() + timedelta(days=7),
                       "role": "data"})  # fmt: skip
    _w(tmp / "fpi_sector.parquet", pd.DataFrame(fp))
    _w(tmp / "metric.parquet", pd.DataFrame({"metric_id": [1], "section": ["x"], "name": ["y"], "unit": "cr",
                                             "canonical_code": None, "source": "s",
                                             "available_from": date(1900, 1, 1), "role": "reference"}))  # fmt: skip

    um = []
    for a in (date(2018, 12, 31), A, date(2019, 9, 30)):
        for c in cids:
            um.append({"anchor": a, "security_id": c * 10, "traded_ok": True, "in_u_score": True,
                       "in_u_score_t": c != 40, "available_from": a})  # fmt: skip
    out = tmp / "outputs"
    _w(out / "universe_member.parquet", pd.DataFrame(um))
    (tmp / "manifest.json").write_text(json.dumps({"snap_id": "t", "observation_date": "2019-12-31"}))
    return PitStore(tmp, outputs_dir=out)


__all__ = ["DAYS", "A", "make_store", "month_end"]
