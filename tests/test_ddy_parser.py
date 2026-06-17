"""Reference DDY parser tests (Zurich Fluntern TMYx).

Protects the validated structure: 114 design days, 6 ClearSky / 108 Tau2017,
31 Honeybee survivors, July tau 0.407/2.321, 12 month-specific tau pairs.
"""
import pytest

from swfgenerator.design_conditions import (
    name_survives_honeybee_filter,
    parse_reference_ddy,
)
from swfgenerator.paths import bundled_reference_ddy


@pytest.fixture(scope="module")
def zurich_ddy():
    path = bundled_reference_ddy("zurich")
    if path is None:
        pytest.skip("Zurich reference DDY not available")
    return path


def test_zurich_reference_counts(zurich_ddy):
    info = parse_reference_ddy(zurich_ddy)
    objs = info["objects"]
    assert len(objs) == 114
    clearsky = [o for o in objs if o["solar_model"] == "ASHRAEClearSky"]
    tau2017 = [o for o in objs if o["solar_model"] == "ASHRAETau2017"]
    assert len(clearsky) == 6
    assert len(tau2017) == 108
    survivors = [o for o in objs if name_survives_honeybee_filter(o["name"])]
    assert len(survivors) == 31


def test_zurich_july_and_monthly_tau(zurich_ddy):
    info = parse_reference_ddy(zurich_ddy)
    assert info["monthly_tau"].get(7) == (0.407, 2.321)
    assert sorted(info["monthly_tau"].keys()) == list(range(1, 13))
    # Month-specific, not one global pair.
    assert len(set(info["monthly_tau"].values())) > 1
