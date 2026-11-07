# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Employer (branch) check-off remittances: expected vs received per member, with posting status.
Rebuilt Nov 2026: the old version compared a month number with month names and used Lending
demand types that do not exist in this version, so it always returned nothing."""

import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
	f = frappe._dict(filters or {})
	cond, vals = ["r.docstatus < 2"], {}
	for key, col in (("branch", "r.branch"), ("month", "r.period_month"), ("year", "r.period_year")):
		if f.get(key):
			cond.append(f"{col} = %({key})s")
			vals[key] = f.get(key)
	rows = frappe.db.sql(
		f"""SELECT r.name AS remittance, r.branch, r.period_month, r.period_year, r.status AS remittance_status,
			r.docstatus, rm.member, rm.member_name, rm.expected_amount AS expected_savings,
			IFNULL(rm.received_savings, 0) AS received_savings, rm.loan,
			IFNULL(rm.expected_loan_repayment, 0) AS expected_loan, IFNULL(rm.received_loan_repayment, 0) AS received_loan,
			IFNULL(rm.share_amount, 0) AS shares, rm.status AS row_status
		FROM `tabSACCO Branch Remittance` r
		JOIN `tabSACCO Branch Remittance Member` rm ON rm.parent = r.name
		WHERE {" AND ".join(cond)}
		ORDER BY r.period_year DESC, r.branch, rm.member_name""",
		vals,
		as_dict=True,
	)
	for r in rows:
		r["expected_total"] = flt(r.expected_savings) + flt(r.expected_loan) + flt(r.shares)
		r["received_total"] = flt(r.received_savings) + flt(r.received_loan) + flt(r.shares)
		r["difference"] = r["received_total"] - r["expected_total"]
		r["state"] = "Draft" if r.docstatus == 0 else r.row_status
	summary = [
		{"label": _("Expected"), "value": sum(r["expected_total"] for r in rows), "datatype": "Currency"},
		{"label": _("Received"), "value": sum(r["received_total"] for r in rows), "datatype": "Currency"},
		{"label": _("Members short-paid"), "value": sum(1 for r in rows if r["difference"] < -0.5), "datatype": "Int"},
	]
	cols = [
		{"label": _("Remittance"), "fieldname": "remittance", "fieldtype": "Link", "options": "SACCO Branch Remittance", "width": 170},
		{"label": _("Employer / Branch"), "fieldname": "branch", "width": 120},
		{"label": _("Month"), "fieldname": "period_month", "width": 90},
		{"label": _("Year"), "fieldname": "period_year", "fieldtype": "Int", "width": 60},
		{"label": _("Member"), "fieldname": "member", "fieldtype": "Link", "options": "SACCO Member", "width": 100},
		{"label": _("Name"), "fieldname": "member_name", "width": 170},
		{"label": _("Expected Savings"), "fieldname": "expected_savings", "fieldtype": "Currency", "width": 115},
		{"label": _("Received Savings"), "fieldname": "received_savings", "fieldtype": "Currency", "width": 115},
		{"label": _("Loan"), "fieldname": "loan", "fieldtype": "Link", "options": "Loan", "width": 140},
		{"label": _("Expected Loan"), "fieldname": "expected_loan", "fieldtype": "Currency", "width": 110},
		{"label": _("Received Loan"), "fieldname": "received_loan", "fieldtype": "Currency", "width": 110},
		{"label": _("Shares"), "fieldname": "shares", "fieldtype": "Currency", "width": 90},
		{"label": _("Difference"), "fieldname": "difference", "fieldtype": "Currency", "width": 100},
		{"label": _("Status"), "fieldname": "state", "width": 80},
	]
	return cols, rows, None, None, summary
