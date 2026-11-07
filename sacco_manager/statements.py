# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Member statement as a branded PDF: download, email one member, or email all members."""

import frappe
from frappe import _
from frappe.utils import add_months, flt, format_date, getdate, today
from frappe.utils.pdf import get_pdf

TEMPLATE = "sacco_manager/templates/member_statement.html"


def _statement_context(member, from_date, to_date):
	from sacco_manager.sacco_manager.report.member_statement.member_statement import execute

	m = frappe.get_doc("SACCO Member", member)
	_cols, rows, _msg, _chart, summary = execute({"member": member, "from_date": from_date, "to_date": to_date})
	sections = {}
	for r in rows:
		sections.setdefault(r["section"], []).append(r)
	company = frappe.db.get_single_value("SACCO Settings", "company")
	return {
		"member": m,
		"from_date": format_date(from_date),
		"to_date": format_date(to_date),
		"printed_on": format_date(today()),
		"sections": sections,
		"summary": summary,
		"company": company,
		"logo": frappe.db.get_single_value("Website Settings", "app_logo") or "",
		"address": frappe.db.get_single_value("Website Settings", "address") or "",
		"footer": frappe.db.get_single_value("SACCO Settings", "statement_footer") or "",
		"fmt": lambda v: frappe.format_value(flt(v), {"fieldtype": "Currency"}) if v not in (None, "") else "",
	}


def statement_pdf(member, from_date, to_date):
	html = frappe.render_template(TEMPLATE, _statement_context(member, from_date, to_date))
	return get_pdf(html, {"page-size": "A4", "margin-top": "12mm", "margin-bottom": "12mm"})


def _default_period(from_date=None, to_date=None):
	to_date = getdate(to_date or today())
	from_date = getdate(from_date or add_months(to_date, -12))
	if from_date > to_date:
		frappe.throw(_("From date must be before To date."))
	return from_date, to_date


@frappe.whitelist()
def download_statement(member, from_date=None, to_date=None):
	frappe.has_permission("SACCO Member", "read", member, throw=True)
	from_date, to_date = _default_period(from_date, to_date)
	no = frappe.db.get_value("SACCO Member", member, "member_no") or member
	frappe.local.response.filename = f"Statement {no} {from_date} to {to_date}.pdf"
	frappe.local.response.filecontent = statement_pdf(member, from_date, to_date)
	frappe.local.response.type = "download"


def _send(member, from_date, to_date):
	m = frappe.db.get_value("SACCO Member", member, ["full_name", "email_id", "member_no"], as_dict=True)
	if not m or not m.email_id:
		return False
	frappe.sendmail(
		recipients=[m.email_id],
		subject=_("Your SACCO statement {0} to {1}").format(format_date(from_date), format_date(to_date)),
		message=_("Dear {0},<br><br>Please find attached your Trident SACCO statement for {1} to {2}.<br><br>"
			"If anything looks wrong, please contact the SACCO office.").format(
			frappe.utils.escape_html(m.full_name or ""), format_date(from_date), format_date(to_date)),
		attachments=[{"fname": f"Statement {m.member_no}.pdf", "fcontent": statement_pdf(member, from_date, to_date)}],
		reference_doctype="SACCO Member",
		reference_name=member,
	)
	return True


@frappe.whitelist()
def email_statement(member, from_date=None, to_date=None):
	frappe.has_permission("SACCO Member", "read", member, throw=True)
	from_date, to_date = _default_period(from_date, to_date)
	if not _send(member, from_date, to_date):
		frappe.throw(_("Member has no email address."))
	return _("Statement emailed.")


@frappe.whitelist()
def email_all_statements(from_date=None, to_date=None, branch=None):
	frappe.only_for(("SACCO Accountant", "SACCO Manager", "SACCO Administrator", "System Manager"))
	from_date, to_date = _default_period(from_date, to_date)
	filters = {"status": "Active", "email_id": ["is", "set"]}
	if branch:
		filters["branch"] = branch
	members = frappe.get_all("SACCO Member", filters=filters, pluck="name")
	frappe.enqueue("sacco_manager.statements._send_batch", members=members, from_date=str(from_date),
		to_date=str(to_date), queue="long", timeout=3600)
	return _("Queued statements for {0} members with an email address.").format(len(members))


def _send_batch(members, from_date, to_date):
	sent = failed = 0
	for name in members:
		try:
			sent += 1 if _send(name, from_date, to_date) else 0
			frappe.db.commit()
		except Exception:
			failed += 1
			frappe.db.rollback()
			frappe.log_error(title=f"Statement email failed {name}", message=frappe.get_traceback())
	frappe.logger("sacco_manager").info(f"statements sent={sent} failed={failed}")
