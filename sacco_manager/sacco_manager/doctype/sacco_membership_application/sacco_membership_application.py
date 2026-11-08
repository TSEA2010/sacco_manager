# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Membership application: filled online at /join (or by staff), reviewed, then approved into a
SACCO Member with a savings account, consent record, documents and optional portal login."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, getdate, now_datetime, validate_email_address

from sacco_manager.utils import is_valid_kra_pin, normalize_ke_phone

APPROVERS = ("System Manager", "SACCO Administrator", "SACCO Manager")
OPEN = ("Submitted", "Under Review")


class SACCOMembershipApplication(Document):
	def validate(self):
		for f in ("first_name", "middle_name", "last_name", "kin_name", "employee_id", "town", "job_title"):
			if self.get(f):
				self.set(f, " ".join(str(self.get(f)).split())[:140])
		self.full_name = " ".join(p for p in (self.first_name, self.middle_name, self.last_name) if p).strip()
		self.id_number = "".join(str(self.id_number or "").split()) or None
		self.kra_pin = (self.kra_pin or "").strip().upper() or None
		phone = normalize_ke_phone(self.phone)
		if not phone:
			frappe.throw(_("{0} is not a valid Kenyan mobile number.").format(self.phone))
		self.phone = phone
		if self.kin_phone:
			self.kin_phone = normalize_ke_phone(self.kin_phone) or self.kin_phone
		if self.email:
			self.email = self.email.strip().lower()
			if not validate_email_address(self.email):
				frappe.throw(_("{0} is not a valid email address.").format(self.email))
		if self.kra_pin and not is_valid_kra_pin(self.kra_pin):
			frappe.throw(_("KRA PIN {0} does not look right (e.g. A012345678Z).").format(self.kra_pin))
		if flt(self.monthly_contribution) <= 0:
			frappe.throw(_("Enter the monthly savings deduction."))
		if not cint(self.consent):
			frappe.throw(_("The data protection and payroll deduction consent is required."))
		if not self.consent_at:
			self.consent_at = now_datetime()
		if self.status in OPEN:
			self._check_duplicates()

	def _check_duplicates(self):
		member = frappe.db.get_value("SACCO Member", {"id_number": self.id_number}, ["name", "member_no"], as_dict=True)
		if member:
			frappe.throw(_("ID {0} already belongs to member {1}.").format(self.id_number, member.member_no or member.name))
		other = frappe.db.get_value("SACCO Membership Application", {"id_number": self.id_number, "status": ["in", OPEN],
			"name": ["!=", self.name or ""]}, "name")
		if other:
			frappe.throw(_("An application for ID {0} is already being processed ({1}).").format(self.id_number, other))

	def on_trash(self):
		if self.member:
			frappe.throw(_("This application created member {0}; it cannot be deleted.").format(self.member))


def _reviewer():
	frappe.only_for(APPROVERS + ("SACCO Credit Officer",))


@frappe.whitelist()
def start_review(name):
	_reviewer()
	doc = frappe.get_doc("SACCO Membership Application", name)
	if doc.status != "Submitted":
		frappe.throw(_("Only submitted applications can be taken into review."))
	doc.db_set({"status": "Under Review", "reviewed_by": frappe.session.user, "reviewed_on": now_datetime()})
	return doc.status


@frappe.whitelist()
def reject(name, reason):
	frappe.only_for(APPROVERS)
	if not (reason or "").strip():
		frappe.throw(_("Give the reason for rejecting the application."))
	doc = frappe.get_doc("SACCO Membership Application", name)
	if doc.status not in OPEN:
		frappe.throw(_("Application {0} is already {1}.").format(name, doc.status))
	doc.db_set({"status": "Rejected", "rejection_reason": reason.strip(), "reviewed_by": frappe.session.user, "reviewed_on": now_datetime()})
	_tell_applicant(doc, _("Dear {0}, your Trident SACCO membership application {1} was not approved: {2}. Please contact the SACCO office.").format(
		(doc.first_name or "").title(), doc.name, reason.strip()), _("Your SACCO membership application"))
	return "Rejected"


@frappe.whitelist()
def approve(name, create_portal_user=0):
	"""Create the member, savings account, documents and (optionally) portal login."""
	frappe.only_for(APPROVERS)
	doc = frappe.get_doc("SACCO Membership Application", name)
	if doc.status not in OPEN:
		frappe.throw(_("Application {0} is already {1}.").format(name, doc.status))
	doc.validate()  # re-check duplicates at the moment of approval
	member = frappe.get_doc({
		"doctype": "SACCO Member", "salutation": doc.salutation, "first_name": doc.first_name, "middle_name": doc.middle_name,
		"last_name": doc.last_name, "id_number": doc.id_number, "kra_pin": doc.kra_pin, "date_of_birth": doc.date_of_birth,
		"gender": doc.gender, "marital_status": doc.marital_status, "phone": doc.phone, "email_id": doc.email,
		"branch": doc.employer, "employee_id": doc.employee_id, "joining_date": frappe.utils.today(), "status": "Active",
		"member_type": "Ordinary", "contact_name": doc.kin_name, "relation": doc.kin_relation, "contact_phone": doc.kin_phone,
		"contact_email": doc.kin_email, "consent_data_processing": 1, "consent_date": getdate(doc.consent_at or now_datetime()),
		"consent_method": "Portal" if doc.source == "Online" else "Signed form", "membership_application": doc.name,
	})
	member.flags.ignore_permissions = True
	member.insert()
	account_type = frappe.db.get_value("SACCO Account Type", "Member Savings", "name") or frappe.db.get_value("SACCO Account Type", {}, "name")
	if account_type:
		frappe.get_doc({"doctype": "SACCO Savings Account", "member": member.name, "account_type": account_type,
			"monthly_contribution": flt(doc.monthly_contribution)}).insert(ignore_permissions=True)
	_copy_documents(doc, member)
	_address(doc, member)
	note = ""
	if cint(create_portal_user) and doc.email:
		from sacco_manager.sacco_manager.doctype.sacco_member.sacco_member import create_portal_user as make_user

		frappe.db.set_value("SACCO Member", member.name, {"is_user": 1, "user_id": doc.email})
		note = make_user(member.name)
	doc.db_set({"status": "Approved", "member": member.name, "reviewed_by": frappe.session.user, "reviewed_on": now_datetime()})
	member.reload()
	_tell_applicant(doc, _("Dear {0}, welcome to Trident SACCO. Your membership is approved: member number {1}. "
		"Your monthly deduction of KES {2:,.0f} starts with the next payroll.").format(
		(doc.first_name or "").title(), member.member_no, flt(doc.monthly_contribution)), _("Welcome to Trident SACCO"))
	return {"member": member.name, "member_no": member.member_no, "portal": note}


def _copy_documents(app, member):
	for f in frappe.get_all("File", filters={"attached_to_doctype": app.doctype, "attached_to_name": app.name},
			fields=["file_name", "file_url", "is_private", "attached_to_field"]):
		label = {"photo": "photo", "id_copy": "ID copy", "kra_copy": "KRA PIN certificate", "signed_form": "signed form"}.get(
			f.attached_to_field, "document")
		frappe.get_doc({"doctype": "File", "file_url": f.file_url, "file_name": f"{member.member_no} {label} - {f.file_name}",
			"is_private": 1, "attached_to_doctype": "SACCO Member", "attached_to_name": member.name}).insert(ignore_permissions=True)
		if f.attached_to_field == "photo" and member.customer:
			frappe.db.set_value("Customer", member.customer, "image", f.file_url)


def _address(app, member):
	if not (app.address and member.customer):
		return
	addr = frappe.get_doc({"doctype": "Address", "address_title": member.full_name, "address_type": "Personal",
		"address_line1": app.address[:140], "city": app.town or "Nairobi", "country": "Kenya",
		"phone": app.phone, "email_id": app.email, "links": [{"link_doctype": "Customer", "link_name": member.customer}]})
	addr.flags.ignore_permissions = True
	addr.flags.ignore_mandatory = True
	addr.insert()


def _tell_applicant(doc, message, subject):
	from sacco_manager.notifications import queue_sms

	queue_sms(doc.phone, message, reference=doc.name)
	if doc.email:
		frappe.sendmail(recipients=[doc.email], subject=subject, message=frappe.utils.escape_html(message),
			reference_doctype=doc.doctype, reference_name=doc.name)
