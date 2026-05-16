#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
05b_build_candidate_daily_summary_v4_1.py

Purpose:
    Build a compact daily summary from the enriched hourly future candidate archive.

Why this exists:
    FRY selection and most XMY profile scores do not need the full hourly candidate
    pool in memory. This script compresses the archive into daily statistics so
    multiple stations × GWLs × XMY profiles can be processed more efficiently.

Output columns include:
    station_id, model_chain, ref_state, target_state, year, month, day,
    tas_daily_mean, hurs_daily_mean, rsds_daily_mean, rsds_daily_sum,
    tmax_daily, tmin_daily, max_hourly_tas, cdh_daily_sum
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd

from pipeline_utils import log


def read_chunks(path: Path, chunksize: int):
    if path.suffix.lower() == ".parquet":
        yield pd.read_parquet(path)
    else:
        yield from pd.read_csv(path, chunksize=chunksize)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build daily summary from hourly future candidate archive.")
    p.add_argument("--candidate-pool", required=True)
    p.add_argument("--output", default=None)
    p.add_argument("--chunksize", type=int, default=500_000)
    p.add_argument("--cdh-base-temperature-c", type=float, default=26.0)
    return p.parse_args()


def aggregate_chunk(df: pd.DataFrame, cdh_base: float) -> pd.DataFrame:
    required = {"station_id", "model_chain", "ref_state", "target_state", "year", "month", "day", "tas_future", "hurs_future", "rsds_future"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Candidate pool missing columns for daily summary: {sorted(missing)}")
    df = df.copy()
    df["cdh_hourly"] = np.maximum(pd.to_numeric(df["tas_future"], errors="coerce") - cdh_base, 0.0)
    group = ["station_id", "model_chain", "ref_state", "target_state", "year", "month", "day"]
    out = df.groupby(group, sort=False).agg(
        tas_daily_sum=("tas_future", "sum"),
        tas_hour_count=("tas_future", "count"),
        hurs_daily_sum=("hurs_future", "sum"),
        hurs_hour_count=("hurs_future", "count"),
        rsds_daily_sum=("rsds_future", "sum"),
        rsds_hour_count=("rsds_future", "count"),
        tmax_daily=("tas_future", "max"),
        tmin_daily=("tas_future", "min"),
        max_hourly_tas=("tas_future", "max"),
        cdh_daily_sum=("cdh_hourly", "sum"),
    ).reset_index()
    return out


def combine_partials(parts: List[pd.DataFrame]) -> pd.DataFrame:
    tmp = pd.concat(parts, ignore_index=True)
    group = ["station_id", "model_chain", "ref_state", "target_state", "year", "month", "day"]
    out = tmp.groupby(group, sort=False).agg(
        tas_daily_sum=("tas_daily_sum", "sum"),
        tas_hour_count=("tas_hour_count", "sum"),
        hurs_daily_sum=("hurs_daily_sum", "sum"),
        hurs_hour_count=("hurs_hour_count", "sum"),
        rsds_daily_sum=("rsds_daily_sum", "sum"),
        rsds_hour_count=("rsds_hour_count", "sum"),
        tmax_daily=("tmax_daily", "max"),
        tmin_daily=("tmin_daily", "min"),
        max_hourly_tas=("max_hourly_tas", "max"),
        cdh_daily_sum=("cdh_daily_sum", "sum"),
    ).reset_index()
    out["tas_daily_mean"] = out["tas_daily_sum"] / out["tas_hour_count"].replace(0, np.nan)
    out["hurs_daily_mean"] = out["hurs_daily_sum"] / out["hurs_hour_count"].replace(0, np.nan)
    out["rsds_daily_mean"] = out["rsds_daily_sum"] / out["rsds_hour_count"].replace(0, np.nan)
    cols = group + [
        "tas_daily_mean", "hurs_daily_mean", "rsds_daily_mean", "rsds_daily_sum",
        "tmax_daily", "tmin_daily", "max_hourly_tas", "cdh_daily_sum",
        "tas_hour_count", "hurs_hour_count", "rsds_hour_count",
    ]
    return out[cols].sort_values(["model_chain", "year", "month", "day"], kind="stable").reset_index(drop=True)


def main() -> int:
    args = parse_args()
    try:
        in_path = Path(args.candidate_pool)
        if args.output:
            out_path = Path(args.output)
        else:
            out_path = in_path.with_name(in_path.stem.replace("hourly_future_candidates", "candidate_daily_summary") + ".csv")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        log(f"[1/3] Reading candidate pool in chunks: {in_path}")
        parts: List[pd.DataFrame] = []
        total_rows = 0
        for i, chunk in enumerate(read_chunks(in_path, args.chunksize), start=1):
            total_rows += len(chunk)
            if i == 1 or i % 5 == 0:
                log(f"      chunk {i}: cumulative {total_rows:,} hourly rows")
            parts.append(aggregate_chunk(chunk, args.cdh_base_temperature_c))
        if not parts:
            raise ValueError("No rows read from candidate pool")
        log("[2/3] Combining partial daily summaries ...")
        daily = combine_partials(parts)
        daily.to_csv(out_path, index=False)
        meta = {
            "input": str(in_path),
            "output": str(out_path),
            "hourly_rows_read": int(total_rows),
            "daily_rows": int(len(daily)),
            "cdh_base_temperature_c": args.cdh_base_temperature_c,
            "model_chain_count": int(daily["model_chain"].nunique()),
            "source_year_count": int(daily["year"].nunique()),
        }
        with open(out_path.with_suffix(out_path.suffix + ".meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)
        log("[3/3] Done.")
        log(f"Output: {out_path}")
        return 0
    except Exception as exc:
        log(f"[ERROR] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
