# Staged validation workflow (pre-Basel case study)

A controlled sequence to validate the weather-generation and DDY pipeline before
running the full Basel building-performance-simulation (BPS) matrix. **Weather
generation and DDY first; do not run the full BPS matrix until these pass.**

## Phase 1 — Basel observed calibration

Config: `configs/examples/basel_calibration.yaml`.

Calibration runs the validated design-condition engine in **calibration mode** on
Basel observed multi-year hourly data and compares against the printed Basel
targets (`compute_design_conditions_SPEC.md` §5/§7/§8, mirrored in the config's
`calibration_targets`). Calibration mode is not exposed through the `swfgenerator`
CLI (which runs future mode); invoke the legacy script directly:

```bash
# 1. fetch + parse Basel observed hourly (API)
python3 legacy/scripts/04a_fetch_meteoswiss_hourly_v4.py \
    --station bas --granularity h --update-types historical \
    --start-year 1991 --end-year 2020 --metadata
python3 legacy/scripts/04_parse_meteoswiss_hourly_v4.py \
    --input ./cache/meteoswiss/bas/h --station-id bas --output-format csv

# 2. calibration design conditions + DDY (Basel reference DDY, station elevation)
python3 legacy/scripts/09b_compute_design_conditions.py \
    --mode calibration \
    --input data_processed/hourly_obs/hourly_obs_bas_v4.csv \
    --station "Basel.Binningen" --elevation 317.3 \
    --reference-ddy data/reference/basel/CHE_BL_Basel.Binningen.066010_TMYx.ddy \
    --outdir outputs/design_conditions_calibration --out-prefix bas_calib \
    --cal-htg996 -7.0 --cal-clg004 31.8 --cal-clg004-mcwb 20.5 \
    --cal-hdd183 2744 --cal-cdd183 241
```

Acceptance (SPEC §12, tiered): Gumbel unit test ±0.1 K; same-source design
reproduction ±0.5 K; cross-source / period-mismatched (MeteoSwiss vs ASHRAE)
±1–2 K with residuals explained; Honeybee survivor count = 31 (never 2).

## Phase 2 — Basel GWL2.0 full pipeline smoke test

Config: `configs/examples/basel_gwl2.yaml` (output root `outputs/basel_gwl2_smoke/`,
git-ignored).

```bash
swfgenerator full --config configs/examples/basel_gwl2.yaml --dry-run   # preview
swfgenerator full --config configs/examples/basel_gwl2.yaml             # execute
```

Validate: future candidate pool, FRY/XMY selections, EPW-ready tables, EPW files,
future DDY, design-condition CSV/JSON/MD, run summary. EPW checks: 8760 h; correct
LOCATION/elevation; DB/RH/GHI morphed; DP/DHI/DNI derived; aux fields inherited;
night solar = 0; DHI ≤ GHI; solar closure failures = 0; no 101325 Pa default. DDY
checks: future conditions from the multi-year pool (per-chain then average); Basel
reference DDY; no silent fallback; survivors = 31; cooling τ inherited from Basel
monthly τ; heating days `ASHRAEClearSky`.

Failure classification: dependency / API-network / path-config / legacy-script /
missing-reference / overwrite-guard / real-algorithm-regression. **Only fix
package/orchestration/path/config issues — never EPW writer or 09b semantics.**

## Phase 3 — Basel four-GWL weather-output batch

Configs: `basel_gwl1.5.yaml`, `basel_gwl2.yaml`, `basel_gwl3.0.yaml`,
`basel_gwl4.0.yaml`. Weather generation only (no BPS yet).

> **GWL availability:** CH2025 publishes GWL 1.5 / 2.0 / 3.0 (the local archive
> also carried 2.5). **GWL 4.0 may not exist in CH2025** — verify against the
> CH2025 STAC before relying on `basel_gwl4.0.yaml`; if unavailable, substitute
> GWL 2.5 or drop 4.0.

Climate-sanity expectations: cooling design DB rises with GWL; CDD rises; HDD
falls; heating design less severe; FRY/XMY profiles show distinct seasonal / peak
/ sustained / nocturnal signatures. Flag and explain any non-monotonic deviation
(ensemble sampling / per-chain averaging — diagnostic, not an automatic failure).

Summary table to produce:

```text
GWL | FRY year | XMY seasonal | XMY peak | XMY sustained | XMY nocturnal | Clg0.4 DB | Htg99.6 DB | CDD18.3 | HDD18.3 | EPW validation | DDY survivor count
```

## Phase 4 — Building-simulation preparation only (DO NOT run full matrix)

Final matrix (fixed case-study building):

```text
Station: Basel
GWL:     1.5 / 2.0 / 3.0 / 4.0
Weather: FRY, XMY Seasonal, XMY Peak, XMY Sustained, XMY Nocturnal
```

Minimum BPS smoke test before the matrix:
1. baseline/reference EPW + baseline DDY;
2. Basel GWL2.0 FRY + future DDY;
3. Basel GWL2.0 Peak XMY + future DDY.

Check: Honeybee/EnergyPlus reads EPW/DDY; **no fallback to 2 design days**; sizing
completes; annual simulation completes; unmet hours reasonable; summary parser
reads outputs.

### Methodological note — which DDY drives autosizing (do not silently mix)

- **Fixed equipment sizing (recommended to isolate weather impact):** size the
  HVAC once on a **single pinned DDY** (e.g. present-day baseline DDY) and reuse
  that capacity across all GWLs/weather files. Only the hourly weather varies, so
  results attribute differences to weather, not to a moving equipment ceiling.
  This matches the thesis "capacity held constant within a comparison" principle.
- **Future sizing study:** use the **per-GWL future DDY** for autosizing to study
  how design requirements change with warming (capacity constant within a GWL,
  varying across GWLs).

These answer different questions. Pick one per experiment and state it explicitly;
do not combine a future DDY for one GWL with a baseline DDY for another in the same
comparison. Running both as a labelled sensitivity comparison is acceptable.
