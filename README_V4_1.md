# CH2025 EPW Pipeline v4.1

This version upgrades the prototype into a multi-station / multi-GWL batch workflow.

## Main changes from v4

- Added `run_batch_pipeline_v4_1.py` for `stations × GWLs × XMY profiles` batch runs.
- Added `05b_build_candidate_daily_summary_v4_1.py` to reduce memory use for FRY/XMY selection.
- Added `06_select_fry_from_candidate_pool_v4_1.py` using daily summary + chunked hourly extraction.
- Added `07_select_xmy_profiles_from_candidate_pool_v4_1.py` using daily summary + chunked full-year extraction.
- Added `00_discover_ch2025_stations_v4_1.py` to generate a frontend station catalog from CH2025 STAC.
- Improved station capability logic: target-state missing, reference-state missing, and hourly missing are now distinguished.
- Improved `rsds` near-zero handling in `03_build_daily_signal.py` by capping factors instead of emitting NaNs.
- Optimized EPW completion by replacing `iterrows()` with `itertuples()`.
- Frontend station selector now loads `frontend/data/stations_catalog.json`.

## Single-station run

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install pandas numpy
bash run_sma_gwl2_full_pipeline_mac.sh
```

## Multi-station / multi-GWL run

```bash
python3 run_batch_pipeline_v4_1.py \
  --stations sma,bas,ber,lug,gve \
  --gwls gwl1.5,gwl2.0,gwl3.0 \
  --profiles seasonal_warm,peak_event,sustained_heat,nocturnal_heat \
  --continue-on-error
```

## Refresh frontend station catalog

```bash
python3 00_discover_ch2025_stations_v4_1.py --output frontend/data/stations_catalog.json
```

## Open frontend

```bash
cd frontend
python3 -m http.server 8000
```

Then open `http://localhost:8000` and import any generated `outputs/run_<station>_<gwl>/run_summary.json`.

## Important note on station metadata

EPW writing requires station metadata: latitude, longitude, timezone and elevation. For SMA a metadata JSON is bundled/created by previous runs. For additional stations, provide `station_metadata/<station>.json` or refresh the station catalog and update metadata before final EPW export. The pipeline can still generate candidate files and selection results, but final EPW geolocation should be checked before simulation.
