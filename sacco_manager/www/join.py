# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Public membership application form (/join)."""

import frappe
from frappe.utils import cint

no_cache = 1


def get_context(context):
	from sacco_manager.membership import employers

	context.no_cache = 1
	context.show_sidebar = False
	context.enabled = cint(frappe.db.get_single_value("SACCO Settings", "online_applications_enabled"))
	context.employers = employers()
	return context
