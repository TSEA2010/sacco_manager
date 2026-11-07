# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Turn an inbound M-Pesa payment into SACCO postings.

Account reference (BillRefNumber) convention printed on member cards and the portal:
    <MemberNo>      savings deposit           e.g. TSCCSL-0013, TSCCSL0013, 0013, 13
    <MemberNo>L     loan repayment (oldest active loan)
    <LoanID>        repayment of that specific loan
    <MemberNo>S     share capital
    <MemberNo>R     registration fee
Anything that cannot be matched stays "Unallocated" for an accountant to allocate.
Every posting debits the M-Pesa account's GL (clearing) account and carries the M-Pesa
receipt number as its reference, so the M-Pesa statement reconciles line by line.
"""

import re

import frappe
from frappe import _
from frappe.utils import flt, getdate

from sacco_manager.lending_utils import create_loan_repayment, get_active_loans, loan_outstanding
from sacco_manager.sacco_manager.doctype.sacco_registration_fee.sacco_registration_fee import (
	create_registration_fee,
)
from sacco_manager.sacco_manager.doctype.sacco_savings_transaction.sacco_savings_transaction import (
	create_savings_transaction,
)
from sacco_manager.sacco_manager.doctype.sacco_share_ledger.sacco_share_ledger import create_share_entry
from sacco_manager.utils import describe_error, normalize_ke_phone

SUFFIX_TARGET = {"L": "Loan Repayment", "S": "Shares", "R": "Registration Fee"}


def _norm(s):
	return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def _member_index():
	idx = frappe.cache().get_value("sacco_member_ref_index")
	if idx:
		return idx
	idx = {"full": {}, "num": {}}
	for m in frappe.get_all("SACCO Member", filters={"status": "Active"}, fields=["name", "member_no"]):
		if not m.member_no:
			continue
		idx["full"][_norm(m.member_no)] = m.name
		digits = re.sub(r"\D", "", m.member_no)
		if digits:
			key = str(int(digits))
			idx["num"][key] = None if key in idx["num"] else m.name  # None = ambiguous
	frappe.cache().set_value("sacco_member_ref_index", idx, expires_in_sec=600)
	return idx


def parse_reference(bill_ref, msisdn=None):
	"""Return (member, target, loan) from a BillRefNumber; any part may be None."""
	ref = _norm(bill_ref)
	if ref and frappe.db.exists("Loan", bill_ref.strip()):
		applicant = frappe.db.get_value("Loan", bill_ref.strip(), "applicant")
		member = frappe.db.get_value("SACCO Member", {"customer": applicant}, "name")
		return member, "Loan Repayment", bill_ref.strip()

	target = "Savings"
	idx = _member_index()
	member = idx["full"].get(ref)
	if not member and ref and ref[-1] in SUFFIX_TARGET:
		member = idx["full"].get(ref[:-1])
		if member:
			target = SUFFIX_TARGET[ref[-1]]
	if not member and ref:
		m = re.fullmatch(r"([A-Z]*?)(\d+)([LSR]?)", ref)
		if m:
			member = idx["num"].get(str(int(m.group(2))))
			if member and m.group(3):
				target = SUFFIX_TARGET[m.group(3)]
	if not member and msisdn:
		phone = normalize_ke_phone(msisdn)
		if phone:
			found = frappe.get_all("SACCO Member", filters={"phone": phone, "status": "Active"}, pluck="name", limit=2)
			if len(found) == 1:
				member = found[0]
	return member, (target if member else None), None


def _primary_savings(member):
	acc = frappe.get_all("SACCO Savings Account", filters={"member": member, "status": ["in", ["Active", "Dormant"]]},
		order_by="creation asc", pluck="name", limit=1)
	if not acc:
		frappe.throw(_("Member {0} has no active savings account.").format(member))
	return acc[0]


def allocate(txn_name, target=None, member=None, savings_account=None, loan=None, by_user=None):
	"""Allocate an inbound SACCO M-Pesa Transaction. Idempotent: allocated txns are skipped."""
	txn = frappe.get_doc("SACCO M-Pesa Transaction", txn_name, for_update=True)
	if txn.direction != "Inbound" or txn.status not in ("Unallocated", "Pending"):
		return txn.status
	if txn.status == "Pending" and txn.channel == "STK Push" and not txn.receipt_number:
		return txn.status  # payment not completed yet

	member = member or txn.member
	target = target or txn.allocation_target
	loan = loan or txn.loan
	if not member:
		member, parsed_target, parsed_loan = parse_reference(txn.bill_ref_number, txn.msisdn)
		target = target or parsed_target
		loan = loan or parsed_loan
	if not member or not target:
		txn.db_set({"status": "Unallocated", "allocation_note": _("Could not match account reference '{0}' to a member.").format(txn.bill_ref_number or "")})
		return "Unallocated"

	gl = frappe.db.get_value("SACCO M-Pesa Account", txn.mpesa_account, "gl_account")
	posting_date = getdate(txn.transaction_time) if txn.transaction_time else frappe.utils.today()
	receipt = txn.receipt_number or txn.transaction_id
	ref = ("SACCO M-Pesa Transaction", txn.name)
	amount = flt(txn.amount)
	notes = []
	first_doc = None

	frappe.db.savepoint("sacco_mpesa_alloc")
	try:
		if target == "Loan Repayment":
			# a named loan first, then the member's other active loans, oldest first
			queue = [loan] if loan else []
			queue += [ln.name for ln in get_active_loans(member) if ln.name not in queue]
			if not queue:
				notes.append(_("No active loan; credited to savings."))
			for ln_name in queue:
				if amount <= 0.005:
					break
				payable = loan_outstanding(ln_name, posting_date)["payable"]
				if payable <= 0.005 and ln_name != queue[0]:
					continue
				to_loan = min(amount, payable) if payable > 0 else amount
				rp = create_loan_repayment(ln_name, to_loan, posting_date, gl, reference_number=receipt,
					remarks=f"M-Pesa {receipt}", mode_of_payment="M-Pesa")
				if rp.meta.has_field("sacco_mpesa_transaction"):
					rp.db_set("sacco_mpesa_transaction", txn.name)
				first_doc = first_doc or ("Loan Repayment", rp.name)
				loan = loan or ln_name
				amount -= to_loan
			if amount > 0.005:
				notes.append(_("Remaining {0} credited to savings.").format(round(amount, 2)))
			target = "Savings"
		if target == "Shares" and amount > 0:
			price = flt(frappe.db.get_single_value("SACCO Settings", "default_share_price"))
			buy = (amount // price) * price if price else 0
			if buy > 0:
				sh = create_share_entry(member, buy, posting_date, gl, reference=ref, reference_no=receipt)
				first_doc = first_doc or ("SACCO Share Ledger", sh.name)
				amount -= buy
			if amount > 0.5:
				notes.append(_("Remainder {0} (less than one share) credited to savings.").format(amount))
			target = "Savings"
		if target == "Registration Fee" and amount > 0:
			fee = flt(frappe.db.get_single_value("SACCO Settings", "registration_fee_amount")) or amount
			fee = min(fee, amount)
			rf = create_registration_fee(member, fee, posting_date, gl)
			first_doc = first_doc or ("SACCO Registration Fee", rf.name)
			amount -= fee
			target = "Savings"
		if target == "Savings" and amount > 0.005:
			acc = savings_account or _primary_savings(member)
			st = create_savings_transaction(acc, "Deposit", amount, posting_date, payment_account=gl,
				mode_of_payment="M-Pesa", reference_no=receipt, reference=ref,
				remarks=f"M-Pesa from {txn.payer_name or txn.msisdn or ''}".strip())
			first_doc = first_doc or ("SACCO Savings Transaction", st.name)
	except Exception as e:
		frappe.db.rollback(save_point="sacco_mpesa_alloc")
		frappe.log_error(title=f"M-Pesa allocation failed {txn_name}", message=frappe.get_traceback())
		frappe.db.set_value("SACCO M-Pesa Transaction", txn_name, {
			"status": "Unallocated", "member": member, "allocation_note": _("Auto-allocation failed: {0}").format(describe_error(e))})
		return "Unallocated"

	txn.db_set({
		"status": "Allocated",
		"member": member,
		"allocation_target": txn.allocation_target or target,
		"loan": loan,
		"allocated_doctype": first_doc[0] if first_doc else None,
		"allocated_document": first_doc[1] if first_doc else None,
		"allocated_by": by_user or "Administrator",
		"allocation_note": " ".join(notes) or None,
	})
	return "Allocated"


def allocate_job(txn_name):
	allocate(txn_name)
	frappe.db.commit()


def retry_unallocated():
	"""Hourly: try again for payments that arrived before the member/loan existed."""
	# includes payments that matched a member but failed (e.g. no savings account yet)
	for name in frappe.get_all("SACCO M-Pesa Transaction",
			filters={"direction": "Inbound", "status": "Unallocated", "allocated_by": ["is", "not set"]},
			pluck="name", limit=200):
		allocate(name)
		frappe.db.commit()
