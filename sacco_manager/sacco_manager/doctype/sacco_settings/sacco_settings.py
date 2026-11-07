# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""SACCO Settings with account-type guards, so the misconfigurations found in the
Nov 2026 audit (savings interest to an asset, remittances to income, share capital
to a deposits account) cannot be saved again."""

import frappe
from frappe import _
from frappe.model.document import Document

from sacco_manager.accounting import assert_account

GUARDS = [
	# field, label, root types, account types
	("cash_account", "Default Cash / Bank Account", ("Asset",), ("Bank", "Cash")),
	("savings_liability_account", "Members' Savings", ("Liability",), None),
	("share_capital_account", "Share Capital", ("Equity", "Liability"), None),
	("income_account", "Loan Interest Income", ("Income",), None),
	("interest_expense_account", "Interest on Deposits", ("Expense",), None),
	("penalty_income_account", "Penalty Income", ("Income",), None),
	("registration_fee_income_account", "Registration Fee Income", ("Income",), None),
	("branch_receivable_account", "Employer Remittance Clearing", ("Asset", "Liability"), None),
	("loan_topup_clearing_account", "Loan Top-Up Clearing", ("Asset", "Liability"), None),
	("withholding_tax_account", "Withholding Tax Payable", ("Liability",), None),
	("dividend_appropriation_account", "Dividend Appropriation", ("Equity",), None),
	("dividend_payable_account", "Dividends Payable", ("Liability",), None),
]

# name, parent root type, account_type, is liability-like etc.
DEFAULT_ACCOUNTS = {
	"interest_expense_account": ("Interest on Members' Deposits", "Expense", None),
	"withholding_tax_account": ("Withholding Tax Payable", "Liability", "Tax"),
	"branch_receivable_account": ("Employer Remittance Clearing", "Asset", None),
	"loan_topup_clearing_account": ("Loan Top-Up Clearing", "Asset", None),
	"share_capital_account": ("Members' Share Capital", "Equity", None),
	"dividend_appropriation_account": ("Dividend Appropriation", "Equity", None),
	"dividend_payable_account": ("Dividends Payable", "Liability", "Payable"),
}


class SACCOSettings(Document):
	def validate(self):
		for field, label, roots, types in GUARDS:
			assert_account(self.get(field), _(label), roots, types)
		if self.company:
			for field, *_rest in GUARDS:
				acc = self.get(field)
				if acc and frappe.get_cached_value("Account", acc, "company") != self.company:
					frappe.throw(_("{0} belongs to another company.").format(acc))
		if (self.savings_multiplier or 0) <= 0:
			self.savings_multiplier = 3
		if self.naming_code:
			self.naming_code = self.naming_code.strip().upper()

	def on_update(self):
		frappe.cache().delete_value("sacco_member_ref_index")


@frappe.whitelist()
def create_missing_accounts():
	"""Create the SACCO ledger accounts that are not mapped yet, under the right root groups."""
	frappe.only_for(("System Manager", "SACCO Administrator"))
	s = frappe.get_doc("SACCO Settings")
	if not s.company:
		frappe.throw(_("Set the Company first."))
	abbr = frappe.get_cached_value("Company", s.company, "abbr")
	created = []
	for field, (name, root, acc_type) in DEFAULT_ACCOUNTS.items():
		if s.get(field):
			continue
		full = f"{name} - {abbr}"
		if not frappe.db.exists("Account", full):
			parent = frappe.db.get_value(
				"Account", {"company": s.company, "root_type": root, "is_group": 1, "parent_account": ["is", "not set"]}, "name"
			)
			acc = frappe.get_doc({
				"doctype": "Account",
				"account_name": name,
				"company": s.company,
				"parent_account": parent,
				"is_group": 0,
				"root_type": root,
				"account_type": acc_type,
			})
			acc.insert(ignore_permissions=True)
			created.append(acc.name)
		s.set(field, full)
	s.save()
	return created
