#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
10_compare_weather_files_v4.py

Purpose:
    Compare selected FRY/XMY hourly files or EPW-ready completed CSV files.
    Produces summary metrics useful for PPT/frontend charts.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd


def read_table(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path)


def longest_consecutive_true(values: pd.Series) -> int:
    best = 0
    cur = 0
    for v in values.astype(bool).tolist():
        if v:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return int(best)


def normalize_hourly(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "dry_bulb_c" in out.columns and "tas" not in out.columns:
        out["tas"] = out["dry_bulb_c"]
    if "rh_pct" in out.columns and "hurs" not in out.columns:
        out["hurs"] = out["rh_pct"]
    if "ghi_wm2" in out.columns and "rsds" not in out.columns:
        out["rsds"] = out["ghi_wm2"]
    required = {"month", "day", "hour", "tas", "hurs", "rsds"}
    missing = required - set(out.columns)
    if missing:
        raise ValueError(f"Weather table missing columns: {sorted(missing)}")
    return out


def summarize_file(label: str, path: Path, warm_months: List[int], cdh_base: float, hot_day_threshold: float, tropical_night_threshold: float) -> Dict[str, Any]:
    df = normalize_hourly(read_table(path))
    daily = df.groupby(["month", "day"], sort=False).agg(
        tas_daily_mean=("tas", "mean"),
        tmax_daily=("tas", "max"),
        tmin_daily=("tas", "min"),
        rsds_daily_sum=("rsds", "sum"),
        hurs_daily_mean=("hurs", "mean"),
    ).reset_index()
    warm_h = df[df["month"].isin(warm_months)]
    warm_d = daily[daily["month"].isin(warm_months)].copy()
    hot_days = warm_d["tmax_daily"] >= hot_day_threshold
    tropical_nights = warm_d["tmin_daily"] >= tropical_night_threshold
    return {
        "label": label,
        "path": str(path),
        "row_count": int(len(df)),
        "annual_mean_tas": float(df["tas"].mean()),
        "annual_max_tas": float(df["tas"].max()),
        "annual_min_tas": float(df["tas"].min()),
        "summer_mean_tas": float(warm_h["tas"].mean()),
        "summer_max_tas": float(warm_h["tas"].max()),
        "summer_cdh": float(np.maximum(pd.to_numeric(warm_h["tas"], errors="coerce") - cdh_base, 0).sum()),
        "hot_day_count": int(hot_days.sum()),
        "longest_hot_spell_days": longest_consecutive_true(hot_days),
        "tropical_night_count": int(tropical_nights.sum()),
        "longest_tropical_night_spell": longest_consecutive_true(tropical_nights),
        "annual_rsds_total": float(df["rsds"].sum()),
        "summer_rsds_total": float(warm_h["rsds"].sum()),
        "annual_mean_hurs": float(df["hurs"].mean()),
    }


def parse_labeled_file(text: str) -> Tuple[str, Path]:
    if "=" not in text:
        path = Path(text)
        return path.stem, path
    label, path = text.split("=", 1)
    return label.strip(), Path(path.strip())


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Compare FRY/XMY weather files.")
    p.add_argument("--files", nargs="+", required=True, help="label=path entries")
    p.add_argument("--output-dir", default="./data_processed/weather_file_comparison")
    p.add_argument("--output", default=None, help="Optional explicit output CSV path; useful for station/GWL-specific batch outputs")
    p.add_argument("--warm-month-start", type=int, default=6)
    p.add_argument("--warm-month-end", type=int, default=8)
    p.add_argument("--cdh-base-temperature-c", type=float, default=26.0)
    p.add_argument("--hot-day-threshold-c", type=float, default=30.0)
    p.add_argument("--tropical-night-threshold-c", type=float, default=20.0)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    warm_months = list(range(args.warm_month_start, args.warm_month_end + 1))
    rows = []
    for entry in args.files:
        label, path = parse_labeled_file(entry)
        rows.append(summarize_file(label, path, warm_months, args.cdh_base_temperature_c, args.hot_day_threshold_c, args.tropical_night_threshold_c))
    summary = pd.DataFrame(rows)
    out_csv = Path(args.output) if args.output else output_dir / "weather_file_comparison_summary_v4.csv"
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(out_csv, index=False)
    json_path = out_csv.with_suffix(".json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"files": args.files, "summary_csv": str(out_csv), "warm_months": warm_months}, f, ensure_ascii=False, indent=2)
    print(f"Comparison summary: {out_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
