# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Interest on member savings for a period.

Interest is computed on the Average Daily Balance (default) so a deposit made the day
before the run does not earn a full period of interest. Gross interest is credited via
SACCO Savings Transactions (Dr Interest on Deposits / Cr Members' Savings); withholding
tax, if configured, is a separate "Withholding Tax" savings transaction per member
(Dr Members' Savings / Cr WHT Payable), so balances always equal the sum of transactions.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import date_diff, flt, getdate

from sacco_manager.sacco_manager.doctype.sacco_savings_transaction.sacco_savings_transaction import (
	create_savings_transaction,
)
from sacco_manager.utils import enforce_maker_checker, get_settings


def average_daily_balance(savings_account, start, end):
	"""ADB over [start, end] inclusive from posted savings transactions."""
	start, end = getdate(start), getdate(end)
	opening = flt(
		frappe.db.sql(
			"""SELECT SUM(CASE WHEN transaction_type IN ('Withdrawal','Withholding Tax') THEN -amount ELSE amount END)
			FROM `tabSACCO Savings Transaction`
			WHERE savings_account=%s AND docstatus=1 AND posting_date < %s""",
			(savings_account, start),
		)[0][0]
	)
	moves = frappe.db.sql(
		"""SELECT posting_date, SUM(CASE WHEN transaction_type IN ('Withdrawal','Withholding Tax') THEN -amount ELSE amount END) AS delta
		FROM `tabSACCO Savings Transaction`
		WHERE savings_account=%s AND docstatus=1 AND posting_date BETWEEN %s AND %s
		GROUP BY posting_date ORDER BY posting_date""",
		(savings_account, start, end),
		as_dict=True,
	)
	days = date_diff(end, start) + 1
	total, balance, cursor = 0.0, opening, start
	for m in moves:
		d = getdate(m.posting_date)
		total += balance * date_diff(d, cursor)
		balance += flt(m.delta)
		cursor = d
	total += balance * (date_diff(end, cursor) + 1)
	return total / days if days > 0 else 0.0, balance


class SACCOInterestRun(Document):
	def validate(self):
		if getdate(self.period_start) >= getdate(self.period_end):
			frappe.throw(_("Period End must be after Period Start."))
		overlap = frappe.db.sql(
			"""SELECT name FROM `tabSACCO Interest Run`
			WHERE docstatus=1 AND name!=%(n)s
			  AND (IFNULL(account_type,'')=%(t)s OR IFNULL(account_type,'')='' OR %(t)s='')
			  AND period_start <= %(e)s AND period_end >= %(s)s LIMIT 1""",
			{"n": self.name or "", "t": self.account_type or "", "s": self.period_start, "e": self.period_end},
		)
		if overlap:
			frappe.throw(_("Interest has already been run for an overlapping period: {0}").format(overlap[0][0]))
		if self.docstatus == 0:
			self.calculate()

	def calculate(self):
		filters = {"status": ["in", ["Active", "Dormant"]]}
		if self.account_type:
			filters["account_type"] = self.account_type
		accounts = frappe.get_all(
			"SACCO Savings Account", filters=filters, fields=["name", "member", "interest_rate"], order_by="member"
		)
		wht_rate = flt(get_settings().interest_wht_rate)
		days = date_diff(self.period_end, self.period_start) + 1
		self.set("details", [])
		total = total_wht = 0.0
		for acc in accounts:
			rate = flt(acc.interest_rate) or flt(self.interest_rate)
			if not rate:
				continue
			adb, closing = average_daily_balance(acc.name, self.period_start, self.period_end)
			base = closing if self.basis == "Closing Balance" else adb
			interest = round(max(base, 0) * rate / 100 * days / 365, 2)
			if interest <= 0:
				continue
			wht = round(interest * wht_rate / 100, 2)
			self.append(
				"details",
				{
					"member": acc.member,
					"savings_account": acc.name,
					"opening_balance": round(base, 2),
					"interest_rate": rate,
					"interest_earned": interest,
					"wht_amount": wht,
					"net_interest": interest - wht,
					"effective_start": self.period_start,
				},
			)
			total += interest
			total_wht += wht
		self.total_interest = total
		self.total_wht = total_wht

	def before_submit(self):
		enforce_maker_checker(self, _("submit"))
		if not self.details:
			frappe.throw(_("No interest to post. Save the document first to calculate."))

	def on_submit(self):
		for row in self.details:
			txn = create_savings_transaction(
				savings_account=row.savings_account,
				transaction_type="Interest",
				amount=row.interest_earned,
				posting_date=self.period_end,
				mode_of_payment="Internal",
				reference=(self.doctype, self.name),
				remarks=f"Interest {self.period_start} to {self.period_end} @ {row.interest_rate}%",
			)
			row.db_set("savings_transaction", txn.name)
			if flt(row.wht_amount) > 0:
				create_savings_transaction(
					savings_account=row.savings_account,
					transaction_type="Withholding Tax",
					amount=row.wht_amount,
					posting_date=self.period_end,
					mode_of_payment="Internal",
					reference=(self.doctype, self.name),
					remarks=f"WHT on interest {txn.name}",
					skip_withdrawable_check=True,
				)

	def on_cancel(self):
		self.ignore_linked_doctypes = ("SACCO Savings Transaction",)
		cancel_linked_savings_transactions(self.doctype, self.name)


def cancel_linked_savings_transactions(doctype, name):
	"""Cancel every savings transaction a run created, newest first (WHT before the credit it taxed)."""
	for txn_name in frappe.get_all(
		"SACCO Savings Transaction",
		filters={"reference_doctype": doctype, "reference_name": name, "docstatus": 1},
		order_by="creation desc",
		pluck="name",
	):
		txn = frappe.get_doc("SACCO Savings Transaction", txn_name)
		txn.flags.ignore_permissions = True
		txn.flags.from_parent = True
		txn.cancel()


@frappe.whitelist()
def preview(name):
	doc = frappe.get_doc("SACCO Interest Run", name)
	doc.check_permission("write")
	doc.calculate()
	doc.save()
	return doc.total_interest
