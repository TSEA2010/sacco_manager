# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Member share capital movements.

Purchase      Dr payment account               Cr Share Capital [member]
Redemption    Dr Share Capital [member]        Cr payment account   (only for members leaving)
Transfer Out  Dr Share Capital [from member]   Cr Share Capital [to member]  - one JE; the paired
              "Transfer In" entry for the receiving member is created automatically (no JE of its own).
SACCO shares are transferable, not withdrawable, so redemption is limited to exiting members.
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
		if self.transaction_type == "Transfer In" and not self.flags.from_transfer:
			frappe.throw(_("Record a share transfer as 'Transfer Out' on the member giving the shares; "
				"the Transfer In is created automatically."))
		if self.transaction_type == "Transfer Out":
			if not self.transfer_member or self.transfer_member == self.member:
				frappe.throw(_("Choose the member receiving the shares."))
		if self.transaction_type == "Redemption":
			status = frappe.db.get_value("SACCO Member", self.member, "status")
			if status not in ("Withdrawn", "Inactive") and "System Manager" not in frappe.get_roles():
				frappe.throw(_("Shares can only be redeemed when a member exits (status Withdrawn). "
					"Use Transfer Out to sell shares to another member."))
		if self.transaction_type in ("Purchase", "Redemption") and not self.payment_account:
			self.payment_account = require_setting("cash_account", "Default Cash / Bank Account")

	def on_submit(self):
		self._update_member_shares(self.member, self._sign(), cint(self.shares), flt(self.total_amount))
		if self.transaction_type == "Transfer In":
			return  # posted by its Transfer Out
		self.db_set("journal_entry", self._post_gl())
		if self.transaction_type == "Transfer Out":
			pair = frappe.get_doc({
				"doctype": "SACCO Share Ledger", "member": self.transfer_member, "transaction_type": "Transfer In",
				"shares": self.shares, "price_per_share": self.price_per_share, "date": self.date,
				"transfer_member": self.member, "transfer_entry": self.name, "journal_entry": self.journal_entry,
				"reference_no": self.reference_no,
			})
			pair.flags.from_transfer = True
			pair.flags.ignore_permissions = True
			pair.insert()
			pair.submit()
			self.db_set("transfer_entry", pair.name)

	def on_cancel(self):
		self.ignore_linked_doctypes = ("Journal Entry", "GL Entry", "Payment Ledger Entry", "SACCO Share Ledger")
		if self.transaction_type == "Transfer In" and not self.flags.from_transfer:
			frappe.throw(_("Cancel the Transfer Out {0} instead.").format(self.transfer_entry))
		if self.transaction_type == "Transfer Out" and self.transfer_entry:
			pair = frappe.get_doc("SACCO Share Ledger", self.transfer_entry)
			if pair.docstatus == 1:
				pair.flags.from_transfer = True
				pair.flags.ignore_permissions = True
				pair.cancel()
		self._update_member_shares(self.member, -self._sign(), cint(self.shares), flt(self.total_amount))
		if self.transaction_type != "Transfer In":
			cancel_journal_entry(self.journal_entry)

	def _sign(self):
		return 1 if self.transaction_type in INCREASE else -1

	@staticmethod
	def _update_member_shares(member, sign, shares, amount):
		row = frappe.db.get_value("SACCO Member", member, ["total_shares", "share_balance"], as_dict=True, for_update=True)
		new_shares = cint(row.total_shares) + sign * shares
		new_balance = flt(row.share_balance) + sign * amount
		if new_shares < 0 or new_balance < -0.005:
			frappe.throw(_("Member {0} does not hold enough shares for this.").format(member))
		frappe.db.set_value("SACCO Member", member, {"total_shares": new_shares, "share_balance": new_balance})

	def _post_gl(self):
		share_capital = require_setting("share_capital_account", "Share Capital (Equity)")
		amt = flt(self.total_amount)
		if self.transaction_type == "Purchase":
			lines = [{"account": self.payment_account, "debit": amt},
				{"account": share_capital, "credit": amt, "party": self.member}]
		elif self.transaction_type == "Redemption":
			lines = [{"account": share_capital, "debit": amt, "party": self.member},
				{"account": self.payment_account, "credit": amt}]
		else:  # Transfer Out
			lines = [{"account": share_capital, "debit": amt, "party": self.member},
				{"account": share_capital, "credit": amt, "party": self.transfer_member}]
		return make_journal_entry(
			lines, self.date, f"Share {self.transaction_type.lower()} {self.name} - {self.member}"
			+ (f" to {self.transfer_member}" if self.transfer_member else ""),
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
