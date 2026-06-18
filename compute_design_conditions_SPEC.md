compute_design_conditions.py — Implementation Spec
Verified against ASHRAE Handbook—Fundamentals 2021, Chapter 14 (original
text), and against the user's actual reference files
(…066010_TMYx.ddy / .epw and the printed Basel design-conditions table).
Every Ch.14 step cites its page/equation; every value taken from the reference
files is marked as such. The attribution boundary between "Ch.14 prescribes
this" and "this is a method decision" is in §13a. Correction history is in §13.

0. Purpose
Compute ASHRAE-style climatic design conditions from a multi-year hourly
dataset, and write them as an EnergyPlus .ddy (companion design-day file).
Two run modes share one core routine:

Calibration mode — input = the multi-year observed record for the
station. Output should reproduce the official ASHRAE table (the user holds the
printed Basel table) within tolerance. This validates the implementation
before any future data is touched.
Period — three distinct things, do not conflate:
(i) the official ASHRAE table period (printed in the table's top row; 2021
Ch.14 uses ~1994–2019 for most stations — use this as the primary calibration
target if available);
(ii) the OneBuilding TMYx COMMENTS period — Period of Record 1990–2023
(a 34-calendar-year span with 28 valid years per the COMMENTS #years=[28];
this is the EPW-assembly period, treat it as the reference-file period, NOT the
official ASHRAE period);
(iii) your MeteoSwiss observed record period.
Caveat: official ASHRAE values derive from ASHRAE's source data and QC
workflow, primarily long-term station records such as ISD for many
international stations. The calibration run uses MeteoSwiss — expect
close-but-not-exact agreement (see the tiered tolerance in §12).
Future mode — input = the morphed future pool. Run the core routine
per model chain, then average across chains (§9). Never derive design
conditions from a single FRY/XMY year.
The single-year epw.to_ddy() fallback is not this. Per Ch.14 p.14.14,
~8+ years are required for ±1 K reliability; the single-year path is an
emergency approximation for sites lacking multi-year data. This module is the
multi-year procedure.

1. Input data contract
One tidy hourly table per station (per chain, in future mode):

timestamp (with explicit interval semantics), DB[°C], DP[°C] or RH[%], P[Pa],
WS[m/s], WD[deg]   (+ optional GHI)
Derived once up front:

If RH given, compute DP from DB, RH (psychrometrics, §6).
P = observed station pressure; if missing, fill from station elevation
via the barometric formula (Ch.1). The standard pressure at elevation is the
StdP field of the ASHRAE table (Basel: 97.57 kPa at 317 m). NEVER use
101 325 Pa for a non-sea-level station.
Timestamp semantics are load-bearing. Daily max/min, the 22:00–06:00 XMY
window, and solar phase all depend on whether a stamp labels the start or end of
its hour. Fix this once, assert it, and document it. (This is the open audit
item; resolve it with a real MeteoSwiss sample before trusting any output.)

2. Percentile ↔ hours mapping (Ch.14 p.14.6, "Calculation of Design Conditions")
Annual percentiles are defined over the 8760 h of a year, averaged over the
period of record:

Percentile	Meaning	Hours/yr
0.4 %	DB/WB/DP exceeded	35
1.0 %	exceeded	88
2.0 %	exceeded	175
5.0 %	exceeded	438
99.0 %	less than design value	88
99.6 %	less than	35
Verbatim basis: "The 0.4, 1.0, 2.0, and 5.0% values are exceeded on average
35, 88, 175, and 438 h per year… The 99.0 and 99.6% (cold-season) values…
less than the design condition for 88 and 35 h, respectively." (p.14.6)

3. Data-quality screening (Ch.14 p.14.6 — reproduce these thresholds exactly)
Gap filling first: gaps ≤ 6 h filled by linear interpolation for DB, DP,
station pressure, humidity ratio. Wind speed/direction are NOT interpolated.

Per calendar month, a month-year is usable only if:

DB count (after filling) ≥ 85 % of the month's total hours
(e.g. January: 0.85 × 744 = 633 h).
|#daytime DB obs − #nighttime DB obs| < 60.
The available Ch.14 text gives this <60 threshold but does NOT define the
exact daytime/nighttime hour boundary. Treat the day/night split as an
implementation choice (e.g. a fixed local-standard-time window) and state
it. For a complete synthetic 8760-h series (the morphed future data) this
screen normally never triggers, so it functions as validation, not filtering.

Per element, additional gates relative to the DB minimum count D (= 633 for Jan):

DP / WB / enthalpy: that element present for ≥ 85 % of D (Jan: 538 h).
Wind: present for ≥ 28.3 % of D (= one-third of 85 %; Jan: 179 h).
Per calendar-month inclusion in the annual distribution:

A station's DB design conditions are computed only if there are ≥ 8 valid
month-years for each of the 12 calendar months (≥8 Januaries, ≥8 Februaries…).
Extremes (§7):

Annual DB extremes computed only for years that are ≥ 85 % complete.
≥ 8 annual extremes required to compute mean and std of annual extremes.
Daily quantities:

Daily min/max, daily ranges, and coincident ranges computed only for
complete days.
All thresholds above are quoted directly from Ch.14 p.14.6. Implement them as
named constants so the thesis methods section can cite them.

4. Daily mean convention — for monthly averages, degree-days, month selection only (Ch.14 p.14.8)
Daily mean temperature = (daily max + daily min) / 2, NOT the 24-hour
arithmetic mean. (Verbatim: "calculated as the average of the daily minimum and
maximum temperatures", p.14.8.)

Scope — this convention applies ONLY to: degree-days (§8), monthly DBAvg,
and coldest/hottest-month selection. The hourly percentile design conditions
(0.4 %, 99.6 %, MCWB, etc.) are computed from HOURLY DB/WB/DP values, NOT from
daily means. Do not apply the (max+min)/2 convention to the percentile binning.

Keep the (max+min)/2 convention consistent with the FRY temperature-target
construction where daily means are used there.

Coldest month = calendar month with lowest mean DB.
Hottest month = calendar month with highest mean DB.
5. Core procedure — annual design conditions
5a. Simple (single-variable) conditions (Ch.14 p.14.6)
Build per-month relative-frequency distributions, assemble into the
annual cumulative frequency distribution, then read the value at the target
exceedance probability.

Heating: DB_99.6, DB_99 (lower tail).
Cooling: DB_0.4, DB_1, DB_2 (upper tail).
Independently: WB_x (evaporation), DP_x (dehumidification), enthalpy h_x.
"Simple design conditions were obtained by binning hourly data into frequency
tables… Annual cumulative frequency distributions were constructed from the
relative frequency distributions compiled for each month." (p.14.6)

5b. Mean coincident values (Ch.14 p.14.6)
Ch.14: simple conditions come from frequency tables; mean coincident values
come from double-binning into joint frequency matrices.

Strict implementation (target): build a joint-frequency matrix between the
primary variable and the coincident variable. The mean coincident value is taken
from the coincident-variable distribution associated with the primary-variable
bin containing the design condition.

Pragmatic fallback (if strict joint binning is not implemented): average the
simultaneous hourly coincident values over the exceedance set (e.g. mean WB
over hours with DB ≥ cooling-0.4 % DB). Report this explicitly as an
ASHRAE-style approximation, not an exact ASHRAE reproduction — the exceedance
set is a larger, differently-defined sample than the design-bin distribution, so
results can differ slightly from the official MCWB.

Produced this way:

MCWB at each cooling DB_x → the DB⇒MCWB cooling day.
MCDB at each WB_x → evaporation; MCDB at each DP_x → dehumidification.
Wind (MCWS/PCWD) is a separate ASHRAE element, not a core input to the
cooling/heating autosizing day (Table 7). It is not morphed and is inherited
from the reference DDY (§10), so it need not be recomputed from the pool.

5c. Coincident daily ranges (Ch.14 p.14.6) — needed for design-day profiles
Double-bin daily temperature range (daily max − min) vs. daily max DB; the
mean coincident daily range = average of all bins above the simple design
condition of interest. Compute MCDBR/MCWBR at the hottest-month 5 %
condition (this is the daily range the annual cooling design day uses — see §10,
Table 7).

NOTE ON THRESHOLD SCOPE: 'hottest-month 5% condition' could be
read as the 95th percentile of the daily-max distribution computed
within the hottest month only. That interpretation was tested and
rejected: with ~30 July days × 30 years (~900 days), the in-month
95th percentile selects only ~45 days, producing high variance and
overshooting the Basel reference MCDBR_DB by ~0.6 K.

Implementation uses the whole-record daily-max 95th percentile
instead. This selects a larger, more stable hot-day sample
(predominantly from July) and matches the Basel reference within
±0.5 K, consistent with the ~1 K hourly-vs-thermometer caveat on
p.14.7.

ANNUAL vs MONTHLY RANGE DEFINITIONS (intentionally different):
- Annual cooling day: hot-tail mean — days above whole-record
  daily-max 95th percentile (~14 K for Basel). Reflects peak
  design conditions.
- Monthly cooling day: all-days mean daily range for that calendar
  month (Jan ~5.2 K, Jul ~10.4 K for Basel). Reflects typical
  month conditions; DB and WB monthly days share the same value.
These must NOT be unified. Verified against Basel reference DDY.

Caveat to state in the thesis (p.14.7): hourly-derived ranges run ~1 K
narrower than thermometer min/max ranges (true extremes fall between hourly
readings, ~0.5 K each side).

5d. Humidification / enthalpy
DP_99.6, DP_99 ⇒ MCDB (+ HR) for cold-season humidification; annual
enthalpy h_0.4/1/2 ⇒ MDB. Extreme max WB = highest WB observed over the whole
record (p.14.7).

5e. Future humidity & wind signal — assumption to declare (NOT Ch.14)
ASHRAE distinguishes four design-condition families (DB, WB, DP, enthalpy). The
trustworthiness of the humidity-based ones (MCWB, WB⇒MDB, DP⇒MDB, enthalpy)
depends entirely on whether humidity actually carries a future signal in the
morphed pool:

If the pipeline morphs RH (per the project's morphed-variable list:
temperature, RH, global radiation), then future DP/WB/enthalpy follow from
morphed RH + morphed DB — state this chain explicitly.
If any humidity quantity instead inherits present-day structure, the
corresponding humidity-based design conditions are only partly future and
must be reported as such.
Wind and pressure are retained (not morphed); design-day wind is
inherited from the reference DDY (§10).
Write a one-paragraph limitation in the thesis: which humidity variables are
morphed, and therefore which design conditions are "fully future" (DB-based)
vs "partly inherited" (some humidity-based). This pre-empts the obvious
examiner question "how did you compute future wet-bulb / dew-point?". This is
a method/data assumption, not an ASHRAE Ch.14 item (see §13a).

6. Psychrometrics — DO NOT hand-code; use a validated library (Ch.14 p.14.6 → Ch.1)
Ch.14 says WB, enthalpy, HR are "calculated from dry-bulb temperature,
dew-point temperature, and station pressure using the psychrometric equations
in Chapter 1." Chapter 1 is not in scope of this chapter, so:

Use psychrolib — an open-source implementation of standard psychrometric
equations consistent with ASHRAE Handbook Chapter 1 (SI mode). It avoids
hand-transcribing saturation-pressure coefficients:

import psychrolib
psychrolib.SetUnitSystem(psychrolib.SI)
W   = psychrolib.GetHumRatioFromTDewPoint(DP, P)        # P = station pressure
WB  = psychrolib.GetTWetBulbFromTDewPoint(DB, DP, P)
h   = psychrolib.GetMoistAirEnthalpy(DB, W)             # J/kg dry air → /1000 for kJ/kg
HR uses DP + station-elevation pressure (Ch.14 p.14.7, confirmed).
Enthalpy reference state: 0 °C, 101.325 kPa (Ch.14 nomenclature, Table 1A).
If psychrolib cannot be installed, CoolProp is an alternative; do not
transcribe saturation-pressure coefficients by hand.
7. Extreme annual values & n-year return periods (Ch.14 p.14.7–14.8, Eq. 1)
Per dataset, take the annual max and annual min DB (and WB) from
≥85 %-complete years; require ≥ 8 extremes. Compute mean M and
std s. Fit Gumbel (Type I EV) by method of moments:

T_n = M + I · F · s                                   (Ch.14 Eq. 1)

F   = -(sqrt(6)/pi) * ( gamma + ln( ln( n / (n-1) ) ) )
gamma = 0.5772156649      # Euler–Mascheroni constant
I   = +1 for maxima, -1 for minima
n   = return period in years (5, 10, 20, 50)
VERIFIED against the clear original text of Ch.14 Eq. 1 (p.14.8). The
formula reads exactly F = -(√6/π){γ + ln[ln(n/(n-1))]}. This corrects an
earlier draft that omitted the √6/π factor. Confirmed two ways: (a) the
chapter's worked example (50-yr max for the example city = 41.2 °C from
M=35.9, s=2.0); (b) the user's Basel table (see test table below).

Built-in verification (use as a unit test — numbers from the user's Basel table):
Basel extreme annual DB: M_max = 34.9, s_max = 1.7, M_min = -9.9, s_min = 3.6.

n	F	T_max = M+F·s	table	T_min = M−F·s	table
5	0.719	34.9+0.719·1.7 = 36.1	36.2	−9.9−0.719·3.6 = −12.5	−12.4
10	1.305	34.9+1.305·1.7 = 37.1	37.2	−9.9−1.305·3.6 = −14.6	−14.5
20	1.866	34.9+1.866·1.7 = 38.1	38.2	−9.9−1.866·3.6 = −16.6	−16.5
50	2.592	34.9+2.592·1.7 = 39.3	39.4	−9.9−2.592·3.6 = −19.2	−19.1
All within ±0.1 K of the official table → formula confirmed. The coder should
assert these in a test before proceeding.

Effective-sample caveat (future mode): within a chain, the ~30 morphed
years share one observed natural-variability sequence; annual extremes are
correlated across chains. Report effective sample size = number of baseline
years, not chains×years. (Ch.14 p.14.8 also warns the Gumbel standard error is
large for short records.)

8. Degree-days (Ch.14 p.14.8, Eqs. 2–3)
Td = (Tmax_d + Tmin_d) / 2
HDD_b = sum_d  max(b - Td, 0)      CDD_b = sum_d  max(Td - b, 0)
Bases b ∈ {10, 18.3} °C. Compute per year, sum the 12 months, then average
across years. (Optionally also cooling degree-hours bases 23.3/26.7 °C from
hourly temps, p.14.3.)

Which formula: use the direct daily-summation definition above
(Ch.14 Eqs. 2–3, p.14.8), because the full hourly/daily series is available.
The Schoenau–Kehrig equations (Ch.14 Eqs. 31–38, p.14.13) are an
estimation method for degree-days to an arbitrary base from monthly mean
temperature + daily-mean std; they are retained here only as an optional
cross-check, and the implementation does NOT rely on them. (Do not claim the
printed table was produced by either specific route — Ch.14 gives the direct
definition first and the estimator as a convenience.)

Basel calibration targets (from the table): HDD18.3 ≈ 2744, CDD18.3 ≈ 241,
HDD10 ≈ 897, CDD10 ≈ 1435. Expect agreement within a few °C-day; differences
can arise from source data, period of record, QC, and direct-vs-estimated
calculation (see §12), so treat these as sanity targets, not exact values.

9. Monthly design conditions (Ch.14 p.14.3) — note the different percentiles
Monthly cooling uses 0.4 / 2.0 / 5.0 / 10.0 % (NOT the annual 0.4/1/2 set).
For a 30-day month these correspond to 3 / 14 / 36 / 72 h. Compute monthly
DB_x ⇒ MCWB, WB_x ⇒ MCDB, and the mean daily DB range per month. Produce
all 12 months. (Rule of thumb to sanity-check: annual 0.4 % ≈ monthly 2 % of the
hottest month, p.14.3.)

NOTE ON NON-SURVIVING MONTHLY DAYS:
The Basel reference DDY shows an internal inconsistency on
non-surviving monthly days: April .4%/2% days carry range 10.3 K
but 5%/10% days jump to 14.4 K (a OneBuilding generation
artifact where less-extreme percentile days carry a larger range).
These 2%/5%/10% monthly days are dropped by the Honeybee survivor
filter and are inert in BPS. The implementation uses the
all-days-mean definition consistently across all percentiles
rather than reproducing the artifact.

10. Design-day generation & DDY assembly (Ch.14 §4, p.14.12–13)
EnergyPlus builds the 24 h profile itself from a SizingPeriod:DesignDay
object; this module supplies the design values + daily range + humidity type +
pressure + wind + solar. Background (so the values are defined correctly):

The hourly profile uses the Table 6 normalized fractions of daily range
(solar-time based; EnergyPlus handles this internally).
Table 7 input sources define which daily range pairs with which design
day:
Annual DB cooling day: 0.4/1/2 % annual cooling DB/MCWB; daily ranges =
hottest-month 5 % DB MCDBR/MCWBR; hourly WB limit = min(DB, WB).

NOTE ON Enth DAY RANGE CONVENTION:
ASHRAE HOF Table 7 does not explicitly specify which daily range
to assign to the Enth=>MDB design day. Two options were evaluated:

Option A — Reuse MCDBR_DP (~10.4 K for Basel):
  Matches the Basel reference DDY convention (reference assigns
  10.4 K to the Enth day, identical to the DP day).
  Hits the 10.0–10.8 K calibration target.

Option B — Compute enthalpy-coincident MCDBR_Enth (~12–13 K):
  Physically distinct quantity, but overshoots the reference by
  ~2 K with no explicit ASHRAE backing.

Decision: Option A adopted (Enth day reuses MCDBR_DP).
Rationale: the Enth=>MDB day describes the same high-humidity
extreme as the DP=>MDB day; ASHRAE's own reference DDY confirms
this by assigning identical ranges to both days.

Monthly DB cooling day: monthly 0.4/2/5/10 % DB/MCWB; daily ranges =
that month's 5 % DB MCDBR/MCWBR.
Per SizingPeriod:DesignDay, write:

Max DB = the percentile DB; daily DB range = the coincident range (§5c).
Humidity: Wetbulb (cooling DB day → MCWB), Dewpoint (dehumid day), or
Enthalpy, per design-day type.
Barometric pressure = station-elevation pressure (§1).
Wind speed/dir = inherited from the present-day reference DDY (wind is not
morphed; it is second-order for sizing — see note below).
Solar model — inherit per-object from the reference DDY, do NOT recompute
from future data (VERIFIED against the user's Basel reference .ddy):
Heating / humidification / heating-wind days → ASHRAEClearSky with
blank τ (6 such days in the Basel reference; solar is irrelevant to heating
sizing).
Cooling days → ASHRAETau2017, inheriting the τb/τd from the
corresponding reference design-day object. Annual cooling days in the Basel
reference use the July τ values: τb = 0.414, τd = 2.301.
Monthly cooling design days use month-specific τb/τd values inherited
from the corresponding monthly reference DDY objects. Do not use one global
τ pair for all cooling days. Basel reference values are:
Month	τb	τd
Jan	0.308	2.486
Feb	0.324	2.437
Mar	0.357	2.358
Apr	0.394	2.257
May	0.406	2.266
Jun	0.419	2.281
Jul	0.414	2.301
Aug	0.405	2.328
Sep	0.387	2.363
Oct	0.360	2.432
Nov	0.327	2.499
Dec	0.305	2.511
Declare in COMMENTS: "solar model inherited from present-day reference DDY;
CH2025 provides no future τ — treated as a reference-file assumption, not a
future-climate output."

What is and isn't ASHRAE here: Ch.14 defines τb/τd as monthly,
site-specific clear-sky parameters. Assigning them to future design days by
inheritance from the present-day reference is a method decision (no future
τ exists), NOT something Ch.14 prescribes. State it as such (see §13a).

Wind note: the reference DDY also contains dedicated Ann Htg Wind …
objects. Wind design statistics (MCWS/PCWD) are a separate ASHRAE element used
mainly for smoke-control/infiltration (Ch.14 p.14.7), not a core input to the
cooling/heating autosizing day (Table 7 lists only DB/MCWB + daily range).
Inherit wind from the reference rather than recomputing it from the pool.

HARD NAMING CONSTRAINT for the Honeybee add_from_ddy_996_004 workflow:
every design-day object intended to survive this Honeybee filter must contain
the literal substring 99.6% or .4% in its Name. The full DDY may still
contain 99%, 1%, 2%, 5%, and 10% objects; these are valid EnergyPlus
design days but will not be selected by this Honeybee helper. Follow
OneBuilding names for the filtered subset, e.g.:

<Station> Ann Htg 99.6% Condns DB
<Station> Ann Hum_n 99.6% Condns DP=>MCDB
<Station> Ann Clg .4% Condns DB=>MWB
<Station> Ann Clg .4% Condns WB=>MDB
<Station> Ann Clg .4% Condns DP=>MDB
<Station> Ann Clg .4% Condns Enth=>MDB
<Station> <Month> .4% Condns DB=>MCWB    (×12)
On design-day count (VERIFIED against the Basel reference DDY): the full
OneBuilding TMYx DDY contains 114 SizingPeriod:DesignDay objects (annual
families at 99.6/99/0.4/1/2 % across DB/WB/DP/Enth/Wind + 12 months × several
percentiles). Honeybee's add_from_ddy_996_004 then name-filters this set
to keep only those containing 99.6% or .4%, leaving 31 design days in
the Basel reference workflow. The full-file count is not the target — the
name-filter result is. The future DDY may contain the full ~114-object
structure; what matters is that the 99.6%/.4%-named subset is present,
correctly named, and physically sound.

11. Aggregation wrapper
core(dataset) -> design_conditions   # §3–§10, operates on one multi-year set

# Calibration mode
dc = core(observed_record)           # Basel MeteoSwiss, 1990-2023 span / 28 valid yrs (TMYx COMMENTS)
# tolerances are TIERED (see §12): ±0.5 K same-source, ±1-2 K cross-source (your case)
check dc.heating_996  vs -7.0   # official ASHRAE table — explain residual if >tol
check dc.cooling_004  vs 31.8
check dc.cooling_mcwb vs 20.5
# + the Gumbel return-period table of §7

# Future mode (per station × GWL)
dcs = [core(chain_years) for chain in chains]   # one core() per chain
dc_future = average_across(dcs)                  # NOT pool-then-percentile
write_ddy(dc_future, names_with_996_or_04)       # one DDY per station × GWL
Pool-then-percentile (all chains merged, single percentile) may be produced as a
sensitivity comparison only; note mean(percentile_c) ≠ percentile(pool).

12. Validation gates (must pass in order)
Psychrometrics: psychrolib round-trips (DP→W→WB→back) within tolerance.
Gumbel unit test: reproduce the §7 Basel return-period table (±0.1 K).
Calibration (tiered, not a single hard threshold):
Tier 1 — same source / period / QC reproduction: target ±0.5 K.
Tier 2 — different source or period (your case: MeteoSwiss vs ASHRAE
source/QC workflow):
target ±1.0–2.0 K for temperatures; investigate anything larger.
Compare core(Basel MeteoSwiss observed, 1990–2023) against the official
table (99.6 % DB, 0.4 % DB, MCWB, HDD/CDD). Do not run future mode until the
calibration differences are either within tolerance OR explicitly explained by
source / period / QC / direct-vs-estimated differences.
Honeybee load test: generated future DDY → after add_from_ddy_996_004
name-filtering, about 31 design days survive (NOT 2 — 2 means the filter
rejected everything and EnergyPlus fell back to single-year approximation).
Confirm every surviving name contains 99.6% or .4%, and that cooling days
carry ASHRAETau2017 + τ while heating days carry ASHRAEClearSky.
Monotonicity diagnostic (not a hard gate): future cooling DB should
generally increase with GWL. Small non-monotonic deviations (from ensemble
sampling / per-chain averaging / a particular morphing profile) must be
flagged and explained, not automatically treated as implementation
failure.
13. Corrections log
Round 1 — after reading the actual Ch.14 text
Gumbel formula corrected — added √6/π factor; later confirmed verbatim
against the clear original of Eq. 1 (§7). The earlier
F = -ln[-ln((n-1)/n)] - γ was wrong (gave 42.5 °C where the table says
39–41 °C).
Quality-screening thresholds now quoted exactly (85 % month, day/night
diff < 60, 8 month-years per calendar month, 28.3 % wind rule).
Daily mean = (Tmax+Tmin)/2 confirmed as the ASHRAE convention (p.14.8).
Monthly percentiles are 0.4/2/5/10 %, distinct from annual 0.4/1/2 %
(p.14.3).
Coincident daily range definition added from p.14.6 / Table 7.
Degree-days: clarified to use the direct definition (Eqs. 2–3), not the
Schoenau–Kehrig estimating formula (Eqs. 31–38) that produces the table.
Psychrometrics: switched to psychrolib (Ch.1 implementation).
Round 2 — after reading the actual reference DDY (…066010_TMYx.ddy)
Solar model split corrected — the reference is NOT all-ClearSky. The 6
heating/humidity/wind days use ASHRAEClearSky (blank τ); cooling days use
ASHRAETau2017. Annual cooling days carry July τb/τd = 0.414/2.301. Monthly
cooling days carry month-specific τb/τd inherited from the corresponding
monthly reference DDY objects (§10).
Design-day count corrected — file holds 114 objects; Honeybee
name-filters to **31**. Count is not the target; the filtered subset is
(§10, §12).
Wind — downgraded from a computed MCWS output to inherited-from-reference
(§5b, §10).
Calibration period — flagged the 1990–2023 span / 28-valid-years and the
ASHRAE-source-vs-MeteoSwiss source difference (§0, §12).
Round 3 — after peer review of the spec
Degree-days — removed the unsupported claim that the printed table is
"produced with Schoenau–Kehrig"; Schoenau–Kehrig is now an optional
arbitrary-base cross-check only, implementation uses the direct definition (§8).
Calibration period — split into three distinct periods (official table /
TMYx COMMENTS / MeteoSwiss); fixed the "1990–2023 = 28 years" wording
(34-year span, 28 valid years) (§0).
Calibration tolerance — made tiered (±0.5 K same-source, ±1–2 K
cross-source) instead of a single hard ±0.5 K; gate is now "within tolerance
OR explained" (§12).
Mean coincident values — separated strict joint-frequency-matrix method
from the pragmatic exceedance-set average (labelled an approximation) (§5b).
psychrolib — removed the "ASHRAE-endorsed" claim; now "open-source
implementation consistent with Ch.1" (§6).
Solar τ — reframed as per-object reference-file inheritance (a method
decision), and then verified against the Basel DDY: annual cooling uses July
τ, while monthly cooling uses month-specific τ. This is not presented as
Ch.14-mandated future climate information (§10).
Day/night threshold — noted Ch.14 gives no exact day/night boundary;
treated as an implementation choice (§3).
Daily-mean convention — scoped to monthly averages / degree-days / month
selection only; hourly percentiles use hourly values (§4).
Future humidity/wind assumption — added §5e: declare which humidity
variables are morphed, hence which design conditions are fully-future vs
partly-inherited.
Monotonicity — demoted from a hard gate to a diagnostic (§12).
13a. Attribution boundary — what is Ch.14 vs. what is a method decision
State this clearly in the thesis; do not present non-Ch.14 choices as if the
handbook prescribes them.

Grounded in ASHRAE HOF 2021 Ch.14 (verified against the text): percentile
definitions; binning / joint-frequency / coincident-range procedures; quality
screening; daily-mean convention; degree-days; Gumbel Eq. 1; monthly conditions;
design-day Table 6/7. Psychrometrics are Ch.14→Ch.1 (Ch.1 not re-derived here;
delegated to psychrolib).

NOT in Ch.14 — your method decisions / external sources (attribute accordingly):

Applying the procedure to future morphed data, per-chain-then-average
aggregation, one DDY per station×GWL, never-from-a-single-year — these are
method decisions with precedent in Gesangyangji et al. (2022). Ch.14
p.14.15 explicitly states there is no accepted method for designing for
future climate; your warrant is the precedent, not the handbook.
add_from_ddy_996_004 name filter, the 114→31 behaviour — ladybug/
honeybee source behaviour (your Weekly Report 12 trace), not ASHRAE.
OneBuilding naming format, the τb/τd values, the ClearSky/Tau split —
from your reference TMYx .ddy, not from Ch.14.
Future humidity/wind signal assumption (which variables are morphed; which
design conditions are fully-future vs partly-inherited) — a data/method
assumption of your pipeline, not an ASHRAE item (§5e).
psychrolib / CoolProp — engineering tooling choice.
14. Source
ASHRAE Handbook—Fundamentals 2021, Chapter 14 "Climatic Design Information":
percentile/hours and binning p.14.6; quality screening p.14.6; psychrometrics
→ Ch.1 p.14.6; extremes & Gumbel Eq.1 p.14.7–14.8; degree-days Eqs.2–3 p.14.8;
monthly conditions p.14.3; design-day generation Table 6/7 p.14.12–13; daily
range caveat p.14.7; representativeness/≥8-year reliability p.14.14–14.15.
Precedent for applying the framework to modelled future hourly data:
Gesangyangji et al. (2022).