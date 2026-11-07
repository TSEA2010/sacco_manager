# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Member self-service portal API (/sacco-portal) and guarantor confirmation (/guarantor-confirm).

Security model
- Every member endpoint resolves the member from the logged-in user (SACCO Member.user_id);
  nothing trusts a member / account / loan id sent by the browser without an ownership check.
- Other members are never shown balances, emails or phone numbers. Guarantor capacity is
  checked server-side and only a yes/no is revealed.
- Guarantor links are HMAC-signed with the site's encryption key and expire.
"""

import hashlib
import hmac
import json
import time

import frappe
from frappe import _
from frappe.utils import cint, cstr, flt, get_url, getdate, today
from frappe.utils.password import get_encryption_key

from sacco_manager.lending_utils import get_active_loans, loan_outstanding
from sacco_manager.utils import get_committed_pledges, get_multiplier, get_pledge_capacity, get_settings

MAX_LIMIT = 500


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------
def get_member_for_user():
	if frappe.session.user == "Guest":
		frappe.throw(_("Please log in."), frappe.AuthenticationError)
	if not cint(get_settings().get("portal_enabled") if get_settings().meta.has_field("portal_enabled") else 1):
		frappe.throw(_("The member portal is currently disabled."), frappe.PermissionError)
	member = frappe.db.get_value(
		"SACCO Member",
		{"user_id": frappe.session.user, "portal_user_created": 1},
		["name", "full_name", "first_name", "last_name", "branch", "status", "customer", "joining_date",
			"member_no", "email_id", "phone", "total_shares", "share_balance"],
		as_dict=True,
	)
	if not member:
		frappe.throw(_("No SACCO member record is linked to your login. Please contact the SACCO office."),
			frappe.PermissionError)
	if member.status not in ("Active",):
		frappe.throw(_("Your membership is {0}. Please contact the SACCO office.").format(member.status),
			frappe.PermissionError)
	return member


def fmt_kes(n):
	return f"KES {flt(n):,.0f}"


def _limit(n, default=100):
	return max(1, min(cint(n) or default, MAX_LIMIT))


def _member_accounts(member_name):
	return frappe.get_all(
		"SACCO Savings Account",
		filters={"member": member_name, "status": ["!=", "Closed"]},
		fields=["name", "account_no", "account_type", "balance", "status"],
		order_by="creation asc",
	)


# ---------------------------------------------------------------------------
# Guarantor tokens
# ---------------------------------------------------------------------------
def _sign(payload):
	return hmac.new(get_encryption_key().encode(), payload.encode(), hashlib.sha256).hexdigest()


def make_guarantor_token(loan_application, guarantor_member):
	days = cint(get_settings().get("guarantor_link_validity_days")) or 14
	exp = int(time.time()) + days * 86400
	return f"{exp}.{_sign(f'{loan_application}:{guarantor_member}:{exp}')}"


def verify_guarantor_token(loan_application, guarantor_member, token):
	try:
		exp_s, sig = cstr(token).split(".", 1)
		exp = int(exp_s)
	except ValueError:
		return False
	if exp < time.time():
		return False
	return hmac.compare_digest(sig, _sign(f"{loan_application}:{guarantor_member}:{exp}"))


# ---------------------------------------------------------------------------
# Portal summary
# ---------------------------------------------------------------------------
def _loan_view(loan):
	out = loan_outstanding(loan.name)
	lrs = frappe.db.get_value("Loan Repayment Schedule", {"loan": loan.name, "docstatus": 1, "status": "Active"}, "name")
	nxt = None
	if lrs:
		nxt = frappe.db.get_value(
			"Repayment Schedule",
			{"parent": lrs, "payment_date": [">=", today()]},
			["payment_date", "total_payment"],
			as_dict=True,
			order_by="payment_date asc",
		)
	return {
		"name": loan.name,
		"loan_type": loan.loan_product,
		"loan_amount": flt(loan.loan_amount),
		"total_amount_paid": flt(loan.total_amount_paid),
		"outstanding_amount": round(out["payable"], 2),
		"repayment_start_date": str(loan.repayment_start_date or ""),
		"repayment_periods": loan.repayment_periods,
		"rate_of_interest": flt(loan.rate_of_interest),
		"status": "Overdue" if cint(loan.days_past_due) > 0 else loan.status,
		"days_past_due": cint(loan.days_past_due),
		"next_payment_date": str(nxt.payment_date) if nxt else None,
		"monthly_repayment_amount": flt(nxt.total_payment) if nxt else flt(loan.monthly_repayment_amount),
	}


@frappe.whitelist()
def get_portal_summary():
	member = get_member_for_user()
	accounts = _member_accounts(member.name)
	total_savings = sum(flt(a.balance) for a in accounts)
	s = get_settings()
	share_price = flt(s.default_share_price)
	multiplier = get_multiplier()
	loans = [_loan_view(ln) for ln in get_active_loans(member.name)]
	outstanding = sum(flt(ln["outstanding_amount"]) for ln in loans)
	committed = sum(get_committed_pledges(a.name) for a in accounts)

	recent = []
	if accounts:
		for tx in frappe.get_all(
			"SACCO Savings Transaction",
			filters={"savings_account": ["in", [a.name for a in accounts]], "docstatus": 1},
			fields=["posting_date", "transaction_type", "amount", "mode_of_payment"],
			order_by="posting_date desc, creation desc",
			limit=4,
		):
			kind = {"Deposit": "deposit", "Withdrawal": "withdrawal"}.get(tx.transaction_type, "shares")
			recent.append({
				"type": kind,
				"description": f"Savings {tx.transaction_type.lower()} - {fmt_kes(tx.amount)}"
					+ (f" via {tx.mode_of_payment}" if tx.mode_of_payment else ""),
				"date": str(tx.posting_date),
			})

	mpesa = frappe.get_all("SACCO M-Pesa Account", filters={"enabled": 1},
		fields=["business_shortcode", "shortcode_type", "passkey"], limit=1)

	return {
		"member": {
			"name": member.name,
			"full_name": member.full_name,
			"first_name": member.first_name,
			"branch": member.branch,
			"status": member.status,
			"member_no": member.member_no,
			"email_id": member.email_id,
			"joining_date": str(member.joining_date) if member.joining_date else None,
		},
		"savings": accounts[0] if accounts else {},
		"savings_accounts": accounts,
		"savings_balance": total_savings,
		"total_savings_balance": total_savings,
		"savings_account": accounts[0].name if accounts else "",
		"total_shares": cint(member.total_shares),
		"share_value": flt(member.share_balance) or cint(member.total_shares) * share_price,
		"share_price": share_price,
		"active_loans": loans,
		"outstanding_loans": outstanding,
		"loan_limit": max(0, total_savings * multiplier - committed),
		"loan_multiplier": multiplier,
		"company": s.company,
		"pending_applications": frappe.db.count("Loan Application",
			{"applicant": member.customer, "status": "Open", "docstatus": ["!=", 2]}) if member.customer else 0,
		"recent_activity": recent,
		"mpesa": {
			"enabled": bool(mpesa),
			"paybill": mpesa[0].business_shortcode if mpesa else None,
			"stk_enabled": bool(mpesa and mpesa[0].passkey),
			"account_savings": member.member_no,
			"account_loan": f"{member.member_no}L",
			"account_shares": f"{member.member_no}S",
		},
	}


@frappe.whitelist()
def get_loan_types():
	fields = ["name", "product_name", "rate_of_interest"]
	filters = {"company": get_settings().company} if get_settings().company else {}
	if frappe.get_meta("Loan Product").has_field("disabled"):
		filters["disabled"] = 0
	products = frappe.get_all("Loan Product", filters=filters, fields=fields, order_by="product_name asc")
	return [{"name": p.name, "label": p.product_name or p.name, "rate_of_interest": flt(p.rate_of_interest)} for p in products]


@frappe.whitelist()
def get_savings_transactions(limit=100, offset=0):
	member = get_member_for_user()
	accounts = [a.name for a in _member_accounts(member.name)]
	if not accounts:
		return []
	rows = frappe.get_all(
		"SACCO Savings Transaction",
		filters={"savings_account": ["in", accounts], "docstatus": 1},
		fields=["name", "posting_date", "transaction_type", "amount", "savings_account", "balance_after",
			"reference_no", "reference_name", "remarks", "mode_of_payment"],
		order_by="posting_date desc, creation desc",
		limit=_limit(limit),
		start=cint(offset),
	)
	for r in rows:
		r["transaction_date"] = str(r.posting_date)
		r["running_balance"] = flt(r.balance_after)
		r["reference_name"] = r.reference_no or r.reference_name or r.name
		if r.transaction_type == "Withholding Tax":
			r["transaction_type"] = "Withdrawal"
			r["remarks"] = r.remarks or "Withholding tax"
	return rows


@frappe.whitelist()
def get_share_ledger(limit=100, offset=0):
	member = get_member_for_user()
	rows = frappe.get_all(
		"SACCO Share Ledger",
		filters={"member": member.name, "docstatus": 1},
		fields=["name", "date", "transaction_type", "shares", "total_amount", "reference_no"],
		order_by="date asc, creation asc",
	)
	running = 0
	out = []
	for r in rows:
		inc = r.transaction_type in ("Purchase", "Transfer In")
		running += cint(r.shares) if inc else -cint(r.shares)
		out.append({
			"name": r.name,
			"posting_date": str(r.date),
			"entry_type": "Purchase" if inc else "Redemption",
			"shares_quantity": cint(r.shares),
			"running_total": running,
			"remarks": r.reference_no or r.transaction_type,
		})
	out.reverse()
	start = cint(offset)
	return out[start:start + _limit(limit)]


@frappe.whitelist()
def get_loan_schedule(loan_name):
	member = get_member_for_user()
	if not member.customer or frappe.db.get_value("Loan", loan_name, "applicant") != member.customer:
		frappe.throw(_("Access denied"), frappe.PermissionError)
	lrs = frappe.db.get_value("Loan Repayment Schedule", {"loan": loan_name, "docstatus": 1, "status": "Active"}, "name")
	if not lrs:
		return []
	rows = frappe.get_all(
		"Repayment Schedule",
		filters={"parent": lrs, "parenttype": "Loan Repayment Schedule"},
		fields=["name", "payment_date", "principal_amount", "interest_amount", "total_payment", "balance_loan_amount"],
		order_by="payment_date asc",
	)
	dues = {
		d.repayment_schedule_detail: d
		for d in frappe.db.sql(
			"""SELECT repayment_schedule_detail, SUM(demand_amount) AS demanded, SUM(outstanding_amount) AS outstanding
			FROM `tabLoan Demand` WHERE loan=%s AND docstatus=1 AND IFNULL(repayment_schedule_detail,'')!=''
			GROUP BY repayment_schedule_detail""",
			loan_name,
			as_dict=True,
		)
	}
	for r in rows:
		d = dues.get(r.name)
		if d and flt(d.outstanding) <= 0.5:
			r["status"] = "Paid"
		elif d and flt(d.outstanding) < flt(d.demanded):
			r["status"] = "Partially Paid"
		elif getdate(r.payment_date) < getdate(today()):
			r["status"] = "Overdue"
		else:
			r["status"] = "Pending"
		r.pop("name")
	return rows


# ---------------------------------------------------------------------------
# Guarantor search: no balances, emails or phones of other members
# ---------------------------------------------------------------------------
@frappe.whitelist()
def search_members(query="", exclude=""):
	me = get_member_for_user()
	q = cstr(query).strip()
	if len(q) < 3:
		return []
	exclude_list = [e.strip() for e in cstr(exclude).split(",") if e.strip()] + [me.name]
	rows = frappe.db.sql(
		"""SELECT m.name, m.member_no, m.full_name, m.branch
		FROM `tabSACCO Member` m
		WHERE m.status='Active' AND m.name NOT IN %(ex)s
		  AND (m.member_no = %(exact)s OR m.full_name LIKE %(q)s)
		ORDER BY m.full_name LIMIT 10""",
		{"ex": tuple(exclude_list), "exact": q.upper(), "q": f"{q}%"},
		as_dict=True,
	)
	out = []
	for r in rows:
		accounts = frappe.get_all("SACCO Savings Account", filters={"member": r.name, "status": "Active"},
			pluck="name", order_by="creation asc")
		best = max(accounts, key=get_pledge_capacity) if accounts else None
		out.append({
			"name": r.name,
			"member_no": r.member_no,
			"full_name": r.full_name,
			"branch": r.branch,
			"savings_account": best or "",
			"savings_balance": None,  # never disclosed
			"can_guarantee": bool(best and get_pledge_capacity(best) > 0),
		})
	return [r for r in out if r["can_guarantee"]]


@frappe.whitelist()
def get_loan_applications():
	member = get_member_for_user()
	if not member.customer:
		return []
	apps = frappe.get_all(
		"Loan Application",
		filters={"applicant": member.customer, "docstatus": ["!=", 2]},
		fields=["name", "loan_product", "loan_amount", "repayment_periods", "rate_of_interest", "status", "posting_date"],
		order_by="posting_date desc",
		limit=50,
	)
	for a in apps:
		a["guarantors"] = frappe.get_all(
			"SACCO Loan Guarantor",
			filters={"loan_application": a.name},
			fields=["guarantor_member", "guarantor_name", "pledge_value", "status", "is_self", "responded_on"],
			order_by="is_self desc, creation asc",
		)
	return apps


# ---------------------------------------------------------------------------
# Loan application with guarantors
# ---------------------------------------------------------------------------
def _ensure_loan_security(savings_account):
	ls = frappe.db.get_value("Loan Security", {"sacco_member_saving": savings_account}, "name")
	if ls:
		return ls
	acc = frappe.get_doc("SACCO Savings Account", savings_account)
	acc.create_loan_security()
	return frappe.db.get_value("Loan Security", {"sacco_member_saving": savings_account}, "name")


@frappe.whitelist(methods=["POST"])
def submit_loan_application_v2(loan_product, loan_amount, repayment_periods, purpose, description="", guarantors="[]"):
	member = get_member_for_user()
	if not member.customer:
		frappe.throw(_("No loan account is linked to your membership. Please contact the office."))
	loan_amount = flt(loan_amount)
	periods = cint(repayment_periods)
	if loan_amount <= 0:
		frappe.throw(_("Loan amount must be greater than zero."))
	if not 1 <= periods <= 120:
		frappe.throw(_("Repayment period must be between 1 and 120 months."))
	product = frappe.db.get_value("Loan Product", loan_product, ["name", "rate_of_interest", "company"], as_dict=True)
	if not product:
		frappe.throw(_("Unknown loan product."))
	if frappe.db.exists("Loan Application", {"applicant": member.customer, "status": "Open", "docstatus": ["!=", 2]}):
		frappe.throw(_("You already have an open loan application. Please wait for it to be decided."))

	items = json.loads(guarantors) if isinstance(guarantors, str) else (guarantors or [])
	if not isinstance(items, list):
		frappe.throw(_("Invalid guarantor list."))

	# --- self pledge: always the member's own accounts, never what the browser says ---
	self_item = next((g for g in items if g.get("is_self")), None)
	others = [g for g in items if not g.get("is_self")]
	lines = []
	if self_item and flt(self_item.get("pledge_value")) > 0:
		own = [a.name for a in _member_accounts(member.name)]
		if not own:
			frappe.throw(_("You have no savings account to pledge."))
		best = max(own, key=get_pledge_capacity)
		cap = get_pledge_capacity(best)
		pledge = flt(self_item.get("pledge_value"))
		if pledge > cap + 0.5:
			frappe.throw(_("Your own pledge can be at most {0}.").format(fmt_kes(cap)))
		lines.append({"member": member.name, "member_name": member.full_name, "savings_account": best,
			"pledge_value": pledge, "is_self": 1})

	seen = {member.name}
	for g in others:
		gm = cstr(g.get("member"))
		pledge = flt(g.get("pledge_value"))
		if not gm or gm in seen:
			frappe.throw(_("Each guarantor can only be listed once."))
		seen.add(gm)
		info = frappe.db.get_value("SACCO Member", gm, ["status", "full_name"], as_dict=True)
		if not info or info.status != "Active":
			frappe.throw(_("Guarantor {0} is not an active member.").format(gm))
		if pledge <= 0:
			frappe.throw(_("Enter a pledge amount for {0}.").format(info.full_name))
		accounts = frappe.get_all("SACCO Savings Account", filters={"member": gm, "status": "Active"}, pluck="name")
		best = max(accounts, key=get_pledge_capacity) if accounts else None
		if not best or pledge > get_pledge_capacity(best) + 0.5:
			frappe.throw(_("{0} cannot guarantee that amount. Reduce the pledge or add another guarantor.").format(info.full_name))
		lines.append({"member": gm, "member_name": info.full_name, "savings_account": best,
			"pledge_value": pledge, "is_self": 0})

	total = sum(x["pledge_value"] for x in lines)
	if total + 0.5 < loan_amount:
		frappe.throw(_("Pledges total {0}, which does not cover the loan of {1}.").format(fmt_kes(total), fmt_kes(loan_amount)))

	pledges = []
	for x in lines:
		x["loan_security"] = _ensure_loan_security(x["savings_account"])
		if not x["loan_security"]:
			frappe.throw(_("Loan security could not be set up. Please contact the office."))
		pledges.append({"loan_security": x["loan_security"], "qty": 1, "loan_security_price": x["pledge_value"],
			"amount": x["pledge_value"]})

	summary = "\n".join(
		f"- {x['member_name']} | {x['savings_account']} | {fmt_kes(x['pledge_value'])}{' (self)' if x['is_self'] else ''}"
		for x in lines
	)
	app = frappe.get_doc({
		"doctype": "Loan Application",
		"applicant_type": "Customer",
		"applicant": member.customer,
		"company": product.company or get_settings().company,
		"posting_date": today(),
		"loan_product": product.name,
		"loan_amount": loan_amount,
		"is_term_loan": 1,
		"is_secured_loan": 1,
		"repayment_method": "Repay Over Number of Periods",
		"repayment_periods": periods,
		"rate_of_interest": flt(product.rate_of_interest),
		"description": f"Purpose: {cstr(purpose)[:140]}. {cstr(description)[:1000]}\n\nGuarantors (total {fmt_kes(total)}):\n{summary}",
		"status": "Open",
		"proposed_pledges": pledges,
	})
	app.flags.ignore_permissions = True
	app.insert()

	emails = 0
	for x in lines:
		row = frappe.get_doc({
			"doctype": "SACCO Loan Guarantor",
			"loan_application": app.name,
			"applicant_member": member.name,
			"guarantor_member": x["member"],
			"guarantor_name": x["member_name"],
			"savings_account": x["savings_account"],
			"pledge_value": x["pledge_value"],
			"multiplier": get_multiplier(),
			"is_self": x["is_self"],
			"loan_security": x["loan_security"],
			"status": "Confirmed" if x["is_self"] else "Pending",
			"responded_on": frappe.utils.now_datetime() if x["is_self"] else None,
			"remarks": "Self-pledge" if x["is_self"] else None,
		})
		row.flags.ignore_permissions = True
		row.insert()
		if not x["is_self"]:
			email = frappe.db.get_value("SACCO Member", x["member"], "email_id")
			if email:
				_send_guarantor_email(app.name, x, email, member.full_name, loan_amount, product.name)
				emails += 1

	return {
		"application": app.name,
		"emails_sent": emails,
		"message": _("Application {0} submitted. {1} guarantor request(s) sent.").format(app.name, emails),
	}


def _send_guarantor_email(application, x, email, applicant_name, loan_amount, product):
	token = make_guarantor_token(application, x["member"])
	url = get_url(f"/guarantor-confirm?app={application}&member={x['member']}&token={token}")
	days = cint(get_settings().get("guarantor_link_validity_days")) or 14
	frappe.sendmail(
		recipients=[email],
		subject=_("Action required: guarantee request {0}").format(application),
		template=None,
		message=f"""
<p>Dear {frappe.utils.escape_html(x['member_name'])},</p>
<p><strong>{frappe.utils.escape_html(applicant_name)}</strong> has asked you to guarantee a
{frappe.utils.escape_html(product)} loan of <strong>{fmt_kes(loan_amount)}</strong> by pledging
<strong>{fmt_kes(x['pledge_value'])}</strong> of your savings capacity.</p>
<p>While the loan is outstanding, part of your savings cannot be withdrawn, and if the borrower
defaults the SACCO may recover from your pledged savings.</p>
<p><a href="{url}" style="background:#1a3c2e;color:#fff;padding:12px 22px;border-radius:8px;text-decoration:none;font-weight:600;display:inline-block">Review and respond</a></p>
<p style="font-size:12px;color:#888">This link expires in {days} days. If you did not expect it, contact the SACCO office.</p>
""",
		reference_doctype="Loan Application",
		reference_name=application,
	)


@frappe.whitelist(methods=["POST"])
def submit_loan_application(loan_type, loan_amount, repayment_periods, purpose, description=""):
	"""Legacy single-borrower form: pledges the member's own savings only."""
	return submit_loan_application_v2(
		loan_product=loan_type, loan_amount=loan_amount, repayment_periods=repayment_periods,
		purpose=purpose, description=description,
		guarantors=json.dumps([{"is_self": True, "pledge_value": flt(loan_amount)}]),
	)


# ---------------------------------------------------------------------------
# Guarantor confirmation (guest, token protected)
# ---------------------------------------------------------------------------
def _guarantor_row(app, member, token):
	if not verify_guarantor_token(app, member, token):
		frappe.throw(_("This link is invalid or has expired. Ask the applicant or the SACCO office to resend it."),
			frappe.AuthenticationError)
	row = frappe.db.get_value(
		"SACCO Loan Guarantor",
		{"loan_application": app, "guarantor_member": member, "is_self": 0},
		["name", "status", "pledge_value", "savings_account", "multiplier", "applicant_member"],
		as_dict=True,
	)
	if not row:
		frappe.throw(_("No guarantee request found for this link."))
	return row


@frappe.whitelist(allow_guest=True)
def get_application_for_guarantor(app, member, token):
	row = _guarantor_row(app, member, token)
	la = frappe.db.get_value("Loan Application", app, ["loan_product", "loan_amount", "status"], as_dict=True)
	if not la:
		frappe.throw(_("Loan application not found."))
	return {
		"applicant_name": frappe.db.get_value("SACCO Member", row.applicant_member, "full_name") or "",
		"loan_product": la.loan_product,
		"loan_amount": flt(la.loan_amount),
		"savings_account": row.savings_account,
		"savings_balance": flt(frappe.db.get_value("SACCO Savings Account", row.savings_account, "balance")),
		"multiplier": flt(row.multiplier),
		"pledge_value": flt(row.pledge_value),
		"status": row.status,
		"application_status": la.status,
		"already_responded": row.status != "Pending" or la.status != "Open",
	}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def confirm_guarantor(app, member, token, action):
	row = _guarantor_row(app, member, token)
	if row.status != "Pending":
		return {"status": row.status, "message": _("You have already responded to this request.")}
	if frappe.db.get_value("Loan Application", app, "status") != "Open":
		return {"status": row.status, "message": _("This application is no longer open.")}
	action = cstr(action).lower()
	if action not in ("confirm", "decline"):
		frappe.throw(_("Invalid action."))
	if action == "confirm" and flt(row.pledge_value) > get_pledge_capacity(row.savings_account, exclude_application=app) + 0.5:
		frappe.throw(_("Your savings can no longer cover this pledge. Please contact the SACCO office."))
	new_status = "Confirmed" if action == "confirm" else "Declined"
	frappe.db.set_value("SACCO Loan Guarantor", row.name, {
		"status": new_status,
		"responded_on": frappe.utils.now_datetime(),
		"remarks": f"{new_status} via emailed link from {frappe.local.request_ip or 'unknown IP'}",
	})
	applicant_email = frappe.db.get_value("SACCO Member", row.applicant_member, "email_id")
	if applicant_email:
		name = frappe.db.get_value("SACCO Member", member, "full_name") or member
		frappe.sendmail(
			recipients=[applicant_email],
			subject=_("Guarantor {0} - {1}").format(new_status.lower(), app),
			message=_("{0} has {1} your guarantee request on application {2}.").format(
				frappe.utils.escape_html(name), new_status.lower(), app),
			reference_doctype="Loan Application",
			reference_name=app,
		)
	return {"status": new_status, "message": _("Thank you. Your response has been recorded.")}
