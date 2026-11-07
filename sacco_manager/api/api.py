# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import flt

from sacco_manager.utils import get_multiplier


@frappe.whitelist()
def get_sacco_savings_balance(savings_account):
	"""Used by the Loan Security form. Requires read access to the savings account."""
	frappe.has_permission("SACCO Savings Account", "read", savings_account, throw=True)
	return flt(frappe.db.get_value("SACCO Savings Account", savings_account, "balance"))


@frappe.whitelist()
def get_sacco_security_value(savings_account):
	frappe.has_permission("SACCO Savings Account", "read", savings_account, throw=True)
	return flt(frappe.db.get_value("SACCO Savings Account", savings_account, "balance")) * get_multiplier()
