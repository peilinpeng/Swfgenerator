#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
02_parse_ch2025_sma.py

Purpose:
    Parse one CH2025 DAILY-LOCAL raw CSV file into a standardized long table.

Input example:
    cache/ch2025/sma/tas/ref91-20/ogd-climate-scenarios-ch2025_sma_tas_ref91-20.csv

Output columns:
    station_id
    station_name
    variable_id
    variable_name
    unit
    state
    calendar
    frequency
    model_chain
    date_generic
    climate_year
    month
    day
    doy
    value
"""

from __future__ import annotations

import argparse
import json
import re
from io import StringIO
from pathlib import Path
from typing import Dict, List, Tuple, Any

import pandas as pd


def log(msg: str) -> None:
    print(msg, flush=True)


def parse_filename(path: Path) -> Tuple[str, str, str]:
    """
    Extract station_id, variable_id, state from official filename.
    Example:
      ogd-climate-scenarios-ch2025_sma_tas_gwl2.0.csv
    """
    pattern = re.compile(
        r"^ogd-climate-scenarios-ch2025_([^_]+)_([^_]+)_([^.]+(?:\.[^.]+)?)\.csv$",
        re.IGNORECASE,
    )
    m = pattern.match(path.name)
    if not m:
        raise ValueError(f"Could not parse official filename: {path.name}")
    station_id, variable_id, state = m.groups()
    return station_id.lower(), variable_id, state.lower()


def split_metadata_and_header(lines: List[str]) -> Tuple[Dict[str, str], int]:
    """
    Metadata block is KEY;VALUE lines until the first blank line.
    The real table header starts on the next line after the blank line.
    """
    metadata: Dict[str, str] = {}
    blank_idx = None

    for i, raw in enumerate(lines):
        line = raw.rstrip("\n")
        if line.strip() == "":
            blank_idx = i
            break

        parts = line.split(";", 1)
        if len(parts) == 2:
            key, value = parts
            metadata[key.strip()] = value.strip()
        else:
            raise ValueError(f"Malformed metadata line at {i+1}: {line}")

    if blank_idx is None:
        raise ValueError("Could not find blank line separating metadata and table header.")

    header_idx = blank_idx + 1
    if header_idx >= len(lines):
        raise ValueError("No table header found after metadata block.")

    return metadata, header_idx


def build_calendar_fields(date_str: str) -> Tuple[int, int, int, int]:
    """
    Parse CH2025 generic 365_day calendar dates like '0001-01-01'.

    IMPORTANT:
    This is NOT a Gregorian calendar with leap years.
    CH2025 DAILY-LOCAL uses a 365_day calendar, so every year has 365 days.
    """
    year_s, month_s, day_s = date_str.split("-")
    climate_year = int(year_s)
    month = int(month_s)
    day = int(day_s)

    month_lengths = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]

    if month < 1 or month > 12:
        raise ValueError(f"Invalid month in CH2025 date: {date_str}")

    max_day = month_lengths[month - 1]
    if day < 1 or day > max_day:
        raise ValueError(f"Invalid day in CH2025 365_day calendar: {date_str}")

    doy = sum(month_lengths[: month - 1]) + day
    return climate_year, month, day, doy


def choose_output_path(input_path: Path, output_dir: Path, output_format: str) -> Path:
    station_id, variable_id, state = parse_filename(input_path)
    suffix = ".parquet" if output_format == "parquet" else ".csv"
    return output_dir / f"ch2025_daily_{station_id}_{variable_id}_{state}{suffix}"


def summarize_dataframe(df: pd.DataFrame) -> Dict[str, Any]:
    value_series = pd.to_numeric(df["value"], errors="coerce")
    non_na = value_series.dropna()

    return {
        "row_count": int(len(df)),
        "model_chain_count": int(df["model_chain"].nunique()),
        "climate_year_min": int(df["climate_year"].min()),
        "climate_year_max": int(df["climate_year"].max()),
        "doy_min": int(df["doy"].min()),
        "doy_max": int(df["doy"].max()),
        "na_fraction": float(value_series.isna().mean()),
        "value_min": None if non_na.empty else float(non_na.min()),
        "value_max": None if non_na.empty else float(non_na.max()),
    }


def validate_dataframe(df: pd.DataFrame) -> List[str]:
    warnings: List[str] = []

    variable_id = str(df["variable_id"].iloc[0])
    station_id = str(df["station_id"].iloc[0])
    state = str(df["state"].iloc[0])

    # Generic checks
    if df["calendar"].nunique() != 1 or str(df["calendar"].iloc[0]) != "365_day":
        warnings.append(f"{station_id}/{variable_id}/{state}: calendar is not strictly '365_day'")

    if int(df["doy"].min()) != 1 or int(df["doy"].max()) != 365:
        warnings.append(f"{station_id}/{variable_id}/{state}: DOY range is not 1..365")

    value = pd.to_numeric(df["value"], errors="coerce")
    non_na = value.dropna()

    if variable_id == "hurs":
        bad = non_na[(non_na < 0) | (non_na > 100)]
        if not bad.empty:
            warnings.append(
                f"{station_id}/{variable_id}/{state}: found {len(bad)} rows outside [0, 100]"
            )

    elif variable_id == "rsds":
        bad = non_na[non_na < 0]
        if not bad.empty:
            warnings.append(
                f"{station_id}/{variable_id}/{state}: found {len(bad)} negative radiation values"
            )

    elif variable_id == "sfcWind":
        bad = non_na[non_na < 0]
        if not bad.empty:
            warnings.append(
                f"{station_id}/{variable_id}/{state}: found {len(bad)} negative wind-speed values"
            )

    return warnings


def write_metadata_json(
    output_path: Path,
    metadata: Dict[str, str],
    parse_metadata: Dict[str, Any],
    summary_metadata: Dict[str, Any],
    validation_warnings: List[str],
) -> None:
    sidecar = output_path.with_suffix(output_path.suffix + ".meta.json")
    payload = {
        "file_metadata": metadata,
        "parse_metadata": parse_metadata,
        "summary_metadata": summary_metadata,
        "validation_warnings": validation_warnings,
    }
    with open(sidecar, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def parse_ch2025_file(input_path: Path) -> Tuple[pd.DataFrame, Dict[str, str]]:
    with open(input_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    metadata, header_idx = split_metadata_and_header(lines)

    # Read only the actual table, starting from the real table header
    table_text = "".join(lines[header_idx:])
    df_wide = pd.read_csv(
        StringIO(table_text),
        sep=";",
        na_values=["NA"],
        keep_default_na=True,
    )

    if "DATE" not in df_wide.columns:
        raise ValueError("Expected 'DATE' column not found in CH2025 table.")

    member_cols = [c for c in df_wide.columns if c != "DATE"]

    # wide -> long
    df_long = df_wide.melt(
        id_vars=["DATE"],
        value_vars=member_cols,
        var_name="model_chain",
        value_name="value",
    )

    # Calendar fields
    calendar_parts = df_long["DATE"].apply(build_calendar_fields)
    df_long["climate_year"] = calendar_parts.apply(lambda x: x[0])
    df_long["month"] = calendar_parts.apply(lambda x: x[1])
    df_long["day"] = calendar_parts.apply(lambda x: x[2])
    df_long["doy"] = calendar_parts.apply(lambda x: x[3])

    station_id, variable_id, state = parse_filename(input_path)

    station_name = metadata.get("STATION_NAME", "")
    variable_name = metadata.get("VARIABLE", "")
    unit = metadata.get("UNIT", "")
    calendar = metadata.get("CALENDAR", "")
    frequency = metadata.get("FREQUENCY", "")

    df_std = pd.DataFrame(
        {
            "station_id": station_id,
            "station_name": station_name,
            "variable_id": variable_id,
            "variable_name": variable_name,
            "unit": unit,
            "state": state,
            "calendar": calendar,
            "frequency": frequency,
            "model_chain": df_long["model_chain"],
            "date_generic": df_long["DATE"],
            "climate_year": df_long["climate_year"].astype("int16"),
            "month": df_long["month"].astype("int8"),
            "day": df_long["day"].astype("int8"),
            "doy": df_long["doy"].astype("int16"),
            "value": pd.to_numeric(df_long["value"], errors="coerce"),
        }
    )

    # Stable sort so repeated runs always write rows in the same order
    df_std = df_std.sort_values(
        by=["model_chain", "climate_year", "month", "day"],
        kind="stable",
    ).reset_index(drop=True)

    return df_std, metadata


def save_dataframe(df: pd.DataFrame, output_path: Path, output_format: str) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if output_format == "parquet":
        try:
            df.to_parquet(output_path, index=False)
            return output_path
        except Exception as e:
            log(f"[WARN] Failed to write parquet ({e}). Falling back to CSV.")
            fallback = output_path.with_suffix(".csv")
            df.to_csv(fallback, index=False)
            return fallback

    df.to_csv(output_path, index=False)
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Parse one CH2025 DAILY-LOCAL raw CSV into a standardized long table."
    )
    parser.add_argument("--input", required=True, help="Path to raw CH2025 CSV file")
    parser.add_argument(
        "--output-dir",
        default="./data_processed/ch2025_daily",
        help="Directory for parsed output",
    )
    parser.add_argument(
        "--output-format",
        choices=["csv", "parquet"],
        default="parquet",
        help="Preferred output format",
    )
    parser.add_argument(
        "--drop-na",
        action="store_true",
        help="Drop rows where value is missing",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    input_path = Path(args.input)
    output_dir = Path(args.output_dir)

    if not input_path.exists():
        log(f"[ERROR] Input file does not exist: {input_path}")
        return 1

    try:
        log("[1/4] Parsing raw CH2025 file ...")
        df, metadata = parse_ch2025_file(input_path)

        if args.drop_na:
            before = len(df)
            df = df.dropna(subset=["value"]).reset_index(drop=True)
            after = len(df)
            log(f"      Dropped NA rows: {before - after}")

        summary = summarize_dataframe(df)
        warnings = validate_dataframe(df)

        log("[2/4] Inspecting parsed table ...")
        log(f"      Rows         : {summary['row_count']}")
        log(f"      Columns      : {list(df.columns)}")
        log(f"      Station      : {df['station_id'].iloc[0]}")
        log(f"      Variable     : {df['variable_id'].iloc[0]}")
        log(f"      State        : {df['state'].iloc[0]}")
        log(f"      Model chains : {summary['model_chain_count']}")
        log(f"      Climate years: {summary['climate_year_min']}–{summary['climate_year_max']}")
        log(f"      DOY range    : {summary['doy_min']}–{summary['doy_max']}")
        log(f"      NA fraction  : {summary['na_fraction']:.6f}")
        log(f"      Value range  : {summary['value_min']} .. {summary['value_max']}")

        if warnings:
            log("      Validation warnings:")
            for w in warnings:
                log(f"        - {w}")
        else:
            log("      Validation warnings: none")

        output_path = choose_output_path(input_path, output_dir, args.output_format)

        log("[3/4] Writing standardized output ...")
        written_path = save_dataframe(df, output_path, args.output_format)
        log(f"      Output path  : {written_path}")

        log("[4/4] Writing metadata sidecar ...")
        write_metadata_json(
            written_path,
            metadata,
            {
                "input_file": str(input_path),
                "preferred_output_format": args.output_format,
                "na_rows_kept": (not args.drop_na),
            },
            summary,
            warnings,
        )
        log("      Done.")
        return 0

    except Exception as e:
        log(f"[ERROR] {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())