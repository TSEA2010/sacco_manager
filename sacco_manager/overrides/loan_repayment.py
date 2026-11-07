import frappe
from lending.loan_management.doctype.loan_repayment.loan_repayment import LoanRepayment


class CustomLoanRepayment(LoanRepayment):

    def set_missing_values(self, amounts=None):
        super().set_missing_values(amounts)
        self._fix_due_date()

    def validate(self):
        super().validate()
        self._fix_due_date()

    def _fix_due_date(self):
        if not self.against_loan:
            return

        loan = frappe.db.get_value(
            "Loan",
            self.against_loan,
            ["disbursement_date", "repayment_start_date"],
            as_dict=True,
        )
        if not loan:
            return

        if (
            self.due_date
            and loan.disbursement_date
            and str(self.due_date) == str(loan.disbursement_date)
            and loan.repayment_start_date
            and str(loan.repayment_start_date) != str(loan.disbursement_date)
        ):
            self.due_date = loan.repayment_start_date
