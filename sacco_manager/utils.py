# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Shared helpers: settings access, phone/ID normalisation, segregation of duties, pledges."""

import re

import frappe
from frappe import _
from frappe.utils import cint, flt

SACCO_MEMBER_ROLE = "Sacco Member"


def get_settings():
	return frappe.get_cached_doc("SACCO Settings")


def get_company():
	return (
		frappe.db.get_single_value("SACCO Settings", "company")
		or frappe.defaults.get_user_default("company")
		or frappe.db.get_single_value("Global Defaults", "default_company")
	)


def require_setting(fieldname, label=None):
	value = get_settings().get(fieldname)
	if not value:
		frappe.throw(
			_("Set <b>{0}</b> in SACCO Settings first.").format(label or fieldname.replace("_", " ").title()),
			title=_("SACCO Settings incomplete"),
		)
	return value


# ---------------------------------------------------------------------------
# Kenyan phone / ID helpers
# ---------------------------------------------------------------------------
def normalize_ke_phone(value):
	"""Return a Safaricom/Airtel style MSISDN 2547XXXXXXXX / 2541XXXXXXXX, or None if not a KE mobile."""
	digits = re.sub(r"\D", "", str(value or ""))
	if not digits:
		return None
	if digits.startswith("254") and len(digits) == 12:
		return digits
	if digits.startswith("0") and len(digits) == 10:
		return "254" + digits[1:]
	if len(digits) == 9 and digits[0] in "17":
		return "254" + digits
	return None


KRA_PIN_RE = re.compile(r"^[AP]\d{9}[A-Z]$")


def is_valid_kra_pin(pin):
	return bool(KRA_PIN_RE.match((pin or "").strip().upper()))


# ---------------------------------------------------------------------------
# Segregation of duties (maker-checker)
# ---------------------------------------------------------------------------
def enforce_maker_checker(doc, action=None):
	"""Block the user who created a money-moving document from also approving/submitting it."""
	if not cint(frappe.db.get_single_value("SACCO Settings", "enforce_maker_checker")):
		return
	user = frappe.session.user
	if user in ("Administrator",) or getattr(frappe.flags, "in_import", False) or frappe.flags.in_patch:
		return
	if doc.owner == user:
		frappe.throw(
			_(
				"Segregation of duties: you created {0} {1}, so a different user must {2} it."
			).format(_(doc.doctype), doc.name, action or _("approve")),
			frappe.PermissionError,
			title=_("Maker-checker"),
		)


# ---------------------------------------------------------------------------
# Guarantor pledges (lien on savings)
# ---------------------------------------------------------------------------
OPEN_APPLICATION_STATUSES = ("Open", "Approved")


def get_committed_pledges(savings_account, exclude_application=None):
	"""Total pledge value still committed against a savings account.

	A pledge stays committed while the guarantee is not Declined and the
	application is still open/approved and its loan (if any) is not closed.
	"""
	rows = frappe.db.sql(
		"""
		SELECT g.pledge_value, g.loan_application, la.status AS app_status, la.docstatus AS app_docstatus
		FROM `tabSACCO Loan Guarantor` g
		LEFT JOIN `tabLoan Application` la ON la.name = g.loan_application
		WHERE g.savings_account = %(acc)s
		  AND g.status != 'Declined'
		  AND IFNULL(g.loan_application, '') != %(exclude)s
		""",
		{"acc": savings_account, "exclude": exclude_application or ""},
		as_dict=True,
	)
	total = 0.0
	for r in rows:
		if r.app_docstatus == 2 or (r.app_status and r.app_status not in OPEN_APPLICATION_STATUSES):
			continue
		loan = frappe.db.get_value(
			"Loan", {"loan_application": r.loan_application, "docstatus": 1}, ["status"], as_dict=True
		)
		if loan and loan.status in ("Closed", "Settled", "Written Off", "Loan Closure Requested"):
			continue
		total += flt(r.pledge_value)
	return total


def get_multiplier():
	return flt(get_settings().get("savings_multiplier") or 3)


def get_pledge_capacity(savings_account, exclude_application=None):
	"""Remaining pledge value a savings account can still back (balance x multiplier - committed)."""
	balance = flt(frappe.db.get_value("SACCO Savings Account", savings_account, "balance"))
	return max(0.0, balance * get_multiplier() - get_committed_pledges(savings_account, exclude_application))


def get_withdrawable_balance(savings_account):
	"""Balance less the savings locked as security for pledges (pledge / multiplier)."""
	balance = flt(frappe.db.get_value("SACCO Savings Account", savings_account, "balance"))
	locked = get_committed_pledges(savings_account) / (get_multiplier() or 1)
	return max(0.0, balance - locked)
