#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
09_write_epw_v4.py

Reference-based morphed EPW writer.

The reference EPW is the carrier of station metadata, header blocks, and all
non-morphed hourly fields. This writer deep-copies the reference file and
overwrites only explicitly declared morphed/derived variables from the selected
FRY/XMY hourly table produced upstream.
"""
from __future__ import annotations

import argparse
import json
import math
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

import pandas as pd

DATA_FIELD_COUNT = 35

EPW_35_FIELDS = [
    "year",
    "month",
    "day",
    "hour",
    "minute",
    "data_source_flags",
    "dry_bulb_temperature",
    "dew_point_temperature",
    "relative_humidity",
    "atmospheric_station_pressure",
    "extraterrestrial_horizontal_radiation",
    "extraterrestrial_direct_normal_radiation",
    "horizontal_infrared_radiation",
    "global_horizontal_radiation",
    "direct_normal_radiation",
    "diffuse_horizontal_radiation",
    "global_horizontal_illuminance",
    "direct_normal_illuminance",
    "diffuse_horizontal_illuminance",
    "zenith_luminance",
    "wind_direction",
    "wind_speed",
    "total_sky_cover",
    "opaque_sky_cover",
    "visibility",
    "ceiling_height",
    "present_weather_observation",
    "present_weather_codes",
    "precipitable_water",
    "aerosol_optical_depth",
    "snow_depth",
    "days_since_last_snowfall",
    "albedo",
    "liquid_precipitation_depth",
    "liquid_precipitation_quantity",
]

FIELD_INDEX = {name: i for i, name in enumerate(EPW_35_FIELDS)}

FIELD_POLICY = {
    "year": "inherit",
    "month": "inherit",
    "day": "inherit",
    "hour": "inherit",
    "minute": "inherit",
    "data_source_flags": "inherit",
    "dry_bulb_temperature": "morph",
    "dew_point_temperature": "derive_from_morphed_temperature_and_rh",
    "relative_humidity": "morph",
    "atmospheric_station_pressure": "inherit",
    "extraterrestrial_horizontal_radiation": "inherit",
    "extraterrestrial_direct_normal_radiation": "inherit",
    "horizontal_infrared_radiation": "inherit",
    "global_horizontal_radiation": "morph",
    "direct_normal_radiation": "derive_from_morphed_ghi",
    "diffuse_horizontal_radiation": "derive_from_morphed_ghi",
    "global_horizontal_illuminance": "inherit",
    "direct_normal_illuminance": "inherit",
    "diffuse_horizontal_illuminance": "inherit",
    "zenith_luminance": "inherit",
    "wind_direction": "inherit",
    "wind_speed": "inherit",
    "total_sky_cover": "inherit",
    "opaque_sky_cover": "inherit",
    "visibility": "inherit",
    "ceiling_height": "inherit",
    "present_weather_observation": "inherit",
    "present_weather_codes": "inherit",
    "precipitable_water": "inherit",
    "aerosol_optical_depth": "inherit",
    "snow_depth": "inherit",
    "days_since_last_snowfall": "inherit",
    "albedo": "inherit",
    "liquid_precipitation_depth": "inherit",
    "liquid_precipitation_quantity": "inherit",
}

DEFAULT_MORPHED_FIELDS = ["dry_bulb_temperature", "relative_humidity", "global_horizontal_radiation"]
DERIVED_FIELDS = ["dew_point_temperature", "direct_normal_radiation", "diffuse_horizontal_radiation"]
BR_A = 8.0
BR_B = 0.586
LOW_SUN_COS_ZENITH = 0.052336  # cos(87 deg); avoids unstable DNI near horizon.
HEADER_KEYS_REQUIRING_INHERITANCE = [
    "LOCATION",
    "DESIGN CONDITIONS",
    "TYPICAL/EXTREME PERIODS",
    "GROUND TEMPERATURES",
    "HOLIDAYS/DAYLIGHT SAVINGS",
    "DATA PERIODS",
]

MISSING_CODES = {
    "dry_bulb_temperature": {"99.9"},
    "dew_point_temperature": {"99.9"},
    "relative_humidity": {"999"},
    "atmospheric_station_pressure": {"999999"},
    "extraterrestrial_horizontal_radiation": {"9999"},
    "extraterrestrial_direct_normal_radiation": {"9999"},
    "horizontal_infrared_radiation": {"9999"},
    "global_horizontal_radiation": {"9999"},
    "direct_normal_radiation": {"9999"},
    "diffuse_horizontal_radiation": {"9999"},
    "global_horizontal_illuminance": {"999999"},
    "direct_normal_illuminance": {"999999"},
    "diffuse_horizontal_illuminance": {"999999"},
    "zenith_luminance": {"9999"},
    "wind_direction": {"999"},
    "wind_speed": {"999"},
    "total_sky_cover": {"99"},
    "opaque_sky_cover": {"99"},
    "visibility": {"9999"},
    "ceiling_height": {"99999"},
    "present_weather_observation": {"9"},
    "present_weather_codes": {"999999999"},
    "precipitable_water": {"999"},
    "aerosol_optical_depth": {".999", "0.999"},
    "snow_depth": {"999"},
    "days_since_last_snowfall": {"99"},
    "albedo": {"999"},
    "liquid_precipitation_depth": {"999"},
    "liquid_precipitation_quantity": {"999"},
}


class ValidationError(ValueError):
    pass


def log(msg: str) -> None:
    print(msg, flush=True)


def read_table(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path)


def parse_epw(path: Path) -> Tuple[List[str], List[List[str]]]:
    if not path.exists():
        raise FileNotFoundError(f"Reference EPW not found: {path}")
    header: List[str] = []
    rows: List[List[str]] = []
    in_data = False
    with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
        for raw in f:
            line = raw.rstrip("\n\r")
            parts = line.split(",")
            is_data = len(parts) == DATA_FIELD_COUNT and _is_int(parts[0]) and _is_int(parts[1]) and _is_int(parts[2])
            if is_data:
                in_data = True
            if in_data:
                if len(parts) != DATA_FIELD_COUNT:
                    raise ValidationError(f"EPW data row has {len(parts)} columns, expected 35: {line[:120]}")
                rows.append(parts)
            else:
                header.append(line)
    if len(rows) != 8760:
        raise ValidationError(f"Reference EPW has {len(rows)} data rows; expected 8760")
    return header, rows


def _is_int(value: str) -> bool:
    try:
        int(str(value).strip())
        return True
    except Exception:
        return False


def header_key(line: str) -> str:
    return line.split(",", 1)[0].strip().upper()


def header_map(header: Sequence[str]) -> Dict[str, str]:
    return {header_key(line): line for line in header}


def get_location(header: Sequence[str]) -> Dict[str, Any]:
    loc = header_map(header).get("LOCATION")
    if not loc:
        raise ValidationError("Reference EPW missing LOCATION header")
    parts = loc.split(",")
    if len(parts) < 10:
        raise ValidationError(f"Invalid LOCATION header: {loc}")
    return {
        "name": parts[1],
        "state": parts[2],
        "country": parts[3],
        "source": parts[4],
        "id": parts[5],
        "latitude": float(parts[6]),
        "longitude": float(parts[7]),
        "timezone": float(parts[8]),
        "elevation": float(parts[9]),
    }


def replace_or_append_header(header: List[str], key: str, new_line: str) -> None:
    key_upper = key.upper()
    for i, line in enumerate(header):
        if header_key(line) == key_upper:
            header[i] = new_line
            return
    header.append(new_line)


def update_comments(
    header: List[str],
    *,
    reference_epw: Path,
    output_epw: Path,
    station: Dict[str, Any],
    generated_file_type: str,
    scenario_label: str,
    gwl_label: str,
    method_label: str,
    morphed_fields: Sequence[str],
    derived_fields: Sequence[str],
    inherited_fields: Sequence[str],
    pipeline_version: str,
) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    comment_1 = (
        f'COMMENTS 1,"Reference-based morphed EPW; reference={reference_epw.name}; '
        f"station={station['name']} ({station['id']}); type={generated_file_type}; "
        f"scenario={scenario_label}; gwl={gwl_label}; method={method_label}; "
        f"generated_at={now}; pipeline_version={pipeline_version}\""
    )
    comment_2 = (
        'COMMENTS 2,"Policies: design conditions, ground temperatures, typical/extreme '
        "periods, pressure, sky cover, wind, illuminance, longwave, precipitation, "
        "snow, visibility, and present-weather fields inherited from reference unless "
        f"explicitly listed; morphed={list(morphed_fields)}; derived={list(derived_fields)}; "
        f"inherited_count={len(inherited_fields)}; output={output_epw.name}\""
    )
    replace_or_append_header(header, "COMMENTS 1", comment_1)
    replace_or_append_header(header, "COMMENTS 2", comment_2)


def parse_csv_list(text: str | None, default: Sequence[str]) -> List[str]:
    if text is None or not text.strip():
        return list(default)
    return [x.strip() for x in text.split(",") if x.strip()]


def load_completed_hourly(path: Path) -> pd.DataFrame:
    df = read_table(path).copy()
    required = {"month", "day", "hour", "dry_bulb_c", "dew_point_c", "rh_pct"}
    missing = required - set(df.columns)
    if missing:
        raise ValidationError(f"Completed hourly table missing required columns: {sorted(missing)}")
    if len(df) != 8760:
        raise ValidationError(f"Completed hourly table has {len(df)} rows; expected 8760")
    return df


def validate_field_policy() -> None:
    if set(FIELD_POLICY) != set(EPW_35_FIELDS):
        missing = set(EPW_35_FIELDS) - set(FIELD_POLICY)
        extra = set(FIELD_POLICY) - set(EPW_35_FIELDS)
        raise ValidationError(f"FIELD_POLICY must cover exactly EPW_35_FIELDS; missing={missing}, extra={extra}")


def extraterrestrial_normal_irradiance(dayofyear: int) -> float:
    return 1367.0 * (1.0 + 0.033 * math.cos(2 * math.pi * int(dayofyear) / 365.0))


def boland_ridley_diffuse_fraction(ghi: float, cos_zenith: float, dayofyear: int) -> float:
    if ghi <= 0.0 or cos_zenith <= 0.0:
        return 1.0
    i0h = extraterrestrial_normal_irradiance(dayofyear) * cos_zenith
    kt = max(0.0, min(1.4, ghi / max(i0h, 1.0)))
    kd = 1.0 / (1.0 + math.exp(BR_A * (kt - BR_B)))
    return max(0.05, min(1.0, kd))


def solar_zenith_from_record(rec: Dict[str, Any]) -> float:
    if "solar_zenith_deg" in rec and pd.notna(rec.get("solar_zenith_deg")):
        return float(rec["solar_zenith_deg"])
    if "solar_altitude_deg" in rec and pd.notna(rec.get("solar_altitude_deg")):
        return 90.0 - float(rec["solar_altitude_deg"])
    raise ValidationError("GHI morph requested but completed table lacks solar_zenith_deg or solar_altitude_deg")


def decompose_morphed_ghi(ghi_raw: float, zenith_deg: float, dayofyear: int) -> Tuple[float, float, float]:
    """Return physically stable GHI/DNI/DHI from morphed GHI.

    The diffuse fraction uses a compact Boland-Ridley / clearness-index logistic
    form. Very low sun is treated as all-diffuse to avoid DNI explosions from
    division by a tiny cos(zenith). A dynamic extraterrestrial-normal ceiling is
    used only as a physical consistency guard, never as a repeated flat cap.
    """
    ghi = max(0.0, float(ghi_raw))
    zenith = float(zenith_deg)
    cosz = max(0.0, math.cos(math.radians(zenith)))
    if zenith >= 90.0 or cosz <= 0.0 or ghi <= 0.0:
        return 0.0, 0.0, 0.0
    if cosz < LOW_SUN_COS_ZENITH:
        return ghi, 0.0, ghi

    kd = boland_ridley_diffuse_fraction(ghi, cosz, dayofyear)
    dhi = max(0.0, min(ghi, kd * ghi))
    dni = max(0.0, (ghi - dhi) / cosz)

    dni_limit = 0.95 * extraterrestrial_normal_irradiance(dayofyear)
    if dni > dni_limit:
        dni = dni_limit
        dhi = max(0.0, min(ghi, ghi - dni * cosz))
    return ghi, dni, dhi


def overlay_morphed_fields(
    reference_rows: Sequence[Sequence[str]],
    completed: pd.DataFrame,
    morphed_fields: Sequence[str],
) -> Tuple[List[List[str]], List[str]]:
    rows = deepcopy([list(r) for r in reference_rows])
    warnings: List[str] = []
    morphed = set(morphed_fields)
    allowed = {
        "dry_bulb_temperature",
        "relative_humidity",
        "global_horizontal_radiation",
        "wind_speed",
        "wind_direction",
    }
    unknown = morphed - allowed
    if unknown:
        raise ValidationError(f"Unsupported morphed field(s) for current writer: {sorted(unknown)}")

    if "global_horizontal_radiation" in morphed:
        needed = {"ghi_wm2"}
        missing = needed - set(completed.columns)
        if missing:
            raise ValidationError(f"GHI morph requested but completed table lacks solar columns: {sorted(missing)}")
        warnings.append("Solar radiation morphed: GHI overwritten from rsds/ghi_wm2; DHI/DNI derived by Boland-Ridley clearness-index decomposition.")
    if "wind_speed" in morphed and "wind_speed_ms" not in completed.columns:
        raise ValidationError("wind_speed morph requested but completed table lacks wind_speed_ms")
    if "wind_direction" in morphed and "wind_dir_deg" not in completed.columns:
        raise ValidationError("wind_direction morph requested but completed table lacks wind_dir_deg")

    for i, r in enumerate(completed.itertuples(index=False)):
        rec = r._asdict()
        ref = rows[i]
        if int(rec["month"]) != int(ref[FIELD_INDEX["month"]]) or int(rec["day"]) != int(ref[FIELD_INDEX["day"]]):
            raise ValidationError(
                f"Completed row {i} month/day {rec['month']}/{rec['day']} does not match reference "
                f"{ref[FIELD_INDEX['month']]}/{ref[FIELD_INDEX['day']]}"
            )
        # Upstream selected tables use 0-23 hours; EPW stores 1-24.
        if int(rec["hour"]) + 1 != int(ref[FIELD_INDEX["hour"]]):
            raise ValidationError(f"Completed row {i} hour {rec['hour']} does not match reference EPW hour {ref[FIELD_INDEX['hour']]}")

        if "dry_bulb_temperature" in morphed:
            ref[FIELD_INDEX["dry_bulb_temperature"]] = f"{float(rec['dry_bulb_c']):.1f}"
        if "relative_humidity" in morphed:
            ref[FIELD_INDEX["relative_humidity"]] = f"{max(0.0, min(100.0, float(rec['rh_pct']))):.0f}"
        if "dry_bulb_temperature" in morphed or "relative_humidity" in morphed:
            dew = min(float(rec["dew_point_c"]), float(ref[FIELD_INDEX["dry_bulb_temperature"]]))
            ref[FIELD_INDEX["dew_point_temperature"]] = f"{dew:.1f}"
        if "global_horizontal_radiation" in morphed:
            zenith = solar_zenith_from_record(rec)
            dt = pd.Timestamp(year=2001, month=int(rec["month"]), day=int(rec["day"]))
            ghi, dni, dhi = decompose_morphed_ghi(float(rec["ghi_wm2"]), zenith, int(dt.dayofyear))
            ref[FIELD_INDEX["global_horizontal_radiation"]] = f"{ghi:.0f}"
            ref[FIELD_INDEX["direct_normal_radiation"]] = f"{dni:.0f}"
            ref[FIELD_INDEX["diffuse_horizontal_radiation"]] = f"{dhi:.0f}"
        if "wind_speed" in morphed:
            ref[FIELD_INDEX["wind_speed"]] = f"{max(0.0, float(rec['wind_speed_ms'])):.1f}"
        if "wind_direction" in morphed:
            ref[FIELD_INDEX["wind_direction"]] = f"{float(rec['wind_dir_deg']) % 360.0:.0f}"
    return rows, warnings


def inherited_fields_for(morphed_fields: Sequence[str]) -> List[str]:
    non_inherited = set(morphed_fields) | set(DERIVED_FIELDS)
    if "global_horizontal_radiation" in non_inherited:
        non_inherited.update({"direct_normal_radiation", "diffuse_horizontal_radiation"})
    return [f for f in EPW_35_FIELDS if f not in non_inherited]


def count_missing(values: Iterable[str], field: str) -> int:
    codes = MISSING_CODES.get(field, set())
    return sum(1 for v in values if str(v).strip() in codes)


def col(rows: Sequence[Sequence[str]], field: str) -> List[str]:
    return [r[FIELD_INDEX[field]] for r in rows]


def numeric_col(rows: Sequence[Sequence[str]], field: str) -> List[float]:
    out = []
    for i, v in enumerate(col(rows, field)):
        try:
            out.append(float(v))
        except Exception as exc:
            raise ValidationError(f"Non-numeric value in {field} at row {i}: {v}") from exc
    return out


def validate_header(reference_header: Sequence[str], output_header: Sequence[str]) -> List[str]:
    errors: List[str] = []
    ref = header_map(reference_header)
    out = header_map(output_header)
    for key in HEADER_KEYS_REQUIRING_INHERITANCE:
        if key not in ref:
            errors.append(f"Reference header missing {key}")
            continue
        if key not in out:
            errors.append(f"Output header missing {key}")
            continue
        if key not in {"COMMENTS 1", "COMMENTS 2"} and out[key] != ref[key]:
            # COMMENTS are not in HEADER_KEYS_REQUIRING_INHERITANCE; this is an explicit guard.
            errors.append(f"Header {key} changed unexpectedly")
    for count_key in ["DESIGN CONDITIONS", "TYPICAL/EXTREME PERIODS", "GROUND TEMPERATURES"]:
        if count_key in ref and count_key in out:
            ref_count = _safe_count(ref[count_key])
            out_count = _safe_count(out[count_key])
            if ref_count > 0 and out_count == 0:
                errors.append(f"{count_key} count is 0 in output but {ref_count} in reference")
            if ref_count != out_count:
                errors.append(f"{count_key} count differs: output={out_count}, reference={ref_count}")
    return errors


def _safe_count(line: str) -> int:
    parts = line.split(",")
    if len(parts) < 2:
        return 0
    try:
        return int(float(parts[1].strip()))
    except Exception:
        return 0


def suspicious_pressure_blocks(values: Sequence[float], elevation_m: float) -> bool:
    count_101325 = sum(1 for v in values if abs(v - 101325.0) < 0.5)
    if elevation_m > 100.0 and count_101325 >= 24:
        return True
    return count_101325 >= 168


def validate_rows(
    reference_rows: Sequence[Sequence[str]],
    output_rows: Sequence[Sequence[str]],
    *,
    inherited_fields: Sequence[str],
    morphed_fields: Sequence[str],
    completed: pd.DataFrame,
    station: Dict[str, Any],
) -> Tuple[List[str], List[str], Dict[str, Any]]:
    errors: List[str] = []
    warnings: List[str] = []
    diagnostics: Dict[str, Any] = {"missing_counts": {}, "field_ranges": {}}
    if len(output_rows) != 8760:
        errors.append(f"Output has {len(output_rows)} rows; expected 8760")
    for i, row in enumerate(output_rows):
        if len(row) != DATA_FIELD_COUNT:
            errors.append(f"Output row {i} has {len(row)} columns; expected 35")
        try:
            hour = int(row[FIELD_INDEX["hour"]])
            minute = int(row[FIELD_INDEX["minute"]])
            if not (1 <= hour <= 24):
                errors.append(f"Output row {i} has invalid EPW hour {hour}")
            if minute != 0:
                errors.append(f"Output row {i} has minute {minute}; expected 0")
        except Exception:
            errors.append(f"Output row {i} has invalid hour/minute")

    for field in inherited_fields:
        if col(output_rows, field) != col(reference_rows, field):
            errors.append(f"Inherited field changed unexpectedly: {field}")

    for field in EPW_35_FIELDS:
        ref_missing = count_missing(col(reference_rows, field), field)
        out_missing = count_missing(col(output_rows, field), field)
        diagnostics["missing_counts"][field] = {"reference": ref_missing, "output": out_missing}
        if out_missing > ref_missing:
            errors.append(f"New missing values introduced in {field}: {out_missing} > {ref_missing}")

    pressure = numeric_col(output_rows, "atmospheric_station_pressure")
    diagnostics["field_ranges"]["atmospheric_station_pressure"] = {"min": min(pressure), "max": max(pressure)}
    if suspicious_pressure_blocks(pressure, float(station["elevation"])):
        errors.append("Suspicious pressure fallback detected: repeated 101325 Pa at non-sea-level station")

    total_sky = numeric_col(output_rows, "total_sky_cover")
    opaque_sky = numeric_col(output_rows, "opaque_sky_cover")
    ref_total_sky = numeric_col(reference_rows, "total_sky_cover")
    ref_opaque_sky = numeric_col(reference_rows, "opaque_sky_cover")
    diagnostics["sky_cover_distribution"] = {
        "total_unique": sorted(set(total_sky))[:20],
        "opaque_unique": sorted(set(opaque_sky))[:20],
        "total_unique_count": len(set(total_sky)),
        "opaque_unique_count": len(set(opaque_sky)),
    }
    inherited_sky = "total_sky_cover" in inherited_fields and "opaque_sky_cover" in inherited_fields
    inherited_sky_violations = 0
    for i, (t, o, rt, ro) in enumerate(zip(total_sky, opaque_sky, ref_total_sky, ref_opaque_sky)):
        if not (0 <= t <= 10) or not (0 <= o <= 10):
            errors.append(f"Sky cover outside EPW 0-10 scale at row {i}: total={t}, opaque={o}")
            break
        if o > t:
            if inherited_sky and o == ro and t == rt:
                inherited_sky_violations += 1
            else:
                errors.append(f"Opaque sky cover exceeds total sky cover at row {i}: opaque={o}, total={t}")
                break
    if inherited_sky_violations:
        warnings.append(
            f"Reference-inherited sky cover has opaque > total in {inherited_sky_violations} rows; "
            "preserved unchanged because sky cover policy is inherit_reference."
        )
    if set(total_sky).issubset({0.0, 1.0}) and len(set(total_sky)) <= 2:
        errors.append("Total sky cover appears to be binary/fractional 0-1 values, not EPW 0-10 tenths")

    dry = numeric_col(output_rows, "dry_bulb_temperature")
    dew = numeric_col(output_rows, "dew_point_temperature")
    rh = numeric_col(output_rows, "relative_humidity")
    diagnostics["field_ranges"]["dry_bulb_temperature"] = {"min": min(dry), "max": max(dry)}
    diagnostics["field_ranges"]["dew_point_temperature"] = {"min": min(dew), "max": max(dew)}
    diagnostics["field_ranges"]["relative_humidity"] = {"min": min(rh), "max": max(rh)}
    for i, (db, dp, relh) in enumerate(zip(dry, dew, rh)):
        if dp > db + 0.2:
            errors.append(f"Dew point exceeds dry bulb at row {i}: dp={dp}, db={db}")
            break
        if not (0 <= relh <= 100):
            errors.append(f"Relative humidity outside 0-100 at row {i}: {relh}")
            break

    ghi = numeric_col(output_rows, "global_horizontal_radiation")
    dni = numeric_col(output_rows, "direct_normal_radiation")
    dhi = numeric_col(output_rows, "diffuse_horizontal_radiation")
    diagnostics["field_ranges"]["global_horizontal_radiation"] = {"min": min(ghi), "max": max(ghi)}
    diagnostics["field_ranges"]["direct_normal_radiation"] = {"min": min(dni), "max": max(dni)}
    diagnostics["field_ranges"]["diffuse_horizontal_radiation"] = {"min": min(dhi), "max": max(dhi)}
    if min(ghi) < 0 or min(dni) < 0 or min(dhi) < 0:
        errors.append("Solar radiation contains negative values")
    if "global_horizontal_radiation" in morphed_fields:
        if "solar_zenith_deg" not in completed.columns and "solar_altitude_deg" not in completed.columns:
            errors.append("Solar validation requires solar_zenith_deg or solar_altitude_deg in completed table")
        else:
            zeniths = []
            for rec in completed.itertuples(index=False):
                zeniths.append(solar_zenith_from_record(rec._asdict()))
            night_nonzero = 0
            dhi_gt_ghi = 0
            closure_errors: List[float] = []
            closure_failures = 0
            low_sun_hours = 0
            for g, n, d, z in zip(ghi, dni, dhi, zeniths):
                cosz = max(0.0, math.cos(math.radians(float(z))))
                if z >= 90.0 or cosz <= 0.0:
                    if abs(g) > 0.5 or abs(n) > 0.5 or abs(d) > 0.5:
                        night_nonzero += 1
                    continue
                if cosz < LOW_SUN_COS_ZENITH:
                    low_sun_hours += 1
                if d > g + 0.5:
                    dhi_gt_ghi += 1
                closure = abs(g - (d + n * cosz))
                closure_errors.append(closure)
                tolerance = max(5.0, 0.03 * max(g, 1.0))
                if closure > tolerance:
                    closure_failures += 1
            cap_1200_hits = sum(1 for v in dni if abs(v - 1200.0) < 0.5)
            max_dni = max(dni)
            repeated_max_dni = sum(1 for v in dni if abs(v - max_dni) < 0.5)
            diagnostics["solar_summary"] = {
                "policy": "morph_ghi_derive_dhi_dni_boland_ridley",
                "ghi_min": min(ghi),
                "ghi_max": max(ghi),
                "dhi_min": min(dhi),
                "dhi_max": max(dhi),
                "dni_min": min(dni),
                "dni_max": max_dni,
                "night_nonzero_hours": night_nonzero,
                "dhi_greater_than_ghi_hours": dhi_gt_ghi,
                "low_sun_hours": low_sun_hours,
                "closure_error_max_wm2": max(closure_errors) if closure_errors else 0.0,
                "closure_error_mean_wm2": sum(closure_errors) / len(closure_errors) if closure_errors else 0.0,
                "closure_failure_hours": closure_failures,
                "dni_1200_cap_hits": cap_1200_hits,
                "dni_repeated_max_count": repeated_max_dni,
            }
            if night_nonzero:
                errors.append(f"Solar validation failed: nighttime GHI/DNI/DHI nonzero in {night_nonzero} hours")
            if dhi_gt_ghi:
                errors.append(f"Solar validation failed: DHI exceeds GHI in {dhi_gt_ghi} hours")
            if closure_failures:
                errors.append(f"Solar validation failed: GHI closure error exceeds tolerance in {closure_failures} hours")
            if cap_1200_hits:
                errors.append(f"Solar validation failed: DNI hits suspicious 1200 W/m2 cap in {cap_1200_hits} hours")
            if repeated_max_dni >= 12 and max_dni >= 1199.5:
                errors.append(f"Solar validation failed: repeated artificial DNI cap suspected at {max_dni:.0f} W/m2 in {repeated_max_dni} hours")
            elif repeated_max_dni > 1 and max_dni >= 1199.5:
                warnings.append(f"Solar diagnostic: DNI maximum {max_dni:.0f} W/m2 repeats in {repeated_max_dni} hours; below cap-failure threshold.")
    else:
        warnings.append("Solar radiation inherited from reference EPW; future GHI signal is not applied in this output.")

    return errors, warnings, diagnostics


def write_epw(path: Path, header: Sequence[str], rows: Sequence[Sequence[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        for line in header:
            f.write(line + "\n")
        for row in rows:
            f.write(",".join(str(x) for x in row) + "\n")


def write_validation_markdown(path: Path, metadata: Dict[str, Any]) -> None:
    lines = [
        "# EPW Validation Report",
        "",
        f"- Reference EPW: `{metadata['reference_epw']}`",
        f"- Output EPW: `{metadata['output_epw']}`",
        f"- Passed: `{metadata['validation']['passed']}`",
        "",
        "## Field Policy",
        "",
    ]
    for field, policy in metadata["field_policy"].items():
        lines.append(f"- `{field}`: `{policy}`")
    lines.extend(["", "## Morphed Fields", "", ", ".join(f"`{f}`" for f in metadata["morphed_fields"]) or "None"])
    lines.extend(["", "## Derived Fields", "", ", ".join(f"`{f}`" for f in metadata["derived_fields"]) or "None"])
    lines.extend(["", "## Validation Errors", ""])
    lines.extend([f"- {e}" for e in metadata["validation"]["errors"]] or ["None"])
    lines.extend(["", "## Validation Warnings", ""])
    lines.extend([f"- {w}" for w in metadata["validation"]["warnings"]] or ["None"])
    solar_summary = metadata["validation"].get("diagnostics", {}).get("solar_summary")
    if solar_summary:
        lines.extend(["", "## Solar Summary", "", "```json", json.dumps(solar_summary, indent=2), "```"])
    lines.extend(["", "## Diagnostics", "", "```json", json.dumps(metadata["validation"].get("diagnostics", {}), indent=2), "```", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Write reference-based morphed EPW from selected hourly table v4.")
    p.add_argument("--completed-hourly", required=True)
    p.add_argument("--reference-epw", required=True, help="Mandatory reference EPW used as header/hourly carrier")
    p.add_argument("--output-epw", required=True)
    p.add_argument("--station-metadata", default=None, help="Deprecated; retained for CLI compatibility and not used to override reference LOCATION")
    p.add_argument("--morphed-fields", default=",".join(DEFAULT_MORPHED_FIELDS), help="Comma-separated EPW field names to overwrite")
    p.add_argument("--scenario-label", default="unspecified")
    p.add_argument("--gwl-label", default="unspecified")
    p.add_argument("--method-label", default="reference_based_morphed_epw")
    p.add_argument("--generated-file-type", default="FRY")
    p.add_argument("--pipeline-version", default="Swfgenerator-main")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    try:
        validate_field_policy()
        completed_path = Path(args.completed_hourly)
        reference_epw = Path(args.reference_epw)
        output_epw = Path(args.output_epw)
        completed = load_completed_hourly(completed_path)
        ref_header, ref_rows = parse_epw(reference_epw)
        output_header = deepcopy(ref_header)
        station = get_location(ref_header)
        morphed_fields = parse_csv_list(args.morphed_fields, DEFAULT_MORPHED_FIELDS)
        output_rows, overlay_warnings = overlay_morphed_fields(ref_rows, completed, morphed_fields)
        inherited_fields = inherited_fields_for(morphed_fields)
        update_comments(
            output_header,
            reference_epw=reference_epw,
            output_epw=output_epw,
            station=station,
            generated_file_type=args.generated_file_type,
            scenario_label=args.scenario_label,
            gwl_label=args.gwl_label,
            method_label=args.method_label,
            morphed_fields=morphed_fields,
            derived_fields=DERIVED_FIELDS,
            inherited_fields=inherited_fields,
            pipeline_version=args.pipeline_version,
        )
        header_errors = validate_header(ref_header, output_header)
        row_errors, row_warnings, diagnostics = validate_rows(
            ref_rows,
            output_rows,
            inherited_fields=inherited_fields,
            morphed_fields=morphed_fields,
            completed=completed,
            station=station,
        )
        errors = header_errors + row_errors
        warnings = overlay_warnings + row_warnings
        metadata = {
            "reference_epw": str(reference_epw),
            "output_epw": str(output_epw),
            "station": station,
            "generation": {
                "method": "reference_based_morphed_epw",
                "file_type": args.generated_file_type,
                "scenario": args.scenario_label,
                "gwl": args.gwl_label,
                "morphing_method": args.method_label,
                "pipeline_version": args.pipeline_version,
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
            "field_policy": FIELD_POLICY,
            "morphed_fields": morphed_fields,
            "derived_fields": DERIVED_FIELDS,
            "inherited_fields": inherited_fields,
            "design_conditions_policy": "inherit_reference",
            "ground_temperatures_policy": "inherit_reference",
            "typical_extreme_periods_policy": "inherit_reference",
            "pressure_policy": "inherit_reference",
            "solar_radiation_policy": "morph_ghi_from_rsds_and_derive_dhi_dni_boland_ridley",
            "sky_cover_policy": "inherit_reference",
            "validation": {"passed": not errors, "errors": errors, "warnings": warnings, "diagnostics": diagnostics},
        }
        if errors:
            meta_path = output_epw.with_suffix(output_epw.suffix + ".metadata.json")
            report_path = output_epw.with_suffix(output_epw.suffix + ".validation.md")
            meta_path.parent.mkdir(parents=True, exist_ok=True)
            meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
            write_validation_markdown(report_path, metadata)
            raise ValidationError("; ".join(errors[:8]))
        write_epw(output_epw, output_header, output_rows)
        output_epw.with_suffix(output_epw.suffix + ".metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        write_validation_markdown(output_epw.with_suffix(output_epw.suffix + ".validation.md"), metadata)
        log(f"Reference-based EPW written: {output_epw}")
        return 0
    except Exception as exc:
        log(f"[ERROR] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
