# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Values for the workspace number cards."""

import frappe
from frappe.utils import flt, today


@frappe.whitelist()
def loan_book(filters=None):
	v = flt(frappe.db.sql("""SELECT SUM(disbursed_amount - total_principal_paid) FROM `tabLoan`
		WHERE docstatus=1 AND status IN ('Disbursed','Partially Disbursed','Active')""")[0][0])
	return {"value": v, "fieldtype": "Currency"}


@frappe.whitelist()
def par30(filters=None):
	from sacco_manager.sacco_manager.report.loan_portfolio_at_risk.loan_portfolio_at_risk import execute

	summary = execute({"as_at": today()})[4]
	v = next((s["value"] for s in summary if s["label"] == "PAR 30"), 0)
	return {"value": v, "fieldtype": "Percent"}


@frappe.whitelist()
def pending_approvals(filters=None):
	n = sum(frappe.db.count(dt, {"docstatus": 0}) for dt in ("Withdrawal Request", "SACCO Interest Run", "SACCO Dividend Run",
		"Loan Top-Up", "SACCO Branch Remittance"))
	n += frappe.db.count("Loan Application", {"status": "Open", "docstatus": ["!=", 2]})
	return {"value": n, "fieldtype": "Int"}
