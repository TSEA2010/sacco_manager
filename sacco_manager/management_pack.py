# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Monthly management pack and AGM pack (PDF), built from the app's own reports and the GL.

Monthly: scheduled on the 5th for the previous month, emailed to SACCO Settings > Monthly Pack
Recipients and saved as a private file on SACCO Settings. AGM: same engine for a financial
year, plus membership by employer and a dividend proposal at a rate the Board enters.
"""

import frappe
from frappe import _
from frappe.utils import add_months, cint, flt, format_date, get_first_day, get_last_day, getdate, today
from frappe.utils.pdf import get_pdf

TEMPLATE = "sacco_manager/templates/management_pack.html"
STAFF = ("SACCO Accountant", "SACCO Manager", "SACCO Administrator", "SACCO Auditor", "System Manager")


def _trial_balance(to_date):
	return frappe.db.sql(
		"""SELECT g.account, a.root_type, SUM(g.debit) AS debit, SUM(g.credit) AS credit, SUM(g.debit - g.credit) AS balance
		FROM `tabGL Entry` g JOIN `tabAccount` a ON a.name = g.account
		WHERE g.is_cancelled = 0 AND g.posting_date <= %s
		GROUP BY g.account, a.root_type HAVING ABS(SUM(g.debit - g.credit)) > 0.005 ORDER BY a.lft""",
		to_date,
		as_dict=True,
	)


def build_context(from_date, to_date, title, agm=False, dividend_rate=None):
	from sacco_manager.sacco_manager.report.defaulters_and_guarantors.defaulters_and_guarantors import (
		execute as defaulters,
	)
	from sacco_manager.sacco_manager.report.loan_portfolio_at_risk.loan_portfolio_at_risk import (
		execute as par,
	)
	from sacco_manager.sacco_manager.report.sacco_monthly_movement.sacco_monthly_movement import movement

	f, t = getdate(from_date), getdate(to_date)
	s = frappe.get_cached_doc("SACCO Settings")
	_c, par_rows, _m, _par_chart, par_summary = par({"as_at": t})
	buckets = {}
	for r in par_rows:
		b = buckets.setdefault(r["classification"], [0, 0.0, 0.0])
		b[0] += 1
		b[1] += flt(r["principal_outstanding"])
		b[2] += flt(r["provision"])
	_c, dfl, _m, _ch, dfl_summary = defaulters({"as_at": t, "min_days": cint(s.get("arrears_days_threshold")) or 30})
	remittances = frappe.get_all(
		"SACCO Branch Remittance",
		filters={"docstatus": ["!=", 2], "received_date": ["between", [f, t]]},
		fields=["name", "branch", "period_month", "period_year", "expected_amount", "received_amount", "penalty_amount", "status"],
		order_by="branch",
	)
	tb = _trial_balance(t)
	ctx = {
		"title": title,
		"company": s.company,
		"from_date": format_date(f),
		"to_date": format_date(t),
		"generated": format_date(today()),
		"movement": movement(f, t),
		"par_summary": par_summary,
		"par_buckets": [(k, *v) for k, v in buckets.items()],
		"defaulters": dfl[:20],
		"defaulters_summary": dfl_summary,
		"remittances": remittances,
		"tb": tb,
		"tb_total": sum(flt(r.balance) for r in tb),
		"agm": agm,
		"fmt": lambda v: frappe.format_value(flt(v), {"fieldtype": "Float", "precision": 2}),
	}
	if agm:
		ctx["by_branch"] = frappe.db.sql(
			"""SELECT IFNULL(m.branch, 'Not set') AS branch, COUNT(*) AS members,
				SUM((SELECT IFNULL(SUM(a.balance), 0) FROM `tabSACCO Savings Account` a WHERE a.member = m.name)) AS savings,
				SUM(IFNULL(m.share_balance, 0)) AS shares
			FROM `tabSACCO Member` m GROUP BY IFNULL(m.branch, 'Not set') ORDER BY members DESC""",
			as_dict=True,
		)
		rate = flt(dividend_rate)
		capital = flt(frappe.db.sql("SELECT SUM(share_balance) FROM `tabSACCO Member`")[0][0])
		wht = flt(s.get("dividend_wht_rate"))
		gross = capital * rate / 100
		ctx["dividend"] = {"rate": rate, "capital": capital, "gross": gross, "wht_rate": wht,
			"wht": gross * wht / 100, "net": gross - gross * wht / 100}
	return ctx


def build_pdf(from_date, to_date, title, agm=False, dividend_rate=None):
	html = frappe.render_template(TEMPLATE, build_context(from_date, to_date, title, agm, dividend_rate))
	return get_pdf(html, {"page-size": "A4", "orientation": "Portrait", "margin-top": "10mm", "margin-bottom": "10mm"})


def _save(pdf, fname):
	f = frappe.get_doc({"doctype": "File", "file_name": fname, "is_private": 1, "content": pdf,
		"attached_to_doctype": "SACCO Settings", "attached_to_name": "SACCO Settings"})
	f.insert(ignore_permissions=True)
	return f.file_url


def _recipients():
	raw = frappe.db.get_single_value("SACCO Settings", "management_pack_recipients") or ""
	return [e.strip() for e in raw.replace(",", "\n").splitlines() if "@" in e]


@frappe.whitelist()
def generate_management_pack(month=None, year=None, send=0):
	frappe.only_for(STAFF)
	ref = getdate(f"{int(year)}-{int(month):02d}-01") if month and year else get_first_day(add_months(today(), -1))
	f, t = get_first_day(ref), get_last_day(ref)
	title = _("Management pack {0}").format(f.strftime("%B %Y"))
	pdf = build_pdf(f, t, title)
	url = _save(pdf, f"Management pack {f:%Y-%m}.pdf")
	if cint(send) and _recipients():
		frappe.sendmail(recipients=_recipients(), subject=title,
			message=_("Attached is the Trident SACCO management pack for {0}.").format(f.strftime("%B %Y")),
			attachments=[{"fname": f"Management pack {f:%Y-%m}.pdf", "fcontent": pdf}])
	return url


@frappe.whitelist()
def generate_agm_pack(from_date, to_date, dividend_rate=0):
	frappe.only_for(STAFF)
	title = _("AGM pack {0} to {1}").format(format_date(from_date), format_date(to_date))
	pdf = build_pdf(from_date, to_date, title, agm=True, dividend_rate=dividend_rate)
	return _save(pdf, f"AGM pack {getdate(from_date):%Y-%m-%d} to {getdate(to_date):%Y-%m-%d}.pdf")


def monthly_job():
	"""Scheduler (5th of the month): previous month's pack, emailed to the recipients list."""
	if not _recipients():
		return
	try:
		frappe.set_user("Administrator")
		ref = get_first_day(add_months(today(), -1))
		generate_management_pack(ref.month, ref.year, send=1)
	except Exception:
		frappe.log_error(title="SACCO management pack failed", message=frappe.get_traceback())
