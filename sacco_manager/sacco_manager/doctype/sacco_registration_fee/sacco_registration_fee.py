# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Membership registration fee: Dr cash/bank/M-Pesa, Cr Registration Fee Income."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, nowdate

from sacco_manager.accounting import cancel_journal_entry, make_journal_entry
from sacco_manager.utils import require_setting


class SACCORegistrationFee(Document):
	def before_insert(self):
		if not flt(self.amount):
			self.amount = flt(frappe.db.get_single_value("SACCO Settings", "registration_fee_amount"))

	def validate(self):
		if flt(self.amount) <= 0:
			frappe.throw(_("Registration fee amount must be greater than zero."))
		existing = frappe.db.get_value(
			"SACCO Registration Fee", {"member": self.member, "docstatus": 1, "name": ["!=", self.name]}, "name"
		)
		if existing:
			frappe.throw(_("Member {0} has already paid the registration fee ({1}).").format(self.member, existing))
		if not self.payment_account:
			self.payment_account = require_setting("cash_account", "Default Cash / Bank Account")
		if frappe.get_cached_value("Account", self.payment_account, "account_type") not in ("Cash", "Bank"):
			frappe.throw(_("Payment Account must be a Cash or Bank (incl. M-Pesa) account."))
		self.member_name = frappe.db.get_value("SACCO Member", self.member, "full_name")
		if not self.payment_date:
			self.payment_date = nowdate()

	def on_submit(self):
		income = require_setting("registration_fee_income_account", "Registration Fee Income")
		je = make_journal_entry(
			[
				{"account": self.payment_account, "debit": flt(self.amount)},
				{"account": income, "credit": flt(self.amount)},
			],
			self.payment_date,
			f"Registration fee {self.name} - {self.member_name} ({self.member})",
			reference=(self.doctype, self.name),
		)
		self.db_set("journal_entry", je)
		frappe.db.set_value("SACCO Member", self.member, {"status": "Active", "registration_fee_je": je})

	def on_cancel(self):
		self.ignore_linked_doctypes = ("Journal Entry", "GL Entry", "Payment Ledger Entry")
		cancel_journal_entry(self.journal_entry)
		frappe.db.set_value("SACCO Member", self.member, "registration_fee_je", None)


def create_registration_fee(member, amount, posting_date, payment_account):
	doc = frappe.get_doc(
		{
			"doctype": "SACCO Registration Fee",
			"member": member,
			"amount": amount,
			"payment_date": posting_date,
			"payment_account": payment_account,
		}
	)
	doc.flags.ignore_permissions = True
	doc.insert()
	doc.submit()
	return doc
