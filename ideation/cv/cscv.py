"""Combinatorially symmetric cross-validation (Bailey, Borwein, López de Prado & Zhu 2017) row splits for the PBO.

The T rows of a performance matrix (T observations × N trials) are cut into S contiguous blocks (S even). Every
choice of S/2 blocks is an in-sample set; its complement is the out-of-sample set: C(S, S/2) combinations.
"""

from __future__ import annotations

from itertools import combinations

import numpy as np


def cscv_splits(n_rows: int, n_blocks: int) -> list[tuple[np.ndarray, np.ndarray]]:
    """``[(is_rows, oos_rows), …]`` in ``itertools.combinations`` order of the in-sample blocks."""
    if n_blocks < 2 or n_blocks % 2:
        raise ValueError(f"n_blocks must be even and >= 2 (got {n_blocks})")
    if n_rows < n_blocks:
        raise ValueError(f"need at least one row per block ({n_rows} rows < {n_blocks} blocks)")
    blocks = np.array_split(np.arange(n_rows), n_blocks)
    out = []
    for chosen in combinations(range(n_blocks), n_blocks // 2):
        rest = [b for b in range(n_blocks) if b not in chosen]
        out.append((np.concatenate([blocks[b] for b in chosen]), np.concatenate([blocks[b] for b in rest])))
    return out
