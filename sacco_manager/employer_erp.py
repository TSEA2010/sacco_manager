# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Link to each group company's own ERPNext payroll (HRMS).

The companies already deduct the SACCO from salaries with one deduction component (e.g. "Trident
Sacco") entered as an Additional Salary per employee per month. This module removes the re-typing:

push_deductions  SACCO -> company: for the month, one Additional Salary per member (savings
                 contribution + loan instalments) is created or updated as a DRAFT in the company's
                 ERPNext. Their HR checks and submits it before running payroll. Submitted entries are
                 never changed; differences are reported.
pull_deductions  company -> SACCO: after payroll, reads the submitted salary slips, takes each
                 employee's SACCO deduction and fills a draft SACCO Branch Remittance (loan instalments
                 first, then savings). The SACCO accountant reviews and submits it (maker-checker).

Members are matched by SACCO Member > Staff / Employee No = Employee ID in the company's ERPNext.
Authentication uses an API key/secret of a limited user in the company's site (SACCO Employer).
"""

import calendar
import json

import frappe
import requests
from frappe import _
from frappe.utils import cint, flt, getdate, now_datetime

from sacco_manager.lending_utils import get_active_loans

MONTHS = list(calendar.month_name)


class EmployerERP:
	"""Minimal REST client for a Frappe/ERPNext site."""

	def __init__(self, url, key, secret):
		self.url = url.rstrip("/")
		self.s = requests.Session()
		self.s.headers.update({"Authorization": f"token {key}:{secret}", "Accept": "application/json"})

	def _check(self, r):
		if r.status_code >= 400:
			try:
				msg = r.json().get("exception") or r.json().get("message") or r.text
			except ValueError:
				msg = r.text
			raise frappe.ValidationError(f"{r.status_code}: {str(msg)[:300]}")
		return r.json()

	def get_list(self, doctype, filters, fields, limit=0):
		r = self.s.get(f"{self.url}/api/resource/{doctype}", timeout=30, params={
			"filters": json.dumps(filters), "fields": json.dumps(fields), "limit_page_length": limit})
		return self._check(r).get("data", [])

	def get_doc(self, doctype, name):
		return self._check(self.s.get(f"{self.url}/api/resource/{doctype}/{name}", timeout=30)).get("data", {})

	def insert(self, doctype, doc):
		return self._check(self.s.post(f"{self.url}/api/resource/{doctype}", json=doc, timeout=30)).get("data", {})

	def update(self, doctype, name, values):
		return self._check(self.s.put(f"{self.url}/api/resource/{doctype}/{name}", json=values, timeout=30)).get("data", {})


def client_for(employer_doc):
	"""Overridable in tests (frappe.flags.sacco_erp_client)."""
	if frappe.flags.sacco_erp_client:
		return frappe.flags.sacco_erp_client
	if not (employer_doc.erp_enabled and employer_doc.erp_url and employer_doc.erp_api_key):
		frappe.throw(_("Set up the ERPNext connection on SACCO Employer {0} first.").format(employer_doc.name))
	return EmployerERP(employer_doc.erp_url, employer_doc.erp_api_key, employer_doc.get_password("erp_api_secret"))


def _period(month, year):
	m = month if isinstance(month, int) else MONTHS.index(month)
	start = getdate(f"{int(year)}-{m:02d}-01")
	end = getdate(f"{int(year)}-{m:02d}-{calendar.monthrange(int(year), m)[1]}")
	return m, start, end


def deduction_schedule(branch):
	"""What each member of the branch should have deducted this month."""
	rows = []
	for m in frappe.get_all("SACCO Member", filters={"branch": branch, "status": "Active"},
			fields=["name", "member_no", "full_name", "employee_id"], order_by="member_no"):
		savings = flt(frappe.db.sql("""SELECT SUM(monthly_contribution) FROM `tabSACCO Savings Account`
			WHERE member = %s AND status IN ('Active', 'Dormant')""", m.name)[0][0])
		loans = [(ln.name, flt(ln.monthly_repayment_amount)) for ln in (get_active_loans(m.name) or [])]
		total = savings + sum(a for _n, a in loans)
		if total > 0:
			rows.append(frappe._dict(member=m.name, member_no=m.member_no, name=m.full_name, employee=(m.employee_id or "").strip(),
				savings=savings, loans=loans, total=round(total, 2)))
	return rows


def _log(employer, text):
	frappe.db.set_value("SACCO Employer", employer.name, "last_sync", f"{now_datetime():%d-%m-%Y %H:%M} {text}"[:1000])
	employer.add_comment("Info", text[:2000])


@frappe.whitelist()
def push_deductions(employer, month, year):
	frappe.only_for(("System Manager", "SACCO Administrator", "SACCO Manager", "SACCO Accountant"))
	emp = frappe.get_doc("SACCO Employer", employer)
	m, start, end = _period(month, year)
	payroll_date = getdate(f"{int(year)}-{m:02d}-{min(cint(emp.payroll_day) or 25, end.day):02d}")
	api = client_for(emp)
	out = {"created": 0, "updated": 0, "unchanged": 0, "submitted_differs": [], "no_employee_id": [], "errors": []}
	for r in deduction_schedule(emp.branch):
		if not r.employee:
			out["no_employee_id"].append(f"{r.member_no} {r.name}")
			continue
		try:
			existing = api.get_list("Additional Salary", [["employee", "=", r.employee], ["salary_component", "=", emp.salary_component],
				["payroll_date", "between", [str(start), str(end)]], ["docstatus", "<", 2]], ["name", "amount", "docstatus"])
			if not existing:
				api.insert("Additional Salary", {"employee": r.employee, "company": emp.erp_company, "salary_component": emp.salary_component,
					"amount": r.total, "payroll_date": str(payroll_date), "overwrite_salary_structure_amount": 1,
					"remarks": f"SACCO {r.member_no}: savings {r.savings:,.0f}"
						+ "".join(f"; loan {n} {a:,.0f}" for n, a in r.loans)})
				out["created"] += 1
			elif cint(existing[0]["docstatus"]) == 0:
				if abs(flt(existing[0]["amount"]) - r.total) > 0.5:
					api.update("Additional Salary", existing[0]["name"], {"amount": r.total})
					out["updated"] += 1
				else:
					out["unchanged"] += 1
			elif abs(flt(existing[0]["amount"]) - r.total) > 0.5:
				out["submitted_differs"].append(f"{r.member_no} {r.name}: payroll {flt(existing[0]['amount']):,.0f}, SACCO {r.total:,.0f}")
			else:
				out["unchanged"] += 1
		except Exception as e:
			out["errors"].append(f"{r.member_no} {r.name}: {str(e)[:150]}")
	_log(emp, _("Pushed {0} {1} deductions: {2} created, {3} updated, {4} unchanged, {5} submitted with a different amount, "
		"{6} members without Employee ID, {7} errors.").format(MONTHS[m], year, out["created"], out["updated"], out["unchanged"],
		len(out["submitted_differs"]), len(out["no_employee_id"]), len(out["errors"])))
	return out


@frappe.whitelist()
def pull_deductions(employer, month, year):
	frappe.only_for(("System Manager", "SACCO Administrator", "SACCO Manager", "SACCO Accountant"))
	emp = frappe.get_doc("SACCO Employer", employer)
	m, start, end = _period(month, year)
	api = client_for(emp)
	filters = [["start_date", ">=", str(start)], ["end_date", "<=", str(end)], ["docstatus", "=", 1]]
	if emp.erp_company:
		filters.append(["company", "=", emp.erp_company])
	deducted = {}
	for s in api.get_list("Salary Slip", filters, ["name", "employee"]):
		slip = api.get_doc("Salary Slip", s["name"])
		amt = sum(flt(d.get("amount")) for d in slip.get("deductions", []) if d.get("salary_component") == emp.salary_component)
		if amt:
			deducted[s["employee"]] = deducted.get(s["employee"], 0) + amt
	if not deducted:
		frappe.throw(_("No submitted salary slips with {0} deductions found for {1} {2}.").format(emp.salary_component, MONTHS[m], year))
	members = {(r.employee_id or "").strip(): r.name for r in frappe.get_all("SACCO Member",
		filters={"branch": emp.branch, "employee_id": ["is", "set"]}, fields=["name", "employee_id"])}
	unmatched = {e: a for e, a in deducted.items() if e not in members}

	rem_name = frappe.db.get_value("SACCO Branch Remittance", {"branch": emp.branch, "period_month": MONTHS[m],
		"period_year": int(year), "docstatus": 0}, "name")
	rem = frappe.get_doc("SACCO Branch Remittance", rem_name) if rem_name else frappe.get_doc({
		"doctype": "SACCO Branch Remittance", "branch": emp.branch, "period_month": MONTHS[m], "period_year": int(year)})
	if rem.is_new():
		rem.insert()
	rem.fetch_members()  # expected savings and instalments per member
	by_member = {}
	for r in rem.members:
		by_member.setdefault(r.member, []).append(r)
	for emp_id, member in members.items():
		left = flt(deducted.get(emp_id))
		rows = by_member.get(member, [])
		for r in rows:  # loan instalments first, then savings; any excess to savings on the first row
			r.received_loan_repayment = min(left, flt(r.expected_loan_repayment))
			left -= r.received_loan_repayment
		for r in rows:
			r.received_savings = min(left, flt(r.expected_amount)) if r.savings_account else 0
			left -= r.received_savings
		if rows and left > 0.005 and rows[0].savings_account:
			rows[0].received_savings = flt(rows[0].received_savings) + left
	for r in rem.members:
		if r.member not in members.values():
			r.received_savings, r.received_loan_repayment = 0, 0  # no Employee ID: nothing read from payroll
	rem.received_amount = round(sum(deducted.values()), 2)
	rem.received_date = getdate(f"{int(year)}-{m:02d}-{min(cint(emp.payroll_day) or 25, end.day):02d}")
	rem.settlement = emp.remittance_settlement or "Paid to the SACCO in cash"
	rem.source = f"Payroll {emp.erp_url or 'ERP'}: {len(deducted)} slips"
	rem.save()
	note = _("Read {0} {1} payroll: {2} employees deducted {3}; draft remittance {4}.").format(
		MONTHS[m], year, len(deducted), frappe.format_value(rem.received_amount, {"fieldtype": "Currency"}), rem.name)
	if unmatched:
		note += " " + _("Not matched to a member (set Staff / Employee No): {0}.").format(
			", ".join(f"{e} {a:,.0f}" for e, a in list(unmatched.items())[:15]))
	_log(emp, note)
	return {"remittance": rem.name, "employees": len(deducted), "total": rem.received_amount, "unmatched": unmatched}
