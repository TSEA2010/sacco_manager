# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Thin wrappers over Frappe Lending so SACCO flows (M-Pesa, check-off, top-ups) create
standard Lending documents instead of writing to Lending tables directly."""

import frappe
from frappe import _
from frappe.utils import flt, getdate, today

ACTIVE_LOAN_STATUSES = ("Disbursed", "Partially Disbursed", "Active")


def member_customer(member):
	customer = frappe.db.get_value("SACCO Member", member, "customer")
	if not customer:
		frappe.throw(_("Member {0} has no linked Customer (loan applicant).").format(member))
	return customer


def get_active_loans(member):
	customer = frappe.db.get_value("SACCO Member", member, "customer")
	if not customer:
		return []
	return frappe.get_all(
		"Loan",
		filters={"applicant_type": "Customer", "applicant": customer, "docstatus": 1, "status": ["in", ACTIVE_LOAN_STATUSES]},
		fields=["name", "loan_product", "loan_amount", "disbursed_amount", "total_principal_paid", "total_amount_paid",
			"repayment_start_date", "repayment_periods", "rate_of_interest", "status", "monthly_repayment_amount",
			"days_past_due", "disbursement_date"],
		order_by="disbursement_date asc, creation asc",
	)


def loan_outstanding(loan, posting_date=None, payment_type=None):
	"""Payable amount (principal + interest + penalty) per Lending, falling back to principal.
	payment_type="Loan Closure" returns the full payoff (includes unaccrued interest)."""
	posting_date = getdate(posting_date or today())
	try:
		from lending.loan_management.doctype.loan_repayment.loan_repayment import calculate_amounts

		amounts = calculate_amounts(loan, posting_date, payment_type=payment_type)
		payable = flt(amounts.get("payable_amount")) or (
			flt(amounts.get("pending_principal_amount")) + flt(amounts.get("interest_amount")) + flt(amounts.get("penalty_amount"))
		)
		return {
			"payable": payable,
			"principal": flt(amounts.get("pending_principal_amount")),
			"interest": flt(amounts.get("interest_amount")) + flt(amounts.get("unaccrued_interest")),
			"penalty": flt(amounts.get("penalty_amount")),
		}
	except Exception:
		frappe.log_error(title="sacco_manager: calculate_amounts failed", message=frappe.get_traceback())
		d = frappe.db.get_value("Loan", loan, ["disbursed_amount", "total_principal_paid"], as_dict=True)
		p = flt(d.disbursed_amount) - flt(d.total_principal_paid)
		return {"payable": p, "principal": p, "interest": 0, "penalty": 0}


def create_loan_repayment(loan, amount, posting_date, payment_account, reference_number=None, remarks=None,
		mode_of_payment=None, submit=True, repayment_type="Normal Repayment"):
	"""Normal repayment against a disbursed loan, paid into `payment_account`."""
	if flt(amount) <= 0:
		frappe.throw(_("Repayment amount must be greater than zero."))
	ln = frappe.db.get_value("Loan", loan, ["applicant_type", "applicant", "company", "docstatus", "status"], as_dict=True)
	if not ln or ln.docstatus != 1:
		frappe.throw(_("Loan {0} is not submitted.").format(loan))
	rp = frappe.new_doc("Loan Repayment")
	rp.against_loan = loan
	rp.applicant_type = ln.applicant_type
	rp.applicant = ln.applicant
	rp.company = ln.company
	rp.posting_date = getdate(posting_date or today())
	if rp.meta.has_field("value_date"):
		rp.value_date = rp.posting_date
	rp.amount_paid = flt(amount)
	rp.repayment_type = repayment_type
	if repayment_type == "Loan Closure":
		# use Lending's own payoff (it accrues interest up to the value date at save time)
		from lending.loan_management.doctype.loan_repayment.loan_repayment import calculate_amounts

		payoff = flt(calculate_amounts(loan, rp.value_date or rp.posting_date, payment_type="Loan Closure",
			for_update=True).get("payable_amount"))
		# Lending compares unrounded floats (e.g. 487436.57000000007), so round the payoff UP to the cent
		cents = round(payoff, 2)
		if cents < payoff:
			limit, write_off = frappe.db.get_value("Loan Product", frappe.db.get_value("Loan", loan, "loan_product"),
				["excess_amount_acceptance_limit", "write_off_amount"])
			if flt(write_off) >= 0.01:
				pass  # Lending accepts payable - auto write-off, so the exact cents close the loan
			elif flt(limit) >= 0.01:
				cents = round(cents + 0.01, 2)
			else:
				frappe.throw(_(
					"Set a small 'Excess Amount Acceptance Limit' (e.g. 10) or 'Auto Write Off Amount' on Loan Product {0} "
					"so Lending can close loans paid off to the cent."
				).format(frappe.db.get_value("Loan", loan, "loan_product")), title=_("Loan Product setting needed"))
		rp.amount_paid = max(flt(amount), cents)
	rp.reference_number = (reference_number or "")[:140] or None
	rp.reference_date = rp.posting_date
	if rp.meta.has_field("manual_remarks"):
		rp.manual_remarks = remarks
	if mode_of_payment and frappe.db.exists("Mode of Payment", mode_of_payment):
		rp.mode_of_payment = mode_of_payment
	rp.payment_account = payment_account
	rp.flags.ignore_permissions = True
	rp.insert()
	if payment_account and rp.payment_account != payment_account:
		rp.payment_account = payment_account
		rp.save()
	if submit:
		rp.submit()
	return rp
