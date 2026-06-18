"""Reference-DDY template parser, classifier, and patcher.

The future/observed DDY is produced by *patching* a station's present-day
reference ``.ddy`` (OneBuilding TMYx), not by hand-building a reduced family.
This guarantees the generated DDY reproduces the reference object structure
exactly:

    generated design-day count          == reference design-day count
    generated object family coverage    == reference object family coverage
    generated Honeybee survivor set     == reference survivor set
    solar model / tau / wind / object & field order / non-design-day objects
        are preserved verbatim unless a field is explicitly recalculated.

Only the fields that genuinely come from the computed design-condition summary
(Maximum Dry-Bulb, the coincident humidity value, optionally the daily
dry-bulb range, and the elevation barometric pressure) are overwritten. Every
other field — wind speed/direction, tau_b/tau_d, solar model indicator,
schedule/blank fields, rain/snow/DST flags, day/month, humidity condition type,
and any non-SizingPeriod:DesignDay helper object — is inherited exactly from the
reference template.

This module contains NO climate algorithm; it only maps already-computed
design-condition values onto the reference template and records a source map.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# EnergyPlus SizingPeriod:DesignDay field indices (token 0 == object type).
IDX_TYPE = 0
IDX_NAME = 1
IDX_MONTH = 2
IDX_DAY = 3
IDX_DAYTYPE = 4
IDX_MAXDB = 5
IDX_DBRANGE = 6
IDX_HUMTYPE = 9
IDX_HUMVAL = 10        # Wetbulb / DewPoint value slot
IDX_HUMRATIO = 12      # Humidity-Ratio value slot
IDX_ENTH = 13          # Enthalpy value slot {J/kg}
IDX_WBRANGE = 14
IDX_PRESSURE = 15
IDX_WS = 16
IDX_WD = 17

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Name percentile token -> design-condition dict key suffix.
PCTL_KEY = {
    "99.6": "99.6", "99": "99.0",
    ".4": "0.4", "1": "1.0", "2": "2.0", "5": "5.0", "10": "10.0",
}


class TemplateError(ValueError):
    pass


# ---------------------------------------------------------------------------
# IDF parsing (field + inline-comment preserving)
# ---------------------------------------------------------------------------

@dataclass
class IdfObject:
    fields: List[List[str]]      # [[value, comment], ...]; fields[0] == type token
    is_design_day: bool

    @property
    def type_name(self) -> str:
        return self.fields[0][0].strip()

    @property
    def name(self) -> str:
        return self.fields[IDX_NAME][0].strip() if len(self.fields) > IDX_NAME else ""

    def value(self, idx: int) -> Optional[str]:
        if 0 <= idx < len(self.fields):
            return self.fields[idx][0]
        return None

    def render(self) -> str:
        lines = [f"{self.fields[0][0]},"]
        n = len(self.fields)
        for i in range(1, n):
            val, comment = self.fields[i]
            sep = ";" if i == n - 1 else ","
            text = f"  {val}{sep}"
            if comment:
                # Parsed comments retain the IDF "- <field>" label convention
                # (the text after '!'); re-emit a single '!' so the rendered line
                # reads "!- <field>" exactly, not "!- - <field>".
                text += f"  !{comment}"
            lines.append(text)
        return "\n".join(lines)


def parse_idf(text: str) -> Tuple[List[IdfObject], str]:
    """Parse IDF text into objects. Returns (objects, preamble_comment_block).

    The preamble is the run of comment/blank lines before the first object; the
    caller replaces it with its own provenance header.
    """
    objects: List[IdfObject] = []
    cur: List[List[str]] = []
    pending = ""
    preamble_lines: List[str] = []
    seen_object = False

    for raw in text.splitlines():
        code, sep_mark, comment = raw.partition("!")
        comment = comment.strip()
        if not seen_object and not code.strip():
            # leading comment/blank line before any object
            preamble_lines.append(raw)
            continue
        seg = code
        toks: List[Tuple[str, str]] = []
        while True:
            ci = seg.find(",")
            si = seg.find(";")
            cands = [i for i in (ci, si) if i != -1]
            if not cands:
                pending += seg
                break
            cut = min(cands)
            sep = seg[cut]
            val = (pending + seg[:cut]).strip()
            pending = ""
            toks.append((val, sep))
            seg = seg[cut + 1:]
        for i, (val, sep) in enumerate(toks):
            seen_object = True
            c = comment if i == len(toks) - 1 else ""
            cur.append([val, c])
            if sep == ";":
                is_dd = cur[0][0].strip().lower() == "sizingperiod:designday"
                objects.append(IdfObject(fields=cur, is_design_day=is_dd))
                cur = []
    return objects, "\n".join(preamble_lines)


# ---------------------------------------------------------------------------
# Dynamic family classification
# ---------------------------------------------------------------------------

@dataclass
class Family:
    name: str                 # canonical family id
    computable: bool          # whether computed values can patch this object
    percentile: Optional[str] = None   # design-condition key suffix (e.g. "0.4")
    month: Optional[int] = None        # 1..12 for monthly families
    kind: Optional[str] = None         # DB/WB/DP/Enth (annual) or DB/WB (monthly)


def classify(obj_name: str) -> Optional[Family]:
    """Classify a SizingPeriod:DesignDay by its OneBuilding-style name.

    Returns None when the name does not match any known family (caller decides
    strict vs permissive handling). The percentile token is converted to the
    design-condition dict suffix; heating-wind days are classified but flagged
    non-computable (no wind statistic is produced -> inherit verbatim).
    """
    n = obj_name
    # Heating wind (DB and wind are the reference's coincident values; we do not
    # compute a wind percentile, so this object is inherited whole).
    if re.search(r"\bHtg\s+Wind\b", n, re.I):
        m = re.search(r"\b(99\.6|99)%", n)
        return Family("ann_htg_wind", computable=False,
                      percentile=PCTL_KEY.get(m.group(1)) if m else None)
    # Annual heating DB
    m = re.search(r"\bAnn\s+Htg\s+(99\.6|99)%\s+Condns\s+DB\b", n, re.I)
    if m:
        return Family("ann_htg_db", True, PCTL_KEY[m.group(1)], None, "DB")
    # Annual humidification DP=>MCDB
    m = re.search(r"\bAnn\s+Hum\w*\s+(99\.6|99)%\s+Condns\s+DP", n, re.I)
    if m:
        return Family("ann_humid_dp", True, PCTL_KEY[m.group(1)], None, "DP")
    # Annual cooling
    m = re.search(r"\bAnn\s+Clg\s+(\.4|1|2)%\s+Condns\s+(DB|WB|DP|Enth)", n, re.I)
    if m:
        return Family("ann_clg", True, PCTL_KEY[m.group(1)], None, m.group(2).title()
                      if m.group(2).lower() != "db" else "DB")
    # Monthly cooling
    m = re.search(r"\b([A-Za-z]+)\s+(\.4|2|5|10)%\s+Condns\s+(DB|WB)", n, re.I)
    if m and m.group(1).lower() in MONTHS:
        return Family("monthly_clg", True, PCTL_KEY[m.group(2)],
                      MONTHS[m.group(1).lower()], m.group(3).upper())
    return None


# ---------------------------------------------------------------------------
# Map computed design conditions onto a classified object
# ---------------------------------------------------------------------------

def _g(d: Dict[str, Any], key: str) -> Optional[float]:
    v = d.get(key)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def computed_values(fam: Family, dc: Dict[str, Any]) -> Dict[str, Any]:
    """Return the computed (max_db, hum_value, hum_slot) for a family, or {}.

    hum_slot is the field index where the humidity value belongs, matching the
    reference humidity condition type. Daily range / pressure are handled by the
    patcher (policy-dependent), not here.
    """
    heating = dc.get("heating", {})
    cooling = dc.get("cooling", {})
    monthly = dc.get("monthly", {})
    p = fam.percentile

    if fam.name == "ann_htg_db":
        v = _g(heating, f"DB_{p}")
        return {"max_db": v, "hum_value": v, "hum_slot": IDX_HUMVAL}
    if fam.name == "ann_humid_dp":
        return {"max_db": _g(heating, f"DP_{p}_MCDB"),
                "hum_value": _g(heating, f"DP_{p}"), "hum_slot": IDX_HUMVAL}
    if fam.name == "ann_clg":
        k = fam.kind
        if k == "DB":
            return {"max_db": _g(cooling, f"DB_{p}"),
                    "hum_value": _g(cooling, f"DB_{p}_MCWB"), "hum_slot": IDX_HUMVAL}
        if k == "Wb":
            return {"max_db": _g(cooling, f"WB_{p}_MCDB"),
                    "hum_value": _g(cooling, f"WB_{p}"), "hum_slot": IDX_HUMVAL}
        if k == "Dp":
            return {"max_db": _g(cooling, f"DP_{p}_MCDB"),
                    "hum_value": _g(cooling, f"DP_{p}"), "hum_slot": IDX_HUMVAL}
        if k == "Enth":
            enth = _g(cooling, f"Enth_{p}")
            return {"max_db": _g(cooling, f"Enth_{p}_MDB"),
                    "hum_value": (enth * 1000.0) if enth is not None else None,
                    "hum_slot": IDX_ENTH}
    if fam.name == "monthly_clg":
        entry = monthly.get(fam.month) or monthly.get(str(fam.month)) or {}
        if fam.kind == "DB":
            return {"max_db": _g(entry, f"DB_{p}"),
                    "hum_value": _g(entry, f"DB_{p}_MCWB"), "hum_slot": IDX_HUMVAL}
        if fam.kind == "WB":
            return {"max_db": _g(entry, f"WB_{p}_MCDB"),
                    "hum_value": _g(entry, f"WB_{p}"), "hum_slot": IDX_HUMVAL}
    return {}


# Cooling design-day family -> kind-specific daily-DB-range key. The daily range
# is coincident with the design's primary variable (a humid WB/DP/Enth design day
# has a smaller DB swing than a hot DB day); the reference DDY encodes this per
# family. classify() yields annual kinds DB/Wb/Dp/Enth and monthly kinds DB/WB.
# Enth reuses the DP-family range: the reference DDY pins the Enth=>MDB day to the
# DP range, not a separate enthalpy-coincident range (which runs ~WB-wide and
# overshoots the reference ~10.4 K by ~2 K).
_ANNUAL_RANGE_KEY = {"DB": "MCDBR_DB", "Wb": "MCDBR_WB", "Dp": "MCDBR_DP", "Enth": "MCDBR_DP"}
_MONTHLY_RANGE_KEY = {"DB": "MCDBR_DB", "WB": "MCDBR_WB"}


def computed_daily_range(fam: Family, dc: Dict[str, Any]) -> Tuple[Optional[float], str]:
    """Return (kind-specific cooling daily DB range, note).

    Prefers the family-specific MCDBR_<kind>; falls back to the generic MCDBR with
    an explicit note when the kind-specific value is absent (older summaries).
    Heating / humidification families return None (they keep the reference
    convention; no computed heating daily-range algorithm).
    """
    if fam.name == "ann_clg":
        cooling = dc.get("cooling", {})
        key = _ANNUAL_RANGE_KEY.get(fam.kind or "")
        if key is not None and _g(cooling, key) is not None:
            return _g(cooling, key), f"kind={fam.kind} coincident ({key})"
        v = _g(dc, "MCDBR")
        return v, "generic MCDBR fallback (kind-specific MCDBR_<kind> absent)"
    if fam.name == "monthly_clg":
        entry = dc.get("monthly", {}).get(fam.month) or dc.get("monthly", {}).get(str(fam.month)) or {}
        key = _MONTHLY_RANGE_KEY.get(fam.kind or "")
        if key is not None and _g(entry, key) is not None:
            return _g(entry, key), f"month={fam.month} kind={fam.kind} coincident ({key})"
        return _g(entry, "MCDBR"), "generic monthly MCDBR fallback (kind-specific absent)"
    return None, ""  # heating families keep their 0.0 range


# ---------------------------------------------------------------------------
# Patcher
# ---------------------------------------------------------------------------

SOURCE_COMPUTED_HOURLY = "computed_from_hourly_pool"
SOURCE_COMPUTED_PSYCHRO = "computed_from_psychrometrics"
SOURCE_COMPUTED_PRESSURE = "computed_from_elevation_pressure"
SOURCE_COMPUTED_RANGE = "computed_from_daily_range_statistics"
SOURCE_INHERIT = "inherited_from_reference_ddy"
SOURCE_TEMPLATE = "template_preserved"
SOURCE_UNCLASSIFIED = "unclassified_template_preserved"


@dataclass
class PatchResult:
    objects: List[IdfObject]
    source_map: List[Dict[str, Any]] = dc_field(default_factory=list)
    families: Dict[str, int] = dc_field(default_factory=dict)
    unclassified: List[str] = dc_field(default_factory=list)
    n_design_days: int = 0
    n_non_design_days: int = 0
    survivors: List[str] = dc_field(default_factory=list)


def name_survives_honeybee(name: str) -> bool:
    """Honeybee add_from_ddy_996_004 keeps names containing '99.6%' or '.4%'."""
    return ("99.6%" in name) or (".4%" in name)


def solar_model(obj: IdfObject) -> str:
    for val, _ in obj.fields:
        s = val.strip()
        if s in ("ASHRAEClearSky", "ASHRAETau", "ASHRAETau2017", "Schedule"):
            return s
    return ""


def _fmt(value: float, like: str) -> str:
    """Format a numeric value with a precision similar to the reference token."""
    if "." in like:
        decimals = len(like.split(".", 1)[1].rstrip())
        decimals = min(max(decimals, 1), 3)
        return f"{value:.{decimals}f}"
    return f"{value:.1f}"


def patch_template(
    objects: List[IdfObject],
    dc: Dict[str, Any],
    *,
    pressure_pa: Optional[float] = None,
    daily_range_policy: str = "compute",     # "compute" (production) | "inherit"
    pressure_policy: str = "compute",        # "compute" | "inherit"
    strictness: str = "permissive",          # "strict" | "permissive"
) -> PatchResult:
    """Patch computed design-condition values onto reference template objects.

    Only Maximum Dry-Bulb, the coincident humidity value, (optionally) the daily
    dry-bulb range and the barometric pressure are overwritten; every other field
    is preserved exactly from the reference object.
    """
    res = PatchResult(objects=objects)
    for obj in objects:
        if not obj.is_design_day:
            res.n_non_design_days += 1
            res.source_map.append({
                "object_name": obj.name or obj.type_name,
                "object_family": f"non_design_day:{obj.type_name}",
                "field_name": "*whole object*",
                "reference_value": "(verbatim)", "generated_value": "(verbatim)",
                "source": SOURCE_TEMPLATE, "note": "non-design-day helper object copied verbatim",
            })
            continue

        res.n_design_days += 1
        if name_survives_honeybee(obj.name):
            res.survivors.append(obj.name)

        fam = classify(obj.name)
        if fam is None:
            res.unclassified.append(obj.name)
            res.families["unclassified"] = res.families.get("unclassified", 0) + 1
            if strictness == "strict":
                raise TemplateError(
                    f"Unclassified reference design-day object in strict mode: "
                    f"'{obj.name}'. Add a family rule or run template-permissive.")
            res.source_map.append({
                "object_name": obj.name, "object_family": "unclassified",
                "field_name": "*whole object*",
                "reference_value": "(verbatim)", "generated_value": "(verbatim)",
                "source": SOURCE_UNCLASSIFIED,
                "note": "name did not match any known family; preserved unchanged",
            })
            continue

        res.families[fam.name] = res.families.get(fam.name, 0) + 1

        if not fam.computable:
            res.source_map.append({
                "object_name": obj.name, "object_family": fam.name,
                "field_name": "*whole object*",
                "reference_value": "(verbatim)", "generated_value": "(verbatim)",
                "source": SOURCE_TEMPLATE,
                "note": "no computed statistic for this family (e.g. wind day); "
                        "DB + wind inherited from reference",
            })
            continue

        cv = computed_values(fam, dc)
        # --- Maximum Dry-Bulb (computed) ---
        if cv.get("max_db") is not None:
            ref_val = obj.value(IDX_MAXDB)
            new_val = _fmt(cv["max_db"], ref_val or "0.0")
            res.source_map.append({
                "object_name": obj.name, "object_family": fam.name,
                "field_name": "Maximum Dry-Bulb Temperature",
                "reference_value": ref_val, "generated_value": new_val,
                "source": SOURCE_COMPUTED_HOURLY, "note": ""})
            obj.fields[IDX_MAXDB][0] = new_val
        # --- coincident humidity value (computed / psychro) ---
        if cv.get("hum_value") is not None:
            slot = cv["hum_slot"]
            while len(obj.fields) <= slot:
                obj.fields.append(["", ""])
            ref_val = obj.value(slot)
            new_val = (f"{cv['hum_value']:.0f}" if slot == IDX_ENTH
                       else _fmt(cv["hum_value"], ref_val or "0.0"))
            res.source_map.append({
                "object_name": obj.name, "object_family": fam.name,
                "field_name": obj.fields[IDX_HUMTYPE][0].strip() or "Humidity value",
                "reference_value": ref_val, "generated_value": new_val,
                "source": SOURCE_COMPUTED_PSYCHRO, "note": f"slot index {slot}"})
            obj.fields[slot][0] = new_val
        # --- daily dry-bulb range (policy) ---
        # Cooling families carry a computed mean-coincident DB range (annual
        # MCDBR / monthly MCDBR); under the production "compute" policy this is a
        # climate-dependent field and IS patched. Heating / humidification
        # families have no computed range statistic (computed_daily_range -> None)
        # so they keep the reference convention (no invented heating-range
        # algorithm); heating-wind objects never reach here (non-computable).
        cr, cr_note = (computed_daily_range(fam, dc) if daily_range_policy == "compute"
                       else (None, ""))
        if cr is not None:
            ref_val = obj.value(IDX_DBRANGE)
            new_val = _fmt(cr, ref_val or "0.0")
            res.source_map.append({
                "object_name": obj.name, "object_family": fam.name,
                "field_name": "Daily Dry-Bulb Temperature Range",
                "reference_value": ref_val, "generated_value": new_val,
                "source": SOURCE_COMPUTED_RANGE, "note": cr_note})
            obj.fields[IDX_DBRANGE][0] = new_val
        else:
            note = ("daily_range_policy=inherit" if daily_range_policy == "inherit"
                    else "no computed range for this family (heating); reference convention kept")
            res.source_map.append({
                "object_name": obj.name, "object_family": fam.name,
                "field_name": "Daily Dry-Bulb Temperature Range",
                "reference_value": obj.value(IDX_DBRANGE),
                "generated_value": obj.value(IDX_DBRANGE),
                "source": SOURCE_INHERIT, "note": note})
        # --- barometric pressure (policy) ---
        if pressure_policy == "compute" and pressure_pa is not None:
            ref_val = obj.value(IDX_PRESSURE)
            new_val = f"{pressure_pa:.0f}"
            res.source_map.append({
                "object_name": obj.name, "object_family": fam.name,
                "field_name": "Barometric Pressure",
                "reference_value": ref_val, "generated_value": new_val,
                "source": SOURCE_COMPUTED_PRESSURE, "note": "from station elevation"})
            obj.fields[IDX_PRESSURE][0] = new_val
        # --- inherited fields explicitly recorded ---
        for idx, label in ((IDX_WS, "Wind Speed"), (IDX_WD, "Wind Direction"),
                           (IDX_HUMTYPE, "Humidity Condition Type")):
            res.source_map.append({
                "object_name": obj.name, "object_family": fam.name,
                "field_name": label, "reference_value": obj.value(idx),
                "generated_value": obj.value(idx), "source": SOURCE_INHERIT, "note": ""})
        # tau (last two tokens for Tau solar model) — inherited verbatim
        if any("tau" in (obj.fields[j][1].lower()) for j in range(len(obj.fields))):
            res.source_map.append({
                "object_name": obj.name, "object_family": fam.name,
                "field_name": "tau_b / tau_d", "reference_value": "(reference)",
                "generated_value": "(reference)", "source": SOURCE_INHERIT,
                "note": "per-object optical depths preserved"})

    return res


def render_ddy(objects: List[IdfObject], header_comment: str) -> str:
    parts = [f"! {line}" for line in header_comment.splitlines()]
    parts.append("")
    for obj in objects:
        parts.append(obj.render())
        parts.append("")
    return "\n".join(parts)


def load_reference(path: Path) -> Tuple[List[IdfObject], str]:
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    return parse_idf(text)
