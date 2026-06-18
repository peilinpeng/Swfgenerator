"""Formal DDY comparison utility.

Compares a reference ``.ddy`` against a generated ``.ddy`` and writes a Markdown
report plus a per-field CSV. Objects are matched across files by their *classified
family signature* (family, percentile, kind, month) rather than raw string
equality, so naming differences (station-prefix punctuation, month full-name vs
abbreviation, ``MWB`` vs ``MCWB`` suffixes) are reported rather than silently
hiding a structural mismatch.

Usage:
    python3 ddy_compare.py --reference <ref.ddy> --generated <gen.ddy> \
        --out-md outputs/validation/ddy_compare_<station>_<tag>.md \
        --out-csv outputs/validation/ddy_compare_<station>_<tag>.csv
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ddy_template as T  # noqa: E402


def signature(name: str) -> Tuple:
    f = T.classify(name)
    if f is None:
        return ("unclassified", normalize_name(name))
    return (f.name, f.percentile, f.kind, f.month)


def normalize_name(name: str) -> str:
    n = name.lower()
    for full, ab in (("january", "jan"), ("february", "feb"), ("march", "mar"),
                     ("april", "apr"), ("june", "jun"), ("july", "jul"),
                     ("august", "aug"), ("september", "sep"), ("october", "oct"),
                     ("november", "nov"), ("december", "dec")):
        n = re.sub(rf"\b{full}\b", ab, n)
    n = n.replace("-", ".").replace("=>", "->")
    n = re.sub(r"\s+", " ", n).strip()
    return n


def _num(v: Optional[str]) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(str(v).strip().rstrip("."))
    except Exception:
        return None


def solar_model(obj: T.IdfObject) -> str:
    for val, _ in obj.fields:
        s = val.strip()
        if s in ("ASHRAEClearSky", "ASHRAETau", "ASHRAETau2017", "Schedule"):
            return s
    return ""


def tau_pair(obj: T.IdfObject) -> Tuple[Optional[float], Optional[float]]:
    if "tau" not in solar_model(obj).lower():
        return (None, None)
    return (_num(obj.value(len(obj.fields) - 2)), _num(obj.value(len(obj.fields) - 1)))


# Field comparison spec: (label, accessor)
def field_rows(ref: T.IdfObject, gen: T.IdfObject) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []

    def add(label: str, rv, gv, numeric=True):
        delta = ""
        if numeric:
            rn, gn = _num(rv), _num(gv)
            if rn is not None and gn is not None:
                delta = f"{gn - rn:+.3f}"
        rows.append({"field": label, "reference": rv, "generated": gv, "delta": delta})

    add("Maximum Dry-Bulb Temperature", ref.value(T.IDX_MAXDB), gen.value(T.IDX_MAXDB))
    add("Daily Dry-Bulb Range", ref.value(T.IDX_DBRANGE), gen.value(T.IDX_DBRANGE))
    add("Humidity Condition Type", ref.value(T.IDX_HUMTYPE), gen.value(T.IDX_HUMTYPE), numeric=False)
    add("Humidity Value (WB/DP slot)", ref.value(T.IDX_HUMVAL), gen.value(T.IDX_HUMVAL))
    add("Enthalpy slot", ref.value(T.IDX_ENTH), gen.value(T.IDX_ENTH))
    add("Daily Wet-Bulb Range", ref.value(T.IDX_WBRANGE), gen.value(T.IDX_WBRANGE))
    add("Barometric Pressure", ref.value(T.IDX_PRESSURE), gen.value(T.IDX_PRESSURE))
    add("Wind Speed", ref.value(T.IDX_WS), gen.value(T.IDX_WS))
    add("Wind Direction", ref.value(T.IDX_WD), gen.value(T.IDX_WD))
    add("Rain Indicator", ref.value(18), gen.value(18), numeric=False)
    add("Snow Indicator", ref.value(19), gen.value(19), numeric=False)
    add("Daylight Saving Indicator", ref.value(20), gen.value(20), numeric=False)
    add("Solar Model Indicator", solar_model(ref), solar_model(gen), numeric=False)
    rtb, rtd = tau_pair(ref)
    gtb, gtd = tau_pair(gen)
    add("tau_b", rtb, gtb)
    add("tau_d", rtd, gtd)
    return rows


def index_objects(objs: List[T.IdfObject]) -> Dict[Tuple, T.IdfObject]:
    out: Dict[Tuple, T.IdfObject] = {}
    for o in objs:
        if o.is_design_day:
            out[signature(o.name)] = o
    return out


def compare(ref_path: Path, gen_path: Path) -> Dict[str, Any]:
    ref_objs, _ = T.load_reference(ref_path)
    gen_objs, _ = T.load_reference(gen_path)

    ref_dd = [o for o in ref_objs if o.is_design_day]
    gen_dd = [o for o in gen_objs if o.is_design_day]
    ref_nd = [o for o in ref_objs if not o.is_design_day]
    gen_nd = [o for o in gen_objs if not o.is_design_day]

    ref_idx = index_objects(ref_objs)
    gen_idx = index_objects(gen_objs)

    matched = sorted(set(ref_idx) & set(gen_idx), key=lambda s: str(s))
    missing = sorted(set(ref_idx) - set(gen_idx), key=lambda s: str(s))  # in ref, not gen
    extra = sorted(set(gen_idx) - set(ref_idx), key=lambda s: str(s))    # in gen, not ref

    def fam_counts(objs):
        c: Dict[str, int] = {}
        for o in objs:
            f = T.classify(o.name)
            key = f.name if f else "unclassified"
            c[key] = c.get(key, 0) + 1
        return c

    field_diffs: List[Dict[str, Any]] = []
    name_diffs: List[Dict[str, str]] = []
    for sig in matched:
        ro, go = ref_idx[sig], gen_idx[sig]
        if ro.name != go.name:
            name_diffs.append({"signature": str(sig), "reference_name": ro.name,
                               "generated_name": go.name})
        for r in field_rows(ro, go):
            r2 = {"signature": str(sig), "reference_name": ro.name, **r}
            field_diffs.append(r2)

    return {
        "ref_path": str(ref_path), "gen_path": str(gen_path),
        "ref_total_dd": len(ref_dd), "gen_total_dd": len(gen_dd),
        "ref_nd": [o.type_name for o in ref_nd], "gen_nd": [o.type_name for o in gen_nd],
        "ref_survivors": [o.name for o in ref_dd if T.name_survives_honeybee(o.name)],
        "gen_survivors": [o.name for o in gen_dd if T.name_survives_honeybee(o.name)],
        "ref_families": fam_counts(ref_dd), "gen_families": fam_counts(gen_dd),
        "matched": matched, "missing": missing, "extra": extra,
        "ref_idx": ref_idx, "gen_idx": gen_idx,
        "name_diffs": name_diffs, "field_diffs": field_diffs,
        "ref_unclassified": [o.name for o in ref_dd if T.classify(o.name) is None],
        "gen_unclassified": [o.name for o in gen_dd if T.classify(o.name) is None],
    }


def _signum_field_diffs(field_diffs, label, tol=0.05):
    out = []
    for r in field_diffs:
        if r["field"] != label or not r["delta"]:
            continue
        try:
            if abs(float(r["delta"])) > tol:
                out.append(r)
        except Exception:
            pass
    return out


def write_md(res: Dict[str, Any], path: Path) -> None:
    L: List[str] = []
    L.append(f"# DDY comparison — reference vs generated\n")
    L.append(f"- Reference: `{res['ref_path']}`")
    L.append(f"- Generated: `{res['gen_path']}`\n")
    L.append("## Object structure\n")
    L.append("| metric | reference | generated | match |")
    L.append("|---|---|---|---|")
    L.append(f"| SizingPeriod:DesignDay count | {res['ref_total_dd']} | {res['gen_total_dd']} | "
             f"{'YES' if res['ref_total_dd']==res['gen_total_dd'] else 'NO'} |")
    L.append(f"| Honeybee survivors | {len(res['ref_survivors'])} | {len(res['gen_survivors'])} | "
             f"{'YES' if len(res['ref_survivors'])==len(res['gen_survivors']) else 'NO'} |")
    L.append(f"| non-design-day objects | {len(res['ref_nd'])} | {len(res['gen_nd'])} | "
             f"{'YES' if len(res['ref_nd'])==len(res['gen_nd']) else 'NO'} |")
    surv_match = set(res['ref_survivors']) == set(res['gen_survivors'])
    L.append(f"| survivor set identical (exact names) | — | — | {'YES' if surv_match else 'NO'} |")
    L.append("")
    L.append(f"Non-design-day objects (reference): {res['ref_nd']}")
    L.append(f"\nNon-design-day objects (generated): {res['gen_nd']}\n")

    L.append("## Object-family classification\n")
    L.append("| family | reference | generated |")
    L.append("|---|---|---|")
    fams = sorted(set(res['ref_families']) | set(res['gen_families']))
    for f in fams:
        L.append(f"| {f} | {res['ref_families'].get(f,0)} | {res['gen_families'].get(f,0)} |")
    L.append("")
    L.append(f"Reference unclassified design days: {res['ref_unclassified'] or 'none'}")
    L.append(f"\nGenerated unclassified design days: {res['gen_unclassified'] or 'none'}\n")

    L.append("## Missing / extra objects (by family signature)\n")
    L.append(f"- Missing (in reference, absent from generated): **{len(res['missing'])}**")
    for s in res['missing']:
        L.append(f"  - {s}")
    L.append(f"- Extra (in generated, absent from reference): **{len(res['extra'])}**")
    for s in res['extra']:
        L.append(f"  - {s}")
    L.append("")

    L.append("## Naming differences (matched objects)\n")
    if res['name_diffs']:
        L.append("| signature | reference name | generated name |")
        L.append("|---|---|---|")
        for d in res['name_diffs']:
            L.append(f"| {d['signature']} | {d['reference_name']} | {d['generated_name']} |")
    else:
        L.append("None — matched objects carry identical names.")
    L.append("")

    L.append("## Field-level differences (matched objects)\n")
    for label, tol in [("Wind Speed", 0.05), ("Wind Direction", 0.5),
                       ("Daily Dry-Bulb Range", 0.05), ("tau_b", 0.0005), ("tau_d", 0.0005),
                       ("Barometric Pressure", 1.0), ("Maximum Dry-Bulb Temperature", 0.05),
                       ("Humidity Value (WB/DP slot)", 0.05)]:
        diffs = _signum_field_diffs(res['field_diffs'], label, tol)
        L.append(f"### {label}: {len(diffs)} object(s) differ beyond {tol}")
        if diffs:
            L.append("| object | reference | generated | delta |")
            L.append("|---|---|---|---|")
            for r in diffs[:40]:
                L.append(f"| {r['reference_name']} | {r['reference']} | {r['generated']} | {r['delta']} |")
            if len(diffs) > 40:
                L.append(f"| … | … | … | (+{len(diffs)-40} more) |")
        L.append("")

    # Solar model / humidity-type mismatches (non-numeric)
    for label in ["Solar Model Indicator", "Humidity Condition Type"]:
        mismatches = [r for r in res['field_diffs']
                      if r['field'] == label and str(r['reference']).strip() != str(r['generated']).strip()]
        L.append(f"### {label}: {len(mismatches)} mismatch(es)")
        for r in mismatches[:20]:
            L.append(f"- {r['reference_name']}: ref=`{r['reference']}` gen=`{r['generated']}`")
        L.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(L), encoding="utf-8")


def write_csv(res: Dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["signature", "reference_name", "field", "reference_value",
                    "generated_value", "delta"])
        for r in res['field_diffs']:
            w.writerow([r['signature'], r['reference_name'], r['field'],
                        r['reference'], r['generated'], r['delta']])


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Compare reference vs generated DDY.")
    p.add_argument("--reference", required=True, type=Path)
    p.add_argument("--generated", required=True, type=Path)
    p.add_argument("--out-md", required=True, type=Path)
    p.add_argument("--out-csv", required=True, type=Path)
    a = p.parse_args(argv)
    res = compare(a.reference, a.generated)
    write_md(res, a.out_md)
    write_csv(res, a.out_csv)
    print(f"DDY compare: ref_dd={res['ref_total_dd']} gen_dd={res['gen_total_dd']} "
          f"survivors {len(res['ref_survivors'])}/{len(res['gen_survivors'])} "
          f"missing={len(res['missing'])} extra={len(res['extra'])} "
          f"name_diffs={len(res['name_diffs'])}")
    print(f"  wrote {a.out_md}")
    print(f"  wrote {a.out_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
