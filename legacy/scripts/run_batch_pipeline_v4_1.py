#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_batch_pipeline_v4_1.py

Batch runner for multiple stations × GWLs × XMY profiles.

The runner orchestrates the existing CLI scripts. It performs no manual downloads:
CH2025 and MeteoSwiss hourly data are fetched through their STAC/file APIs.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import List


def log(msg: str) -> None:
    print(msg, flush=True)


# Directory holding this script and its sibling numbered stage scripts. After the
# package refactor these live in legacy/scripts/, so child scripts must be invoked
# by their absolute sibling path rather than a CWD-relative bare name. Data/output
# paths remain relative to the working directory (project root), unchanged.
_SCRIPT_DIR = Path(__file__).resolve().parent


def _resolve_script_in_cmd(cmd: List[str]) -> List[str]:
    """Rewrite a `[python, "NN_stage.py", ...]` command so the script is resolved
    against this file's directory (robust to the working directory and the move
    into legacy/scripts/). Leaves already-absolute paths and non-.py args alone."""
    if len(cmd) >= 2 and isinstance(cmd[1], str) and cmd[1].endswith(".py"):
        p = Path(cmd[1])
        if not p.is_absolute():
            cmd = [cmd[0], str(_SCRIPT_DIR / cmd[1]), *cmd[2:]]
    return cmd


def run(cmd: List[str], dry_run: bool = False) -> None:
    cmd = _resolve_script_in_cmd(cmd)
    log("\n$ " + " ".join(cmd))
    if dry_run:
        return
    subprocess.run(cmd, check=True)


def run_optional(cmd: List[str], dry_run: bool = False) -> bool:
    """Run an optional auxiliary step. Return False instead of aborting on failure."""
    cmd = _resolve_script_in_cmd(cmd)
    log("\n$ " + " ".join(cmd))
    if dry_run:
        return True
    proc = subprocess.run(cmd, check=False)
    return proc.returncode == 0


def split_csv(text: str) -> List[str]:
    return [x.strip() for x in text.split(",") if x.strip()]


def py() -> str:
    return sys.executable or "python3"


def load_station_catalog() -> dict:
    path = Path("frontend/data/stations_catalog.json")
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def station_metadata_from_catalog(station: str) -> dict | None:
    catalog = load_station_catalog()
    for row in catalog.get("stations", []):
        code = str(row.get("code") or row.get("id") or row.get("value") or "").lower()
        if code == station.lower():
            lat = row.get("latitude")
            lon = row.get("longitude")
            elev = row.get("elevation") or row.get("height") or row.get("altitude") or row.get("elevation_m")
            if lat is None or lon is None:
                return None
            return {
                "city": row.get("name") or row.get("label") or station.upper(),
                "state": "",
                "country": "CHE",
                "source": "CH2025/MeteoSwiss station catalog",
                "wmo": str(row.get("wmo") or ""),
                "latitude": float(lat),
                "longitude": float(lon),
                "timezone": 1.0,
                "elevation": float(elev) if elev is not None else 0.0,
                "comments_1": "Auto-generated station metadata from frontend/data/stations_catalog.json",
            }
    return None


def ensure_station_metadata(station: str, dry_run: bool = False) -> Path:
    Path("station_metadata").mkdir(exist_ok=True)
    station_meta = Path(f"station_metadata/{station}.json")
    if station_meta.exists():
        return station_meta
    meta = station_metadata_from_catalog(station)
    if meta is None and not dry_run:
        # Try to auto-refresh the CH2025 station catalog once; this avoids manual
        # station-metadata creation for batch runs. Elevation is not always present
        # in the CH2025 STAC item; if absent, station_metadata_from_catalog uses 0 m.
        try:
            subprocess.run(_resolve_script_in_cmd([py(), "00_discover_ch2025_stations_v4_1.py", "--output", "frontend/data/stations_catalog.json"]), check=True)
            meta = station_metadata_from_catalog(station)
        except Exception:
            meta = None
    if meta is None and station.lower() == "sma":
        meta = {"city":"Zürich / Fluntern","state":"ZH","country":"CHE","source":"bundled fallback","wmo":"06660","latitude":47.3779,"longitude":8.5657,"timezone":1.0,"elevation":556.0}
    if meta is None:
        raise RuntimeError(f"Missing station metadata for {station.upper()}. Could not derive it from frontend/data/stations_catalog.json; provide station_metadata/{station}.json before EPW export.")
    if not dry_run:
        station_meta.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return station_meta


def expected_ch2025_cache(station: str, var: str, state: str) -> str:
    return f"cache/ch2025/{station}/{var}/{state}/ogd-climate-scenarios-ch2025_{station}_{var}_{state}.csv"


def parsed_ch2025(station: str, var: str, state: str) -> str:
    return f"data_processed/ch2025_daily/ch2025_daily_{station}_{var}_{state}.csv"


def daily_signal(station: str, var: str, ref_state: str, target_state: str) -> str:
    return f"data_processed/daily_signal/daily_signal_{station}_{var}_{ref_state}_to_{target_state}.csv"


def main() -> int:
    p = argparse.ArgumentParser(description="Run batch CH2025 EPW pipeline v4.1.")
    p.add_argument("--stations", default="sma", help="Comma-separated station IDs, e.g. sma,bas,ber")
    p.add_argument("--gwls", default="gwl2.0", help="Comma-separated GWL target states, e.g. gwl1.5,gwl2.0,gwl3.0")
    p.add_argument("--ref-state", default="ref91-20")
    p.add_argument("--profiles", default="seasonal_warm,peak_event,sustained_heat,nocturnal_heat")
    p.add_argument("--baseline-start-year", type=int, default=1991)
    p.add_argument("--baseline-end-year", type=int, default=2020)
    p.add_argument("--reference-epw", default=None, help="Mandatory for final EPW export: reference EPW used as metadata/header/hourly carrier")
    p.add_argument("--skip-existing", action="store_true", help="Skip some file-generation steps when expected outputs already exist.")
    p.add_argument("--continue-on-error", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--refresh-station-catalog", action="store_true", help="Refresh frontend/data/stations_catalog.json from CH2025 STAC before the batch run.")
    p.add_argument("--cloudcover-mode", choices=["auto", "skip", "required"], default="auto", help="Fetch/merge cloud cover before candidate generation. auto = try visual then satellite CFC then fallback; required = fail if no visual/CFC source works; skip = do not run cloud-cover auxiliary layer.")
    p.add_argument("--cloudcover-granularity", default="d", choices=["h", "d", "m", "y"], help="Granularity for visual-observation cloud-cover fetch; default daily.")
    p.add_argument("--cfc-frequency", default="h", choices=["h", "d", "m", "y"], help="Satellite CFC frequency. Hourly CFC is attempted as monthly NetCDF assets.")
    p.add_argument("--cfc-max-files", type=int, default=0, help="Safety cap for satellite CFC downloads; 0 = no cap.")
    p.add_argument("--include-wehrli-benchmark", action="store_true", help="Download/normalize Wehrli/SIA 2028 1-in-10 warm-summer benchmark for frontend weather comparison only.")
    p.add_argument("--wehrli-url", default="https://s.geo.admin.ch/94e9d38450")
    p.add_argument("--wehrli-label", default="Wehrli 1-in-10 warm summer")
    p.add_argument("--wehrli-keywords", default="1in10,1-in-10,warm,summer,2060,rcp85")
    p.add_argument("--xmy-cdh-base-temperature-c", type=float, default=26.0, help="Fixed CDH base used for peak/event CDH metrics.")
    p.add_argument("--xmy-night-base-temperature-c", type=float, default=20.0, help="Base temperature for nocturnal nighttime CDH metric.")
    p.add_argument("--xmy-night-start-hour", type=int, default=22)
    p.add_argument("--xmy-night-end-hour", type=int, default=6)
    p.add_argument("--xmy-peak-window-days", type=int, default=3)
    p.add_argument("--xmy-peak-secondary-window-days", type=int, default=5)
    p.add_argument("--xmy-min-heatwave-duration-days", type=int, default=3)
    args = p.parse_args()

    stations = [s.lower() for s in split_csv(args.stations)]
    gwls = [g.lower() for g in split_csv(args.gwls)]
    profiles = split_csv(args.profiles)
    profile_list = ",".join(profiles)
    target_vars = ["tas", "hurs", "rsds", "tasmax", "tasmin"]
    ref_vars = ["tas", "hurs", "rsds"]
    core_vars = ["tas", "hurs", "rsds"]

    if args.refresh_station_catalog:
        run([py(), "00_discover_ch2025_stations_v4_1.py", "--output", "frontend/data/stations_catalog.json"], args.dry_run)

    failures = []
    for station in stations:
        for target_state in gwls:
            try:
                log("\n" + "=" * 90)
                log(f"Running station={station.upper()}, target={target_state}")
                log("=" * 90)

                # 1) Fetch and parse CH2025 reference and target variables
                for var in ref_vars:
                    raw = expected_ch2025_cache(station, var, args.ref_state)
                    out = parsed_ch2025(station, var, args.ref_state)
                    if not (args.skip_existing and Path(out).exists()):
                        run([py(), "01_fetch_ch2025_asset.py", "--station", station, "--variable", var, "--state", args.ref_state, "--fmt", "csv"], args.dry_run)
                        run([py(), "02_parse_ch2025_daily.py", "--input", raw, "--output-format", "csv"], args.dry_run)
                for var in target_vars:
                    raw = expected_ch2025_cache(station, var, target_state)
                    out = parsed_ch2025(station, var, target_state)
                    if not (args.skip_existing and Path(out).exists()):
                        run([py(), "01_fetch_ch2025_asset.py", "--station", station, "--variable", var, "--state", target_state, "--fmt", "csv"], args.dry_run)
                        run([py(), "02_parse_ch2025_daily.py", "--input", raw, "--output-format", "csv"], args.dry_run)

                # 2) Build daily signals for tas/hurs/rsds
                for var in core_vars:
                    out = daily_signal(station, var, args.ref_state, target_state)
                    if not (args.skip_existing and Path(out).exists()):
                        run([py(), "03_build_daily_signal.py", "--ref", parsed_ch2025(station, var, args.ref_state), "--target", parsed_ch2025(station, var, target_state), "--output-format", "csv"], args.dry_run)

                common = f"data_processed/common_model_chains/common_model_chains_{station}_{args.ref_state}_to_{target_state}_v4.csv"
                if not (args.skip_existing and Path(common).exists()):
                    run([py(), "03b_extract_common_model_chains_v4.py", "--inputs",
                         f"tas={daily_signal(station,'tas',args.ref_state,target_state)}",
                         f"hurs={daily_signal(station,'hurs',args.ref_state,target_state)}",
                         f"rsds={daily_signal(station,'rsds',args.ref_state,target_state)}",
                         f"tasmax={parsed_ch2025(station,'tasmax',target_state)}",
                         f"tasmin={parsed_ch2025(station,'tasmin',target_state)}",
                         "--name-suffix", "v4"], args.dry_run)

                # 3) Fetch and parse MeteoSwiss hourly observations
                hourly = f"data_processed/hourly_obs/hourly_obs_{station}_v4.csv"
                if not (args.skip_existing and Path(hourly).exists()):
                    run([py(), "04a_fetch_meteoswiss_hourly_v4.py", "--station", station, "--granularity", "h", "--update-types", "historical", "--start-year", str(args.baseline_start_year), "--end-year", str(args.baseline_end_year), "--metadata"], args.dry_run)
                    run([py(), "04_parse_meteoswiss_hourly_v4.py", "--input", f"./cache/meteoswiss/{station}/h", "--station-id", station, "--output-format", "csv"], args.dry_run)

                # 3b) Optional cloud-cover auxiliary layer. Cloud cover is not part of the
                #     core automatic station hourly file for many stations. The runner now tries:
                #       visual observations -> satellite CFC -> documented fallback.
                hourly_base = hourly
                hourly_cloud = f"data_processed/hourly_obs/hourly_obs_{station}_v4_cloud.csv"
                cloud_parsed = f"data_processed/cloudcover/cloudcover_obs_{station}_v4.csv"
                cloud_cfc = f"data_processed/cloudcover/cloudcover_cfc_{station}_v4.csv"
                cfc_manifest = f"cache/meteoswiss_cfc/{args.cfc_frequency}/{args.baseline_start_year}-{args.baseline_end_year}/cfc_fetch_manifest.json"
                if args.cloudcover_mode != "skip":
                    cloud_errors = []
                    try:
                        if not (args.skip_existing and Path(hourly_cloud).exists()):
                            # Source A: target-station visual observations.
                            ok_visual = run_optional([py(), "04b_fetch_meteoswiss_cloudcover_obs_v4.py",
                                "--station", station, "--granularity", args.cloudcover_granularity,
                                "--update-types", "historical", "--start-year", str(args.baseline_start_year),
                                "--end-year", str(args.baseline_end_year), "--metadata"], args.dry_run)
                            if ok_visual:
                                ok_parse = run_optional([py(), "04c_parse_cloudcover_observations_v4.py",
                                    "--input", f"./cache/meteoswiss_cloudcover/{station}/{args.cloudcover_granularity}",
                                    "--station-id", station, "--output", cloud_parsed, "--output-format", "csv"], args.dry_run)
                                if not ok_parse:
                                    cloud_errors.append("visual cloud-cover parsing failed")
                                else:
                                    ok_merge = run_optional([py(), "04d_merge_cloudcover_to_hourly_obs_v4.py",
                                        "--hourly-obs", hourly_base, "--cloudcover", cloud_parsed,
                                        "--output", hourly_cloud, "--method", "linear", "--opaque-policy", "equal_total",
                                        "--output-format", "csv"], args.dry_run)
                                    if not ok_merge:
                                        cloud_errors.append("visual cloud-cover merge failed")
                            else:
                                cloud_errors.append("target station has no visual-observation cloud-cover asset")

                            # Source B: satellite-derived Cloud Fractional Cover (CFC), by station lat/lon.
                            if not Path(hourly_cloud).exists() and not args.dry_run:
                                try:
                                    station_meta_for_cfc = ensure_station_metadata(station, dry_run=args.dry_run)
                                except Exception as meta_exc:
                                    station_meta_for_cfc = None
                                    cloud_errors.append(f"station metadata unavailable for CFC extraction: {meta_exc}")
                                if station_meta_for_cfc is not None:
                                    ok_cfc_fetch = run_optional([py(), "04e_fetch_meteoswiss_satellite_cfc_v4.py",
                                        "--frequency", args.cfc_frequency,
                                        "--start-year", str(args.baseline_start_year), "--end-year", str(args.baseline_end_year),
                                        "--manifest", cfc_manifest,
                                        "--max-files", str(args.cfc_max_files)], args.dry_run)
                                    if not ok_cfc_fetch:
                                        cloud_errors.append("satellite CFC fetch found no usable assets")
                                    else:
                                        ok_cfc_extract = run_optional([py(), "04f_extract_cfc_to_station_hourly_v4.py",
                                            "--manifest", cfc_manifest,
                                            "--station-metadata", str(station_meta_for_cfc),
                                            "--hourly-obs", hourly_base,
                                            "--output", cloud_cfc,
                                            "--output-format", "csv"], args.dry_run)
                                        if not ok_cfc_extract:
                                            cloud_errors.append("satellite CFC extraction failed")
                                        else:
                                            ok_cfc_merge = run_optional([py(), "04d_merge_cloudcover_to_hourly_obs_v4.py",
                                                "--hourly-obs", hourly_base, "--cloudcover", cloud_cfc,
                                                "--output", hourly_cloud, "--method", "linear", "--opaque-policy", "equal_total",
                                                "--output-format", "csv"], args.dry_run)
                                            if not ok_cfc_merge:
                                                cloud_errors.append("satellite CFC merge failed")
                        if Path(hourly_cloud).exists() or args.dry_run:
                            hourly = hourly_cloud
                            log(f"[INFO] Using cloud-cover-enriched hourly backbone: {hourly}")
                        elif args.cloudcover_mode == "required":
                            raise RuntimeError("; ".join(cloud_errors) or "cloud-cover auxiliary layer unavailable")
                        else:
                            log(f"[WARN] Cloud-cover auxiliary layer unavailable for {station.upper()}; continuing with fallback sky-cover handling. Reasons: {'; '.join(cloud_errors)}")
                            hourly = hourly_base
                    except Exception as cloud_exc:
                        if args.cloudcover_mode == "required":
                            raise
                        log(f"[WARN] Cloud-cover auxiliary layer unavailable for {station.upper()}; continuing with fallback sky-cover handling. Reason: {cloud_exc}")
                        hourly = hourly_base

                # 4) Capability matrix
                run([py(), "01_station_capability_matrix_v4.py", "--station", station, "--ref-state", args.ref_state, "--target-state", target_state, "--hourly-obs", hourly], args.dry_run)

                # 5) Candidate pool + daily summary
                pool = f"data_processed/hourly_future_candidates/hourly_future_candidates_{station}_{args.ref_state}_to_{target_state}_v4.csv"
                daily_sum = f"data_processed/hourly_future_candidates/candidate_daily_summary_{station}_{args.ref_state}_to_{target_state}_v4.csv"
                if not (args.skip_existing and Path(pool).exists()):
                    run([py(), "05_build_hourly_future_candidates_v4.py", "--hourly-obs", hourly,
                         "--tas-signal", daily_signal(station,"tas",args.ref_state,target_state),
                         "--hurs-signal", daily_signal(station,"hurs",args.ref_state,target_state),
                         "--rsds-signal", daily_signal(station,"rsds",args.ref_state,target_state),
                         "--common-chains", common, "--output", pool,
                         "--baseline-start-year", str(args.baseline_start_year),
                         "--baseline-end-year", str(args.baseline_end_year)], args.dry_run)
                if not (args.skip_existing and Path(daily_sum).exists()):
                    run([py(), "05b_build_candidate_daily_summary_v4_1.py", "--candidate-pool", pool, "--output", daily_sum], args.dry_run)

                # 6) FRY and XMY selection
                run([py(), "06_select_fry_from_candidate_pool_v4_1.py", "--candidate-pool", pool, "--candidate-daily-summary", daily_sum,
                     "--target-tas", parsed_ch2025(station,"tas",target_state),
                     "--target-hurs", parsed_ch2025(station,"hurs",target_state),
                     "--target-rsds", parsed_ch2025(station,"rsds",target_state),
                     "--target-tasmax", parsed_ch2025(station,"tasmax",target_state),
                     "--target-tasmin", parsed_ch2025(station,"tasmin",target_state),
                     "--common-chains", common], args.dry_run)
                run([py(), "07_select_xmy_profiles_from_candidate_pool_v4_1.py",
                     "--candidate-pool", pool,
                     "--candidate-daily-summary", daily_sum,
                     "--profiles", profile_list,
                     "--cdh-base-temperature-c", str(args.xmy_cdh_base_temperature_c),
                     "--night-base-temperature-c", str(args.xmy_night_base_temperature_c),
                     "--night-start-hour", str(args.xmy_night_start_hour),
                     "--night-end-hour", str(args.xmy_night_end_hour),
                     "--peak-window-days", str(args.xmy_peak_window_days),
                     "--peak-secondary-window-days", str(args.xmy_peak_secondary_window_days),
                     "--min-heatwave-duration-days", str(args.xmy_min_heatwave_duration_days)], args.dry_run)

                # 7) Comparison
                fry = f"data_processed/fry/fry_{station}_{args.ref_state}_to_{target_state}_v4.csv"
                xmy_paths = {prof: f"data_processed/xmy/xmy_{prof}_{station}_{args.ref_state}_to_{target_state}_v4.csv" for prof in profiles}
                files_args = [f"FRY={fry}"] + [f"{prof}={path}" for prof, path in xmy_paths.items()]
                external_benchmark_files = []
                if args.include_wehrli_benchmark:
                    wehrli_out = f"data_processed/external_benchmarks/wehrli_1in10_{station}_{target_state}.csv"
                    ok_wehrli = run_optional([py(), "13_add_external_wehrli_benchmark.py",
                        "--url", args.wehrli_url,
                        "--station", station,
                        "--keywords", args.wehrli_keywords,
                        "--label", args.wehrli_label,
                        "--output", wehrli_out], args.dry_run)
                    if ok_wehrli or args.dry_run:
                        external_benchmark_files.append(f"{args.wehrli_label}={wehrli_out}")
                        files_args.append(f"{args.wehrli_label}={wehrli_out}")
                    else:
                        log(f"[WARN] Wehrli benchmark could not be normalized for {station.upper()}; continuing without it.")
                comparison = f"data_processed/weather_file_comparison/weather_file_comparison_{station}_{args.ref_state}_to_{target_state}_v4.csv"
                run([py(), "10_compare_weather_files_v4.py", "--files", *files_args, "--output", comparison], args.dry_run)

                # 8) EPW completion and writing
                if not args.reference_epw:
                    raise RuntimeError("EPW export now requires --reference-epw; the writer is reference-based and will not build a donor-free EPW.")
                reference_epw = Path(args.reference_epw)
                if not args.dry_run and not reference_epw.exists():
                    raise RuntimeError(f"Reference EPW not found: {reference_epw}")
                station_meta = ensure_station_metadata(station, dry_run=args.dry_run)
                selected_files = {"fry": fry, **xmy_paths}
                epw_files = {}
                for key, csv_path in selected_files.items():
                    outbase = f"{key}_{station}_{args.ref_state}_to_{target_state}"
                    ready = f"data_processed/epw_ready/{outbase}_epw_ready.csv"
                    epw = f"outputs/epw/{outbase}.epw"
                    run([py(), "08_complete_epw_fields_v4.py", "--hourly-table", csv_path, "--station-metadata", str(station_meta), "--output", ready], args.dry_run)
                    run([
                        py(), "09_write_epw_v4.py",
                        "--completed-hourly", ready,
                        "--reference-epw", str(reference_epw),
                        "--station-metadata", str(station_meta),
                        "--output-epw", epw,
                        "--generated-file-type", key.upper(),
                        "--scenario-label", target_state,
                        "--gwl-label", target_state,
                        "--method-label", "reference_based_morphed_epw",
                    ], args.dry_run)
                    epw_files[key] = epw

                # 9) Run summary for frontend
                run_id = f"run_{station}_{target_state.replace('.', '_')}"
                out_json = f"outputs/run_{station}_{target_state}/run_summary.json"
                xmy_sel = f"data_processed/xmy/xmy_profiles_{station}_{args.ref_state}_to_{target_state}_v4_selections.csv"
                fry_sel = f"data_processed/fry/fry_{station}_{args.ref_state}_to_{target_state}_v4_selection.csv"
                cap_json = f"data_processed/station_capability/station_capability_{station}_{args.ref_state}_to_{target_state}_v4.json"
                # station/GWL-specific comparison CSV created above
                fry_rankings = f"data_processed/fry/fry_{station}_{args.ref_state}_to_{target_state}_v4_rankings.csv"
                summary_files = [f"FRY={fry}", f"FRY_EPW={epw_files['fry']}"]
                for prof, pth in xmy_paths.items():
                    summary_files.append(f"XMY_{prof}={pth}")
                    summary_files.append(f"XMY_{prof}_EPW={epw_files[prof]}")
                run([py(), "12_build_run_summary_v4.py", "--station", station.upper(), "--station-name", station.upper(),
                     "--ref-state", args.ref_state, "--target-state", target_state,
                     "--capability-json", cap_json,
                     "--weather-comparison-csv", comparison,
                     "--xmy-selections-csv", xmy_sel,
                     "--fry-selection-csv", fry_sel,
                     "--fry-rankings-csv", fry_rankings,
                     "--candidate-daily-summary", daily_sum,
                     "--target-tas", parsed_ch2025(station,"tas",target_state),
                     "--target-hurs", parsed_ch2025(station,"hurs",target_state),
                     "--target-rsds", parsed_ch2025(station,"rsds",target_state),
                     "--monthly-source", fry,
                     "--monthly-reference-source", hourly,
                     "--external-weather-files", *external_benchmark_files,
                     "--files", *summary_files,
                     "--frontend-dir", "frontend",
                     "--output", out_json], args.dry_run)

            except Exception as exc:
                failures.append((station, target_state, str(exc)))
                log(f"[FAILED] station={station}, target={target_state}: {exc}")
                if not args.continue_on_error:
                    return 1

    if failures:
        log("\nBatch completed with failures:")
        for st, gwl, err in failures:
            log(f"  - {st} {gwl}: {err}")
        return 1
    log("\nBatch completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
