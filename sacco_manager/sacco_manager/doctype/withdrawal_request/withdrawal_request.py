# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Member savings withdrawal. Submit = approval (maker-checker), which posts the savings
withdrawal and, for M-Pesa payouts, queues a B2C payment."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, today

from sacco_manager.sacco_manager.doctype.sacco_savings_transaction.sacco_savings_transaction import (
	create_savings_transaction,
)
from sacco_manager.utils import enforce_maker_checker, get_withdrawable_balance, normalize_ke_phone


class WithdrawalRequest(Document):
	def validate(self):
		if flt(self.amount) <= 0:
			frappe.throw(_("Amount must be greater than zero."))
		account = frappe.db.get_value(
			"SACCO Savings Account", self.savings_account, ["member", "status"], as_dict=True
		)
		if not account:
			frappe.throw(_("Savings Account {0} not found.").format(self.savings_account))
		if self.member and account.member != self.member:
			frappe.throw(_("Savings Account {0} does not belong to member {1}.").format(self.savings_account, self.member))
		self.member = account.member
		self.withdrawable_balance = get_withdrawable_balance(self.savings_account)
		if flt(self.amount) > flt(self.withdrawable_balance):
			frappe.throw(
				_("Requested amount exceeds the withdrawable balance of {0}.").format(
					frappe.format_value(self.withdrawable_balance, {"fieldtype": "Currency"})
				)
			)
		if not self.initiated_by:
			self.initiated_by = frappe.session.user
		if self.docstatus == 0:
			self.status = "Pending Approval"
		if self.payment_mode == "M-Pesa":
			phone = self.mpesa_phone or frappe.db.get_value("SACCO Member", self.member, "phone")
			self.mpesa_phone = normalize_ke_phone(phone)
			if not self.mpesa_phone:
				frappe.throw(_("Enter a valid M-Pesa phone number for the payout."))
			if not self.payment_account:
				self.payment_account = _default_mpesa_gl()

	def before_submit(self):
		enforce_maker_checker(self, _("approve"))
		self.approved_by = frappe.session.user
		self.status = "Approved"

	def on_submit(self):
		txn = create_savings_transaction(
			savings_account=self.savings_account,
			transaction_type="Withdrawal",
			amount=self.amount,
			posting_date=self.transaction_date or today(),
			payment_account=self.payment_account,
			mode_of_payment=self.payment_mode,
			reference=(self.doctype, self.name),
			remarks=self.reason,
		)
		self.db_set("savings_transaction", txn.name)
		if self.payment_mode == "M-Pesa":
			from sacco_manager.mobile_money.b2c import queue_withdrawal_payout

			queue_withdrawal_payout(self.name)
		else:
			self.db_set("status", "Paid")

	def on_cancel(self):
		self.ignore_linked_doctypes = ("SACCO M-Pesa Transaction",)
		if self.mpesa_transaction and frappe.db.get_value(
			"SACCO M-Pesa Transaction", self.mpesa_transaction, "status"
		) in ("Pending", "Paid"):
			frappe.throw(_("An M-Pesa payout was already sent for this request; reverse it with Safaricom first."))
		if self.savings_transaction:
			txn = frappe.get_doc("SACCO Savings Transaction", self.savings_transaction)
			if txn.docstatus == 1:
				txn.flags.ignore_permissions = True
				txn.flags.from_parent = True
				txn.cancel()
		self.db_set("status", "Rejected")


def _default_mpesa_gl():
	acc = frappe.get_all(
		"SACCO M-Pesa Account", filters={"enabled": 1}, fields=["gl_account"], order_by="creation asc", limit=1
	)
	return acc[0].gl_account if acc else None


@frappe.whitelist()
def get_withdrawable(savings_account):
	frappe.has_permission("SACCO Savings Account", "read", savings_account, throw=True)
	return get_withdrawable_balance(savings_account)
