# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Page controller for /sacco-login. Passwordless member login: National ID + SMS one-time code.

The form posts to sacco_manager.auth.otp.request_login_otp / verify_login_otp. On success the session
is opened and the browser is sent to redirect_to (defaults to /sacco-portal)."""

import frappe

no_cache = 1


def _safe_redirect(value):
	"""Only allow a local path, never an off-site URL."""
	value = (value or "").strip()
	if value.startswith("/") and not value.startswith("//"):
		return value
	return "/sacco-portal"


def get_context(context):
	context.no_cache = 1
	context.show_sidebar = False
	redirect_to = _safe_redirect(frappe.form_dict.get("redirect-to"))
	context.redirect_to = redirect_to

	# Already signed in as a portal member? Skip the form.
	if frappe.session.user != "Guest":
		is_member = frappe.db.get_value(
			"SACCO Member",
			{"user_id": frappe.session.user, "portal_user_created": 1},
			"name",
		)
		if is_member:
			frappe.local.flags.redirect_location = redirect_to
			raise frappe.Redirect

	context.title = "Member Login"
	return context
