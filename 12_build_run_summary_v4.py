#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
12_build_run_summary_v4.py

Build a frontend-readable run_summary.json from v4/v4.1 pipeline outputs.

v4.3 updates:
    - publishes output files to frontend/public/downloads/<run_id>/;
    - supports variable-switchable monthly diagnostics via monthly_variables;
    - supports reference-period monthly comparison when --monthly-reference-source is passed;
    - supports Wehrli-style CDF visualisation via selection_cdf when candidate daily
      summary + target files are passed;
    - keeps BPS metrics empty unless an explicit --bps-metrics-csv is passed;
    - marks CDF data provenance and filters CDF target distributions to common candidate chains where possible.
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import numpy as np
import pandas as pd

MONTH_LABELS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

PROFILE_INFO: Dict[str, Dict[str, str]] = {
    "seasonal_warm": {
        "label": "Seasonal heat burden",
        "metric": "seasonal_wcdh",
        "fallback_metric": "seasonal_cdh",
        "unit": "WCDH (°C²·h)",
        "objective": "Maximises warm-season CIBSE-style WCDH."
    },
    "peak_event": {
        "label": "Peak heat event",
        "metric": "peak_rolling_3day_cdh",
        "fallback_metric": "max_daily_tmax",
        "unit": "3-day CDH (°C·h)",
        "objective": "Maximises the worst short-window heat burden."
    },
    "sustained_heat": {
        "label": "Sustained heatwave",
        "metric": "heatwave_event_cdh",
        "fallback_metric": "longest_hot_spell_days",
        "unit": "event CDH (°C·h)",
        "objective": "Maximises the most severe single heatwave event."
    },
    "nocturnal_heat": {
        "label": "Nocturnal heat stress",
        "metric": "night_cdh20",
        "fallback_metric": "tropical_night_count",
        "unit": "night CDH20 (°C·h)",
        "objective": "Maximises nighttime cooling-degree-hours above 20°C."
    },
}

VARIABLE_META: Dict[str, Tuple[str, str]] = {
    "tas": ("Dry-bulb temperature", "°C"),
    "hurs": ("Relative humidity", "%"),
    "rsds": ("Global horizontal radiation", "W/m²"),
    "sfcWind": ("Wind speed", "m/s"),
    "windDir": ("Wind direction", "°"),
    "pres": ("Atmospheric pressure", "Pa"),
    "horizIR": ("Horizontal infrared radiation", "W/m²"),
    "cloudcover": ("Cloud cover", "oktas / tenths"),
    "dew_point_c": ("Dew point", "°C"),
    "ghi_wm2": ("Global horizontal radiation", "W/m²"),
    "dhi_wm2": ("Diffuse horizontal irradiance", "W/m²"),
    "dni_wm2": ("Direct normal irradiance", "W/m²"),
    "dry_bulb_c": ("Dry-bulb temperature", "°C"),
    "rh_pct": ("Relative humidity", "%"),
}


def read_json_optional(path: str | None) -> Dict[str, Any]:
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def read_csv_optional(path: str | None) -> pd.DataFrame:
    if not path:
        return pd.DataFrame()
    p = Path(path)
    if not p.exists():
        return pd.DataFrame()
    return pd.read_csv(p)


def parse_labeled_path(text: str) -> Tuple[str, Path]:
    if "=" not in text:
        p = Path(text)
        return p.stem, p
    label, path = text.split("=", 1)
    return label.strip(), Path(path.strip())


def file_size_kb(path: Path) -> int | None:
    if not path.exists():
        return None
    return max(1, int(round(path.stat().st_size / 1024)))


def safe_filename(label: str, path: Path) -> str:
    stem = "".join(ch.lower() if ch.isalnum() else "_" for ch in label).strip("_") or path.stem
    return f"{stem}{path.suffix}"


def build_files(entries: List[str], run_id: str, frontend_dir: Path | None, copy_downloads: bool) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    download_dir: Path | None = None
    if frontend_dir and copy_downloads:
        download_dir = frontend_dir / "public" / "downloads" / run_id
        download_dir.mkdir(parents=True, exist_ok=True)
    for entry in entries:
        label, path = parse_labeled_path(entry)
        ext = path.suffix.replace(".", "").upper() or "FILE"
        exists = path.exists()
        item: Dict[str, Any] = {
            "label": label,
            "type": ext,
            "path": str(path),
            "size_kb": file_size_kb(path),
            "status": "ready" if exists else "missing",
        }
        if exists and download_dir:
            dest = download_dir / safe_filename(label, path)
            shutil.copy2(path, dest)
            item["download_href"] = f"public/downloads/{run_id}/{dest.name}"
        out.append(item)
    return out


def standardize_weather_cols(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    aliases = {
        "dry_bulb_c": "tas",
        "tas_future": "tas",
        "rh_pct": "hurs",
        "hurs_future": "hurs",
        "ghi_wm2": "rsds",
        "rsds_future": "rsds",
        "pressure_pa": "pres",
        "wind_speed_mps": "sfcWind",
        "wind_dir_deg": "windDir",
        "horizontal_ir_wm2": "horizIR",
    }
    for src, dst in aliases.items():
        if src in df.columns and dst not in df.columns:
            df[dst] = df[src]
    return df


def numeric_monthly_means(path: str | None) -> pd.DataFrame:
    if not path or not Path(path).exists():
        return pd.DataFrame()
    df = standardize_weather_cols(pd.read_csv(path))
    if "month" not in df.columns:
        return pd.DataFrame()
    numeric_cols = [c for c in df.columns if c != "month" and pd.api.types.is_numeric_dtype(df[c])]
    keep = [c for c in numeric_cols if c in VARIABLE_META or c in {"tas", "hurs", "rsds", "sfcWind", "pres", "windDir", "horizIR", "cloudcover"}]
    if not keep:
        return pd.DataFrame()
    out = df.groupby("month", sort=True)[keep].mean(numeric_only=True).reset_index()
    out = pd.DataFrame({"month": range(1, 13)}).merge(out, on="month", how="left")
    return out


def _month_values(df: pd.DataFrame, col: str) -> List[float | None]:
    if df.empty or col not in df.columns:
        return []
    return [None if pd.isna(x) else round(float(x), 3) for x in df[col].tolist()]


def build_monthly_variables(selected_path: str | None, reference_path: str | None, external_weather_files: List[str] | None = None) -> Dict[str, Any]:
    sel = numeric_monthly_means(selected_path)
    ref = numeric_monthly_means(reference_path)
    externals: List[Tuple[str, pd.DataFrame, str]] = []
    for entry in external_weather_files or []:
        try:
            label, path = parse_labeled_path(entry)
            externals.append((label, numeric_monthly_means(str(path)), str(path)))
        except Exception:
            continue
    if sel.empty and not externals:
        return {}
    variables: Dict[str, Any] = {}
    cols = set(sel.columns if not sel.empty else []) | set(ref.columns if not ref.empty else [])
    for _, edf, _ in externals:
        cols |= set(edf.columns if not edf.empty else [])
    cols.discard("month")
    for col in sorted(cols):
        label, unit = VARIABLE_META.get(col, (col.replace("_", " ").title(), ""))
        selected = _month_values(sel, col)
        reference = _month_values(ref, col)
        series: List[Dict[str, Any]] = []
        if reference:
            series.append({"id": "reference", "label": "Reference 1991–2020", "role": "reference", "values": reference})
        if selected:
            series.append({"id": "fry", "label": "FRY / selected file", "role": "generated", "values": selected})
        for elabel, edf, epath in externals:
            vals = _month_values(edf, col)
            if vals:
                series.append({"id": elabel.lower().replace(" ", "_"), "label": elabel, "role": "external_benchmark", "values": vals, "path": epath})
        variables[col] = {"label": label, "unit": unit, "selected": selected, "reference": reference, "series": series}
    return variables


def monthly_arrays(selected_path: str | None) -> Tuple[List[float], List[float], List[List[float]]]:
    if not selected_path or not Path(selected_path).exists():
        return [], [], []
    df = standardize_weather_cols(pd.read_csv(selected_path))
    if not {"month", "tas", "rsds"}.issubset(df.columns):
        return [], [], []
    m = df.groupby("month", sort=True).agg(tas=("tas", "mean"), rsds=("rsds", "mean")).reset_index()
    m = pd.DataFrame({"month": range(1, 13)}).merge(m, on="month", how="left")
    temp = [None if pd.isna(x) else round(float(x), 2) for x in m["tas"]]
    rad = [None if pd.isna(x) else round(float(x), 2) for x in m["rsds"]]
    hourly: List[List[float | None]] = []
    if {"month", "hour", "tas"}.issubset(df.columns):
        matrix = df.pivot_table(index="month", columns="hour", values="tas", aggfunc="mean")
        for month in range(1, 13):
            row: List[float | None] = []
            for hour in range(24):
                if month in matrix.index and hour in matrix.columns:
                    val = matrix.loc[month, hour]
                    row.append(None if pd.isna(val) else round(float(val), 2))
                else:
                    row.append(None)
            hourly.append(row)
    return temp, rad, hourly


def infer_fry_selection_path(monthly_source: str | None) -> str | None:
    if not monthly_source:
        return None
    p = Path(monthly_source)
    cand = p.with_name(p.stem + "_selection" + p.suffix)
    return str(cand) if cand.exists() else None


def infer_fry_rankings_path(monthly_source: str | None) -> str | None:
    if not monthly_source:
        return None
    p = Path(monthly_source)
    cand = p.with_name(p.stem + "_rankings" + p.suffix)
    return str(cand) if cand.exists() else None


def build_selection_process(selection_csv: str | None) -> Dict[str, Any] | None:
    df = read_csv_optional(selection_csv)
    if df.empty or "month" not in df.columns:
        return None
    months: List[Dict[str, Any]] = []
    for _, r in df.sort_values("month").iterrows():
        month_idx = int(r.get("month", 0))
        fs_score = r.get("fs_sum", r.get("fs_tas", None))
        tiebreak = r.get("tiebreak_score", None)
        delta = f"≤ {float(tiebreak):.2f} K" if tiebreak is not None and not pd.isna(tiebreak) else ""
        reason = "Selected after FS shortlisting; official tasmax/tasmin resolves the final tie-break."
        if "rank_sum" in r:
            reason = f"rank_sum={float(r.get('rank_sum')):.0f}; {reason}"
        months.append({
            "month": MONTH_LABELS[month_idx - 1] if 1 <= month_idx <= 12 else str(month_idx),
            "selected_year": int(r.get("source_year", 0)),
            "model_chain": str(r.get("model_chain", "")),
            "fs_score": None if pd.isna(fs_score) else round(float(fs_score), 4),
            "tas_delta": delta,
            "reason": reason,
        })
    return {
        "method": "EN ISO 15927-4-inspired Finkelstein–Schafer monthly selection",
        "variables_ranked": ["tas", "hurs", "rsds"],
        "tiebreak": "Official CH2025 tasmax/tasmin, used only after FS shortlisting",
        "candidate_pool": "Future hourly archive: model_chain × source_year × month",
        "months": months,
    }


def cdf_series(values: Iterable[Any], max_points: int = 300) -> Dict[str, List[float]]:
    arr = pd.to_numeric(pd.Series(list(values)), errors="coerce").dropna().to_numpy(dtype=float)
    if len(arr) == 0:
        return {"x": [], "y": []}
    arr = np.sort(arr)
    y = np.arange(1, len(arr) + 1, dtype=float) / len(arr)
    if len(arr) > max_points:
        idx = np.unique(np.linspace(0, len(arr) - 1, max_points).astype(int))
        arr = arr[idx]
        y = y[idx]
    return {"x": [round(float(x), 4) for x in arr], "y": [round(float(v), 4) for v in y]}


def load_target_values(path: str | None, variable: str) -> pd.DataFrame:
    df = read_csv_optional(path)
    if df.empty:
        return df
    if "variable_id" in df.columns:
        df = df[df["variable_id"].astype(str) == variable].copy()
    return df


def build_selection_cdf(selection_csv: str | None, rankings_csv: str | None, candidate_daily_summary: str | None, target_tas: str | None, target_hurs: str | None, target_rsds: str | None) -> Dict[str, Any] | None:
    selection = read_csv_optional(selection_csv)
    rankings = read_csv_optional(rankings_csv)
    daily = read_csv_optional(candidate_daily_summary)
    if selection.empty or daily.empty:
        return None
    targets = {
        "tas": load_target_values(target_tas, "tas"),
        "hurs": load_target_values(target_hurs, "hurs"),
        "rsds": load_target_values(target_rsds, "rsds"),
    }
    col_map = {"tas": "tas_daily_mean", "hurs": "hurs_daily_mean", "rsds": "rsds_daily_mean"}
    common_chains = set(daily["model_chain"].dropna().astype(str).unique()) if "model_chain" in daily.columns else set()
    if common_chains:
        for key, df in list(targets.items()):
            if not df.empty and "model_chain" in df.columns:
                targets[key] = df[df["model_chain"].astype(str).isin(common_chains)].copy()
    payload: Dict[str, Any] = {
        "variables": ["tas", "hurs", "rsds"],
        "months": {},
        "provenance": {
            "generated_from_real_pipeline_outputs": True,
            "candidate_daily_summary": candidate_daily_summary,
            "selection_csv": selection_csv,
            "rankings_csv": rankings_csv,
            "target_files": {"tas": target_tas, "hurs": target_hurs, "rsds": target_rsds},
            "common_chain_count": len(common_chains),
        },
    }
    for _, sel in selection.sort_values("month").iterrows():
        month = int(sel["month"])
        month_key = str(month)
        payload["months"].setdefault(month_key, {})
        selected_mc = str(sel["model_chain"])
        selected_year = int(sel["source_year"])
        alt_rows = pd.DataFrame()
        if not rankings.empty:
            alt_rows = rankings[rankings["month"].astype(int) == month].sort_values(["rank_sum", "fs_sum"], kind="stable").head(5)
        for var, daily_col in col_map.items():
            target_df = targets[var]
            target_vals = target_df.loc[target_df["month"].astype(int) == month, "value"] if not target_df.empty and "month" in target_df.columns else []
            selected_vals = daily.loc[
                (daily["month"].astype(int) == month)
                & (daily["model_chain"].astype(str) == selected_mc)
                & (daily["year"].astype(int) == selected_year),
                daily_col,
            ]
            alternatives: List[Dict[str, Any]] = []
            if not alt_rows.empty:
                for _, a in alt_rows.iterrows():
                    mc = str(a["model_chain"]); yr = int(a["source_year"])
                    if mc == selected_mc and yr == selected_year:
                        continue
                    vals = daily.loc[(daily["month"].astype(int) == month) & (daily["model_chain"].astype(str) == mc) & (daily["year"].astype(int) == yr), daily_col]
                    if vals.empty:
                        continue
                    alternatives.append({
                        "label": f"{yr} · {mc}",
                        "source_year": yr,
                        "model_chain": mc,
                        **cdf_series(vals),
                    })
                    if len(alternatives) >= 3:
                        break
            payload["months"][month_key][var] = {
                "is_real": True,
                "target": {"label": "CH2025 target distribution", "n": int(pd.to_numeric(pd.Series(target_vals), errors="coerce").dropna().shape[0]), **cdf_series(target_vals)},
                "selected": {"label": f"Selected {selected_year} · {selected_mc}", "source_year": selected_year, "model_chain": selected_mc, "n": int(pd.to_numeric(pd.Series(selected_vals), errors="coerce").dropna().shape[0]), **cdf_series(selected_vals)},
                "alternatives": alternatives,
            }
    return payload


def records_from_csv(path: str | None) -> List[Dict[str, Any]]:
    df = read_csv_optional(path)
    if df.empty:
        return []
    return df.to_dict(orient="records")


def xmy_cards_from_selection(path: str | None) -> List[Dict[str, Any]]:
    df = read_csv_optional(path)
    if df.empty or "profile" not in df.columns:
        return []
    rows: List[Dict[str, Any]] = []
    for _, r in df.iterrows():
        profile = str(r.get("profile", ""))
        info = PROFILE_INFO.get(profile, {})
        metric = str(r.get("primary_metric", "")) if "primary_metric" in df.columns and not pd.isna(r.get("primary_metric")) else info.get("metric")
        value = r.get("primary_metric_value") if "primary_metric_value" in df.columns else None
        if value is None or pd.isna(value):
            if metric not in df.columns and info.get("fallback_metric") in df.columns:
                metric = info.get("fallback_metric")
            value = r.get(metric) if metric in df.columns else None
        if value is None or pd.isna(value):
            continue
        unit = str(r.get("metric_unit", "")) if "metric_unit" in df.columns and not pd.isna(r.get("metric_unit")) else info.get("unit", metric)
        objective = str(r.get("stress_function", "")) if "stress_function" in df.columns and not pd.isna(r.get("stress_function")) else info.get("objective", "Selected by predefined stress indicator.")
        rows.append({
            "profile": profile,
            "label": info.get("label", profile.replace("_", " ").title()),
            "score": round(float(value), 2),
            "unit": unit,
            "metric": metric,
            "criterion_type": str(r.get("criterion_type", "")) if "criterion_type" in df.columns and not pd.isna(r.get("criterion_type")) else "",
            "source_year": int(r.get("source_year")) if "source_year" in df.columns and not pd.isna(r.get("source_year")) else None,
            "model_chain": str(r.get("model_chain", "")),
            "objective": objective,
            "reason": objective,
            "implementation_note": str(r.get("implementation_note", "")) if "implementation_note" in df.columns and not pd.isna(r.get("implementation_note")) else "",
        })
    return rows


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build frontend run_summary.json from pipeline outputs.")
    p.add_argument("--station", default="SMA")
    p.add_argument("--station-name", default="Zürich / Fluntern")
    p.add_argument("--ref-state", default="ref91-20")
    p.add_argument("--target-state", default="gwl2.0")
    p.add_argument("--capability-json", default=None)
    p.add_argument("--weather-comparison-csv", default=None)
    p.add_argument("--xmy-selections-csv", default=None)
    p.add_argument("--fry-selection-csv", default=None)
    p.add_argument("--fry-rankings-csv", default=None)
    p.add_argument("--candidate-daily-summary", default=None)
    p.add_argument("--target-tas", default=None)
    p.add_argument("--target-hurs", default=None)
    p.add_argument("--target-rsds", default=None)
    p.add_argument("--bps-metrics-csv", default=None)
    p.add_argument("--monthly-source", default=None, help="Selected FRY/XMY CSV used to plot monthly values")
    p.add_argument("--monthly-reference-source", default=None, help="Reference hourly CSV used for 1991–2020 comparison")
    p.add_argument("--external-weather-files", nargs="*", default=[], help="label=path external benchmark weather tables used for frontend comparison only")
    p.add_argument("--files", nargs="*", default=[], help="label=path entries shown in Output files")
    p.add_argument("--frontend-dir", default="frontend")
    p.add_argument("--no-copy-downloads", action="store_true")
    p.add_argument("--output", required=True)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    run_id = f"run_{args.station.lower()}_{args.target_state.replace('.', '_')}"
    capability = read_json_optional(args.capability_json)
    monthly_temp, monthly_rad, hourly_temp = monthly_arrays(args.monthly_source)
    monthly_vars = build_monthly_variables(args.monthly_source, args.monthly_reference_source, args.external_weather_files)
    fry_selection_csv = args.fry_selection_csv or infer_fry_selection_path(args.monthly_source)
    fry_rankings_csv = args.fry_rankings_csv or infer_fry_rankings_path(args.monthly_source)
    frontend_dir = Path(args.frontend_dir) if args.frontend_dir and Path(args.frontend_dir).exists() else None
    files = build_files(args.files, run_id, frontend_dir, copy_downloads=not args.no_copy_downloads)

    payload = {
        "run_id": run_id,
        "data_origin": "pipeline_outputs",
        "is_mock": False,
        "station": {"id": args.station, "name": args.station_name, "capability_level": capability.get("capability_level", "Unknown"), "capability_reason": capability.get("technical_handling", capability.get("reason", "Capability metadata not provided."))},
        "scenario": {"ref_state": args.ref_state, "target_state": args.target_state, "calendar": "365_day / local standard time output", "selection_method": "FRY: FS over tas/hurs/rsds + official CH2025 tasmax/tasmin tie-break; XMY: profile-based stress selection"},
        "files": files,
        "external_benchmarks": [
            {"label": parse_labeled_path(entry)[0], "path": str(parse_labeled_path(entry)[1]), "positioning": "External published benchmark for weather-level comparison only; not generated by this pipeline and not treated as an EPW."}
            for entry in args.external_weather_files
        ],
        "sources": [
            {"name": "CH2025 DAILY-LOCAL", "description": "tas, hurs, rsds, tasmax, tasmin future scenario signals by GWL", "url": "https://www.meteoswiss.admin.ch/"},
            {"name": "MeteoSwiss hourly observations", "description": "historical hourly backbone and retained auxiliary variables", "url": "https://www.meteoswiss.admin.ch/"},
            {"name": "Machard-inspired solar decomposition", "description": "GHI to DHI/DNI EPW completion logic", "url": "https://www.nature.com/articles/s41597-024-03319-8"},
            {"name": "Wehrli-style auxiliary-variable handling", "description": "cloud cover is retained/interpolated rather than delta-changed", "url": "https://www.sciencedirect.com/"},
            {"name": "MeteoSwiss satellite Cloud Fractional Cover", "description": "optional gridded CFC auxiliary layer for Total Sky Cover when station visual cloud observations are unavailable", "url": "https://opendatadocs.meteoswiss.ch/c-climate-data/c4-satellite-based-climate-data"},
        ],
        "monthly_temperature": monthly_temp,
        "monthly_radiation": monthly_rad,
        "monthly_variables": monthly_vars,
        "hourly_temperature_by_month": hourly_temp,
        "selection_process": build_selection_process(fry_selection_csv),
        "selection_cdf": build_selection_cdf(fry_selection_csv, fry_rankings_csv, args.candidate_daily_summary, args.target_tas, args.target_hurs, args.target_rsds),
        "xmy_scores": xmy_cards_from_selection(args.xmy_selections_csv),
        "xmy_selection_cards": xmy_cards_from_selection(args.xmy_selections_csv),
        "weather_diagnostics": records_from_csv(args.weather_comparison_csv),
        "evaluation_metrics": records_from_csv(args.bps_metrics_csv),
        "assumptions": [
            "Candidate pool retains useful auxiliary variables, but final EPW completion is applied only to selected FRY/XMY files.",
            "Official CH2025 tasmax/tasmin are used only as FRY tie-break targets, not as primary FS variables.",
            "DHI/DNI are generated in the EPW completion layer from GHI using a Boland-Ridley-style diffuse-fraction model unless observed DHI is available.",
            "Cloud cover is retained/interpolated from visual observations or satellite CFC where available and used as the basis for Total Sky Cover; Opaque Sky Cover is proxy/fallback when no independent source exists.",
            "External published benchmarks such as Wehrli/SIA 2028 1-in-10 warm summer are used for frontend weather-level comparison only; they are not generated XMY outputs and are not complete EPW files.",
        ],
    }

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if frontend_dir and not args.no_copy_downloads:
        ddir = frontend_dir / "public" / "downloads" / run_id
        ddir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(out, ddir / "run_summary.json")
    print(f"Wrote {out}")
    if frontend_dir and not args.no_copy_downloads:
        print(f"Published downloads under {frontend_dir / 'public' / 'downloads' / run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
