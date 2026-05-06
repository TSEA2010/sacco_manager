# Copyright (c) 2026, TSEA and contributors
# For license information, please see license.txt
# sacco_interest_run.py

import frappe
from frappe.model.document import Document
from frappe.utils import date_diff, flt


class SACCOInterestRun(Document):

    def validate(self):
        if self.period_start >= self.period_end:
            frappe.throw("Period End must be after Period Start")

        if self.docstatus == 0:
            self.calculate_preview()

    def calculate_preview(self):
        filters = {"status": "Active"}
        if self.account_type:
            filters["account_type"] = self.account_type

        accounts = frappe.get_all(
            "SACCO Savings Account",
            filters=filters,
            fields=["name", "member", "balance", "interest_rate"]
        )

        days = date_diff(self.period_end, self.period_start)
        if days <= 0:
            frappe.throw("Invalid period: days must be greater than zero")

        self.set("details", [])
        total = 0

        for acc in accounts:
            rate = flt(acc.interest_rate) or flt(self.interest_rate)
            if not rate:
                continue
            interest = round(flt(acc.balance) * (rate / 100) * days / 365, 2)
            if interest <= 0:
                continue
            self.append("details", {
                "member": acc.member,
                "savings_account": acc.name,
                "opening_balance": acc.balance,
                "interest_rate": rate,
                "interest_earned": interest
            })
            total += interest

        self.total_interest = total

    def on_submit(self):
        if not self.details:
            frappe.throw("No interest details to process. Save the document first to preview.")

        # Check for duplicate run
        existing = frappe.db.exists("SACCO Interest Run", {
            "period_start": self.period_start,
            "period_end": self.period_end,
            "account_type": self.account_type,
            "docstatus": 1,
            "name": ("!=", self.name)
        })
        if existing:
            frappe.throw(f"Interest already run for this period: {existing}")

        for row in self.details:
            txn = frappe.new_doc("SACCO Savings Transaction")
            txn.savings_account = row.savings_account
            txn.member = row.member
            txn.transaction_type = "Interest"
            txn.amount = row.interest_earned
            txn.posting_date = self.period_end
            txn.remarks = f"Interest Run: {self.name}"
            txn.insert(ignore_permissions=True)
            txn.submit()
            frappe.db.set_value(
                "SACCO Interest Run Detail",
                row.name,
                "savings_transaction",
                txn.name
            )

        frappe.db.set_value(self.doctype, self.name, "total_interest", self.total_interest)

    def on_cancel(self):
        for row in self.details:
            if not row.savings_transaction:
                continue
            txn = frappe.get_doc("SACCO Savings Transaction", row.savings_transaction)
            if txn.docstatus == 1:
                txn.cancel()