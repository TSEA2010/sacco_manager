"""Unit tests for pure helpers (no Frappe site needed). A tiny frappe stub lets the modules import."""
import sys
import types

frappe = types.ModuleType("frappe")
frappe._ = lambda s: s
frappe.throw = lambda msg, *a, **k: (_ for _ in ()).throw(Exception(msg))
frappe.whitelist = lambda *a, **k: (lambda fn: fn)
frappe.validate_and_sanitize_search_inputs = lambda fn: fn
utils = types.ModuleType("frappe.utils")
utils.flt = lambda v, *a: float(v or 0)
utils.cint = lambda v: int(float(v or 0))
for name in ("today", "getdate", "add_months", "date_diff", "add_days", "now_datetime"):
    setattr(utils, name, lambda *a, **k: None)
frappe.utils = utils
model = types.ModuleType("frappe.model")
document = types.ModuleType("frappe.model.document")
document.Document = object
sys.modules.update({"frappe": frappe, "frappe.utils": utils, "frappe.model": model, "frappe.model.document": document})

from sacco_manager.utils import is_valid_kra_pin, normalize_ke_phone


def test_phone_normalisation():
    assert normalize_ke_phone("0712 345 678") == "254712345678"
    assert normalize_ke_phone("+254 712-345-678") == "254712345678"
    assert normalize_ke_phone("712345678") == "254712345678"
    assert normalize_ke_phone("0110345678") == "254110345678"
    assert normalize_ke_phone("188") is None
    assert normalize_ke_phone("020 1234567") is None  # landline


def test_kra_pin():
    assert is_valid_kra_pin("A012345678Z")
    assert is_valid_kra_pin("p051234567x")
    assert not is_valid_kra_pin("A123")
    assert not is_valid_kra_pin("")


def test_emi_and_tenure():
    from sacco_manager.sacco_manager.doctype.loan_top_up.loan_top_up import emi, tenure

    assert emi(120000, 0, 12) == 10000
    e = emi(100000, 12, 12)
    assert 8884 < e < 8886  # standard amortised instalment at 1% a month
    assert tenure(100000, 12, e) == 12
