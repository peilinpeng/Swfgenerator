#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
04_parse_meteoswiss_hourly_v4.py

Purpose:
    Parse one or multiple MeteoSwiss hourly observation files into a standardized hourly table,
    preserving a few more EPW-relevant variables for a donor-free EPW workflow.

Key design choices:
    - raw MeteoSwiss timestamps are parsed as UTC
    - a second datetime view in local standard time (no DST) is created via a fixed offset
    - backward-compatible `datetime/year/month/day/hour/doy` fields are derived from
      local standard time so later selectors can operate in local-weather time
    - optional pressure, wind direction, diffuse horizontal radiation, and horizontal
      infrared radiation are preserved when present

Output core fields:
    station_id
    datetime_utc
    datetime_local_std
    datetime          # alias of datetime_local_std for downstream compatibility
    year month day hour doy  # derived from datetime_local_std
    tas hurs rsds sfcWind
    pres              # optional, NaN if not found
    windDir           # optional, NaN if not found
    dhi               # optional, diffuse horizontal radiation
    horizIR           # optional, horizontal infrared / incoming longwave radiation
"""

from __future__ import annotations

import argparse
import glob
import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

import numpy as np
import pandas as pd


def log(msg: str) -> None:
    print(msg, flush=True)


def normalize_name(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(s).strip().lower())


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


def write_metadata_json(
    output_path: Path,
    file_metadata: Dict[str, Any],
    parse_metadata: Dict[str, Any],
    summary_metadata: Dict[str, Any],
    validation_warnings: List[str],
) -> None:
    sidecar = output_path.with_suffix(output_path.suffix + ".meta.json")
    payload = {
        "file_metadata": file_metadata,
        "parse_metadata": parse_metadata,
        "summary_metadata": summary_metadata,
        "validation_warnings": validation_warnings,
    }
    with open(sidecar, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def detect_delimiter(sample_text: str) -> str:
    candidates = [",", ";", "\t", "|"]
    scores = {}
    for delim in candidates:
        lines = [ln for ln in sample_text.splitlines() if ln.strip()]
        if not lines:
            scores[delim] = -1
            continue
        counts = [ln.count(delim) for ln in lines[:20]]
        scores[delim] = sum(c > 0 for c in counts)
    return max(scores, key=scores.get)


def row_looks_like_header(fields: List[str]) -> bool:
    norm = [normalize_name(x) for x in fields]
    date_time_aliases = {"datetime", "timestamp", "date", "time", "datum", "heure", "zeit", "referencets"}
    variable_aliases = {
        "tas", "temperature", "temp", "airtemperature", "ta",
        "hurs", "rh", "relativehumidity", "humidity",
        "rsds", "globalradiation", "radiation", "solar", "ghi",
        "sfcwind", "windspeed", "wind", "ff",
        "pressure", "pres", "stationpressure", "winddir", "winddirection", "dd",
        "dhi", "diffuseradiation", "difhorrad", "horizir", "horizontalinfraredradiation", "longwaveincoming",
        "cloudcover", "cloudiness", "skycover", "totalcloudcover"
    }
    has_datetime = any(n in date_time_aliases for n in norm)
    has_variable = any(
        n in variable_aliases or n.startswith(("tre", "ure", "gre", "fkl", "fu3", "pre", "p0", "dkl", "dd", "ods", "oli", "nht", "nto"))
        for n in norm
    )
    non_numeric_labels = 0
    for raw in fields:
        txt = str(raw).strip()
        if not txt:
            continue
        try:
            float(txt)
        except Exception:
            non_numeric_labels += 1
    return has_datetime or (has_variable and non_numeric_labels >= 2)


def detect_header_row_and_delimiter(path: Path, max_lines: int = 120, encoding: str = "cp1252") -> Tuple[int, str]:
    with open(path, "r", encoding=encoding, errors="replace") as f:
        lines = [next(f, "") for _ in range(max_lines)]
    sample_text = "".join(lines)
    delim = detect_delimiter(sample_text)
    for i, line in enumerate(lines):
        if not line:
            continue
        fields = [x.strip() for x in line.rstrip("\n").split(delim)]
        if row_looks_like_header(fields):
            return i, delim
    for i, line in enumerate(lines):
        if line.strip():
            return i, delim
    raise ValueError("Could not detect a header row in the raw hourly observation file.")


ALIASES = {
    "datetime": {"datetime", "timestamp", "dateheure", "date_time", "time_stamp", "referencets"},
    "date": {"date", "datum"},
    "time": {"time", "hour", "heure", "uhrzeit"},
    "tas": {"tas", "temp", "temperature", "airtemperature", "ta", "tre200h0", "t2m", "temperature2m"},
    "hurs": {"hurs", "rh", "relativehumidity", "humidity", "ure200h0", "relhumidity"},
    "rsds": {"rsds", "globalradiation", "radiation", "solar", "solarradiation", "ghi", "gre000h0", "globalrad"},
    "sfcWind": {"sfcwind", "windspeed", "wind", "windspeedms", "fkl010h0", "ff", "fu3010h0", "fu3010z0"},
    "pres": {"pres", "pressure", "stationpressure", "p0", "prestas", "prestah0", "prestah1", "press"},
    "windDir": {"winddir", "winddirection", "dd", "dkl010h0", "winddirdeg"},
    "dhi": {"dhi", "diffuseradiation", "difhorrad", "ods000h0"},
    "horizIR": {"horizir", "horizontalinfraredradiation", "horizontalir", "longwaveincoming", "oli000h0", "longwavein"},
    "cloudcover": {"cloudcover", "cloudiness", "skycover", "totalcloudcover", "clt", "cloudamount", "n", "nht000h0", "nto000h0"},
}


def find_column(df: pd.DataFrame, target: str, explicit_name: Optional[str] = None) -> Optional[str]:
    if explicit_name:
        if explicit_name not in df.columns:
            raise ValueError(f"Requested column '{explicit_name}' not found in input file.")
        return explicit_name
    norm_map = {col: normalize_name(col) for col in df.columns}
    alias_set = ALIASES[target]
    for col, norm in norm_map.items():
        if norm in alias_set:
            return col
    for col, norm in norm_map.items():
        if target == "datetime" and ("datetime" in norm or "timestamp" in norm or "referencets" in norm):
            return col
        if target == "date" and norm == "date":
            return col
        if target == "time" and norm == "time":
            return col
        if target == "tas" and (norm.startswith("tre") or "temp" in norm or "temperature" in norm):
            return col
        if target == "hurs" and (norm.startswith("ure") or "humidity" in norm or norm == "rh"):
            return col
        if target == "rsds" and (norm.startswith("gre") or "radiation" in norm or norm == "ghi"):
            return col
        if target == "sfcWind" and (norm.startswith(("fkl", "fu3")) or "wind" in norm or norm == "ff"):
            return col
        if target == "pres" and (norm.startswith(("pre", "p0")) or "press" in norm):
            return col
        if target == "windDir" and (norm.startswith(("dkl", "dd")) or "winddir" in norm):
            return col
        if target == "dhi" and (norm.startswith("ods") or ("diffuse" in norm and "radiation" in norm)):
            return col
        if target == "horizIR" and (norm.startswith("oli") or ("longwave" in norm and "incoming" in norm) or "infrared" in norm):
            return col
        if target == "cloudcover" and (norm.startswith(("nht", "nto")) or "cloud" in norm or "skycover" in norm):
            return col
    return None


def parse_meteoswiss_datetime(series: pd.Series) -> pd.Series:
    raw = series.astype(str).str.strip()
    dt = pd.to_datetime(raw, format="%d.%m.%Y %H:%M", errors="coerce", utc=True)
    if dt.isna().mean() > 0:
        dt_fallback = pd.to_datetime(raw, errors="coerce", dayfirst=True, utc=True)
        dt = dt.fillna(dt_fallback)
    return dt


def build_datetime_series(df: pd.DataFrame, datetime_col: Optional[str], date_col: Optional[str], time_col: Optional[str]) -> pd.Series:
    if datetime_col:
        return parse_meteoswiss_datetime(df[datetime_col])
    if date_col and time_col:
        combined = df[date_col].astype(str).str.strip() + " " + df[time_col].astype(str).str.strip()
        return parse_meteoswiss_datetime(combined)
    raise ValueError("Could not build datetime. Provide either a datetime column or a date+time pair.")


def parse_hourly_file(
    input_path: Path,
    station_id: str,
    local_utc_offset_hours: int,
    datetime_col: Optional[str],
    date_col: Optional[str],
    time_col: Optional[str],
    tas_col: Optional[str],
    hurs_col: Optional[str],
    rsds_col: Optional[str],
    sfcwind_col: Optional[str],
    pres_col: Optional[str],
    winddir_col: Optional[str],
    dhi_col: Optional[str],
    horizir_col: Optional[str],
    cloudcover_col: Optional[str],
) -> Tuple[pd.DataFrame, Dict[str, Any], Dict[str, Any]]:
    header_row, delimiter = detect_header_row_and_delimiter(input_path, encoding="cp1252")
    df_raw = pd.read_csv(input_path, sep=delimiter, skiprows=header_row, header=0, encoding="cp1252", encoding_errors="replace")
    if df_raw.empty:
        raise ValueError("Parsed raw file is empty.")
    df_raw = df_raw.dropna(axis=1, how="all")

    detected_datetime_col = find_column(df_raw, "datetime", explicit_name=datetime_col)
    detected_date_col = find_column(df_raw, "date", explicit_name=date_col) if not detected_datetime_col else None
    detected_time_col = find_column(df_raw, "time", explicit_name=time_col) if not detected_datetime_col else None
    detected_tas_col = find_column(df_raw, "tas", explicit_name=tas_col)
    detected_hurs_col = find_column(df_raw, "hurs", explicit_name=hurs_col)
    detected_rsds_col = find_column(df_raw, "rsds", explicit_name=rsds_col)
    detected_sfcwind_col = find_column(df_raw, "sfcWind", explicit_name=sfcwind_col)
    detected_pres_col = find_column(df_raw, "pres", explicit_name=pres_col)
    detected_winddir_col = find_column(df_raw, "windDir", explicit_name=winddir_col)
    detected_dhi_col = find_column(df_raw, "dhi", explicit_name=dhi_col)
    detected_horizir_col = find_column(df_raw, "horizIR", explicit_name=horizir_col)
    detected_cloudcover_col = find_column(df_raw, "cloudcover", explicit_name=cloudcover_col)

    dt_utc = build_datetime_series(df_raw, detected_datetime_col, detected_date_col, detected_time_col)
    dt_local_std = dt_utc + pd.to_timedelta(local_utc_offset_hours, unit="h")

    df_std = pd.DataFrame(
        {
            "station_id": station_id.lower(),
            "datetime_utc": dt_utc,
            "datetime_local_std": dt_local_std.dt.tz_localize(None),
            "tas": pd.to_numeric(df_raw[detected_tas_col], errors="coerce") if detected_tas_col else np.nan,
            "hurs": pd.to_numeric(df_raw[detected_hurs_col], errors="coerce") if detected_hurs_col else np.nan,
            "rsds": pd.to_numeric(df_raw[detected_rsds_col], errors="coerce") if detected_rsds_col else np.nan,
            "sfcWind": pd.to_numeric(df_raw[detected_sfcwind_col], errors="coerce") if detected_sfcwind_col else np.nan,
            "pres": pd.to_numeric(df_raw[detected_pres_col], errors="coerce") if detected_pres_col else np.nan,
            "windDir": pd.to_numeric(df_raw[detected_winddir_col], errors="coerce") if detected_winddir_col else np.nan,
            "dhi": pd.to_numeric(df_raw[detected_dhi_col], errors="coerce") if detected_dhi_col else np.nan,
            "horizIR": pd.to_numeric(df_raw[detected_horizir_col], errors="coerce") if detected_horizir_col else np.nan,
            "cloudcover": pd.to_numeric(df_raw[detected_cloudcover_col], errors="coerce") if detected_cloudcover_col else np.nan,
        }
    )

    datetime_na_before_drop = int(df_std["datetime_utc"].isna().sum())
    df_std = df_std.dropna(subset=["datetime_utc"]).copy()
    df_std = df_std.sort_values("datetime_utc", kind="stable").reset_index(drop=True)

    # Backward-compatible downstream datetime fields use local standard time.
    df_std["datetime"] = df_std["datetime_local_std"]
    df_std["year"] = df_std["datetime_local_std"].dt.year.astype("int16")
    df_std["month"] = df_std["datetime_local_std"].dt.month.astype("int8")
    df_std["day"] = df_std["datetime_local_std"].dt.day.astype("int8")
    df_std["hour"] = df_std["datetime_local_std"].dt.hour.astype("int8")
    df_std["doy"] = df_std["datetime_local_std"].dt.dayofyear.astype("int16")

    # Physical bounds.
    df_std["hurs"] = df_std["hurs"].clip(lower=0, upper=100)
    df_std["rsds"] = df_std["rsds"].clip(lower=0)
    df_std["sfcWind"] = df_std["sfcWind"].clip(lower=0)
    if "windDir" in df_std.columns:
        df_std["windDir"] = df_std["windDir"] % 360
    if "dhi" in df_std.columns:
        df_std["dhi"] = df_std["dhi"].clip(lower=0)
    if "horizIR" in df_std.columns:
        df_std["horizIR"] = df_std["horizIR"].clip(lower=0)
    if "cloudcover" in df_std.columns:
        # Keep original scale for now. EPW completion will infer whether this is oktas, percent or 0-10.
        df_std["cloudcover"] = df_std["cloudcover"].clip(lower=0)

    file_metadata = {
        "source_file": str(input_path),
        "detected_delimiter": delimiter,
        "detected_header_row_zero_based": header_row,
        "detected_datetime_col": detected_datetime_col,
        "detected_date_col": detected_date_col,
        "detected_time_col": detected_time_col,
        "detected_tas_col": detected_tas_col,
        "detected_hurs_col": detected_hurs_col,
        "detected_rsds_col": detected_rsds_col,
        "detected_sfcwind_col": detected_sfcwind_col,
        "detected_pres_col": detected_pres_col,
        "detected_winddir_col": detected_winddir_col,
        "detected_dhi_col": detected_dhi_col,
        "detected_horizir_col": detected_horizir_col,
        "detected_cloudcover_col": detected_cloudcover_col,
        "raw_columns": list(df_raw.columns),
    }
    parse_metadata = {
        "station_id": station_id.lower(),
        "used_datetime_strategy": "datetime_col" if detected_datetime_col else "date_plus_time",
        "timezone_reference": "UTC",
        "hourly_timestamp_semantics": "end_of_interval",
        "local_time_policy": "local_standard_time_no_dst",
        "local_utc_offset_hours": local_utc_offset_hours,
        "datetime_na_rows_dropped": datetime_na_before_drop,
        "hurs_clipped_to_0_100": True,
        "rsds_clipped_to_nonnegative": True,
        "sfcwind_clipped_to_nonnegative": True,
        "dhi_clipped_to_nonnegative": True,
        "horizir_clipped_to_nonnegative": True,
    }
    return df_std, file_metadata, parse_metadata


def summarize_dataframe(df: pd.DataFrame) -> Dict[str, Any]:
    def nz(col: str):
        s = df[col].dropna()
        return (None if s.empty else float(s.min()), None if s.empty else float(s.max()))
    tas_min, tas_max = nz("tas")
    hurs_min, hurs_max = nz("hurs")
    rsds_min, rsds_max = nz("rsds")
    wind_min, wind_max = nz("sfcWind")
    pres_min, pres_max = nz("pres")
    wdir_min, wdir_max = nz("windDir")
    dhi_min, dhi_max = nz("dhi")
    hir_min, hir_max = nz("horizIR")
    cloud_min, cloud_max = nz("cloudcover")
    return {
        "row_count": int(len(df)),
        "datetime_utc_min": None if df.empty else str(df["datetime_utc"].min()),
        "datetime_utc_max": None if df.empty else str(df["datetime_utc"].max()),
        "datetime_local_std_min": None if df.empty else str(df["datetime_local_std"].min()),
        "datetime_local_std_max": None if df.empty else str(df["datetime_local_std"].max()),
        "year_min": None if df.empty else int(df["year"].min()),
        "year_max": None if df.empty else int(df["year"].max()),
        "na_fraction_tas": float(df["tas"].isna().mean()),
        "na_fraction_hurs": float(df["hurs"].isna().mean()),
        "na_fraction_rsds": float(df["rsds"].isna().mean()),
        "na_fraction_sfcWind": float(df["sfcWind"].isna().mean()),
        "na_fraction_pres": float(df["pres"].isna().mean()),
        "na_fraction_windDir": float(df["windDir"].isna().mean()),
        "na_fraction_dhi": float(df["dhi"].isna().mean()),
        "na_fraction_horizIR": float(df["horizIR"].isna().mean()),
        "na_fraction_cloudcover": float(df["cloudcover"].isna().mean()),
        "tas_min": tas_min, "tas_max": tas_max,
        "hurs_min": hurs_min, "hurs_max": hurs_max,
        "rsds_min": rsds_min, "rsds_max": rsds_max,
        "sfcWind_min": wind_min, "sfcWind_max": wind_max,
        "pres_min": pres_min, "pres_max": pres_max,
        "windDir_min": wdir_min, "windDir_max": wdir_max,
        "dhi_min": dhi_min, "dhi_max": dhi_max,
        "horizIR_min": hir_min, "horizIR_max": hir_max,
        "cloudcover_min": cloud_min, "cloudcover_max": cloud_max,
    }


def validate_dataframe(df: pd.DataFrame) -> List[str]:
    warnings: List[str] = []
    if df.empty:
        return ["Parsed dataframe is empty"]
    dup_count = int(df["datetime_utc"].duplicated().sum())
    if dup_count > 0:
        warnings.append(f"Found {dup_count} duplicate datetime_utc rows")
    if len(df) >= 2:
        diffs = df["datetime_utc"].diff().dropna()
        diff_hours = diffs.dt.total_seconds() / 3600.0
        non_hourly = diff_hours[diff_hours != 1.0]
        if len(non_hourly) > 0:
            warnings.append(f"Found {len(non_hourly)} non-1-hour gaps in datetime_utc sequence")
    return warnings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Parse one or multiple MeteoSwiss hourly observation files into a richer hourly template (v4).")
    parser.add_argument("--input", required=True, help="Path to raw hourly observation file, a directory containing CSVs, or a glob pattern")
    parser.add_argument("--station-id", default="sma", help="Station id to write into standardized output")
    parser.add_argument("--local-utc-offset-hours", type=int, default=1, help="Fixed UTC offset used to build local standard time (default: +1 for Switzerland)")
    parser.add_argument("--output-dir", default="./data_processed/hourly_obs", help="Directory for standardized output")
    parser.add_argument("--output-format", choices=["csv", "parquet"], default="parquet")
    parser.add_argument("--datetime-col", default=None)
    parser.add_argument("--date-col", default=None)
    parser.add_argument("--time-col", default=None)
    parser.add_argument("--tas-col", default=None)
    parser.add_argument("--hurs-col", default=None)
    parser.add_argument("--rsds-col", default=None)
    parser.add_argument("--sfcwind-col", default=None)
    parser.add_argument("--pres-col", default=None)
    parser.add_argument("--winddir-col", default=None)
    parser.add_argument("--dhi-col", default=None)
    parser.add_argument("--horizir-col", default=None)
    parser.add_argument("--cloudcover-col", default=None)
    return parser.parse_args()


def resolve_input_files(input_arg: str) -> List[Path]:
    raw = Path(input_arg)
    if raw.exists() and raw.is_file():
        return [raw]
    if raw.exists() and raw.is_dir():
        files = sorted([p for p in raw.glob("*.csv") if p.is_file()])
        if files:
            return files
        raise FileNotFoundError(f"Input directory contains no CSV files: {raw}")
    matches = sorted(Path(x) for x in glob.glob(input_arg))
    matches = [m for m in matches if m.is_file()]
    if matches:
        return matches
    raise FileNotFoundError(f"Input path/glob not found: {input_arg}")


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        input_files = resolve_input_files(args.input)
        log(f"[1/4] Parsing MeteoSwiss hourly file(s): {len(input_files)} file(s) ...")
        frames: List[pd.DataFrame] = []
        file_metadata_list: List[Dict[str, Any]] = []
        parse_metadata_list: List[Dict[str, Any]] = []
        for idx, input_path in enumerate(input_files, start=1):
            log(f"      [{idx}/{len(input_files)}] {input_path}")
            df_part, file_metadata, parse_metadata = parse_hourly_file(
                input_path=input_path,
                station_id=args.station_id,
                local_utc_offset_hours=args.local_utc_offset_hours,
                datetime_col=args.datetime_col,
                date_col=args.date_col,
                time_col=args.time_col,
                tas_col=args.tas_col,
                hurs_col=args.hurs_col,
                rsds_col=args.rsds_col,
                sfcwind_col=args.sfcwind_col,
                pres_col=args.pres_col,
                winddir_col=args.winddir_col,
                dhi_col=args.dhi_col,
                horizir_col=args.horizir_col,
                cloudcover_col=args.cloudcover_col,
            )
            frames.append(df_part)
            file_metadata_list.append(file_metadata)
            parse_metadata_list.append(parse_metadata)
        df = pd.concat(frames, ignore_index=True)
        before_dedupe = len(df)
        df = df.sort_values("datetime_utc", kind="stable").drop_duplicates(subset=["datetime_utc"], keep="last").reset_index(drop=True)
        duplicates_dropped = before_dedupe - len(df)
        file_metadata = {
            "source_inputs": [str(p) for p in input_files],
            "source_file_count": len(input_files),
            "per_file_metadata": file_metadata_list,
        }
        parse_metadata = {
            "station_id": args.station_id.lower(),
            "input_mode": "multi_file" if len(input_files) > 1 else "single_file",
            "duplicates_dropped_after_concat": int(duplicates_dropped),
            "per_file_parse_metadata": parse_metadata_list,
        }
        summary = summarize_dataframe(df)
        warnings = validate_dataframe(df)
        log("[2/4] Inspecting standardized hourly table ...")
        log(f"      Rows               : {summary['row_count']}")
        log(f"      UTC range          : {summary['datetime_utc_min']} .. {summary['datetime_utc_max']}")
        log(f"      Local-std range    : {summary['datetime_local_std_min']} .. {summary['datetime_local_std_max']}")
        log(f"      NA frac pres       : {summary['na_fraction_pres']:.6f}")
        log(f"      NA frac windDir    : {summary['na_fraction_windDir']:.6f}")
        log(f"      NA frac dhi        : {summary['na_fraction_dhi']:.6f}")
        log(f"      NA frac horizIR    : {summary['na_fraction_horizIR']:.6f}")
        log(f"      NA frac cloudcover : {summary['na_fraction_cloudcover']:.6f}")
        if warnings:
            log("      Validation warnings:")
            for w in warnings:
                log(f"        - {w}")
        else:
            log("      Validation warnings: none")
        base_name = f"hourly_obs_{args.station_id.lower()}_v4"
        output_path = output_dir / (base_name + (".parquet" if args.output_format == "parquet" else ".csv"))
        log("[3/4] Writing standardized output ...")
        written_path = save_dataframe(df, output_path, args.output_format)
        log(f"      Output path     : {written_path}")
        log("[4/4] Writing metadata sidecar ...")
        write_metadata_json(written_path, file_metadata, parse_metadata, summary, warnings)
        log("      Done.")
        return 0
    except Exception as e:
        log(f"[ERROR] {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
