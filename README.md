# Swfgenerator

Research-grade engine for generating **future Swiss weather files** (FRY =
Future Representative Year, XMY = eXtreme Meteorological Year) plus
**ASHRAE-style climatic design conditions / DDY** for building performance
simulation (Honeybee / OpenStudio / EnergyPlus).

The pipeline applies CH2025 climate deltas to MeteoSwiss observations
(Delta Morphing) at Global Warming Levels (GWL), and produces EPW + DDY bundles
with validation reports and machine-readable summaries. An optional static
**frontend dashboard** visualizes the generated results.

## Architecture

```
src/swfgenerator/      # computational engine (config-driven, station-agnostic)
  cli.py               #   argument parsing -> orchestrator (no algorithms here)
  config.py            #   YAML config loader + schema
  stations.py          #   Switzerland-wide station registry / reference resolution
  orchestrator.py      #   stage ordering; invokes validated legacy scripts (subprocess)
  design_conditions.py #   thin adapter re-exporting validated 09b functions
  resources/reference/ #   bundled reference DDY templates (Zurich, Basel)
legacy/scripts/        # original, VALIDATED numbered pipeline (00..13, 09b, run_batch)
configs/examples/      # example workflow configs (Zurich/SMA as example only)
data/reference/        # versioned reference DDY templates (+ README)
frontend/              # optional visualization dashboard (consumes JSON/CSV outputs)
docs/                  # release notes / methodology
tests/                 # validation-first test suite
```

The Python package is the **computational engine**; the frontend is a
**visualization layer** that consumes the generated JSON / CSV / reports. There
is no backend server, database, or task queue.

## Install

```bash
python3 -m pip install -e .
python3 -c "import swfgenerator; print(swfgenerator.__version__)"
swfgenerator --help
```

## CLI

```bash
swfgenerator info              --config configs/examples/zurich_gwl2.yaml
swfgenerator full              --config configs/examples/zurich_gwl2.yaml
swfgenerator fetch             --config configs/examples/zurich_gwl2.yaml
swfgenerator build-candidates  --config configs/examples/zurich_gwl2.yaml
swfgenerator select-years      --config configs/examples/zurich_gwl2.yaml
swfgenerator write-epw         --config configs/examples/zurich_gwl2.yaml
swfgenerator design-conditions --config configs/examples/zurich_gwl2.yaml
swfgenerator validate          --config configs/examples/zurich_gwl2.yaml
swfgenerator summary           --config configs/examples/zurich_gwl2.yaml
```

All stages support `--dry-run` (print the exact commands) and `--force` /
`--overwrite` (off by default — existing outputs are not clobbered). Paths are
resolved from the project root / config / packaged resources, never from the
current working directory.

## Station switching

Stations are driven by config, not hardcoded. Edit the `station:` block (code,
name, lat/lon, elevation, timezone, source ids, optional reference EPW/DDY) or
point `--config` at another file. Zurich/SMA appears only in the example config,
tests, docs, and bundled reference resources. If a station has no reference DDY,
future design-day generation **fails by default** rather than silently borrowing
another station's values; an explicit `design_conditions.reference_fallback.allow`
is required to override.

## Data access

The default workflow fetches CH2025 / MeteoSwiss data via the existing API /
remote-access stages (`00`, `01`, `04*`). Cache is configurable (`data_source.cache`).
Local files are optional overrides for debugging, not a default requirement.

## Validated behavior (must not regress)

- **EPW writer** (`legacy/scripts/09_write_epw_v4.py`): reference-based carrier;
  morphs DB/RH/GHI; derives DP/DHI/DNI; inherits pressure/wind/sky/visibility/
  weather/precipitation; solar-closure / night-zero / DHI<=GHI / DNI-cap
  diagnostics; batch EPW export requires `--reference-epw`.
- **Design conditions** (`legacy/scripts/09b_compute_design_conditions.py`):
  future mode uses the multi-year morphed pool (never a single FRY/XMY/EPW year);
  per-chain-then-average; psychrolib; pressure from elevation (never 101325 Pa);
  Gumbel with sqrt(6)/pi; reference DDY required unless explicit fallback; Zurich
  reference DDY = 114 objects, generated functional subset = 72, Honeybee
  survivors = 31, 6 ClearSky, annual cooling July tau 0.407/2.321, month-specific
  monthly tau; no silent fallback.

These algorithms are wrapped, not reimplemented. See `legacy/scripts/README_LEGACY.md`.

## Tests

```bash
python3 -m pytest tests
```

Covers the Gumbel table, Zurich DDY parser counts/tau, reference-fallback policy,
config loading / station-agnostic core, and CLI dry-run. Heavy end-to-end runs
are marked `integration` and deselected by default.

## Known limitations / TODO

- The new CLI's data->EPW chain currently delegates to the monolithic legacy
  `run_batch_pipeline_v4_1.py`; granular single-stage execution of that chain is
  a TODO. Design-conditions, validate, and summary are wired directly.
- `epw_writer.morphed_fields` and `data_source.local_overrides` are reserved
  config fields (surfaced via `swfgenerator info`), not yet wired per-stage.
- `summary` currently shows the legacy script contract; wiring its output paths
  from `output.root` is a TODO.
