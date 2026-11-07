# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt

# sacco_manager/sacco_manager/doctype/sacco_monthly_savings_run/sacco_monthly_savings_run.py

import frappe
from frappe.model.document import Document
from frappe.utils import today


class SACCOMonthlySavingsRun(Document):

    def validate(self):
        self.title = f"{self.period_month} {self.period_year} - Savings Run"
        self.update_totals()

    def update_totals(self):
        active_rows = [r for r in self.members if r.status != "Skipped"]
        self.total_members = len(active_rows)
        self.total_amount = sum(r.amount for r in active_rows)

    def on_submit(self):
        self.post_savings_transactions()
        self.status = "Submitted"
        self.db_set("status", "Submitted")

    def on_cancel(self):
        self.cancel_savings_transactions()
        self.status = "Draft"
        self.db_set("status", "Draft")

    @frappe.whitelist()
    def fetch_members(self):
        """Fetch all active SACCO members with their savings accounts and pledged amounts."""
        if self.docstatus == 1:
            frappe.throw("Cannot fetch members on a submitted run.")

        # Check if this period already has a submitted run
        existing = frappe.db.exists("SACCO Monthly Savings Run", {
            "period_month": self.period_month,
            "period_year": self.period_year,
            "docstatus": 1,
            "name": ["!=", self.name]
        })
        if existing:
            frappe.throw(
                f"A submitted savings run already exists for {self.period_month} {self.period_year}: {existing}"
            )

        # Fetch all active savings accounts with a monthly contribution
        accounts = frappe.db.get_all(
            "SACCO Savings Account",
            filters={
                "status": "Active",
                "monthly_contribution": [">", 0]
            },
            fields=["name", "member", "monthly_contribution"]
        )

        if not accounts:
            frappe.throw("No active savings accounts with a monthly contribution found.")

        self.members = []
        for acc in accounts:
            self.append("members", {
                "member": acc.member,
                "savings_account": acc.name,
                "pledged_amount": acc.monthly_contribution,
                "amount": acc.monthly_contribution,
                "status": "Pending"
            })

        self.update_totals()
        self.save()
        frappe.msgprint(f"Fetched {len(accounts)} members.", alert=True)

    def post_savings_transactions(self):
        """Create a SACCO Savings Transaction for each non-skipped member."""
        for row in self.members:
            if row.status == "Skipped":
                continue

            # Prevent duplicate posting
            if row.transaction_ref:
                continue

            try:
                txn = frappe.new_doc("SACCO Savings Transaction")
                txn.member = row.member
                txn.savings_account = row.savings_account
                txn.transaction_type = "Deposit"
                txn.amount = row.amount
                txn.posting_date = self.posting_date or today()
                txn.reference = self.name
                txn.insert(ignore_permissions=True)
                txn.submit()

                # Update child row
                frappe.db.set_value(
                    "SACCO Monthly Savings Run Member",
                    row.name,
                    {
                        "status": "Posted",
                        "transaction_ref": txn.name
                    }
                )
            except Exception as e:
                frappe.log_error(
                    f"Failed to post savings for {row.member}: {str(e)}",
                    "SACCO Monthly Savings Run"
                )
                frappe.db.set_value(
                    "SACCO Monthly Savings Run Member",
                    row.name,
                    "status", "Skipped"
                )

        self.update_totals()

    def cancel_savings_transactions(self):
        """Cancel all posted transactions when run is cancelled."""
        for row in self.members:
            if row.status == "Posted" and row.transaction_ref:
                try:
                    txn = frappe.get_doc("SACCO Savings Transaction", row.transaction_ref)
                    txn.cancel()
                    frappe.db.set_value(
                        "SACCO Monthly Savings Run Member",
                        row.name,
                        {
                            "status": "Pending",
                            "transaction_ref": ""
                        }
                    )
                except Exception as e:
                    frappe.log_error(
                        f"Failed to cancel transaction {row.transaction_ref}: {str(e)}",
                        "SACCO Monthly Savings Run"
                    )