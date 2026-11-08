# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Member login for /member-login: mobile number (any format) or email, with a password or a
one-time code sent by SMS or email.

Only portal members can use it (enabled Website Users with the Sacco Member role). Staff with desk
access keep the standard /login, so a phone code can never replace a staff password or 2FA.

Security: 6-digit code, stored only as a salted hash in the cache, valid 5 minutes, 5 tries;
rate limits per IP; identical responses whether or not the account exists."""

import hashlib
import hmac
import secrets

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import cint, validate_email_address

from sacco_manager.utils import SACCO_MEMBER_ROLE, normalize_ke_phone

OTP_TTL = 300
OTP_TRIES = 5
GENERIC = _("If an account matches, a code has been sent. It is valid for 5 minutes.")


def _setting(field):
	return cint(frappe.db.get_single_value("SACCO Settings", field))


def resolve_user(identifier):
	"""Email or mobile (07.., 7.., +2547.., 2547..) -> portal user name, or None."""
	ident = (identifier or "").strip()
	user = None
	if "@" in ident:
		if validate_email_address(ident.lower()):
			user = frappe.db.get_value("User", {"name": ident.lower()}) or frappe.db.get_value("User", {"email": ident.lower()})
	else:
		phone = normalize_ke_phone(ident)
		if phone:
			user = frappe.db.get_value("SACCO Member", {"phone": phone, "portal_user_created": 1, "status": "Active"}, "user_id")
			if not user:
				local = "0" + phone[3:]
				user = frappe.db.get_value("User", {"mobile_no": ["in", [phone, local, "+" + phone]], "user_type": "Website User"})
	if not user:
		return None
	u = frappe.db.get_value("User", user, ["enabled", "user_type"], as_dict=True)
	if not u or not u.enabled or u.user_type != "Website User":
		return None
	if SACCO_MEMBER_ROLE not in frappe.get_roles(user):
		return None
	return user


def _hash(user, code):
	key = (frappe.local.conf.get("secret_key") or frappe.local.site or "sacco").encode()
	return hmac.new(key, f"{user}:{code}".encode(), hashlib.sha256).hexdigest()


def _key(user):
	return f"sacco_member_otp:{user}"


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=10, seconds=600)
def login_with_password(identifier, password):
	if not _setting("member_login_phone"):
		frappe.throw(_("Use the standard login page."))
	user = resolve_user(identifier)
	if not user:
		frappe.throw(_("Mobile number / email or password is incorrect."), frappe.AuthenticationError)
	lm = frappe.local.login_manager
	lm.authenticate(user=user, pwd=password)  # raises on a wrong password and counts failed attempts
	lm.post_login()
	return {"redirect": "/sacco-portal"}


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=5, seconds=600)
def send_login_code(identifier, channel="sms"):
	if not _setting("member_login_otp"):
		frappe.throw(_("Login codes are switched off."))
	user = resolve_user(identifier)
	if user:
		code = f"{secrets.randbelow(10**6):06d}"
		frappe.cache.set_value(_key(user), {"h": _hash(user, code), "tries": 0}, expires_in_sec=OTP_TTL)
		text = _("Your Trident SACCO login code is {0}. It expires in 5 minutes. Never share it.").format(code)
		phone = normalize_ke_phone(frappe.db.get_value("SACCO Member", {"user_id": user}, "phone")
			or frappe.db.get_value("User", user, "mobile_no"))
		if channel == "sms" and phone and frappe.db.get_single_value("SMS Settings", "sms_gateway_url"):
			frappe.enqueue("sacco_manager.notifications._send", phone=phone, message=text, reference="login code",
				queue="short", enqueue_after_commit=True)
		else:
			frappe.sendmail(recipients=[frappe.db.get_value("User", user, "email")], subject=_("Your SACCO login code"),
				message=text, now=True, delayed=False)
	return {"message": GENERIC}


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=10, seconds=600)
def verify_login_code(identifier, code):
	user = resolve_user(identifier)
	bad = _("The code is wrong or has expired. Request a new one.")
	if not user:
		frappe.throw(bad, frappe.AuthenticationError)
	entry = frappe.cache.get_value(_key(user))
	if not entry:
		frappe.throw(bad, frappe.AuthenticationError)
	if not hmac.compare_digest(entry["h"], _hash(user, "".join((code or "").split()))):
		entry["tries"] = cint(entry.get("tries")) + 1
		if entry["tries"] >= OTP_TRIES:
			frappe.cache.delete_value(_key(user))
		else:
			frappe.cache.set_value(_key(user), entry, expires_in_sec=OTP_TTL)
		frappe.throw(bad, frappe.AuthenticationError)
	frappe.cache.delete_value(_key(user))
	frappe.local.login_manager.login_as(user)
	return {"redirect": "/sacco-portal"}


@frappe.whitelist(allow_guest=True)
def login_options():
	return {
		"password": _setting("member_login_phone"),
		"otp": _setting("member_login_otp"),
		"sms": bool(frappe.db.get_single_value("SMS Settings", "sms_gateway_url")),
	}
