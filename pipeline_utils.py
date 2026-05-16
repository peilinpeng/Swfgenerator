#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Small shared helpers for the CH2025 EPW pipeline."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


def log(msg: str) -> None:
    print(msg, flush=True)


def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def read_table(path: str | Path, usecols=None, chunksize=None) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Input file not found: {p}")
    if chunksize is not None:
        if p.suffix.lower() == ".parquet":
            raise ValueError("chunksize is only supported for CSV inputs")
        return pd.read_csv(p, usecols=usecols, chunksize=chunksize)
    if p.suffix.lower() == ".parquet":
        return pd.read_parquet(p, columns=usecols)
    return pd.read_csv(p, usecols=usecols)


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def save_table(df: pd.DataFrame, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.suffix.lower() == ".parquet":
        df.to_parquet(p, index=False)
    else:
        df.to_csv(p, index=False)
    return p
