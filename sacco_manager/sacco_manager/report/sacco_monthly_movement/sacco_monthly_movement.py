# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Movement for a period: membership, savings, shares and loans roll forward from opening to
closing, plus income and expenses from the general ledger. Used by the monthly management pack
and the AGM pack. Each roll-forward line can be ticked off against the GL control account."""

import frappe
from frappe import _
from frappe.utils import add_days, flt, getdate

DEBIT_TYPES = "('Withdrawal','Withholding Tax')"


def _one(sql, vals):
	return flt(frappe.db.sql(sql, vals)[0][0])


def movement(from_date, to_date):
	f, t = getdate(from_date), getdate(to_date)
	before = add_days(f, -1)
	v = {"f": f, "t": t, "b": before}
	s = frappe.get_cached_doc("SACCO Settings")
	rows = []

	def add(section, item, amount, note=""):
		rows.append({"section": section, "item": item, "amount": round(flt(amount), 2), "note": note})

	# membership
	add("Membership", "Members at start", _one("SELECT COUNT(*) FROM `tabSACCO Member` WHERE IFNULL(joining_date, creation) < %(f)s", v))
	add("Membership", "Joined in period", _one("SELECT COUNT(*) FROM `tabSACCO Member` WHERE IFNULL(joining_date, DATE(creation)) BETWEEN %(f)s AND %(t)s", v))
	add("Membership", "Withdrawn / inactive (current status)", _one("SELECT COUNT(*) FROM `tabSACCO Member` WHERE status IN ('Withdrawn','Inactive')", v))

	# savings
	sav_bal = f"SUM(CASE WHEN transaction_type IN {DEBIT_TYPES} THEN -amount ELSE amount END)"
	opening = _one(f"SELECT {sav_bal} FROM `tabSACCO Savings Transaction` WHERE docstatus=1 AND posting_date < %(f)s", v)
	add("Savings", "Opening balance", opening)
	moved = 0.0
	for typ, sign in (("Deposit", 1), ("Interest", 1), ("Dividend", 1), ("Withdrawal", -1), ("Withholding Tax", -1)):
		amt = _one("SELECT SUM(amount) FROM `tabSACCO Savings Transaction` WHERE docstatus=1 AND transaction_type=%(ty)s "
			"AND posting_date BETWEEN %(f)s AND %(t)s", {**v, "ty": typ})
		add("Savings", f"{typ}s" if typ != "Withholding Tax" else "Withholding tax", sign * amt)
		moved += sign * amt
	closing = opening + moved
	add("Savings", "Closing balance", closing)
	if s.savings_liability_account:
		gl = -_one("SELECT SUM(debit-credit) FROM `tabGL Entry` WHERE is_cancelled=0 AND account=%(a)s AND posting_date <= %(t)s",
			{**v, "a": s.savings_liability_account})
		add("Savings", f"GL {s.savings_liability_account}", gl, "OK" if abs(gl - closing) < 1 else f"Difference {gl - closing:,.2f}")

	# shares
	sh = "SUM(CASE WHEN transaction_type IN ('Purchase','Transfer In') THEN total_amount ELSE -total_amount END)"
	sh_open = _one(f"SELECT {sh} FROM `tabSACCO Share Ledger` WHERE docstatus=1 AND date < %(f)s", v)
	sh_in = _one("SELECT SUM(total_amount) FROM `tabSACCO Share Ledger` WHERE docstatus=1 AND transaction_type IN ('Purchase','Transfer In') AND date BETWEEN %(f)s AND %(t)s", v)
	sh_out = _one("SELECT SUM(total_amount) FROM `tabSACCO Share Ledger` WHERE docstatus=1 AND transaction_type NOT IN ('Purchase','Transfer In') AND date BETWEEN %(f)s AND %(t)s", v)
	add("Share capital (ledger)", "Opening", sh_open)
	add("Share capital (ledger)", "Purchases / transfers in", sh_in)
	add("Share capital (ledger)", "Redemptions / transfers out", -sh_out)
	add("Share capital (ledger)", "Closing", sh_open + sh_in - sh_out)
	add("Share capital (ledger)", "Member share balances (all time, incl. migrated)", _one("SELECT SUM(share_balance) FROM `tabSACCO Member`", v))

	# loans (principal)
	disb = "SELECT SUM(disbursed_amount) FROM `tabLoan Disbursement` WHERE docstatus=1 AND disbursement_date"
	repd = "SELECT SUM(principal_amount_paid) FROM `tabLoan Repayment` WHERE docstatus=1 AND DATE(posting_date)"
	l_open = _one(f"{disb} < %(f)s", v) - _one(f"{repd} < %(f)s", v)
	l_disb = _one(f"{disb} BETWEEN %(f)s AND %(t)s", v)
	l_rep = _one(f"{repd} BETWEEN %(f)s AND %(t)s", v)
	add("Loans (principal)", "Opening", l_open)
	add("Loans (principal)", "Disbursed", l_disb)
	add("Loans (principal)", "Principal repaid", -l_rep)
	add("Loans (principal)", "Closing", l_open + l_disb - l_rep)
	add("Loans (principal)", "Interest collected", _one("SELECT SUM(total_interest_paid) FROM `tabLoan Repayment` WHERE docstatus=1 AND DATE(posting_date) BETWEEN %(f)s AND %(t)s", v))
	add("Loans (principal)", "Penalty collected", _one("SELECT SUM(total_penalty_paid) FROM `tabLoan Repayment` WHERE docstatus=1 AND DATE(posting_date) BETWEEN %(f)s AND %(t)s", v))
	add("Loans (principal)", "Loans closed in period", _one("SELECT COUNT(*) FROM `tabLoan` WHERE docstatus=1 AND closure_date BETWEEN %(f)s AND %(t)s", v))

	# income & expenses from GL
	for root, label in (("Income", "Income"), ("Expense", "Expenses")):
		for r in frappe.db.sql(
			"""SELECT g.account, SUM(g.credit - g.debit) AS amt FROM `tabGL Entry` g JOIN `tabAccount` a ON a.name = g.account
			WHERE g.is_cancelled=0 AND a.root_type=%(r)s AND g.posting_date BETWEEN %(f)s AND %(t)s GROUP BY g.account""",
			{**v, "r": root}, as_dict=True):
			add(label, r.account, r.amt if root == "Income" else -r.amt)
	inc = sum(r["amount"] for r in rows if r["section"] == "Income")
	exp = sum(r["amount"] for r in rows if r["section"] == "Expenses")
	add("Result", "Surplus / (deficit) for period", inc - exp)
	return rows


def execute(filters=None):
	f = frappe._dict(filters or {})
	rows = movement(f.from_date, f.to_date)
	cols = [
		{"label": _("Section"), "fieldname": "section", "width": 170},
		{"label": _("Item"), "fieldname": "item", "width": 330},
		{"label": _("Amount"), "fieldname": "amount", "fieldtype": "Float", "precision": 2, "width": 150},
		{"label": _("Check"), "fieldname": "note", "width": 200},
	]
	return cols, rows
