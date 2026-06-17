"""Reference-DDY fallback policy (SPEC s10).

Future mode must NOT silently fall back to placeholder tau/wind: it fails by
default without a reference DDY, requires an explicit opt-in, and records the
fallback state. Verified at both the function and CLI levels.
"""
import subprocess
import sys

from swfgenerator.legacy_loader import load_legacy_module
from swfgenerator.paths import bundled_reference_ddy, legacy_script


def test_resolve_flags_fallback_without_reference():
    m = load_legacy_module("09b_compute_design_conditions.py", alias="swf_dc_fb")
    *_rest, is_fallback = m.resolve_tau_and_wind(None)
    assert is_fallback is True


def test_resolve_no_fallback_with_reference():
    m = load_legacy_module("09b_compute_design_conditions.py", alias="swf_dc_fb")
    ddy = bundled_reference_ddy("zurich")
    if ddy is None:
        import pytest
        pytest.skip("Zurich reference DDY not available")
    *_rest, is_fallback = m.resolve_tau_and_wind(ddy)
    assert is_fallback is False


def test_future_mode_refuses_silent_fallback():
    # Gate runs before data load, so this fails fast without touching the pool.
    proc = subprocess.run(
        [sys.executable, str(legacy_script("09b_compute_design_conditions.py")),
         "--mode", "future", "--input", "/nonexistent/pool.csv",
         "--station", "Test", "--elevation", "500"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 1
    combined = proc.stdout + proc.stderr
    assert "REFERENCE-DDY FALLBACK" in combined
    assert "must not silently fall back" in combined
