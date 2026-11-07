# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Page controller for /sacco-portal. All data comes from sacco_manager.api.sacco_portal."""

import frappe
from frappe import _

no_cache = 1


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/sacco-portal"
		raise frappe.Redirect
	context.no_cache = 1
	context.show_sidebar = False
	context.title = _("Member Portal")
	return context
