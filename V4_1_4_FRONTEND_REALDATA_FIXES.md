# v4.1.4 Frontend real-data and chart-stability fixes

This update addresses frontend issues observed after importing real `run_summary.json` files.

## Main fixes

1. Removed the Swiss Confederation / Federal Office identity bar
   - The interface no longer imitates an official federal website header.
   - The tool remains CH2025/MeteoSwiss-inspired but visually independent.

2. Removed synthetic chart fallbacks
   - The temperature carpet plot no longer fabricates a diurnal pattern when hourly data are missing.
   - CDF charts are drawn only when `selection_cdf` is present in `run_summary.json`.
   - `selection_cdf` now carries provenance metadata indicating that it was built from candidate daily summaries and CH2025 target files.

3. Fixed chart resizing instability
   - Canvas sizing now measures the parent container and resets CSS width before drawing.
   - The canvas transform uses `ctx.setTransform(...)` to avoid accumulated scaling artifacts.
   - Chart containers now have fixed logical heights and overflow protection.

4. CDF data consistency
   - The CDF target distributions are filtered to the common model chains present in the candidate archive when possible.
   - CDF panels now report data provenance and show an empty state instead of drawing fake data.

5. BPS panels hidden until real BPS data are available
   - BPS metrics and simulation-result-comparison cards are hidden when no `evaluation_metrics` are loaded.

## Usage

Re-run the backend summary generation, then import the updated `outputs/run_<station>_<gwl>/run_summary.json` into the frontend.
