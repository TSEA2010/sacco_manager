# Copyright (c) 2026, TSEA and contributors
# For license information, please see license.txt
# sacco_manager/sacco_manager/overrides/loan_application.py
"""
Hard gate: a Loan Application created through the member portal (i.e. one
that has SACCO Loan Guarantor rows attached) cannot be moved to "Approved"
while any guarantor on it is still Pending or has Declined.

Wired via hooks.py -> doc_events -> "Loan Application" -> "validate".

NOTE: this assumes Frappe Lending's Loan Application uses a `status` Select
field with "Approved" as one of its options (true as of the Frappe Lending
versions this was written against). If your version names that transition
differently, update the STATUS_FIELD / APPROVED_VALUE constants below —
frappe.db.has_column() guards against a hard crash if the field is missing,
but the block itself will silently no-op in that case, so it's worth
confirming this against your actual site before relying on it.
"""
import frappe
from frappe import _

STATUS_FIELD = "status"
APPROVED_VALUE = "Approved"


def validate(doc, method=None):
    if doc.doctype != "Loan Application":
        return
    if not frappe.db.has_column("Loan Application", STATUS_FIELD):
        return
    if doc.get(STATUS_FIELD) != APPROVED_VALUE:
        return

    guarantors = frappe.get_all(
        "SACCO Loan Guarantor",
        filters={"loan_application": doc.name},
        fields=["guarantor_name", "status"],
    )
    if not guarantors:
        # Not a portal/guarantor-backed application (e.g. created directly
        # on the desk) — nothing for this gate to enforce.
        return

    unresolved = [g for g in guarantors if g.status != "Confirmed"]
    if unresolved:
        pending = [g.guarantor_name for g in unresolved if g.status == "Pending"]
        declined = [g.guarantor_name for g in unresolved if g.status == "Declined"]
        parts = []
        if pending:
            parts.append(_("awaiting confirmation from {0}").format(", ".join(pending)))
        if declined:
            parts.append(_("declined by {0}").format(", ".join(declined)))
        frappe.throw(
            _("Cannot approve this application — guarantee is not fully in place ({0}).").format(
                "; ".join(parts)
            ),
            title=_("Guarantor Approval Pending"),
        )