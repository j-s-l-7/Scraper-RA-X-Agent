"""Reproducibility helpers: content hashes of frames (02 T-19; ``ideation.run_manifest`` rows in Phase 1)."""

from __future__ import annotations

import hashlib
import io

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


def frame_parquet_bytes(df: pd.DataFrame) -> bytes:
    """Parquet bytes with fixed writer options. They are stable for identical frames within one environment;
    the file metadata records library versions, so hashes are not comparable across lockfile changes."""
    buf = io.BytesIO()
    table = pa.Table.from_pandas(df, preserve_index=False)
    pq.write_table(table, buf, compression="zstd", write_statistics=False, use_dictionary=False)
    return buf.getvalue()


def frame_sha256(df: pd.DataFrame) -> str:
    return hashlib.sha256(frame_parquet_bytes(df)).hexdigest()
