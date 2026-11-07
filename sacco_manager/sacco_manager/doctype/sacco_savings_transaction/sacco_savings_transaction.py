# sacco_manager/sacco_manager/doctype/sacco_savings_transaction/sacco_savings_transaction.py
import frappe
from frappe.model.document import Document
import traceback

class SACCOSavingsTransaction(Document):

    def validate(self):
        try:
            if not self.savings_account:
                return
            acc = frappe.get_doc("SACCO Savings Account", self.savings_account)
            self.balance_before = acc.balance
            if self.transaction_type == "Withdrawal" and self.amount > acc.balance:
                frappe.throw("Insufficient balance. Available: {}".format(acc.balance))
        except Exception as e:
            frappe.log_error(f"Validation Error: {str(e)}\n{traceback.format_exc()}", "SACCO Transaction Error")
            frappe.throw(str(e))

    def on_submit(self):
        try:
            frappe.publish_realtime("console_log", {
                "message": f"Starting on_submit for {self.name}",
                "type": "info"
            })
            self.update_balance()
            self.post_gl()
            frappe.publish_realtime("console_log", {
                "message": f"Successfully submitted {self.name}",
                "type": "success"
            })
        except Exception as e:
            error_msg = str(e)
            frappe.log_error(f"Submit Error: {error_msg}\n{traceback.format_exc()}", "SACCO Transaction Error")
            frappe.publish_realtime("console_log", {
                "message": f"Error in on_submit: {error_msg}",
                "type": "error",
                "traceback": traceback.format_exc()
            })
            frappe.throw(error_msg)

    def on_cancel(self):
        try:
            self.reverse_balance()
            if self.get("journal_entry"):
                jv = frappe.get_doc("Journal Entry", self.journal_entry)
                jv.cancel()
        except Exception as e:
            frappe.log_error(f"Cancel Error: {str(e)}\n{traceback.format_exc()}", "SACCO Transaction Error")
            frappe.throw(str(e))

    def update_balance(self):
        try:
            # Lock the account row for the duration of this DB transaction so
            # concurrent deposits/withdrawals against the same account can't
            # race and overwrite each other's balance update.
            current_balance = frappe.db.get_value(
                "SACCO Savings Account", self.savings_account, "balance", for_update=True
            ) or 0

            if self.transaction_type in ("Deposit", "Interest"):
                new_bal = current_balance + self.amount
            else:
                new_bal = current_balance - self.amount
            self.balance_after = new_bal
            frappe.db.set_value("SACCO Savings Account", self.savings_account, "balance", new_bal)

            # Sync loan security collateral value
            acc = frappe.get_doc("SACCO Savings Account", self.savings_account)
            acc.balance = new_bal
            acc.sync_loan_security()

            frappe.publish_realtime("console_log", {
                "message": f"Balance updated: {current_balance} -> {new_bal}",
                "type": "debug"
            })
        except Exception as e:
            frappe.log_error(f"Update Balance Error: {str(e)}\n{traceback.format_exc()}", "SACCO Transaction Error")
            raise

    def reverse_balance(self):
        current_balance = frappe.db.get_value(
            "SACCO Savings Account", self.savings_account, "balance", for_update=True
        ) or 0
        if self.transaction_type in ("Deposit", "Interest"):
            new_bal = current_balance - self.amount
        else:
            new_bal = current_balance + self.amount
        frappe.db.set_value("SACCO Savings Account", self.savings_account, "balance", new_bal)

        # Sync loan security collateral value on cancel
        acc = frappe.get_doc("SACCO Savings Account", self.savings_account)
        acc.balance = new_bal
        acc.sync_loan_security()

    def post_gl(self):
        try:
            settings = frappe.get_single("SACCO Settings")
            if not settings.savings_liability_account:
                frappe.throw("Configure GL accounts in SACCO Settings first")

            jv = frappe.new_doc("Journal Entry")
            jv.posting_date = self.posting_date or frappe.utils.today()
            jv.voucher_type = "Journal Entry"
            jv.company = frappe.db.get_value("Account", settings.savings_liability_account, "company")
            jv.user_remark = f"{self.transaction_type} - {self.member}"

            if self.transaction_type == "Deposit":
                jv.append("accounts", {
                    "account": settings.savings_liability_account,
                    "credit_in_account_currency": self.amount,
                    "party_type": "SACCO Member",
                    "party": self.member
                })
                jv.append("accounts", {
                    "account": settings.cash_account,
                    "debit_in_account_currency": self.amount
                })

            elif self.transaction_type == "Interest":
                if not settings.interest_account:
                    frappe.throw("Configure Interest Expense account in SACCO Settings first")
                jv.append("accounts", {
                    "account": settings.savings_liability_account,
                    "credit_in_account_currency": self.amount,
                    "party_type": "SACCO Member",
                    "party": self.member
                })
                jv.append("accounts", {
                    "account": settings.interest_account,
                    "debit_in_account_currency": self.amount
                })

            elif self.transaction_type == "Withdrawal":
                jv.append("accounts", {
                    "account": settings.savings_liability_account,
                    "debit_in_account_currency": self.amount,
                    "party_type": "SACCO Member",
                    "party": self.member
                })
                jv.append("accounts", {
                    "account": settings.cash_account,
                    "credit_in_account_currency": self.amount
                })

            jv.insert(ignore_permissions=True)
            jv.submit()
            frappe.db.set_value(self.doctype, self.name, "journal_entry", jv.name)

        except Exception as e:
            frappe.publish_realtime("console_log", {
                "message": f"GL Posting Error: {str(e)}",
                "type": "error",
                "traceback": traceback.format_exc()
            })
            raise