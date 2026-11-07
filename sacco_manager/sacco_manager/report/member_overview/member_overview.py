# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""One line per member for a month: savings balance at month end, deposits in the month,
share capital, open loans, principal outstanding and overdue demands.
Rebuilt Nov 2026: the old version looked for a non-existent 'Contribution' type and Lending
demand types, so every money column showed zero."""

import frappe
from frappe import _
from frappe.utils import flt, get_first_day, get_last_day, getdate


def execute(filters=None):
	f = frappe._dict(filters or {})
	month = int(f.month or getdate().month)
	year = int(f.year or getdate().year)
	start = get_first_day(f"{year}-{month:02d}-01")
	end = get_last_day(start)
	cond, vals = ["1=1"], {"s": start, "e": end}
	if f.branch:
		cond.append("m.branch = %(branch)s")
		vals["branch"] = f.branch
	if f.member:
		cond.append("m.name = %(member)s")
		vals["member"] = f.member
	data = frappe.db.sql(
		f"""SELECT m.name AS member, m.member_no, m.full_name, m.branch, m.share_balance,
			(SELECT IFNULL(SUM(a.monthly_contribution), 0) FROM `tabSACCO Savings Account` a WHERE a.member = m.name) AS monthly_contribution,
			(SELECT IFNULL(SUM(CASE WHEN t.transaction_type IN ('Withdrawal','Withholding Tax') THEN -t.amount ELSE t.amount END), 0)
				FROM `tabSACCO Savings Transaction` t WHERE t.member = m.name AND t.docstatus = 1 AND t.posting_date <= %(e)s) AS savings_balance,
			(SELECT IFNULL(SUM(t.amount), 0) FROM `tabSACCO Savings Transaction` t WHERE t.member = m.name AND t.docstatus = 1
				AND t.transaction_type = 'Deposit' AND t.posting_date BETWEEN %(s)s AND %(e)s) AS deposits_in_month,
			(SELECT COUNT(*) FROM `tabLoan` l WHERE l.applicant = m.customer AND l.docstatus = 1
				AND l.status IN ('Disbursed','Partially Disbursed','Active')) AS open_loans,
			(SELECT IFNULL(SUM(l.disbursed_amount - l.total_principal_paid), 0) FROM `tabLoan` l WHERE l.applicant = m.customer
				AND l.docstatus = 1 AND l.status IN ('Disbursed','Partially Disbursed','Active')) AS principal_outstanding,
			(SELECT IFNULL(SUM(d.outstanding_amount), 0) FROM `tabLoan Demand` d JOIN `tabLoan` l ON l.name = d.loan
				WHERE l.applicant = m.customer AND d.docstatus = 1 AND DATE(d.demand_date) <= %(e)s) AS overdue
		FROM `tabSACCO Member` m WHERE {" AND ".join(cond)} ORDER BY m.member_no""",
		vals,
		as_dict=True,
	)
	for r in data:
		r["contribution_gap"] = flt(r.deposits_in_month) - flt(r.monthly_contribution)
	cols = [
		{"label": _("Member No"), "fieldname": "member_no", "width": 110},
		{"label": _("Member"), "fieldname": "member", "fieldtype": "Link", "options": "SACCO Member", "width": 100},
		{"label": _("Name"), "fieldname": "full_name", "width": 170},
		{"label": _("Branch"), "fieldname": "branch", "width": 110},
		{"label": _("Monthly Contribution"), "fieldname": "monthly_contribution", "fieldtype": "Currency", "width": 120},
		{"label": _("Deposits in Month"), "fieldname": "deposits_in_month", "fieldtype": "Currency", "width": 120},
		{"label": _("Gap"), "fieldname": "contribution_gap", "fieldtype": "Currency", "width": 90},
		{"label": _("Savings at Month End"), "fieldname": "savings_balance", "fieldtype": "Currency", "width": 130},
		{"label": _("Share Capital"), "fieldname": "share_balance", "fieldtype": "Currency", "width": 110},
		{"label": _("Open Loans"), "fieldname": "open_loans", "fieldtype": "Int", "width": 70},
		{"label": _("Principal Outstanding"), "fieldname": "principal_outstanding", "fieldtype": "Currency", "width": 130},
		{"label": _("Overdue"), "fieldname": "overdue", "fieldtype": "Currency", "width": 110},
	]
	return cols, data
