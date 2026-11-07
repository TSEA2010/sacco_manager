# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Idempotent install/migrate setup: roles, role profiles, permissions on ERPNext/Lending
doctypes, custom fields the app depends on, and the SACCO Member party type.

Runs after install and after every `bench migrate`, so a fresh site gets everything the
app needs without hand configuration (the old app relied on fields and scripts that only
existed in the live database)."""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.permissions import add_permission, update_permission_property

ADMIN, MGR, ACC, CREDIT, TELLER, AUDIT = (
	"SACCO Administrator",
	"SACCO Manager",
	"SACCO Accountant",
	"SACCO Credit Officer",
	"SACCO Teller",
	"SACCO Auditor",
)
STAFF_ROLES = (ADMIN, MGR, ACC, CREDIT, TELLER, AUDIT)
MEMBER_ROLE = "Sacco Member"
SYSTEM_MANAGER = "System Manager"

ROLE_DESCRIPTIONS = {
	ADMIN: "Configures SACCO Settings, M-Pesa, products and users. Can do everything a Manager can.",
	MGR: "Approves: withdrawals, loans, interest/dividend runs, top-ups, remittances; can cancel.",
	ACC: "Posts savings, remittances, share entries, loan repayments; allocates M-Pesa; prepares runs.",
	CREDIT: "Onboards members, captures loan applications, guarantors and top-up requests.",
	TELLER: "Front office: captures deposits, withdrawal requests and repayments as drafts.",
	AUDIT: "Read-only access to all SACCO, lending and accounting records and reports.",
}

# Role profiles = what you assign to a user. Each bundles the SACCO role with the ERPNext
# roles needed for desk navigation; money-moving rights come only from the SACCO role.
ROLE_PROFILES = {
	"SACCO Administrator": [ADMIN, MGR, ACC, CREDIT, TELLER],
	"SACCO Manager": [MGR],
	"SACCO Accountant": [ACC],
	"SACCO Credit Officer": [CREDIT],
	"SACCO Teller": [TELLER],
	"SACCO Auditor": [AUDIT],
}

# rights letters: r read, w write, c create, s submit, x cancel, a amend, d delete
EXTERNAL_PERMS = {
	# Lending
	"Loan Application": {ADMIN: "rwcsxa", MGR: "rwcsxa", CREDIT: "rwc", ACC: "r", AUDIT: "r"},
	# Lending updates days-past-due on the Loan when a repayment is submitted, so the
	# submitter needs write (not create/submit) on Loan.
	"Loan": {ADMIN: "rwcsxa", MGR: "rwcsxa", CREDIT: "rwc", ACC: "rw", TELLER: "r", AUDIT: "r"},
	"Loan Disbursement": {ADMIN: "rwcsxa", MGR: "rwcsxa", ACC: "rwc", CREDIT: "r", AUDIT: "r"},
	"Loan Repayment": {ADMIN: "rwcsxad", MGR: "rwcsxad", ACC: "rwcsd", TELLER: "rwc", CREDIT: "r", AUDIT: "r"},
	"Loan Security": {ADMIN: "rwc", MGR: "rwc", CREDIT: "rwc", ACC: "r", AUDIT: "r"},
	"Loan Security Type": {ADMIN: "rwc", MGR: "r", CREDIT: "r", ACC: "r", AUDIT: "r"},
	"Loan Product": {ADMIN: "rwc", MGR: "r", CREDIT: "r", ACC: "r", TELLER: "r", AUDIT: "r"},
	# Lending creates these itself while a disbursement / repayment is submitted, with the
	# submitting user's permissions; managers and accountants therefore need create + submit.
	"Loan Demand": {ADMIN: "rwcsxd", MGR: "rwcsxd", ACC: "rwcsxd", CREDIT: "r", AUDIT: "r"},
	"Loan Interest Accrual": {ADMIN: "rwcsxd", MGR: "rwcsxd", ACC: "rwcsxd", AUDIT: "r"},
	"Process Loan Interest Accrual": {ADMIN: "rwcsxd", MGR: "rwcsxd", ACC: "rwcsxd", AUDIT: "r"},
	"Process Loan Demand": {ADMIN: "rwcsxd", MGR: "rwcsxd", ACC: "rwcsxd", AUDIT: "r"},
	"Loan Repayment Repost": {ADMIN: "rwcsxd", MGR: "rwcsxd", ACC: "rwcsxd", AUDIT: "r"},
	"Loan Security Assignment": {ADMIN: "rwcs", MGR: "rwcs", ACC: "rwcs", CREDIT: "rwc", AUDIT: "r"},
	"Loan Security Deposit": {ADMIN: "rwcsxd", MGR: "rwcsxd", ACC: "rwcsxd", AUDIT: "r"},
	"Loan Repayment Schedule": {ADMIN: "rwcsxd", MGR: "rwcsxd", ACC: "rwcsxd", CREDIT: "r", TELLER: "r", AUDIT: "r"},
	"Loan Write Off": {ADMIN: "rwcsxa", MGR: "rwcsxa", ACC: "r", AUDIT: "r"},
	"Loan Restructure": {ADMIN: "rwcsxa", MGR: "rwcsxa", CREDIT: "rwc", ACC: "r", AUDIT: "r"},
	# ERPNext accounting / masters
	"Journal Entry": {ADMIN: "rwcsxa", MGR: "rwcsxa", ACC: "rwcs", AUDIT: "r"},
	"Payment Entry": {ADMIN: "rwcsxa", MGR: "rwcsxa", ACC: "rwcs", AUDIT: "r"},
	"GL Entry": {ADMIN: "r", MGR: "r", ACC: "r", AUDIT: "r"},
	"Payment Ledger Entry": {ADMIN: "r", MGR: "r", ACC: "r", AUDIT: "r"},
	"Account": {ADMIN: "rwc", MGR: "r", ACC: "r", AUDIT: "r"},
	"Bank Account": {ADMIN: "rwc", MGR: "r", ACC: "r", AUDIT: "r"},
	"Customer": {ADMIN: "rwc", MGR: "rw", CREDIT: "rw", ACC: "r", TELLER: "r", AUDIT: "r"},
	"Branch": {ADMIN: "rwc", MGR: "r", ACC: "r", CREDIT: "r", TELLER: "r", AUDIT: "r"},
	"Company": {r: "r" for r in STAFF_ROLES},
	"Cost Center": {r: "r" for r in STAFF_ROLES},
	"Fiscal Year": {r: "r" for r in STAFF_ROLES},
	"Mode of Payment": {r: "r" for r in STAFF_ROLES},
	"Party Type": {r: "r" for r in STAFF_ROLES},
}

REPORT_ROLES = {
	"General Ledger": (ADMIN, MGR, ACC, AUDIT),
	"Trial Balance": (ADMIN, MGR, ACC, AUDIT),
	"Balance Sheet": (ADMIN, MGR, ACC, AUDIT),
	"Profit and Loss Statement": (ADMIN, MGR, ACC, AUDIT),
	"Cash Flow": (ADMIN, MGR, ACC, AUDIT),
	"Accounts Receivable": (ADMIN, MGR, ACC, AUDIT),
}

CUSTOM_FIELDS = {
	"Loan Security": [
		{"fieldname": "is_sacco_security", "fieldtype": "Check", "label": "Is SACCO Savings Security",
			"insert_after": "loan_security_code"},
		{"fieldname": "sacco_member_saving", "fieldtype": "Link", "label": "SACCO Savings Account",
			"options": "SACCO Savings Account", "insert_after": "loan_security_name",
			"depends_on": "is_sacco_security", "read_only": 1},
	],
	"Customer": [
		{"fieldname": "national_id", "fieldtype": "Data", "label": "National ID", "insert_after": "image"},
		{"fieldname": "sacco_member", "fieldtype": "Link", "label": "SACCO Member", "options": "SACCO Member",
			"insert_after": "national_id", "read_only": 1},
	],
	"Loan Repayment": [
		{"fieldname": "sacco_mpesa_transaction", "fieldtype": "Link", "label": "M-Pesa Payment",
			"options": "SACCO M-Pesa Transaction", "insert_after": "reference_number", "read_only": 1},
	],
}


def after_install():
	after_migrate()


def after_migrate():
	ensure_party_type()
	ensure_roles()
	ensure_role_profiles()
	ensure_external_permissions()
	ensure_permlevel1()
	ensure_report_roles()
	create_custom_fields(CUSTOM_FIELDS, ignore_validate=True, update=True)
	link_customers_to_members()
	frappe.clear_cache()


def ensure_party_type():
	if frappe.db.exists("Party Type", "SACCO Member"):
		if frappe.db.get_value("Party Type", "SACCO Member", "account_type") != "Payable":
			frappe.db.set_value("Party Type", "SACCO Member", "account_type", "Payable")
		return
	frappe.get_doc({"doctype": "Party Type", "party_type": "SACCO Member", "account_type": "Payable"}).insert(
		ignore_permissions=True
	)


def ensure_roles():
	for role, desc in ROLE_DESCRIPTIONS.items():
		if not frappe.db.exists("Role", role):
			frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1, "description": desc}).insert(
				ignore_permissions=True
			)
	if not frappe.db.exists("Role", MEMBER_ROLE):
		frappe.get_doc({"doctype": "Role", "role_name": MEMBER_ROLE, "desk_access": 0}).insert(ignore_permissions=True)
	else:
		frappe.db.set_value("Role", MEMBER_ROLE, "desk_access", 0)


def ensure_role_profiles():
	for profile, roles in ROLE_PROFILES.items():
		exists = frappe.db.exists("Role Profile", profile)
		doc = frappe.get_doc("Role Profile", profile) if exists else frappe.new_doc("Role Profile")
		doc.role_profile = profile
		have = {r.role for r in doc.get("roles")}
		missing = [r for r in roles if r not in have]
		if exists and not missing:
			continue
		for r in missing:
			doc.append("roles", {"role": r})
		doc.save(ignore_permissions=True)


RIGHTS = {"r": "read", "w": "write", "c": "create", "s": "submit", "x": "cancel", "a": "amend", "d": "delete"}


# Lending reposts back-dated repayments by cancelling/deleting and recreating its own records
LENDING_INTERNAL_DEFAULT = {ADMIN: "rwcsxd", MGR: "rwcsxd", ACC: "rwcsxd", AUDIT: "r"}


def _lending_internal_doctypes():
	"""Every Lending doctype not listed explicitly: Lending creates many of these itself
	(classification, accrual, repost, NPA logs ...) under the submitting user's permissions."""
	try:
		modules = frappe.get_module_list("lending")
	except Exception:
		return []
	return frappe.get_all(
		"DocType",
		filters={"module": ["in", modules], "istable": 0, "issingle": 0, "name": ["not in", list(EXTERNAL_PERMS)]},
		pluck="name",
	)


def ensure_external_permissions():
	perms = {dt: dict(roles) for dt, roles in EXTERNAL_PERMS.items()}
	for dt in _lending_internal_doctypes():
		perms[dt] = dict(LENDING_INTERNAL_DEFAULT)
	for roles in perms.values():
		roles[SYSTEM_MANAGER] = "rwcsxad"  # System Manager: full rights everywhere the app touches
	for doctype, roles in perms.items():
		if not frappe.db.exists("DocType", doctype):
			continue
		meta = frappe.get_meta(doctype)
		for role, rights in roles.items():
			exists = frappe.db.exists("Custom DocPerm", {"parent": doctype, "role": role, "permlevel": 0})
			if not exists:
				add_permission(doctype, role, 0)
			for letter, ptype in RIGHTS.items():
				if ptype in ("submit", "cancel", "amend") and not meta.is_submittable:
					continue
				update_permission_property(doctype, role, 0, ptype, 1 if letter in rights else 0, validate=False)
			for ptype in ("report", "export", "print"):
				update_permission_property(doctype, role, 0, ptype, 1, validate=False)


# Lending protects approval status with permlevel 1; only approvers get it.
PERMLEVEL1 = {
	"Loan Application": (ADMIN, MGR, "System Manager"),
	"Loan Security Release": (ADMIN, MGR, "System Manager"),
}


def ensure_permlevel1():
	for doctype, roles in PERMLEVEL1.items():
		if not frappe.db.exists("DocType", doctype):
			continue
		for role in roles:
			if not frappe.db.exists("Custom DocPerm", {"parent": doctype, "role": role, "permlevel": 1}):
				add_permission(doctype, role, 1)
			update_permission_property(doctype, role, 1, "read", 1, validate=False)
			update_permission_property(doctype, role, 1, "write", 1, validate=False)
		for role in (AUDIT, ACC, CREDIT):
			if not frappe.db.exists("Custom DocPerm", {"parent": doctype, "role": role, "permlevel": 1}):
				add_permission(doctype, role, 1)
			update_permission_property(doctype, role, 1, "read", 1, validate=False)
			update_permission_property(doctype, role, 1, "write", 0, validate=False)


def ensure_report_roles():
	for report, roles in REPORT_ROLES.items():
		if not frappe.db.exists("Report", report):
			continue
		have = set(frappe.get_all("Has Role", filters={"parent": report, "parenttype": "Report"}, pluck="role"))
		for role in roles:
			if role not in have:
				frappe.get_doc({"doctype": "Has Role", "parent": report, "parenttype": "Report", "parentfield": "roles",
					"role": role}).db_insert()


def link_customers_to_members():
	if not frappe.get_meta("Customer").has_field("sacco_member"):
		return
	frappe.db.sql(
		"""UPDATE `tabCustomer` c JOIN `tabSACCO Member` m ON m.customer = c.name
		SET c.sacco_member = m.name WHERE IFNULL(c.sacco_member, '') != m.name"""
	)


# Back-compat for the old hook name
ensure_party_type_only = ensure_party_type
