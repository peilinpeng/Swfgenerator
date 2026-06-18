"""Regenerate a DDY from an existing design-condition summary JSON.

Patches the computed design conditions stored in a ``*_design_conditions.json``
(written by 09b) onto the station reference ``.ddy`` using the template patcher,
WITHOUT recomputing the hourly pool. Useful when only the DDY assembly/template
policy changed (e.g. daily-range policy) and the expensive design-condition pass
is unchanged. The produced DDY is identical to what a fresh 09b template run on
the same pool would emit, because it consumes the same stored ``design_conditions``.

Usage:
    python3 ddy_from_summary.py \
        --summary-json outputs/.../bas_gwl2.0_design_conditions.json \
        --out-ddy      outputs/.../ddy/bas_gwl2.0.ddy \
        --out-source-map outputs/.../reports/bas_gwl2.0_ddy_source_map.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ddy_template as ddt  # noqa: E402


def write_source_map(path: Path, source_map) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = ["object_name", "object_family", "field_name", "reference_value",
            "generated_value", "source", "note"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for row in source_map:
            w.writerow({c: row.get(c, "") for c in cols})


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--summary-json", required=True, type=Path)
    p.add_argument("--reference-ddy", type=Path, default=None,
                   help="Override; defaults to the reference_ddy recorded in the JSON.")
    p.add_argument("--out-ddy", required=True, type=Path)
    p.add_argument("--out-source-map", type=Path, default=None)
    p.add_argument("--station", default=None)
    p.add_argument("--daily-range-policy", choices=["compute", "inherit"], default="compute")
    p.add_argument("--pressure-policy", choices=["compute", "inherit"], default="compute")
    p.add_argument("--ddy-strictness", choices=["strict", "permissive"], default="permissive")
    a = p.parse_args(argv)

    payload = json.loads(a.summary_json.read_text(encoding="utf-8"))
    dc = payload.get("design_conditions")
    if not dc:
        print("[ERROR] summary JSON has no 'design_conditions' block.", file=sys.stderr)
        return 2
    ref_ddy = a.reference_ddy or (Path(payload["reference_ddy"]) if payload.get("reference_ddy") else None)
    if ref_ddy is None or not Path(ref_ddy).exists():
        print(f"[ERROR] reference DDY not found: {ref_ddy}", file=sys.stderr)
        return 2
    station = a.station or payload.get("station", "Station")

    ref_objects, _ = ddt.load_reference(Path(ref_ddy))
    patch = ddt.patch_template(
        ref_objects, dc, pressure_pa=float(dc["pressure_pa"]),
        daily_range_policy=a.daily_range_policy, pressure_policy=a.pressure_policy,
        strictness=a.ddy_strictness)

    n_range_computed = sum(1 for r in patch.source_map
                           if r["field_name"] == "Daily Dry-Bulb Temperature Range"
                           and r["source"] == ddt.SOURCE_COMPUTED_RANGE)
    header = (
        f"{station} future design conditions (regenerated from summary)\n"
        f"Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')} from "
        f"{a.summary_json.name} by ddy_from_summary.\n"
        f"Generator: reference-DDY TEMPLATE patch of {Path(ref_ddy).name} "
        f"(strictness={a.ddy_strictness}); reproduces reference object structure.\n"
        f"Computed-and-patched: Maximum Dry-Bulb, coincident humidity value, "
        f"daily dry-bulb range (cooling; {n_range_computed} objects), "
        f"barometric pressure(elevation).\n"
        f"Inherited verbatim: wind speed/direction, tau_b/tau_d, solar model, schedules, "
        f"rain/snow/DST flags, non-design-day objects, object & field order.\n"
        f"Honeybee add_from_ddy_996_004 surviving objects: {len(patch.survivors)} of "
        f"{patch.n_design_days}."
    )
    a.out_ddy.parent.mkdir(parents=True, exist_ok=True)
    a.out_ddy.write_text(ddt.render_ddy(patch.objects, header), encoding="utf-8")
    if a.out_source_map:
        write_source_map(a.out_source_map, patch.source_map)

    print(f"[OK] {a.out_ddy}: design_days={patch.n_design_days} "
          f"survivors={len(patch.survivors)} non_design={patch.n_non_design_days} "
          f"daily_range_computed={n_range_computed} unclassified={len(patch.unclassified)}")
    print(f"     families={patch.families}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
