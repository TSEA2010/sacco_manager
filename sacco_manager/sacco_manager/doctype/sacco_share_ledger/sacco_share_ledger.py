# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Member share capital movements.

Purchase      Dr payment account          Cr Share Capital [member]
Redemption    Dr Share Capital [member]   Cr payment account
Transfer In/Out move shares between members (paired entries, no cash); post with payment account = Share Capital
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt

from sacco_manager.accounting import cancel_journal_entry, make_journal_entry
from sacco_manager.utils import require_setting

INCREASE = ("Purchase", "Transfer In")


class SACCOShareLedger(Document):
	def before_insert(self):
		if not self.price_per_share:
			self.price_per_share = frappe.db.get_single_value("SACCO Settings", "default_share_price")

	def validate(self):
		if cint(self.shares) <= 0:
			frappe.throw(_("Number of shares must be greater than zero."))
		if flt(self.price_per_share) <= 0:
			frappe.throw(_("Set a price per share (or the default share price in SACCO Settings)."))
		self.total_amount = cint(self.shares) * flt(self.price_per_share)
		if not self.date:
			self.date = frappe.utils.today()
		if not self.payment_account:
			self.payment_account = require_setting("cash_account", "Default Cash / Bank Account")

	def on_submit(self):
		self._update_member_shares(reverse=False)
		self.db_set("journal_entry", self._post_gl())

	def on_cancel(self):
		self.ignore_linked_doctypes = ("Journal Entry", "GL Entry", "Payment Ledger Entry")
		self._update_member_shares(reverse=True)
		cancel_journal_entry(self.journal_entry)

	def _update_member_shares(self, reverse):
		row = frappe.db.get_value(
			"SACCO Member", self.member, ["total_shares", "share_balance"], as_dict=True, for_update=True
		)
		sign = 1 if self.transaction_type in INCREASE else -1
		if reverse:
			sign = -sign
		new_shares = cint(row.total_shares) + sign * cint(self.shares)
		new_balance = flt(row.share_balance) + sign * flt(self.total_amount)
		if new_shares < 0 or new_balance < -0.005:
			frappe.throw(_("Member {0} does not hold enough shares for this.").format(self.member))
		frappe.db.set_value("SACCO Member", self.member, {"total_shares": new_shares, "share_balance": new_balance})

	def _post_gl(self):
		share_capital = require_setting("share_capital_account", "Share Capital (Equity)")
		amt = flt(self.total_amount)
		if self.transaction_type in INCREASE:
			lines = [
				{"account": self.payment_account, "debit": amt},
				{"account": share_capital, "credit": amt, "party": self.member},
			]
		else:
			lines = [
				{"account": share_capital, "debit": amt, "party": self.member},
				{"account": self.payment_account, "credit": amt},
			]
		if self.payment_account == share_capital:
			# a transfer leg: member-to-member on the same account
			lines[0]["party"] = lines[0].get("party") or self.member
			lines[1]["party"] = lines[1].get("party") or self.member
		return make_journal_entry(
			lines, self.date, f"Share {self.transaction_type.lower()} {self.name} - {self.member}",
			reference=(self.doctype, self.name),
		)


def create_share_entry(member, amount, posting_date, payment_account, reference=None, reference_no=None):
	"""Buy as many whole shares as `amount` covers at the default price (used by remittances / M-Pesa)."""
	price = flt(frappe.db.get_single_value("SACCO Settings", "default_share_price"))
	if price <= 0:
		frappe.throw(_("Set the default share price in SACCO Settings."))
	shares = int(flt(amount) // price)
	if shares <= 0:
		frappe.throw(_("Amount {0} is less than one share ({1}).").format(amount, price))
	doc = frappe.get_doc(
		{
			"doctype": "SACCO Share Ledger",
			"member": member,
			"transaction_type": "Purchase",
			"shares": shares,
			"price_per_share": price,
			"date": posting_date,
			"payment_account": payment_account,
			"reference_no": reference_no or (f"{reference[0]} {reference[1]}" if reference else None),
		}
	)
	doc.flags.ignore_permissions = True
	doc.insert()
	doc.submit()
	return doc
