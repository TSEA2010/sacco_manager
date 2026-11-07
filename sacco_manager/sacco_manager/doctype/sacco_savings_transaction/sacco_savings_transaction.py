# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""A single movement on a member's savings account, with its GL posting.

Deposit     Dr payment account (cash/bank/M-Pesa/check-off clearing)   Cr Members' Savings [member]
Withdrawal  Dr Members' Savings [member]                               Cr payment account
Interest    Dr Interest on Deposits (expense)                          Cr Members' Savings [member]
Dividend    Dr Dividends Payable [member]                              Cr Members' Savings [member]
Withholding Tax  Dr Members' Savings [member]                         Cr Withholding Tax Payable
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from sacco_manager.accounting import cancel_journal_entry, make_journal_entry
from sacco_manager.utils import get_settings, get_withdrawable_balance, require_setting

CREDIT_TYPES = ("Deposit", "Interest", "Dividend")


class SACCOSavingsTransaction(Document):
	def validate(self):
		if flt(self.amount) <= 0:
			frappe.throw(_("Amount must be greater than zero."))
		acc = frappe.db.get_value(
			"SACCO Savings Account", self.savings_account, ["member", "status", "balance"], as_dict=True
		)
		if not acc:
			frappe.throw(_("Savings Account {0} not found.").format(self.savings_account))
		if self.member and self.member != acc.member:
			frappe.throw(
				_("Savings Account {0} belongs to {1}, not {2}.").format(self.savings_account, acc.member, self.member)
			)
		self.member = acc.member
		if acc.status == "Closed":
			frappe.throw(_("Savings Account {0} is closed.").format(self.savings_account))
		if self.transaction_type in ("Deposit", "Withdrawal") and not self.payment_account:
			self.payment_account = require_setting("cash_account", "Default Cash / Bank Account")
		if self.docstatus == 0:
			self.status = "Draft"
			self.balance_before = flt(acc.balance)
		if self.transaction_type == "Withdrawal" and not self.flags.skip_withdrawable_check:
			available = get_withdrawable_balance(self.savings_account)
			if flt(self.amount) > available:
				frappe.throw(
					_("Insufficient withdrawable balance. Available: {0} (part of the balance is locked as loan security).").format(
						frappe.format_value(available, {"fieldtype": "Currency"})
					)
				)

	def on_submit(self):
		self._apply_balance(reverse=False)
		self.db_set("journal_entry", self._post_gl())
		self.db_set("status", "Posted")

	def on_cancel(self):
		self.ignore_linked_doctypes = ("Journal Entry", "GL Entry", "Payment Ledger Entry")
		self._apply_balance(reverse=True)
		cancel_journal_entry(self.journal_entry)
		self.db_set("status", "Cancelled")

	# ------------------------------------------------------------------
	def _apply_balance(self, reverse):
		current = flt(
			frappe.db.get_value("SACCO Savings Account", self.savings_account, "balance", for_update=True)
		)
		sign = 1 if self.transaction_type in CREDIT_TYPES else -1
		if reverse:
			sign = -sign
		new_bal = current + sign * flt(self.amount)
		if new_bal < -0.005:
			frappe.throw(
				_("This would make Savings Account {0} negative ({1}).").format(self.savings_account, new_bal)
			)
		frappe.db.set_value("SACCO Savings Account", self.savings_account, "balance", new_bal)
		if not reverse:
			self.db_set("balance_before", current)
			self.db_set("balance_after", new_bal)
		acc = frappe.get_doc("SACCO Savings Account", self.savings_account)
		acc.sync_loan_security()

	def _post_gl(self):
		s = get_settings()
		savings = require_setting("savings_liability_account", "Members' Savings (Liability)")
		amt = flt(self.amount)
		member = self.member
		t = self.transaction_type
		if t == "Deposit":
			lines = [
				{"account": self.payment_account, "debit": amt},
				{"account": savings, "credit": amt, "party": member},
			]
		elif t == "Withdrawal":
			lines = [
				{"account": savings, "debit": amt, "party": member},
				{"account": self.payment_account, "credit": amt},
			]
		elif t == "Interest":
			expense = s.interest_expense_account or frappe.throw(
				_("Set 'Interest on Deposits (Expense)' in SACCO Settings first.")
			)
			lines = [
				{"account": expense, "debit": amt},
				{"account": savings, "credit": amt, "party": member},
			]
		elif t == "Dividend":
			payable = s.dividend_payable_account or frappe.throw(_("Set 'Dividends Payable' in SACCO Settings first."))
			lines = [
				{"account": payable, "debit": amt, "party": member},
				{"account": savings, "credit": amt, "party": member},
			]
		elif t == "Withholding Tax":
			wht = s.withholding_tax_account or frappe.throw(_("Set 'Withholding Tax Payable' in SACCO Settings first."))
			lines = [
				{"account": savings, "debit": amt, "party": member},
				{"account": wht, "credit": amt},
			]
		else:
			frappe.throw(_("Unknown transaction type {0}").format(t))

		remark = f"Savings {t.lower()} {self.name} - {member}"
		if self.reference_no:
			remark += f" (ref {self.reference_no})"
		if self.remarks:
			remark += f": {self.remarks}"
		return make_journal_entry(lines, self.posting_date, remark, reference=(self.doctype, self.name))


def create_savings_transaction(
	savings_account,
	transaction_type,
	amount,
	posting_date=None,
	payment_account=None,
	mode_of_payment=None,
	reference_no=None,
	reference=None,
	remarks=None,
	submit=True,
	skip_withdrawable_check=False,
):
	"""Programmatic entry point used by runs, remittances, M-Pesa and withdrawals."""
	txn = frappe.new_doc("SACCO Savings Transaction")
	txn.savings_account = savings_account
	txn.transaction_type = transaction_type
	txn.amount = flt(amount)
	txn.posting_date = posting_date or frappe.utils.today()
	txn.payment_account = payment_account
	txn.mode_of_payment = mode_of_payment or "Bank"
	txn.reference_no = reference_no
	if reference:
		txn.reference_doctype, txn.reference_name = reference
	txn.remarks = remarks
	txn.flags.skip_withdrawable_check = skip_withdrawable_check
	txn.flags.ignore_permissions = True
	txn.insert()
	if submit:
		txn.submit()
	return txn
