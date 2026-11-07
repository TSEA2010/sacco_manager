"""Fail CI if any JSON in the app is invalid or a DocType's field_order and fields disagree."""
import json
import pathlib
import sys

root = pathlib.Path(__file__).resolve().parents[1]
errors = []
for p in root.rglob("*.json"):
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        errors.append(f"{p}: invalid JSON ({e})")
        continue
    if isinstance(d, dict) and d.get("doctype") == "DocType":
        names = [f["fieldname"] for f in d.get("fields", [])]
        dup = {n for n in names if names.count(n) > 1}
        if dup:
            errors.append(f"{p}: duplicate fields {sorted(dup)}")
        missing = set(d.get("field_order", [])) ^ set(names)
        if missing:
            errors.append(f"{p}: field_order/fields mismatch {sorted(missing)}")
        for f in d.get("fields", []):
            if f.get("fieldtype") in ("Link", "Table", "Dynamic Link") and not f.get("options"):
                errors.append(f"{p}: {f['fieldname']} {f['fieldtype']} without options")
print("\n".join(errors) or "JSON OK")
sys.exit(1 if errors else 0)
