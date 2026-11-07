# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Safaricom Daraja callback endpoints (guest).

Protection: each URL carries ?sc=<shortcode>&t=<per-account secret token>; the token is compared
in constant time, and an optional IP allowlist is enforced. Payloads are stored verbatim and
processed idempotently by M-Pesa receipt number, so Safaricom retries never double-post.
"""

import hmac
import json

import frappe
from frappe.utils import flt, get_datetime

OK = {"ResultCode": 0, "ResultDesc": "Accepted"}


def _payload():
	try:
		return json.loads(frappe.request.get_data(as_text=True) or "{}")
	except ValueError:
		return {}


def _account():
	# Safaricom posts JSON; Frappe then builds form_dict from the body only, so read the
	# shortcode and token from the query string explicitly.
	args = frappe.request.args if getattr(frappe, "request", None) else {}
	sc = args.get("sc") or frappe.form_dict.get("sc")
	token = args.get("t") or frappe.form_dict.get("t") or ""
	if not sc or not frappe.db.exists("SACCO M-Pesa Account", sc):
		frappe.throw("Unknown shortcode", frappe.PermissionError)
	acc = frappe.get_doc("SACCO M-Pesa Account", sc)
	expected = acc.get_password("callback_token", raise_exception=False) or ""
	if not expected or not hmac.compare_digest(str(token), expected):
		frappe.throw("Forbidden", frappe.PermissionError)
	allowed = [ip.strip() for ip in (acc.allowed_callback_ips or "").splitlines() if ip.strip()]
	if allowed and frappe.local.request_ip not in allowed:
		frappe.log_error(title="M-Pesa callback from unlisted IP", message=f"{frappe.local.request_ip} -> {sc}")
		frappe.throw("Forbidden", frappe.PermissionError)
	return acc


def _mpesa_time(value):
	s = str(value or "")
	if len(s) == 14 and s.isdigit():
		return get_datetime(f"{s[:4]}-{s[4:6]}-{s[6:8]} {s[8:10]}:{s[10:12]}:{s[12:14]}")
	return frappe.utils.now_datetime()


def _receipt_exists(receipt):
	return frappe.db.exists("SACCO M-Pesa Transaction", receipt) or frappe.db.exists(
		"SACCO M-Pesa Transaction", {"receipt_number": receipt})


def _respond(data=None):
	frappe.local.response.update(data or OK)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def c2b_validate(**kwargs):
	_account()
	# Accept everything; unmatched references are held as Unallocated rather than bounced,
	# so a member's money is never refused because of a typo.
	_respond({"ResultCode": "0", "ResultDesc": "Accepted"})


@frappe.whitelist(allow_guest=True, methods=["POST"])
def c2b_confirm(**kwargs):
	acc = _account()
	p = _payload()
	receipt = p.get("TransID")
	if not receipt:
		return _respond({"ResultCode": 1, "ResultDesc": "Missing TransID"})
	if _receipt_exists(receipt):
		return _respond()
	name = " ".join(x for x in (p.get("FirstName"), p.get("MiddleName"), p.get("LastName")) if x).strip()
	doc = frappe.get_doc({
		"doctype": "SACCO M-Pesa Transaction",
		"transaction_id": receipt,
		"receipt_number": receipt,
		"direction": "Inbound",
		"channel": "C2B",
		"status": "Pending",
		"transaction_time": _mpesa_time(p.get("TransTime")),
		"amount": flt(p.get("TransAmount")),
		"mpesa_account": acc.name,
		"msisdn": str(p.get("MSISDN") or "")[:140],
		"payer_name": name[:140],
		"bill_ref_number": (p.get("BillRefNumber") or "").strip()[:140],
		"raw_payload": json.dumps(p, indent=1),
	})
	doc.flags.ignore_permissions = True
	doc.insert()
	frappe.db.commit()
	frappe.enqueue("sacco_manager.mobile_money.allocation.allocate_job", txn_name=doc.name,
		queue="short", enqueue_after_commit=True)
	_respond()


@frappe.whitelist(allow_guest=True, methods=["POST"])
def stk_result(**kwargs):
	_account()
	p = _payload()
	cb = (p.get("Body") or {}).get("stkCallback") or {}
	checkout = cb.get("CheckoutRequestID")
	name = frappe.db.get_value("SACCO M-Pesa Transaction", {"checkout_request_id": checkout}, "name")
	if not name:
		frappe.log_error(title="STK callback for unknown request", message=json.dumps(p)[:4000])
		return _respond()
	txn = frappe.get_doc("SACCO M-Pesa Transaction", name)
	if txn.status != "Pending":
		return _respond()
	items = {i.get("Name"): i.get("Value") for i in (cb.get("CallbackMetadata") or {}).get("Item", [])}
	updates = {
		"result_code": str(cb.get("ResultCode")),
		"result_description": cb.get("ResultDesc"),
		"raw_payload": json.dumps(p, indent=1),
	}
	if str(cb.get("ResultCode")) == "0":
		receipt = items.get("MpesaReceiptNumber")
		if receipt and _receipt_exists(receipt):
			updates.update({"status": "Reversed", "allocation_note": f"Duplicate of {receipt} already recorded via C2B."})
			txn.db_set(updates)
			frappe.db.commit()
			return _respond()
		updates.update({
			"receipt_number": receipt,
			"amount": flt(items.get("Amount")) or txn.amount,
			"transaction_time": _mpesa_time(items.get("TransactionDate")),
			"msisdn": str(items.get("PhoneNumber") or txn.msisdn),
		})
		txn.db_set(updates)
		frappe.db.commit()
		frappe.enqueue("sacco_manager.mobile_money.allocation.allocate_job", txn_name=txn.name,
			queue="short", enqueue_after_commit=True)
	else:
		updates["status"] = "Failed"
		txn.db_set(updates)
		frappe.db.commit()
	_respond()


@frappe.whitelist(allow_guest=True, methods=["POST"])
def b2c_result(**kwargs):
	_account()
	p = _payload()
	res = p.get("Result") or {}
	from sacco_manager.mobile_money.b2c import handle_b2c_result

	handle_b2c_result(res, json.dumps(p, indent=1))
	frappe.db.commit()
	_respond()


@frappe.whitelist(allow_guest=True, methods=["POST"])
def b2c_timeout(**kwargs):
	_account()
	p = _payload()
	frappe.log_error(title="M-Pesa B2C timeout", message=json.dumps(p)[:4000])
	originator = (p.get("Result") or p).get("OriginatorConversationID") if isinstance(p, dict) else None
	name = frappe.db.get_value("SACCO M-Pesa Transaction", {"transaction_id": originator, "status": "Pending"}, "name") if originator else None
	if name:
		frappe.db.set_value("SACCO M-Pesa Transaction", name, {"status": "Failed",
			"result_description": "Queue timeout reported by Safaricom - check the M-Pesa statement before retrying."})
		frappe.db.commit()
	_respond()
