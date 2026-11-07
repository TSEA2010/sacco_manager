# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Per member: savings, guarantee capacity, what they have pledged for others and what
others have pledged for them. Flags members whose pledges exceed their savings capacity."""

import frappe
from frappe import _
from frappe.utils import flt

from sacco_manager.utils import get_committed_pledges, get_multiplier


def execute(filters=None):
	f = frappe._dict(filters or {})
	mult = get_multiplier()
	members = frappe.get_all(
		"SACCO Member",
		filters={"status": "Active", **({"name": f.member} if f.member else {})},
		fields=["name", "member_no", "full_name", "branch"],
		order_by="member_no",
	)
	received = {
		r.applicant_member: flt(r.total)
		for r in frappe.db.sql(
			"""SELECT applicant_member, SUM(pledge_value) AS total FROM `tabSACCO Loan Guarantor`
			WHERE status != 'Declined' AND is_self = 0 GROUP BY applicant_member""",
			as_dict=True,
		)
	}
	data = []
	for m in members:
		accounts = frappe.get_all("SACCO Savings Account", filters={"member": m.name}, fields=["name", "balance"])
		savings = sum(flt(a.balance) for a in accounts)
		committed = sum(get_committed_pledges(a.name) for a in accounts)
		if not (committed or received.get(m.name)) and not f.member:
			continue
		capacity = savings * mult
		data.append({
			**m,
			"savings": savings,
			"capacity": capacity,
			"committed": committed,
			"available": capacity - committed,
			"guaranteed_by_others": received.get(m.name, 0),
			"over_committed": 1 if committed > capacity + 0.5 else 0,
		})
	return _columns(), data


def _columns():
	return [
		{"label": _("Member No"), "fieldname": "member_no", "width": 110},
		{"label": _("Member"), "fieldname": "name", "fieldtype": "Link", "options": "SACCO Member", "width": 100},
		{"label": _("Name"), "fieldname": "full_name", "width": 180},
		{"label": _("Branch"), "fieldname": "branch", "width": 100},
		{"label": _("Savings"), "fieldname": "savings", "fieldtype": "Currency", "width": 120},
		{"label": _("Pledge Capacity"), "fieldname": "capacity", "fieldtype": "Currency", "width": 130},
		{"label": _("Committed Pledges"), "fieldname": "committed", "fieldtype": "Currency", "width": 140},
		{"label": _("Available"), "fieldname": "available", "fieldtype": "Currency", "width": 120},
		{"label": _("Guaranteed by Others"), "fieldname": "guaranteed_by_others", "fieldtype": "Currency", "width": 150},
		{"label": _("Over-committed"), "fieldname": "over_committed", "fieldtype": "Check", "width": 110},
	]
