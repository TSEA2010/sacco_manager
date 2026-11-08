# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Loans from the SACCO to the group companies (Astral, Trident Plumbers, TVTC, Doctor Plumber).

The SACCO lends surplus member funds to the sponsor companies to earn interest for members.
They are Customers in the "Group Companies" customer group and borrow on the "Group Company Loan"
product, which has its own ledger accounts so the exposure and its income are visible separately:

    1620 Loans to Group Companies          (principal)
    1309 Interest Receivable - Group Cos   (accrued / demanded interest)
    4130 Interest from Group Companies     (income)

Controls: every group-company loan needs a submitted SACCO Board Approval (related-party lending),
and SACCO Settings can cap total group-company loans as a % of members' savings."""

import frappe
from frappe import _
from frappe.utils import flt

GROUP = "Group Companies"
OPEN_LOAN = ("Disbursed", "Partially Disbursed", "Active", "Sanctioned")


def group_name():
	return frappe.db.get_single_value("SACCO Settings", "group_company_customer_group") or GROUP


def is_group_company(customer):
	return bool(customer) and frappe.db.get_value("Customer", customer, "customer_group") == group_name()


def exposure(exclude_application=None):
	"""Principal currently lent to group companies plus approved-but-undisbursed applications."""
	group = group_name()
	lent = flt(frappe.db.sql("""SELECT SUM(GREATEST(l.disbursed_amount - l.total_principal_paid, 0)) FROM `tabLoan` l
		JOIN `tabCustomer` c ON c.name = l.applicant WHERE l.docstatus = 1 AND l.applicant_type = 'Customer'
		AND c.customer_group = %s AND l.status IN ('Disbursed', 'Partially Disbursed', 'Active')""", group)[0][0])
	pipeline = flt(frappe.db.sql("""SELECT SUM(a.loan_amount) FROM `tabLoan Application` a JOIN `tabCustomer` c ON c.name = a.applicant
		WHERE a.docstatus < 2 AND a.status = 'Approved' AND a.applicant_type = 'Customer' AND c.customer_group = %s AND a.name != %s
		AND NOT EXISTS (SELECT 1 FROM `tabLoan` l WHERE l.loan_application = a.name AND l.docstatus = 1)""",
		(group, exclude_application or ""))[0][0])
	return lent + pipeline


def members_savings():
	return flt(frappe.db.sql("""SELECT SUM(CASE WHEN transaction_type IN ('Withdrawal', 'Withholding Tax') THEN -amount ELSE amount END)
		FROM `tabSACCO Savings Transaction` WHERE docstatus = 1""")[0][0])


def check_application(doc):
	"""Called when a Loan Application is approved."""
	if doc.get("applicant_type") != "Customer" or not is_group_company(doc.applicant):
		return
	from sacco_manager.sacco_manager.doctype.sacco_board_approval.sacco_board_approval import has_board_approval

	if not has_board_approval("Loan Application", doc.name):
		frappe.throw(_("{0} is a group company: loans to the sponsor companies are related-party lending and need a submitted "
			"SACCO Board Approval (decision Approved) for {1} before approval.").format(doc.applicant, doc.name),
			title=_("Board approval required"))
	limit = flt(frappe.db.get_single_value("SACCO Settings", "group_company_exposure_limit"))
	if limit > 0:
		savings = members_savings()
		after = exposure(exclude_application=doc.name) + flt(doc.loan_amount)
		cap = savings * limit / 100
		if after > cap + 0.5:
			fmt = lambda v: frappe.format_value(v, {"fieldtype": "Currency"})  # noqa: E731
			frappe.throw(_("This loan would take lending to group companies to {0}, above the limit of {1}% of members' savings "
				"({2}). Reduce the amount or have the Board change the limit in SACCO Settings.").format(fmt(after), limit, fmt(cap)),
				title=_("Group company exposure limit"))


def apply_disbursement_account(doc, method=None):
	"""Loan Disbursement validate hook. Lending always copies the loan's disbursement (bank) account;
	a drawdown created from a remittance the company kept must instead credit Employer Remittance
	Clearing. The remittance sets sacco_disbursement_account; nothing else changes."""
	if doc.get("sacco_disbursement_account"):
		doc.disbursement_account = doc.sacco_disbursement_account
