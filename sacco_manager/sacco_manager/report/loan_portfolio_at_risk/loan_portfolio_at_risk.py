# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Portfolio at Risk and prudential loan classification (SACCO Societies Regulations):
Performing 0 days, Watch 1-30, Substandard 31-180, Doubtful 181-360, Loss over 360,
with provisions of 1% / 5% / 25% / 50% / 100% of principal outstanding.

Days in arrears come from the oldest unpaid Lending demand as at the date, so the report
does not depend on Lending's own DPD/NPA flags (which the June 2026 bulk import distorted)."""

import frappe
from frappe import _
from frappe.utils import date_diff, flt, getdate

CLASSES = [  # (name, min days, max days, provision %)
	("Performing", 0, 0, 1),
	("Watch", 1, 30, 5),
	("Substandard", 31, 180, 25),
	("Doubtful", 181, 360, 50),
	("Loss", 361, 10**6, 100),
]


def classify(days):
	for name, lo, hi, prov in CLASSES:
		if lo <= days <= hi:
			return name, prov
	return "Loss", 100


def execute(filters=None):
	f = frappe._dict(filters or {})
	as_at = getdate(f.as_at)
	cond = [
		"l.docstatus=1",
		"l.status IN ('Disbursed','Partially Disbursed','Active')",
		"l.disbursement_date <= %(as_at)s",
	]
	vals = {"as_at": as_at}
	if f.loan_product:
		cond.append("l.loan_product=%(lp)s")
		vals["lp"] = f.loan_product
	loans = frappe.db.sql(
		f"""SELECT l.name, l.applicant, l.applicant_name, l.loan_product, l.disbursement_date,
			l.disbursed_amount, l.total_principal_paid, m.member_no, m.branch
		FROM `tabLoan` l LEFT JOIN `tabSACCO Member` m ON m.customer = l.applicant
		WHERE {" AND ".join(cond)}""",
		vals,
		as_dict=True,
	)
	if f.branch:
		loans = [x for x in loans if x.branch == f.branch]
	arrears = {
		r.loan: r
		for r in frappe.db.sql(
			"""SELECT loan, MIN(demand_date) AS oldest, SUM(outstanding_amount) AS overdue
			FROM `tabLoan Demand`
			WHERE docstatus=1 AND outstanding_amount > 0.5 AND demand_date <= %s
			GROUP BY loan""",
			as_at,
			as_dict=True,
		)
	}
	data = []
	buckets = {c[0]: [0, 0.0, 0.0] for c in CLASSES}
	for ln in loans:
		principal = max(0, flt(ln.disbursed_amount) - flt(ln.total_principal_paid))
		if principal <= 0.5:
			continue
		a = arrears.get(ln.name)
		days = max(0, date_diff(as_at, a.oldest)) if a else 0
		cls, prov = classify(days)
		provision = round(principal * prov / 100, 2)
		buckets[cls][0] += 1
		buckets[cls][1] += principal
		buckets[cls][2] += provision
		data.append({
			**ln,
			"principal_outstanding": principal,
			"overdue_amount": flt(a.overdue) if a else 0,
			"days_in_arrears": days,
			"classification": cls,
			"provision_pct": prov,
			"provision": provision,
		})
	data.sort(key=lambda r: -r["days_in_arrears"])
	gross = sum(b[1] for b in buckets.values())
	denom = gross or 1
	at_risk = sum(b[1] for k, b in buckets.items() if k != "Performing")
	par30 = sum(b[1] for k, b in buckets.items() if k not in ("Performing", "Watch"))
	summary = [
		{"label": _("Gross loan portfolio"), "value": gross, "datatype": "Currency"},
		{"label": _("PAR (>0 days)"), "value": round(at_risk / denom * 100, 2), "datatype": "Percent",
			"indicator": "Green" if at_risk / denom < 0.05 else "Red"},
		{"label": _("PAR 30"), "value": round(par30 / denom * 100, 2), "datatype": "Percent"},
		{"label": _("Required provision"), "value": sum(b[2] for b in buckets.values()), "datatype": "Currency"},
	]
	chart = {
		"data": {
			"labels": [c[0] for c in CLASSES],
			"datasets": [{"name": _("Principal"), "values": [buckets[c[0]][1] for c in CLASSES]}],
		},
		"type": "bar",
		"fieldtype": "Currency",
	}
	return _columns(), data, None, chart, summary


def _columns():
	return [
		{"label": _("Loan"), "fieldname": "name", "fieldtype": "Link", "options": "Loan", "width": 150},
		{"label": _("Member No"), "fieldname": "member_no", "width": 110},
		{"label": _("Borrower"), "fieldname": "applicant_name", "width": 170},
		{"label": _("Branch"), "fieldname": "branch", "width": 100},
		{"label": _("Product"), "fieldname": "loan_product", "width": 110},
		{"label": _("Disbursed"), "fieldname": "disbursement_date", "fieldtype": "Date", "width": 100},
		{"label": _("Principal Outstanding"), "fieldname": "principal_outstanding", "fieldtype": "Currency", "width": 140},
		{"label": _("Overdue Amount"), "fieldname": "overdue_amount", "fieldtype": "Currency", "width": 120},
		{"label": _("Days in Arrears"), "fieldname": "days_in_arrears", "fieldtype": "Int", "width": 100},
		{"label": _("Classification"), "fieldname": "classification", "width": 110},
		{"label": _("Provision %"), "fieldname": "provision_pct", "fieldtype": "Percent", "width": 90},
		{"label": _("Provision"), "fieldname": "provision", "fieldtype": "Currency", "width": 120},
	]
