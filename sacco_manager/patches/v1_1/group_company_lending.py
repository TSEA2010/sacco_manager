# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Set up lending to the group companies and tidy the ERPNext customer/supplier/lending setup.

Idempotent: creates only what is missing and changes an existing setting only when it still has
the known-wrong value. Safe to re-run (also exposed as sacco_manager.company_lending_setup.run).

1. Accounts: 1305 Trade Debtors, 1309 Interest Receivable - Group Companies, 1620 Loans to Group
   Companies, 2140 Interest in Suspense, 2141 Loan Security Deposits, 4130 Interest from Group Companies.
2. Company default receivable moves off "1310 Debtors" (the member loan principal account) to 1305,
   so an ordinary sales invoice can never land in the loan book.
3. Customer group "Group Companies" and the four group companies as Company customers.
4. Loan product "Group Company Loan" (GRPCO01) on the new accounts.
5. Member loan product: interest on non-performing loans goes to Interest in Suspense instead of
   income, and penalty waivers/suspense stop pointing at the penalty receivable account.
6. Supplier group "SACCO Service Providers" with the service providers from the Tally creditors.
"""

import frappe

COMPANIES = ["Astral Industries Limited", "Trident Plumbers Limited", "TVTC Limited", "Doctor Plumber Limited"]
SUPPLIERS = ["CPJ & Associates", "Dew CIS Solutions Limited", "Sunafrica Software Group Ltd", "Parkash Associates",
	"Henlee Enterprises", "Kikimbi Enterprises", "Ushirika Celebrations", "Diamond Property Merchants Kajiado"]


def execute():
	company = frappe.db.get_single_value("SACCO Settings", "company")
	if not company or not frappe.db.exists("Company", company):
		return
	acc = _accounts(company)
	if not acc:
		return
	_default_receivable(company, acc)
	_customers()
	_product(company, acc)
	_member_product_fixes(acc)
	_suppliers()
	frappe.db.set_single_value("SACCO Settings", "group_company_customer_group", "Group Companies")


def _find(company, number):
	return frappe.db.get_value("Account", {"company": company, "account_number": number}, "name")


def _account(company, number, name, parent_number, root_type, account_type=""):
	existing = _find(company, number)
	if existing:
		return existing
	parent = _find(company, parent_number)
	if not parent:
		return None
	doc = frappe.get_doc({"doctype": "Account", "company": company, "account_number": number, "account_name": name,
		"parent_account": parent, "root_type": root_type, "account_type": account_type, "is_group": 0})
	doc.flags.ignore_permissions = True
	doc.insert()
	return doc.name


def _accounts(company):
	a = {
		"trade_debtors": _account(company, "1305", "Trade Debtors", "1300", "Asset", "Receivable"),
		"grp_interest_recv": _account(company, "1309", "Interest Receivable - Group Companies", "1300", "Asset"),
		"grp_loans": _account(company, "1620", "Loans to Group Companies", "1600", "Asset", "Receivable"),
		"suspense": _account(company, "2140", "Interest in Suspense", "2100", "Liability"),
		"security": _account(company, "2141", "Loan Security Deposits", "2100", "Liability"),
		"grp_income": _account(company, "4130", "Interest from Group Companies", "4100", "Income"),
	}
	return a if all(a.values()) else None


def _default_receivable(company, acc):
	member_loan_accounts = set(frappe.get_all("Loan Product", filters={"company": company}, pluck="loan_account"))
	current = frappe.db.get_value("Company", company, "default_receivable_account")
	if current in member_loan_accounts:
		frappe.db.set_value("Company", company, "default_receivable_account", acc["trade_debtors"])


def _customers():
	if not frappe.db.exists("Customer Group", "Group Companies"):
		frappe.get_doc({"doctype": "Customer Group", "customer_group_name": "Group Companies",
			"parent_customer_group": "All Customer Groups", "is_group": 0}).insert(ignore_permissions=True)
	territory = frappe.db.get_value("Territory", {"is_group": 0}, "name") or "All Territories"
	for name in COMPANIES:
		if frappe.db.exists("Customer", name) or frappe.db.exists("Customer", {"customer_name": name}):
			continue
		frappe.get_doc({"doctype": "Customer", "customer_name": name, "customer_type": "Company",
			"customer_group": "Group Companies", "territory": territory}).insert(ignore_permissions=True)


def _product(company, acc):
	if frappe.db.exists("Loan Product", "GRPCO01"):
		return
	base = frappe.get_all("Loan Product", filters={"company": company}, fields=["*"], order_by="creation", limit=1)
	if not base:
		return
	b = base[0]
	keep = ("rate_of_interest", "bpi_recovery_method", "bpi_treatment", "cyclic_day_of_the_month", "repayment_date_on",
		"repayment_schedule_type", "days_past_due_threshold_for_npa", "is_term_loan", "collection_offset_sequence_for_standard_asset",
		"collection_offset_sequence_for_sub_standard_asset", "collection_offset_sequence_for_written_off_asset",
		"collection_offset_sequence_for_settlement_collection", "disbursement_account", "payment_account", "customer_refund_account",
		"penalty_income_account", "penalty_accrued_account", "penalty_receivable_account", "write_off_account",
		"write_off_recovery_account", "excess_amount_acceptance_limit")
	doc = frappe.get_doc({
		"doctype": "Loan Product", "product_code": "GRPCO01", "product_name": "Group Company Loan", "company": company,
		**{k: b.get(k) for k in keep if b.get(k) not in (None, "")},
		"loan_account": acc["grp_loans"],
		"security_deposit_account": acc["security"],
		"interest_income_account": acc["grp_income"],
		"interest_accrued_account": acc["grp_interest_recv"],
		"interest_receivable_account": acc["grp_interest_recv"],
		"interest_waiver_account": acc["grp_income"],
		"suspense_interest_income": acc["suspense"],
		"broken_period_interest_recovery_account": acc["grp_income"],
		"penalty_waiver_account": b.get("penalty_income_account"),
		"penalty_suspense_account": acc["suspense"],
		"same_as_regular_interest_accounts": 1,
	})
	doc.flags.ignore_permissions = True
	doc.flags.ignore_mandatory = True
	doc.insert()


def _member_product_fixes(acc):
	for p in frappe.get_all("Loan Product", filters={"name": ["!=", "GRPCO01"]},
			fields=["name", "interest_income_account", "suspense_interest_income", "penalty_income_account",
				"penalty_receivable_account", "penalty_waiver_account", "penalty_suspense_account"]):
		upd = {}
		if p.suspense_interest_income and p.suspense_interest_income == p.interest_income_account:
			upd["suspense_interest_income"] = acc["suspense"]
		if p.penalty_suspense_account and p.penalty_suspense_account == p.penalty_receivable_account:
			upd["penalty_suspense_account"] = acc["suspense"]
		if p.penalty_waiver_account and p.penalty_waiver_account == p.penalty_receivable_account and p.penalty_income_account:
			upd["penalty_waiver_account"] = p.penalty_income_account
		if upd:
			frappe.db.set_value("Loan Product", p.name, upd)


def _suppliers():
	if not frappe.db.exists("Supplier Group", "SACCO Service Providers"):
		frappe.get_doc({"doctype": "Supplier Group", "supplier_group_name": "SACCO Service Providers",
			"parent_supplier_group": "All Supplier Groups", "is_group": 0}).insert(ignore_permissions=True)
	for name in SUPPLIERS:
		if frappe.db.exists("Supplier", name) or frappe.db.exists("Supplier", {"supplier_name": name}):
			continue
		frappe.get_doc({"doctype": "Supplier", "supplier_name": name, "supplier_type": "Company",
			"supplier_group": "SACCO Service Providers"}).insert(ignore_permissions=True)
