# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Member login page (/member-login): mobile/email + password, or a one-time code."""

import frappe

no_cache = 1


def get_context(context):
	if frappe.session.user != "Guest":
		# staff already signed in go to the desk; only members go to the portal
		is_staff = frappe.db.get_value("User", frappe.session.user, "user_type") == "System User"
		frappe.local.flags.redirect_location = "/app" if is_staff else "/sacco-portal"
		raise frappe.Redirect
	from sacco_manager.member_login import login_options

	context.no_cache = 1
	context.show_sidebar = False
	context.opts = frappe._dict(login_options())
	return context
