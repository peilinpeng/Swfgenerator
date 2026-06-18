#!/usr/bin/env python3
"""Create BPS-ready EPW copies with GWL-specific header metadata.

This is intentionally a header-only post-processing step. It reads the existing
generated EPWs, keeps the hourly weather data byte-for-byte unchanged, and writes
copies whose EPW header makes the design-condition/DDY policy explicit.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Tuple


GWLS = ("gwl1.5", "gwl2.0", "gwl2.5", "gwl3.0")
WEATHER_TYPES = ("fry", "seasonal_warm", "peak_event", "sustained_heat", "nocturnal_heat")
HEADER_LINES = 8


class HeaderUpdateError(RuntimeError):
    """Raised when an EPW cannot be safely header-updated."""


def _csv_line(fields: Iterable[object]) -> str:
    import io

    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="")
    writer.writerow(list(fields))
    return buf.getvalue()


def read_epw(path: Path) -> Tuple[List[str], List[str]]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    if len(lines) < HEADER_LINES:
        raise HeaderUpdateError(f"{path} has fewer than {HEADER_LINES} EPW header lines")
    header = [line.rstrip("\r\n") for line in lines[:HEADER_LINES]]
    data = lines[HEADER_LINES:]
    return header, data


def hourly_hash(data_lines: List[str]) -> str:
    h = hashlib.sha256()
    for line in data_lines:
        h.update(line.encode("utf-8"))
    return h.hexdigest()


def parse_design_summary(path: Path) -> Dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if "design_conditions" not in payload:
        raise HeaderUpdateError(f"{path} does not contain design_conditions")
    return payload


def _g(mapping: Dict, key: str, default: object = "") -> object:
    value = mapping.get(key, default)
    if isinstance(value, float):
        return round(value, 3)
    return value


def build_design_conditions_line(summary_payload: Dict, gwl: str, ddy_path: Path) -> str:
    """Build a compact EPW DESIGN CONDITIONS summary from the GWL JSON.

    The paired DDY remains the authoritative EnergyPlus sizing source. This line
    is a nonzero EPW header summary so weather-file readers do not see the future
    EPW as lacking design-condition metadata.
    """

    dc = summary_payload["design_conditions"]
    heating = dc.get("heating", {})
    cooling = dc.get("cooling", {})
    extremes = dc.get("extremes", {})
    fields = [
        "DESIGN CONDITIONS",
        1,
        f"SWF multi-year future design-condition summary {gwl}; authoritative sizing DDY={ddy_path.name}",
        "Heating",
        1,
        _g(heating, "DB_99.6"),
        _g(heating, "DB_99.0"),
        _g(heating, "DP_99.6"),
        _g(heating, "DP_99.6_MCDB"),
        "Cooling",
        1,
        _g(cooling, "DB_0.4"),
        _g(cooling, "DB_0.4_MCWB"),
        _g(cooling, "WB_0.4"),
        _g(cooling, "WB_0.4_MCDB"),
        _g(cooling, "DP_0.4"),
        _g(cooling, "DP_0.4_MCDB"),
        _g(cooling, "Enth_0.4"),
        _g(cooling, "Enth_0.4_MDB"),
        _g(cooling, "MCDBR_DB"),
        _g(cooling, "MCDBR_WB"),
        _g(cooling, "MCDBR_DP"),
        _g(cooling, "MCDBR_Enth"),
        "Extremes",
        _g(extremes, "M_min"),
        _g(extremes, "M_max"),
        _g(extremes, "s_min"),
        _g(extremes, "s_max"),
    ]
    return _csv_line(fields)


def build_comments(source_epw: Path, reference_epw: Path, summary_payload: Dict, gwl: str, ddy_path: Path) -> Tuple[str, str]:
    dc = summary_payload["design_conditions"]
    inherited = (
        "TYPICAL/EXTREME PERIODS and GROUND TEMPERATURES inherited from the "
        "reference EPW and treated as metadata in this version"
    )
    computed = (
        "DESIGN CONDITIONS regenerated from the multi-year future design-condition "
        "summary for this GWL"
    )
    comment1 = (
        f"COMMENTS 1,\"BPS-ready header copy; source_epw={source_epw.name}; "
        f"reference_epw={reference_epw.name}; gwl={gwl}; station={summary_payload.get('station', '')}; "
        f"generated_at={datetime.now(timezone.utc).isoformat()}\""
    )
    comment2 = (
        "COMMENTS 2,\""
        f"{computed}. {inherited}. Authoritative EnergyPlus autosizing design days "
        f"are provided by paired DDY {ddy_path.name} and must be injected into the IDF/workflow. "
        f"Hourly weather data unchanged; key values: htg99.6={_g(dc.get('heating', {}), 'DB_99.6')}C, "
        f"clg0.4={_g(dc.get('cooling', {}), 'DB_0.4')}C, "
        f"mcwb0.4={_g(dc.get('cooling', {}), 'DB_0.4_MCWB')}C.\""
    )
    return comment1, comment2


def update_header(
    source_header: List[str],
    reference_header: List[str],
    design_conditions_line: str,
    comments: Tuple[str, str],
) -> List[str]:
    if len(source_header) != HEADER_LINES or len(reference_header) != HEADER_LINES:
        raise HeaderUpdateError("EPW headers must contain exactly 8 lines")
    if not source_header[0].startswith("LOCATION,"):
        raise HeaderUpdateError("source EPW LOCATION line is invalid")
    updated = list(source_header)
    updated[1] = design_conditions_line
    updated[2] = reference_header[2]
    updated[3] = reference_header[3]
    updated[5], updated[6] = comments
    return updated


def count_data_rows(data_lines: List[str]) -> int:
    return sum(1 for line in data_lines if line.strip())


def validate_header_update(
    source_path: Path,
    output_path: Path,
    source_header: List[str],
    output_header: List[str],
    reference_header: List[str],
    source_data: List[str],
    output_data: List[str],
    before_hash: str,
    after_hash: str,
) -> Dict:
    errors: List[str] = []
    if len(output_header) != HEADER_LINES:
        errors.append("header_line_count_not_8")
    if not output_header[0].startswith("LOCATION,"):
        errors.append("invalid_location_line")
    if output_header[0] != source_header[0]:
        errors.append("location_changed")
    try:
        if float(output_header[0].split(",")[-1]) == 0.0:
            errors.append("zero_elevation")
    except (ValueError, IndexError):
        errors.append("location_elevation_not_numeric")
    if output_header[1].startswith("DESIGN CONDITIONS,0"):
        errors.append("design_conditions_zero")
    if not output_header[1].startswith("DESIGN CONDITIONS,1,"):
        errors.append("design_conditions_not_single_summary")
    if output_header[2] != reference_header[2]:
        errors.append("typical_extreme_not_inherited_from_reference")
    if output_header[3] != reference_header[3]:
        errors.append("ground_temperatures_not_inherited_from_reference")
    if "DESIGN CONDITIONS regenerated" not in output_header[6]:
        errors.append("comments_missing_design_policy")
    if "Authoritative EnergyPlus autosizing design days" not in output_header[6]:
        errors.append("comments_missing_ddy_policy")
    if count_data_rows(source_data) != 8760:
        errors.append("source_hourly_rows_not_8760")
    if count_data_rows(output_data) != 8760:
        errors.append("output_hourly_rows_not_8760")
    if before_hash != after_hash:
        errors.append("hourly_data_hash_changed")
    return {
        "source_epw": str(source_path),
        "output_epw": str(output_path),
        "passed": not errors,
        "errors": errors,
        "hourly_rows": count_data_rows(output_data),
        "hourly_sha256_before": before_hash,
        "hourly_sha256_after": after_hash,
        "location": output_header[0],
        "design_conditions": output_header[1],
        "typical_extreme_periods": output_header[2],
        "ground_temperatures": output_header[3],
        "comments_1": output_header[5],
        "comments_2": output_header[6],
    }


def write_epw(path: Path, header: List[str], data_lines: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(header) + "\n" + "".join(data_lines)
    path.write_text(text, encoding="utf-8")


def expected_paths(weather_root: Path, reference_epw: Path) -> List[Tuple[str, str, Path, Path, Path]]:
    paths = []
    for gwl in GWLS:
        ddy = weather_root / gwl / "ddy" / f"bas_{gwl}.ddy"
        summary = weather_root / gwl / "reports" / f"bas_{gwl}_design_conditions.json"
        for weather_type in WEATHER_TYPES:
            source = weather_root / gwl / "epw" / f"{weather_type}_bas_ref91-20_to_{gwl}.epw"
            paths.append((gwl, weather_type, source, ddy, summary))
    return paths


def update_matrix(weather_root: Path, reference_epw: Path, output_subdir: str) -> List[Dict]:
    reference_header, _ = read_epw(reference_epw)
    reports = []
    for gwl, weather_type, source, ddy, summary_path in expected_paths(weather_root, reference_epw):
        missing = [p for p in (source, ddy, summary_path) if not p.exists()]
        if missing:
            raise HeaderUpdateError("missing required input(s): " + ", ".join(str(p) for p in missing))
        source_header, source_data = read_epw(source)
        before_hash = hourly_hash(source_data)
        summary = parse_design_summary(summary_path)
        design_line = build_design_conditions_line(summary, gwl, ddy)
        comments = build_comments(source, reference_epw, summary, gwl, ddy)
        output_header = update_header(source_header, reference_header, design_line, comments)
        out_name = f"{source.stem}_header_updated{source.suffix}"
        output = source.parents[1] / output_subdir / out_name
        write_epw(output, output_header, source_data)
        _, output_data = read_epw(output)
        after_hash = hourly_hash(output_data)
        row = validate_header_update(
            source,
            output,
            source_header,
            output_header,
            reference_header,
            source_data,
            output_data,
            before_hash,
            after_hash,
        )
        row.update({"gwl": gwl, "weather_type": weather_type, "ddy": str(ddy), "design_summary": str(summary_path)})
        reports.append(row)
    return reports


def write_report(path: Path, reports: List[Dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"passed": all(r["passed"] for r in reports), "files": reports}, indent=2), encoding="utf-8")


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weather-root", type=Path, default=Path("outputs/basel_weather_batch"))
    parser.add_argument("--reference-epw", type=Path, default=Path("data/reference/basel/CHE_BL_Basel.Binningen.066010_TMYx.epw"))
    parser.add_argument("--output-subdir", default="epw_header_updated")
    parser.add_argument("--validation-report", type=Path, default=Path("outputs/basel_weather_batch/epw_header_update_validation.json"))
    args = parser.parse_args(argv)

    reports = update_matrix(args.weather_root, args.reference_epw, args.output_subdir)
    write_report(args.validation_report, reports)
    if not all(r["passed"] for r in reports):
        failed = [r for r in reports if not r["passed"]]
        raise HeaderUpdateError(f"{len(failed)} EPW header update(s) failed validation; see {args.validation_report}")
    print(f"updated {len(reports)} EPW headers")
    print(f"validation report: {args.validation_report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
