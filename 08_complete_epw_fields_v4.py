#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
08_complete_epw_fields_v4.py

Purpose:
    Complete EPW-required meteorological fields for a selected FRY/XMY hourly file.

Inputs:
    Selected hourly CSV from FRY/XMY selection layer. Minimum columns:
        month, day, hour, tas, hurs, rsds, sfcWind
    Optional columns:
        pres, windDir, horizIR, cloudcover, dhi_obs,
        total_sky_cover_tenths, opaque_sky_cover_tenths

Outputs:
    A completed hourly CSV with EPW-ready core fields:
        dry_bulb_c, dew_point_c, rh_pct, pressure_pa,
        ghi_wm2, dhi_wm2, dni_wm2, horiz_ir_wm2,
        wind_dir_deg, wind_speed_ms, total_sky_cover_tenths,
        opaque_sky_cover_tenths

Method notes:
    - GHI is taken from rsds.
    - DHI/DNI are computed using a Boland-Ridley-style logistic diffuse-fraction
      model when no observed DHI exists. The coefficients are configurable.
    - Total Sky Cover first uses pre-interpolated total_sky_cover_tenths when
      available. Otherwise it is derived from retained MeteoSwiss cloud cover.
      Opaque Sky Cover uses a precomputed column when available; otherwise it is
      either equal to Total Sky Cover or EPW missing value 99.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Dict, Tuple

import numpy as np
import pandas as pd

from pipeline_utils import log, read_table


def load_station_metadata(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        meta = json.load(f)
    required = ["latitude", "longitude", "timezone", "elevation"]
    missing = [k for k in required if k not in meta]
    if missing:
        raise ValueError(f"Station metadata missing keys: {missing}")
    return meta


def compute_dew_point_c(tas_c: float, rh_pct: float) -> float:
    rh_pct = max(0.1, min(100.0, float(rh_pct)))
    tas_c = float(tas_c)
    a = 17.625
    b = 243.04
    gamma = math.log(rh_pct / 100.0) + (a * tas_c) / (b + tas_c)
    return (b * gamma) / (a - gamma)


def pressure_from_altitude_pa(elevation_m: float) -> float:
    # Standard atmosphere approximation.
    return 101325.0 * (1.0 - 2.25577e-5 * float(elevation_m)) ** 5.25588


def normalize_pressure_pa(value: Any, fallback_pa: float) -> float:
    if value is None or pd.isna(value):
        return float(fallback_pa)
    p = float(value)
    # Many station files store pressure in hPa; EPW needs Pa.
    if 800.0 <= p <= 1200.0:
        p *= 100.0
    elif 80.0 <= p <= 120.0:
        p *= 1000.0
    if not (70000.0 <= p <= 110000.0):
        return float(fallback_pa)
    return p


def estimate_horizontal_ir_from_dewpoint(dry_bulb_c: float, dew_point_c: float) -> float:
    t_k = dry_bulb_c + 273.15
    td_k = max(150.0, dew_point_c + 273.15)
    emissivity = 0.787 + 0.764 * math.log(td_k / 273.0)
    emissivity = max(0.5, min(1.2, emissivity))
    sigma = 5.6697e-8
    return max(0.0, emissivity * sigma * (t_k ** 4))


def solar_position_simple(dt_local_std: pd.Timestamp, latitude_deg: float, longitude_deg: float, timezone_hours: float) -> Tuple[float, float, float]:
    n = int(dt_local_std.dayofyear)
    hour_decimal = dt_local_std.hour + dt_local_std.minute / 60.0
    b = math.radians((360.0 / 365.0) * (n - 81))
    eot = 9.87 * math.sin(2 * b) - 7.53 * math.cos(b) - 1.5 * math.sin(b)
    lstm = 15.0 * float(timezone_hours)
    time_correction_min = 4.0 * (float(longitude_deg) - lstm) + eot
    local_solar_time = hour_decimal + time_correction_min / 60.0
    hra = math.radians(15.0 * (local_solar_time - 12.0))
    decl = math.radians(23.45 * math.sin(math.radians(360.0 * (284 + n) / 365.0)))
    lat = math.radians(float(latitude_deg))
    cos_zen = math.sin(lat) * math.sin(decl) + math.cos(lat) * math.cos(decl) * math.cos(hra)
    cos_zen = max(-1.0, min(1.0, cos_zen))
    zen = math.degrees(math.acos(cos_zen))
    solar_altitude = 90.0 - zen
    return zen, max(0.0, cos_zen), solar_altitude


def extraterrestrial_normal_irradiance(dayofyear: int) -> float:
    return 1367.0 * (1.0 + 0.033 * math.cos(2 * math.pi * int(dayofyear) / 365.0))


def boland_ridley_style_dhi(ghi: float, cos_zen: float, doy: int, a: float, b: float) -> float:
    """Simple logistic diffuse-fraction model using clearness index.

    This intentionally implements a compact Boland/Ridley-style logistic form.
    Coefficients are CLI-configurable because exact calibrated coefficients can
    vary by implementation and may be updated after literature/code verification.
    """
    ghi = max(0.0, float(ghi))
    if ghi <= 0.0 or cos_zen <= 0.0:
        return 0.0
    i0h = extraterrestrial_normal_irradiance(doy) * cos_zen
    kt = ghi / max(i0h, 1.0)
    kt = max(0.0, min(1.4, kt))
    kd = 1.0 / (1.0 + math.exp(a * (kt - b)))
    kd = max(0.05, min(1.0, kd))
    return max(0.0, min(ghi, kd * ghi))


def sanitize_radiation(ghi: float, dhi: float, zen_deg: float, cos_zen: float, dni_cap: float) -> Tuple[float, float, float]:
    ghi = max(0.0, float(ghi))
    dhi = max(0.0, float(dhi))
    if cos_zen <= 0.0 or zen_deg >= 87.0 or ghi <= 0.0:
        return ghi, min(dhi, ghi), 0.0
    dhi = min(dhi, ghi)
    dni = max(0.0, (ghi - dhi) / max(cos_zen, 1e-6))
    return ghi, dhi, min(dni, dni_cap)


def cloudcover_to_epw_tenths(value: Any) -> float:
    if value is None or pd.isna(value):
        return 99.0
    v = float(value)
    if v < 0:
        return 99.0
    # MeteoSwiss/Wehrli-style observations may be oktas 0-8, percent 0-100,
    # or already EPW-like tenths 0-10. This range-based inference is necessarily
    # ambiguous for values around 8-10. We treat values <= 8 as oktas; values > 8
    # and <= 10 as already tenths. This is documented in the metadata as a proxy.
    if 0.0 <= v <= 8.0:
        out = v / 8.0 * 10.0
    elif 8.0 < v <= 10.0:
        out = v
    elif 10.0 < v <= 100.0:
        out = v / 10.0
    else:
        return 99.0
    return float(max(0.0, min(10.0, round(out))))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Complete EPW-ready fields for selected FRY/XMY hourly file.")
    p.add_argument("--hourly-table", required=True)
    p.add_argument("--station-metadata", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--synthetic-year", type=int, default=2001)
    p.add_argument("--fallback-pressure-pa", type=float, default=None)
    p.add_argument("--fallback-wind-dir-deg", type=float, default=0.0)
    p.add_argument("--opaque-sky-cover-policy", choices=["equal_total", "missing"], default="equal_total")
    p.add_argument("--br-a", type=float, default=8.0, help="Logistic slope coefficient for simple Boland-Ridley-style model")
    p.add_argument("--br-b", type=float, default=0.586, help="Logistic midpoint coefficient for simple Boland-Ridley-style model")
    p.add_argument("--dni-cap", type=float, default=1200.0)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    try:
        hourly_path = Path(args.hourly_table)
        meta_path = Path(args.station_metadata)
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        hourly = read_table(hourly_path).copy()
        required = {"month", "day", "hour", "tas", "hurs", "rsds", "sfcWind"}
        missing = required - set(hourly.columns)
        if missing:
            raise ValueError(f"Hourly table missing required columns: {sorted(missing)}")
        if len(hourly) != 8760:
            raise ValueError(f"Hourly table has {len(hourly)} rows; expected 8760")
        meta = load_station_metadata(meta_path)
        lat = float(meta["latitude"])
        lon = float(meta["longitude"])
        tz = float(meta["timezone"])
        fallback_pressure = float(args.fallback_pressure_pa) if args.fallback_pressure_pa is not None else pressure_from_altitude_pa(float(meta.get("elevation", 0)))
        rows = []
        source_counts = {"dhi_from_observed": 0, "dhi_from_boland_ridley_style": 0, "horizIR_from_observed": 0, "horizIR_estimated": 0, "cloudcover_used": 0, "cloudcover_missing": 0}
        has_dhi_obs = "dhi_obs" in hourly.columns
        has_dhi = "dhi" in hourly.columns
        has_pres = "pres" in hourly.columns
        has_wind_dir = "windDir" in hourly.columns
        has_horiz_ir = "horizIR" in hourly.columns
        has_total_sky = "total_sky_cover_tenths" in hourly.columns
        has_opaque_sky = "opaque_sky_cover_tenths" in hourly.columns
        has_cloudcover = "cloudcover" in hourly.columns
        for rec in hourly.itertuples(index=False):
            r = rec._asdict()
            month = int(r["month"])
            day = int(r["day"])
            hour = int(r["hour"])
            dt = pd.Timestamp(year=int(args.synthetic_year), month=month, day=day, hour=hour)
            tas = float(r["tas"])
            rh = max(0.0, min(100.0, float(r["hurs"])))
            ghi = max(0.0, float(r["rsds"]))
            dew = compute_dew_point_c(tas, rh)
            zen, cos_zen, solar_alt = solar_position_simple(dt, lat, lon, tz)
            # Prefer observed DHI if passed through, otherwise decompose GHI.
            if has_dhi_obs and pd.notna(r.get("dhi_obs")):
                dhi = max(0.0, float(r["dhi_obs"]))
                source_counts["dhi_from_observed"] += 1
            elif has_dhi and pd.notna(r.get("dhi")):
                dhi = max(0.0, float(r["dhi"]))
                source_counts["dhi_from_observed"] += 1
            else:
                dhi = boland_ridley_style_dhi(ghi, cos_zen, dt.dayofyear, args.br_a, args.br_b)
                source_counts["dhi_from_boland_ridley_style"] += 1
            ghi, dhi, dni = sanitize_radiation(ghi, dhi, zen, cos_zen, args.dni_cap)
            if has_pres and pd.notna(r.get("pres")):
                pressure = normalize_pressure_pa(r["pres"], fallback_pressure)
            else:
                pressure = fallback_pressure
            if has_wind_dir and pd.notna(r.get("windDir")):
                wind_dir = float(r["windDir"]) % 360.0
            else:
                wind_dir = float(args.fallback_wind_dir_deg)
            if has_horiz_ir and pd.notna(r.get("horizIR")):
                horiz_ir = max(0.0, float(r["horizIR"]))
                source_counts["horizIR_from_observed"] += 1
            else:
                horiz_ir = estimate_horizontal_ir_from_dewpoint(tas, dew)
                source_counts["horizIR_estimated"] += 1
            # Prefer the explicit pre-interpolated sky-cover field produced by
            # 04d_merge_cloudcover_to_hourly_obs_v4.py. Fall back to converting
            # a raw/interpolated cloudcover column if present.
            if has_total_sky and pd.notna(r.get("total_sky_cover_tenths")):
                total_sky = float(r.get("total_sky_cover_tenths"))
                if not (0.0 <= total_sky <= 10.0):
                    total_sky = 99.0
            else:
                total_sky = cloudcover_to_epw_tenths(r.get("cloudcover") if has_cloudcover else np.nan)
            if total_sky == 99.0:
                source_counts["cloudcover_missing"] += 1
            else:
                source_counts["cloudcover_used"] += 1
            if has_opaque_sky and pd.notna(r.get("opaque_sky_cover_tenths")):
                opaque_sky = float(r.get("opaque_sky_cover_tenths"))
                if not (0.0 <= opaque_sky <= 10.0):
                    opaque_sky = 99.0
            else:
                opaque_sky = total_sky if args.opaque_sky_cover_policy == "equal_total" else 99.0
            row = r.copy()
            row.update({
                "dry_bulb_c": tas,
                "dew_point_c": dew,
                "rh_pct": rh,
                "pressure_pa": pressure,
                "ghi_wm2": ghi,
                "dhi_wm2": dhi,
                "dni_wm2": dni,
                "horiz_ir_wm2": horiz_ir,
                "wind_speed_ms": max(0.0, float(r["sfcWind"])),
                "wind_dir_deg": wind_dir,
                "total_sky_cover_tenths": total_sky,
                "opaque_sky_cover_tenths": opaque_sky,
                "solar_zenith_deg": zen,
                "solar_altitude_deg": solar_alt,
                "epw_completion_model": "v4",
            })
            rows.append(row)
        out = pd.DataFrame(rows)
        out.to_csv(out_path, index=False)
        meta_payload = {
            "inputs": {"hourly_table": str(hourly_path), "station_metadata": str(meta_path)},
            "method": {
                "dew_point": "Magnus formula from tas + RH",
                "pressure": "observed if available, else standard atmosphere/fallback",
                "solar_decomposition": "Boland-Ridley-style logistic diffuse fraction from GHI when DHI is unavailable",
                "boland_ridley_style_coefficients": {"a": args.br_a, "b": args.br_b},
                "dni_cap": args.dni_cap,
                "total_sky_cover": "uses pre-interpolated total_sky_cover_tenths from cloud-cover auxiliary layer when available; otherwise converts retained/interpolated MeteoSwiss cloudcover to EPW 0-10 tenths; invalid/missing values become 99",
                "opaque_sky_cover_policy": args.opaque_sky_cover_policy,
            },
            "summary_metadata": {"row_count": int(len(out)), "source_counts": source_counts, "fallback_pressure_pa": fallback_pressure},
        }
        with open(out_path.with_suffix(out_path.suffix + ".meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta_payload, f, ensure_ascii=False, indent=2)
        log(f"Completed EPW-ready table: {out_path}")
        return 0
    except Exception as exc:
        log(f"[ERROR] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
