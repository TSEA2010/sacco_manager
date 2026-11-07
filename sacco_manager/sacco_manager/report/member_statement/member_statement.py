# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""One member's statement: savings, shares and loans with opening and running balances."""

import frappe
from frappe import _
from frappe.utils import flt, getdate

DEBIT_TYPES = ("Withdrawal", "Withholding Tax")


def execute(filters=None):
	f = frappe._dict(filters or {})
	member = frappe.get_doc("SACCO Member", f.member)
	member.check_permission("read")
	start, end = getdate(f.from_date), getdate(f.to_date)
	savings_rows, savings_total = _savings(member.name, start, end)
	rows = savings_rows + _shares(member.name, start, end)
	rows += _loans(member.customer, start, end) if member.customer else []
	return _columns(), rows, None, None, _summary(member, savings_total)


def _columns():
	return [
		{"label": _("Section"), "fieldname": "section", "fieldtype": "Data", "width": 90},
		{"label": _("Date"), "fieldname": "date", "fieldtype": "Date", "width": 100},
		{"label": _("Account"), "fieldname": "account", "fieldtype": "Data", "width": 170},
		{"label": _("Description"), "fieldname": "description", "fieldtype": "Data", "width": 230},
		{"label": _("Reference"), "fieldname": "reference", "fieldtype": "Data", "width": 150},
		{"label": _("Debit"), "fieldname": "debit", "fieldtype": "Currency", "width": 120},
		{"label": _("Credit"), "fieldname": "credit", "fieldtype": "Currency", "width": 120},
		{"label": _("Balance"), "fieldname": "balance", "fieldtype": "Currency", "width": 130},
	]


def _savings(member, start, end):
	out, total = [], 0.0
	for acc in frappe.get_all("SACCO Savings Account", filters={"member": member}, fields=["name", "account_type"]):
		opening = flt(frappe.db.sql(
			"""SELECT SUM(CASE WHEN transaction_type IN %(d)s THEN -amount ELSE amount END)
			FROM `tabSACCO Savings Transaction` WHERE savings_account=%(a)s AND docstatus=1 AND posting_date < %(s)s""",
			{"a": acc.name, "s": start, "d": DEBIT_TYPES})[0][0])
		bal = opening
		out.append({"section": "Savings", "date": start, "account": f"{acc.name} ({acc.account_type or ''})",
			"description": _("Opening balance"), "balance": bal, "bold": 1})
		for t in frappe.get_all("SACCO Savings Transaction",
				filters={"savings_account": acc.name, "docstatus": 1, "posting_date": ["between", [start, end]]},
				fields=["name", "posting_date", "transaction_type", "amount", "reference_no", "mode_of_payment", "remarks"],
				order_by="posting_date asc, creation asc"):
			is_dr = t.transaction_type in DEBIT_TYPES
			bal += -flt(t.amount) if is_dr else flt(t.amount)
			out.append({"section": "Savings", "date": t.posting_date, "account": acc.name,
				"description": " - ".join(x for x in (t.transaction_type, t.mode_of_payment, t.remarks) if x),
				"reference": t.reference_no or t.name, "debit": flt(t.amount) if is_dr else 0,
				"credit": 0 if is_dr else flt(t.amount), "balance": bal})
		total += bal
	return out, total


def _shares(member, start, end):
	out = []
	entries = frappe.get_all("SACCO Share Ledger", filters={"member": member, "docstatus": 1, "date": ["<=", end]},
		fields=["name", "date", "transaction_type", "shares", "total_amount", "reference_no"], order_by="date asc, creation asc")
	if not entries:
		cap = flt(frappe.db.get_value("SACCO Member", member, "share_balance"))
		return [{"section": "Shares", "date": end, "description": _("Share capital (no ledger entries)"), "balance": cap, "bold": 1}] if cap else []
	bal = 0.0
	opening_done = False
	for e in entries:
		sign = 1 if e.transaction_type in ("Purchase", "Transfer In") else -1
		if getdate(e.date) < start:
			bal += sign * flt(e.total_amount)
			continue
		if not opening_done:
			out.append({"section": "Shares", "date": start, "description": _("Opening share capital"), "balance": bal, "bold": 1})
			opening_done = True
		bal += sign * flt(e.total_amount)
		out.append({"section": "Shares", "date": e.date, "description": f"{e.transaction_type} {e.shares} shares",
			"reference": e.reference_no or e.name, "debit": flt(e.total_amount) if sign < 0 else 0,
			"credit": flt(e.total_amount) if sign > 0 else 0, "balance": bal})
	return out


def _loans(customer, start, end):
	from sacco_manager.lending_utils import loan_outstanding

	out = []
	for ln in frappe.get_all("Loan", filters={"applicant": customer, "docstatus": 1},
			fields=["name", "loan_product", "disbursement_date", "disbursed_amount", "status"], order_by="disbursement_date"):
		out.append({"section": "Loan", "date": ln.disbursement_date, "account": ln.name,
			"description": f"{ln.loan_product} disbursed ({ln.status})", "debit": flt(ln.disbursed_amount), "bold": 1})
		for rp in frappe.get_all("Loan Repayment", filters={"against_loan": ln.name, "docstatus": 1,
				"posting_date": ["between", [start, end]]},
				fields=["name", "posting_date", "amount_paid", "principal_amount_paid", "total_interest_paid",
					"total_penalty_paid", "reference_number"], order_by="posting_date asc"):
			out.append({"section": "Loan", "date": rp.posting_date, "account": ln.name,
				"description": _("Repayment: principal {0}, interest {1}, penalty {2}").format(
					flt(rp.principal_amount_paid), flt(rp.total_interest_paid), flt(rp.total_penalty_paid)),
				"reference": rp.reference_number or rp.name, "credit": flt(rp.amount_paid)})
		if ln.status not in ("Closed", "Settled", "Written Off"):
			out.append({"section": "Loan", "date": end, "account": ln.name, "description": _("Amount payable as at date"),
				"balance": loan_outstanding(ln.name, end)["payable"], "bold": 1})
	return out


def _summary(member, savings_total):
	return [
		{"label": _("Member"), "value": f"{member.member_no} {member.full_name}", "datatype": "Data"},
		{"label": _("Savings balance"), "value": savings_total, "datatype": "Currency"},
		{"label": _("Share capital"), "value": flt(member.share_balance), "datatype": "Currency"},
	]
