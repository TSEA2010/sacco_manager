# Copyright (c) 2026, TSEA and contributors
# For license information, please see license.txt
# sacco_manager/sacco_manager/doctype/sacco_branch_remittance/sacco_branch_remittance.py

import frappe
from frappe.model.document import Document
from frappe.utils import today, getdate, date_diff
import calendar


class SACCOBranchRemittance(Document):

    def validate(self):
        self.title = f"{self.branch} - {self.period_month} {self.period_year}"
        self.set_due_date()
        self.calculate_expected_amount()
        self.calculate_penalty()

    def set_due_date(self):
        """Due date = remittance_due_day of the period month/year."""
        settings = frappe.get_single("SACCO Settings")
        due_day = settings.remittance_due_day or 5
        month_num = list(calendar.month_name).index(self.period_month)
        self.due_date = f"{self.period_year}-{str(month_num).zfill(2)}-{str(due_day).zfill(2)}"

    def calculate_expected_amount(self):
        """Sum monthly contributions of all active members in this branch."""
        total = 0
        for row in self.members:
            total += row.expected_amount or 0
        self.expected_amount = total

    def calculate_penalty(self):
        """Calculate penalty if received_date is after due_date."""
        if not self.received_date or not self.due_date:
            self.is_late = 0
            self.days_late = 0
            self.penalty_amount = 0
            return

        settings = frappe.get_single("SACCO Settings")
        self.penalty_rate = settings.penalty_rate or 2.0

        received = getdate(self.received_date)
        due = getdate(self.due_date)

        if received > due:
            self.is_late = 1
            self.days_late = date_diff(received, due)
            # Penalty = expected_amount × rate% × days_late/30
            self.penalty_amount = (
                (self.expected_amount or 0)
                * (self.penalty_rate / 100)
                * (self.days_late / 30)
            )
        else:
            self.is_late = 0
            self.days_late = 0
            self.penalty_amount = 0

    @frappe.whitelist()
    def fetch_members(self):
        """Fetch all active members belonging to this branch."""
        if self.docstatus == 1:
            frappe.throw("Cannot fetch on a submitted document.")

        if not self.branch:
            frappe.throw("Please select a Branch first.")

        # Check for duplicate submitted run
        existing = frappe.db.exists("SACCO Branch Remittance", {
            "branch": self.branch,
            "period_month": self.period_month,
            "period_year": self.period_year,
            "docstatus": 1,
            "name": ["!=", self.name]
        })
        if existing:
            frappe.throw(
                f"A submitted remittance already exists for {self.branch} "
                f"— {self.period_month} {self.period_year}: {existing}"
            )

        accounts = frappe.db.get_all(
            "SACCO Savings Account",
            filters={
                "status": "Active",
                "monthly_contribution": [">", 0]
            },
            fields=["name", "member", "monthly_contribution"]
        )

        # Filter by branch via SACCO Member
        branch_accounts = []
        for acc in accounts:
            member_branch = frappe.db.get_value("SACCO Member", acc.member, "branch")
            if member_branch == self.branch:
                branch_accounts.append(acc)

        if not branch_accounts:
            frappe.throw(f"No active members with contributions found in branch {self.branch}.")

        self.members = []
        for acc in branch_accounts:
            self.append("members", {
                "member": acc.member,
                "savings_account": acc.name,
                "expected_amount": acc.monthly_contribution,
                "status": "Pending"
            })

        self.calculate_expected_amount()
        self.save()
        frappe.msgprint(
            f"Fetched {len(branch_accounts)} members. "
            f"Expected remittance: {frappe.utils.fmt_money(self.expected_amount)}",
            alert=True
        )

    def on_submit(self):
        """Post savings transactions for all members and penalty JE if late."""
        self.post_member_savings()
        if self.is_late and self.penalty_amount > 0:
            self.post_penalty_journal_entry()
        self.update_status()
        self.db_set("status", self.status)

    def post_member_savings(self):
        """Create deposit transactions for each member."""
        for row in self.members:
            if row.status == "Posted" and row.transaction_ref:
                continue
            try:
                txn = frappe.new_doc("SACCO Savings Transaction")
                txn.member = row.member
                txn.savings_account = row.savings_account
                txn.transaction_type = "Deposit"
                txn.amount = row.expected_amount
                txn.posting_date = self.received_date or today()
                txn.reference = self.name
                txn.insert(ignore_permissions=True)
                txn.submit()

                frappe.db.set_value(
                    "SACCO Branch Remittance Member", row.name,
                    {"status": "Posted", "transaction_ref": txn.name}
                )
            except Exception as e:
                frappe.log_error(
                    f"Failed posting for member {row.member}: {str(e)}",
                    "SACCO Branch Remittance"
                )

    def post_penalty_journal_entry(self):
        """Post penalty charged to the branch (not individual members)."""
        settings = frappe.get_single("SACCO Settings")

        if not settings.penalty_income_account:
            frappe.log_error(
                "penalty_income_account not set in SACCO Settings — skipping penalty JE",
                "SACCO Branch Remittance"
            )
            return

        if not settings.cash_account:
            frappe.log_error(
                "cash_account not set in SACCO Settings — skipping penalty JE",
                "SACCO Branch Remittance"
            )
            return

        jv = frappe.new_doc("Journal Entry")
        jv.posting_date = self.received_date or today()
        jv.voucher_type = "Journal Entry"
        jv.user_remark = (
            f"Late remittance penalty — {self.branch} "
            f"{self.period_month} {self.period_year} "
            f"({self.days_late} days late)"
        )

        # Dr. Cash/Bank (branch pays the penalty)
        jv.append("accounts", {
            "account": settings.cash_account,
            "debit_in_account_currency": self.penalty_amount
        })
        # Cr. Penalty Income
        jv.append("accounts", {
            "account": settings.penalty_income_account,
            "credit_in_account_currency": self.penalty_amount
        })

        jv.insert(ignore_permissions=True)
        jv.submit()
        self.db_set("penalty_journal_entry", jv.name)

    def update_status(self):
        if self.received_amount >= self.expected_amount:
            self.status = "Paid"
        elif self.is_late:
            self.status = "Overdue"
        else:
            self.status = "Pending"

    def on_cancel(self):
        """Cancel all posted transactions and penalty JE."""
        for row in self.members:
            if row.status == "Posted" and row.transaction_ref:
                try:
                    txn = frappe.get_doc("SACCO Savings Transaction", row.transaction_ref)
                    txn.cancel()
                    frappe.db.set_value(
                        "SACCO Branch Remittance Member", row.name,
                        {"status": "Pending", "transaction_ref": ""}
                    )
                except Exception as e:
                    frappe.log_error(
                        f"Failed cancelling {row.transaction_ref}: {str(e)}",
                        "SACCO Branch Remittance"
                    )

        if self.penalty_journal_entry:
            try:
                jv = frappe.get_doc("Journal Entry", self.penalty_journal_entry)
                jv.cancel()
                self.db_set("penalty_journal_entry", "")
            except Exception as e:
                frappe.log_error(
                    f"Failed cancelling penalty JE {self.penalty_journal_entry}: {str(e)}",
                    "SACCO Branch Remittance"
                )

        self.db_set("status", "Draft")