# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Employer (branch) check-off remittance.

On submit (maker-checker):
1. Receipt:      Dr bank/M-Pesa (payment_account)      Cr Employer Remittance Clearing
2. Per member:   savings deposit, loan repayment and share purchase, each paid from the
                 clearing account (Dr clearing / Cr member savings, loan, share capital)
3. Late penalty: Dr Employer Remittance Clearing       Cr Penalty Income
Whatever is left in clearing is the employer's over/under-payment, visible in the
"Remittance Clearing" report until it is refunded or allocated.
"""

import calendar

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import date_diff, flt, getdate

from sacco_manager.accounting import cancel_journal_entry, make_journal_entry
from sacco_manager.lending_utils import create_loan_repayment, get_active_loans
from sacco_manager.sacco_manager.doctype.sacco_interest_run.sacco_interest_run import (
	cancel_linked_savings_transactions,
)
from sacco_manager.sacco_manager.doctype.sacco_savings_transaction.sacco_savings_transaction import (
	create_savings_transaction,
)
from sacco_manager.sacco_manager.doctype.sacco_share_ledger.sacco_share_ledger import create_share_entry
from sacco_manager.utils import enforce_maker_checker, get_settings, require_setting


class SACCOBranchRemittance(Document):
	def validate(self):
		self.title = f"{self.branch} - {self.period_month} {self.period_year}"
		self._set_due_date()
		self.expected_amount = sum(
			flt(r.expected_amount) + flt(r.expected_loan_repayment) + flt(r.share_amount) for r in self.members
		)
		self.allocated_amount = sum(
			flt(r.received_savings) + flt(r.received_loan_repayment) + flt(r.share_amount) for r in self.members
		)
		if flt(self.received_amount) and flt(self.allocated_amount) > flt(self.received_amount) + 0.005:
			frappe.throw(
				_("Allocated {0} is more than the {1} received.").format(self.allocated_amount, self.received_amount)
			)
		self.unallocated_amount = max(0, flt(self.received_amount) - flt(self.allocated_amount))
		for r in self.members:
			if flt(r.received_loan_repayment) and not r.loan:
				frappe.throw(_("Row {0}: select the loan for {1}'s repayment.").format(r.idx, r.member))
		self._calculate_penalty()
		if self.docstatus == 0:
			self.status = "Draft"

	def _set_due_date(self):
		due_day = int(get_settings().remittance_due_day or 5)
		month = list(calendar.month_name).index(self.period_month)
		# due on day N of the period month (same rule as the original app)
		year = int(self.period_year)
		due_day = min(due_day, calendar.monthrange(year, month)[1])
		self.due_date = f"{year}-{month:02d}-{due_day:02d}"

	def _calculate_penalty(self):
		self.is_late, self.days_late, self.penalty_amount = 0, 0, 0
		if not self.received_date or not self.due_date:
			return
		days = date_diff(getdate(self.received_date), getdate(self.due_date))
		if days <= 0:
			return
		s = get_settings()
		self.penalty_rate = flt(s.penalty_rate)
		cap = flt(s.max_penalty_percent) or 100
		self.is_late, self.days_late = 1, days
		penalty = flt(self.expected_amount) * self.penalty_rate / 100 * days / 30
		self.penalty_amount = round(min(penalty, flt(self.expected_amount) * cap / 100), 2)

	@frappe.whitelist()
	def fetch_members(self):
		if self.docstatus != 0:
			frappe.throw(_("Cannot fetch members on a submitted remittance."))
		if not (self.branch and self.period_month and self.period_year):
			frappe.throw(_("Set Branch, Month and Year first."))
		existing = frappe.db.exists(
			"SACCO Branch Remittance",
			{"branch": self.branch, "period_month": self.period_month, "period_year": self.period_year,
			 "docstatus": 1, "name": ["!=", self.name]},
		)
		if existing:
			frappe.throw(_("A submitted remittance already exists for this branch and month: {0}").format(existing))
		members = frappe.get_all("SACCO Member", filters={"branch": self.branch, "status": "Active"},
			fields=["name", "full_name"], order_by="member_no")
		self.members = []
		for m in members:
			acc = frappe.get_all("SACCO Savings Account", filters={"member": m.name, "status": "Active"},
				fields=["name", "monthly_contribution"], order_by="creation asc", limit=1)
			loans = get_active_loans(m.name)
			loan = loans[0] if loans else None
			saving = flt(acc[0].monthly_contribution) if acc else 0
			repay = flt(loan.monthly_repayment_amount) if loan else 0
			if not saving and not repay:
				continue
			self.append("members", {
				"member": m.name,
				"member_name": m.full_name,
				"savings_account": acc[0].name if acc else None,
				"expected_amount": saving,
				"received_savings": saving,
				"loan": loan.name if loan else None,
				"expected_loan_repayment": repay,
				"received_loan_repayment": repay,
				"status": "Pending",
			})
		if not self.members:
			frappe.throw(_("No active members with contributions or loans found in {0}.").format(self.branch))
		self.save()
		frappe.msgprint(_("Fetched {0} members. Expected: {1}").format(
			len(self.members), frappe.format_value(self.expected_amount, {"fieldtype": "Currency"})), alert=True)

	def before_submit(self):
		enforce_maker_checker(self, _("submit"))
		if flt(self.received_amount) <= 0 or not self.received_date:
			frappe.throw(_("Enter the Amount Received and Date Received before submitting."))
		if not self.payment_account:
			self.payment_account = require_setting("cash_account", "Default Cash / Bank Account")

	def on_submit(self):
		clearing = require_setting("branch_receivable_account", "Employer Remittance Clearing")
		ref = (self.doctype, self.name)
		self.db_set("receipt_journal_entry", make_journal_entry(
			[{"account": self.payment_account, "debit": flt(self.received_amount)},
			 {"account": clearing, "credit": flt(self.received_amount)}],
			self.received_date, f"Check-off received - {self.title} ({self.reference_no or ''})", reference=ref))

		label = f"{self.period_month} {self.period_year} check-off"
		for r in self.members:
			try:
				if flt(r.received_savings) > 0:
					if not r.savings_account:
						frappe.throw(_("no savings account"))
					txn = create_savings_transaction(r.savings_account, "Deposit", r.received_savings, self.received_date,
						payment_account=clearing, mode_of_payment="Check-off", reference=ref, remarks=label)
					r.db_set("transaction_ref", txn.name)
				if flt(r.received_loan_repayment) > 0:
					rp = create_loan_repayment(r.loan, r.received_loan_repayment, self.received_date, clearing,
						reference_number=self.name, remarks=label)
					r.db_set("loan_repayment", rp.name)
				if flt(r.share_amount) > 0:
					sh = create_share_entry(r.member, r.share_amount, self.received_date, clearing, reference=ref)
					r.db_set("share_entry", sh.name)
				r.db_set("status", "Posted")
			except Exception as e:
				frappe.throw(_("Row {0} ({1}): {2}").format(r.idx, r.member_name or r.member, e))

		if self.is_late and flt(self.penalty_amount) > 0:
			income = require_setting("penalty_income_account", "Penalty Income")
			self.db_set("penalty_journal_entry", make_journal_entry(
				[{"account": clearing, "debit": flt(self.penalty_amount)},
				 {"account": income, "credit": flt(self.penalty_amount)}],
				self.received_date, f"Late remittance penalty - {self.title} ({self.days_late} days)", reference=ref))

		status = "Paid" if flt(self.received_amount) >= flt(self.expected_amount) else ("Overdue" if self.is_late else "Pending")
		self.db_set("status", status)

	def on_cancel(self):
		self.ignore_linked_doctypes = ("SACCO Savings Transaction", "Loan Repayment", "SACCO Share Ledger",
			"Journal Entry", "GL Entry", "Payment Ledger Entry")
		for r in self.members:
			for field, dt in (("loan_repayment", "Loan Repayment"), ("share_entry", "SACCO Share Ledger")):
				if r.get(field) and frappe.db.get_value(dt, r.get(field), "docstatus") == 1:
					d = frappe.get_doc(dt, r.get(field))
					d.flags.ignore_permissions = True
					d.cancel()
			r.db_set("status", "Pending")
		cancel_linked_savings_transactions(self.doctype, self.name)
		cancel_journal_entry(self.penalty_journal_entry)
		cancel_journal_entry(self.receipt_journal_entry)
		self.db_set("status", "Cancelled")
