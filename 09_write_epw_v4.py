#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
09_write_epw_v4.py

Purpose:
    Write a donor-free EPW from an EPW-completed hourly table produced by
    08_complete_epw_fields_v4.py.

Minimum completed fields:
    month, day, hour,
    dry_bulb_c, dew_point_c, rh_pct, pressure_pa,
    ghi_wm2, dni_wm2, dhi_wm2, horiz_ir_wm2,
    wind_dir_deg, wind_speed_ms,
    total_sky_cover_tenths, opaque_sky_cover_tenths
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

DATA_SOURCE_FLAG = "?9?9?9?9E0?9?9?9*9*9?9?9?9?9?9?9*9?9*9*9?9*9"
DATA_FIELD_COUNT = 35
COL_YEAR = 0
COL_MONTH = 1
COL_DAY = 2
COL_HOUR = 3
COL_MINUTE = 4
COL_SOURCE = 5
COL_DRY_BULB = 6
COL_DEW_POINT = 7
COL_REL_HUM = 8
COL_PRESSURE = 9
COL_EXTRATER_HORIZONTAL = 10
COL_EXTRATER_DIRECT = 11
COL_HORIZ_IR = 12
COL_GHI = 13
COL_DNI = 14
COL_DHI = 15
COL_GLOB_ILLUM = 16
COL_DIR_ILLUM = 17
COL_DIF_ILLUM = 18
COL_ZENITH_LUM = 19
COL_WIND_DIR = 20
COL_WIND_SPEED = 21
COL_TOTAL_SKY = 22
COL_OPAQUE_SKY = 23
COL_VISIBILITY = 24
COL_CEILING_HEIGHT = 25
COL_PRESENT_WEATHER_OBS = 26
COL_PRESENT_WEATHER_CODES = 27
COL_PRECIP_WATER = 28
COL_AOD = 29
COL_SNOW_DEPTH = 30
COL_DAYS_SINCE_SNOW = 31
COL_ALBEDO = 32
COL_LIQ_PRECIP_DEPTH = 33
COL_LIQ_PRECIP_RATE = 34


def log(msg: str) -> None:
    print(msg, flush=True)


def read_table(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path)


def load_station_metadata(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        meta = json.load(f)
    required = ["city", "state", "country", "source", "wmo", "latitude", "longitude", "timezone", "elevation"]
    missing = [k for k in required if k not in meta]
    if missing:
        raise ValueError(f"Station metadata file missing required keys: {missing}")
    return meta


def build_header(meta: Dict[str, Any], start_day_of_week: str = "Sunday") -> List[str]:
    location = (
        f"LOCATION,{meta['city']},{meta['state']},{meta['country']},"
        f"{meta['source']},{meta['wmo']},{float(meta['latitude']):.5f},"
        f"{float(meta['longitude']):.5f},{float(meta['timezone']):.1f},{float(meta['elevation']):.1f}"
    )
    return [
        location,
        "DESIGN CONDITIONS,0",
        "TYPICAL/EXTREME PERIODS,0",
        "GROUND TEMPERATURES,0",
        "HOLIDAYS/DAYLIGHT SAVINGS,No,0,0,0",
        f"COMMENTS 1,{meta.get('comments_1', 'Donor-free EPW generated from CH2025 + MeteoSwiss workflow v4')}",
        f"COMMENTS 2,{meta.get('comments_2', 'Solar decomposition and sky cover completed by v4 EPW completion layer')}",
        f"DATA PERIODS,1,1,Data,{start_day_of_week},1/ 1,12/31",
    ]


def epw_missing_row(year: int, month: int, day: int, hour_0_23: int) -> List[str]:
    row = [""] * DATA_FIELD_COUNT
    row[COL_YEAR] = str(year)
    row[COL_MONTH] = str(month)
    row[COL_DAY] = str(day)
    row[COL_HOUR] = str(int(hour_0_23) + 1)
    row[COL_MINUTE] = "0"
    row[COL_SOURCE] = DATA_SOURCE_FLAG
    row[COL_DRY_BULB] = "99.9"
    row[COL_DEW_POINT] = "99.9"
    row[COL_REL_HUM] = "999"
    row[COL_PRESSURE] = "999999"
    row[COL_EXTRATER_HORIZONTAL] = "9999"
    row[COL_EXTRATER_DIRECT] = "9999"
    row[COL_HORIZ_IR] = "9999"
    row[COL_GHI] = "9999"
    row[COL_DNI] = "9999"
    row[COL_DHI] = "9999"
    row[COL_GLOB_ILLUM] = "999999"
    row[COL_DIR_ILLUM] = "999999"
    row[COL_DIF_ILLUM] = "999999"
    row[COL_ZENITH_LUM] = "9999"
    row[COL_WIND_DIR] = "999"
    row[COL_WIND_SPEED] = "999"
    row[COL_TOTAL_SKY] = "99"
    row[COL_OPAQUE_SKY] = "99"
    row[COL_VISIBILITY] = "9999"
    row[COL_CEILING_HEIGHT] = "99999"
    row[COL_PRESENT_WEATHER_OBS] = "9"
    row[COL_PRESENT_WEATHER_CODES] = "999999999"
    row[COL_PRECIP_WATER] = "999"
    row[COL_AOD] = ".999"
    row[COL_SNOW_DEPTH] = "999"
    row[COL_DAYS_SINCE_SNOW] = "99"
    row[COL_ALBEDO] = "999"
    row[COL_LIQ_PRECIP_DEPTH] = "999"
    row[COL_LIQ_PRECIP_RATE] = "999"
    return row


def load_completed_hourly(path: Path) -> pd.DataFrame:
    df = read_table(path).copy()
    required = {
        "month", "day", "hour", "dry_bulb_c", "dew_point_c", "rh_pct", "pressure_pa",
        "ghi_wm2", "dni_wm2", "dhi_wm2", "horiz_ir_wm2", "wind_dir_deg", "wind_speed_ms",
        "total_sky_cover_tenths", "opaque_sky_cover_tenths",
    }
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Completed hourly table missing required columns: {sorted(missing)}")
    if len(df) != 8760:
        raise ValueError(f"Completed hourly table has {len(df)} rows; expected 8760")
    return df


def build_rows(hourly: pd.DataFrame, synthetic_year: int) -> List[List[str]]:
    rows = []
    for _, r in hourly.iterrows():
        month = int(r["month"])
        day = int(r["day"])
        hour = int(r["hour"])
        row = epw_missing_row(synthetic_year, month, day, hour)
        row[COL_DRY_BULB] = f"{float(r['dry_bulb_c']):.1f}"
        row[COL_DEW_POINT] = f"{float(r['dew_point_c']):.1f}"
        row[COL_REL_HUM] = f"{max(0,min(100,float(r['rh_pct']))):.0f}"
        row[COL_PRESSURE] = f"{float(r['pressure_pa']):.0f}"
        row[COL_HORIZ_IR] = f"{max(0,float(r['horiz_ir_wm2'])):.0f}"
        row[COL_GHI] = f"{max(0,float(r['ghi_wm2'])):.0f}"
        row[COL_DNI] = f"{max(0,float(r['dni_wm2'])):.0f}"
        row[COL_DHI] = f"{max(0,float(r['dhi_wm2'])):.0f}"
        row[COL_WIND_DIR] = f"{float(r['wind_dir_deg']) % 360:.0f}"
        row[COL_WIND_SPEED] = f"{max(0,float(r['wind_speed_ms'])):.1f}"
        row[COL_TOTAL_SKY] = f"{float(r['total_sky_cover_tenths']):.0f}"
        row[COL_OPAQUE_SKY] = f"{float(r['opaque_sky_cover_tenths']):.0f}"
        rows.append(row)
    return rows


def write_epw(path: Path, header: List[str], rows: List[List[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        for line in header:
            f.write(line + "\n")
        for row in rows:
            f.write(",".join(str(x) for x in row) + "\n")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Write donor-free EPW from completed hourly table v4.")
    p.add_argument("--completed-hourly", required=True)
    p.add_argument("--station-metadata", required=True)
    p.add_argument("--output-epw", required=True)
    p.add_argument("--synthetic-year", type=int, default=2001)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    try:
        completed_path = Path(args.completed_hourly)
        station_meta_path = Path(args.station_metadata)
        output_epw = Path(args.output_epw)
        hourly = load_completed_hourly(completed_path)
        meta = load_station_metadata(station_meta_path)
        header = build_header(meta)
        rows = build_rows(hourly, args.synthetic_year)
        write_epw(output_epw, header, rows)
        payload = {
            "inputs": {"completed_hourly": str(completed_path), "station_metadata": str(station_meta_path)},
            "summary_metadata": {"row_count": len(rows), "synthetic_year": int(args.synthetic_year), "output_epw": str(output_epw)},
            "writer_policy": "donor-free header from station metadata; weather rows from completed v4 hourly table",
        }
        with open(output_epw.with_suffix(output_epw.suffix + ".meta.json"), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        log(f"EPW written: {output_epw}")
        return 0
    except Exception as exc:
        log(f"[ERROR] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
