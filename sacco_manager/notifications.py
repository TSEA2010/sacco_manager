# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""SMS notifications to members (deposits, withdrawals, repayments, guarantor requests, arrears).

Uses Frappe's SMS Settings gateway (Africa's Talking, etc.); every message is recorded in SMS Log.
Sending is queued so a slow gateway never blocks a posting, and a failure never rolls back the
transaction that triggered it. Members can opt out (SACCO Member > Opted out of SMS).
"""

import frappe
from frappe.utils import cint, flt, fmt_money, today

from sacco_manager.utils import normalize_ke_phone


def _settings():
	return frappe.get_cached_doc("SACCO Settings")


DEFAULT_ON = {"sms_on_savings", "sms_on_repayment", "sms_on_guarantor"}


def _enabled(flag):
	# read straight from the database so switching SMS on/off takes effect immediately
	get = lambda f: frappe.db.get_single_value("SACCO Settings", f, cache=False)  # noqa: E731
	value = frappe.db.sql("SELECT value FROM `tabSingles` WHERE doctype='SACCO Settings' AND field=%s", flag)
	value = value[0][0] if value else (1 if flag in DEFAULT_ON else 0)
	return cint(get("sms_enabled")) and cint(value)


def _kes(v):
	return fmt_money(flt(v), precision=0, currency="KES")


def _member_phone(member):
	m = frappe.db.get_value("SACCO Member", member, ["phone", "sms_opt_out", "full_name", "status"], as_dict=True)
	if not m or cint(m.sms_opt_out):
		return None, None
	return normalize_ke_phone(m.phone), (m.full_name or "").split(" ")[0].title()


def queue_sms(phone, message, reference=None):
	if not phone:
		return
	sig = _settings().get("sms_signature") or "Trident SACCO"
	frappe.enqueue("sacco_manager.notifications._send", phone=phone, message=f"{message} - {sig}"[:459],
		reference=reference, queue="short", enqueue_after_commit=True)


def _send(phone, message, reference=None):
	try:
		from frappe.core.doctype.sms_settings.sms_settings import send_sms

		send_sms([phone], message, success_msg=False)
	except Exception:
		frappe.log_error(title=f"SACCO SMS failed {reference or ''}", message=frappe.get_traceback())


# ---------------------------------------------------------------------------
# doc_events
# ---------------------------------------------------------------------------
def on_savings_transaction_submit(doc, method=None):
	if not _enabled("sms_on_savings"):
		return
	phone, first = _member_phone(doc.member)
	if not phone:
		return
	verbs = {"Deposit": "received", "Withdrawal": "paid out", "Interest": "credited as interest",
		"Dividend": "credited as dividend", "Withholding Tax": "deducted as withholding tax"}
	verb = verbs.get(doc.transaction_type, doc.transaction_type.lower())
	bal = frappe.db.get_value("SACCO Savings Account", doc.savings_account, "balance")
	queue_sms(phone, f"Dear {first}, {_kes(doc.amount)} {verb} on your savings {doc.savings_account} on "
		f"{frappe.utils.format_date(doc.posting_date)}. New balance {_kes(bal)}. Ref {doc.reference_no or doc.name}",
		reference=doc.name)


def on_loan_repayment_submit(doc, method=None):
	if not _enabled("sms_on_repayment") or doc.applicant_type != "Customer":
		return
	member = frappe.db.get_value("SACCO Member", {"customer": doc.applicant}, "name")
	if not member:
		return
	phone, first = _member_phone(member)
	if not phone:
		return
	ln = frappe.db.get_value("Loan", doc.against_loan, ["disbursed_amount", "total_principal_paid"], as_dict=True)
	balance = max(0, flt(ln.disbursed_amount) - flt(ln.total_principal_paid)) if ln else 0
	queue_sms(phone, f"Dear {first}, loan repayment of {_kes(doc.amount_paid)} received for {doc.against_loan}. "
		f"Principal balance {_kes(balance)}. Ref {doc.reference_number or doc.name}", reference=doc.name)


def on_guarantor_insert(doc, method=None):
	if doc.is_self or doc.status != "Pending" or not _enabled("sms_on_guarantor"):
		return
	phone, first = _member_phone(doc.guarantor_member)
	if not phone:
		return
	applicant = frappe.db.get_value("SACCO Member", doc.applicant_member, "full_name") or ""
	from sacco_manager.guarantors import request_link

	queue_sms(phone, f"Dear {first}, {applicant} asks you to guarantee {_kes(doc.pledge_value)} of a loan "
		f"({doc.loan_application}). Accept or decline: {request_link(doc)} or in the member portal.",
		reference=doc.name)


# ---------------------------------------------------------------------------
# scheduler (weekly)
# ---------------------------------------------------------------------------
def weekly_arrears_reminders():
	if not _enabled("sms_arrears_reminder"):
		return
	min_days = cint(_settings().get("arrears_days_threshold")) or 30
	rows = frappe.db.sql(
		"""SELECT d.loan, l.applicant, SUM(d.outstanding_amount) AS overdue, MIN(d.demand_date) AS oldest
		FROM `tabLoan Demand` d JOIN `tabLoan` l ON l.name = d.loan
		WHERE d.docstatus = 1 AND d.outstanding_amount > 0.5 AND d.demand_date <= %s
		  AND l.docstatus = 1 AND l.status IN ('Disbursed','Partially Disbursed','Active')
		GROUP BY d.loan, l.applicant HAVING DATEDIFF(%s, oldest) >= %s""",
		(today(), today(), min_days),
		as_dict=True,
	)
	for r in rows:
		member = frappe.db.get_value("SACCO Member", {"customer": r.applicant}, "name")
		phone, first = _member_phone(member) if member else (None, None)
		if phone:
			queue_sms(phone, f"Dear {first}, your loan {r.loan} has {_kes(r.overdue)} overdue. "
				"Please pay via the SACCO paybill or contact the office.", reference=r.loan)
