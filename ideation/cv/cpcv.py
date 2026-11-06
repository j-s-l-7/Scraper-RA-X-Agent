"""Combinatorial purged cross-validation (López de Prado 2018 ch. 12; 02 §4.5 as amended by 05 O4/§8).

Anchors are cut into ``n_groups`` contiguous groups; every choice of ``k`` groups is a test set:
C(n_groups, k) splits and C(n_groups − 1, k − 1) backtest paths.

* 1Y annual: anchors 2016–2024, 9 groups, k = 2 ⇒ 36 splits / 8 paths, embargo 1 anchor.
* tactical: anchors 2011-01…2025-06, 12 groups, k = 2 ⇒ 66 splits / 11 paths, embargo 1M → 1, 3M → 3 anchors.

For each test group with anchors [T_first, T_last] and label span (T_first, L_max]:
* purge: drop a training anchor whose label span (A, L_A] overlaps it, i.e. ``A < L_max and L_A > T_first``
  (for an anchor before the group this is exactly 03 §C3: ``L_A > T_first``);
* embargo: then drop the first ``embargo`` remaining (non-test, non-purged) anchors after T_last: the embargo starts
  where the purge ends (their features, e.g. momentum, contain the test label). For 1Y annual under C3 the anchor
  T_last + 1 is not purged (its label starts at T_last + 1's close), so the embargo removes exactly that anchor.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations

import numpy as np
import pandas as pd

from ideation.cv.walkforward import anchor_end_map


@dataclass(frozen=True)
class CPCVSplit:
    index: int
    test_groups: tuple[int, ...]
    test_anchors: tuple[pd.Timestamp, ...]
    train_anchors: tuple[pd.Timestamp, ...]
    purged: tuple[pd.Timestamp, ...]
    embargoed: tuple[pd.Timestamp, ...]


def anchor_groups(anchors: Sequence[object], n_groups: int) -> list[pd.DatetimeIndex]:
    idx = pd.DatetimeIndex(pd.to_datetime(list(anchors))).sort_values().unique()
    if len(idx) < n_groups:
        raise ValueError(f"{len(idx)} anchors < {n_groups} groups")
    return [idx[g] for g in np.array_split(np.arange(len(idx)), n_groups)]


def cpcv_splits(
    anchors: Sequence[object],
    label_end: Mapping[object, object] | pd.Series,
    n_groups: int,
    k: int,
    embargo: int,
) -> list[CPCVSplit]:
    ends = anchor_end_map(anchors, label_end)
    idx = ends.index
    groups = anchor_groups(idx, n_groups)
    out = []
    for i, chosen in enumerate(combinations(range(n_groups), k)):
        test = idx[idx.isin(np.concatenate([groups[g] for g in chosen]))]
        rest = idx.difference(test)
        purged: set[pd.Timestamp] = set()
        for g in chosen:
            t_first, l_max = groups[g][0], ends[groups[g]].max()
            purged.update(a for a in rest if a < l_max and ends[a] > t_first)
        embargoed: set[pd.Timestamp] = set()
        for g in chosen:
            after = [a for a in rest if a > groups[g][-1] and a not in purged]
            embargoed.update(after[:embargo])
        train = tuple(a for a in idx if a not in test and a not in purged and a not in embargoed)
        out.append(CPCVSplit(i, chosen, tuple(test), train, tuple(sorted(purged)), tuple(sorted(embargoed))))
    return out


def n_paths(n_groups: int, k: int) -> int:
    return math.comb(n_groups - 1, k - 1)


def cpcv_paths(splits: Sequence[CPCVSplit], n_groups: int) -> list[list[tuple[int, int]]]:
    """Backtest paths: path j takes, for every group g, the j-th split (in split order) that tests g.

    Returns ``[[(group, split_index), …] per path]``; each path covers every group (and anchor) exactly once.
    """
    by_group: dict[int, list[int]] = {g: [] for g in range(n_groups)}
    for s in splits:
        for g in s.test_groups:
            by_group[g].append(s.index)
    counts = {len(v) for v in by_group.values()}
    if len(counts) != 1:
        raise ValueError(f"unbalanced CPCV: groups tested {sorted(counts)} times")
    m = counts.pop()
    return [[(g, by_group[g][j]) for g in range(n_groups)] for j in range(m)]
