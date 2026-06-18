"""Reference-DDY template reproduction tests.

The future/observed DDY is produced by patching the station reference .ddy. These
tests protect the guarantees that motivated the refactor:

  * object count / family coverage / survivor set reproduce the reference;
  * wind, tau, solar model, daily range, non-design-day objects are inherited
    verbatim (NOT defaulted);
  * only Maximum Dry-Bulb and the coincident humidity value are overwritten by
    computed design conditions;
  * strict mode fails on an unclassifiable design-day object.
"""
import pytest

from swfgenerator.ddy_template import module as _ddt_module
from swfgenerator.paths import bundled_reference_ddy

T = _ddt_module()


@pytest.fixture(scope="module")
def ref_path():
    for key in ("zurich", "basel"):
        p = bundled_reference_ddy(key)
        if p is not None:
            return p
    pytest.skip("no bundled reference DDY available")


@pytest.fixture
def ref_objects(ref_path):
    objs, _ = T.load_reference(ref_path)
    return objs


def _synthetic_dc():
    """Minimal computed design-condition dict with sentinel values for every
    field family, distinct from typical reference values so patches are visible."""
    cooling = {}
    for p in ("0.4", "1.0", "2.0"):
        cooling[f"DB_{p}"] = 99.0
        cooling[f"DB_{p}_MCWB"] = 88.0
        cooling[f"WB_{p}"] = 77.0
        cooling[f"WB_{p}_MCDB"] = 66.0
        cooling[f"DP_{p}"] = 55.0
        cooling[f"DP_{p}_MCDB"] = 44.0
        cooling[f"Enth_{p}"] = 33.0
        cooling[f"Enth_{p}_MDB"] = 22.0
    monthly = {}
    for m in range(1, 13):
        e = {"MCDBR": 9.0}
        for p in ("0.4", "2.0", "5.0", "10.0"):
            e[f"DB_{p}"] = 90.0 + m
            e[f"DB_{p}_MCWB"] = 50.0
            e[f"WB_{p}"] = 40.0
            e[f"WB_{p}_MCDB"] = 60.0
        monthly[m] = e
    return {
        "pressure_pa": 95000.0,
        "MCDBR": 12.34,
        "heating": {"DB_99.6": -99.0, "DB_99.0": -88.0,
                    "DP_99.6": -77.0, "DP_99.6_MCDB": -66.0,
                    "DP_99.0": -55.0, "DP_99.0_MCDB": -44.0},
        "cooling": cooling,
        "monthly": monthly,
    }


def test_reference_structure_classifies_fully(ref_objects):
    dd = [o for o in ref_objects if o.is_design_day]
    nd = [o for o in ref_objects if not o.is_design_day]
    assert len(dd) == 114
    assert len(nd) >= 1  # Site:Location etc. preserved
    survivors = [o for o in dd if T.name_survives_honeybee(o.name)]
    assert len(survivors) == 31
    unclassified = [o.name for o in dd if T.classify(o.name) is None]
    assert unclassified == []


def test_identity_roundtrip_preserves_everything(ref_path):
    objs, _ = T.load_reference(ref_path)
    text = T.render_ddy(objs, "roundtrip")
    objs2, _ = T.parse_idf(text)
    dd1 = [o for o in objs if o.is_design_day]
    dd2 = [o for o in objs2 if o.is_design_day]
    assert len(objs) == len(objs2)
    assert [o.name for o in dd1] == [o.name for o in dd2]
    # wind values survive a render/parse round-trip unchanged
    assert [o.value(T.IDX_WS) for o in dd1] == [o.value(T.IDX_WS) for o in dd2]


def test_patch_preserves_structure_and_inherits_wind(ref_path):
    objs, _ = T.load_reference(ref_path)
    ref_ws = {o.name: o.value(T.IDX_WS) for o in objs if o.is_design_day}
    ref_tau = {o.name: (o.value(len(o.fields) - 1)) for o in objs if o.is_design_day}
    objs2, _ = T.load_reference(ref_path)
    res = T.patch_template(objs2, _synthetic_dc(), pressure_pa=95000.0,
                           daily_range_policy="inherit", pressure_policy="compute",
                           strictness="permissive")
    dd = [o for o in res.objects if o.is_design_day]
    # structure preserved
    assert res.n_design_days == 114
    assert len(res.survivors) == 31
    assert res.unclassified == []
    # wind + tau inherited verbatim (NOT defaulted)
    for o in dd:
        assert o.value(T.IDX_WS) == ref_ws[o.name], f"wind changed on {o.name}"
        assert o.value(len(o.fields) - 1) == ref_tau[o.name], f"tau changed on {o.name}"
    # a computable object had Max Dry-Bulb overwritten by the computed value
    htg = next(o for o in dd if o.name.endswith("Ann Htg 99.6% Condns DB"))
    assert float(htg.value(T.IDX_MAXDB)) == pytest.approx(-99.0)


def test_daily_range_compute_policy_overwrites(ref_path):
    objs, _ = T.load_reference(ref_path)
    res = T.patch_template(objs, _synthetic_dc(), pressure_pa=95000.0,
                           daily_range_policy="compute", pressure_policy="inherit",
                           strictness="permissive")
    clg = next(o for o in res.objects
               if o.is_design_day and o.name.endswith("Ann Clg .4% Condns DB=>MWB"))
    # value is formatted to the reference field's precision (1 decimal here)
    assert float(clg.value(T.IDX_DBRANGE)) == pytest.approx(12.34, abs=0.05)


def test_strict_mode_raises_on_unclassified(ref_path):
    objs, _ = T.load_reference(ref_path)
    # corrupt one design-day name so it cannot be classified
    for o in objs:
        if o.is_design_day:
            o.fields[T.IDX_NAME][0] = "Totally Unknown Design Day"
            break
    with pytest.raises(T.TemplateError):
        T.patch_template(objs, _synthetic_dc(), pressure_pa=95000.0,
                         strictness="strict")


def test_source_map_labels_computed_vs_inherited(ref_path):
    objs, _ = T.load_reference(ref_path)
    # production defaults (daily_range_policy='compute', pressure_policy='compute')
    res = T.patch_template(objs, _synthetic_dc(), pressure_pa=95000.0)
    sources = {r["source"] for r in res.source_map}
    assert T.SOURCE_COMPUTED_HOURLY in sources       # Max Dry-Bulb
    assert T.SOURCE_COMPUTED_PSYCHRO in sources       # humidity value
    assert T.SOURCE_COMPUTED_PRESSURE in sources      # elevation pressure
    assert T.SOURCE_COMPUTED_RANGE in sources         # daily DB range (cooling, default)
    assert T.SOURCE_INHERIT in sources                # wind / tau / humidity type
    assert T.SOURCE_TEMPLATE in sources               # non-design-day + wind days


def test_default_policy_computes_cooling_range(ref_path):
    """Production default must patch cooling daily DB range (not inherit)."""
    objs, _ = T.load_reference(ref_path)
    res = T.patch_template(objs, _synthetic_dc(), pressure_pa=95000.0)
    clg = next(o for o in res.objects
               if o.is_design_day and o.name.endswith("Ann Clg .4% Condns DB=>MWB"))
    assert float(clg.value(T.IDX_DBRANGE)) == pytest.approx(12.34, abs=0.05)
    # the computed-range source row must exist for a cooling object
    range_rows = [r for r in res.source_map
                  if r["field_name"] == "Daily Dry-Bulb Temperature Range"
                  and r["source"] == T.SOURCE_COMPUTED_RANGE]
    assert any("Ann Clg" in r["object_name"] for r in range_rows)
    # heating objects keep the reference convention (inherited range)
    htg_range = [r for r in res.source_map
                 if r["field_name"] == "Daily Dry-Bulb Temperature Range"
                 and "Ann Htg 99.6% Condns DB" in r["object_name"]]
    assert htg_range and htg_range[0]["source"] == T.SOURCE_INHERIT
