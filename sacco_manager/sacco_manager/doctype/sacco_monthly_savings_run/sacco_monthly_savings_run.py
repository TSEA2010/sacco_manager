# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Bulk monthly savings posting (e.g. a payroll check-off file already received into the bank).

All-or-nothing: if any member's posting fails, the whole run is rolled back and the
error names the member, instead of silently skipping people.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, today

from sacco_manager.utils import describe_error
from sacco_manager.sacco_manager.doctype.sacco_savings_transaction.sacco_savings_transaction import (
	create_savings_transaction,
)


class SACCOMonthlySavingsRun(Document):
	def validate(self):
		self.title = f"{self.period_month} {self.period_year} - Savings Run"
		dupes = {r.savings_account for r in self.members if [x.savings_account for x in self.members].count(r.savings_account) > 1}
		if dupes:
			frappe.throw(_("Savings account(s) listed more than once: {0}").format(", ".join(sorted(dupes))))
		for r in self.members:
			if r.status != "Skipped" and flt(r.amount) <= 0:
				frappe.throw(_("Row {0}: amount must be greater than zero (or mark the row Skipped).").format(r.idx))
			if r.status == "Skipped" and not r.skip_reason:
				frappe.throw(_("Row {0}: give a reason for skipping {1}.").format(r.idx, r.member))
		self.update_totals()

	def update_totals(self):
		active = [r for r in self.members if r.status != "Skipped"]
		self.total_members = len(active)
		self.total_amount = sum(flt(r.amount) for r in active)

	def on_submit(self):
		for row in self.members:
			if row.status == "Skipped" or row.transaction_ref:
				continue
			try:
				txn = create_savings_transaction(
					savings_account=row.savings_account,
					transaction_type="Deposit",
					amount=row.amount,
					posting_date=self.posting_date or today(),
					mode_of_payment="Check-off",
					reference=(self.doctype, self.name),
					remarks=f"{self.period_month} {self.period_year} savings",
				)
			except Exception as e:
				frappe.throw(_("Row {0} ({1}): {2}").format(row.idx, row.member, describe_error(e)))
			row.db_set({"status": "Posted", "transaction_ref": txn.name})
		self.db_set("status", "Submitted")

	def on_cancel(self):
		self.ignore_linked_doctypes = ("SACCO Savings Transaction",)
		for row in self.members:
			if row.transaction_ref:
				txn = frappe.get_doc("SACCO Savings Transaction", row.transaction_ref)
				if txn.docstatus == 1:
					txn.flags.ignore_permissions = True
					txn.cancel()
				row.db_set({"status": "Pending", "transaction_ref": None})
		self.db_set("status", "Draft")

	@frappe.whitelist()
	def fetch_members(self):
		if self.docstatus != 0:
			frappe.throw(_("Cannot fetch members on a submitted run."))
		existing = frappe.db.exists(
			"SACCO Monthly Savings Run",
			{"period_month": self.period_month, "period_year": self.period_year, "docstatus": 1, "name": ["!=", self.name]},
		)
		if existing:
			frappe.throw(_("A submitted savings run already exists for {0} {1}: {2}").format(self.period_month, self.period_year, existing))
		accounts = frappe.get_all(
			"SACCO Savings Account",
			filters={"status": "Active", "monthly_contribution": [">", 0]},
			fields=["name", "member", "monthly_contribution"],
			order_by="member asc",
		)
		if not accounts:
			frappe.throw(_("No active savings accounts with a monthly contribution found."))
		self.members = []
		for acc in accounts:
			self.append(
				"members",
				{
					"member": acc.member,
					"member_name": frappe.db.get_value("SACCO Member", acc.member, "full_name"),
					"savings_account": acc.name,
					"pledged_amount": acc.monthly_contribution,
					"amount": acc.monthly_contribution,
					"status": "Pending",
				},
			)
		self.save()
		frappe.msgprint(_("Fetched {0} members.").format(len(accounts)), alert=True)
