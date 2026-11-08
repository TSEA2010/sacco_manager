# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Loan Application gates:
1. Only SACCO Managers/Administrators may approve.
2. An application with guarantor rows cannot be approved while any guarantee is still pending, while
   the confirmed pledges do not cover the loan (a declined guarantor can be replaced), or while any
   confirmed pledge exceeds what the guarantor's savings can still back."""

import frappe
from frappe import _
from frappe.utils import flt

from sacco_manager.utils import get_pledge_capacity

APPROVERS = ("SACCO Manager", "SACCO Administrator", "System Manager")


def validate(doc, method=None):
	if doc.get("status") != "Approved":
		return
	before = doc.get_doc_before_save()
	newly_approved = not before or before.get("status") != "Approved"
	if not newly_approved:
		return
	if not set(APPROVERS) & set(frappe.get_roles()) and not frappe.flags.in_import:
		frappe.throw(_("Only a SACCO Manager can approve loan applications."), frappe.PermissionError)
	from sacco_manager.sacco_manager.doctype.sacco_board_approval.sacco_board_approval import (
		check_loan_application,
	)

	from sacco_manager.company_lending import check_application as check_group_company

	check_group_company(doc)  # related-party rules first: clearer message
	check_loan_application(doc)

	rows = frappe.get_all(
		"SACCO Loan Guarantor",
		filters={"loan_application": doc.name},
		fields=["guarantor_name", "status", "savings_account", "pledge_value"],
	)
	if not rows:
		return
	pending = [r.guarantor_name for r in rows if r.status == "Pending"]
	if pending:
		frappe.throw(_("Cannot approve: still waiting for {0} to answer the guarantee request.").format(", ".join(pending)),
			title=_("Guarantor approval pending"))
	confirmed = [r for r in rows if r.status == "Confirmed"]
	covered = sum(flt(r.pledge_value) for r in confirmed)
	if covered + 0.5 < flt(doc.loan_amount):
		declined = [r.guarantor_name for r in rows if r.status == "Declined"]
		frappe.throw(_("Cannot approve: confirmed guarantees cover {0} of the {1} loan{2}. Add another guarantor or reduce the loan.").format(
			frappe.format_value(covered, {"fieldtype": "Currency"}), frappe.format_value(doc.loan_amount, {"fieldtype": "Currency"}),
			_(" ({0} declined)").format(", ".join(declined)) if declined else ""), title=_("Not enough guarantees"))
	short = [r.guarantor_name for r in confirmed
		if flt(r.pledge_value) > get_pledge_capacity(r.savings_account, exclude_application=doc.name) + 0.5]
	if short:
		frappe.throw(_("Cannot approve: savings of {0} no longer cover their pledge.").format(", ".join(short)))
