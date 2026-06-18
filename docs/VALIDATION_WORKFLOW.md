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

### DDY generation: reference-template patching (not a reduced rebuild)

The future/observed `.ddy` is produced by **patching the station reference
`.ddy`** (`legacy/scripts/ddy_template.py`), not by hand-building a reduced
family. The generated DDY reproduces the reference object structure exactly:

```text
generated design-day count        == reference (Basel/Zurich TMYx: 114, not hard-coded)
generated object family coverage  == reference (per dynamic name classification)
generated Honeybee survivor set   == reference (identical names; 31)
non-design-day objects            == preserved verbatim (Site:Location, Site:Precipitation, …)
```

Only the genuinely computed fields are overwritten — **Maximum Dry-Bulb**, the
**coincident humidity value**, optionally the **daily dry-bulb range**
(`--daily-range-policy`), and the **elevation barometric pressure**
(`--pressure-policy`). Wind speed/direction, tau_b/tau_d, solar model, schedule
and flag fields, day/month, humidity-condition type, and object & field order are
**inherited per-object verbatim** from the reference (no default-wind
placeholder). `09b` flags: `--ddy-mode template|legacy` (default `template`;
`legacy` is the old reduced builder, used only when no reference DDY is
available), `--ddy-strictness strict|permissive`. Each run writes a per-field
**source map** (`<prefix>_ddy_source_map.csv`) labelling every field
`computed_from_*` vs `inherited_from_reference_ddy` / `template_preserved`.

Compare any generated DDY against its reference:

```bash
python3 legacy/scripts/ddy_compare.py \
    --reference data/reference/basel/CHE_BL_Basel.Binningen.066010_TMYx.ddy \
    --generated outputs/design_conditions_calibration/bas_calib.ddy \
    --out-md  outputs/validation/ddy_compare_bas_calibration.md \
    --out-csv outputs/validation/ddy_compare_bas_calibration.csv
```

Acceptance: `missing == 0`, `extra == 0`, `name_diffs == 0`, survivors equal and
identical names, and **zero** wind / tau / daily-range / pressure / solar-model /
humidity-type differences — the only field differences are Maximum Dry-Bulb and
the coincident humidity value (the recomputed climate signal). Do **not** accept
"survivors = 31" alone as success.

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

Configs: `basel_gwl1.5.yaml`, `basel_gwl2.yaml`, `basel_gwl2.5.yaml`,
`basel_gwl3.0.yaml`. Weather generation only (no BPS yet).

> **GWL availability (re-verified 2026-06-18):** CH2025 publishes GWL **1.5 / 2.0
> / 2.5 / 3.0** for Basel (`bas`) — and **no GWL4.0**. Confirmed by enumerating
> the STAC item assets for both the per-station DAILY-LOCAL collection
> (`ch.meteoschweiz.ogd-climate-scenarios-ch2025`, 70 assets = 5 states × 7 vars
> × 2 fmt) and the DAILY-GRIDDED collection (`…-ch2025-grid`); neither exposes a
> 4.0 level. An earlier note claiming 4.0 availability was incorrect: a fetch for
> the non-existent `gwl4.0` had silently resolved to GWL1.5 data and poisoned the
> cache, because `01_fetch_ch2025_asset.py::choose_asset` only *added* score for
> a state match instead of *requiring* it. `choose_asset` now requires both the
> variable and state filename tokens and raises a clear "no asset matched … no
> silent fallback" error for an absent state. The Basel matrix therefore uses
> **1.5 / 2.0 / 2.5 / 3.0**; `basel_gwl4.0.yaml` was removed.

```bash
for c in basel_gwl1.5 basel_gwl2 basel_gwl2.5 basel_gwl3.0; do
  swfgenerator full --config configs/examples/$c.yaml
done
```

Output layout (assembled under the git-ignored `outputs/basel_weather_batch/`):
`gwl<level>/{epw/ (5 EPW + sidecars), ddy/ (future DDY), reports/
(design-condition CSV/JSON/MD), run_summary.json}`. The `swfgenerator full`
pipeline natively writes EPWs to `outputs/epw/`, the run summary to
`outputs/run_bas_<state>/`, and the DDY + design-condition reports to
`<output_root>/design_conditions/`; the per-GWL `epw/ddy/reports` tree is a
post-run reorganisation of those native artifacts.

Climate-sanity expectations: cooling design DB rises with GWL; CDD rises; HDD
falls; heating design less severe; FRY/XMY profiles show distinct seasonal / peak
/ sustained / nocturnal signatures. Flag and explain any non-monotonic deviation
(ensemble sampling / per-chain averaging — diagnostic, not an automatic failure).

Verified results (2026-06-18; all monotonic; 20/20 EPWs pass; DDY survivors = 31;
no reference fallback; no solar-closure / night / DHI>GHI / cap-plateau flags):

```text
GWL    | FRY      | XMYseas | XMYpeak | XMYsust | XMYnoct | Clg0.4 | Htg99.6 | CDD18.3 | HDD18.3 | EPW | DDYsurv
gwl1.5 | composite| 2003    | 2019    | 2003    | 2003    | 32.38  | -6.85   | 305     | 2584    | 5/5 | 31
gwl2.0 | composite| 2003    | 2015    | 2003    | 2003    | 33.09  | -6.28   | 369     | 2435    | 5/5 | 31
gwl2.5 | composite| 2003    | 2015    | 2003    | 2003    | 33.83  | -5.75   | 444     | 2282    | 5/5 | 31
gwl3.0 | composite| 2003    | 2015    | 2003    | 2003    | 34.58  | -5.26   | 520     | 2149    | 5/5 | 31
```

FRY is a 12-month composite of best-fit monthly source years (not a single year);
the XMY columns report the selected `source_year` per profile. 2003 (European
heatwave) dominates the warm-season stress profiles, as expected.

## Phase 4 — Building-simulation preparation only (DO NOT run full matrix)

Final matrix (fixed case-study building):

```text
Station: Basel
GWL:     1.5 / 2.0 / 2.5 / 3.0   (CH2025 has no 4.0)
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
