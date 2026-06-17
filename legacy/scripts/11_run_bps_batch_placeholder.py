#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
11_run_bps_batch_placeholder.py

Purpose:
    Minimal batch runner wrapper for later Honeybee/OpenStudio/EnergyPlus automation.
    This script intentionally does not assume a fixed simulation stack. It creates
    a run manifest and optionally executes user-provided commands per EPW.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Create/optionally run BPS batch manifest.")
    p.add_argument("--epw-files", nargs="+", required=True)
    p.add_argument("--output-dir", default="./simulation_runs")
    p.add_argument("--command-template", default=None, help="Optional command template, use {epw} and {outdir}")
    p.add_argument("--execute", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    outdir = Path(args.output_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    manifest: List[Dict[str, Any]] = []
    for epw in args.epw_files:
        epw_path = Path(epw)
        run_dir = outdir / epw_path.stem
        run_dir.mkdir(parents=True, exist_ok=True)
        cmd = None
        status = "manifest_only"
        if args.command_template:
            cmd = args.command_template.format(epw=str(epw_path), outdir=str(run_dir))
            status = "pending"
            if args.execute:
                try:
                    result = subprocess.run(cmd, shell=True, check=False, capture_output=True, text=True)
                    (run_dir / "stdout.txt").write_text(result.stdout, encoding="utf-8")
                    (run_dir / "stderr.txt").write_text(result.stderr, encoding="utf-8")
                    status = "success" if result.returncode == 0 else f"failed:{result.returncode}"
                except Exception as exc:
                    status = f"error:{exc}"
        manifest.append({"epw": str(epw_path), "run_dir": str(run_dir), "command": cmd, "status": status})
    manifest_path = outdir / "bps_batch_manifest_v4.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
