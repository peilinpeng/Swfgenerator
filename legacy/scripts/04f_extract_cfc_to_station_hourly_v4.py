#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
04f_extract_cfc_to_station_hourly_v4.py

Extract station-nearest Cloud Fractional Cover (CFC) from MeteoSwiss satellite
NetCDF files and convert it to the standardized cloudcover table expected by
04d_merge_cloudcover_to_hourly_obs_v4.py.

Requires optional dependency for NetCDF:
    python3 -m pip install xarray netCDF4

Output columns:
    datetime_local_std, datetime_utc, cloudcover_raw, cloudcover_unit_guess,
    cloudcover_source_column, source_file, extraction_method

CFC value convention is inferred per file:
    0-1   -> fraction, converted to percent
    0-10  -> tenths, retained as tenths-like raw value
    0-100 -> percent
The downstream 04d script converts cloudcover_raw to EPW total_sky_cover_tenths.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import numpy as np
import pandas as pd


def log(msg: str) -> None:
    print(msg, flush=True)


def load_json(path: str | None) -> Dict[str, Any]:
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def station_lat_lon(args: argparse.Namespace) -> Tuple[float, float]:
    if args.latitude is not None and args.longitude is not None:
        return float(args.latitude), float(args.longitude)
    meta = load_json(args.station_metadata)
    for lat_key in ["latitude", "lat", "station_latitude"]:
        for lon_key in ["longitude", "lon", "station_longitude"]:
            if lat_key in meta and lon_key in meta:
                return float(meta[lat_key]), float(meta[lon_key])
    raise ValueError("Provide --latitude/--longitude or --station-metadata with latitude/longitude.")


def files_from_manifest_or_dir(manifest: str | None, input_dir: str | None) -> List[Path]:
    files: List[Path] = []
    if manifest and Path(manifest).exists():
        data = json.loads(Path(manifest).read_text(encoding="utf-8"))
        for p in data.get("downloaded_files", []):
            if Path(p).exists():
                files.append(Path(p))
        for a in data.get("assets", []):
            lp = a.get("local_path")
            if lp and Path(lp).exists():
                files.append(Path(lp))
    if input_dir and Path(input_dir).exists():
        files.extend(sorted(Path(input_dir).rglob("*.nc")))
    # de-duplicate
    out = []
    seen = set()
    for f in files:
        if str(f) not in seen:
            seen.add(str(f)); out.append(f)
    return out


def find_var(ds: Any) -> str:
    candidates = []
    for name in list(ds.data_vars):
        lname = name.lower()
        attrs = " ".join(str(ds[name].attrs.get(k, "")) for k in ["standard_name", "long_name", "description", "units"]).lower()
        score = 0
        if lname == "cfc": score += 100
        if "cfc" in lname: score += 60
        if "cloud" in lname and ("fraction" in lname or "cover" in lname): score += 50
        if "cloud fractional" in attrs or "fraction of the sky" in attrs: score += 80
        if score > 0:
            candidates.append((score, name))
    if not candidates:
        raise ValueError(f"Could not identify CFC variable. Available variables: {list(ds.data_vars)}")
    return sorted(candidates, reverse=True)[0][1]


def find_coord(ds: Any, names: List[str]) -> str | None:
    all_names = list(ds.coords) + list(ds.variables)
    for n in all_names:
        if n.lower() in names:
            return n
    for n in all_names:
        ln = n.lower()
        if any(key in ln for key in names):
            return n
    return None


def select_nearest(da: Any, lat: float, lon: float) -> Any:
    lat_name = find_coord(da.to_dataset(name="v"), ["lat", "latitude", "y"])
    lon_name = find_coord(da.to_dataset(name="v"), ["lon", "longitude", "x"])
    if lat_name is None or lon_name is None:
        raise ValueError("Could not identify latitude/longitude coordinates in CFC file.")
    lat_coord = da[lat_name]
    lon_coord = da[lon_name]
    # 1D regular lat/lon grid
    if lat_coord.ndim == 1 and lon_coord.ndim == 1:
        return da.sel({lat_name: lat, lon_name: lon}, method="nearest")
    # 2D lat/lon grid: compute nearest cell, then index its dims.
    lat_vals = np.asarray(lat_coord.values, dtype=float)
    lon_vals = np.asarray(lon_coord.values, dtype=float)
    dist = (lat_vals - lat) ** 2 + (lon_vals - lon) ** 2
    idx = np.unravel_index(np.nanargmin(dist), dist.shape)
    indexers = {}
    for dim, i in zip(lat_coord.dims, idx):
        indexers[dim] = int(i)
    return da.isel(indexers)


def infer_time_name(da: Any) -> str | None:
    for name in list(da.coords) + list(da.dims):
        lname = str(name).lower()
        if lname in {"time", "datetime", "date"} or "time" in lname:
            return str(name)
    return None


def normalize_cfc_values(values: pd.Series) -> Tuple[pd.Series, str]:
    v = pd.to_numeric(values, errors="coerce")
    finite = v.dropna()
    if finite.empty:
        return v, "unknown"
    mn, mx = float(finite.min()), float(finite.max())
    if mn >= 0 and mx <= 1.05:
        return v * 100.0, "fraction_0_1_to_percent"
    if mn >= 0 and mx <= 10.5:
        # keep as tenths-like; downstream can convert 0-10 directly.
        return v, "tenths_0_10"
    if mn >= 0 and mx <= 105:
        return v.clip(0, 100), "percent_0_100"
    return v, "unknown"


def extract_file(path: Path, lat: float, lon: float) -> pd.DataFrame:
    try:
        import xarray as xr  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("Missing optional dependency xarray/netCDF4. Run: python3 -m pip install xarray netCDF4") from exc
    ds = xr.open_dataset(path)
    var = find_var(ds)
    da = ds[var]
    point = select_nearest(da, lat, lon)
    time_name = infer_time_name(point)
    if time_name is None:
        raise ValueError(f"No time coordinate found in {path}")
    # Collapse any remaining non-time dimensions by selecting first valid point.
    for dim in list(point.dims):
        if dim != time_name:
            point = point.isel({dim: 0})
    df = point.to_dataframe(name="cloudcover_value").reset_index()
    df["datetime_utc"] = pd.to_datetime(df[time_name], errors="coerce", utc=True)
    df = df.dropna(subset=["datetime_utc"])
    vals, unit_guess = normalize_cfc_values(df["cloudcover_value"])
    df["cloudcover_raw"] = vals
    df["cloudcover_unit_guess"] = unit_guess
    df["cloudcover_source_column"] = var
    df["source_file"] = str(path)
    df["extraction_method"] = "nearest_grid_cell_satellite_cfc"
    # The existing MeteoSwiss hourly backbone uses local standard time; CFC is UTC.
    # For EPW-style no-DST local standard time in Switzerland, UTC+1 is used.
    df["datetime_local_std"] = (df["datetime_utc"].dt.tz_convert(None) + pd.Timedelta(hours=1))
    return df[["datetime_local_std", "datetime_utc", "cloudcover_raw", "cloudcover_unit_guess", "cloudcover_source_column", "source_file", "extraction_method"]]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Extract station-nearest hourly CFC from MeteoSwiss satellite NetCDF files.")
    p.add_argument("--manifest", default=None)
    p.add_argument("--input-dir", default=None)
    p.add_argument("--station-metadata", default=None)
    p.add_argument("--latitude", type=float, default=None)
    p.add_argument("--longitude", type=float, default=None)
    p.add_argument("--hourly-obs", default=None, help="Optional hourly backbone used to limit output time range.")
    p.add_argument("--output", required=True)
    p.add_argument("--output-format", choices=["csv", "parquet"], default="csv")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    try:
        lat, lon = station_lat_lon(args)
        files = files_from_manifest_or_dir(args.manifest, args.input_dir)
        if not files:
            raise FileNotFoundError("No CFC NetCDF files found in manifest/input directory.")
        log(f"[1/4] Extracting nearest CFC grid cell for lat={lat:.5f}, lon={lon:.5f} from {len(files)} files ...")
        parts = []
        for i, f in enumerate(files, 1):
            log(f"      [{i}/{len(files)}] {f}")
            try:
                parts.append(extract_file(f, lat, lon))
            except Exception as exc:
                log(f"      [WARN] Skipping {f}: {exc}")
        if not parts:
            raise RuntimeError("No CFC file could be extracted successfully.")
        out = pd.concat(parts, ignore_index=True).drop_duplicates(subset=["datetime_local_std"]).sort_values("datetime_local_std")
        if args.hourly_obs and Path(args.hourly_obs).exists():
            h = pd.read_csv(args.hourly_obs, usecols=lambda c: c in {"datetime_local_std", "datetime"})
            col = "datetime_local_std" if "datetime_local_std" in h.columns else "datetime"
            dt = pd.to_datetime(h[col], errors="coerce")
            if dt.notna().any():
                out = out[(pd.to_datetime(out["datetime_local_std"]) >= dt.min()) & (pd.to_datetime(out["datetime_local_std"]) <= dt.max())]
        log("[2/4] Writing standardized cloud-cover table ...")
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        if args.output_format == "parquet":
            try:
                if path.suffix.lower() != ".parquet": path = path.with_suffix(".parquet")
                out.to_parquet(path, index=False)
            except Exception as exc:
                log(f"[WARN] parquet failed ({exc}); writing CSV")
                path = path.with_suffix(".csv")
                out.to_csv(path, index=False)
        else:
            if path.suffix.lower() != ".csv": path = path.with_suffix(".csv")
            out.to_csv(path, index=False)
        meta = {
            "source": "MeteoSwiss satellite-derived Cloud Fractional Cover (CFC)",
            "station_latitude": lat,
            "station_longitude": lon,
            "file_count": len(files),
            "rows": int(len(out)),
            "unit_guesses": sorted(out["cloudcover_unit_guess"].dropna().astype(str).unique().tolist()),
            "output": str(path),
        }
        path.with_suffix(path.suffix + ".meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        log("[3/4] Done.")
        log(f"Output: {path}")
        log(f"Rows  : {len(out)}")
        return 0
    except Exception as exc:
        log(f"[ERROR] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
