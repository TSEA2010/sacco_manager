# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Membership application form (/join). Shown only with the joining code (/join?code=...)."""

import frappe
from frappe.utils import cint

no_cache = 1
sitemap = 0


def get_context(context):
	from sacco_manager.membership import code_ok, employers, form_token

	context.no_cache = 1
	context.show_sidebar = False
	context.enabled = cint(frappe.db.get_single_value("SACCO Settings", "online_applications_enabled"))
	code = frappe.form_dict.get("code") or ""
	context.code_given = bool(code)
	context.unlocked = code_ok(code)  # true when no code is set
	context.join_code = code if context.unlocked else ""
	context.employers = employers() if context.unlocked else []
	context.form_token = form_token()
	return context
