# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import flt, add_months
import math


class LoanTopUp(Document):

    def validate(self):
        self.fetch_outstanding()
        self.new_principal = flt(self.outstanding_principal) + flt(self.top_up_amount)

        if self.repayment_method == "Repay Over Number of Periods":
            if not self.new_tenure_months:
                frappe.throw("Please set New Tenure (months)")
            self.new_installment_amount = self.calculate_emi(
                self.new_principal,
                self.new_interest_rate,
                self.new_tenure_months
            )

        elif self.repayment_method == "Repay Fixed Amount per Period":
            # Accept from either field — new_monthly_repayment_amount if added, else new_installment_amount
            fixed_amount = flt(self.get("new_monthly_repayment_amount")) or flt(self.new_installment_amount)
            if not fixed_amount:
                frappe.throw("Please set New Monthly Repayment Amount")
            self.new_installment_amount = fixed_amount
            self.new_tenure_months = self.calculate_tenure(
                self.new_principal,
                self.new_interest_rate,
                self.new_installment_amount
            )

    def fetch_outstanding(self):
        loan = frappe.get_doc("Loan", self.loan)
        self.outstanding_principal = flt(loan.disbursed_amount) - flt(loan.total_principal_paid)

    def calculate_emi(self, principal, annual_rate, months):
        if not months:
            return 0
        r = (flt(annual_rate) / 100) / 12
        if r == 0:
            return round(flt(principal) / months, 2)
        emi = principal * r * math.pow(1 + r, months) / (math.pow(1 + r, months) - 1)
        return round(emi, 2)

    def calculate_tenure(self, principal, annual_rate, emi):
        r = (flt(annual_rate) / 100) / 12
        if r == 0:
            return math.ceil(flt(principal) / flt(emi))
        # n = -log(1 - P*r/EMI) / log(1+r)
        val = 1 - (flt(principal) * r / flt(emi))
        if val <= 0:
            frappe.throw("Monthly repayment amount is too low to cover interest. Minimum is KES {:,.2f}".format(
                round(flt(principal) * r + 1, 2)
            ))
        n = -math.log(val) / math.log(1 + r)
        return math.ceil(n)

    def on_submit(self):
        try:
            self.cancel_future_schedule()
            self.increase_loan_amount_for_disbursement()  # Step 1: Increase loan_amount first
            self.post_disbursement_gl()                   # Step 2: Create disbursement (sees available amount)
            self.update_loan_fields()                     # Step 3: Update remaining loan fields
            self.regenerate_schedule()                    # Step 4: Generate new repayment schedule
            frappe.db.set_value("Loan Top-Up", self.name, "status", "Submitted")
            frappe.db.commit()
            frappe.msgprint("Loan Top-Up submitted successfully", indicator="green", alert=True)
        except Exception as e:
            frappe.db.rollback()
            frappe.log_error(f"Loan Top-Up {self.name} submission failed: {str(e)}", "Loan Top-Up Error")
            frappe.throw(f"Failed to submit Loan Top-Up: {str(e)}")

    def on_cancel(self):
        frappe.db.set_value("Loan Top-Up", self.name, "status", "Cancelled")

    def increase_loan_amount_for_disbursement(self):
        """Temporarily increase loan_amount so disbursement validation passes"""
        loan = frappe.get_doc("Loan", self.loan)
        new_total = flt(loan.loan_amount) + flt(self.top_up_amount)
        frappe.db.set_value("Loan", self.loan, "loan_amount", new_total)

    def update_loan_fields(self):
        loan = frappe.get_doc("Loan", self.loan)
        frappe.db.set_value("Loan", self.loan, {
            "loan_amount": self.new_principal,
            "disbursed_amount": flt(loan.disbursed_amount) + flt(self.top_up_amount),
            "monthly_repayment_amount": self.new_installment_amount,
            "repayment_periods": self.new_tenure_months,
            "repayment_method": self.repayment_method
        })

    def cancel_future_schedule(self):
        existing = frappe.db.get_value(
            "Loan Repayment Schedule",
            {"loan": self.loan, "status": "Active"},
            "name"
        )
        if existing:
            frappe.db.set_value("Loan Repayment Schedule", existing, "status", "Outdated")

    def regenerate_schedule(self):
        loan = frappe.get_doc("Loan", self.loan)

        lrs = frappe.get_doc({
            "doctype": "Loan Repayment Schedule",
            "loan": self.loan,
            "loan_amount": self.new_principal,
            "disbursed_amount": flt(loan.disbursed_amount) + flt(self.top_up_amount),
            "repayment_start_date": self.top_up_date,
            "repayment_method": self.repayment_method,
            "repayment_periods": self.new_tenure_months,
            "monthly_repayment_amount": self.new_installment_amount,
            "rate_of_interest": self.new_interest_rate,
            "repayment_frequency": loan.repayment_frequency,
            "company": loan.company,
            "status": "Active"
        })
        lrs.insert(ignore_permissions=True)
        lrs.submit()

        # Lending app clears schedule on submit — insert rows directly after
        self.insert_schedule_rows(lrs.name)

    def insert_schedule_rows(self, lrs_name):
        r = (flt(self.new_interest_rate) / 100) / 12
        balance = flt(self.new_principal)
        emi = flt(self.new_installment_amount)
        months = int(self.new_tenure_months)
        idx = 1

        for i in range(months):
            payment_date = add_months(self.top_up_date, i)
            interest = round(balance * r, 2) if r else 0
            principal_component = round(emi - interest, 2)

            if i == months - 1:
                principal_component = balance
                row_emi = round(principal_component + interest, 2)
            else:
                row_emi = emi

            balance = max(round(balance - principal_component, 2), 0)

            frappe.db.sql("""
                INSERT INTO `tabRepayment Schedule`
                    (name, creation, modified, modified_by, owner, docstatus,
                     idx, parent, parentfield, parenttype,
                     payment_date, principal_amount, interest_amount,
                     total_payment, balance_loan_amount,
                     number_of_days, demand_generated, charges)
                VALUES
                    (%s, NOW(), NOW(), %s, %s, 1,
                     %s, %s, 'repayment_schedule', 'Loan Repayment Schedule',
                     %s, %s, %s, %s, %s, 30, 0, 0)
            """, (
                frappe.generate_hash(length=10),
                frappe.session.user,
                frappe.session.user,
                idx,
                lrs_name,
                payment_date,
                principal_component,
                interest,
                row_emi,
                balance
            ))
            idx += 1

        frappe.db.commit()

    def post_disbursement_gl(self):
        """Create loan disbursement record against the loan - GL posting handled by Loan Disbursement doctype"""
        try:
            loan = frappe.get_doc("Loan", self.loan)
            
            # Get disbursement account
            disbursement_account = self.get("disbursement_account") or loan.get("disbursement_account")
            
            if not disbursement_account:
                frappe.throw("Disbursement Account is missing. Please set it in Loan Top-Up or Loan document.")
            
            # Create loan disbursement document (loan_amount already increased by increase_loan_amount_for_disbursement)
            disbursement = frappe.get_doc({
                "doctype": "Loan Disbursement",
                "against_loan": self.loan,
                "applicant_type": loan.applicant_type,
                "applicant": loan.applicant,
                "company": loan.company,
                "disbursement_date": self.top_up_date,
                "disbursed_amount": self.top_up_amount,
                "disbursement_account": disbursement_account,
                "loan_account": loan.loan_account,
                "cost_center": loan.get("cost_center"),
                "remarks": f"Top-up disbursement for loan {self.loan} - {self.name}"
            })
            
            # Insert and submit the disbursement
            disbursement.insert(ignore_permissions=True)
            disbursement.submit()
            
            # Store reference
            frappe.db.set_value("Loan Top-Up", self.name, "loan_disbursement", disbursement.name)
            
            frappe.msgprint(
                f"Loan Disbursement {disbursement.name} created successfully for amount {self.top_up_amount}",
                alert=True
            )
            
        except Exception as e:
            frappe.log_error(f"Failed to create loan disbursement: {str(e)}", "Loan Disbursement Creation Error")
            frappe.throw(f"Failed to create loan disbursement: {str(e)}")