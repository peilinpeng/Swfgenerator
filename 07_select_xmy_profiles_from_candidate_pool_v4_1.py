#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
07_select_xmy_profiles_from_candidate_pool_v4_1.py

Select fit-for-purpose XMY files from a future candidate archive.

v4.1.6 metric upgrade:
    - seasonal_warm: CIBSE-style warm-season WCDH. T_rm is computed over the
      full candidate year, while WCDH is evaluated only over the warm season.
    - peak_event: max rolling-window CDH over the warm season, replacing a
      single-point Tmax criterion.
    - sustained_heat: maximum single heatwave-event CDH, not annual sum of
      separated heatwave events.
    - nocturnal_heat: nighttime CDH above a configurable base temperature
      (default 20°C), keeping selection in the meteorological domain.

The script still selects full-year candidates (model_chain + source_year) and
streams the hourly archive only to extract selected XMY files.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import numpy as np
import pandas as pd

from pipeline_utils import log, read_table


def read_chunks(path: Path, chunksize: int):
    if path.suffix.lower() == ".parquet":
        yield pd.read_parquet(path)
    else:
        yield from pd.read_csv(path, chunksize=chunksize)


def save_csv(df: pd.DataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return path


def save_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def longest_consecutive_true(values: Iterable[bool]) -> int:
    best = 0
    cur = 0
    for v in values:
        if bool(v):
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return int(best)


def load_daily_summary(path: Path) -> pd.DataFrame:
    df = read_table(path)
    required = {
        "station_id", "model_chain", "ref_state", "target_state", "year", "month", "day",
        "tas_daily_mean", "tmax_daily", "tmin_daily", "cdh_daily_sum",
    }
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Candidate daily summary missing required columns: {sorted(missing)}")
    out = df.copy()
    for col in ["year", "month", "day"]:
        out[col] = pd.to_numeric(out[col], errors="coerce").astype("Int64")
    for col in ["tas_daily_mean", "tmax_daily", "tmin_daily", "cdh_daily_sum"]:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    return out.dropna(subset=["year", "month", "day"]).copy()


def compute_running_mean_temperature(daily_tmean: pd.Series, alpha: float = 0.8) -> pd.Series:
    """Compute CIBSE-style exponentially weighted running mean over a full year.

    Initialization uses the first day's mean temperature. For day i > 0:
        T_rm[i] = alpha * T_rm[i-1] + (1-alpha) * T_mean[i-1]
    """
    vals = pd.to_numeric(daily_tmean, errors="coerce").astype(float).to_numpy()
    trm = np.full(len(vals), np.nan, dtype=float)
    if len(vals) == 0:
        return pd.Series(trm, index=daily_tmean.index)
    if np.isnan(vals[0]):
        first_valid = vals[~np.isnan(vals)]
        prev = float(first_valid[0]) if len(first_valid) else np.nan
    else:
        prev = float(vals[0])
    trm[0] = prev
    for i in range(1, len(vals)):
        prev_tmean = vals[i - 1]
        if np.isnan(prev_tmean):
            prev_tmean = prev
        if np.isnan(prev):
            prev = prev_tmean
        else:
            prev = alpha * prev + (1.0 - alpha) * prev_tmean
        trm[i] = prev
    return pd.Series(trm, index=daily_tmean.index)


def add_adaptive_temperature_columns(daily: pd.DataFrame, alpha: float) -> pd.DataFrame:
    parts: List[pd.DataFrame] = []
    for (_chain, _year), grp in daily.groupby(["model_chain", "year"], sort=False):
        g = grp.sort_values(["month", "day"], kind="stable").copy()
        g["trm_full_year"] = compute_running_mean_temperature(g["tas_daily_mean"], alpha=alpha)
        g["tc_adaptive_c"] = 0.33 * g["trm_full_year"] + 18.8
        parts.append(g)
    if not parts:
        raise ValueError("No daily rows available for adaptive temperature calculation.")
    return pd.concat(parts, ignore_index=True)


def detect_heatwave_events(grp_d: pd.DataFrame, hot_mask: pd.Series, min_duration_days: int) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    start_idx: int | None = None
    hot_values = hot_mask.astype(bool).tolist()
    for i, is_hot in enumerate(hot_values + [False]):
        if is_hot and start_idx is None:
            start_idx = i
        elif (not is_hot) and start_idx is not None:
            end_idx = i - 1
            duration = end_idx - start_idx + 1
            if duration >= min_duration_days:
                block = grp_d.iloc[start_idx : end_idx + 1]
                event_cdh = float(pd.to_numeric(block["cdh_daily_sum"], errors="coerce").fillna(0).sum())
                events.append({
                    "start_month": int(block.iloc[0]["month"]),
                    "start_day": int(block.iloc[0]["day"]),
                    "end_month": int(block.iloc[-1]["month"]),
                    "end_day": int(block.iloc[-1]["day"]),
                    "duration_days": int(duration),
                    "event_cdh": event_cdh,
                })
            start_idx = None
    return events


def build_candidate_year_metrics(
    daily: pd.DataFrame,
    warm_months: List[int],
    hot_day_threshold: float,
    tropical_night_threshold: float,
    peak_window_days: int,
    peak_secondary_window_days: int,
    min_heatwave_duration_days: int,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    rows: List[Dict[str, Any]] = []
    day_lookup_cols = ["model_chain", "year", "month", "day", "tc_adaptive_c"]
    day_lookup = daily[day_lookup_cols].copy()
    day_lookup["warm_season"] = daily["month"].astype(int).isin(warm_months)

    warm_daily = daily[daily["month"].astype(int).isin(warm_months)].copy()
    if warm_daily.empty:
        raise ValueError("No warm-season daily rows found. Check warm month range.")

    for (chain, year), grp_full in daily.groupby(["model_chain", "year"], sort=False):
        grp_full = grp_full.sort_values(["month", "day"], kind="stable").reset_index(drop=True)
        grp_w = grp_full[grp_full["month"].astype(int).isin(warm_months)].copy().reset_index(drop=True)
        if grp_w.empty:
            continue

        hot_day = grp_w["tmax_daily"] >= hot_day_threshold
        tropical_night = grp_w["tmin_daily"] >= tropical_night_threshold
        heat_exceed = np.maximum(pd.to_numeric(grp_w["tmax_daily"], errors="coerce") - hot_day_threshold, 0)
        night_exceed = np.maximum(pd.to_numeric(grp_w["tmin_daily"], errors="coerce") - tropical_night_threshold, 0)

        peak_roll_primary = pd.to_numeric(grp_w["cdh_daily_sum"], errors="coerce").fillna(0).rolling(
            peak_window_days, min_periods=peak_window_days
        ).sum()
        peak_roll_secondary = pd.to_numeric(grp_w["cdh_daily_sum"], errors="coerce").fillna(0).rolling(
            peak_secondary_window_days, min_periods=peak_secondary_window_days
        ).sum()

        events = detect_heatwave_events(grp_w, hot_day, min_heatwave_duration_days)
        if events:
            max_event = max(events, key=lambda x: (x["event_cdh"], x["duration_days"]))
            heatwave_detected = True
            max_event_cdh = float(max_event["event_cdh"])
            event_duration_days = int(max_event["duration_days"])
            event_start = f"{max_event['start_month']:02d}-{max_event['start_day']:02d}"
            event_end = f"{max_event['end_month']:02d}-{max_event['end_day']:02d}"
        else:
            heatwave_detected = False
            max_event_cdh = 0.0
            event_duration_days = 0
            event_start = ""
            event_end = ""

        rows.append({
            "model_chain": str(chain),
            "source_year": int(year),
            # seasonal scores. seasonal_wcdh is filled from hourly archive later.
            "seasonal_wcdh": np.nan,
            "seasonal_cdh": float(pd.to_numeric(grp_w["cdh_daily_sum"], errors="coerce").fillna(0).sum()),
            "summer_cdh": float(pd.to_numeric(grp_w["cdh_daily_sum"], errors="coerce").fillna(0).sum()),
            "seasonal_mean_tas": float(grp_w["tas_daily_mean"].mean()),
            "summer_mean_tas": float(grp_w["tas_daily_mean"].mean()),
            "seasonal_max_tas": float(grp_w.get("max_hourly_tas", grp_w["tmax_daily"]).max()),
            "summer_mean_tmax": float(grp_w["tmax_daily"].mean()),
            "summer_mean_tmin": float(grp_w["tmin_daily"].mean()),
            "max_hourly_tas": float(grp_w.get("max_hourly_tas", grp_w["tmax_daily"]).max()),
            "max_daily_tmax": float(grp_w["tmax_daily"].max()),
            "max_3day_tmax_mean": float(grp_w["tmax_daily"].rolling(3, min_periods=3).mean().max()),
            # peak scores.
            "peak_rolling_3day_cdh": float(peak_roll_primary.max()) if len(peak_roll_primary.dropna()) else 0.0,
            "peak_rolling_5day_cdh": float(peak_roll_secondary.max()) if len(peak_roll_secondary.dropna()) else 0.0,
            "peak_max_daily_tmax": float(grp_w["tmax_daily"].max()),
            "peak_max_hourly_tas": float(grp_w.get("max_hourly_tas", grp_w["tmax_daily"]).max()),
            # sustained scores.
            "hot_day_count": int(hot_day.sum()),
            "longest_hot_spell_days": longest_consecutive_true(hot_day),
            "heatwave_detected": bool(heatwave_detected),
            "heatwave_event_cdh": float(max_event_cdh),
            "heatwave_event_duration_days": int(event_duration_days),
            "heatwave_event_start": event_start,
            "heatwave_event_end": event_end,
            "heatwave_severity_degree_days": float(heat_exceed.sum()),
            # nocturnal scores. night_cdh* are filled from hourly archive later.
            "tropical_night_count": int(tropical_night.sum()),
            "longest_tropical_night_spell": longest_consecutive_true(tropical_night),
            "night_heat_degree_days": float(night_exceed.sum()),
            "night_cdh20": np.nan,
            "night_cdh22": np.nan,
            "night_cdh26": np.nan,
            "night_mean_tas": np.nan,
            "night_max_tas": np.nan,
            "summer_rsds_total": float(grp_w["rsds_daily_sum"].sum()) if "rsds_daily_sum" in grp_w.columns else np.nan,
        })
    out = pd.DataFrame(rows)
    if out.empty:
        raise ValueError("No candidate-year metrics were built.")
    return out, day_lookup


def night_mask_from_hours(hours: pd.Series, start_hour: int, end_hour: int) -> pd.Series:
    h = pd.to_numeric(hours, errors="coerce").astype("Int64")
    if start_hour <= end_hour:
        return (h >= start_hour) & (h <= end_hour)
    return (h >= start_hour) | (h <= end_hour)


def enrich_metrics_from_hourly_archive(
    candidate_pool_path: Path,
    metrics: pd.DataFrame,
    day_lookup: pd.DataFrame,
    night_start_hour: int,
    night_end_hour: int,
    night_bases: List[float],
    chunksize: int,
) -> pd.DataFrame:
    """Add hourly-dependent scores: seasonal WCDH and nighttime CDH bases."""
    lookup = day_lookup.copy()
    lookup["year"] = lookup["year"].astype(int)
    lookup["month"] = lookup["month"].astype(int)
    lookup["day"] = lookup["day"].astype(int)
    partials: List[pd.DataFrame] = []

    for i, chunk in enumerate(read_chunks(candidate_pool_path, chunksize), start=1):
        if i == 1 or i % 5 == 0:
            log(f"      hourly metric chunk {i}")
        required = {"model_chain", "year", "month", "day", "hour", "tas_future"}
        missing = required - set(chunk.columns)
        if missing:
            raise ValueError(f"Candidate pool missing required columns for hourly XMY metrics: {sorted(missing)}")
        ch = chunk[["model_chain", "year", "month", "day", "hour", "tas_future"]].copy()
        for c in ["year", "month", "day", "hour"]:
            ch[c] = pd.to_numeric(ch[c], errors="coerce")
        ch["tas_future"] = pd.to_numeric(ch["tas_future"], errors="coerce")
        ch = ch.dropna(subset=["year", "month", "day", "hour", "tas_future"])
        if ch.empty:
            continue
        ch["year"] = ch["year"].astype(int)
        ch["month"] = ch["month"].astype(int)
        ch["day"] = ch["day"].astype(int)
        ch = ch.merge(lookup, on=["model_chain", "year", "month", "day"], how="left")
        ch["warm_season"] = ch["warm_season"].fillna(False).astype(bool)

        delta_adaptive = np.maximum(ch["tas_future"] - pd.to_numeric(ch["tc_adaptive_c"], errors="coerce"), 0.0)
        ch["seasonal_wcdh_part"] = np.where(ch["warm_season"], delta_adaptive ** 2, 0.0)

        nmask = night_mask_from_hours(ch["hour"], night_start_hour, night_end_hour) & ch["warm_season"]
        ch["night_tas_sum_part"] = np.where(nmask, ch["tas_future"], 0.0)
        ch["night_count_part"] = np.where(nmask, 1, 0)
        ch["night_max_part"] = np.where(nmask, ch["tas_future"], np.nan)
        for base in night_bases:
            label = f"night_cdh{int(base) if float(base).is_integer() else str(base).replace('.', '_')}"
            ch[f"{label}_part"] = np.where(nmask, np.maximum(ch["tas_future"] - float(base), 0.0), 0.0)

        agg_spec: Dict[str, Tuple[str, str]] = {
            "seasonal_wcdh": ("seasonal_wcdh_part", "sum"),
            "night_tas_sum": ("night_tas_sum_part", "sum"),
            "night_count": ("night_count_part", "sum"),
            "night_max_tas": ("night_max_part", "max"),
        }
        for base in night_bases:
            label = f"night_cdh{int(base) if float(base).is_integer() else str(base).replace('.', '_')}"
            agg_spec[label] = (f"{label}_part", "sum")
        part = ch.groupby(["model_chain", "year"], sort=False).agg(**agg_spec).reset_index()
        partials.append(part)

    if not partials:
        log("      [WARN] No hourly metric partials built; keeping daily-only XMY metrics.")
        return metrics

    tmp = pd.concat(partials, ignore_index=True)
    agg_spec2: Dict[str, Tuple[str, str]] = {
        "seasonal_wcdh": ("seasonal_wcdh", "sum"),
        "night_tas_sum": ("night_tas_sum", "sum"),
        "night_count": ("night_count", "sum"),
        "night_max_tas": ("night_max_tas", "max"),
    }
    for base in night_bases:
        label = f"night_cdh{int(base) if float(base).is_integer() else str(base).replace('.', '_')}"
        agg_spec2[label] = (label, "sum")
    hourly_scores = tmp.groupby(["model_chain", "year"], sort=False).agg(**agg_spec2).reset_index()
    hourly_scores["source_year"] = hourly_scores["year"].astype(int)
    hourly_scores["night_mean_tas"] = hourly_scores["night_tas_sum"] / hourly_scores["night_count"].replace(0, np.nan)
    hourly_scores = hourly_scores.drop(columns=["year", "night_tas_sum", "night_count"])

    out = metrics.merge(hourly_scores, on=["model_chain", "source_year"], how="left", suffixes=("", "_hourly"))
    # Prefer hourly-calculated columns when present.
    for col in ["seasonal_wcdh", "night_cdh20", "night_cdh22", "night_cdh26", "night_mean_tas", "night_max_tas"]:
        hcol = f"{col}_hourly"
        if hcol in out.columns:
            out[col] = out[hcol].combine_first(out[col])
            out = out.drop(columns=[hcol])
    return out


def profile_registry(peak_window_days: int, peak_secondary_window_days: int, night_base: float) -> Dict[str, Dict[str, Any]]:
    night_label = f"night_cdh{int(night_base) if float(night_base).is_integer() else str(night_base).replace('.', '_')}"
    return {
        "seasonal_warm": {
            "criterion_type": "aggregate seasonal heat burden",
            "test_perspective": "seasonal heat burden",
            "literature_families": ["CIBSE WCDH", "DSY", "pDSY", "HSY", "SRY"],
            "stress_function": "maximize warm-season CIBSE-style WCDH",
            "sort_by": [("seasonal_wcdh", False), ("seasonal_cdh", False), ("seasonal_mean_tas", False), ("seasonal_max_tas", False)],
            "required_summary_variables": ["tas_daily_mean", "cdh_daily_sum"],
            "primary_metric": "seasonal_wcdh",
            "unit": "WCDH (°C²·h)",
            "implementation_note": "T_rm is computed over the full candidate year; WCDH is evaluated over warm-season hours.",
        },
        "peak_event": {
            "criterion_type": "short-window extreme",
            "test_perspective": "short peak heat stress",
            "literature_families": ["CIBSE DSY2-like short intense warm spell"],
            "stress_function": f"maximize rolling {peak_window_days}-day CDH over the warm season",
            "sort_by": [(f"peak_rolling_{peak_window_days}day_cdh", False), (f"peak_rolling_{peak_secondary_window_days}day_cdh", False), ("peak_max_daily_tmax", False), ("peak_max_hourly_tas", False)],
            "required_summary_variables": ["cdh_daily_sum", "tmax_daily"],
            "primary_metric": f"peak_rolling_{peak_window_days}day_cdh",
            "unit": f"{peak_window_days}-day CDH (°C·h)",
            "implementation_note": "Fixed-base CDH is used to represent absolute short-window cooling stress.",
        },
        "sustained_heat": {
            "criterion_type": "duration threshold + event severity",
            "test_perspective": "prolonged heat accumulation",
            "literature_families": ["HWE", "HWY", "heatwave event files"],
            "stress_function": "maximize single heatwave-event CDH, not annual sum of separated events",
            "sort_by": [("heatwave_event_cdh", False), ("heatwave_event_duration_days", False), ("hot_day_count", False), ("seasonal_wcdh", False)],
            "required_summary_variables": ["tmax_daily", "cdh_daily_sum"],
            "primary_metric": "heatwave_event_cdh",
            "unit": "event CDH (°C·h)",
            "implementation_note": "Heatwave events are detected using daily Tmax threshold and minimum duration; score is maximum single-event CDH.",
        },
        "nocturnal_heat": {
            "criterion_type": "night-time cooling failure",
            "test_perspective": "night-time recovery failure",
            "literature_families": ["tropical-night indicators", "RSWY reviewed but not reproduced"],
            "stress_function": f"maximize nighttime CDH above {night_base:g}°C during the warm season",
            "sort_by": [(night_label, False), ("tropical_night_count", False), ("longest_tropical_night_spell", False), ("night_cdh26", False)],
            "required_summary_variables": ["tmin_daily"],
            "primary_metric": night_label,
            "unit": f"night CDH{night_base:g} (°C·h)",
            "implementation_note": "Meteorological metric for reduced passive night-cooling potential; no PET/t-SET physiology is used.",
        },
    }


def rank_for_profile(metrics: pd.DataFrame, profile_name: str, spec: Dict[str, Any]) -> Tuple[pd.DataFrame, pd.Series]:
    rank = metrics.copy()
    rank["profile"] = profile_name
    rank["criterion_type"] = spec["criterion_type"]
    rank["test_perspective"] = spec["test_perspective"]
    rank["stress_function"] = spec["stress_function"]
    rank["selection_unit"] = "full_year_candidate:model_chain+source_year"
    rank["primary_metric"] = spec["primary_metric"]
    rank["primary_metric_value"] = pd.to_numeric(rank.get(spec["primary_metric"], np.nan), errors="coerce")
    rank["metric_unit"] = spec.get("unit", "")
    rank["literature_anchor"] = "; ".join(spec.get("literature_families", []))
    rank["implementation_note"] = spec.get("implementation_note", "")

    by = [x[0] for x in spec["sort_by"]] + ["model_chain", "source_year"]
    ascending = [x[1] for x in spec["sort_by"]] + [True, True]
    missing_sort = [c for c in by if c not in rank.columns]
    if missing_sort:
        raise ValueError(f"Profile {profile_name} cannot be ranked because sort columns are missing: {missing_sort}")
    rank = rank.sort_values(by=by, ascending=ascending, kind="stable", na_position="last").reset_index(drop=True)
    rank["rank_within_profile"] = np.arange(1, len(rank) + 1)
    selected = rank.iloc[0].copy()
    rank["selected_flag"] = rank["rank_within_profile"] == 1
    return rank, selected


def choose_col(df: pd.DataFrame, candidates: List[str]) -> str | None:
    for c in candidates:
        if c in df.columns:
            return c
    return None


def stream_full_year_xmy(candidate_pool_path: Path, selected: pd.Series, profile_name: str, synthetic_year: int, chunksize: int) -> pd.DataFrame:
    chain = str(selected["model_chain"])
    year = int(selected["source_year"])
    blocks: List[pd.DataFrame] = []
    for chunk in read_chunks(candidate_pool_path, chunksize):
        sub = chunk[(chunk["model_chain"].astype(str) == chain) & (chunk["year"].astype(int) == year)].copy()
        if not sub.empty:
            blocks.append(sub)
    if not blocks:
        raise ValueError(f"No hourly data for selected profile={profile_name}, chain={chain}, year={year}")
    block = pd.concat(blocks, ignore_index=True).sort_values(["month", "day", "hour"], kind="stable").reset_index(drop=True)
    if len(block) != 8760:
        raise ValueError(f"Selected full-year XMY has {len(block)} rows; expected 8760 after leap-day removal.")
    dt = pd.to_datetime({"year": synthetic_year, "month": block["month"].astype(int), "day": block["day"].astype(int), "hour": block["hour"].astype(int)}, errors="raise")
    out = pd.DataFrame({
        "station_id": block["station_id"],
        "ref_state": block["ref_state"],
        "target_state": block["target_state"],
        "file_type": "XMY",
        "profile": profile_name,
        "source_model_chain": chain,
        "source_year": year,
        "datetime": dt,
        "month": block["month"],
        "day": block["day"],
        "hour": block["hour"],
        "tas": block["tas_future"],
        "hurs": block["hurs_future"],
        "rsds": block["rsds_future"],
    })
    wind_col = choose_col(block, ["sfcWind_future", "sfcWind_retained", "sfcWind"])
    if wind_col:
        out["sfcWind"] = block[wind_col]
    for out_name, candidates in {
        "pres": ["pres_retained", "pres_future", "pres"],
        "windDir": ["windDir_retained", "windDir_future", "windDir"],
        "horizIR": ["horizIR_retained", "horizIR_future", "horizIR"],
        "cloudcover": ["cloudcover_interpolated_future", "cloudcover_interpolated_retained", "cloudcover_retained", "cloudcover_future", "cloudcover"],
        "total_sky_cover_tenths": ["total_sky_cover_tenths_future", "total_sky_cover_tenths_retained", "total_sky_cover_tenths"],
        "opaque_sky_cover_tenths": ["opaque_sky_cover_tenths_future", "opaque_sky_cover_tenths_retained", "opaque_sky_cover_tenths"],
        "dhi_obs": ["dhi_retained", "dhi_future", "dhi"],
    }.items():
        col = choose_col(block, candidates)
        if col:
            out[out_name] = block[col]
    return out


def build_feasibility_table(registry: Dict[str, Dict[str, Any]], daily_columns: List[str]) -> pd.DataFrame:
    rows = []
    cols = set(daily_columns)
    for name, spec in registry.items():
        missing = [v for v in spec["required_summary_variables"] if v not in cols]
        rows.append({
            "profile": name,
            "criterion_type": spec["criterion_type"],
            "test_perspective": spec["test_perspective"],
            "literature_families": "; ".join(spec["literature_families"]),
            "stress_function": spec["stress_function"],
            "primary_metric": spec["primary_metric"],
            "required_summary_variables": "; ".join(spec["required_summary_variables"]),
            "missing_required_variables": "; ".join(missing),
            "implemented": len(missing) == 0,
            "selection_unit": "full_year_candidate:model_chain+source_year",
            "implementation_note": spec.get("implementation_note", ""),
        })
    return pd.DataFrame(rows)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Select fit-for-purpose XMY profiles from daily summary and candidate archive.")
    p.add_argument("--candidate-pool", required=True)
    p.add_argument("--candidate-daily-summary", required=True)
    p.add_argument("--output-dir", default="./data_processed/xmy")
    p.add_argument("--synthetic-year", type=int, default=2001)
    p.add_argument("--warm-month-start", type=int, default=6)
    p.add_argument("--warm-month-end", type=int, default=8)
    p.add_argument("--cdh-base-temperature-c", type=float, default=26.0)
    p.add_argument("--wcdh-alpha", type=float, default=0.8)
    p.add_argument("--hot-day-threshold-c", type=float, default=30.0)
    p.add_argument("--min-heatwave-duration-days", type=int, default=3)
    p.add_argument("--tropical-night-threshold-c", type=float, default=20.0)
    p.add_argument("--night-start-hour", type=int, default=22)
    p.add_argument("--night-end-hour", type=int, default=6)
    p.add_argument("--night-base-temperature-c", type=float, default=20.0)
    p.add_argument("--peak-window-days", type=int, default=3)
    p.add_argument("--peak-secondary-window-days", type=int, default=5)
    p.add_argument("--profiles", default="seasonal_warm,peak_event,sustained_heat,nocturnal_heat")
    p.add_argument("--chunksize", type=int, default=500_000)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    try:
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        candidate_pool_path = Path(args.candidate_pool)
        daily = load_daily_summary(Path(args.candidate_daily_summary))
        station_id = sorted(daily["station_id"].dropna().astype(str).unique().tolist())[0]
        ref_state = sorted(daily["ref_state"].dropna().astype(str).unique().tolist())[0]
        target_state = sorted(daily["target_state"].dropna().astype(str).unique().tolist())[0]
        warm_months = list(range(args.warm_month_start, args.warm_month_end + 1))

        registry = profile_registry(args.peak_window_days, args.peak_secondary_window_days, args.night_base_temperature_c)
        requested = [x.strip() for x in args.profiles.split(",") if x.strip()]
        if requested == ["all"]:
            requested = list(registry.keys())
        for prof in requested:
            if prof not in registry:
                raise ValueError(f"Unknown profile '{prof}'. Available: {sorted(registry)}")

        log("[1/6] Computing adaptive comfort baseline over full candidate years ...")
        daily_adapt = add_adaptive_temperature_columns(daily, alpha=args.wcdh_alpha)

        log("[2/6] Building candidate-year metrics from daily summary ...")
        metrics, day_lookup = build_candidate_year_metrics(
            daily_adapt,
            warm_months,
            args.hot_day_threshold_c,
            args.tropical_night_threshold_c,
            args.peak_window_days,
            args.peak_secondary_window_days,
            args.min_heatwave_duration_days,
        )

        log("[3/6] Adding hourly-dependent XMY metrics from candidate archive ...")
        night_bases = sorted(set([20.0, 22.0, 26.0, float(args.night_base_temperature_c)]))
        metrics = enrich_metrics_from_hourly_archive(
            candidate_pool_path,
            metrics,
            day_lookup,
            args.night_start_hour,
            args.night_end_hour,
            night_bases,
            args.chunksize,
        )

        base = f"xmy_profiles_{station_id}_{ref_state}_to_{target_state}_v4"
        metrics_path = output_dir / f"{base}_candidate_year_metrics.csv"
        save_csv(metrics, metrics_path)
        feasibility = build_feasibility_table(registry, list(daily.columns))
        feasibility_path = output_dir / f"{base}_algorithm_feasibility.csv"
        save_csv(feasibility, feasibility_path)

        log("[4/6] Ranking and selecting requested profiles ...")
        all_rankings: List[pd.DataFrame] = []
        selected_rows: List[pd.Series] = []
        xmy_paths: Dict[str, str] = {}
        for prof in requested:
            spec = registry[prof]
            missing = [v for v in spec["required_summary_variables"] if v not in daily.columns]
            if missing:
                log(f"      skipping {prof}: missing {missing}")
                continue
            rank, selected = rank_for_profile(metrics, prof, spec)
            all_rankings.append(rank)
            selected_rows.append(selected)
            log(f"      extracting profile={prof}, metric={selected.get('primary_metric')}={selected.get('primary_metric_value')}, chain={selected['model_chain']}, year={int(selected['source_year'])}")
            xmy = stream_full_year_xmy(candidate_pool_path, selected, prof, args.synthetic_year, args.chunksize)
            xmy_path = output_dir / f"xmy_{prof}_{station_id}_{ref_state}_to_{target_state}_v4.csv"
            save_csv(xmy, xmy_path)
            xmy_paths[prof] = str(xmy_path)

        if not selected_rows:
            raise ValueError("No XMY profiles could be selected.")
        selection_table = pd.DataFrame(selected_rows).reset_index(drop=True)
        rankings = pd.concat(all_rankings, ignore_index=True)
        selection_path = output_dir / f"{base}_selections.csv"
        rankings_path = output_dir / f"{base}_rankings.csv"
        save_csv(selection_table, selection_path)
        save_csv(rankings, rankings_path)

        meta = {
            "inputs": {"candidate_pool": args.candidate_pool, "candidate_daily_summary": args.candidate_daily_summary},
            "method": {
                "xmy_design": "definition-based, building-response-oriented meteorological XMY profiles",
                "selection_unit": "full-year candidate = model_chain + source_year",
                "profiles": {k: registry[k] for k in requested},
                "warm_months": warm_months,
                "cdh_base_temperature_c": args.cdh_base_temperature_c,
                "wcdh": {
                    "alpha": args.wcdh_alpha,
                    "trm_initialization": "first daily mean temperature of the candidate year",
                    "trm_calculation_period": "full year",
                    "evaluation_period": "warm season only",
                    "formula": "WCDH=sum(max(Tas_future - (0.33*T_rm+18.8),0)^2)",
                },
                "peak_window_days": args.peak_window_days,
                "peak_secondary_window_days": args.peak_secondary_window_days,
                "hot_day_threshold_c": args.hot_day_threshold_c,
                "min_heatwave_duration_days": args.min_heatwave_duration_days,
                "sustained_event_rule": "score is maximum single-event CDH among detected heatwave events; separated events are not summed",
                "tropical_night_threshold_c": args.tropical_night_threshold_c,
                "night_window": f"{args.night_start_hour:02d}:00-{args.night_end_hour:02d}:00 inclusive",
                "night_base_temperature_c": args.night_base_temperature_c,
                "daily_summary_used": True,
                "hourly_metric_enrichment": True,
                "hourly_extraction": "chunked_streaming",
            },
            "outputs": {
                "xmy_files": xmy_paths,
                "candidate_year_metrics": str(metrics_path),
                "algorithm_feasibility": str(feasibility_path),
                "selections": str(selection_path),
                "rankings": str(rankings_path),
            },
            "summary_metadata": {
                "station_id": station_id,
                "ref_state": ref_state,
                "target_state": target_state,
                "candidate_daily_rows": int(len(daily)),
                "candidate_year_count": int(len(metrics)),
                "selected_profile_count": len(xmy_paths),
            },
        }
        meta_path = output_dir / f"{base}.json"
        save_json(meta_path, meta)
        log("[6/6] Done.")
        log(f"Selections: {selection_path}")
        return 0
    except Exception as exc:
        log(f"[ERROR] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
