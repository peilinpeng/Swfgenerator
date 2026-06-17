#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
04d_merge_cloudcover_to_hourly_obs_v4.py

Purpose:
    Interpolate observed cloud-cover records to the MeteoSwiss hourly weather
    backbone and create EPW-ready Total Sky Cover fields.

Input:
    --hourly-obs: standardized hourly observation table from 04_parse_meteoswiss_hourly_v4.py
    --cloudcover: standardized cloudcover table from 04c_parse_cloudcover_observations_v4.py

Output:
    hourly observation table with additional columns:
        cloudcover_interpolated
        cloudcover_unit_guess
        total_sky_cover_tenths
        opaque_sky_cover_tenths
        skycover_source
        skycover_interpolation_method

Design:
    Cloud cover is an auxiliary observation layer. It is not delta-changed.
    This script aligns low-frequency visual observations or hourly cloud-cover
    products to the hourly backbone. The final EPW completion layer then uses
    total_sky_cover_tenths directly.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

import numpy as np
import pandas as pd


def log(msg: str) -> None:
    print(msg, flush=True)


def read_table(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(path)
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path)


def save_table(df: pd.DataFrame, path: Path, output_format: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if output_format == "parquet":
        try:
            if path.suffix.lower() != ".parquet":
                path = path.with_suffix(".parquet")
            df.to_parquet(path, index=False)
            return path
        except Exception as e:
            log(f"[WARN] Could not write parquet ({e}); falling back to CSV.")
            path = path.with_suffix(".csv")
    if path.suffix.lower() != ".csv":
        path = path.with_suffix(".csv")
    df.to_csv(path, index=False)
    return path


def cloudcover_to_epw_tenths(value: Any) -> float:
    if value is None or pd.isna(value):
        return 99.0
    try:
        v = float(value)
    except Exception:
        return 99.0
    if v < 0:
        return 99.0
    # Range-based inference:
    #   0-8   -> oktas, converted to tenths.
    #   >8-10 -> already tenths.
    #   >10-100 -> percent, converted to tenths.
    # The 8-10 interval is inherently ambiguous and documented in metadata.
    if 0.0 <= v <= 8.0:
        out = v / 8.0 * 10.0
    elif 8.0 < v <= 10.0:
        out = v
    elif 10.0 < v <= 100.0:
        out = v / 10.0
    else:
        return 99.0
    return float(max(0.0, min(10.0, round(out))))


def build_hourly_cloud_series(cloud: pd.DataFrame, hourly_dt: pd.Series, method: str, max_gap_hours: float | None) -> pd.Series:
    if "datetime_local_std" in cloud.columns:
        cloud_dt = pd.to_datetime(cloud["datetime_local_std"], errors="coerce")
    elif "datetime" in cloud.columns:
        cloud_dt = pd.to_datetime(cloud["datetime"], errors="coerce")
    elif "datetime_utc" in cloud.columns:
        cloud_dt = pd.to_datetime(cloud["datetime_utc"], errors="coerce", utc=True).dt.tz_convert(None)
    else:
        raise ValueError("Cloudcover table has no datetime_local_std/datetime/datetime_utc column.")

    if "cloudcover_raw" not in cloud.columns:
        raise ValueError("Cloudcover table missing cloudcover_raw column.")

    c = pd.DataFrame({"dt": cloud_dt, "cloudcover_raw": pd.to_numeric(cloud["cloudcover_raw"], errors="coerce")})
    c = c.dropna(subset=["dt", "cloudcover_raw"]).sort_values("dt")
    if c.empty:
        raise ValueError("No valid cloudcover records after datetime/value parsing.")
    # Collapse duplicate timestamps before interpolation.
    c = c.groupby("dt", as_index=False)["cloudcover_raw"].mean()

    target_index = pd.to_datetime(hourly_dt, errors="coerce")
    if target_index.isna().any():
        raise ValueError("Hourly observation datetime contains invalid values; cannot align cloud cover.")
    target_index = pd.DatetimeIndex(target_index)

    source = c.set_index("dt")["cloudcover_raw"].sort_index()
    combined_index = source.index.union(target_index).sort_values()
    s = source.reindex(combined_index)

    if method == "linear":
        interp = s.interpolate(method="time", limit_direction="both")
    elif method == "nearest":
        interp = s.reindex(combined_index).interpolate(method="nearest", limit_direction="both")
    elif method == "ffill":
        interp = s.ffill().bfill()
    else:
        raise ValueError(f"Unknown interpolation method: {method}")

    out = interp.reindex(target_index)

    if max_gap_hours is not None:
        # Mask hourly targets that are too far from the nearest original observation.
        src_idx = pd.DatetimeIndex(source.index)
        nearest_dist_hours = []
        # Searchsorted is much faster than all-pairs distance for normal station series.
        src_ns = src_idx.view("int64")
        tgt_ns = target_index.view("int64")
        pos = np.searchsorted(src_ns, tgt_ns)
        for i, ns in enumerate(tgt_ns):
            dists = []
            if pos[i] < len(src_ns):
                dists.append(abs(src_ns[pos[i]] - ns))
            if pos[i] > 0:
                dists.append(abs(src_ns[pos[i] - 1] - ns))
            nearest = min(dists) if dists else np.inf
            nearest_dist_hours.append(nearest / 3.6e12)
        dist = np.array(nearest_dist_hours)
        out = out.mask(dist > float(max_gap_hours))

    return pd.Series(out.to_numpy(), index=hourly_dt.index)


def infer_unit(values: pd.Series) -> str:
    v = pd.to_numeric(values, errors="coerce").dropna()
    if v.empty:
        return "unknown"
    mx = float(v.max())
    if mx <= 8:
        return "oktas_0_8"
    if mx <= 10:
        return "tenths_0_10"
    if mx <= 100:
        return "percent_0_100"
    return "unknown"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Interpolate cloudcover observations to hourly obs and create EPW sky-cover fields.")
    p.add_argument("--hourly-obs", required=True)
    p.add_argument("--cloudcover", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--method", choices=["linear", "nearest", "ffill"], default="linear")
    p.add_argument("--max-gap-hours", type=float, default=None, help="Optional maximum allowed distance to nearest cloud observation; otherwise keep all interpolated hours.")
    p.add_argument("--opaque-policy", choices=["equal_total", "missing"], default="equal_total")
    p.add_argument("--output-format", choices=["csv", "parquet"], default="csv")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    try:
        hourly_path = Path(args.hourly_obs)
        cloud_path = Path(args.cloudcover)
        output = Path(args.output)
        log("[1/4] Reading hourly observations and cloud-cover records ...")
        hourly = read_table(hourly_path).copy()
        cloud = read_table(cloud_path).copy()
        dt_col = "datetime_local_std" if "datetime_local_std" in hourly.columns else "datetime"
        if dt_col not in hourly.columns:
            raise ValueError("Hourly observation table has no datetime_local_std or datetime column.")
        hourly_dt = pd.to_datetime(hourly[dt_col], errors="coerce")

        log("[2/4] Interpolating cloud cover onto hourly backbone ...")
        cloud_hourly = build_hourly_cloud_series(cloud, hourly_dt, args.method, args.max_gap_hours)
        unit_guess = infer_unit(cloud["cloudcover_raw"])
        hourly["cloudcover_interpolated"] = cloud_hourly
        hourly["cloudcover_unit_guess"] = unit_guess
        hourly["total_sky_cover_tenths"] = hourly["cloudcover_interpolated"].apply(cloudcover_to_epw_tenths)
        if args.opaque_policy == "equal_total":
            hourly["opaque_sky_cover_tenths"] = hourly["total_sky_cover_tenths"]
        else:
            hourly["opaque_sky_cover_tenths"] = 99.0
        source_tag = "meteoswiss_cloudcover_observation_interpolated"
        if "extraction_method" in cloud.columns and cloud["extraction_method"].astype(str).str.contains("satellite_cfc", case=False, na=False).any():
            source_tag = "meteoswiss_satellite_cfc_nearest_grid_interpolated"
        hourly["skycover_source"] = source_tag
        hourly["skycover_interpolation_method"] = args.method
        # Keep legacy cloudcover column as the raw interpolated observation value.
        hourly["cloudcover"] = hourly["cloudcover_interpolated"]

        used_frac = float((hourly["total_sky_cover_tenths"] != 99.0).mean())
        log("[3/4] Writing enriched hourly observation table ...")
        actual = save_table(hourly, output, args.output_format)
        meta: Dict[str, object] = {
            "inputs": {"hourly_obs": str(hourly_path), "cloudcover": str(cloud_path)},
            "summary_metadata": {
                "row_count": int(len(hourly)),
                "cloudcover_source_rows": int(len(cloud)),
                "cloudcover_unit_guess": unit_guess,
                "interpolation_method": args.method,
                "max_gap_hours": args.max_gap_hours,
                "skycover_used_fraction": used_frac,
                "total_sky_cover_missing_fraction": float((hourly["total_sky_cover_tenths"] == 99.0).mean()),
            },
            "policies": {
                "cloudcover_to_total_sky_cover": "0-8 oktas -> 0-10 tenths; >8-10 tenths retained; >10-100 percent -> tenths; invalid -> 99",
                "opaque_sky_cover_policy": args.opaque_policy,
                "delta_change": "cloud cover is not delta-changed; it is retained/interpolated as an auxiliary observation layer",
            },
        }
        with open(actual.with_suffix(actual.suffix + ".meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        log("[4/4] Done.")
        log(f"Output: {actual}")
        log(f"Sky cover available fraction: {used_frac:.3f}")
        return 0
    except Exception as exc:
        log(f"[ERROR] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
