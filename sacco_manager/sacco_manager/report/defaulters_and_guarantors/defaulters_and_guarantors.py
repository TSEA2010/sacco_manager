# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Call list for credit follow-up: loans in arrears with the borrower's contacts, savings that
could be offset, and every guarantor with their pledge and phone."""

import frappe
from frappe import _
from frappe.utils import cint, date_diff, flt, getdate


def execute(filters=None):
	f = frappe._dict(filters or {})
	as_at = getdate(f.as_at)
	min_days = cint(f.min_days) if f.min_days not in (None, "") else 30
	arrears = frappe.db.sql(
		"""SELECT d.loan, MIN(d.demand_date) AS oldest, SUM(d.outstanding_amount) AS overdue
		FROM `tabLoan Demand` d
		WHERE d.docstatus = 1 AND d.outstanding_amount > 0.5 AND d.demand_date <= %s
		GROUP BY d.loan""",
		f"{as_at} 23:59:59",
		as_dict=True,
	)
	data = []
	for a in arrears:
		days = date_diff(as_at, getdate(a.oldest))
		if days < min_days:
			continue
		ln = frappe.db.get_value("Loan", a.loan, ["applicant", "applicant_name", "status", "docstatus", "disbursed_amount",
			"total_principal_paid", "loan_application", "disbursement_date"], as_dict=True)
		if not ln or ln.docstatus != 1 or ln.status not in ("Disbursed", "Partially Disbursed", "Active"):
			continue
		m = frappe.db.get_value("SACCO Member", {"customer": ln.applicant},
			["name", "member_no", "full_name", "branch", "phone", "email_id"], as_dict=True) or frappe._dict()
		if f.branch and m.branch != f.branch:
			continue
		savings = flt(frappe.db.sql("SELECT SUM(balance) FROM `tabSACCO Savings Account` WHERE member=%s", m.name)[0][0]) if m.name else 0
		guarantors = frappe.get_all("SACCO Loan Guarantor", filters={"loan_application": ln.loan_application, "is_self": 0,
			"status": ["!=", "Declined"]}, fields=["guarantor_member", "guarantor_name", "pledge_value"]) if ln.loan_application else []
		glist = "; ".join(
			f"{g.guarantor_name} ({frappe.db.get_value('SACCO Member', g.guarantor_member, 'phone') or 'no phone'}) pledge {flt(g.pledge_value):,.0f}"
			for g in guarantors
		)
		data.append({
			"loan": a.loan, "member_no": m.member_no, "borrower": m.full_name or ln.applicant_name, "branch": m.branch,
			"phone": m.phone, "email": m.email_id, "disbursed": ln.disbursement_date,
			"principal_outstanding": max(0, flt(ln.disbursed_amount) - flt(ln.total_principal_paid)),
			"overdue": flt(a.overdue), "days": days, "savings": savings,
			"guarantors": glist or _("none recorded"), "guarantor_count": len(guarantors),
		})
	data.sort(key=lambda r: (-r["days"], -r["overdue"]))
	summary = [
		{"label": _("Loans in arrears"), "value": len(data), "datatype": "Int"},
		{"label": _("Overdue amount"), "value": sum(r["overdue"] for r in data), "datatype": "Currency"},
		{"label": _("Principal at risk"), "value": sum(r["principal_outstanding"] for r in data), "datatype": "Currency"},
		{"label": _("Without guarantors"), "value": sum(1 for r in data if not r["guarantor_count"]), "datatype": "Int"},
	]
	return _columns(), data, None, None, summary


def _columns():
	c = lambda fn, label, ft="Data", w=120, **kw: {"label": _(label), "fieldname": fn, "fieldtype": ft, "width": w, **kw}  # noqa: E731
	return [
		c("loan", "Loan", "Link", 140, options="Loan"),
		c("member_no", "Member No", w=110),
		c("borrower", "Borrower", w=170),
		c("branch", "Employer / Branch", w=110),
		c("phone", "Phone", w=115),
		c("days", "Days in Arrears", "Int", 90),
		c("overdue", "Overdue", "Currency", 110),
		c("principal_outstanding", "Principal Outstanding", "Currency", 130),
		c("savings", "Borrower Savings", "Currency", 120),
		c("guarantors", "Guarantors (phone, pledge)", w=380),
		c("disbursed", "Disbursed", "Date", 95),
		c("email", "Email", w=160),
	]
