# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Revolving facility product for the group companies, and one SACCO Employer per group company.

GRPFAC01 "Group Company Facility" is a non-term Lending loan with a limit: the company draws down
(cash or kept deductions) and repays at any time; Lending accrues interest monthly on the balance.
It copies the accounts of GRPCO01 (1620 loans, 1309 interest receivable, 4130 income).
"""

import frappe

EMPLOYERS = {  # branch on SACCO Member -> group company customer
	"Trident": ("Trident Plumbers", "Trident Plumbers Limited", "Trident Sacco"),
	"Astral": ("Astral Industries", "Astral Industries Limited", "Triddent Sacco"),
	"DR. Plumber": ("Doctor Plumber", "Doctor Plumber Limited", None),
	"TVTC": ("TVTC", "TVTC Limited", None),
}


def execute():
	_product()
	_employers()


def _product():
	if frappe.db.exists("Loan Product", "GRPFAC01") or not frappe.db.exists("Loan Product", "GRPCO01"):
		return
	p = frappe.copy_doc(frappe.get_doc("Loan Product", "GRPCO01"))
	p.product_code = "GRPFAC01"
	p.product_name = "Group Company Facility"
	p.is_term_loan = 0
	p.flags.ignore_permissions = True
	p.flags.ignore_mandatory = True
	p.insert()


def _employers():
	for branch, (title, customer, component) in EMPLOYERS.items():
		if not frappe.db.exists("Branch", branch) or frappe.db.exists("SACCO Employer", {"branch": branch}):
			continue
		doc = frappe.get_doc({
			"doctype": "SACCO Employer", "employer_name": title, "branch": branch, "enabled": 1,
			"customer": customer if frappe.db.exists("Customer", customer) else None,
			"remittance_settlement": "Paid to the SACCO in cash",
			"salary_component": component or "Trident Sacco",
		})
		doc.flags.ignore_permissions = True
		doc.insert()
