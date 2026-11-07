# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
import frappe
from frappe.model.document import Document
from frappe import _
from frappe.utils import nowdate, flt


class SACCORegistrationFee(Document):

    def validate(self):
        # Block duplicate submitted fee for same member
        existing = frappe.db.exists("SACCO Registration Fee", {
            "member": self.member,
            "docstatus": 1
        })
        if existing and existing != self.name and not self.amended_from:
            frappe.throw(_(f"A Registration Fee payment already exists for this member: {existing}"))

        # Validate payment_account is Cash or Bank, not an income account
        if self.payment_account:
            acc_type = frappe.db.get_value("Account", self.payment_account, "account_type")
            if acc_type not in ("Cash", "Bank"):
                frappe.throw(_("Payment Account must be a Cash or Bank account, not an Income account."))

    def before_insert(self):
        # Block if member already active
        status = frappe.db.get_value("SACCO Member", self.member, "status")
        if status == "Active":
            frappe.throw(_(f"Member {self.member} is already Active."))

        # Pull fee amount from settings if not set
        if not flt(self.amount):
            settings = frappe.get_single("SACCO Settings")
            self.amount = flt(settings.registration_fee_amount)

    def on_submit(self):
        self._post_je()
        frappe.db.set_value("SACCO Member", self.member, "status", "Active")
        frappe.msgprint(
            _(f"Member <b>{self.member_name}</b> is now <b>Active</b>. JE: {self.journal_entry}"),
            indicator="green", alert=True
        )

    def on_cancel(self):
        self._cancel_je()
        frappe.db.set_value("SACCO Member", self.member, "status", "Inactive")
        frappe.msgprint(
            _(f"Registration Fee reversed. Member <b>{self.member_name}</b> set to Inactive."),
            indicator="orange", alert=True
        )

    def _post_je(self):
        settings = frappe.get_single("SACCO Settings")
        fee = flt(self.amount)

        income_account = settings.registration_fee_income_account
        if not income_account:
            frappe.throw(_("Set Registration Fee Income Account in SACCO Settings."))

        # Always use settings.cash_account as fallback; payment_account already validated as Cash/Bank
        cash_account = self.payment_account or settings.cash_account
        if not cash_account:
            frappe.throw(_("Set a Cash/Bank Account in SACCO Settings or on this document."))

        company = frappe.db.get_value("Account", income_account, "company")
        cost_center = frappe.db.get_value("Company", company, "cost_center")

        je = frappe.new_doc("Journal Entry")
        je.voucher_type = "Journal Entry"
        je.company = company
        je.posting_date = self.payment_date or nowdate()
        je.user_remark = f"Registration Fee - {self.member_name} ({self.member})"

        # Debit Cash/Bank
        je.append("accounts", {
            "account": cash_account,
            "debit_in_account_currency": fee,
            "credit_in_account_currency": 0,
            "cost_center": cost_center,
        })

        # Credit Income
        je.append("accounts", {
            "account": income_account,
            "debit_in_account_currency": 0,
            "credit_in_account_currency": fee,
            "cost_center": cost_center,
        })

        je.insert(ignore_permissions=True)
        je.submit()
        self.db_set("journal_entry", je.name)

    def _cancel_je(self):
        if not self.journal_entry:
            return
        je = frappe.get_doc("Journal Entry", self.journal_entry)
        if je.docstatus == 1:
            je.cancel()