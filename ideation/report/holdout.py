# ruff: noqa: E501, N818
"""``ideation holdout unseal``: IRREVERSIBLE. Writes the one ``ideation.holdout_unseal`` row of a family (05 §8).

Ready but NOT run by MS3: the owner decides when, and which trial is the frozen champion. The row records the frozen
champion and baseline config hashes; after it exists the holdout labels become readable through ``load_labels``
for that family, and re-selecting requires a new family name.
"""

from __future__ import annotations

from typing import Any

from ideation.labels.loader import FAMILY_HORIZONS


class UnsealRefused(RuntimeError):
    pass


def unseal(conn: Any, family: str, champion_trial_id: int, baseline_trial_id: int | None, *, yes: bool, note: str = "") -> dict[str, Any]:
    """Insert the family's unseal row. Refuses without ``yes``, for an unknown family, a trial of another family, or a
    second call. Run it inside a transaction you control in tests (a stray live call unseals the holdout)."""
    if not yes:
        raise UnsealRefused("unsealing is irreversible: pass --yes after explicit owner confirmation")
    if family not in FAMILY_HORIZONS:
        raise UnsealRefused(f"unknown family {family!r}")
    if conn.execute("select 1 from ideation.holdout_unseal where family = %s", (family,)).fetchone():
        raise UnsealRefused(f"family {family!r} is already unsealed (once only)")
    hashes = {}
    for label, tid in (("champion", champion_trial_id), ("baseline", baseline_trial_id)):
        if tid is None:
            hashes[label] = None
            continue
        row = conn.execute("select family, config_hash, status from ideation.experiment_trial where trial_id = %s", (tid,)).fetchone()
        if row is None or row[0] != family:
            raise UnsealRefused(f"{label} trial {tid} is not a trial of family {family!r}")
        if row[2] != "ok":
            raise UnsealRefused(f"{label} trial {tid} has status {row[2]!r}, not 'ok'")
        hashes[label] = row[1]
    conn.execute(
        "insert into ideation.holdout_unseal (family, champion_trial_id, champion_config_hash, baseline_config_hash, note)"
        " values (%s, %s, %s, %s, %s)",
        (family, champion_trial_id, hashes["champion"], hashes["baseline"], note or "owner-confirmed"),
    )
    return {"family": family, "champion": hashes["champion"], "baseline": hashes["baseline"]}
