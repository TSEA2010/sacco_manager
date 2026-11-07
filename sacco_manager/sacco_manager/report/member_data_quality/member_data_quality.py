# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""KYC / data-quality exceptions to fix before go-live and before relying on M-Pesa matching."""

import frappe
from frappe import _

from sacco_manager.utils import is_valid_kra_pin, normalize_ke_phone


def execute(filters=None):
	members = frappe.get_all(
		"SACCO Member",
		fields=["name", "member_no", "full_name", "branch", "phone", "email_id", "id_number", "kra_pin",
			"customer", "status", "date_of_birth", "contact_name"],
		order_by="member_no",
	)
	dup_no = _dups(members, "member_no")
	dup_id = _dups(members, "id_number")
	dup_phone = _dups(members, "phone")
	with_account = set(frappe.get_all("SACCO Savings Account", pluck="member"))
	data = []
	for m in members:
		issues = []
		if m.member_no in dup_no:
			issues.append(_("duplicate member no"))
		if not m.id_number:
			issues.append(_("no national ID"))
		elif m.id_number in dup_id:
			issues.append(_("duplicate national ID"))
		if not normalize_ke_phone(m.phone):
			issues.append(_("invalid mobile"))
		elif m.phone in dup_phone:
			issues.append(_("shared mobile"))
		if not m.email_id:
			issues.append(_("no email (cannot receive guarantor requests)"))
		if m.kra_pin and not is_valid_kra_pin(m.kra_pin):
			issues.append(_("KRA PIN format"))
		if not m.branch:
			issues.append(_("no branch / employer"))
		if not m.customer:
			issues.append(_("no linked customer"))
		if m.name not in with_account:
			issues.append(_("no savings account"))
		if not m.contact_name:
			issues.append(_("no next of kin"))
		if issues:
			data.append({**m, "issues": "; ".join(issues), "issue_count": len(issues)})
	cols = [
		{"label": _("Member"), "fieldname": "name", "fieldtype": "Link", "options": "SACCO Member", "width": 100},
		{"label": _("Member No"), "fieldname": "member_no", "width": 120},
		{"label": _("Name"), "fieldname": "full_name", "width": 180},
		{"label": _("Branch"), "fieldname": "branch", "width": 110},
		{"label": _("# Issues"), "fieldname": "issue_count", "fieldtype": "Int", "width": 80},
		{"label": _("Issues"), "fieldname": "issues", "width": 420},
	]
	return cols, sorted(data, key=lambda r: -r["issue_count"])


def _dups(rows, field):
	seen, dups = set(), set()
	for r in rows:
		v = r.get(field)
		if v and v in seen:
			dups.add(v)
		seen.add(v)
	return dups
