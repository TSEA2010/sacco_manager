# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import validate_email_address

from sacco_manager.utils import SACCO_MEMBER_ROLE, is_valid_kra_pin, normalize_ke_phone


def get_naming_code():
	code = frappe.db.get_single_value("SACCO Settings", "naming_code")
	if not code:
		frappe.throw(
			_("Set the <b>Naming Code</b> in SACCO Settings before creating members."),
			title=_("Naming Code Not Configured"),
		)
	return code.strip().upper()


def _ensure_customer_group(branch=None):
	group_name = (branch or "").strip() or "Sacco Member"
	if not frappe.db.exists("Customer Group", group_name):
		frappe.get_doc(
			{
				"doctype": "Customer Group",
				"customer_group_name": group_name,
				"parent_customer_group": "All Customer Groups",
			}
		).insert(ignore_permissions=True)
	return group_name


class SACCOMember(Document):
	def before_insert(self):
		if not self.member_no:
			self.member_no = get_next_member_no()
		self.create_linked_customer()

	def validate(self):
		self.full_name = " ".join(p for p in (self.first_name, self.middle_name, self.last_name) if p).strip() or self.full_name
		self.id_number = (str(self.id_number or "")).strip() or None
		self.kra_pin = (self.kra_pin or "").strip().upper() or None

		for field in ("phone", "contact_phone"):
			raw = self.get(field)
			if raw:
				normalized = normalize_ke_phone(raw)
				if not normalized and field == "phone":
					frappe.throw(_("Mobile {0} is not a valid Kenyan mobile number.").format(raw))
				self.set(field, normalized or str(raw))

		if self.email_id:
			self.email_id = self.email_id.strip().lower()
			if not validate_email_address(self.email_id):
				frappe.throw(_("Invalid email address: {0}").format(self.email_id))

		if self.kra_pin and not is_valid_kra_pin(self.kra_pin):
			frappe.msgprint(_("KRA PIN {0} does not look valid (expected e.g. A012345678Z).").format(self.kra_pin),
				indicator="orange", alert=True)

		self._validate_unique("member_no", _("Member No"))
		if self.customer:
			self._validate_unique("customer", _("Customer (loan account)"))
		if self.id_number:
			self._validate_unique("id_number", _("National ID"))

		if self.is_user and not self.user_id:
			self.user_id = self.email_id
		if self.is_user and not self.user_id:
			frappe.throw(_("Set an email / User ID before enabling portal access."), title=_("User ID Required"))

	def _validate_unique(self, field, label):
		value = self.get(field)
		if not value:
			return
		clash = frappe.db.get_value("SACCO Member", {field: value, "name": ["!=", self.name]}, "name")
		if clash:
			frappe.throw(_("{0} {1} is already used by member {2}.").format(label, value, clash))

	def after_insert(self):
		self._sync_customer_fields()
		self._sync_customer_contact()

	def on_update(self):
		self._sync_customer_fields()
		self._sync_customer_contact()
		if self.is_user and self.user_id and self.portal_user_created:
			self._sync_user_permissions()
		elif not self.is_user:
			before = self.get_doc_before_save()
			if before and before.user_id and before.portal_user_created:
				self._revoke_portal_access(before.user_id)
				self.db_set("portal_user_created", 0)

	def on_trash(self):
		if frappe.db.exists("SACCO Savings Transaction", {"member": self.name, "docstatus": 1}):
			frappe.throw(_("Member {0} has posted savings transactions and cannot be deleted. Set status to Withdrawn instead.").format(self.name))
		if self.customer and frappe.db.exists("Loan", {"applicant": self.customer, "docstatus": 1}):
			frappe.throw(_("Member {0} has loans and cannot be deleted.").format(self.name))

	# ------------------------------------------------------------------
	def _sync_customer_fields(self):
		if not self.customer or not frappe.db.exists("Customer", self.customer):
			return
		customer = frappe.get_doc("Customer", self.customer)
		customer.customer_name = self.full_name or customer.customer_name
		customer.customer_group = _ensure_customer_group(self.branch)
		field_map = {
			"joining_date": "custom_joining_date",
			"phone": "mobile_no",
			"kra_pin": "tax_id",
			"date_of_birth": "custom_date_of_birth",
			"marital_status": "marital_status",
			"email_id": "email_id",
			"branch": "branch",
			"id_number": "national_id",
		}
		meta = frappe.get_meta("Customer")
		for member_field, customer_field in field_map.items():
			if meta.has_field(customer_field) and self.get(member_field) is not None:
				customer.set(customer_field, self.get(member_field))
		customer.flags.ignore_permissions = True
		customer.save()

	def _sync_customer_contact(self):
		if not self.customer or not frappe.db.exists("Customer", self.customer):
			return
		if not any([self.contact_name, self.contact_email, self.contact_phone]):
			return
		existing = frappe.db.get_value(
			"Dynamic Link",
			{"link_doctype": "Customer", "link_name": self.customer, "parenttype": "Contact"},
			"parent",
		)
		if existing and frappe.db.exists("Contact", existing):
			contact = frappe.get_doc("Contact", existing)
		else:
			contact = frappe.new_doc("Contact")
			contact.append("links", {"link_doctype": "Customer", "link_name": self.customer})
		parts = (self.contact_name or "").split(" ", 1)
		contact.first_name = parts[0] or "Next of kin"
		contact.last_name = parts[1] if len(parts) > 1 else ""
		if self.relation:
			contact.designation = self.relation
		if self.contact_phone and not any(p.phone == self.contact_phone for p in contact.get("phone_nos")):
			contact.phone_nos = []
			contact.append("phone_nos", {"phone": self.contact_phone, "is_primary_phone": 1, "is_primary_mobile_no": 1})
		if self.contact_email and not any(e.email_id == self.contact_email for e in contact.get("email_ids")):
			contact.email_ids = []
			contact.append("email_ids", {"email_id": self.contact_email, "is_primary": 1})
		contact.flags.ignore_permissions = True
		contact.save()

	def _sync_user_permissions(self):
		for allow, value in (("SACCO Member", self.name), ("Customer", self.customer)):
			if not value:
				continue
			filters = {"user": self.user_id, "allow": allow, "for_value": value}
			if not frappe.db.exists("User Permission", filters):
				frappe.get_doc({"doctype": "User Permission", **filters, "apply_to_all_doctypes": 1}).insert(
					ignore_permissions=True
				)

	def _revoke_portal_access(self, user_id):
		if frappe.db.exists("User", user_id):
			user = frappe.get_doc("User", user_id)
			user.remove_roles(SACCO_MEMBER_ROLE)
		for allow, value in (("SACCO Member", self.name), ("Customer", self.customer)):
			frappe.db.delete("User Permission", {"user": user_id, "allow": allow, "for_value": value})

	def create_linked_customer(self):
		"""Each member gets their own Customer (the Lending applicant). Never match by name:
		two members with the same name must not share a loan ledger."""
		if self.customer:
			return
		if self.id_number and frappe.get_meta("Customer").has_field("national_id"):
			by_id = frappe.get_all("Customer", filters={"national_id": self.id_number}, pluck="name", limit=2)
			linked = frappe.get_all("SACCO Member", filters={"customer": ["in", by_id or [""]]}, pluck="name")
			if len(by_id) == 1 and not linked:
				self.customer = by_id[0]
				self.customer_group = _ensure_customer_group(self.branch)
				return
		group = _ensure_customer_group(self.branch)
		name = " ".join(p for p in (self.first_name, self.middle_name, self.last_name) if p).strip() or self.full_name
		customer = frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": name,
				"customer_type": "Individual",
				"customer_group": group,
				"territory": frappe.db.get_single_value("Selling Settings", "territory") or "All Territories",
			}
		)
		customer.flags.ignore_permissions = True
		customer.insert()
		self.customer = customer.name
		self.customer_group = group


# ---------------------------------------------------------------------------
# Whitelisted methods (desk)
# ---------------------------------------------------------------------------
@frappe.whitelist()
def create_portal_user(member_name):
	frappe.has_permission("SACCO Member", "write", member_name, throw=True)
	member = frappe.get_doc("SACCO Member", member_name)
	if not member.is_user or not member.user_id:
		frappe.throw(_("Enable portal access and set a User ID first."))
	if not frappe.db.exists("User", member.user_id):
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": member.user_id,
				"first_name": member.first_name or member.full_name,
				"last_name": member.last_name or "",
				"mobile_no": member.phone,
				"enabled": 1,
				"user_type": "Website User",
				"send_welcome_email": 1,
			}
		)
		user.insert(ignore_permissions=True)
	user = frappe.get_doc("User", member.user_id)
	if user.user_type != "Website User" and not frappe.flags.in_test:
		frappe.throw(_("{0} is a desk (System) user. Portal members must use a separate login.").format(user.name))
	user.add_roles(SACCO_MEMBER_ROLE)
	frappe.db.set_value("SACCO Member", member_name, "portal_user_created", 1)
	member.reload()
	member._sync_user_permissions()
	return _("Portal user {0} created and linked.").format(member.user_id)


def _parse_member_seq(member_no, prefix):
	m = re.match(rf"^{re.escape(prefix)}(\d+)$", member_no or "")
	return int(m.group(1)) if m else 0


@frappe.whitelist()
def get_next_member_no():
	prefix = f"{get_naming_code()}-"
	existing = frappe.get_all("SACCO Member", filters={"member_no": ["like", f"{prefix}%"]}, pluck="member_no")
	nxt = max([_parse_member_seq(n, prefix) for n in existing] or [0]) + 1
	return f"{prefix}{nxt:04d}"


@frappe.whitelist()
def create_customer_for_member(member_name):
	frappe.has_permission("SACCO Member", "write", member_name, throw=True)
	member = frappe.get_doc("SACCO Member", member_name)
	if member.customer:
		return _("Member {0} already has a linked Customer.").format(member_name)
	member.create_linked_customer()
	member.save()
	return _("Customer {0} created and linked to {1}.").format(member.customer, member_name)


@frappe.whitelist()
def bulk_sync_members_to_customers():
	frappe.only_for(("System Manager", "SACCO Administrator"))
	errors, success = [], 0
	members = frappe.get_all("SACCO Member", filters={"customer": ["is", "set"]}, pluck="name")
	for name in members:
		try:
			doc = frappe.get_doc("SACCO Member", name)
			doc._sync_customer_fields()
			doc._sync_customer_contact()
			success += 1
		except Exception as e:
			errors.append({"member": name, "error": str(e)})
	return {"total": len(members), "success": success, "errors": errors}
