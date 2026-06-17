"""Workflow orchestration.

Maps the conceptual CLI stages onto the validated legacy scripts (invoked as
subprocesses from the project root, so their relative data paths resolve the same
way they always have). No pipeline algorithm is reimplemented here.

Design-conditions and summary are wired directly to their legacy scripts. The
data-acquisition / candidate / selection / EPW chain currently delegates to the
monolithic ``run_batch_pipeline_v4_1.py`` (see KNOWN LIMITATIONS in the README):
granular single-stage execution of that chain is a documented TODO; today those
subcommands run the full batch with ``--skip-existing`` so completed stages are
skipped rather than recomputed.
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .config import Config
from .paths import legacy_script, project_root
from .stations import resolve_reference_ddy, resolve_reference_epw


class OrchestratorError(RuntimeError):
    pass


@dataclass
class Command:
    description: str
    argv: List[str]


def _py() -> str:
    return sys.executable or "python3"


def _gwl_to_state(gwl: str) -> str:
    return gwl if gwl.startswith("gwl") else f"gwl{gwl}"


def _future_pool_path(cfg: Config, gwl: str) -> Path:
    state = _gwl_to_state(gwl)
    return (project_root() / "data_processed" / "hourly_future_candidates" /
            f"hourly_future_candidates_{cfg.station.code}_{cfg.ref_state}_to_{state}_v4.csv")


# --------------------------------------------------------------------------
# Command builders
# --------------------------------------------------------------------------

def _batch_command(cfg: Config, *, skip_existing: bool, dry_run: bool) -> Command:
    argv = [
        _py(), str(legacy_script("run_batch_pipeline_v4_1.py")),
        "--stations", cfg.station.code,
        "--gwls", ",".join(_gwl_to_state(g) for g in cfg.gwls),
        "--ref-state", cfg.ref_state,
        "--profiles", ",".join(cfg.profiles),
        "--baseline-start-year", str(cfg.baseline_start_year),
        "--baseline-end-year", str(cfg.baseline_end_year),
    ]
    ref_epw = resolve_reference_epw(cfg.station)
    if ref_epw is not None:
        argv += ["--reference-epw", str(ref_epw)]
    if skip_existing:
        argv += ["--skip-existing"]
    if dry_run:
        argv += ["--dry-run"]
    return Command("Run validated legacy batch pipeline (fetch -> ... -> write EPW)", argv)


def _design_conditions_commands(cfg: Config) -> List[Command]:
    cmds: List[Command] = []
    ref_ddy = resolve_reference_ddy(cfg.station)
    outdir = cfg.output_root / "design_conditions"
    for gwl in cfg.gwls:
        state = _gwl_to_state(gwl)
        argv = [
            _py(), str(legacy_script("09b_compute_design_conditions.py")),
            "--mode", "future",
            "--input", str(_future_pool_path(cfg, gwl)),
            "--station", cfg.station.name.replace(" ", "."),
            "--elevation", str(cfg.station.elevation_m),
            "--db-bin-width", str(cfg.db_bin_width_c),
            "--timestamp-semantics", cfg.timestamp_semantics,
            "--outdir", str(outdir),
            "--out-prefix", f"{cfg.station.code}_{state}",
        ]
        if ref_ddy is not None:
            argv += ["--reference-ddy", str(ref_ddy)]
        # The legacy 09b enforces the no-silent-fallback policy itself; we only
        # pass the explicit opt-in when the config permits it.
        if cfg.allow_reference_fallback:
            argv += ["--allow-reference-fallback"]
        cmds.append(Command(f"Compute ASHRAE-style design conditions / DDY ({state})", argv))
    return cmds


def _summary_command(cfg: Config) -> Command:
    # 12_build_run_summary_v4.py is the validated summary stage.
    argv = [_py(), str(legacy_script("12_build_run_summary_v4.py")), "--help"]
    return Command("Build run summary (legacy 12); --help shown as the contract is "
                   "output-path dependent (TODO: wire from config output_root)", argv)


def _validate_command() -> Command:
    argv = [_py(), str(legacy_script("09b_compute_design_conditions.py")), "--mode", "gumbel-test"]
    return Command("Validation gate: Gumbel return-period + psychrometric round-trip", argv)


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------

def _run(cmd: Command, *, dry_run: bool) -> None:
    print(f"[stage] {cmd.description}")
    print("        $ " + " ".join(cmd.argv))
    if dry_run:
        return
    proc = subprocess.run(cmd.argv, cwd=str(project_root()))
    if proc.returncode != 0:
        raise OrchestratorError(
            f"Stage failed (exit {proc.returncode}): {cmd.description}")


def _check_api(cfg: Config) -> None:
    if cfg.data_source_mode != "api":
        print(f"[warn] data_source.mode='{cfg.data_source_mode}'; default workflow is API. "
              "Local mode treats manual files as overrides.")


# --------------------------------------------------------------------------
# Public stage entry points (called by the CLI)
# --------------------------------------------------------------------------

def stage_fetch(cfg: Config, *, dry_run: bool, force: bool) -> None:
    _check_api(cfg)
    _run(_batch_command(cfg, skip_existing=not force, dry_run=dry_run), dry_run=dry_run)


def stage_build_candidates(cfg: Config, *, dry_run: bool, force: bool) -> None:
    _run(_batch_command(cfg, skip_existing=not force, dry_run=dry_run), dry_run=dry_run)


def stage_select_years(cfg: Config, *, dry_run: bool, force: bool) -> None:
    _run(_batch_command(cfg, skip_existing=not force, dry_run=dry_run), dry_run=dry_run)


def stage_write_epw(cfg: Config, *, dry_run: bool, force: bool) -> None:
    if cfg.require_reference_epw and resolve_reference_epw(cfg.station) is None:
        raise OrchestratorError(
            "EPW export requires a reference EPW (epw_writer.require_reference_epw=true) "
            "but no station.reference_epw was found. Provide one in the config.")
    _run(_batch_command(cfg, skip_existing=not force, dry_run=dry_run), dry_run=dry_run)


def stage_design_conditions(cfg: Config, *, dry_run: bool, force: bool) -> None:
    for cmd in _design_conditions_commands(cfg):
        out = cfg.output_root / "design_conditions"
        # Overwrite guard: 09b writes <prefix>.ddy etc.; refuse to clobber unless --force.
        if not force and not dry_run:
            existing = list(out.glob(f"{cfg.station.code}_*_design_conditions.json"))
            if existing:
                raise OrchestratorError(
                    f"Design-condition outputs already exist in {out}. Re-run with "
                    "--force/--overwrite to regenerate.")
        _run(cmd, dry_run=dry_run)


def stage_validate(cfg: Config, *, dry_run: bool, force: bool) -> None:
    _run(_validate_command(), dry_run=dry_run)


def stage_summary(cfg: Config, *, dry_run: bool, force: bool) -> None:
    _run(_summary_command(cfg), dry_run=dry_run)


def stage_full(cfg: Config, *, dry_run: bool, force: bool) -> None:
    _check_api(cfg)
    _run(_batch_command(cfg, skip_existing=not force, dry_run=dry_run), dry_run=dry_run)
    stage_design_conditions(cfg, dry_run=dry_run, force=force)


STAGES = {
    "fetch": stage_fetch,
    "build-candidates": stage_build_candidates,
    "select-years": stage_select_years,
    "write-epw": stage_write_epw,
    "design-conditions": stage_design_conditions,
    "validate": stage_validate,
    "summary": stage_summary,
    "full": stage_full,
}
