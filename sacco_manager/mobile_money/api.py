# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Logged-in actions: member STK push from the portal, staff allocation and setup helpers."""

import frappe
from frappe import _
from frappe.utils import cstr, flt

from sacco_manager.mobile_money.allocation import allocate
from sacco_manager.mobile_money.daraja import Daraja
from sacco_manager.utils import normalize_ke_phone

STAFF_ALLOCATORS = ("SACCO Accountant", "SACCO Manager", "SACCO Administrator", "System Manager")


def _default_account():
	acc = frappe.get_all("SACCO M-Pesa Account", filters={"enabled": 1}, pluck="name", order_by="creation asc", limit=1)
	if not acc:
		frappe.throw(_("M-Pesa is not set up yet."))
	return acc[0]


@frappe.whitelist(methods=["POST"])
def member_stk_push(purpose, amount, phone=None):
	from sacco_manager.api.sacco_portal import get_member_for_user

	member = get_member_for_user()
	amount = flt(amount)
	if amount < 1 or amount > 250000:
		frappe.throw(_("Amount must be between 1 and 250,000."))
	msisdn = normalize_ke_phone(phone or member.phone)
	if not msisdn:
		frappe.throw(_("Enter a valid Safaricom number."))
	purpose = cstr(purpose)
	target, loan, ref = "Savings", None, member.member_no
	if purpose.startswith("Loan:"):
		loan = purpose.split(":", 1)[1]
		if frappe.db.get_value("Loan", loan, "applicant") != member.customer:
			frappe.throw(_("Access denied"), frappe.PermissionError)
		target, ref = "Loan Repayment", f"{member.member_no}L"
	elif purpose == "Shares":
		target, ref = "Shares", f"{member.member_no}S"

	recent = frappe.db.count("SACCO M-Pesa Transaction", {"member": member.name, "channel": "STK Push",
		"creation": [">", frappe.utils.add_to_date(None, minutes=-2)]})
	if recent >= 3:
		frappe.throw(_("Too many requests. Please wait a minute and check your phone."))

	acc = _default_account()
	resp = Daraja(acc).stk_push(msisdn, amount, ref.replace("-", ""), "SACCO payment")
	if str(resp.get("ResponseCode")) != "0":
		frappe.throw(_("M-Pesa declined the request: {0}").format(resp.get("ResponseDescription") or resp.get("errorMessage")))
	doc = frappe.get_doc({
		"doctype": "SACCO M-Pesa Transaction",
		"transaction_id": resp["CheckoutRequestID"],
		"checkout_request_id": resp["CheckoutRequestID"],
		"direction": "Inbound",
		"channel": "STK Push",
		"status": "Pending",
		"amount": amount,
		"mpesa_account": acc,
		"msisdn": msisdn,
		"member": member.name,
		"allocation_target": target,
		"loan": loan,
		"bill_ref_number": ref,
		"purpose": purpose[:140],
		"transaction_time": frappe.utils.now_datetime(),
	})
	doc.flags.ignore_permissions = True
	doc.insert()
	return {"request": doc.name, "message": _("Check your phone and enter your M-Pesa PIN to pay KES {0:,.0f}.").format(amount)}


@frappe.whitelist(methods=["POST"])
def allocate_manually(txn, target, member, savings_account=None, loan=None, note=None):
	frappe.only_for(STAFF_ALLOCATORS)
	doc = frappe.get_doc("SACCO M-Pesa Transaction", txn)
	doc.check_permission("write")
	if doc.status != "Unallocated":
		frappe.throw(_("Only Unallocated payments can be allocated (this one is {0}).").format(doc.status))
	if savings_account and frappe.db.get_value("SACCO Savings Account", savings_account, "member") != member:
		frappe.throw(_("Savings account does not belong to the selected member."))
	if loan and frappe.db.get_value("Loan", loan, "applicant") != frappe.db.get_value("SACCO Member", member, "customer"):
		frappe.throw(_("Loan does not belong to the selected member."))
	doc.db_set({"member": member, "allocation_target": target, "loan": loan, "allocation_note": note})
	result = allocate(txn, target=target, member=member, savings_account=savings_account, loan=loan,
		by_user=frappe.session.user)
	if result != "Allocated":
		frappe.throw(frappe.db.get_value("SACCO M-Pesa Transaction", txn, "allocation_note") or _("Allocation failed."))
	return result


@frappe.whitelist(methods=["POST"])
def register_c2b_urls(account):
	frappe.only_for(("SACCO Administrator", "System Manager"))
	resp = Daraja(account).register_c2b_urls()
	frappe.db.set_value("SACCO M-Pesa Account", account, "urls_registered_on", frappe.utils.now_datetime())
	return resp


@frappe.whitelist(methods=["POST"])
def test_connection(account):
	frappe.only_for(("SACCO Administrator", "System Manager"))
	Daraja(account).token()
	return _("Connected to Daraja successfully.")
