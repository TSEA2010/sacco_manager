# Copyright (c) 2026, TSEA and contributors
# For license information, please see license.txt
# sacco_manager/sacco_manager/doctype/sacco_savings_account/sacco_savings_account.py

import frappe
from frappe.model.document import Document


class SACCOSavingsAccount(Document):

    def autoname(self):
        member_no = frappe.db.get_value("SACCO Member", self.member, "member_no")
        count = frappe.db.count("SACCO Savings Account", {"member": self.member})
        self.name = f"{member_no}-SAV-{str(count + 1).zfill(2)}"

    def validate(self):
        if not self.interest_rate:
            defaults = {"Ordinary": 0.0, "Fixed": 0.0, "Holiday": 0.0}
            self.interest_rate = defaults.get(self.account_type, 0.0)

    def after_insert(self):
        self.create_loan_security()

    def create_loan_security(self):
        existing = frappe.db.get_value(
            "Loan Security",
            {"sacco_member_saving": self.name},
            "name"
        )
        if existing:
            frappe.db.set_value(self.doctype, self.name, "loan_security", existing)
            return

        settings = frappe.get_single("SACCO Settings")
        if not settings.get("sacco_loan_security_type"):
            frappe.log_error(
                "sacco_loan_security_type not set in SACCO Settings — skipping Loan Security creation",
                "SACCO Savings Account"
            )
            return

        multiplier = settings.savings_multiplier or 3.0
        full_name = frappe.db.get_value("SACCO Member", self.member, "full_name") or self.member

        security = frappe.new_doc("Loan Security")
        security.loan_security_code = f"SACCO-SEC-{self.name}"
        security.loan_security_name = f"{full_name} - SACCO Savings"
        security.loan_security_type = settings.sacco_loan_security_type
        security.is_sacco_security = 1
        security.sacco_member_saving = self.name
        security.original_security_value = (self.balance or 0) * multiplier
        security.available_security_value = security.original_security_value  # utilized = 0 on creation
        security.insert(ignore_permissions=True)

        frappe.db.set_value(self.doctype, self.name, "loan_security", security.name)

    def sync_loan_security(self):
        """Sync original_security_value and available_security_value on Loan Security."""
        if not self.loan_security:
            self.loan_security = frappe.db.get_value(
                "Loan Security",
                {"sacco_member_saving": self.name},
                "name"
            )
        if not self.loan_security:
            return

        # Read multiplier from SACCO Settings — fallback to 3
        multiplier = frappe.db.get_single_value("SACCO Settings", "savings_multiplier") or 3.0

        original_value = (self.balance or 0) * multiplier

        # available = original - already utilized (don't touch utilized_security_value)
        utilized = frappe.db.get_value(
            "Loan Security", self.loan_security, "utilized_security_value"
        ) or 0

        available_value = original_value - utilized

        frappe.db.set_value(
            "Loan Security",
            self.loan_security,
            {
                "original_security_value": original_value,
                "available_security_value": available_value
            }
        )