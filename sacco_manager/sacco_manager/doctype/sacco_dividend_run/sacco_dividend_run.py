# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Dividend on share capital, as approved by the AGM.

Declaration JE:  Dr Retained Earnings / Dividend Appropriation (gross)
                 Cr Dividends Payable [member] (net)   Cr Withholding Tax Payable (WHT)
Payout "Credit to Savings": one Dividend savings transaction per member
                 (Dr Dividends Payable [member] / Cr Members' Savings [member]).
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from sacco_manager.accounting import cancel_journal_entry, make_journal_entry
from sacco_manager.sacco_manager.doctype.sacco_interest_run.sacco_interest_run import (
	cancel_linked_savings_transactions,
)
from sacco_manager.sacco_manager.doctype.sacco_savings_transaction.sacco_savings_transaction import (
	create_savings_transaction,
)
from sacco_manager.utils import enforce_maker_checker, get_settings, require_setting


class SACCODividendRun(Document):
	def validate(self):
		if flt(self.dividend_rate) <= 0:
			frappe.throw(_("Dividend rate must be greater than zero."))
		dup = frappe.db.get_value(
			"SACCO Dividend Run", {"financial_year": self.financial_year, "docstatus": 1, "name": ["!=", self.name]}
		)
		if dup:
			frappe.throw(_("A dividend for {0} was already declared in {1}.").format(self.financial_year, dup))
		if self.docstatus == 0 and not self.details:
			self.calculate()
		self._totals()

	@frappe.whitelist()
	def calculate(self):
		"""Share capital per member as at `as_at_date`, from submitted share ledger entries."""
		rows = frappe.db.sql(
			"""SELECT member,
				SUM(CASE WHEN transaction_type IN ('Purchase','Transfer In') THEN total_amount ELSE -total_amount END) AS capital
			FROM `tabSACCO Share Ledger`
			WHERE docstatus=1 AND date <= %s
			GROUP BY member HAVING capital > 0 ORDER BY member""",
			self.as_at_date,
			as_dict=True,
		)
		wht_rate = flt(get_settings().dividend_wht_rate)
		self.set("details", [])
		for r in rows:
			gross = round(flt(r.capital) * flt(self.dividend_rate) / 100, 2)
			wht = round(gross * wht_rate / 100, 2)
			sav = frappe.get_all(
				"SACCO Savings Account",
				filters={"member": r.member, "status": ["in", ["Active", "Dormant"]]},
				order_by="creation asc",
				pluck="name",
				limit=1,
			)
			self.append(
				"details",
				{
					"member": r.member,
					"eligible_share_capital": r.capital,
					"gross_dividend": gross,
					"wht_amount": wht,
					"net_dividend": gross - wht,
					"savings_account": sav[0] if sav else None,
				},
			)
		self._totals()

	def _totals(self):
		self.total_gross = sum(flt(r.gross_dividend) for r in self.details)
		self.total_wht = sum(flt(r.wht_amount) for r in self.details)
		self.total_net = sum(flt(r.net_dividend) for r in self.details)

	def before_submit(self):
		enforce_maker_checker(self, _("submit"))
		if not self.details:
			frappe.throw(_("No members with share capital as at {0}.").format(self.as_at_date))
		if self.payout_method == "Credit to Savings":
			missing = [r.member for r in self.details if not r.savings_account]
			if missing:
				frappe.throw(_("These members have no savings account to credit: {0}").format(", ".join(missing)))

	def on_submit(self):
		appropriation = require_setting("dividend_appropriation_account", "Retained Earnings / Dividend Appropriation")
		payable = require_setting("dividend_payable_account", "Dividends Payable")
		lines = [{"account": appropriation, "debit": flt(self.total_gross)}]
		for r in self.details:
			lines.append({"account": payable, "credit": flt(r.net_dividend), "party": r.member})
		if flt(self.total_wht):
			lines.append({"account": require_setting("withholding_tax_account", "Withholding Tax Payable"), "credit": flt(self.total_wht)})
		self.db_set(
			"journal_entry",
			make_journal_entry(lines, self.posting_date, f"Dividend {self.financial_year} @ {self.dividend_rate}% - {self.name} ({self.agm_resolution})",
				reference=(self.doctype, self.name)),
		)
		if self.payout_method == "Credit to Savings":
			for r in self.details:
				if flt(r.net_dividend) <= 0:
					continue
				txn = create_savings_transaction(
					savings_account=r.savings_account,
					transaction_type="Dividend",
					amount=r.net_dividend,
					posting_date=self.posting_date,
					mode_of_payment="Internal",
					reference=(self.doctype, self.name),
					remarks=f"Dividend {self.financial_year} (net of WHT)",
				)
				r.db_set("savings_transaction", txn.name)

	def on_cancel(self):
		self.ignore_linked_doctypes = ("SACCO Savings Transaction", "Journal Entry", "GL Entry", "Payment Ledger Entry")
		cancel_linked_savings_transactions(self.doctype, self.name)
		cancel_journal_entry(self.journal_entry)
