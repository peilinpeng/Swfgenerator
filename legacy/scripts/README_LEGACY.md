# Legacy scripts (validated pipeline stages)

These are the **original, validated** numbered pipeline scripts plus the batch
orchestrator and shared helpers. They are kept here as the authoritative
implementation and safety net. **Their algorithms must not be changed** — the
new `swfgenerator` package wraps them (subprocess) rather than reimplementing
them.

## Status: supported (legacy entry point)

The recommended interface is now the `swfgenerator` CLI (see top-level README).
The legacy entry points below remain supported:

- `run_batch_pipeline_v4_1.py` — full batch chain (fetch → … → EPW export).
- `09b_compute_design_conditions.py` — ASHRAE-style design conditions + DDY.
- `09_write_epw_v4.py` — reference-based morphed EPW writer.
- numbered stages `00`–`13` — individual stages.

## Working directory

These scripts use **relative paths** (`data_processed/…`, `frontend/data/…`,
`cache/…`). Run them from the **repository root** so those paths resolve as
designed, e.g.:

```bash
python3 legacy/scripts/09b_compute_design_conditions.py --mode gumbel-test
python3 legacy/scripts/run_batch_pipeline_v4_1.py --stations sma --gwls gwl2.0 --dry-run
```

The `swfgenerator` orchestrator already invokes them with the project root as
the working directory.

## Superseded duplicates (kept intentionally)

- `06_select_fry_from_candidate_pool_v4.py` (superseded by `_v4_1`)
- `07_select_xmy_profiles_from_candidate_pool_v4.py` (superseded by `_v4_1`)
- `11_run_bps_batch_placeholder.py`, `12_build_run_summary_placeholder.py`

These are retained for provenance and are not deleted.
