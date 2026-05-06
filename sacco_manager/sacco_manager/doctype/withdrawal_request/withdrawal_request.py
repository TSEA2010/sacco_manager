# Copyright (c) 2026, TSEA and contributors
# For license information, please see license.txt
# withdrawal_request.py

import frappe
from frappe.model.document import Document
from frappe.utils import flt


class WithdrawalRequest(Document):

    def validate(self):
        if not self.savings_account:
            return
        balance = frappe.db.get_value("SACCO Savings Account", self.savings_account, "balance")
        if flt(self.amount) > flt(balance):
            frappe.throw(f"Requested amount exceeds available balance of {balance}")

    def on_submit(self):
        txn = frappe.new_doc("SACCO Savings Transaction")
        txn.savings_account = self.savings_account
        txn.member = self.member
        txn.transaction_type = "Withdrawal"
        txn.amount = self.amount
        txn.posting_date = frappe.utils.today()
        txn.remarks = f"Withdrawal Request: {self.name}"
        txn.insert(ignore_permissions=True)
        txn.submit()
        frappe.db.set_value(self.doctype, self.name, "savings_transaction", txn.name)

    def on_cancel(self):
        if self.get("savings_transaction"):
            txn = frappe.get_doc("SACCO Savings Transaction", self.savings_transaction)
            if txn.docstatus == 1:
                txn.cancel()