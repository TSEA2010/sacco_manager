# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""A group company whose staff are SACCO members.

It plays two roles:
* Employer: deducts members' savings and loan instalments from salaries (check-off). With the ERP
  connection the SACCO sends each month's deductions to the company's ERPNext payroll and reads back
  what was actually deducted (see sacco_manager.employer_erp).
* Borrower: the SACCO lends its funds, apart from a liquid float, back to the company on a revolving
  facility (a Lending loan on product GRPFAC01). Deductions the company keeps instead of paying in
  become drawdowns on that facility, so the company pays interest on them.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_years, flt, getdate, today

FACILITY_PRODUCT = "GRPFAC01"
KEEP = "Kept by the company as a facility drawdown"


class SACCOEmployer(Document):
	def validate(self):
		if self.customer:
			from sacco_manager.company_lending import is_group_company

			if not is_group_company(self.customer):
				frappe.throw(_("{0} is not in the Group Companies customer group.").format(self.customer))
		if self.remittance_settlement == KEEP and not self.facility_loan and not self.is_new():
			frappe.msgprint(_("Create the facility before deductions can be kept by the company."), indicator="orange")
		if self.erp_url:
			self.erp_url = self.erp_url.strip().rstrip("/")
			if not self.erp_url.startswith("https://"):
				frappe.throw(_("The employer ERPNext URL must start with https://"))

	def facility_status(self):
		if not self.facility_loan:
			return {}
		ln = frappe.db.get_value("Loan", self.facility_loan, ["status", "disbursed_amount", "total_principal_paid",
			"maximum_limit_amount", "available_limit_amount", "rate_of_interest"], as_dict=True) or {}
		ln["outstanding"] = max(0, flt(ln.get("disbursed_amount")) - flt(ln.get("total_principal_paid")))
		return ln


@frappe.whitelist()
def create_facility(employer):
	"""Open the revolving facility in Lending. Needs a submitted SACCO Board Approval for this employer."""
	frappe.only_for(("System Manager", "SACCO Administrator", "SACCO Manager"))
	doc = frappe.get_doc("SACCO Employer", employer)
	if doc.facility_loan and frappe.db.get_value("Loan", doc.facility_loan, "docstatus") == 1:
		frappe.throw(_("{0} already has facility {1}.").format(employer, doc.facility_loan))
	if not doc.customer:
		frappe.throw(_("Set the company as borrower (Customer) first."))
	if flt(doc.facility_limit) <= 0 or flt(doc.facility_rate) <= 0:
		frappe.throw(_("Enter the facility limit and interest rate agreed by the Board."))
	from sacco_manager.sacco_manager.doctype.sacco_board_approval.sacco_board_approval import has_board_approval

	if not has_board_approval("SACCO Employer", employer):
		frappe.throw(_("Lending to a group company is related-party lending: submit a SACCO Board Approval (decision Approved) "
			"for SACCO Employer {0}, with the limit and rate, before opening the facility.").format(employer),
			title=_("Board approval required"))
	if not frappe.db.exists("Loan Product", FACILITY_PRODUCT):
		frappe.throw(_("Loan product {0} is missing; run bench migrate.").format(FACILITY_PRODUCT))
	start = getdate(doc.facility_start or today())
	end = getdate(doc.facility_end or add_years(start, 5))
	company = frappe.db.get_single_value("SACCO Settings", "company")
	loan = frappe.get_doc({
		"doctype": "Loan", "applicant_type": "Customer", "applicant": doc.customer, "company": company,
		"loan_product": FACILITY_PRODUCT, "is_term_loan": 0, "loan_amount": flt(doc.facility_limit),
		"posting_date": start, "rate_of_interest": flt(doc.facility_rate),
		"maximum_limit_amount": flt(doc.facility_limit), "limit_applicable_start": start, "limit_applicable_end": end,
	})
	loan.flags.ignore_permissions = True
	loan.flags.ignore_mandatory = True
	loan.insert()
	loan.submit()
	doc.db_set({"facility_loan": loan.name, "facility_start": start, "facility_end": end})
	doc.add_comment("Info", _("Facility {0} opened: limit {1}, {2}% p.a.").format(
		loan.name, frappe.format_value(doc.facility_limit, {"fieldtype": "Currency"}), doc.facility_rate))
	return loan.name


@frappe.whitelist()
def get_facility_status(employer):
	frappe.has_permission("SACCO Employer", "read", employer, throw=True)
	return frappe.get_doc("SACCO Employer", employer).facility_status()


def for_branch(branch):
	return frappe.db.get_value("SACCO Employer", {"branch": branch, "enabled": 1}, "name") if branch else None
