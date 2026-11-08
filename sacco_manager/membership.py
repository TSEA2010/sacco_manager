# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Endpoint behind /join: staff of the group companies apply without an account.

Kept away from the public internet, crawlers, bots and spam by:
* a joining code (SACCO Settings > Joining Code) shared only internally; without it the page shows
  just a code box, and the server refuses submissions without the right code;
* noindex meta, excluded from the sitemap and Disallowed in robots.txt;
* a signed form token: submissions faster than a person could fill the form, or older than a day,
  are refused; a hidden honeypot field;
* rate limits: 5 per IP per hour and a daily cap for the whole site;
* private uploads with size/type/content checks; duplicate-ID check.
Nothing is created in the books until staff approve."""

import base64
import hashlib
import hmac
import time

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import cint

FIELDS = ("salutation", "first_name", "middle_name", "last_name", "id_number", "kra_pin", "date_of_birth", "gender",
	"marital_status", "phone", "email", "address", "town", "employer", "employee_id", "job_title", "monthly_contribution",
	"kin_name", "kin_relation", "kin_phone", "kin_email", "wants_portal", "consent")
FILES = {"photo": ("jpg", "jpeg", "png"), "id_copy": ("jpg", "jpeg", "png", "pdf"), "kra_copy": ("jpg", "jpeg", "png", "pdf"),
	"signed_form": ("jpg", "jpeg", "png", "pdf")}
MAX_FILE = 3 * 1024 * 1024
MIN_FILL_SECONDS = 8
DAILY_CAP = 40


def _secret():
	return (frappe.local.conf.get("secret_key") or frappe.local.site or "sacco").encode()


def join_code():
	return (frappe.db.get_single_value("SACCO Settings", "join_code") or "").strip()


def code_ok(code):
	expected = join_code()
	return not expected or hmac.compare_digest(expected.upper(), (code or "").strip().upper())


def form_token():
	ts = str(int(time.time()))
	return f"{ts}.{hmac.new(_secret(), ts.encode(), hashlib.sha256).hexdigest()[:24]}"


def _token_ok(token):
	try:
		ts, sig = str(token or "").split(".", 1)
		age = time.time() - int(ts)
	except ValueError:
		return False
	good = hmac.compare_digest(sig, hmac.new(_secret(), ts.encode(), hashlib.sha256).hexdigest()[:24])
	return good and MIN_FILL_SECONDS <= age <= 86400


def employers():
	"""Employers shown on the form: enabled SACCO Employers, else all branches."""
	rows = frappe.get_all("SACCO Employer", filters={"enabled": 1}, fields=["branch", "employer_name"], order_by="employer_name")
	if rows:
		return [{"value": r.branch, "label": r.employer_name} for r in rows]
	return [{"value": b, "label": b} for b in frappe.get_all("Branch", pluck="name", order_by="name")]


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=5, seconds=3600)
def submit_application(data, files=None):
	return create_application(data, files)


def create_application(data, files=None):
	if not cint(frappe.db.get_single_value("SACCO Settings", "online_applications_enabled")):
		frappe.throw(_("Online applications are closed. Please apply at the SACCO office."))
	data = frappe.parse_json(data) if isinstance(data, str) else (data or {})
	if data.get("website"):  # honeypot: real people never see this field
		return {"name": None}
	if not code_ok(data.get("join_code")):
		frappe.throw(_("The joining code is not right. Ask HR or the SACCO office for the joining link."), frappe.PermissionError)
	if not frappe.flags.in_test and not _token_ok(data.get("form_token")):
		frappe.throw(_("Please reload the page and fill the form again."))
	key = f"sacco_join_count:{frappe.utils.today()}"
	count = cint(frappe.cache.get_value(key))
	if count >= DAILY_CAP:
		frappe.throw(_("Too many applications today. Please try again tomorrow or apply at the SACCO office."))
	frappe.cache.set_value(key, count + 1, expires_in_sec=90000)
	values = {k: data.get(k) for k in FIELDS if data.get(k) not in (None, "")}
	if values.get("employer") not in {e["value"] for e in employers()}:
		frappe.throw(_("Choose your employer from the list."))
	doc = frappe.get_doc({"doctype": "SACCO Membership Application", **values, "status": "Submitted", "source": "Online",
		"ip_address": frappe.local.request_ip if getattr(frappe.local, "request_ip", None) else None})
	doc.flags.ignore_permissions = True
	doc.insert()
	for f in (frappe.parse_json(files) if isinstance(files, str) else files) or []:
		_attach(doc, f)
	_notify_staff(doc)
	return {"name": doc.name}


def _attach(doc, f):
	field = f.get("field")
	name = (f.get("filename") or "").strip()[:120]
	ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
	if field not in FILES or ext not in FILES[field]:
		frappe.throw(_("{0}: use a {1} file.").format(name or field, "/".join(FILES.get(field, ())).upper()))
	try:
		content = base64.b64decode((f.get("content") or "").split(",")[-1], validate=True)
	except Exception:
		frappe.throw(_("{0} could not be read; please attach it again.").format(name))
	if not content or len(content) > MAX_FILE:
		frappe.throw(_("{0} is larger than 3 MB; please attach a smaller photo or scan.").format(name))
	if ext == "pdf" and not content.startswith(b"%PDF"):
		frappe.throw(_("{0} is not a valid PDF.").format(name))
	if ext in ("jpg", "jpeg", "png") and not (content.startswith(b"\xff\xd8") or content.startswith(b"\x89PNG")):
		frappe.throw(_("{0} is not a valid image.").format(name))
	fd = frappe.get_doc({"doctype": "File", "file_name": f"{doc.name}-{field}.{ext}", "content": content, "is_private": 1,
		"attached_to_doctype": doc.doctype, "attached_to_name": doc.name, "attached_to_field": field})
	fd.flags.ignore_permissions = True
	fd.insert()
	doc.db_set(field, fd.file_url)


def _notify_staff(doc):
	emails = [e.strip() for e in (frappe.db.get_single_value("SACCO Settings", "application_notify") or "").splitlines() if e.strip()]
	emails += frappe.db.sql_list("""SELECT DISTINCT u.name FROM `tabUser` u JOIN `tabHas Role` r ON r.parent = u.name
		WHERE r.role = 'SACCO Manager' AND u.enabled = 1 AND u.user_type = 'System User'""")
	if emails:
		frappe.sendmail(recipients=sorted(set(emails)), subject=_("New SACCO membership application {0}").format(doc.name),
			message=_("{0} ({1}) applied online to join from {2}. Review it in SACCO Membership Application {3}.").format(
				frappe.utils.escape_html(doc.full_name), doc.employer, doc.employer, doc.name),
			reference_doctype=doc.doctype, reference_name=doc.name)
	from sacco_manager.notifications import queue_sms

	queue_sms(doc.phone, _("Dear {0}, we received your Trident SACCO membership application {1}. We will contact you once it is reviewed.")
		.format((doc.first_name or "").title(), doc.name), reference=doc.name)
