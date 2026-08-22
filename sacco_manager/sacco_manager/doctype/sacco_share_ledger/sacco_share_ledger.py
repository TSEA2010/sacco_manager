# Copyright (c) 2026, TSEA and contributors
# For license information, please see license.txt

# sacco_share_ledger.py
import frappe
from frappe.model.document import Document
from frappe.utils import flt

class SACCOShareLedger(Document):
    def validate(self):
        self.total_amount = int(self.shares) * flt(self.price_per_share)

    def before_insert(self):
        if not self.price_per_share:
            default_price = frappe.db.get_single_value("SACCO Settings", "default_share_price")
            if default_price:
                self.price_per_share = default_price

    def on_submit(self):
        self.update_member_shares()
        self.post_gl()

    def on_cancel(self):
        self.update_member_shares(reverse=True)

    def update_member_shares(self, reverse=False):
        # Lock the member row for the duration of this DB transaction so
        # concurrent share purchases/redemptions against the same member
        # can't race and overwrite each other's totals.
        locked = frappe.db.get_value(
            "SACCO Member", self.member, ["total_shares", "share_balance"],
            as_dict=True, for_update=True
        ) or {}
        current_shares = locked.get("total_shares") or 0
        current_balance = locked.get("share_balance") or 0.0

        if self.transaction_type == "Purchase":
            new_shares = current_shares - self.shares if reverse else current_shares + self.shares
            new_balance = current_balance - self.total_amount if reverse else current_balance + self.total_amount
        else:  # Redemption
            new_shares = current_shares + self.shares if reverse else current_shares - self.shares
            new_balance = current_balance + self.total_amount if reverse else current_balance - self.total_amount

        if new_shares < 0:
            frappe.throw("Share redemption exceeds available shares")
        if new_balance < 0:
            frappe.throw("Share redemption exceeds available share balance")

        frappe.db.set_value("SACCO Member", self.member, {
            "total_shares": new_shares,
            "share_balance": new_balance
        })

    def post_gl(self):
        settings = frappe.get_single("SACCO Settings")
        if not settings.share_capital_account:
            frappe.throw("Configure Share Capital account in SACCO Settings first")
        jv = frappe.new_doc("Journal Entry")
        jv.posting_date = self.date or frappe.utils.today()
        jv.voucher_type = "Journal Entry"
        jv.company = frappe.db.get_value("Account", settings.share_capital_account, "company")
        jv.user_remark = f"Share {self.transaction_type} - {self.member}"
        if self.transaction_type == "Purchase":
            jv.append("accounts", {
                "account": settings.share_capital_account,
                "credit_in_account_currency": self.total_amount,
                "party_type": "SACCO Member",
                "party": self.member
            })
            jv.append("accounts", {
                "account": settings.cash_account,
                "debit_in_account_currency": self.total_amount
            })
        else:
            jv.append("accounts", {
                "account": settings.share_capital_account,
                "debit_in_account_currency": self.total_amount,
                "party_type": "SACCO Member",
                "party": self.member
            })
            jv.append("accounts", {
                "account": settings.cash_account,
                "credit_in_account_currency": self.total_amount
            })
        jv.insert(ignore_permissions=True)
        jv.submit()
        frappe.db.set_value(self.doctype, self.name, "journal_entry", jv.name)