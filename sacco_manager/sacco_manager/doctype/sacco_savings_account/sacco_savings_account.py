# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from sacco_manager.utils import get_multiplier


class SACCOSavingsAccount(Document):
	def autoname(self):
		member_no = frappe.db.get_value("SACCO Member", self.member, "member_no") or self.member
		prefix = f"{member_no}-SAV-"
		existing = frappe.get_all("SACCO Savings Account", filters={"name": ["like", f"{prefix}%"]}, pluck="name")
		nums = [int(m.group(1)) for n in existing if (m := re.search(r"-SAV-(\d+)$", n))]
		self.name = f"{prefix}{(max(nums) + 1 if nums else 1):02d}"
		if not self.account_no:
			self.account_no = self.name

	def validate(self):
		if self.is_new():
			self.balance = 0  # balances only move through SACCO Savings Transactions
		if self.interest_rate is None:
			self.interest_rate = 0
		if self.status == "Closed" and flt(self.balance):
			frappe.throw(_("Cannot close an account with a non-zero balance."))

	def after_insert(self):
		self.create_loan_security()

	def create_loan_security(self):
		existing = frappe.db.get_value("Loan Security", {"sacco_member_saving": self.name}, "name")
		if existing:
			self.db_set("loan_security", existing)
			return
		security_type = frappe.db.get_single_value("SACCO Settings", "sacco_loan_security_type")
		if not security_type:
			return
		full_name = frappe.db.get_value("SACCO Member", self.member, "full_name") or self.member
		security = frappe.new_doc("Loan Security")
		security.loan_security_code = f"SACCO-SEC-{self.name}"
		security.loan_security_name = f"{full_name} - SACCO Savings"
		security.loan_security_type = security_type
		security.is_sacco_security = 1
		security.sacco_member_saving = self.name
		security.original_security_value = flt(self.balance) * get_multiplier()
		security.available_security_value = security.original_security_value
		security.flags.ignore_permissions = True
		security.insert()
		self.db_set("loan_security", security.name)

	def sync_loan_security(self):
		"""Keep the Lending Loan Security value equal to balance x multiplier."""
		name = self.loan_security or frappe.db.get_value(
			"Loan Security", {"sacco_member_saving": self.name}, "name"
		)
		if not name:
			return
		balance = flt(frappe.db.get_value("SACCO Savings Account", self.name, "balance"))
		original = balance * get_multiplier()
		utilized = flt(frappe.db.get_value("Loan Security", name, "utilized_security_value"))
		frappe.db.set_value(
			"Loan Security",
			name,
			{"original_security_value": original, "available_security_value": max(0, original - utilized)},
		)
