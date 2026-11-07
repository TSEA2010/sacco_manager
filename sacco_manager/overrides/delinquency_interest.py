# sacco_manager/overrides/delinquency_interest.py
import frappe
from frappe.utils import add_days, today, get_first_day, getdate

def apply_monthly_delinquency_interest():
    loans = frappe.get_all(
        "Loan",
        filters={"status": "Disbursed", "is_term_loan": 1},
        fields=[
            "name", "loan_amount", "loan_product",
            "repayment_start_date", "disbursement_date"
        ]
    )
    for loan in loans:
        process_single_loan(loan)

def process_single_loan(loan):
    loan_name = loan.name
    due_date = loan.repayment_start_date

    if not due_date or not loan.loan_product:
        return

    product = frappe.db.get_value(
        "Loan Product",
        loan.loan_product,
        ["rate_of_interest", "grace_period_in_days"],
        as_dict=True
    )
    if not product or not product.rate_of_interest:
        frappe.log_error(
            f"Loan Product {loan.loan_product} missing rate_of_interest",
            "Delinquency Interest"
        )
        return

    grace_days = product.grace_period_in_days or 5
    overdue_start = getdate(add_days(due_date, grace_days))

    if getdate(today()) <= overdue_start:
        return

    monthly_rate = product.rate_of_interest / 12
    interest_amount = loan.loan_amount * (monthly_rate / 100)

    posting_month = get_first_day(today())

    # Idempotency — demand_date is the overdue_start date
    if frappe.db.exists("Loan Demand", {
        "loan": loan_name,
        "demand_type": "Additional Interest",
        "demand_subtype": "Interest",
        "demand_date": overdue_start
    }):
        return

    # Fetch linked disbursement
    disbursement = frappe.db.get_value(
        "Loan Disbursement",
        {"against_loan": loan_name, "docstatus": 1},
        ["name", "disbursement_date"],
        as_dict=True
    )

    doc = frappe.get_doc({
        "doctype": "Loan Demand",
        "loan": loan_name,
        "demand_type": "Additional Interest",
        "demand_subtype": "Interest",
        "demand_date": overdue_start,
        "posting_date": today(),
        "demand_amount": interest_amount,
        "loan_disbursement": disbursement.name if disbursement else None,
        "disbursement_date": disbursement.disbursement_date if disbursement else None,
        "description": (
            f"Monthly delinquency interest: {loan.loan_amount} "
            f"× {monthly_rate}% (annual {product.rate_of_interest}% ÷ 12)"
        )
    })
    doc.insert(ignore_permissions=True)
    doc.submit()
    frappe.db.commit()