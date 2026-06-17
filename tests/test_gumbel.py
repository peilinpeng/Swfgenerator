"""Gumbel return-period reproduction (SPEC s7 / Ch.14 Eq. 1).

Reproduces the Basel return-period table within +/-0.1 K via the validated
legacy 09b implementation (sqrt(6)/pi factor).
"""
from swfgenerator.design_conditions import gumbel_return_value, run_gumbel_unit_test


def test_gumbel_unit_test_passes():
    result = run_gumbel_unit_test()
    assert result["passed"] is True
    assert result["max_error_K"] <= 0.1 + 1e-9


def test_gumbel_basel_table_values():
    # Basel extreme annual DB: M_max=34.9, s_max=1.7, M_min=-9.9, s_min=3.6.
    expected = {5: (36.2, -12.4), 10: (37.2, -14.5), 20: (38.2, -16.5), 50: (39.4, -19.1)}
    for n, (tmax_ref, tmin_ref) in expected.items():
        tmax = round(gumbel_return_value(34.9, 1.7, n, "max"), 1)
        tmin = round(gumbel_return_value(-9.9, 3.6, n, "min"), 1)
        assert abs(tmax - tmax_ref) <= 0.1 + 1e-9, (n, tmax, tmax_ref)
        assert abs(tmin - tmin_ref) <= 0.1 + 1e-9, (n, tmin, tmin_ref)
