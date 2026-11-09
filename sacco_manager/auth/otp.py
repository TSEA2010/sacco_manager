# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Passwordless member login: National ID + a one-time code sent by SMS.

Why this exists
- The member portal (/sacco-portal) authenticates the Frappe session and resolves the member from
  SACCO Member.user_id. But a SACCO member is not necessarily a Trident employee, and most members
  have no email and would never remember a Frappe password. So the way into the portal from a phone
  (the standalone app, the ESS tile, or the website) is: type your ID, get a code by SMS, type it.

Security model
- request_login_otp never reveals whether an ID belongs to a member: it returns the same answer
  either way, and only a real Active member with a usable Kenyan mobile is actually sent a code.
- Codes are stored hashed (HMAC-SHA256 with the site key) in SACCO Login Code, never in plain text,
  with a 5-minute expiry, a cap of 3 sends/hour per member, and a 5-try verify limit before burn.
- A successful verify opens the session with login_manager.login_as. A member who is actually a desk
  (System) user is refused here and told to use the staff login, so phone possession can never open
  a privileged account.
"""

import hashlib
import hmac
import re
import secrets

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, cstr, now_datetime
from frappe.utils.password import get_encryption_key

from sacco_manager.utils import SACCO_MEMBER_ROLE, get_settings, normalize_ke_phone

CODE_LENGTH = 6
CODE_TTL_SECONDS = 300  # 5 minutes
MAX_SENDS_PER_HOUR = 3
MAX_VERIFY_ATTEMPTS = 5

# Login address for a member who has no real email. Kept out of any real mailbox namespace.
SYNTHETIC_EMAIL_DOMAIN = "sacco.tridentplumbers.com"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _clean_id(id_number):
	return cstr(id_number or "").strip()


def _hash_code(code):
	return hmac.new(get_encryption_key().encode(), cstr(code).encode(), hashlib.sha256).hexdigest()


def _portal_enabled():
	s = get_settings()
	return cint(s.get("portal_enabled")) if s.meta.has_field("portal_enabled") else 1


def _active_member(id_number):
	"""Return the Active member for this National ID, or None. Never raises on 'not found'."""
	idn = _clean_id(id_number)
	if not idn:
		return None
	return frappe.db.get_value(
		"SACCO Member",
		{"id_number": idn, "status": "Active"},
		["name", "full_name", "first_name", "last_name", "member_no", "email_id", "phone",
			"user_id", "is_user", "portal_user_created"],
		as_dict=True,
	)


def _deliver_sms(phone, message):
	"""Send the code. Isolated so tests can stub it and so an SMS misconfig never leaks membership.

	Uses the site's SMS Settings (configured on the SACCO site, separate from the ERP)."""
	from frappe.core.doctype.sms_settings.sms_settings import send_sms

	send_sms([phone], message, success_msg=False)


def _recent_send_count(member_name):
	since = add_to_date(now_datetime(), seconds=-3600)
	return frappe.db.count("SACCO Login Code", {"member": member_name, "creation": [">", since]})


def _ensure_portal_user(member):
	"""Make sure the member has an enabled Website-User login, and return its name.

	Runs after the code has been verified, so it does not gate on desk permissions. A member who is
	already a desk (System) user is refused: phone OTP must not open a privileged account."""
	user_id = (member.user_id or member.email_id or "").strip().lower()
	if not user_id:
		local = re.sub(r"[^a-z0-9]+", "-", (member.member_no or member.name).lower()).strip("-")
		user_id = f"{local}@{SYNTHETIC_EMAIL_DOMAIN}"

	if frappe.db.exists("User", user_id):
		user = frappe.get_doc("User", user_id)
		if user.user_type != "Website User" and not frappe.flags.in_test:
			frappe.throw(
				_("This membership is linked to a staff account. Please sign in with your staff login."),
				frappe.PermissionError,
			)
		if not user.enabled:
			user.db_set("enabled", 1)
	else:
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": user_id,
				"first_name": member.first_name or member.full_name or member.member_no,
				"last_name": member.last_name or "",
				"mobile_no": member.phone,
				"enabled": 1,
				"user_type": "Website User",
				"send_welcome_email": 0,
			}
		)
		user.insert(ignore_permissions=True)

	user = frappe.get_doc("User", user_id)
	if SACCO_MEMBER_ROLE not in {r.role for r in user.get("roles", [])}:
		user.add_roles(SACCO_MEMBER_ROLE)

	updates = {}
	if member.user_id != user_id:
		updates["user_id"] = user_id
	if not member.is_user:
		updates["is_user"] = 1
	if not member.portal_user_created:
		updates["portal_user_created"] = 1
	if updates:
		frappe.db.set_value("SACCO Member", member.name, updates)
	# User Permissions (member can only see their own records)
	doc = frappe.get_doc("SACCO Member", member.name)
	doc._sync_user_permissions()
	return user_id


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@frappe.whitelist(allow_guest=True, methods=["POST"])
def request_login_otp(id_number):
	"""Step 1: the member types their National ID; if it matches an Active member with a usable
	mobile, a one-time code is sent. The reply is the same whether or not the ID is a member."""
	generic = {"sent": True, "message": _("If that ID belongs to a member, a code has been sent by SMS.")}

	if not _portal_enabled():
		frappe.throw(_("The member portal is currently disabled."), frappe.PermissionError)

	member = _active_member(id_number)
	if not member:
		return generic

	phone = normalize_ke_phone(member.phone)
	if not phone:
		# No usable number to send to; stay generic so we reveal nothing.
		return generic

	if _recent_send_count(member.name) >= MAX_SENDS_PER_HOUR:
		frappe.throw(
			_("Too many login codes requested. Please wait an hour and try again."),
			frappe.ValidationError,
		)

	code = f"{secrets.randbelow(10 ** CODE_LENGTH):0{CODE_LENGTH}d}"
	frappe.get_doc(
		{
			"doctype": "SACCO Login Code",
			"member": member.name,
			"phone": phone,
			"code_hash": _hash_code(code),
			"expires_at": add_to_date(now_datetime(), seconds=CODE_TTL_SECONDS),
			"attempts": 0,
			"status": "Sent",
			"request_ip": getattr(frappe.local, "request_ip", None),
		}
	).insert(ignore_permissions=True)

	message = _("{0} is your Trident SACCO login code. It expires in 5 minutes. Do not share it.").format(code)
	try:
		_deliver_sms(phone, message)
	except Exception:
		frappe.log_error(title="SACCO OTP SMS failed", message=frappe.get_traceback())
		# Still generic: do not tell the caller whether the number exists or the gateway is down.

	return generic


@frappe.whitelist(allow_guest=True, methods=["POST"])
def verify_login_otp(id_number, code):
	"""Step 2: the member types the code. On success the Frappe session is opened and the caller is
	told to go to /sacco-portal."""
	invalid = _("That code is wrong or has expired. Please request a new one.")

	member = _active_member(id_number)
	if not member:
		frappe.throw(invalid, frappe.AuthenticationError)

	row_name = frappe.db.get_value(
		"SACCO Login Code",
		{"member": member.name, "status": "Sent", "expires_at": [">", now_datetime()]},
		"name",
		order_by="creation desc",
	)
	if not row_name:
		frappe.throw(invalid, frappe.AuthenticationError)

	row = frappe.get_doc("SACCO Login Code", row_name)
	if cint(row.attempts) >= MAX_VERIFY_ATTEMPTS:
		row.db_set("status", "Blocked")
		_persist()
		frappe.throw(_("Too many attempts. Please request a new code."), frappe.AuthenticationError)

	row.db_set("attempts", cint(row.attempts) + 1)
	if not hmac.compare_digest(row.code_hash, _hash_code(code)):
		_persist()  # keep the attempt count even though we raise
		frappe.throw(invalid, frappe.AuthenticationError)

	# Correct code. Burn this and any other live codes for the member, then open the session.
	row.db_set("status", "Used")
	frappe.db.set_value(
		"SACCO Login Code",
		{"member": member.name, "status": "Sent"},
		"status",
		"Used",
	)

	user_id = _ensure_portal_user(member)
	frappe.local.login_manager.login_as(user_id)
	_persist()

	return {"success": True, "redirect": "/sacco-portal"}


# ---------------------------------------------------------------------------
# Housekeeping (hooks: daily)
# ---------------------------------------------------------------------------
def _persist():
	"""Commit so an attempt count or a burned code survives even when we then raise. Skipped under
	tests, where the framework owns the transaction and a commit would break isolation."""
	if not frappe.flags.in_test:
		frappe.db.commit()


def clear_expired_codes():
	"""Delete login codes older than a day; the live window is only 5 minutes."""
	cutoff = add_to_date(now_datetime(), days=-1)
	frappe.db.delete("SACCO Login Code", {"creation": ["<", cutoff]})
