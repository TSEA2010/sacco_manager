# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Guarantor management.

Members (portal):
* my_guarantees        requests waiting for me, and guarantees I have given (with what they lock)
* respond              accept (optionally for a smaller amount) or decline a request
* request_guarantee    applicant asks another member to guarantee an open application
                       (also how a declined guarantor is replaced)
* withdraw_request     applicant withdraws a request that is still pending

Office (desk): resend a request by email and SMS, or record a response given on a signed form.

Requests reach the guarantor by SMS and email with a personal link (/guarantor-confirm) and appear
in their portal. A guarantor's savings capacity is never shown to the applicant."""

import frappe
from frappe import _
from frappe.utils import cint, cstr, flt, get_url, now_datetime

from sacco_manager.utils import get_committed_pledges, get_multiplier, get_pledge_capacity

OPEN_APP = ("Open",)


def _me():
	from sacco_manager.api.sacco_portal import get_member_for_user

	return get_member_for_user()


def _kes(v):
	return f"KES {flt(v):,.0f}"


def coverage(application):
	"""Pledges that still count (confirmed or pending) and confirmed only, vs the loan amount."""
	rows = frappe.get_all("SACCO Loan Guarantor", filters={"loan_application": application},
		fields=["status", "pledge_value"])
	amount = flt(frappe.db.get_value("Loan Application", application, "loan_amount"))
	confirmed = sum(flt(r.pledge_value) for r in rows if r.status == "Confirmed")
	pending = sum(flt(r.pledge_value) for r in rows if r.status == "Pending")
	return {"loan_amount": amount, "confirmed": confirmed, "pending": pending, "short": max(0, amount - confirmed - pending)}


# ---------------------------------------------------------------------------
# member portal
# ---------------------------------------------------------------------------
@frappe.whitelist()
def my_guarantees():
	me = _me()
	rows = frappe.get_all("SACCO Loan Guarantor", filters={"guarantor_member": me.name, "is_self": 0},
		fields=["name", "loan_application", "applicant_member", "status", "pledge_value", "savings_account", "responded_on", "creation"],
		order_by="creation desc")
	requests, given = [], []
	for r in rows:
		app = frappe.db.get_value("Loan Application", r.loan_application, ["loan_product", "loan_amount", "status", "repayment_periods"],
			as_dict=True) or frappe._dict()
		item = {"name": r.name, "application": r.loan_application, "status": r.status, "pledge_value": flt(r.pledge_value),
			"applicant": frappe.db.get_value("SACCO Member", r.applicant_member, "full_name") or "",
			"loan_product": app.loan_product, "loan_amount": flt(app.loan_amount), "periods": app.repayment_periods,
			"application_status": app.status, "requested_on": str(r.creation)[:10]}
		if r.status == "Pending" and app.status in OPEN_APP:
			item["can_cover"] = flt(r.pledge_value) <= get_pledge_capacity(r.savings_account, exclude_application=r.loan_application) + 0.5
			item["max_pledge"] = max(0, round(get_pledge_capacity(r.savings_account, exclude_application=r.loan_application), 0))
			requests.append(item)
		elif r.status == "Confirmed":
			loans = frappe.get_all("Loan", filters={"loan_application": r.loan_application, "docstatus": 1},
				fields=["name", "status", "disbursed_amount", "total_principal_paid"])
			active = app.status in ("Open", "Approved") and (not loans or any(
				x.status in ("Sanctioned", "Disbursed", "Partially Disbursed", "Active") for x in loans))
			item["loan_balance"] = sum(max(0, flt(x.disbursed_amount) - flt(x.total_principal_paid)) for x in loans)
			item["active"] = active
			given.append(item)
	accounts = frappe.get_all("SACCO Savings Account", filters={"member": me.name}, pluck="name")
	pledged = sum(get_committed_pledges(a) for a in accounts)
	return {"requests": requests, "given": given, "total_pledged": pledged, "locked_savings": pledged / (get_multiplier() or 1)}


@frappe.whitelist(methods=["POST"])
def respond(row, action, pledge_value=None):
	me = _me()
	g = frappe.get_doc("SACCO Loan Guarantor", row)
	if g.guarantor_member != me.name or cint(g.is_self):
		frappe.throw(_("This request is not addressed to you."), frappe.PermissionError)
	return _record(g, action, pledge_value, _("via member portal"))


def _record(g, action, pledge_value, how):
	if g.status != "Pending":
		return {"status": g.status, "message": _("This request was already answered.")}
	if frappe.db.get_value("Loan Application", g.loan_application, "status") not in OPEN_APP:
		frappe.throw(_("Application {0} is no longer open.").format(g.loan_application))
	action = cstr(action).lower()
	if action not in ("accept", "decline"):
		frappe.throw(_("Choose accept or decline."))
	note = f"{'Accepted' if action == 'accept' else 'Declined'} {how}"
	if action == "accept":
		amount = flt(pledge_value) if pledge_value not in (None, "") else flt(g.pledge_value)
		if amount <= 0 or amount > flt(g.pledge_value) + 0.005:
			frappe.throw(_("You can guarantee up to {0}.").format(_kes(g.pledge_value)))
		if amount > get_pledge_capacity(g.savings_account, exclude_application=g.loan_application) + 0.5:
			frappe.throw(_("Your savings can back at most {0} right now.").format(
				_kes(get_pledge_capacity(g.savings_account, exclude_application=g.loan_application))))
		if amount < flt(g.pledge_value) - 0.005:
			note += f" for {_kes(amount)} of {_kes(g.pledge_value)} requested"
			_update_pledge(g, amount)
		g.db_set({"status": "Confirmed", "pledge_value": amount, "responded_on": now_datetime(), "remarks": note})
	else:
		g.db_set({"status": "Declined", "responded_on": now_datetime(), "remarks": note})
	_tell_applicant(g)
	return {"status": g.status, "message": _("Thank you. Your response has been recorded."), "coverage": coverage(g.loan_application)}


def _update_pledge(g, amount):
	"""Keep the Loan Application's proposed pledge in step with the guarantor's accepted amount."""
	app = frappe.get_doc("Loan Application", g.loan_application)
	for p in app.get("proposed_pledges") or []:
		if p.loan_security == g.loan_security:
			p.loan_security_price = amount
			p.amount = amount
	app.flags.ignore_permissions = True
	app.flags.ignore_validate_update_after_submit = True
	app.save()


def _tell_applicant(g):
	from sacco_manager.notifications import _member_phone, queue_sms

	name = frappe.db.get_value("SACCO Member", g.guarantor_member, "full_name") or g.guarantor_member
	cov = coverage(g.loan_application)
	tail = _(" Your application is fully covered.") if not cov["short"] and not cov["pending"] else (
		_(" You still need guarantees for {0}: add another guarantor in the portal.").format(_kes(cov["short"])) if cov["short"] else "")
	text = _("{0} has {1} your guarantee request on {2} ({3}).").format(name, g.status.lower(), g.loan_application, _kes(g.pledge_value)) + tail
	phone, _first = _member_phone(g.applicant_member)
	queue_sms(phone, text, reference=g.name)
	email = frappe.db.get_value("SACCO Member", g.applicant_member, "email_id")
	if email:
		frappe.sendmail(recipients=[email], subject=_("Guarantor {0} - {1}").format(g.status.lower(), g.loan_application),
			message=frappe.utils.escape_html(text), reference_doctype="Loan Application", reference_name=g.loan_application)


@frappe.whitelist(methods=["POST"])
def request_guarantee(application, guarantor_member, pledge_value):
	me = _me()
	app = frappe.get_doc("Loan Application", application)
	if app.applicant != me.customer:
		frappe.throw(_("This is not your application."), frappe.PermissionError)
	if app.status not in OPEN_APP:
		frappe.throw(_("Guarantors can only be added while the application is open."))
	if guarantor_member == me.name:
		frappe.throw(_("Your own savings are already pledged; choose another member."))
	gm = frappe.db.get_value("SACCO Member", guarantor_member, ["name", "full_name", "status"], as_dict=True)
	if not gm or gm.status != "Active":
		frappe.throw(_("Choose an active member."))
	if frappe.db.exists("SACCO Loan Guarantor", {"loan_application": application, "guarantor_member": guarantor_member,
			"status": ["!=", "Declined"]}):
		frappe.throw(_("{0} is already a guarantor on this application.").format(gm.full_name))
	amount = flt(pledge_value)
	if amount <= 0:
		frappe.throw(_("Enter the amount to ask them to guarantee."))
	acc = frappe.get_all("SACCO Savings Account", filters={"member": guarantor_member, "status": "Active"},
		order_by="balance desc", pluck="name", limit=1)
	if not acc or amount > get_pledge_capacity(acc[0], exclude_application=application) + 0.5:
		# capacity is private to the guarantor: do not say how much they could cover
		frappe.throw(_("{0} cannot guarantee {1}. Try a smaller amount or another member.").format(gm.full_name, _kes(amount)))
	from sacco_manager.api.sacco_portal import _ensure_loan_security

	security = _ensure_loan_security(acc[0])
	row = frappe.get_doc({"doctype": "SACCO Loan Guarantor", "loan_application": application, "applicant_member": me.name,
		"guarantor_member": guarantor_member, "guarantor_name": gm.full_name, "savings_account": acc[0], "pledge_value": amount,
		"multiplier": get_multiplier(), "is_self": 0, "loan_security": security, "status": "Pending",
		"remarks": _("Requested by the applicant in the portal")})
	row.flags.ignore_permissions = True
	row.insert()
	app.append("proposed_pledges", {"loan_security": security, "qty": 1, "loan_security_price": amount, "amount": amount})
	app.flags.ignore_permissions = True
	app.save()
	send_request(row, sms=False)  # the SMS goes from the SACCO Loan Guarantor after_insert hook
	return {"row": row.name, "coverage": coverage(application),
		"message": _("Request sent to {0} by SMS{1}.").format(gm.full_name, _(" and email") if frappe.db.get_value(
			"SACCO Member", guarantor_member, "email_id") else "")}


@frappe.whitelist(methods=["POST"])
def withdraw_request(row):
	me = _me()
	g = frappe.get_doc("SACCO Loan Guarantor", row)
	if g.applicant_member != me.name or cint(g.is_self):
		frappe.throw(_("You can only withdraw your own requests."), frappe.PermissionError)
	if g.status != "Pending":
		frappe.throw(_("Only pending requests can be withdrawn."))
	g.db_set({"status": "Declined", "responded_on": now_datetime(), "remarks": _("Withdrawn by the applicant")})
	return {"coverage": coverage(g.loan_application)}


# ---------------------------------------------------------------------------
# messages + office actions
# ---------------------------------------------------------------------------
def request_link(g):
	from sacco_manager.api.sacco_portal import make_guarantor_token

	return get_url(f"/guarantor-confirm?app={g.loan_application}&member={g.guarantor_member}"
		f"&token={make_guarantor_token(g.loan_application, g.guarantor_member)}")


def send_request(g, sms=True):
	"""Email (if any) and SMS with the personal link."""
	from sacco_manager.api.sacco_portal import _send_guarantor_email
	from sacco_manager.notifications import _member_phone, queue_sms

	app = frappe.db.get_value("Loan Application", g.loan_application, ["loan_amount", "loan_product"], as_dict=True)
	applicant = frappe.db.get_value("SACCO Member", g.applicant_member, "full_name") or ""
	email = frappe.db.get_value("SACCO Member", g.guarantor_member, "email_id")
	if email:
		_send_guarantor_email(g.loan_application, {"member": g.guarantor_member, "member_name": g.guarantor_name,
			"pledge_value": g.pledge_value}, email, applicant, app.loan_amount, app.loan_product)
	if not sms:
		return
	phone, first = _member_phone(g.guarantor_member)
	queue_sms(phone, _("Dear {0}, {1} asks you to guarantee {2} of a loan ({3}). Accept or decline: {4}").format(
		first, applicant, _kes(g.pledge_value), g.loan_application, request_link(g)), reference=g.name)


@frappe.whitelist()
def resend_request(row):
	frappe.only_for(("System Manager", "SACCO Administrator", "SACCO Manager", "SACCO Credit Officer"))
	g = frappe.get_doc("SACCO Loan Guarantor", row)
	if g.status != "Pending" or cint(g.is_self):
		frappe.throw(_("Only pending requests to another member can be resent."))
	send_request(g)
	g.add_comment("Info", _("Request resent by {0}").format(frappe.session.user))
	return _("Request resent.")


@frappe.whitelist()
def record_response(row, action, reference, pledge_value=None):
	"""Office records a guarantor's answer given on a signed paper form."""
	frappe.only_for(("System Manager", "SACCO Administrator", "SACCO Manager", "SACCO Credit Officer"))
	if not cstr(reference).strip():
		frappe.throw(_("Enter the signed form reference."))
	g = frappe.get_doc("SACCO Loan Guarantor", row)
	return _record(g, action, pledge_value, _("on signed form {0}, recorded by {1}").format(cstr(reference).strip(), frappe.session.user))
