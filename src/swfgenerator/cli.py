"""swfgenerator command-line interface.

The CLI only parses arguments and delegates to :mod:`swfgenerator.orchestrator`.
It contains no climate/EPW/DDY algorithms.
"""
from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from . import __version__
from .config import ConfigError, load_config
from .orchestrator import STAGES, OrchestratorError
from .stations import station_summary


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="swfgenerator",
        description="Future Swiss weather file generator (FRY/XMY EPW + ASHRAE-style "
                    "design conditions / DDY). Config-driven; station-agnostic core.",
    )
    p.add_argument("--version", action="version", version=f"swfgenerator {__version__}")
    sub = p.add_subparsers(dest="command", metavar="<command>")

    common_help = {
        "fetch": "Acquire CH2025 / MeteoSwiss data (API by default).",
        "build-candidates": "Build the multi-year hourly future candidate pool.",
        "select-years": "Select FRY and XMY profiles from the candidate pool.",
        "write-epw": "Write reference-based morphed EPW files (requires reference EPW).",
        "design-conditions": "Compute ASHRAE-style design conditions and write DDY.",
        "validate": "Run validation gates (Gumbel + psychrometrics).",
        "summary": "Build the run summary artifacts.",
        "full": "Run the full workflow end to end.",
    }
    for name in ["full", "fetch", "build-candidates", "select-years", "write-epw",
                 "design-conditions", "validate", "summary"]:
        sp = sub.add_parser(name, help=common_help[name], description=common_help[name])
        sp.add_argument("--config", required=True, help="Path to a YAML workflow config.")
        sp.add_argument("--dry-run", action="store_true",
                        help="Print the stage commands without executing them.")
        force = sp.add_mutually_exclusive_group()
        force.add_argument("--force", action="store_true",
                           help="Overwrite existing outputs (default: do not overwrite).")
        force.add_argument("--overwrite", action="store_true", help="Alias of --force.")

    info = sub.add_parser("info", help="Print resolved config + station summary.",
                          description="Print resolved config + station summary.")
    info.add_argument("--config", required=True)
    return p


def _run_stage(name: str, args: argparse.Namespace) -> int:
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"[ERROR] config: {exc}", file=sys.stderr)
        return 2
    force = bool(getattr(args, "force", False) or getattr(args, "overwrite", False))
    try:
        STAGES[name](cfg, dry_run=bool(args.dry_run), force=force)
    except (OrchestratorError, ConfigError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0

    if args.command == "info":
        try:
            cfg = load_config(args.config)
        except ConfigError as exc:
            print(f"[ERROR] config: {exc}", file=sys.stderr)
            return 2
        import json
        print(json.dumps({
            "config": str(cfg.path),
            "station": station_summary(cfg.station),
            "ref_state": cfg.ref_state,
            "gwls": cfg.gwls,
            "profiles": cfg.profiles,
            "data_source_mode": cfg.data_source_mode,
            "cache": {"enabled": cfg.cache_enabled, "dir": str(cfg.cache_dir),
                      "refresh": cfg.cache_refresh},
            "output_root": str(cfg.output_root),
            "allow_reference_fallback": cfg.allow_reference_fallback,
            "timestamp_semantics": cfg.timestamp_semantics,
            "reserved_fields": cfg.reserved,
        }, indent=2, default=str))
        return 0

    return _run_stage(args.command, args)


if __name__ == "__main__":
    raise SystemExit(main())
