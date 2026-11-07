# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Board / committee approval register. Required (per SACCO Settings) before approving loans above
the limit and before submitting loan write-offs; also the place to record dividend and policy decisions."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class SACCOBoardApproval(Document):
	def validate(self):
		if self.reference_doctype and not self.reference_name:
			frappe.throw(_("Select the document this approval applies to."))
		if self.reference_doctype == "Loan Application" and self.reference_name and not self.amount:
			self.amount = frappe.db.get_value("Loan Application", self.reference_name, "loan_amount")


def has_board_approval(reference_doctype, reference_name, approval_types=None):
	filters = {"reference_doctype": reference_doctype, "reference_name": reference_name, "docstatus": 1, "decision": "Approved"}
	if approval_types:
		filters["approval_type"] = ["in", approval_types]
	return frappe.db.get_value("SACCO Board Approval", filters, "name")


def check_loan_application(doc):
	limit = flt(frappe.db.get_single_value("SACCO Settings", "board_approval_loan_limit"))
	if limit > 0 and flt(doc.loan_amount) > limit and not has_board_approval("Loan Application", doc.name):
		frappe.throw(
			_("Loans above {0} need a submitted SACCO Board Approval (decision Approved) for {1} before approval.").format(
				frappe.format_value(limit, {"fieldtype": "Currency"}), doc.name),
			title=_("Board approval required"),
		)


def check_write_off(doc, method=None):
	if not frappe.db.get_single_value("SACCO Settings", "board_approval_for_write_off"):
		return
	loan = doc.get("loan") or doc.get("against_loan")
	if not (has_board_approval(doc.doctype, doc.name) or (loan and has_board_approval("Loan", loan, ["Loan write-off"]))):
		frappe.throw(_("Write-offs need a submitted SACCO Board Approval (type Loan write-off) for this loan."),
			title=_("Board approval required"))
