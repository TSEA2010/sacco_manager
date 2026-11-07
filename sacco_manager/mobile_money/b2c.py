# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""M-Pesa B2C payouts for approved savings withdrawals."""

import frappe
from frappe import _
from frappe.utils import flt

from sacco_manager.mobile_money.daraja import Daraja


def _account_for_gl(gl_account):
	acc = frappe.db.get_value("SACCO M-Pesa Account", {"gl_account": gl_account, "enabled": 1}, "name")
	if not acc:
		frappe.throw(_("No enabled M-Pesa account uses GL account {0}.").format(gl_account))
	return acc


def queue_withdrawal_payout(withdrawal):
	frappe.enqueue("sacco_manager.mobile_money.b2c.send_withdrawal_payout", withdrawal=withdrawal,
		queue="short", enqueue_after_commit=True)


def send_withdrawal_payout(withdrawal):
	wr = frappe.get_doc("Withdrawal Request", withdrawal)
	if wr.docstatus != 1 or wr.payment_mode != "M-Pesa":
		return
	if wr.mpesa_transaction and frappe.db.get_value("SACCO M-Pesa Transaction", wr.mpesa_transaction, "status") in ("Pending", "Paid"):
		return  # already sent
	acc = _account_for_gl(wr.payment_account)
	try:
		originator, resp = Daraja(acc).b2c(wr.mpesa_phone, wr.amount, f"Savings withdrawal {wr.name}", wr.name)
		status, note = ("Pending", None) if str(resp.get("ResponseCode")) == "0" else ("Failed", resp.get("ResponseDescription"))
	except Exception as e:
		originator, resp, status, note = f"FAILED-{wr.name}-{frappe.generate_hash(length=6)}", {}, "Failed", str(e)[:300]
		frappe.log_error(title=f"B2C payout failed {wr.name}", message=frappe.get_traceback())
	txn = frappe.get_doc({
		"doctype": "SACCO M-Pesa Transaction",
		"transaction_id": originator,
		"direction": "Outbound",
		"channel": "B2C",
		"status": status,
		"amount": flt(wr.amount),
		"mpesa_account": acc,
		"msisdn": wr.mpesa_phone,
		"member": wr.member,
		"checkout_request_id": resp.get("ConversationID"),
		"allocated_doctype": "Withdrawal Request",
		"allocated_document": wr.name,
		"result_description": note or resp.get("ResponseDescription"),
		"raw_payload": frappe.as_json(resp),
	})
	txn.flags.ignore_permissions = True
	txn.insert()
	wr.db_set("mpesa_transaction", txn.name)
	if status == "Failed":
		_notify_failure(wr, note)
	frappe.db.commit()


def handle_b2c_result(result, raw):
	originator = result.get("OriginatorConversationID")
	name = frappe.db.get_value("SACCO M-Pesa Transaction", {"transaction_id": originator}, "name") or \
		frappe.db.get_value("SACCO M-Pesa Transaction", {"checkout_request_id": result.get("ConversationID")}, "name")
	if not name:
		frappe.log_error(title="B2C result for unknown request", message=raw[:4000])
		return
	txn = frappe.get_doc("SACCO M-Pesa Transaction", name)
	if txn.status not in ("Pending",):
		return
	ok = str(result.get("ResultCode")) == "0"
	txn.db_set({
		"status": "Paid" if ok else "Failed",
		"receipt_number": result.get("TransactionID"),
		"result_code": str(result.get("ResultCode")),
		"result_description": result.get("ResultDesc"),
		"raw_payload": raw,
	})
	if txn.allocated_doctype == "Withdrawal Request":
		wr = frappe.get_doc("Withdrawal Request", txn.allocated_document)
		if ok:
			wr.db_set("status", "Paid")
		else:
			_notify_failure(wr, result.get("ResultDesc"))


def _notify_failure(wr, reason):
	wr.add_comment("Comment", _("M-Pesa payout failed: {0}. Retry the payout or cancel the withdrawal to restore the savings.").format(reason))
	recipients = [u.parent for u in frappe.get_all("Has Role", filters={"role": "SACCO Accountant", "parenttype": "User"},
		fields=["parent"])]
	if recipients:
		frappe.sendmail(recipients=recipients, subject=_("M-Pesa payout failed: {0}").format(wr.name),
			message=_("Payout of {0} to {1} for {2} failed: {3}").format(wr.amount, wr.mpesa_phone, wr.member, reason),
			reference_doctype=wr.doctype, reference_name=wr.name)


@frappe.whitelist()
def retry_payout(withdrawal):
	frappe.has_permission("Withdrawal Request", "submit", withdrawal, throw=True)
	status = frappe.db.get_value("SACCO M-Pesa Transaction",
		frappe.db.get_value("Withdrawal Request", withdrawal, "mpesa_transaction"), "status")
	if status in ("Pending", "Paid"):
		frappe.throw(_("Payout is {0}; nothing to retry.").format(status))
	send_withdrawal_payout(withdrawal)
	return frappe.db.get_value("Withdrawal Request", withdrawal, "mpesa_transaction")
