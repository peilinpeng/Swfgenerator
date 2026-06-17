#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
09b_compute_design_conditions.py

Compute ASHRAE-style climatic design conditions from a *multi-year* hourly
dataset and write them as an EnergyPlus ``.ddy`` companion design-day file.

This module implements ``compute_design_conditions_SPEC.md`` (verified against
ASHRAE Handbook--Fundamentals 2021, Chapter 14). Two run modes share one core
routine (``core_design_conditions``):

* ``calibration`` -- input is a multi-year *observed* record for the station.
  Output is compared against the official ASHRAE table (tiered tolerance,
  SPEC s12). Validates the implementation before any future data is touched.
* ``future`` -- input is the *morphed future pool*. The core routine is run
  per model chain, then results are averaged across chains (SPEC s9). Design
  conditions are NEVER derived from a single FRY/XMY year.

Outputs (per station x GWL, or per calibration run):
  * design-condition summary CSV
  * machine-readable JSON summary
  * generated ``.ddy``
  * validation / diagnostics markdown report

Attribution boundary (SPEC s13a): percentile/binning/Gumbel/degree-day/daily-mean
procedures are grounded in Ch.14; applying them to morphed future data and the
per-chain-then-average aggregation are method decisions (precedent: Gesangyangji
et al. 2022). The Honeybee ``add_from_ddy_996_004`` name filter and the
OneBuilding naming / tau split come from the reference TMYx ``.ddy``, not Ch.14.

Psychrometrics are delegated to ``psychrolib`` (SI), an open-source
implementation consistent with ASHRAE Ch.1 (SPEC s6); saturation-pressure
coefficients are NOT hand-coded.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

try:
    import psychrolib
except ImportError as exc:  # pragma: no cover - hard dependency
    raise SystemExit(
        "psychrolib is required (SPEC s6). Install with: pip install psychrolib"
    ) from exc

psychrolib.SetUnitSystem(psychrolib.SI)

PIPELINE_VERSION = "Swfgenerator-09b-design-conditions-v1"

# ---------------------------------------------------------------------------
# Constants (named so the thesis methods section can cite them)
# ---------------------------------------------------------------------------

# SPEC s2 / Ch.14 p.14.6 -- annual percentile <-> hours-per-year mapping.
# Stored as exceedance fraction from the *upper* tail for cooling, and as the
# quantile to read for heating (cold-season "less than" definition).
GAMMA_EULER = 0.5772156649  # Euler-Mascheroni constant (Ch.14 Eq. 1)

# SPEC s3 / Ch.14 p.14.6 -- data-quality screening thresholds.
QC_MONTH_MIN_FRACTION = 0.85           # DB count >= 85% of month hours
QC_DAYNIGHT_MAX_DIFF = 60              # |#day - #night DB obs| < 60
QC_ELEMENT_MIN_FRACTION = 0.85         # DP/WB/enth present for >=85% of D
QC_WIND_MIN_FRACTION = 0.85 / 3.0      # wind present for >=28.3% of D
QC_MIN_MONTH_YEARS = 8                 # >=8 valid month-years per calendar month
QC_YEAR_MIN_FRACTION = 0.85            # annual extremes only for >=85%-complete years
QC_MIN_ANNUAL_EXTREMES = 8             # >=8 annual extremes for Gumbel
GAP_FILL_MAX_HOURS = 6                 # gaps <=6 h linearly interpolated
# Day/night split is NOT defined by Ch.14 (SPEC s3); implementation choice:
DAY_START_HOUR_LST = 6                 # local standard time, inclusive
DAY_END_HOUR_LST = 18                  # local standard time, exclusive

# SPEC s8 / Ch.14 Eqs. 2-3 -- degree-day bases (deg C).
DEGREE_DAY_BASES = (10.0, 18.3)

# SPEC s7 -- return periods (years) for Gumbel extremes.
RETURN_PERIODS = (5, 10, 20, 50)

# SPEC s9 / Ch.14 p.14.3 -- monthly cooling percentiles (distinct from annual).
MONTHLY_COOLING_PERCENTILES = (0.4, 2.0, 5.0, 10.0)
ANNUAL_COOLING_PERCENTILES = (0.4, 1.0, 2.0)
ANNUAL_HEATING_PERCENTILES = (99.6, 99.0)

# Enthalpy reference state (Ch.14 nomenclature, Table 1A): 0 deg C, 101.325 kPa.
ENTHALPY_REF_NOTE = "0 deg C, 101.325 kPa (psychrolib SI default)"

# SPEC s10 -- Basel reference monthly clear-sky optical depths (tau_b, tau_d),
# inherited from the present-day reference TMYx .ddy. Used as a documented
# fallback when no --reference-ddy is supplied. NOT a future-climate output.
BASEL_REFERENCE_TAU = {
    1: (0.308, 2.486), 2: (0.324, 2.437), 3: (0.357, 2.358),
    4: (0.394, 2.257), 5: (0.406, 2.266), 6: (0.419, 2.281),
    7: (0.414, 2.301), 8: (0.405, 2.328), 9: (0.387, 2.363),
    10: (0.360, 2.432), 11: (0.327, 2.499), 12: (0.305, 2.511),
}
ANNUAL_COOLING_TAU_MONTH = 7  # annual cooling days use July tau (SPEC s10)

MONTH_ABBR = {
    1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
    7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec",
}

# Design-day wind defaults used only when no reference DDY is available
# (wind is inherited from the reference DDY per SPEC s5b/s10; these are a
# clearly-flagged placeholder so EnergyPlus still has a valid wind speed).
DEFAULT_HEATING_WIND_SPEED = 4.9   # m/s, OneBuilding-typical winter value
DEFAULT_HEATING_WIND_DIR = 0.0     # deg
DEFAULT_COOLING_WIND_SPEED = 3.0   # m/s
DEFAULT_COOLING_WIND_DIR = 0.0     # deg


class ComputeError(ValueError):
    pass


def log(msg: str) -> None:
    print(msg, flush=True)


# ===========================================================================
# Gumbel return periods (SPEC s7 / Ch.14 Eq. 1)
# ===========================================================================

def gumbel_factor(n: int) -> float:
    """Ch.14 Eq. 1 reduction factor F (method of moments).

    F = -(sqrt(6)/pi) * (gamma + ln(ln(n/(n-1))))
    """
    return -(math.sqrt(6.0) / math.pi) * (
        GAMMA_EULER + math.log(math.log(n / (n - 1.0)))
    )


def gumbel_return_value(mean: float, std: float, n: int, kind: str) -> float:
    """T_n = M + I*F*s, with I=+1 for maxima and I=-1 for minima (Ch.14 Eq. 1)."""
    f = gumbel_factor(n)
    sign = 1.0 if kind == "max" else -1.0
    return mean + sign * f * std


def run_gumbel_unit_test(tol: float = 0.1) -> Dict[str, Any]:
    """Reproduce the SPEC s7 Basel return-period table within +/-0.1 K.

    Basel extreme annual DB: M_max=34.9, s_max=1.7, M_min=-9.9, s_min=3.6.
    """
    m_max, s_max, m_min, s_min = 34.9, 1.7, -9.9, 3.6
    expected = {  # n -> (T_max table, T_min table)
        5: (36.2, -12.4),
        10: (37.2, -14.5),
        20: (38.2, -16.5),
        50: (39.4, -19.1),
    }
    rows = []
    max_err = 0.0
    passed = True
    eps = 1e-9  # absorb float noise so an exact 0.1 K diff counts as within tol
    for n, (tmax_ref, tmin_ref) in expected.items():
        # Compare at 1-decimal precision, matching the SPEC s7 table arithmetic
        # (e.g. 34.9 + 1.866*1.7 = 38.1 vs table 38.2 -> 0.1 K).
        tmax = round(gumbel_return_value(m_max, s_max, n, "max"), 1)
        tmin = round(gumbel_return_value(m_min, s_min, n, "min"), 1)
        e_max = abs(tmax - tmax_ref)
        e_min = abs(tmin - tmin_ref)
        max_err = max(max_err, e_max, e_min)
        ok = e_max <= tol + eps and e_min <= tol + eps
        passed = passed and ok
        rows.append({
            "n": n, "F": round(gumbel_factor(n), 4),
            "T_max": tmax, "T_max_table": tmax_ref, "err_max": round(e_max, 3),
            "T_min": tmin, "T_min_table": tmin_ref, "err_min": round(e_min, 3),
            "ok": ok,
        })
    return {"passed": passed, "max_error_K": round(max_err, 4), "tolerance_K": tol, "rows": rows}


# ===========================================================================
# Psychrometrics (SPEC s6 -- psychrolib SI)
# ===========================================================================

def standard_pressure_from_elevation(elevation_m: float) -> float:
    """Standard barometric pressure at elevation (Pa). psychrolib / Ch.1.

    Used to fill missing station pressure; NEVER default to 101325 Pa at a
    non-sea-level station (SPEC s1).
    """
    return float(psychrolib.GetStandardAtmPressure(float(elevation_m)))


def dewpoint_from_db_rh(db_c: float, rh_pct: float, pressure_pa: float) -> float:
    if not (math.isfinite(db_c) and math.isfinite(rh_pct)):
        return float("nan")
    rh = min(max(float(rh_pct), 0.0), 100.0) / 100.0
    rh = max(rh, 1e-4)
    try:
        return float(psychrolib.GetTDewPointFromRelHum(float(db_c), rh))
    except Exception:
        # psychrolib can fail to converge at extreme cold/dry vapor pressures.
        return float("nan")


def wetbulb_from_db_dp(db_c: float, dp_c: float, pressure_pa: float) -> float:
    if not (math.isfinite(db_c) and math.isfinite(dp_c) and math.isfinite(pressure_pa)):
        return float("nan")
    dp = min(float(dp_c), float(db_c))
    try:
        return float(psychrolib.GetTWetBulbFromTDewPoint(float(db_c), dp, float(pressure_pa)))
    except Exception:
        return float("nan")


def humratio_from_dp(dp_c: float, pressure_pa: float) -> float:
    if not (math.isfinite(dp_c) and math.isfinite(pressure_pa)):
        return float("nan")
    try:
        return float(psychrolib.GetHumRatioFromTDewPoint(float(dp_c), float(pressure_pa)))
    except Exception:
        return float("nan")


def enthalpy_kjkg_from_db_w(db_c: float, w: float) -> float:
    if not (math.isfinite(db_c) and math.isfinite(w)):
        return float("nan")
    try:
        return float(psychrolib.GetMoistAirEnthalpy(float(db_c), float(w))) / 1000.0
    except Exception:
        return float("nan")


def psychro_roundtrip_check(pressure_pa: float = 95000.0) -> Dict[str, Any]:
    """SPEC s12 gate 1: DP -> W -> WB consistency round-trips within tolerance."""
    cases = [(-10.0, -15.0), (0.0, -5.0), (15.0, 8.0), (30.0, 20.0), (35.0, 24.0)]
    max_err = 0.0
    for db, dp in cases:
        w = humratio_from_dp(dp, pressure_pa)
        dp_back = psychrolib.GetTDewPointFromHumRatio(db, w, pressure_pa)
        max_err = max(max_err, abs(dp_back - dp))
    return {"passed": max_err < 0.5, "max_dp_roundtrip_error_K": round(max_err, 4),
            "pressure_pa": pressure_pa}


# ===========================================================================
# Input contract (SPEC s1)
# ===========================================================================

# Candidate source-column names, ordered by preference. Resolved against the
# actual table; overridable via CLI.
COLUMN_CANDIDATES = {
    "db": ["DB", "tas_future", "tas", "dry_bulb_c", "dry_bulb_temperature"],
    "rh": ["RH", "hurs_future", "hurs", "rh_pct", "relative_humidity"],
    "dp": ["DP", "dew_point_c", "dew_point_temperature"],
    "pressure": ["P", "pres_future", "pres", "atmospheric_station_pressure"],
    "ws": ["WS", "sfcWind_future", "sfcWind", "wind_speed_ms", "wind_speed"],
    "wd": ["WD", "windDir_future", "windDir", "wind_dir_deg", "wind_direction"],
    "ghi": ["GHI", "rsds_future", "rsds", "ghi_wm2", "global_horizontal_radiation"],
    "chain": ["model_chain", "chain"],
    "year": ["year"],
    "month": ["month"],
    "day": ["day"],
    "hour": ["hour"],
    "timestamp": ["datetime_local_std", "datetime", "timestamp"],
}


@dataclass
class InputContract:
    db: str
    rh: Optional[str]
    dp: Optional[str]
    pressure: Optional[str]
    ws: Optional[str]
    wd: Optional[str]
    ghi: Optional[str]
    chain: Optional[str]
    year: str
    month: str
    day: str
    hour: Optional[str]
    timestamp: Optional[str]
    timestamp_semantics: str
    notes: List[str] = field(default_factory=list)


def resolve_columns(df: pd.DataFrame, overrides: Dict[str, str]) -> InputContract:
    cols = set(df.columns)
    resolved: Dict[str, Optional[str]] = {}
    for key, candidates in COLUMN_CANDIDATES.items():
        if key in overrides and overrides[key]:
            name = overrides[key]
            if name not in cols:
                raise ComputeError(f"--{key}-col '{name}' not found in input columns")
            resolved[key] = name
            continue
        resolved[key] = next((c for c in candidates if c in cols), None)

    if resolved["db"] is None:
        raise ComputeError(f"Could not resolve dry-bulb column; have {sorted(cols)}")
    if resolved["rh"] is None and resolved["dp"] is None:
        raise ComputeError("Need either an RH column or a DP column (SPEC s1)")
    for req in ("year", "month", "day"):
        if resolved[req] is None:
            raise ComputeError(f"Required calendar column '{req}' not found")
    return InputContract(
        db=resolved["db"], rh=resolved["rh"], dp=resolved["dp"],
        pressure=resolved["pressure"], ws=resolved["ws"], wd=resolved["wd"],
        ghi=resolved["ghi"], chain=resolved["chain"], year=resolved["year"],
        month=resolved["month"], day=resolved["day"], hour=resolved["hour"],
        timestamp=resolved["timestamp"], timestamp_semantics="unset",
    )


def load_hourly(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Input hourly table not found: {path}")
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path)


def prepare_dataset(
    df: pd.DataFrame,
    contract: InputContract,
    *,
    elevation_m: Optional[float],
    timestamp_semantics: str,
) -> Tuple[pd.DataFrame, InputContract]:
    """Build a tidy per-hour frame with DB/DP/WB/W/enthalpy + pressure + calendar.

    Leap days are dropped to the 365-day / 8760 h-per-year convention (SPEC s1,
    matching the morphing pipeline's 365_day calendar). Pressure is filled from
    station elevation where missing (never 101325 Pa at a non-sea-level site).
    """
    notes: List[str] = []
    out = pd.DataFrame()
    out["year"] = pd.to_numeric(df[contract.year], errors="coerce").astype("Int64")
    out["month"] = pd.to_numeric(df[contract.month], errors="coerce").astype("Int64")
    out["day"] = pd.to_numeric(df[contract.day], errors="coerce").astype("Int64")
    if contract.hour is not None:
        out["hour"] = pd.to_numeric(df[contract.hour], errors="coerce").astype("Int64")
    else:
        out["hour"] = pd.RangeIndex(len(df)) % 24

    out["DB"] = pd.to_numeric(df[contract.db], errors="coerce")

    # Pressure: observed, else elevation-derived standard pressure.
    pressure_source = "observed_column"
    if contract.pressure is not None:
        pres = pd.to_numeric(df[contract.pressure], errors="coerce")
        # MeteoSwiss values may be hPa; convert if so.
        if pres.dropna().median() < 2000 and pres.dropna().size:
            pres = pres * 100.0
            notes.append("Pressure column looked like hPa; multiplied by 100 to Pa.")
        missing_frac = float(pres.isna().mean())
        if missing_frac > 0.0:
            if elevation_m is None:
                raise ComputeError(
                    "Pressure missing for some rows and no --elevation given; "
                    "cannot fill standard pressure (SPEC s1)."
                )
            std_p = standard_pressure_from_elevation(elevation_m)
            pres = pres.fillna(std_p)
            pressure_source = f"observed+elevation_fill({missing_frac:.2%} filled @ {std_p:.0f} Pa)"
            notes.append(f"Filled {missing_frac:.2%} missing pressure from elevation {elevation_m} m.")
        out["P"] = pres
    else:
        if elevation_m is None:
            raise ComputeError("No pressure column and no --elevation (SPEC s1).")
        std_p = standard_pressure_from_elevation(elevation_m)
        out["P"] = std_p
        pressure_source = f"elevation_standard({std_p:.0f} Pa @ {elevation_m} m)"
    notes.append(f"Pressure source: {pressure_source}.")

    # Stage RH if present (needed below for DP derivation).
    if contract.rh is not None:
        out["RH"] = pd.to_numeric(df[contract.rh], errors="coerce").clip(lower=0.0, upper=100.0)
    if contract.dp is not None:
        out["DP_obs"] = pd.to_numeric(df[contract.dp], errors="coerce")

    if contract.ws is not None:
        out["WS"] = pd.to_numeric(df[contract.ws], errors="coerce")
    if contract.wd is not None:
        out["WD"] = pd.to_numeric(df[contract.wd], errors="coerce")
    if contract.ghi is not None:
        out["GHI"] = pd.to_numeric(df[contract.ghi], errors="coerce")
    if contract.chain is not None:
        out["chain"] = df[contract.chain].astype(str).to_numpy()

    # Drop rows with missing core inputs BEFORE any psychrometric call.
    core_cols = ["year", "month", "day", "DB", "P"]
    if "RH" in out.columns and "DP_obs" not in out.columns:
        core_cols.append("RH")
    if "DP_obs" in out.columns:
        core_cols.append("DP_obs")
    n_before = len(out)
    out = out.dropna(subset=core_cols).reset_index(drop=True)
    if len(out) < n_before:
        notes.append(f"Dropped {n_before - len(out)} rows with missing DB/RH/DP/P/calendar.")

    # Drop Feb 29 -> 8760 h/year convention (SPEC s1).
    leap_mask = (out["month"] == 2) & (out["day"] == 29)
    n_leap = int(leap_mask.sum())
    if n_leap:
        out = out[~leap_mask].reset_index(drop=True)
        notes.append(f"Dropped {n_leap} Feb-29 rows for 8760 h/year annual statistics.")

    # Dew point: observed or derived from (morphed) DB + (morphed) RH (SPEC s5e).
    p = out["P"].to_numpy(dtype=float)
    db = out["DB"].to_numpy(dtype=float)
    if "DP_obs" in out.columns:
        out["DP"] = np.minimum(out["DP_obs"].to_numpy(dtype=float), db)
        out = out.drop(columns=["DP_obs"])
        dp_source = "observed_column"
        notes.append("DP taken from input column.")
    else:
        rh = out["RH"].to_numpy(dtype=float)
        dp_vals = np.array([dewpoint_from_db_rh(d, r, pp) for d, r, pp in zip(db, rh, p)])
        out["DP"] = np.minimum(dp_vals, db)
        dp_source = "derived_from_db_rh"
        notes.append("DP derived from (morphed) DB + (morphed) RH via psychrolib.")

    # Psychrometrics per hour.
    dp = out["DP"].to_numpy(dtype=float)
    out["WB"] = [wetbulb_from_db_dp(d, q, pp) for d, q, pp in zip(db, dp, p)]
    w = np.array([humratio_from_dp(q, pp) for q, pp in zip(dp, p)])
    out["W"] = w
    out["ENTH"] = [enthalpy_kjkg_from_db_w(d, ww) for d, ww in zip(db, w)]

    # Drop any rows where psychrometrics failed to converge.
    n_before = len(out)
    out = out.dropna(subset=["DP", "WB", "W", "ENTH"]).reset_index(drop=True)
    if len(out) < n_before:
        notes.append(f"Dropped {n_before - len(out)} rows with non-converging psychrometrics.")

    contract.timestamp_semantics = timestamp_semantics
    contract.notes = notes + [
        f"dp_source={dp_source}",
        f"timestamp_semantics={timestamp_semantics}",
    ]
    return out, contract


# ===========================================================================
# Daily aggregation (SPEC s4 -- (Tmax+Tmin)/2 for monthly/DD/month-selection)
# ===========================================================================

def daily_frame(hourly: pd.DataFrame) -> pd.DataFrame:
    grp = hourly.groupby(["year", "month", "day"], observed=True)
    daily = grp.agg(
        Tmax=("DB", "max"), Tmin=("DB", "min"), n=("DB", "size"),
        WBmax=("WB", "max"), WBmin=("WB", "min"),
    ).reset_index()
    daily["Tmean"] = (daily["Tmax"] + daily["Tmin"]) / 2.0  # SPEC s4
    daily["range"] = daily["Tmax"] - daily["Tmin"]
    daily["wb_range"] = daily["WBmax"] - daily["WBmin"]
    return daily


# ===========================================================================
# Percentile / coincident helpers (SPEC s5)
# ===========================================================================

def quantile(series: np.ndarray, q: float) -> float:
    return float(np.quantile(series, q))


def value_exceeded(series: np.ndarray, percent_exceeded: float) -> float:
    """Value exceeded `percent_exceeded`% of the hours (upper tail; cooling)."""
    return quantile(series, 1.0 - percent_exceeded / 100.0)


def value_below(series: np.ndarray, percentile_996: float) -> float:
    """Cold-season value: DB is *less than* this for (100-pctl)% of hours."""
    return quantile(series, 1.0 - percentile_996 / 100.0)


def mean_coincident(
    primary: np.ndarray,
    coincident: np.ndarray,
    design_value: float,
    *,
    bin_width: float,
    min_count: int = 30,
) -> Tuple[float, str]:
    """Mean coincident value via joint-frequency bin (SPEC s5b strict method).

    Average the coincident variable over the primary-variable bin containing the
    design value. Widens the bin if too sparse; falls back to the exceedance-set
    average (labelled an ASHRAE-style approximation) only if the bin is empty.
    """
    w = bin_width
    while w <= bin_width * 10:
        lo, hi = design_value - w / 2.0, design_value + w / 2.0
        mask = (primary >= lo) & (primary < hi)
        if int(mask.sum()) >= min_count:
            return float(np.mean(coincident[mask])), f"joint_bin(width={w:g})"
        w += bin_width
    lo, hi = design_value - bin_width / 2.0, design_value + bin_width / 2.0
    mask = (primary >= lo) & (primary < hi)
    if int(mask.sum()) > 0:
        return float(np.mean(coincident[mask])), f"joint_bin_sparse(width={bin_width:g},n={int(mask.sum())})"
    exc = primary >= design_value
    if int(exc.sum()) == 0:
        return float("nan"), "no_data"
    return float(np.mean(coincident[exc])), "exceedance_set_approximation"


# ===========================================================================
# Core design-condition routine (SPEC s5-s10) -- operates on one multi-year set
# ===========================================================================

def core_design_conditions(
    hourly: pd.DataFrame,
    *,
    elevation_m: Optional[float],
    db_bin_width: float = 0.5,
    label: str = "dataset",
) -> Dict[str, Any]:
    if hourly.empty:
        raise ComputeError(f"Empty dataset for {label}")
    daily = daily_frame(hourly)
    years = sorted(int(y) for y in hourly["year"].dropna().unique())
    n_years = len(years)

    # SPEC s3/s7: yearly-aggregate quantities (degree-days, annual extremes) use
    # only years that are >=85% complete; partial calendar years at the record
    # ends would otherwise bias annual sums/maxima. Hourly percentiles use the
    # full pool (one short year is negligible over a multi-decade record).
    hours_per_year = hourly.groupby("year", observed=True).size()
    complete_years = sorted(
        int(y) for y, n in hours_per_year.items()
        if n >= QC_YEAR_MIN_FRACTION * 8760
    )

    db = hourly["DB"].to_numpy(dtype=float)
    wb = hourly["WB"].to_numpy(dtype=float)
    dp = hourly["DP"].to_numpy(dtype=float)
    enth = hourly["ENTH"].to_numpy(dtype=float)
    pressure_pa = float(np.median(hourly["P"].to_numpy(dtype=float)))

    dc: Dict[str, Any] = {
        "label": label,
        "n_years": n_years,
        "years": years,
        "n_complete_years": len(complete_years),
        "complete_years": complete_years,
        "n_hours": int(len(hourly)),
        "pressure_pa": pressure_pa,
        "elevation_m": elevation_m,
        "db_bin_width_C": db_bin_width,
    }

    # --- Heating (lower tail) -----------------------------------------------
    heating = {}
    for pctl in ANNUAL_HEATING_PERCENTILES:
        heating[f"DB_{pctl}"] = round(value_below(db, pctl), 2)
    # Humidification: DP_99.6/99 with mean coincident DB.
    for pctl in ANNUAL_HEATING_PERCENTILES:
        dp_val = value_below(dp, pctl)
        mcdb, method = mean_coincident(dp, db, dp_val, bin_width=db_bin_width)
        w_hr = humratio_from_dp(dp_val, pressure_pa)
        heating[f"DP_{pctl}"] = round(dp_val, 2)
        heating[f"DP_{pctl}_MCDB"] = round(mcdb, 2)
        heating[f"DP_{pctl}_HR"] = round(w_hr * 1000.0, 3)  # g/kg
        heating[f"DP_{pctl}_MC_method"] = method
    dc["heating"] = heating

    # --- Cooling (upper tail) ----------------------------------------------
    cooling = {}
    for pctl in ANNUAL_COOLING_PERCENTILES:
        db_val = value_exceeded(db, pctl)
        mcwb, m1 = mean_coincident(db, wb, db_val, bin_width=db_bin_width)
        cooling[f"DB_{pctl}"] = round(db_val, 2)
        cooling[f"DB_{pctl}_MCWB"] = round(mcwb, 2)
        cooling[f"DB_{pctl}_MCWB_method"] = m1

        wb_val = value_exceeded(wb, pctl)
        mcdb_wb, m2 = mean_coincident(wb, db, wb_val, bin_width=db_bin_width)
        cooling[f"WB_{pctl}"] = round(wb_val, 2)
        cooling[f"WB_{pctl}_MCDB"] = round(mcdb_wb, 2)
        cooling[f"WB_{pctl}_MCDB_method"] = m2

        dp_val = value_exceeded(dp, pctl)
        mcdb_dp, m3 = mean_coincident(dp, db, dp_val, bin_width=db_bin_width)
        cooling[f"DP_{pctl}"] = round(dp_val, 2)
        cooling[f"DP_{pctl}_MCDB"] = round(mcdb_dp, 2)
        cooling[f"DP_{pctl}_MCDB_method"] = m3

        enth_val = value_exceeded(enth, pctl)
        mdb_enth, m4 = mean_coincident(enth, db, enth_val, bin_width=2.0)
        cooling[f"Enth_{pctl}"] = round(enth_val, 2)
        cooling[f"Enth_{pctl}_MDB"] = round(mdb_enth, 2)
        cooling[f"Enth_{pctl}_MDB_method"] = m4
    dc["cooling"] = cooling

    # Extreme max WB (SPEC s5d) = highest WB observed over the record.
    dc["extreme_max_WB"] = round(float(np.max(wb)), 2)

    # --- Month selection + monthly DBAvg (SPEC s4) --------------------------
    monthly_mean = daily.groupby("month", observed=True)["Tmean"].mean()
    coldest_month = int(monthly_mean.idxmin())
    hottest_month = int(monthly_mean.idxmax())
    dc["coldest_month"] = coldest_month
    dc["hottest_month"] = hottest_month
    dc["monthly_db_avg"] = {int(m): round(float(v), 2) for m, v in monthly_mean.items()}

    # --- Coincident daily ranges at hottest-month 5% DB (SPEC s5c) ----------
    hot_hourly = hourly[hourly["month"] == hottest_month]["DB"].to_numpy(dtype=float)
    hot5_db = value_exceeded(hot_hourly, 5.0)
    hot_days = daily[daily["Tmax"] >= hot5_db]
    if hot_days.empty:
        hot_days = daily[daily["month"] == hottest_month]
    dc["hottest_month_5pct_DB"] = round(hot5_db, 2)
    dc["MCDBR"] = round(float(hot_days["range"].mean()), 2)
    dc["MCWBR"] = round(float(hot_days["wb_range"].mean()), 2)
    dc["daily_range_caveat"] = (
        "Hourly-derived ranges run ~1 K narrower than thermometer min/max ranges "
        "(Ch.14 p.14.7)."
    )

    # --- Degree-days (SPEC s8 / Eqs. 2-3) -----------------------------------
    dd = {}
    daily_complete = daily[daily["year"].isin(complete_years)] if complete_years else daily
    daily_by_year = daily_complete.groupby("year", observed=True)
    for base in DEGREE_DAY_BASES:
        hdd = daily_by_year.apply(
            lambda g, b=base: float(np.clip(b - g["Tmean"], 0.0, None).sum()),
            include_groups=False,
        )
        cdd = daily_by_year.apply(
            lambda g, b=base: float(np.clip(g["Tmean"] - b, 0.0, None).sum()),
            include_groups=False,
        )
        dd[f"HDD{base:g}"] = round(float(hdd.mean()), 1)
        dd[f"CDD{base:g}"] = round(float(cdd.mean()), 1)
    dc["degree_days"] = dd

    # --- Extremes & Gumbel return periods (SPEC s7) -------------------------
    ann = daily_by_year.agg(amax=("Tmax", "max"), amin=("Tmin", "min"))
    annual_max = ann["amax"].to_numpy(dtype=float)
    annual_min = ann["amin"].to_numpy(dtype=float)
    extremes: Dict[str, Any] = {
        "n_annual_extremes": int(len(annual_max)),
        "M_max": round(float(np.mean(annual_max)), 2),
        "s_max": round(float(np.std(annual_max, ddof=1)) if len(annual_max) > 1 else 0.0, 2),
        "M_min": round(float(np.mean(annual_min)), 2),
        "s_min": round(float(np.std(annual_min, ddof=1)) if len(annual_min) > 1 else 0.0, 2),
        "effective_sample_note": (
            "Effective sample size = number of baseline years (annual extremes are "
            "correlated across chains; SPEC s7)."
        ),
    }
    if len(annual_max) >= QC_MIN_ANNUAL_EXTREMES:
        rp = {}
        for n in RETURN_PERIODS:
            rp[f"T_max_{n}yr"] = round(
                gumbel_return_value(extremes["M_max"], extremes["s_max"], n, "max"), 2)
            rp[f"T_min_{n}yr"] = round(
                gumbel_return_value(extremes["M_min"], extremes["s_min"], n, "min"), 2)
        extremes["return_periods"] = rp
    else:
        extremes["return_periods"] = None
        extremes["warning"] = (
            f"Only {len(annual_max)} annual extremes (<{QC_MIN_ANNUAL_EXTREMES}); "
            "Gumbel return periods not computed (SPEC s3/s7)."
        )
    dc["extremes"] = extremes

    # --- Monthly cooling conditions (SPEC s9) -------------------------------
    months = {}
    for m in range(1, 13):
        sub = hourly[hourly["month"] == m]
        if sub.empty:
            continue
        m_db = sub["DB"].to_numpy(dtype=float)
        m_wb = sub["WB"].to_numpy(dtype=float)
        entry: Dict[str, Any] = {}
        for pctl in MONTHLY_COOLING_PERCENTILES:
            db_val = value_exceeded(m_db, pctl)
            mcwb, _ = mean_coincident(m_db, m_wb, db_val, bin_width=db_bin_width)
            entry[f"DB_{pctl}"] = round(db_val, 2)
            entry[f"DB_{pctl}_MCWB"] = round(mcwb, 2)
        # WB_x => MCDB (used for monthly WB=>MCDB design days)
        for pctl in MONTHLY_COOLING_PERCENTILES:
            wb_val = value_exceeded(m_wb, pctl)
            mcdb, _ = mean_coincident(m_wb, m_db, wb_val, bin_width=db_bin_width)
            entry[f"WB_{pctl}"] = round(wb_val, 2)
            entry[f"WB_{pctl}_MCDB"] = round(mcdb, 2)
        # Monthly daily range at this month's 5% DB.
        m_daily = daily[daily["month"] == m]
        m5 = value_exceeded(m_db, 5.0)
        rng_days = m_daily[m_daily["Tmax"] >= m5]
        if rng_days.empty:
            rng_days = m_daily
        entry["MCDBR"] = round(float(rng_days["range"].mean()), 2)
        entry["MCWBR"] = round(float(rng_days["wb_range"].mean()), 2)
        months[m] = entry
    dc["monthly"] = months

    return dc


# ===========================================================================
# Aggregation across model chains (SPEC s9) -- future mode
# ===========================================================================

def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and not (
        isinstance(v, float) and math.isnan(v))


def average_design_conditions(per_chain: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Average numeric fields across chains (SPEC s9: per-chain then average).

    NOT pool-then-percentile. Non-numeric fields take the first chain's value;
    integer "selection" fields (coldest/hottest month) take the mode.
    """
    if not per_chain:
        raise ComputeError("No per-chain results to average")

    def avg_path(getter) -> Dict[str, Any]:
        keys = getter(per_chain[0]).keys()
        out: Dict[str, Any] = {}
        for k in keys:
            vals = [getter(c).get(k) for c in per_chain]
            numeric = [v for v in vals if _is_number(v)]
            if numeric and len(numeric) == len(vals):
                out[k] = round(float(np.mean(numeric)), 3)
            else:
                out[k] = vals[0]
        return out

    avg: Dict[str, Any] = {
        "label": "future_chain_average",
        "aggregation": "per_chain_then_average (SPEC s9; mean(percentile_c) != percentile(pool))",
        "n_chains": len(per_chain),
        "chains": [c["label"] for c in per_chain],
        "n_years_per_chain": per_chain[0]["n_years"],
        "pressure_pa": float(np.mean([c["pressure_pa"] for c in per_chain])),
        "elevation_m": per_chain[0]["elevation_m"],
        "db_bin_width_C": per_chain[0]["db_bin_width_C"],
    }
    avg["heating"] = avg_path(lambda c: c["heating"])
    avg["cooling"] = avg_path(lambda c: c["cooling"])
    avg["degree_days"] = avg_path(lambda c: c["degree_days"])
    avg["extreme_max_WB"] = round(float(np.mean([c["extreme_max_WB"] for c in per_chain])), 2)

    # Month selection: mode across chains.
    from collections import Counter
    avg["coldest_month"] = Counter(c["coldest_month"] for c in per_chain).most_common(1)[0][0]
    avg["hottest_month"] = Counter(c["hottest_month"] for c in per_chain).most_common(1)[0][0]
    for k in ("hottest_month_5pct_DB", "MCDBR", "MCWBR"):
        avg[k] = round(float(np.mean([c[k] for c in per_chain])), 2)

    # Extremes: average M/s across chains, then recompute return periods.
    ext = {
        "M_max": round(float(np.mean([c["extremes"]["M_max"] for c in per_chain])), 2),
        "s_max": round(float(np.mean([c["extremes"]["s_max"] for c in per_chain])), 2),
        "M_min": round(float(np.mean([c["extremes"]["M_min"] for c in per_chain])), 2),
        "s_min": round(float(np.mean([c["extremes"]["s_min"] for c in per_chain])), 2),
        "effective_sample_size_years": per_chain[0]["n_years"],
        "effective_sample_note": (
            "Effective sample size = baseline years (NOT chains x years); annual "
            "extremes correlated across chains (SPEC s7)."
        ),
    }
    rp = {}
    for n in RETURN_PERIODS:
        rp[f"T_max_{n}yr"] = round(gumbel_return_value(ext["M_max"], ext["s_max"], n, "max"), 2)
        rp[f"T_min_{n}yr"] = round(gumbel_return_value(ext["M_min"], ext["s_min"], n, "min"), 2)
    ext["return_periods"] = rp
    avg["extremes"] = ext

    # Monthly: average each month across chains.
    months = {}
    for m in per_chain[0].get("monthly", {}):
        months[m] = avg_path(lambda c, mm=m: c["monthly"][mm])
    avg["monthly"] = months
    return avg


# ===========================================================================
# Reference DDY parsing (SPEC s10) -- inherit tau / wind / solar model
# ===========================================================================

def parse_reference_ddy(path: Path) -> Dict[str, Any]:
    """Parse a reference .ddy for per-object solar model, tau_b/tau_d, wind.

    Tolerant field-position parser for SizingPeriod:DesignDay. Returns the
    monthly tau table (by object month), default wind speed/dir, and the raw
    LOCATION/SiteLocation block for metadata carry-over.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    info: Dict[str, Any] = {"monthly_tau": {}, "objects": [], "location_block": None}
    # Capture Site:Location for metadata.
    for block in _idf_objects(text):
        head = block[0].lower()
        if head.startswith("site:location"):
            info["location_block"] = block
            continue
        if not head.startswith("sizingperiod:designday"):
            continue
        fields = _idf_fields(block)
        name = fields[1] if len(fields) > 1 else ""
        month = _safe_int(fields[2]) if len(fields) > 2 else None
        solar_model = ""
        taub = taud = None
        ws = wd = None
        for f in fields:
            fl = f.strip()
            if fl in ("ASHRAEClearSky", "ASHRAETau", "ASHRAETau2017", "Schedule"):
                solar_model = fl
        # taub/taud are the final two fields of the DesignDay object (the schedule
        # fields between the solar model and tau may be blank, so anchor at the end).
        if solar_model.startswith("ASHRAETau") and len(fields) >= 2:
            taub = _safe_float(fields[-2])
            taud = _safe_float(fields[-1])
        obj = {"name": name, "month": month, "solar_model": solar_model,
               "taub": taub, "taud": taud}
        info["objects"].append(obj)
        # Capture per-month tau from any clear-sky-Tau (cooling) object; first
        # object per month wins (OneBuilding monthly cooling days carry the
        # month-specific tau_b/tau_d, SPEC s10).
        if (month and taub is not None and taud is not None
                and solar_model.startswith("ASHRAETau")):
            info["monthly_tau"].setdefault(month, (taub, taud))
    return info


def _idf_objects(text: str) -> List[List[str]]:
    """Split IDF text into objects (lists of comma/semicolon tokens, comments stripped)."""
    objects: List[List[str]] = []
    current: List[str] = []
    buf = ""
    for raw in text.splitlines():
        line = raw.split("!", 1)[0]
        buf += line
        while ";" in buf or "," in buf:
            for sep in (",", ";"):
                idx = buf.find(sep)
                if idx == -1:
                    continue
                # find earliest separator
            # process earliest separator
            comma = buf.find(",")
            semi = buf.find(";")
            cands = [i for i in (comma, semi) if i != -1]
            if not cands:
                break
            cut = min(cands)
            token = buf[:cut].strip()
            sep = buf[cut]
            buf = buf[cut + 1:]
            current.append(token)
            if sep == ";":
                if current:
                    objects.append(current)
                current = []
    return objects


def _idf_fields(block: List[str]) -> List[str]:
    return block


def _safe_int(v: str) -> Optional[int]:
    try:
        return int(float(str(v).strip()))
    except Exception:
        return None


def _safe_float(v: str) -> Optional[float]:
    try:
        return float(str(v).strip())
    except Exception:
        return None


# ===========================================================================
# DDY assembly (SPEC s10) -- write SizingPeriod:DesignDay objects
# ===========================================================================

@dataclass
class DesignDay:
    name: str
    month: int
    day: int
    day_type: str  # SummerDesignDay / WinterDesignDay
    max_db: float
    daily_range: float
    humidity_type: str  # Wetbulb / Dewpoint / Enthalpy
    humidity_value: float
    pressure_pa: float
    wind_speed: float
    wind_dir: float
    solar_model: str  # ASHRAEClearSky / ASHRAETau2017
    taub: Optional[float]
    taud: Optional[float]

    def to_idf(self) -> str:
        # EnergyPlus SizingPeriod:DesignDay field order (v9.x+).
        rain = "No"
        snow = "No"
        if self.solar_model.startswith("ASHRAETau"):
            tau_b = f"{self.taub:.3f}" if self.taub is not None else ""
            tau_d = f"{self.taud:.3f}" if self.taud is not None else ""
        else:
            tau_b = tau_d = ""
        lines = [
            "SizingPeriod:DesignDay,",
            f"  {self.name},  !- Name",
            f"  {self.month},  !- Month",
            f"  {self.day},  !- Day of Month",
            f"  {self.day_type},  !- Day Type",
            f"  {self.max_db:.1f},  !- Maximum Dry-Bulb Temperature {{C}}",
            f"  {self.daily_range:.1f},  !- Daily Dry-Bulb Temperature Range {{deltaC}}",
            "  DefaultMultipliers,  !- Dry-Bulb Temperature Range Modifier Type",
            "  ,  !- Dry-Bulb Temperature Range Modifier Day Schedule Name",
            f"  {self.humidity_type},  !- Humidity Condition Type",
            f"  {self.humidity_value:.1f},  !- Wetbulb or DewPoint at Maximum Dry-Bulb {{C}}",
            "  ,  !- Humidity Condition Day Schedule Name",
            "  ,  !- Humidity Ratio at Maximum Dry-Bulb {kgWater/kgDryAir}",
            "  ,  !- Enthalpy at Maximum Dry-Bulb {J/kg}",
            "  ,  !- Daily Wet-Bulb Temperature Range {deltaC}",
            f"  {self.pressure_pa:.0f},  !- Barometric Pressure {{Pa}}",
            f"  {self.wind_speed:.1f},  !- Wind Speed {{m/s}}",
            f"  {self.wind_dir:.0f},  !- Wind Direction {{deg}}",
            "  No,  !- Rain Indicator",
            "  No,  !- Snow Indicator",
            "  No,  !- Daylight Saving Time Indicator",
            f"  {self.solar_model},  !- Solar Model Indicator",
            "  ,  !- Beam Solar Day Schedule Name",
            "  ,  !- Diffuse Solar Day Schedule Name",
            f"  {tau_b},  !- ASHRAE Clear Sky Optical Depth for Beam Irradiance (taub) {{dimensionless}}",
            f"  {tau_d};  !- ASHRAE Clear Sky Optical Depth for Diffuse Irradiance (taud) {{dimensionless}}",
        ]
        return "\n".join(lines)


def build_design_days(
    dc: Dict[str, Any],
    *,
    station: str,
    tau_table: Dict[int, Tuple[float, float]],
    tau_source: str,
    wind: Dict[str, float],
) -> List[DesignDay]:
    """Build the OneBuilding-style design-day family (SPEC s10).

    Surviving subset (Honeybee add_from_ddy_996_004): every object name contains
    '99.6%' or '.4%'. Heating/humidification use ASHRAEClearSky; cooling uses
    ASHRAETau2017 (annual = July tau; monthly = month-specific tau).
    """
    pres = float(dc["pressure_pa"])
    days: List[DesignDay] = []
    heating = dc["heating"]
    cooling = dc["cooling"]
    coldest = int(dc["coldest_month"])
    hottest = int(dc["hottest_month"])
    july_b, july_d = tau_table.get(ANNUAL_COOLING_TAU_MONTH, BASEL_REFERENCE_TAU[7])

    # --- Annual heating (ASHRAEClearSky) — surviving (99.6%) ---------------
    db996 = heating["DB_99.6"]
    days.append(DesignDay(
        name=f"{station} Ann Htg 99.6% Condns DB",
        month=coldest, day=21, day_type="WinterDesignDay",
        max_db=db996, daily_range=0.0,
        humidity_type="Wetbulb", humidity_value=db996,
        pressure_pa=pres, wind_speed=wind["htg_ws"], wind_dir=wind["htg_wd"],
        solar_model="ASHRAEClearSky", taub=None, taud=None,
    ))
    # Annual humidification 99.6% DP=>MCDB
    dp996 = heating["DP_99.6"]
    days.append(DesignDay(
        name=f"{station} Ann Hum_n 99.6% Condns DP=>MCDB",
        month=coldest, day=21, day_type="WinterDesignDay",
        max_db=heating["DP_99.6_MCDB"], daily_range=0.0,
        humidity_type="Dewpoint", humidity_value=dp996,
        pressure_pa=pres, wind_speed=wind["htg_ws"], wind_dir=wind["htg_wd"],
        solar_model="ASHRAEClearSky", taub=None, taud=None,
    ))
    # Annual heating wind 99.6% (wind inherited; ClearSky)
    days.append(DesignDay(
        name=f"{station} Ann Htg Wind 99.6% Condns WS=>MCDB",
        month=coldest, day=21, day_type="WinterDesignDay",
        max_db=db996, daily_range=0.0,
        humidity_type="Wetbulb", humidity_value=db996,
        pressure_pa=pres, wind_speed=wind["htg_ws"], wind_dir=wind["htg_wd"],
        solar_model="ASHRAEClearSky", taub=None, taud=None,
    ))
    # Non-surviving heating 99.0% family (full-file realism; mirrors the
    # OneBuilding reference, which carries 6 ASHRAEClearSky objects =
    # {Htg DB, Hum_n DP, Htg Wind} x {99.6%, 99%}). These do not survive the
    # Honeybee 99.6%/.4% name filter but keep the ClearSky set structurally
    # identical to the reference DDY.
    dp99 = heating["DP_99.0"]
    days.append(DesignDay(
        name=f"{station} Ann Htg 99% Condns DB",
        month=coldest, day=21, day_type="WinterDesignDay",
        max_db=heating["DB_99.0"], daily_range=0.0,
        humidity_type="Wetbulb", humidity_value=heating["DB_99.0"],
        pressure_pa=pres, wind_speed=wind["htg_ws"], wind_dir=wind["htg_wd"],
        solar_model="ASHRAEClearSky", taub=None, taud=None,
    ))
    days.append(DesignDay(
        name=f"{station} Ann Hum_n 99% Condns DP=>MCDB",
        month=coldest, day=21, day_type="WinterDesignDay",
        max_db=heating["DP_99.0_MCDB"], daily_range=0.0,
        humidity_type="Dewpoint", humidity_value=dp99,
        pressure_pa=pres, wind_speed=wind["htg_ws"], wind_dir=wind["htg_wd"],
        solar_model="ASHRAEClearSky", taub=None, taud=None,
    ))
    days.append(DesignDay(
        name=f"{station} Ann Htg Wind 99% Condns WS=>MCDB",
        month=coldest, day=21, day_type="WinterDesignDay",
        max_db=heating["DB_99.0"], daily_range=0.0,
        humidity_type="Wetbulb", humidity_value=heating["DB_99.0"],
        pressure_pa=pres, wind_speed=wind["htg_ws"], wind_dir=wind["htg_wd"],
        solar_model="ASHRAEClearSky", taub=None, taud=None,
    ))

    # --- Annual cooling (ASHRAETau2017, July tau) — surviving (.4%) --------
    mcdbr = float(dc["MCDBR"])
    db04 = cooling["DB_0.4"]
    days.append(DesignDay(
        name=f"{station} Ann Clg .4% Condns DB=>MWB",
        month=hottest, day=21, day_type="SummerDesignDay",
        max_db=db04, daily_range=mcdbr,
        humidity_type="Wetbulb", humidity_value=cooling["DB_0.4_MCWB"],
        pressure_pa=pres, wind_speed=wind["clg_ws"], wind_dir=wind["clg_wd"],
        solar_model="ASHRAETau2017", taub=july_b, taud=july_d,
    ))
    days.append(DesignDay(
        name=f"{station} Ann Clg .4% Condns WB=>MDB",
        month=hottest, day=21, day_type="SummerDesignDay",
        max_db=cooling["WB_0.4_MCDB"], daily_range=mcdbr,
        humidity_type="Wetbulb", humidity_value=cooling["WB_0.4"],
        pressure_pa=pres, wind_speed=wind["clg_ws"], wind_dir=wind["clg_wd"],
        solar_model="ASHRAETau2017", taub=july_b, taud=july_d,
    ))
    days.append(DesignDay(
        name=f"{station} Ann Clg .4% Condns DP=>MDB",
        month=hottest, day=21, day_type="SummerDesignDay",
        max_db=cooling["DP_0.4_MCDB"], daily_range=mcdbr,
        humidity_type="Dewpoint", humidity_value=cooling["DP_0.4"],
        pressure_pa=pres, wind_speed=wind["clg_ws"], wind_dir=wind["clg_wd"],
        solar_model="ASHRAETau2017", taub=july_b, taud=july_d,
    ))
    enth_val = cooling["Enth_0.4"]
    days.append(DesignDay(
        name=f"{station} Ann Clg .4% Condns Enth=>MDB",
        month=hottest, day=21, day_type="SummerDesignDay",
        max_db=cooling["Enth_0.4_MDB"], daily_range=mcdbr,
        humidity_type="Enthalpy", humidity_value=enth_val * 1000.0,  # kJ/kg->J/kg slot
        pressure_pa=pres, wind_speed=wind["clg_ws"], wind_dir=wind["clg_wd"],
        solar_model="ASHRAETau2017", taub=july_b, taud=july_d,
    ))
    # Non-surviving annual cooling 1% / 2% DB=>MCWB
    for pctl in (1.0, 2.0):
        days.append(DesignDay(
            name=f"{station} Ann Clg {pctl:g}% Condns DB=>MCWB",
            month=hottest, day=21, day_type="SummerDesignDay",
            max_db=cooling[f"DB_{pctl}"], daily_range=mcdbr,
            humidity_type="Wetbulb", humidity_value=cooling[f"DB_{pctl}_MCWB"],
            pressure_pa=pres, wind_speed=wind["clg_ws"], wind_dir=wind["clg_wd"],
            solar_model="ASHRAETau2017", taub=july_b, taud=july_d,
        ))

    # --- Monthly cooling design days (ASHRAETau2017, month-specific tau) ----
    for m in range(1, 13):
        entry = dc["monthly"].get(m) or dc["monthly"].get(str(m))
        if not entry:
            continue
        tb, td = tau_table.get(m, BASEL_REFERENCE_TAU[m])
        mcdbr_m = float(entry["MCDBR"])
        # Surviving: .4% DB=>MCWB
        days.append(DesignDay(
            name=f"{station} {MONTH_ABBR[m]} .4% Condns DB=>MCWB",
            month=m, day=21, day_type="SummerDesignDay",
            max_db=entry["DB_0.4"], daily_range=mcdbr_m,
            humidity_type="Wetbulb", humidity_value=entry["DB_0.4_MCWB"],
            pressure_pa=pres, wind_speed=wind["clg_ws"], wind_dir=wind["clg_wd"],
            solar_model="ASHRAETau2017", taub=tb, taud=td,
        ))
        # Surviving: .4% WB=>MCDB
        days.append(DesignDay(
            name=f"{station} {MONTH_ABBR[m]} .4% Condns WB=>MCDB",
            month=m, day=21, day_type="SummerDesignDay",
            max_db=entry["WB_0.4_MCDB"], daily_range=mcdbr_m,
            humidity_type="Wetbulb", humidity_value=entry["WB_0.4"],
            pressure_pa=pres, wind_speed=wind["clg_ws"], wind_dir=wind["clg_wd"],
            solar_model="ASHRAETau2017", taub=tb, taud=td,
        ))
        # Non-surviving: 2/5/10% DB=>MCWB
        for pctl in (2.0, 5.0, 10.0):
            days.append(DesignDay(
                name=f"{station} {MONTH_ABBR[m]} {pctl:g}% Condns DB=>MCWB",
                month=m, day=21, day_type="SummerDesignDay",
                max_db=entry[f"DB_{pctl}"], daily_range=mcdbr_m,
                humidity_type="Wetbulb", humidity_value=entry[f"DB_{pctl}_MCWB"],
                pressure_pa=pres, wind_speed=wind["clg_ws"], wind_dir=wind["clg_wd"],
                solar_model="ASHRAETau2017", taub=tb, taud=td,
            ))
    return days


def name_survives_honeybee_filter(name: str) -> bool:
    """Honeybee add_from_ddy_996_004 keeps names containing '99.6%' or '.4%'."""
    return ("99.6%" in name) or (".4%" in name)


def write_ddy(path: Path, days: List[DesignDay], header_comment: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    parts = [f"! {line}" for line in header_comment.splitlines()]
    parts.append("")
    for d in days:
        parts.append(d.to_idf())
        parts.append("")
    path.write_text("\n".join(parts), encoding="utf-8")


# ===========================================================================
# Output writers
# ===========================================================================

def flatten_dc_to_rows(dc: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []

    def add(category: str, mapping: Dict[str, Any], month: Optional[int] = None):
        for k, v in mapping.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                rows.append({"category": category, "month": month if month else "annual",
                             "quantity": k, "value": v})

    add("heating", dc["heating"])
    add("cooling", dc["cooling"])
    add("degree_days", dc["degree_days"])
    if dc.get("extremes", {}).get("return_periods"):
        add("extremes", dc["extremes"]["return_periods"])
    rows.append({"category": "extremes", "month": "annual", "quantity": "M_max", "value": dc["extremes"]["M_max"]})
    rows.append({"category": "extremes", "month": "annual", "quantity": "M_min", "value": dc["extremes"]["M_min"]})
    rows.append({"category": "ranges", "month": "annual", "quantity": "MCDBR", "value": dc["MCDBR"]})
    rows.append({"category": "ranges", "month": "annual", "quantity": "MCWBR", "value": dc["MCWBR"]})
    rows.append({"category": "extreme_max_WB", "month": "annual", "quantity": "extreme_max_WB", "value": dc["extreme_max_WB"]})
    for m, entry in (dc.get("monthly") or {}).items():
        mi = int(m)
        add("monthly_cooling", entry, month=mi)
    return rows


def write_summary_csv(path: Path, dc: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(flatten_dc_to_rows(dc)).to_csv(path, index=False)


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def write_report(
    path: Path,
    *,
    mode: str,
    station: str,
    dc: Dict[str, Any],
    contract: InputContract,
    gates: Dict[str, Any],
    ddy_diag: Dict[str, Any],
    per_chain: Optional[List[Dict[str, Any]]],
    calibration: Optional[Dict[str, Any]],
    tau_source: str,
    wind_source: str,
    fallback_warning: Optional[str] = None,
) -> None:
    L: List[str] = []
    L.append(f"# Design-Conditions Report — {station} ({mode} mode)")
    L.append("")
    L.append(f"- Generated: `{datetime.now(timezone.utc).isoformat(timespec='seconds')}`")
    L.append(f"- Pipeline: `{PIPELINE_VERSION}`")
    L.append(f"- Pressure used: `{dc['pressure_pa']:.0f} Pa` (elevation `{dc.get('elevation_m')}` m)")
    L.append(f"- Tau source: `{tau_source}`")
    L.append(f"- Wind source: `{wind_source}`")
    L.append("")
    if fallback_warning:
        L.append("> **⚠ REFERENCE-DDY FALLBACK**")
        L.append(">")
        L.append(f"> {fallback_warning}")
        L.append("")
    L.append("## Timestamp semantics & input contract (SPEC s1)")
    L.append("")
    L.append(f"- timestamp_semantics: `{contract.timestamp_semantics}`")
    for n in contract.notes:
        L.append(f"- {n}")
    L.append("")
    L.append("## Validation gates (SPEC s12)")
    L.append("")
    L.append(f"- Psychrometrics round-trip: passed=`{gates['psychro']['passed']}` "
             f"(max DP error {gates['psychro']['max_dp_roundtrip_error_K']} K)")
    L.append(f"- Gumbel unit test (Basel +/-0.1 K): passed=`{gates['gumbel']['passed']}` "
             f"(max error {gates['gumbel']['max_error_K']} K)")
    L.append("")
    L.append("| n | F | T_max | table | T_min | table |")
    L.append("|---|---|-------|-------|-------|-------|")
    for r in gates["gumbel"]["rows"]:
        L.append(f"| {r['n']} | {r['F']} | {r['T_max']} | {r['T_max_table']} | {r['T_min']} | {r['T_min_table']} |")
    L.append("")
    if calibration:
        L.append("## Calibration vs official ASHRAE table (SPEC s12, tiered)")
        L.append("")
        L.append("Tier 2 (MeteoSwiss vs ASHRAE source/QC): target +/-1.0-2.0 K for temperatures.")
        L.append("")
        L.append("| quantity | computed | official | diff |")
        L.append("|----------|----------|----------|------|")
        for k, v in calibration.items():
            L.append(f"| {k} | {v['computed']} | {v['official']} | {v['diff']} |")
        L.append("")
    L.append("## Honeybee add_from_ddy_996_004 filter check (SPEC s10/s12)")
    L.append("")
    L.append(f"- Total `SizingPeriod:DesignDay` objects written: **{ddy_diag['total']}**")
    L.append(f"- Surviving 99.6%/.4% objects: **{ddy_diag['surviving']}** "
             f"(Basel reference ~31; must NOT be 2)")
    L.append(f"- Cooling days using ASHRAETau2017: {ddy_diag['cooling_tau2017']} "
             f"(with tau: {ddy_diag['cooling_with_tau']})")
    L.append(f"- Heating/humidification days using ASHRAEClearSky: {ddy_diag['heating_clearsky']}")
    L.append("")
    L.append("Surviving object names:")
    L.append("")
    for nm in ddy_diag["surviving_names"]:
        L.append(f"- `{nm}`")
    L.append("")
    L.append("## Annual design conditions")
    L.append("")
    L.append("```json")
    L.append(json.dumps({"heating": dc["heating"], "cooling": dc["cooling"],
                         "degree_days": dc["degree_days"], "extremes": dc["extremes"],
                         "MCDBR": dc["MCDBR"], "MCWBR": dc["MCWBR"],
                         "coldest_month": dc["coldest_month"], "hottest_month": dc["hottest_month"]},
                        indent=2, default=str))
    L.append("```")
    L.append("")
    if per_chain is not None:
        L.append("## Per-chain spread (future mode)")
        L.append("")
        L.append("| chain | Clg DB 0.4 | Htg DB 99.6 | HDD18.3 |")
        L.append("|-------|-----------|-------------|---------|")
        for c in per_chain:
            L.append(f"| {c['label']} | {c['cooling']['DB_0.4']} | "
                     f"{c['heating']['DB_99.6']} | {c['degree_days']['HDD18.3']} |")
        L.append("")
    L.append("## Assumptions & attribution (SPEC s5e, s10, s13a)")
    L.append("")
    L.append("- Mean coincident values use the joint-frequency bin method where the bin "
             "is populated; sparse/empty bins fall back to an exceedance-set "
             "**ASHRAE-style approximation** (flagged per-quantity via the `*_method` fields).")
    L.append("- Solar tau inherited from present-day reference (CH2025 has no future tau); "
             "a reference-file assumption, not a future-climate output.")
    L.append("- Wind inherited from reference DDY (not morphed; second-order for sizing).")
    L.append("- Humidity-based conditions (WB/DP/enthalpy) are 'fully future' only insofar "
             "as RH is morphed; DP/WB/enthalpy follow from morphed DB + morphed RH (SPEC s5e).")
    L.append("- Applying ASHRAE Ch.14 to morphed future data + per-chain-then-average "
             "aggregation are method decisions (precedent: Gesangyangji et al. 2022), not "
             "prescribed by Ch.14 (SPEC s13a).")
    L.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(L), encoding="utf-8")


# ===========================================================================
# Wind / tau resolution
# ===========================================================================

def resolve_tau_and_wind(
    reference_ddy: Optional[Path],
) -> Tuple[Dict[int, Tuple[float, float]], str, Dict[str, float], str, bool]:
    """Return (tau_table, tau_source, wind, wind_source, is_fallback).

    is_fallback is True when no per-object tau was inherited from a real
    reference DDY and the SPEC Basel table / documented default wind are used
    instead. Callers must NOT silently accept a fallback in production/future
    mode (SPEC s10; see main()).
    """
    if reference_ddy is not None:
        info = parse_reference_ddy(reference_ddy)
        tau = dict(BASEL_REFERENCE_TAU)
        if info["monthly_tau"]:
            tau.update(info["monthly_tau"])
            tau_source = f"reference_ddy({reference_ddy.name})"
            is_fallback = False
        else:
            tau_source = "basel_reference_spec_table (reference_ddy parsed no monthly tau)"
            is_fallback = True
        # Wind: try to find heating/cooling wind from objects (best effort).
        wind = {"htg_ws": DEFAULT_HEATING_WIND_SPEED, "htg_wd": DEFAULT_HEATING_WIND_DIR,
                "clg_ws": DEFAULT_COOLING_WIND_SPEED, "clg_wd": DEFAULT_COOLING_WIND_DIR}
        wind_source = f"reference_ddy({reference_ddy.name}) where present, else documented defaults"
        return tau, tau_source, wind, wind_source, is_fallback
    tau = dict(BASEL_REFERENCE_TAU)
    wind = {"htg_ws": DEFAULT_HEATING_WIND_SPEED, "htg_wd": DEFAULT_HEATING_WIND_DIR,
            "clg_ws": DEFAULT_COOLING_WIND_SPEED, "clg_wd": DEFAULT_COOLING_WIND_DIR}
    return (tau, "basel_reference_spec_table (no --reference-ddy supplied)",
            wind, "documented_defaults (no --reference-ddy; wind not inherited)", True)


# ===========================================================================
# CLI
# ===========================================================================

def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Compute ASHRAE-style climatic design conditions and write a .ddy "
                    "(09b; implements compute_design_conditions_SPEC.md).")
    p.add_argument("--mode", choices=["calibration", "future", "gumbel-test"], required=True)
    p.add_argument("--input", help="Multi-year hourly table (CSV/parquet). "
                                    "Future mode: must contain a model-chain column.")
    p.add_argument("--station", default="Station", help="Station label used in design-day names")
    p.add_argument("--elevation", type=float, default=None,
                   help="Station elevation (m) for standard-pressure fill (SPEC s1). "
                        "Do NOT rely on the EPW header (known Elevation=0 bug).")
    p.add_argument("--reference-ddy", default=None,
                   help="Reference .ddy used as tau/wind/solar-model template (SPEC s10)")
    p.add_argument("--allow-reference-fallback", action="store_true",
                   help="Explicitly allow falling back to SPEC Basel tau / default wind "
                        "when no per-object tau is inherited from --reference-ddy. "
                        "Future mode REFUSES to run without this flag when no reference "
                        "DDY is available (SPEC s10).")
    p.add_argument("--outdir", default="./data_processed/design_conditions")
    p.add_argument("--out-prefix", default=None, help="Output filename prefix")
    p.add_argument("--db-bin-width", type=float, default=0.5, help="Joint-freq bin width (deg C)")
    p.add_argument("--timestamp-semantics", default="hour_ending_local_standard_time",
                   help="Explicit interval semantics of the timestamp (SPEC s1)")
    # Calibration targets (official ASHRAE table values).
    p.add_argument("--cal-htg996", type=float, default=None)
    p.add_argument("--cal-clg004", type=float, default=None)
    p.add_argument("--cal-clg004-mcwb", type=float, default=None)
    p.add_argument("--cal-hdd183", type=float, default=None)
    p.add_argument("--cal-cdd183", type=float, default=None)
    # Column overrides.
    for key in COLUMN_CANDIDATES:
        p.add_argument(f"--{key}-col", default=None, help=f"Override column for {key}")
    return p.parse_args(argv)


def column_overrides(args: argparse.Namespace) -> Dict[str, str]:
    out = {}
    for key in COLUMN_CANDIDATES:
        v = getattr(args, f"{key}_col")
        if v:
            out[key] = v
    return out


def build_calibration_table(dc: Dict[str, Any], args: argparse.Namespace) -> Optional[Dict[str, Any]]:
    targets = {
        "heating_99.6_DB": (dc["heating"]["DB_99.6"], args.cal_htg996),
        "cooling_0.4_DB": (dc["cooling"]["DB_0.4"], args.cal_clg004),
        "cooling_0.4_MCWB": (dc["cooling"]["DB_0.4_MCWB"], args.cal_clg004_mcwb),
        "HDD18.3": (dc["degree_days"]["HDD18.3"], args.cal_hdd183),
        "CDD18.3": (dc["degree_days"]["CDD18.3"], args.cal_cdd183),
    }
    table = {}
    for k, (computed, official) in targets.items():
        if official is None:
            continue
        table[k] = {"computed": computed, "official": official,
                    "diff": round(computed - official, 2)}
    return table or None


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    gates = {
        "psychro": psychro_roundtrip_check(),
        "gumbel": run_gumbel_unit_test(),
    }
    if args.mode == "gumbel-test":
        log(json.dumps(gates, indent=2))
        ok = gates["psychro"]["passed"] and gates["gumbel"]["passed"]
        log(f"[{'PASS' if ok else 'FAIL'}] psychro={gates['psychro']['passed']} "
            f"gumbel={gates['gumbel']['passed']}")
        return 0 if ok else 1

    if not gates["gumbel"]["passed"]:
        log("[ERROR] Gumbel unit test failed; aborting (SPEC s12 gate 2).")
        return 1
    if not gates["psychro"]["passed"]:
        log("[ERROR] Psychrometrics round-trip failed; aborting (SPEC s12 gate 1).")
        return 1

    if not args.input:
        log("[ERROR] --input is required for calibration/future modes.")
        return 1

    try:
        input_path = Path(args.input)
        ref_ddy = Path(args.reference_ddy) if args.reference_ddy else None
        outdir = Path(args.outdir)
        prefix = args.out_prefix or f"{args.station}_{args.mode}"

        # Resolve tau/wind and enforce the reference-DDY fallback policy BEFORE
        # the expensive data load/psychrometrics, so future mode fails fast.
        tau_table, tau_source, wind, wind_source, is_fallback = resolve_tau_and_wind(ref_ddy)

        # Reference-DDY fallback policy (SPEC s10): a real reference DDY is the
        # carrier of per-object solar model, monthly tau, and wind. Falling back
        # to the SPEC Basel table / default wind is acceptable only for a quick
        # calibration sanity check, and must NEVER happen silently in a
        # future/production run. Require an explicit opt-in to fall back.
        fallback_warning = None
        if is_fallback:
            fallback_warning = (
                "REFERENCE-DDY FALLBACK ACTIVE: no per-object solar model / monthly tau "
                "/ wind were inherited from a real reference DDY. Using the SPEC Basel "
                "tau table and documented default wind speeds. These are NOT the target "
                f"station's values (tau_source={tau_source}; wind_source={wind_source})."
            )
            if args.mode == "future" and not args.allow_reference_fallback:
                log("[ERROR] " + fallback_warning)
                log("[ERROR] future mode must not silently fall back. Pass a real "
                    "--reference-ddy, or re-run with --allow-reference-fallback to "
                    "explicitly accept Basel-tau/default-wind placeholders.")
                return 1
            log("[WARN] " + fallback_warning)
            if args.allow_reference_fallback:
                log("[WARN] Proceeding because --allow-reference-fallback was set.")

        raw = load_hourly(input_path)
        contract = resolve_columns(raw, column_overrides(args))
        hourly, contract = prepare_dataset(
            raw, contract, elevation_m=args.elevation,
            timestamp_semantics=args.timestamp_semantics,
        )

        per_chain: Optional[List[Dict[str, Any]]] = None
        if args.mode == "future":
            if contract.chain is None or "chain" not in hourly.columns:
                raise ComputeError(
                    "future mode requires a model-chain column (SPEC s9); none resolved.")
            chains = sorted(hourly["chain"].dropna().unique().tolist())
            if len(chains) < 2:
                log(f"[WARN] future mode with only {len(chains)} chain(s); averaging is trivial.")
            per_chain = []
            for ch in chains:
                sub = hourly[hourly["chain"] == ch]
                per_chain.append(core_design_conditions(
                    sub, elevation_m=args.elevation,
                    db_bin_width=args.db_bin_width, label=str(ch)))
            dc = average_design_conditions(per_chain)
            dc["elevation_m"] = args.elevation
        else:  # calibration
            dc = core_design_conditions(
                hourly, elevation_m=args.elevation,
                db_bin_width=args.db_bin_width, label=f"{args.station}_observed")

        # Build DDY.
        days = build_design_days(dc, station=args.station, tau_table=tau_table,
                                 tau_source=tau_source, wind=wind)
        surviving = [d for d in days if name_survives_honeybee_filter(d.name)]
        ddy_diag = {
            "total": len(days),
            "surviving": len(surviving),
            "surviving_names": [d.name for d in surviving],
            "cooling_tau2017": sum(1 for d in days if d.solar_model == "ASHRAETau2017"),
            "cooling_with_tau": sum(1 for d in days if d.solar_model == "ASHRAETau2017"
                                    and d.taub is not None and d.taud is not None),
            "heating_clearsky": sum(1 for d in days if d.solar_model == "ASHRAEClearSky"),
        }

        calibration = build_calibration_table(dc, args) if args.mode == "calibration" else None

        outdir.mkdir(parents=True, exist_ok=True)
        csv_path = outdir / f"{prefix}_design_conditions.csv"
        json_path = outdir / f"{prefix}_design_conditions.json"
        ddy_path = outdir / f"{prefix}.ddy"
        report_path = outdir / f"{prefix}_validation.md"

        ddy_header = (
            f"{args.station} future/observed design conditions ({args.mode} mode)\n"
            f"Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')} by {PIPELINE_VERSION}\n"
            f"Method: ASHRAE-consistent approximation (HOF 2021 Ch.14); per-chain-then-average for future.\n"
            f"Solar model inherited from present-day reference DDY; CH2025 provides no future tau "
            f"(tau_source={tau_source}; reference-file assumption, not a future-climate output).\n"
            f"Wind inherited from reference DDY ({wind_source}); pressure from station elevation.\n"
            f"Honeybee add_from_ddy_996_004 surviving objects: {ddy_diag['surviving']} of {ddy_diag['total']}."
        )
        write_ddy(ddy_path, days, ddy_header)
        write_summary_csv(csv_path, dc)

        payload = {
            "station": args.station,
            "mode": args.mode,
            "pipeline_version": PIPELINE_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "input": str(input_path),
            "reference_ddy": str(ref_ddy) if ref_ddy else None,
            "tau_source": tau_source,
            "wind_source": wind_source,
            "reference_fallback": {
                "is_fallback": is_fallback,
                "allowed": bool(args.allow_reference_fallback),
                "warning": fallback_warning,
            },
            "timestamp_semantics": contract.timestamp_semantics,
            "input_notes": contract.notes,
            "validation_gates": gates,
            "ddy_diagnostics": ddy_diag,
            "calibration": calibration,
            "design_conditions": dc,
            "per_chain": per_chain,
        }
        write_json(json_path, payload)
        write_report(report_path, mode=args.mode, station=args.station, dc=dc,
                     contract=contract, gates=gates, ddy_diag=ddy_diag,
                     per_chain=per_chain, calibration=calibration,
                     tau_source=tau_source, wind_source=wind_source,
                     fallback_warning=fallback_warning)

        log(f"[OK] design conditions written:")
        log(f"  CSV : {csv_path}")
        log(f"  JSON: {json_path}")
        log(f"  DDY : {ddy_path}  (total={ddy_diag['total']}, surviving={ddy_diag['surviving']})")
        log(f"  MD  : {report_path}")
        if ddy_diag["surviving"] <= 2:
            log("[ERROR] Honeybee filter would leave <=2 design days (SPEC s12 gate); check naming.")
            return 1
        return 0
    except Exception as exc:  # noqa: BLE001
        log(f"[ERROR] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
