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
MAX_SENDS_PER_HOUR = 3  # per submitted ID
MAX_VERIFY_ATTEMPTS = 5  # per code, before it is burned
MAX_VERIFY_PER_HOUR = 10  # per submitted ID
MAX_IP_REQUESTS_PER_HOUR = 60  # per IP, across request + verify (when an IP is known)
RATE_WINDOW = 3600

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


def _ip():
	return getattr(frappe.local, "request_ip", None)


def _throttle(bucket, ident, limit, window=RATE_WINDOW):
	"""Fixed-window counter in Redis. Returns True while still within the limit.

	Applied to raw inputs (IP, submitted ID) before any membership lookup, so throttling behaves
	identically for members and non-members and can't be used to tell them apart."""
	key = f"sacco_otp:{bucket}:{getattr(frappe.local, 'site', '')}:{cstr(ident).lower()}"
	cache = frappe.cache()
	count = cache.incrby(key, 1)
	if count == 1:
		cache.expire(key, window)
	return count <= limit


def _sacco_login_id(member):
	"""The member's dedicated SACCO login, in a namespace of its own (@sacco.*). Deliberately NOT
	the member's real email: sacco_manager may be installed ON the Trident/Astral ERP site, where
	the member could also be an employee. Reusing that employee's ESS user and adding the Sacco
	Member role to it would break their PIN sign-in (which needs exactly Employee + Employee Self
	Service). A separate login keeps the two identities from ever touching."""
	local = re.sub(r"[^a-z0-9]+", "-", (member.member_no or member.name).lower()).strip("-")
	return f"{local}@{SYNTHETIC_EMAIL_DOMAIN}"


def _linked_portal_login(member):
	"""The member's current portal login when it is already a dedicated SACCO login (a Website User
	that carries the Sacco Member role, linked with portal_user_created), else None.

	Such a login was made by the office (Create Portal User) or the joining flow, and /member-login
	signs in to it with a password or a code. Reusing it here adds no role to anyone and keeps those
	logins working; replacing it would leave the member's password login pointing at nothing."""
	if not (member.user_id and member.portal_user_created):
		return None
	u = frappe.db.get_value("User", member.user_id, ["enabled", "user_type"], as_dict=True)
	if not u or u.user_type != "Website User" or SACCO_MEMBER_ROLE not in frappe.get_roles(member.user_id):
		return None
	if not u.enabled:
		# The office switched this login off on purpose: a phone code must not switch it back on.
		frappe.throw(_("Your portal access is switched off. Please contact the SACCO office."), frappe.AuthenticationError)
	return member.user_id


def _ensure_portal_user(member):
	"""Make sure the member has an enabled, dedicated Website-User login (never an employee/ESS or
	desk account), and return its name. Runs after the code has been verified."""
	existing = _linked_portal_login(member)
	if existing:
		return existing

	user_id = _sacco_login_id(member)

	if frappe.db.exists("User", user_id):
		user = frappe.get_doc("User", user_id)
		if not user.enabled:
			frappe.throw(_("Your portal access is switched off. Please contact the SACCO office."), frappe.AuthenticationError)
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

	# Throttle raw inputs before any lookup, so a rate limit can't reveal who is a member. On limit
	# we return the same generic reply (and the counter still climbs), never a distinct error.
	idn = _clean_id(id_number)
	ip = _ip()
	if ip and not _throttle("req_ip", ip, MAX_IP_REQUESTS_PER_HOUR):
		return generic
	if not _throttle("req_id", idn, MAX_SENDS_PER_HOUR):
		return generic

	member = _active_member(idn)
	if not member:
		return generic

	phone = normalize_ke_phone(member.phone)
	if not phone:
		# No usable number to send to; stay generic so we reveal nothing.
		return generic

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

	idn = _clean_id(id_number)
	ip = _ip()
	# Same uniform throttles as request, so brute force is bounded per IP and per ID (members or not).
	if ip and not _throttle("ver_ip", ip, MAX_IP_REQUESTS_PER_HOUR):
		frappe.throw(invalid, frappe.AuthenticationError)
	if not _throttle("ver_id", idn, MAX_VERIFY_PER_HOUR):
		frappe.throw(invalid, frappe.AuthenticationError)

	member = _active_member(idn)
	if not member:
		frappe.throw(invalid, frappe.AuthenticationError)

	# Lock the live code row for this member so concurrent verifies serialise: the read -> attempt
	# check -> increment below is then atomic, and the 5-try limit cannot be raced past.
	row = frappe.db.get_value(
		"SACCO Login Code",
		{"member": member.name, "status": "Sent", "expires_at": [">", now_datetime()]},
		["name", "code_hash", "attempts"],
		as_dict=True,
		order_by="creation desc",
		for_update=True,
	)
	if not row:
		frappe.throw(invalid, frappe.AuthenticationError)

	if cint(row.attempts) >= MAX_VERIFY_ATTEMPTS:
		frappe.db.set_value("SACCO Login Code", row.name, "status", "Blocked")
		_persist()
		frappe.throw(_("Too many attempts. Please request a new code."), frappe.AuthenticationError)

	frappe.db.set_value("SACCO Login Code", row.name, "attempts", cint(row.attempts) + 1)
	if not hmac.compare_digest(row.code_hash, _hash_code(code)):
		_persist()  # keep the attempt count even though we raise
		frappe.throw(invalid, frappe.AuthenticationError)

	# Correct code. Burn this and any other live codes for the member, then open the session.
	frappe.db.set_value("SACCO Login Code", row.name, "status", "Used")
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
