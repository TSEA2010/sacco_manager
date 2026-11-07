# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Everything the office keeps in a member's physical file, gathered for the
"SACCO Member File" and "SACCO Customer File" print formats.

Page 1 is a one-page summary (identity, contacts, next of kin, financial position, KYC checklist);
the following pages carry the detail (accounts, loans, guarantees, shares, recent activity, documents)."""

import frappe
from frappe import _
from frappe.utils import flt, format_date, getdate, now_datetime, today

from sacco_manager.utils import (
	get_committed_pledges,
	get_multiplier,
	is_valid_kra_pin,
	normalize_ke_phone,
)

OPEN_LOAN = ("Disbursed", "Partially Disbursed", "Active")


def member_for_customer(customer):
	linked = frappe.db.get_value("Customer", customer, "sacco_member") if frappe.db.has_column("Customer", "sacco_member") else None
	return linked or frappe.db.get_value("SACCO Member", {"customer": customer}, "name")


def sacco_customer_file(customer):
	"""Jinja method: the member file for a Customer record (None if the customer is not a member)."""
	return sacco_member_file(member_for_customer(customer)) if customer else None


def sacco_member_file(member):
	"""Jinja method: the full context for one member's file."""
	if not member or not frappe.db.exists("SACCO Member", member):
		return None
	frappe.has_permission("SACCO Member", "read", member, throw=True)
	m = frappe.get_doc("SACCO Member", member)
	cust = frappe.get_doc("Customer", m.customer) if m.customer and frappe.db.exists("Customer", m.customer) else None
	fmt = lambda v: frappe.format_value(flt(v), {"fieldtype": "Currency"})  # noqa: E731
	f = frappe._dict(member=m, customer=cust, fmt=fmt, fdate=lambda d: format_date(d) if d else "")

	f.company = frappe.db.get_single_value("SACCO Settings", "company") or ""
	f.logo = frappe.db.get_single_value("Website Settings", "app_logo") or ""
	f.printed_on = format_date(today())
	f.printed_at = now_datetime().strftime("%d-%m-%Y %H:%M")
	f.printed_by = frappe.db.get_value("User", frappe.session.user, "full_name") or frappe.session.user
	f.photo = (cust.image if cust and cust.get("image") else None) or _first_image(member, m.customer)

	f.addresses = _linked("Address", m.customer, ["address_title", "address_line1", "address_line2", "city", "county",
		"state", "pincode", "country", "phone", "email_id", "address_type"])
	f.contacts = _linked("Contact", m.customer, ["full_name", "mobile_no", "phone", "email_id", "designation"])
	f.age = _age(m.date_of_birth)
	f.years_member = _age(m.joining_date)

	_savings(f, m)
	_loans(f, m)
	_guarantees(f, m)
	_shares(f, m)
	_activity(f, m)
	f.attachments = frappe.get_all("File", filters=[["attached_to_doctype", "in", ["SACCO Member", "Customer"]],
		["attached_to_name", "in", [member, m.customer or "-"]]], fields=["file_name", "file_url", "creation", "attached_to_doctype"],
		order_by="creation")
	f.registration_fee = frappe.db.get_value("Journal Entry", m.registration_fee_je, ["posting_date", "total_debit"], as_dict=True) \
		if m.registration_fee_je else None
	f.pending_withdrawals = frappe.get_all("Withdrawal Request", filters={"member": member, "docstatus": 0},
		fields=["name", "amount", "creation"]) if frappe.db.has_column("Withdrawal Request", "member") else []
	f.comments = frappe.get_all("Comment", filters={"reference_doctype": "SACCO Member", "reference_name": member,
		"comment_type": "Comment"}, fields=["content", "owner", "creation"], order_by="creation desc", limit=8)

	f.position = [
		(_("Share capital"), flt(m.share_balance)),
		(_("Savings"), f.savings_total),
		(_("Loans outstanding (principal)"), -f.loan_principal),
		(_("Net position"), flt(m.share_balance) + f.savings_total - f.loan_principal),
	]
	_kyc(f, m)
	return f


# ---------------------------------------------------------------------------
def _linked(doctype, customer, fields):
	if not customer:
		return []
	names = frappe.get_all("Dynamic Link", filters={"parenttype": doctype, "link_doctype": "Customer", "link_name": customer},
		pluck="parent")
	return frappe.get_all(doctype, filters={"name": ["in", names]}, fields=["name", *fields]) if names else []


def _first_image(member, customer):
	return frappe.db.get_value("File", {"attached_to_doctype": ["in", ["SACCO Member", "Customer"]],
		"attached_to_name": ["in", [member, customer or "-"]], "file_name": ["like", "%photo%"]}, "file_url")


def _age(d):
	if not d:
		return None
	d, t = getdate(d), getdate(today())
	return t.year - d.year - ((t.month, t.day) < (d.month, d.day))


def _savings(f, m):
	mult = get_multiplier() or 1
	f.savings = frappe.get_all("SACCO Savings Account", filters={"member": m.name},
		fields=["name", "account_no", "account_type", "balance", "status", "monthly_contribution", "interest_rate"], order_by="account_type")
	for a in f.savings:
		a.locked = get_committed_pledges(a.name) / mult
		a.free = max(0, flt(a.balance) - a.locked)
	f.savings_total = sum(flt(a.balance) for a in f.savings)
	f.monthly_contribution = sum(flt(a.monthly_contribution) for a in f.savings)
	f.savings_locked = sum(a.locked for a in f.savings)


def _loans(f, m):
	f.loans, f.loan_principal, f.loan_overdue, f.loan_monthly = [], 0, 0, 0
	f.open_loans, f.summary_loans, f.more_loans, f.max_days, f.applications = [], [], 0, 0, []
	if not m.customer:
		return
	eod = f"{today()} 23:59:59"
	for ln in frappe.get_all("Loan", filters={"applicant": m.customer, "docstatus": 1},
			fields=["name", "loan_product", "loan_amount", "disbursed_amount", "total_principal_paid", "status",
				"disbursement_date", "rate_of_interest", "repayment_periods", "monthly_repayment_amount", "closure_date",
				"loan_application"], order_by="disbursement_date desc"):
		ln.principal = max(0, flt(ln.disbursed_amount) - flt(ln.total_principal_paid)) if ln.status in OPEN_LOAN else 0
		d = frappe.db.sql("""SELECT IFNULL(SUM(outstanding_amount), 0), MIN(demand_date) FROM `tabLoan Demand`
			WHERE loan=%s AND docstatus=1 AND outstanding_amount > 0.5 AND demand_date <= %s""", (ln.name, eod))[0]
		ln.overdue = flt(d[0]) if ln.status in OPEN_LOAN else 0
		ln.days = (getdate(today()) - getdate(d[1])).days if d[1] and ln.overdue else 0
		ln.guarantors = frappe.get_all("SACCO Loan Guarantor", filters={"loan_application": ln.loan_application, "is_self": 0,
			"status": ["!=", "Declined"]}, fields=["guarantor_member", "guarantor_name", "pledge_value", "status"]) \
			if ln.loan_application else []
		f.loans.append(ln)
		if ln.status in OPEN_LOAN:
			f.loan_principal += ln.principal
			f.loan_overdue += ln.overdue
			f.loan_monthly += flt(ln.monthly_repayment_amount)
	f.open_loans = [x for x in f.loans if x.status in OPEN_LOAN]
	f.max_days = max([x.days for x in f.open_loans] or [0])
	# page 1 must stay one page: show the six largest exposures, the rest are on the detail page
	ranked = sorted(f.open_loans, key=lambda x: -(x.principal + x.overdue))
	f.summary_loans = [x for x in ranked if x.principal or x.overdue][:6]
	f.more_loans = len(f.open_loans) - len(f.summary_loans)
	f.applications = frappe.get_all("Loan Application", filters={"applicant": m.customer, "docstatus": ["<", 2],
		"status": ["in", ["Open", "Approved"]]}, fields=["name", "loan_product", "loan_amount", "status", "posting_date"])


def _guarantees(f, m):
	rows = frappe.db.sql("""SELECT g.loan_application, g.applicant_member, g.pledge_value, g.status,
			am.full_name AS applicant_name, am.member_no AS applicant_no
		FROM `tabSACCO Loan Guarantor` g LEFT JOIN `tabSACCO Member` am ON am.name = g.applicant_member
		WHERE g.guarantor_member = %s AND g.is_self = 0 AND g.status != 'Declined'""", m.name, as_dict=True)
	f.guarantees_given = []
	for g in rows:
		loans = frappe.get_all("Loan", filters={"loan_application": g.loan_application, "docstatus": 1},
			fields=["name", "status", "disbursed_amount", "total_principal_paid"])
		if loans and all(x.status not in OPEN_LOAN for x in loans):
			continue  # loan repaid: guarantee released
		g.loan = ", ".join(x.name for x in loans) or _("(application only)")
		g.loan_balance = sum(max(0, flt(x.disbursed_amount) - flt(x.total_principal_paid)) for x in loans if x.status in OPEN_LOAN)
		f.guarantees_given.append(g)
	f.guarantee_total = sum(flt(g.pledge_value) for g in f.guarantees_given)


def _shares(f, m):
	f.share_entries = frappe.get_all("SACCO Share Ledger", filters={"member": m.name, "docstatus": 1},
		fields=["date", "transaction_type", "shares", "total_amount", "name"], order_by="date desc, creation desc", limit=10)


def _activity(f, m):
	f.transactions = frappe.get_all("SACCO Savings Transaction", filters={"member": m.name, "docstatus": 1},
		fields=["posting_date", "transaction_type", "amount", "savings_account", "remarks", "name"],
		order_by="posting_date desc, creation desc", limit=15) if frappe.db.has_column("SACCO Savings Transaction", "remarks") else \
		frappe.get_all("SACCO Savings Transaction", filters={"member": m.name, "docstatus": 1},
		fields=["posting_date", "transaction_type", "amount", "savings_account", "name"], order_by="posting_date desc, creation desc", limit=15)
	f.repayments = frappe.get_all("Loan Repayment", filters={"applicant": m.customer, "docstatus": 1},
		fields=["posting_date", "against_loan", "amount_paid", "name"], order_by="posting_date desc", limit=10) if m.customer else []


def _kyc(f, m):
	has_addr = bool(f.addresses)
	att = " ".join((a.file_name or "").lower() for a in f.attachments)
	try:
		phone_ok = bool(normalize_ke_phone(m.phone or ""))
	except Exception:
		phone_ok = False
	checks = [
		(_("Full name"), bool(m.full_name), m.full_name),
		(_("National ID / passport"), bool(m.id_number), m.id_number),
		(_("KRA PIN (valid format)"), bool(m.kra_pin) and is_valid_kra_pin(m.kra_pin), m.kra_pin),
		(_("Mobile number (valid)"), phone_ok, m.phone),
		(_("Email"), bool(m.email_id), m.email_id),
		(_("Date of birth"), bool(m.date_of_birth), f.fdate(m.date_of_birth)),
		(_("Gender"), bool(m.gender), m.gender),
		(_("Employer / branch"), bool(m.branch), m.branch),
		(_("Joining date"), bool(m.joining_date), f.fdate(m.joining_date)),
		(_("Postal / physical address"), has_addr, f.addresses[0].address_line1 if has_addr else ""),
		(_("Next of kin name"), bool(m.contact_name), m.contact_name),
		(_("Next of kin phone"), bool(m.contact_phone), m.contact_phone),
		(_("Next of kin relation"), bool(m.relation), m.relation),
		(_("Data protection consent"), bool(m.consent_data_processing), f.fdate(m.consent_date)),
		(_("Registration fee paid"), bool(m.registration_fee_je), f.fdate(f.registration_fee.posting_date) if f.registration_fee else ""),
		(_("Passport photo on file"), bool(f.photo), ""),
		(_("Copy of ID attached"), any(k in att for k in ("id", "passport")), ""),
		(_("Signed membership form attached"), any(k in att for k in ("form", "application", "signed")), ""),
	]
	f.kyc = [frappe._dict(label=c[0], ok=c[1], value=c[2] or "") for c in checks]
	f.kyc_done = sum(1 for c in f.kyc if c.ok)
	f.kyc_pct = round(100 * f.kyc_done / len(f.kyc))
	f.kyc_missing = [c.label for c in f.kyc if not c.ok]
