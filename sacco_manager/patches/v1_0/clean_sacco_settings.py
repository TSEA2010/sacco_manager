# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Remove the dangerous account mappings found in the Nov 2026 audit and set safe defaults.

- Employer remittance clearing pointed at Interest Income -> cleared (must be an asset).
- Company set from Global Defaults; maker-checker on; portal settings defaulted.
The accountant must then map the new accounts (or press "Create missing SACCO accounts")."""

import frappe


def _root(account):
	return frappe.db.get_value("Account", account, "root_type") if account else None


def execute():
	get = lambda f: frappe.db.get_single_value("SACCO Settings", f)  # noqa: E731
	updates = {}
	if not get("company"):
		updates["company"] = frappe.db.get_single_value("Global Defaults", "default_company")
	if _root(get("branch_receivable_account")) in ("Income", "Expense"):
		updates["branch_receivable_account"] = None
	if _root(get("interest_expense_account")) not in (None, "Expense"):
		updates["interest_expense_account"] = None
	in_singles = lambda f: frappe.db.sql(  # noqa: E731
		"SELECT 1 FROM `tabSingles` WHERE doctype='SACCO Settings' AND field=%s", f)
	if not in_singles("enforce_maker_checker"):
		updates["enforce_maker_checker"] = 1
	for field, value in (("portal_enabled", 1), ("guarantor_link_validity_days", 14),
			("dividend_wht_rate", 5), ("max_penalty_percent", 10)):
		if get(field) in (None, "", 0, "0"):
			updates[field] = value
	for field, value in updates.items():
		frappe.db.set_single_value("SACCO Settings", field, value)
	frappe.clear_cache(doctype="SACCO Settings")
