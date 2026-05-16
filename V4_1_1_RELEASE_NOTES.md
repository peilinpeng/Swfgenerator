# v4.1.1 Stability Update

This update targets multi-station / multi-GWL / multi-profile robustness.

## Fixes

- `06_select_fry_from_candidate_pool_v4_1.py`
  - Replaced row-wise `DataFrame.apply(...)` in `stream_selected_hourly()` with vectorized key matching.
  - FRY hourly extraction now raises `ValueError` if the extracted file is not exactly 8760 rows.
  - Removed duplicated `rank_tables` declaration.

- `07_select_xmy_profiles_from_candidate_pool_v4_1.py`
  - Full-year XMY extraction now raises `ValueError` if the selected year is not exactly 8760 rows.

- `08_complete_epw_fields_v4.py`
  - Removed redundant `dict(r)` conversion after `itertuples()._asdict()`.
  - Added explicit documentation for ambiguous cloud-cover unit inference around 8–10.

- `12_build_run_summary_v4.py`
  - Replaced repeated boolean filtering for the 12×24 temperature matrix with `pivot_table`.
  - Keeps explicit `--fry-selection-csv` support for robust frontend selection-trace rendering.

- Shared utilities
  - `pipeline_utils.py` is now imported by the main v4.1 selection/completion scripts.

## Notes

- `run_sma_gwl2_full_pipeline_mac.sh` remains a compatibility wrapper around `run_batch_pipeline_v4_1.py`, so it automatically uses the v4.1 daily-summary selection layer.
- `run_batch_pipeline_v4_1.py` already passes `--fry-selection-csv` explicitly into `12_build_run_summary_v4.py`.
