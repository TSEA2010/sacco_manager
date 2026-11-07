# Copyright (c) 2026, TSEA and contributors
# For license information, please see license.txt
# sacco_manager/sacco_manager/doctype/sacco_loan_guarantor/sacco_loan_guarantor.py

from frappe.model.document import Document


class SACCOLoanGuarantor(Document):
	"""
	One row per proposed guarantor on a Loan Application.
	Created when the member submits a loan application (self-pledge rows are
	auto-confirmed since the applicant is pledging their own savings).
	Non-self rows start life as "Pending" and are flipped to "Confirmed" or
	"Declined" only by the guarantor themselves, via the tokenised
	/guarantor-confirm link — see api/sacco_portal.py.

	sacco_manager.overrides.loan_application enforces that a Loan Application
	cannot move to "Approved" while any linked row here is still Pending or
	has been Declined.
	"""
	pass
