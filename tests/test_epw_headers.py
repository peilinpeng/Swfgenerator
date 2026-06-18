import importlib.util
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[1] / "legacy" / "scripts" / "10c_update_epw_headers.py"
    spec = importlib.util.spec_from_file_location("update_epw_headers", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


H = _load_module()


def _summary():
    return {
        "station": "Basel.Binningen",
        "design_conditions": {
            "heating": {
                "DB_99.6": -6.2,
                "DB_99.0": -4.1,
                "DP_99.6": -9.5,
                "DP_99.6_MCDB": -4.5,
            },
            "cooling": {
                "DB_0.4": 33.1,
                "DB_0.4_MCWB": 21.6,
                "WB_0.4": 22.6,
                "WB_0.4_MCDB": 30.6,
                "DP_0.4": 20.2,
                "DP_0.4_MCDB": 25.7,
                "Enth_0.4": 68.2,
                "Enth_0.4_MDB": 30.5,
                "MCDBR_DB": 12.2,
                "MCDBR_WB": 11.2,
                "MCDBR_DP": 10.0,
                "MCDBR_Enth": 11.1,
            },
            "extremes": {
                "M_min": -9.1,
                "M_max": 35.2,
                "s_min": 3.2,
                "s_max": 1.9,
            },
        },
    }


def test_design_conditions_line_uses_gwl_summary():
    line = H.build_design_conditions_line(_summary(), "gwl2.0", Path("bas_gwl2.0.ddy"))

    assert line.startswith("DESIGN CONDITIONS,1,")
    assert "SWF multi-year future design-condition summary gwl2.0" in line
    assert "bas_gwl2.0.ddy" in line
    assert "33.1" in line
    assert "21.6" in line
    assert "12.2" in line


def test_update_header_inherits_reference_metadata_and_comments_policy():
    source_header = [
        "LOCATION,Basel.Binningen,BL,CHE,SRC-TMYx,066010,47.54110,7.58360,1.0,317.3",
        "DESIGN CONDITIONS,1,old",
        "TYPICAL/EXTREME PERIODS,0",
        "GROUND TEMPERATURES,0",
        "HOLIDAYS/DAYLIGHT SAVINGS,No,0,0,0",
        "COMMENTS 1,old",
        "COMMENTS 2,old",
        "DATA PERIODS,1,1,Data,Sunday,1/ 1,12/31",
    ]
    reference_header = list(source_header)
    reference_header[2] = "TYPICAL/EXTREME PERIODS,6,reference"
    reference_header[3] = "GROUND TEMPERATURES,3,reference"
    comments = H.build_comments(
        Path("fry_bas_ref91-20_to_gwl2.0.epw"),
        Path("CHE_BL_Basel.Binningen.066010_TMYx.epw"),
        _summary(),
        "gwl2.0",
        Path("bas_gwl2.0.ddy"),
    )

    updated = H.update_header(source_header, reference_header, "DESIGN CONDITIONS,1,new", comments)

    assert updated[0] == source_header[0]
    assert updated[1] == "DESIGN CONDITIONS,1,new"
    assert updated[2] == reference_header[2]
    assert updated[3] == reference_header[3]
    assert "DESIGN CONDITIONS regenerated" in updated[6]
    assert "Authoritative EnergyPlus autosizing design days" in updated[6]


def test_hourly_hash_changes_only_when_hourly_data_changes():
    data = ["2001,1,1,1,60,?9?9,1,2,3\n", "2001,1,1,2,60,?9?9,4,5,6\n"]
    assert H.hourly_hash(data) == H.hourly_hash(list(data))
    assert H.hourly_hash(data) != H.hourly_hash(data + ["2001,1,1,3,60,?9?9,7,8,9\n"])
