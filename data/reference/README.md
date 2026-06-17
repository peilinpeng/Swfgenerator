# Reference weather files

This directory holds **present-day reference design-day templates** used by
`09b_compute_design_conditions.py` (see `compute_design_conditions_SPEC.md`, §10).

## Canonical location vs. packaged copy (avoid divergence)

The same reference `.ddy` templates exist in two places:

- `data/reference/<station>/` — **canonical, user-visible repository data.** This
  is what the example configs point to (`station.reference_ddy`) and what you
  should edit/add stations to.
- `src/swfgenerator/resources/reference/<station>/` — a **byte-identical packaged
  copy** so an installed wheel can still find a bundled reference when no config
  path is given. It is only a *fallback*: `stations.resolve_reference_ddy()` first
  uses the explicit config path, then this packaged copy.

Because the runtime prefers the config path (→ `data/reference`), the canonical
copy wins in normal use. **If you change a reference `.ddy`, update both copies**
(or regenerate the packaged copy from `data/reference`) to prevent divergence. The
duplication is intentional for wheel portability; if wheel installs are not
needed, the packaged copy under `src/.../resources/reference/` can be dropped and
the pipeline will still work from `data/reference`.

## `zurich/CHE_ZH_Zurich.Fluntern.066600_TMYx.2011-2025.ddy`

Reference design-day file (`.ddy`) for **Zürich-Fluntern / SMA** (WMO 066600).

### Purpose — template / metadata carrier
The pipeline passes this file via `--reference-ddy`. It is used **only** as a
template / metadata carrier to inherit:

- the **solar model** per design-day object (`ASHRAEClearSky` for
  heating / humidification / heating-wind days; `ASHRAETau2017` for cooling days);
- the **monthly clear-sky optical depths** `tau_b` / `tau_d` (annual cooling
  days inherit the **July** tau; monthly cooling days inherit the corresponding
  **month-specific** tau);
- **wind** and other **default design-day metadata**;
- the **OneBuilding naming convention** (so Honeybee's `add_from_ddy_996_004`
  filter keeps the `99.6%` / `.4%` subset);
- the overall **DDY object structure**.

### What is NOT taken from this file
The **future design temperatures and humidity statistics** (heating/cooling
dry-bulb percentiles, MCWB/MCDB, dew point, enthalpy, degree-days, return-period
extremes, daily ranges) are **re-computed from the multi-year morphed future
pool** per model chain, then averaged across chains. They are *not* read from
this reference file.

### Important caveats
- This reference DDY **does not represent a future-climate output**. CH2025
  provides no future `tau`, so the inherited solar parameters are a
  **reference-file assumption**, declared in the generated DDY `COMMENTS`.
- Source: **OneBuilding / TMYx** reference weather file
  (`CHE_ZH_Zurich.Fluntern.066600_TMYx.2011-2025`).

### Structure summary (for validation)
- Total `SizingPeriod:DesignDay` objects: **114**
- Solar models: **6 × `ASHRAEClearSky`**, **108 × `ASHRAETau2017`**
- Honeybee-surviving (`99.6%` / `.4%`) objects: **31**
- July tau: `tau_b = 0.407`, `tau_d = 2.321`

> The companion `.epw` (if present) is a large binary-ish data file and is
> **not committed** (see `.gitignore`); only the `.ddy` template is tracked.
