#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
04c_parse_cloudcover_observations_v4.py

Purpose:
    Parse one or multiple MeteoSwiss cloud-cover observation CSV files into a
    standardized table that can be interpolated onto the hourly weather backbone.

Accepted inputs:
    - Meteorological visual observations from ch.meteoschweiz.ogd-obs.
    - Any CSV/table containing a datetime/date column and a numeric cloud-cover
      variable. The parser tries broad aliases and cloud-related parameter codes.

Output columns:
    station_id
    datetime_utc
    datetime_local_std
    datetime
    year month day hour doy
    cloudcover_raw
    cloudcover_unit_guess
    cloudcover_source_column

Unit convention is not forced here. Conversion to EPW Total Sky Cover tenths is
handled by 04d_merge_cloudcover_to_hourly_obs_v4.py.
"""
from __future__ import annotations

import argparse
import glob
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


def log(msg: str) -> None:
    print(msg, flush=True)


def normalize_name(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(s).strip().lower())


def detect_delimiter(sample_text: str) -> str:
    candidates = [",", ";", "\t", "|"]
    lines = [ln for ln in sample_text.splitlines() if ln.strip()]
    scores = {d: sum(ln.count(d) > 0 for ln in lines[:30]) for d in candidates}
    return max(scores, key=scores.get)


def row_looks_like_header(fields: List[str]) -> bool:
    norm = [normalize_name(x) for x in fields]
    date_hits = {"datetime", "timestamp", "date", "time", "datum", "heure", "zeit", "referencets", "timeyy", "timemm", "timedd", "timehh"}
    cloud_tokens = {"cloudcover", "cloudiness", "skycover", "totalcloudcover", "cloudamount", "clt", "n", "nto", "nht", "nh", "neb"}
    has_date = any(n in date_hits for n in norm)
    has_cloud = any((n in cloud_tokens) or ("cloud" in n) or n.startswith(("nto", "nht", "neb")) for n in norm)
    non_numeric = 0
    for raw in fields:
        try:
            float(str(raw).strip())
        except Exception:
            if str(raw).strip():
                non_numeric += 1
    return has_date or (has_cloud and non_numeric >= 2)


def detect_header_row_and_delimiter(path: Path, max_lines: int = 120) -> Tuple[int, str]:
    with open(path, "r", encoding="cp1252", errors="replace") as f:
        lines = [next(f, "") for _ in range(max_lines)]
    delim = detect_delimiter("".join(lines))
    for i, line in enumerate(lines):
        fields = [x.strip() for x in line.rstrip("\n").split(delim)]
        if row_looks_like_header(fields):
            return i, delim
    for i, line in enumerate(lines):
        if line.strip():
            return i, delim
    raise ValueError(f"Could not detect header in {path}")


ALIASES = {
    "datetime": {"datetime", "timestamp", "dateheure", "referencets"},
    "date": {"date", "datum", "time", "timeyyyymmdd", "day"},
    "time": {"hour", "heure", "zeit", "timehh", "hh"},
    "year": {"year", "timeyy", "yyyy"},
    "month": {"month", "timemm", "mm"},
    "day": {"day", "timedd", "dd"},
}


CLOUD_NAME_KEYWORDS = [
    "cloudcover", "cloudiness", "skycover", "totalcloudcover", "cloudamount", "cloudfraction", "cfc",
    "bewoelkung", "bewolkung", "bedeckung", "nuage", "nebulosite", "nuvolosita", "coperturanuvolosa",
]
CLOUD_CODE_PREFIXES = ("nto", "nht", "neb", "clt", "n")


def find_column(df: pd.DataFrame, target: str, explicit: Optional[str] = None) -> Optional[str]:
    if explicit:
        if explicit not in df.columns:
            raise ValueError(f"Requested column '{explicit}' not found.")
        return explicit
    norm_map = {c: normalize_name(c) for c in df.columns}
    if target in ALIASES:
        for c, n in norm_map.items():
            if n in ALIASES[target]:
                return c
        if target == "datetime":
            for c, n in norm_map.items():
                if "datetime" in n or "timestamp" in n or "referencets" in n:
                    return c
    return None


def score_cloud_column(name: str, series: pd.Series) -> int:
    n = normalize_name(name)
    score = 0
    if any(k in n for k in CLOUD_NAME_KEYWORDS):
        score += 50
    if n.startswith(CLOUD_CODE_PREFIXES):
        score += 30
    if n in {"n", "nto", "nht", "clt", "cfc"}:
        score += 40
    numeric = pd.to_numeric(series, errors="coerce")
    valid_frac = 1.0 - float(numeric.isna().mean()) if len(numeric) else 0.0
    if valid_frac > 0.5:
        score += 10
    if numeric.notna().any():
        vmax = float(numeric.max())
        vmin = float(numeric.min())
        if 0 <= vmin and vmax <= 110:
            score += 10
    # Avoid obvious non-cloud station/time columns.
    if n in {"station", "stationid", "timeyy", "timemm", "timedd", "timehh", "year", "month", "day", "hour"}:
        score -= 100
    return score


def find_cloud_column(df: pd.DataFrame, explicit: Optional[str] = None) -> str:
    if explicit:
        if explicit not in df.columns:
            raise ValueError(f"Requested cloudcover column '{explicit}' not found.")
        return explicit
    scored = [(score_cloud_column(c, df[c]), c) for c in df.columns]
    scored.sort(reverse=True)
    if not scored or scored[0][0] <= 0:
        raise ValueError(f"Could not identify a cloud-cover column. Available columns: {list(df.columns)}")
    return scored[0][1]


def parse_datetime_series(df: pd.DataFrame, datetime_col: Optional[str], date_col: Optional[str], time_col: Optional[str], local_offset: int) -> pd.Series:
    if datetime_col:
        raw = df[datetime_col].astype(str).str.strip()
        dt = pd.to_datetime(raw, errors="coerce", dayfirst=True, utc=True)
        return dt

    # Common MeteoSwiss split columns: time_yy/time_mm/time_dd and optionally time_hh.
    y_col = find_column(df, "year")
    m_col = find_column(df, "month")
    d_col = find_column(df, "day")
    h_col = time_col or find_column(df, "time")
    if y_col and m_col and d_col:
        year = pd.to_numeric(df[y_col], errors="coerce").astype("Int64")
        month = pd.to_numeric(df[m_col], errors="coerce").astype("Int64")
        day = pd.to_numeric(df[d_col], errors="coerce").astype("Int64")
        hour = pd.to_numeric(df[h_col], errors="coerce").fillna(12).astype(int) if h_col else pd.Series(12, index=df.index)
        # Interpret split visual-observation records as local standard time and convert to UTC.
        local = pd.to_datetime(dict(year=year, month=month, day=day, hour=hour), errors="coerce")
        return (local - pd.to_timedelta(local_offset, unit="h")).dt.tz_localize("UTC")

    if date_col:
        raw = df[date_col].astype(str).str.strip()
        if time_col:
            raw = raw + " " + df[time_col].astype(str).str.strip()
        dt_local = pd.to_datetime(raw, errors="coerce", dayfirst=True)
        # If only a date is present, set noon local time before interpolation.
        if time_col is None:
            dt_local = dt_local + pd.to_timedelta(12, unit="h")
        return (dt_local - pd.to_timedelta(local_offset, unit="h")).dt.tz_localize("UTC")

    raise ValueError("Could not build cloudcover datetime. Provide datetime/date columns or split time_yy/time_mm/time_dd columns.")


def guess_unit(values: pd.Series) -> str:
    v = pd.to_numeric(values, errors="coerce").dropna()
    if v.empty:
        return "unknown"
    vmax = float(v.max())
    if vmax <= 8.0:
        return "oktas_0_8"
    if vmax <= 10.0:
        return "tenths_0_10"
    if vmax <= 100.0:
        return "percent_0_100"
    return "unknown"


def resolve_input_files(input_text: str) -> List[Path]:
    p = Path(input_text)
    if p.is_dir():
        return sorted([x for x in p.glob("*.csv") if x.is_file()])
    matches = sorted(Path(x) for x in glob.glob(input_text))
    if matches:
        return matches
    if p.exists():
        return [p]
    raise FileNotFoundError(f"No cloudcover input matched: {input_text}")


def parse_one(path: Path, station_id: str, local_offset: int, datetime_col: Optional[str], date_col: Optional[str], time_col: Optional[str], cloudcover_col: Optional[str]) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    header, delim = detect_header_row_and_delimiter(path)
    raw = pd.read_csv(path, sep=delim, skiprows=header, header=0, encoding="cp1252", encoding_errors="replace")
    raw = raw.dropna(axis=1, how="all")
    dt_col = find_column(raw, "datetime", datetime_col)
    d_col = find_column(raw, "date", date_col)
    t_col = find_column(raw, "time", time_col)
    cloud_col = find_cloud_column(raw, cloudcover_col)
    dt_utc = parse_datetime_series(raw, dt_col, d_col, t_col, local_offset)
    values = pd.to_numeric(raw[cloud_col], errors="coerce")
    out = pd.DataFrame({
        "station_id": station_id.lower(),
        "datetime_utc": dt_utc,
        "cloudcover_raw": values,
    }).dropna(subset=["datetime_utc", "cloudcover_raw"]).copy()
    out["datetime_local_std"] = (out["datetime_utc"] + pd.to_timedelta(local_offset, unit="h")).dt.tz_convert(None)
    out["datetime"] = out["datetime_local_std"]
    out["year"] = out["datetime"].dt.year
    out["month"] = out["datetime"].dt.month
    out["day"] = out["datetime"].dt.day
    out["hour"] = out["datetime"].dt.hour
    out["doy"] = out["datetime"].dt.dayofyear
    out["cloudcover_unit_guess"] = guess_unit(out["cloudcover_raw"])
    out["cloudcover_source_column"] = cloud_col
    return out, {"input": str(path), "delimiter": delim, "header_row": header, "cloud_column": cloud_col, "rows": int(len(out)), "unit_guess": guess_unit(out["cloudcover_raw"])}


def save_dataframe(df: pd.DataFrame, output_path: Path, output_format: str) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_format == "parquet":
        try:
            df.to_parquet(output_path, index=False)
            return output_path
        except Exception as e:
            log(f"[WARN] Could not write parquet ({e}); falling back to CSV.")
            output_path = output_path.with_suffix(".csv")
    df.to_csv(output_path, index=False)
    return output_path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Parse cloud-cover observations to a standardized table.")
    p.add_argument("--input", required=True, help="CSV file, directory, or glob pattern from 04b fetch layer.")
    p.add_argument("--station-id", required=True)
    p.add_argument("--local-utc-offset-hours", type=int, default=1)
    p.add_argument("--datetime-col", default=None)
    p.add_argument("--date-col", default=None)
    p.add_argument("--time-col", default=None)
    p.add_argument("--cloudcover-col", default=None)
    p.add_argument("--output", default=None)
    p.add_argument("--output-format", choices=["csv", "parquet"], default="csv")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    try:
        files = resolve_input_files(args.input)
        if not files:
            raise ValueError(f"No cloudcover CSV files found under {args.input}")
        log(f"[1/4] Parsing cloud-cover observation file(s): {len(files)} file(s) ...")
        pieces: List[pd.DataFrame] = []
        meta_rows: List[Dict[str, Any]] = []
        for i, path in enumerate(files, start=1):
            log(f"      [{i}/{len(files)}] {path}")
            df, meta = parse_one(path, args.station_id, args.local_utc_offset_hours, args.datetime_col, args.date_col, args.time_col, args.cloudcover_col)
            pieces.append(df)
            meta_rows.append(meta)
        out = pd.concat(pieces, ignore_index=True) if pieces else pd.DataFrame()
        if out.empty:
            raise ValueError("No valid cloud-cover observations parsed.")
        out = out.sort_values("datetime_utc", kind="stable").drop_duplicates(subset=["datetime_utc"], keep="last").reset_index(drop=True)
        unit_guess = guess_unit(out["cloudcover_raw"])
        out["cloudcover_unit_guess"] = unit_guess
        log("[2/4] Inspecting standardized cloud-cover table ...")
        log(f"      Rows        : {len(out)}")
        log(f"      UTC range   : {out['datetime_utc'].min()} .. {out['datetime_utc'].max()}")
        log(f"      Unit guess  : {unit_guess}")
        output = Path(args.output) if args.output else Path(f"data_processed/cloudcover/cloudcover_obs_{args.station_id.lower()}_v4.csv")
        if args.output_format == "parquet" and output.suffix.lower() != ".parquet":
            output = output.with_suffix(".parquet")
        if args.output_format == "csv" and output.suffix.lower() != ".csv":
            output = output.with_suffix(".csv")
        log("[3/4] Writing standardized output ...")
        actual = save_dataframe(out, output, args.output_format)
        meta = {
            "input_files": meta_rows,
            "summary_metadata": {
                "station_id": args.station_id.lower(),
                "row_count": int(len(out)),
                "unit_guess": unit_guess,
                "datetime_min_utc": str(out["datetime_utc"].min()),
                "datetime_max_utc": str(out["datetime_utc"].max()),
            },
            "notes": {
                "next_step": f"python3 04d_merge_cloudcover_to_hourly_obs_v4.py --hourly-obs data_processed/hourly_obs/hourly_obs_{args.station_id.lower()}_v4.csv --cloudcover {actual} --output data_processed/hourly_obs/hourly_obs_{args.station_id.lower()}_v4_cloud.csv",
            },
        }
        with open(actual.with_suffix(actual.suffix + ".meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        log("[4/4] Done.")
        log(f"Output: {actual}")
        return 0
    except Exception as exc:
        log(f"[ERROR] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
