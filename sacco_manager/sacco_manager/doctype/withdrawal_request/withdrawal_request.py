# Copyright (c) 2026, TSEA and contributors
# For license information, please see license.txt
import frappe
from frappe.model.document import Document
from frappe.utils import flt, today


class WithdrawalRequest(Document):
    def validate(self):
        if not self.savings_account:
            return

        account = frappe.db.get_value(
            "SACCO Savings Account",
            self.savings_account,
            ["balance", "member"],
            as_dict=True,
        )

        # Lock savings_account to the selected member (server-side safety net,
        # in case the client-side filter is bypassed via API/import)
        if self.member and account.member and account.member != self.member:
            frappe.throw(
                f"Savings Account {self.savings_account} does not belong to member {self.member}"
            )

        if flt(self.amount) > flt(account.balance):
            frappe.throw(f"Requested amount exceeds available balance of {account.balance}")

    def on_submit(self):
        txn = frappe.new_doc("SACCO Savings Transaction")
        txn.savings_account = self.savings_account
        txn.member = self.member
        txn.transaction_type = "Withdrawal"
        txn.amount = self.amount
        # Use the requested transaction_date so the GL/savings transaction
        # posts backdated instead of always landing on today
        txn.posting_date = self.transaction_date or today()
        txn.remarks = f"Withdrawal Request: {self.name}"
        txn.insert(ignore_permissions=True)
        txn.submit()
        frappe.db.set_value(self.doctype, self.name, "savings_transaction", txn.name)

    def on_cancel(self):
        if self.get("savings_transaction"):
            txn = frappe.get_doc("SACCO Savings Transaction", self.savings_transaction)
            if txn.docstatus == 1:
                txn.cancel()