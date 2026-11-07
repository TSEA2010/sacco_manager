import frappe
from frappe import _

def get_member_for_user():
    user = frappe.session.user
    if user == "Guest":
        frappe.throw(_("Not logged in"), frappe.AuthenticationError)
    member = frappe.db.get_value(
        "SACCO Member", {"user": user}, ["name", "member_name", "branch", "status"], as_dict=True
    )
    if not member:
        frappe.throw(_("No SACCO Member record linked to your account."))
    return member


@frappe.whitelist()
def get_portal_summary():
    member = get_member_for_user()

    # Savings balance
    savings = frappe.db.get_value(
        "Savings Account",
        {"sacco_member": member.name, "status": "Active"},
        ["name", "current_balance"],
        as_dict=True,
    ) or {}

    # Share total
    share_total = frappe.db.sql("""
        SELECT COALESCE(SUM(shares_quantity),0) as total_shares
        FROM `tabShare Ledger Entry`
        WHERE sacco_member=%s AND docstatus=1
    """, member.name, as_dict=True)[0].total_shares or 0

    share_value_per = frappe.db.get_single_value("SACCO Settings", "share_nominal_value") or 100

    # Active loans (via Customer linked to member)
    customer = frappe.db.get_value("SACCO Member", member.name, "customer")
    active_loans = []
    if customer:
        active_loans = frappe.get_all(
            "Loan",
            filters={"applicant": customer, "status": ["in", ["Disbursed", "Active", "Partially Disbursed"]], "docstatus": 1},
            fields=["name", "loan_type", "loan_amount", "total_amount_paid", "outstanding_amount", "repayment_start_date", "repayment_periods", "rate_of_interest", "status"],
        )

    balance = savings.get("current_balance", 0)
    multiplier = frappe.db.get_single_value("SACCO Settings", "loan_multiplier") or 3

    return {
        "member": member,
        "savings": savings,
        "savings_balance": balance,
        "total_shares": int(share_total),
        "share_value": int(share_total) * share_value_per,
        "active_loans": active_loans,
        "outstanding_loans": sum(l.outstanding_amount or 0 for l in active_loans),
        "loan_limit": balance * multiplier,
    }


@frappe.whitelist()
def get_savings_transactions(limit=50, offset=0):
    member = get_member_for_user()
    savings_account = frappe.db.get_value(
        "Savings Account", {"sacco_member": member.name}, "name"
    )
    if not savings_account:
        return []
    return frappe.get_all(
        "Savings Transaction",
        filters={"savings_account": savings_account, "docstatus": 1},
        fields=["name", "transaction_date", "transaction_type", "amount", "running_balance", "reference_doctype", "reference_name", "remarks"],
        order_by="transaction_date desc, creation desc",
        limit=int(limit),
        start=int(offset),
    )


@frappe.whitelist()
def get_share_ledger(limit=50, offset=0):
    member = get_member_for_user()
    return frappe.get_all(
        "Share Ledger Entry",
        filters={"sacco_member": member.name, "docstatus": 1},
        fields=["name", "posting_date", "entry_type", "shares_quantity", "running_total", "remarks"],
        order_by="posting_date desc, creation desc",
        limit=int(limit),
        start=int(offset),
    )


@frappe.whitelist()
def get_loan_schedule(loan_name):
    member = get_member_for_user()
    customer = frappe.db.get_value("SACCO Member", member.name, "customer")
    # Verify the loan belongs to this member
    loan_applicant = frappe.db.get_value("Loan", loan_name, "applicant")
    if loan_applicant != customer:
        frappe.throw(_("Access denied"))
    return frappe.get_all(
        "Loan Repayment Schedule",
        filters={"parent": loan_name},
        fields=["payment_date", "principal_amount", "interest_amount", "total_payment", "balance_loan_amount", "paid_amount", "status"],
        order_by="payment_date asc",
    )


@frappe.whitelist()
def submit_loan_application(loan_type, loan_amount, repayment_periods, purpose, description=""):
    member = get_member_for_user()
    customer = frappe.db.get_value("SACCO Member", member.name, "customer")
    if not customer:
        frappe.throw(_("No Customer record linked to your SACCO Member account."))

    # Validate against limit
    balance = frappe.db.get_value(
        "Savings Account", {"sacco_member": member.name}, "current_balance"
    ) or 0
    multiplier = frappe.db.get_single_value("SACCO Settings", "loan_multiplier") or 3
    loan_limit = balance * multiplier

    if float(loan_amount) > loan_limit:
        frappe.throw(_(f"Loan amount exceeds your limit of KES {loan_limit:,.0f}"))

    # Get interest rate from Loan Type
    rate = frappe.db.get_value("Loan Type", loan_type, "rate_of_interest") or 0
    company = frappe.db.get_single_value("SACCO Settings", "company")

    loan = frappe.get_doc({
        "doctype": "Loan",
        "loan_type": loan_type,
        "applicant_type": "Customer",
        "applicant": customer,
        "loan_amount": float(loan_amount),
        "repayment_method": "Repay Fixed Amount per Period",
        "repayment_periods": int(repayment_periods),
        "rate_of_interest": rate,
        "description": description,
        "company": company,
        "status": "Draft",
    })
    loan.insert(ignore_permissions=False)
    return {"loan": loan.name, "message": "Loan application submitted successfully."}