# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Savings balance per account as at a date, rebuilt from posted transactions, with a
reconciliation line to the Members' Savings GL account."""

import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
	f = frappe._dict(filters or {})
	cond, vals = ["1=1"], {"as_at": f.as_at}
	if f.account_type:
		cond.append("a.account_type = %(account_type)s")
		vals["account_type"] = f.account_type
	if f.branch:
		cond.append("m.branch = %(branch)s")
		vals["branch"] = f.branch
	data = frappe.db.sql(
		f"""SELECT a.name AS savings_account, a.account_type, a.status, m.name AS member, m.member_no,
			m.full_name, m.branch,
			COALESCE(SUM(CASE WHEN t.transaction_type IN ('Withdrawal','Withholding Tax') THEN -t.amount
				ELSE t.amount END), 0) AS balance,
			MAX(t.posting_date) AS last_txn, a.balance AS stored_balance
		FROM `tabSACCO Savings Account` a
		JOIN `tabSACCO Member` m ON m.name = a.member
		LEFT JOIN `tabSACCO Savings Transaction` t
			ON t.savings_account = a.name AND t.docstatus = 1 AND t.posting_date <= %(as_at)s
		WHERE {" AND ".join(cond)}
		GROUP BY a.name ORDER BY m.member_no""",
		vals,
		as_dict=True,
	)
	from sacco_manager.utils import get_committed_pledges, get_multiplier

	with_pledges = set(frappe.db.sql_list("SELECT DISTINCT savings_account FROM `tabSACCO Loan Guarantor` WHERE status != 'Declined'"))
	mult = get_multiplier() or 1
	for d in data:
		d["locked_by_pledges"] = get_committed_pledges(d.savings_account) / mult if d.savings_account in with_pledges else 0
	total = sum(flt(d.balance) for d in data)
	acc = frappe.db.get_single_value("SACCO Settings", "savings_liability_account")
	gl = 0
	if acc:
		gl = flt(
			frappe.db.sql(
				"""SELECT SUM(credit - debit) FROM `tabGL Entry`
				WHERE account=%s AND is_cancelled=0 AND posting_date<=%s""",
				(acc, f.as_at),
			)[0][0]
		)
	summary = [
		{"label": _("Accounts"), "value": len(data), "datatype": "Int"},
		{"label": _("Total savings (sub-ledger)"), "value": total, "datatype": "Currency"},
	]
	if not (f.account_type or f.branch):  # the GL is company-wide; only compare an unfiltered total
		summary += [
			{"label": _("Members' Savings GL"), "value": gl, "datatype": "Currency"},
			{"label": _("Difference"), "value": total - gl, "datatype": "Currency",
				"indicator": "Green" if abs(total - gl) < 1 else "Red"},
		]
	return _columns(), data, None, None, summary


def _columns():
	return [
		{"label": _("Member No"), "fieldname": "member_no", "width": 120},
		{"label": _("Member"), "fieldname": "member", "fieldtype": "Link", "options": "SACCO Member", "width": 100},
		{"label": _("Name"), "fieldname": "full_name", "width": 180},
		{"label": _("Branch"), "fieldname": "branch", "width": 110},
		{"label": _("Savings Account"), "fieldname": "savings_account", "fieldtype": "Link",
			"options": "SACCO Savings Account", "width": 170},
		{"label": _("Type"), "fieldname": "account_type", "width": 120},
		{"label": _("Status"), "fieldname": "status", "width": 80},
		{"label": _("Balance"), "fieldname": "balance", "fieldtype": "Currency", "width": 130},
		{"label": _("Locked by Guarantees"), "fieldname": "locked_by_pledges", "fieldtype": "Currency", "width": 140},
		{"label": _("Stored Balance (today)"), "fieldname": "stored_balance", "fieldtype": "Currency", "width": 140},
		{"label": _("Last Transaction"), "fieldname": "last_txn", "fieldtype": "Date", "width": 110},
	]
