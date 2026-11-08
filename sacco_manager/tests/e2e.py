# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""End-to-end checks against a copy of the live database. Everything is rolled back.

    bench --site sacco.test execute sacco_manager.tests.e2e.run

Never run on production: it creates (then rolls back) members, postings and users.
"""

import pathlib
import traceback

import frappe
from frappe.utils import getdate, add_days, flt, today

RESULTS = []
CTX = frappe._dict()


def check(name):
	def deco(fn):
		def wrapper():
			frappe.set_user("Administrator")
			frappe.db.savepoint("e2e")
			try:
				detail = fn()
				RESULTS.append(("PASS", name, detail or ""))
			except Exception as e:
				frappe.db.rollback(save_point="e2e")
				RESULTS.append(("FAIL", name, f"{type(e).__name__}: {e}\n{traceback.format_exc()[-2500:]}"))
			finally:
				frappe.set_user("Administrator")
				frappe.local.message_log = []

		wrapper.__name__ = fn.__name__
		return wrapper

	return deco


def expect_error(fn, exc=Exception, contains=None):
	try:
		frappe.db.savepoint("expect")
		fn()
	except exc as e:
		frappe.db.rollback(save_point="expect")
		frappe.local.message_log = []
		if contains and contains.lower() not in str(e).lower():
			raise AssertionError(f"error raised but message was: {e}") from e
		return str(e)
	raise AssertionError("expected an error, none raised")


def je_balanced(name):
	je = frappe.get_doc("Journal Entry", name)
	assert je.docstatus == 1, f"{name} not submitted"
	assert abs(flt(je.total_debit) - flt(je.total_credit)) < 0.01, f"{name} unbalanced"
	return je


def make_user(email, roles, user_type="System User"):
	if frappe.db.exists("User", email):
		u = frappe.get_doc("User", email)
	else:
		u = frappe.get_doc({"doctype": "User", "email": email, "first_name": email.split("@")[0],
			"user_type": user_type, "send_welcome_email": 0, "enabled": 1})
		u.insert(ignore_permissions=True)
	u.add_roles(*roles)
	return email


# ---------------------------------------------------------------------------
@check("setup: accounts, M-Pesa account, users")
def t_setup():
	from sacco_manager.sacco_manager.doctype.sacco_settings.sacco_settings import create_missing_accounts

	s = frappe.get_doc("SACCO Settings")
	CTX.company = s.company
	created = create_missing_accounts()
	s.reload()
	abbr = frappe.get_cached_value("Company", s.company, "abbr")
	parent = frappe.db.get_value("Account", {"company": s.company, "account_type": "Bank", "is_group": 1}, "name") or \
		frappe.db.get_value("Account", "1201 - Guaranty Trust Bank - " + abbr, "parent_account")
	mp = f"M-Pesa Paybill - {abbr}"
	if not frappe.db.exists("Account", mp):
		frappe.get_doc({"doctype": "Account", "account_name": "M-Pesa Paybill", "company": s.company,
			"parent_account": parent, "account_type": "Bank", "is_group": 0}).insert(ignore_permissions=True)
	CTX.mpesa_gl = mp
	frappe.get_doc({"doctype": "SACCO M-Pesa Account", "title": "Test Paybill", "environment": "Sandbox",
		"shortcode_type": "Paybill", "business_shortcode": "600999", "gl_account": mp,
		"consumer_key": "x", "consumer_secret": "y", "passkey": "z", "enabled": 1}).insert(ignore_permissions=True)
	s.reload()
	s.interest_wht_rate = 15
	s.save()
	# go-live checklist item: tolerance so Lending can auto-close loans paid off to the cent
	for lp in frappe.get_all("Loan Product", pluck="name"):
		frappe.db.set_value("Loan Product", lp, "excess_amount_acceptance_limit", 10)
	CTX.teller = make_user("e2e.teller@example.com", ["SACCO Teller"])
	CTX.manager = make_user("e2e.manager@example.com", ["SACCO Manager"])
	CTX.accountant = make_user("e2e.accountant@example.com", ["SACCO Accountant"])
	CTX.auditor = make_user("e2e.auditor@example.com", ["SACCO Auditor"])
	return f"created accounts {created}; maker_checker={s.enforce_maker_checker}"


@check("settings guard rejects income account as remittance clearing")
def t_settings_guard():
	s = frappe.get_doc("SACCO Settings")
	s.branch_receivable_account = s.income_account
	return expect_error(s.save, contains="account")


@check("new member: phone normalised, own customer, savings account")
def t_member():
	m = frappe.get_doc({"doctype": "SACCO Member", "first_name": "Test", "last_name": "Mwangi",
		"id_number": "99887766", "phone": "0712 345 678", "email_id": "e2e.member@example.com",
		"branch": "DR. Plumber", "kra_pin": "A123456789Z"}).insert()
	assert m.phone == "254712345678", m.phone
	assert m.customer and frappe.db.get_value("Customer", m.customer, "sacco_member") in (None, m.name)
	m2 = frappe.get_doc({"doctype": "SACCO Member", "first_name": "Test", "last_name": "Mwangi",
		"id_number": "99887767", "phone": "0712345679"}).insert()
	assert m2.customer != m.customer, "same-name members must not share a customer"
	acc = frappe.get_doc({"doctype": "SACCO Savings Account", "member": m.name, "account_type": "Member Savings",
		"monthly_contribution": 2000, "interest_rate": 6}).insert()
	CTX.member, CTX.member_no, CTX.acc, CTX.member2 = m.name, m.member_no, acc.name, m2.name
	frappe.get_doc({"doctype": "SACCO Savings Account", "member": m2.name, "account_type": "Member Savings"}).insert()
	return f"{m.member_no} -> {m.customer}; {acc.name}"


@check("deposit posts balanced JE and updates balance")
def t_deposit():
	from sacco_manager.sacco_manager.doctype.sacco_savings_transaction.sacco_savings_transaction import (
		create_savings_transaction,
	)

	t = create_savings_transaction(CTX.acc, "Deposit", 50000, add_days(today(), -60), remarks="opening")
	je = je_balanced(t.journal_entry)
	bal = frappe.db.get_value("SACCO Savings Account", CTX.acc, "balance")
	assert flt(bal) == 50000, bal
	lines = {(a.account, flt(a.debit), flt(a.credit), a.party) for a in je.accounts}
	return f"balance {bal}; JE {je.name} {lines}"


@check("withdrawal: maker-checker and over-withdrawal blocked")
def t_withdrawal():
	frappe.set_user(CTX.teller)
	wr = frappe.get_doc({"doctype": "Withdrawal Request", "member": CTX.member, "savings_account": CTX.acc,
		"amount": 5000, "reason": "test", "payment_mode": "Bank"}).insert()
	assert wr.status == "Pending Approval"
	msg_perm = expect_error(wr.submit)  # teller has no submit right
	frappe.set_user("Administrator")
	frappe.db.set_value("Withdrawal Request", wr.name, "owner", CTX.manager)
	frappe.set_user(CTX.manager)
	wr = frappe.get_doc("Withdrawal Request", wr.name)
	expect_error(wr.submit, contains="segregation")
	frappe.set_user("Administrator")
	frappe.db.set_value("Withdrawal Request", wr.name, "owner", CTX.teller)
	frappe.set_user(CTX.manager)
	wr = frappe.get_doc("Withdrawal Request", wr.name)
	wr.submit()
	assert frappe.db.get_value("Withdrawal Request", wr.name, "status") == "Paid"
	frappe.set_user(CTX.teller)
	big = frappe.get_doc({"doctype": "Withdrawal Request", "member": CTX.member, "savings_account": CTX.acc,
		"amount": 10**7, "reason": "too much"})
	expect_error(big.insert, contains="withdrawable")
	return f"teller submit blocked: {msg_perm[:60]} | own approval blocked | over-withdraw blocked"


@check("auditor is read-only")
def t_auditor():
	frappe.set_user(CTX.auditor)
	assert frappe.has_permission("SACCO Savings Transaction", "read")
	assert not frappe.has_permission("SACCO Savings Transaction", "create")
	assert frappe.has_permission("GL Entry", "read")
	assert not frappe.has_permission("Journal Entry", "create")
	return "ok"


@check("portal: member summary, private search, fake self-pledge rejected")
def t_portal():
	from sacco_manager.api import sacco_portal as P
	from sacco_manager.sacco_manager.doctype.sacco_member.sacco_member import create_portal_user

	email = "e2e.member@example.com"
	m = frappe.get_doc("SACCO Member", CTX.member)
	make_user(email, [], user_type="Website User")  # pre-create: the test bench has no built assets for welcome mail
	m.is_user, m.user_id = 1, email
	m.save()
	create_portal_user(m.name)
	frappe.set_user(email)
	s = P.get_portal_summary()
	assert flt(s["savings_balance"]) == 45000, s["savings_balance"]
	res = P.search_members(query=frappe.db.get_value("SACCO Member", CTX.member2, "full_name")[:5])
	assert all(r.get("savings_balance") is None and "email_id" not in r for r in res), res
	other_acc = frappe.get_all("SACCO Savings Account", filters={"member": ["!=", CTX.member]},
		order_by="balance desc", pluck="name", limit=1)[0]
	import json

	err = expect_error(lambda: P.submit_loan_application_v2(
		"TSCCSL0001", 5_000_000, 12, "Business",
		guarantors=json.dumps([{"is_self": True, "savings_account": other_acc, "pledge_value": 5_000_000}])),
		contains="own pledge")
	out = P.submit_loan_application_v2("TSCCSL0001", 60000, 12, "Business",
		guarantors=json.dumps([{"is_self": True, "pledge_value": 60000}]))
	CTX.app = out["application"]
	g = frappe.get_all("SACCO Loan Guarantor", filters={"loan_application": CTX.app}, fields=["savings_account", "status"])
	assert g and g[0].savings_account == CTX.acc and g[0].status == "Confirmed", g
	assert P.get_loan_types(), "no loan products"
	frappe.set_user("Administrator")
	cap_after = __import__("sacco_manager.utils", fromlist=["x"]).get_withdrawable_balance(CTX.acc)
	return f"fake pledge -> '{err[:50]}'; app {CTX.app}; withdrawable now {cap_after} (45000 - 60000/3)"


@check("guarantor token: valid, tampered and expired")
def t_token():
	import time

	from sacco_manager.api import sacco_portal as P

	tok = P.make_guarantor_token("APP-1", "SCC-0001")
	assert P.verify_guarantor_token("APP-1", "SCC-0001", tok)
	assert not P.verify_guarantor_token("APP-1", "SCC-0002", tok)
	_exp, _sig = tok.split(".")
	old = f"{int(time.time()) - 10}.{P._sign(f'APP-1:SCC-0001:{int(time.time()) - 10}')}"
	assert not P.verify_guarantor_token("APP-1", "SCC-0001", old)
	return "ok"


@check("approval gate: only managers approve")
def t_approval():
	if not CTX.app:
		raise AssertionError("no application from portal test")
	frappe.set_user(make_user("e2e.credit@example.com", ["SACCO Credit Officer"]))
	app = frappe.get_doc("Loan Application", CTX.app)
	app.status = "Approved"
	app.save()  # status is permlevel 1: a credit officer's change is discarded
	assert frappe.db.get_value("Loan Application", CTX.app, "status") == "Open", "credit officer approved a loan"
	frappe.set_user(CTX.manager)
	app = frappe.get_doc("Loan Application", CTX.app)
	app.status = "Approved"
	app.save()
	assert frappe.db.get_value("Loan Application", CTX.app, "status") == "Approved", "manager could not approve"
	# a guarantee still pending blocks approval
	frappe.set_user("Administrator")
	frappe.db.set_value("Loan Application", CTX.app, "status", "Open")
	frappe.db.set_value("SACCO Loan Guarantor", {"loan_application": CTX.app}, "status", "Pending")
	frappe.set_user(CTX.manager)
	app = frappe.get_doc("Loan Application", CTX.app)
	app.status = "Approved"
	msg = expect_error(app.save, contains="waiting for")
	return f"credit officer cannot approve; manager can; pending guarantee blocks: {msg[:60]}"



@check("M-Pesa: savings by member no, loan by L suffix, unknown held")
def t_mpesa():
	from sacco_manager.mobile_money.allocation import allocate

	def txn(tid, amount, ref):
		return frappe.get_doc({"doctype": "SACCO M-Pesa Transaction", "transaction_id": tid, "receipt_number": tid,
			"direction": "Inbound", "channel": "C2B", "status": "Pending", "amount": amount, "mpesa_account": "600999",
			"bill_ref_number": ref, "transaction_time": frappe.utils.now_datetime(), "msisdn": "254700000000"}).insert(
			ignore_permissions=True).name

	frappe.set_user(CTX.accountant)
	r1 = allocate(txn("E2ETEST001", 1500, CTX.member_no.replace("-", "").lower()))
	d1 = frappe.get_doc("SACCO M-Pesa Transaction", "E2ETEST001")
	st = frappe.get_doc("SACCO Savings Transaction", d1.allocated_document)
	je = je_balanced(st.journal_entry)
	assert any(a.account == CTX.mpesa_gl and flt(a.debit) == 1500 for a in je.accounts)

	borrower = frappe.db.sql("""SELECT m.member_no, l.name FROM `tabLoan` l JOIN `tabSACCO Member` m ON m.customer=l.applicant
		WHERE l.docstatus=1 AND l.status='Disbursed' AND l.disbursed_amount - l.total_principal_paid > 5000 LIMIT 1""", as_dict=True)[0]
	r2 = allocate(txn("E2ETEST002", 3000, borrower.member_no + "L"))
	d2 = frappe.get_doc("SACCO M-Pesa Transaction", "E2ETEST002")
	rp = frappe.get_doc("Loan Repayment", d2.allocated_document) if d2.allocated_doctype == "Loan Repayment" else None
	assert r1 == "Allocated", r1
	assert r2 == "Allocated" and d2.allocated_doctype == "Loan Repayment", d2.allocation_note
	assert rp.payment_account == CTX.mpesa_gl, rp.payment_account
	r3 = allocate(txn("E2ETEST003", 700, "WRONGREF"))
	assert r3 == "Unallocated", r3
	from sacco_manager.mobile_money.api import allocate_manually

	allocate_manually("E2ETEST003", "Savings", CTX.member, note="phoned in")
	r3 += " -> manual " + frappe.db.get_value("SACCO M-Pesa Transaction", "E2ETEST003", "status")
	frappe.set_user("Administrator")
	CTX.borrower_loan = borrower.name
	return (f"savings:{r1} ({st.name}); loan:{r2} -> {d2.allocated_doctype} {d2.allocated_document} "
		f"paid_to={rp.payment_account if rp else '-'} note={d2.allocation_note}; unknown:{r3}")


@check("interest run on average daily balance with WHT")
def t_interest():
	from sacco_manager.sacco_manager.doctype.sacco_interest_run.sacco_interest_run import (
		average_daily_balance,
	)

	adb, closing = average_daily_balance(CTX.acc, add_days(today(), -90), today())
	run = frappe.get_doc({"doctype": "SACCO Interest Run", "period_start": add_days(today(), -90), "period_end": today(),
		"interest_rate": 6, "account_type": "Member Savings"})
	run.insert()
	mine = [d for d in run.details if d.savings_account == CTX.acc]
	assert mine, "member account missing from run"
	frappe.db.set_value("SACCO Interest Run", run.name, "owner", CTX.accountant)
	frappe.set_user(CTX.manager)
	run = frappe.get_doc("SACCO Interest Run", run.name)
	run.submit()
	frappe.set_user("Administrator")
	types = frappe.get_all("SACCO Savings Transaction", filters={"reference_name": run.name, "savings_account": CTX.acc},
		pluck="transaction_type")
	return f"ADB {adb:.2f} closing {closing}; interest {mine[0].interest_earned} wht {mine[0].wht_amount}; txns {types}; run total {run.total_interest:.2f} over {len(run.details)} accounts"


@check("shares purchase + dividend run (declaration JE, WHT, credit to savings)")
def t_dividend():
	from sacco_manager.sacco_manager.doctype.sacco_share_ledger.sacco_share_ledger import create_share_entry

	sh = create_share_entry(CTX.member, 2000, add_days(today(), -30), frappe.db.get_single_value("SACCO Settings", "cash_account"))
	je_balanced(sh.journal_entry)
	fy = frappe.db.get_value("Fiscal Year", {"year_start_date": ["<=", today()], "year_end_date": [">=", today()]}, "name")
	run = frappe.get_doc({"doctype": "SACCO Dividend Run", "financial_year": fy, "as_at_date": today(), "dividend_rate": 10,
		"posting_date": today(), "agm_resolution": "AGM/TEST"}).insert()
	frappe.db.set_value("SACCO Dividend Run", run.name, "owner", CTX.accountant)
	run = frappe.get_doc("SACCO Dividend Run", run.name)
	run.submit()
	je_balanced(run.journal_entry)
	row = next(d for d in run.details if d.member == CTX.member)
	return f"shares {sh.shares}; gross {row.gross_dividend} wht {row.wht_amount} net {row.net_dividend} -> {row.savings_transaction}"


@check("check-off remittance: receipt, member savings + loan repayments via clearing")
def t_remittance():
	r = frappe.get_doc({"doctype": "SACCO Branch Remittance", "branch": "DR. Plumber", "period_month": "October",
		"period_year": 2026})
	r.insert()
	r.fetch_members()
	r.reload()
	r.received_amount = r.expected_amount
	r.received_date = "2026-10-04"
	r.reference_no = "CHQ-TEST"
	r.save()
	frappe.db.set_value("SACCO Branch Remittance", r.name, "owner", CTX.accountant)
	frappe.set_user(CTX.manager)
	r = frappe.get_doc("SACCO Branch Remittance", r.name)
	r.submit()
	frappe.set_user("Administrator")
	clearing = frappe.db.get_single_value("SACCO Settings", "branch_receivable_account")
	frappe.db.sql("""SELECT SUM(debit-credit) FROM `tabGL Entry` WHERE account=%s AND is_cancelled=0
		AND voucher_no IN (SELECT name FROM `tabJournal Entry` WHERE cheque_no LIKE %s)""", (clearing, f"%{r.name}%"))[0][0]
	posted = sum(1 for m in r.members if frappe.db.get_value("SACCO Branch Remittance Member", m.name, "status") == "Posted")
	reps = sum(1 for m in r.members if frappe.db.get_value("SACCO Branch Remittance Member", m.name, "loan_repayment"))
	return f"{r.name}: {len(r.members)} members, {posted} posted, {reps} loan repayments, expected {r.expected_amount}, status {frappe.db.get_value(r.doctype, r.name, 'status')}"


@check("loan top-up as refinance closes old loan")
def t_topup():
	# Clean loan: legacy imported loans carry historic interest demands Lending will not settle on closure
	# (the loan-book rebuild issue from the audit), so test the mechanics on a freshly disbursed loan.
	customer = frappe.db.get_value("SACCO Member", CTX.member, "customer")
	ln = frappe.get_doc({"doctype": "Loan", "applicant_type": "Customer", "applicant": customer, "company": CTX.company,
		"loan_product": "TSCCSL0001", "loan_amount": 100000, "rate_of_interest": 12, "is_term_loan": 1,
		"repayment_method": "Repay Over Number of Periods", "repayment_periods": 12, "posting_date": today(),
		"repayment_start_date": frappe.utils.add_months(today(), 1)})
	ln.insert()
	ln.submit()
	d = frappe.get_doc({"doctype": "Loan Disbursement", "against_loan": ln.name, "applicant_type": "Customer",
		"applicant": customer, "company": CTX.company, "disbursement_date": today(), "posting_date": today(),
		"disbursed_amount": 100000})
	d.insert()
	d.submit()
	loan = ln.name
	t = frappe.get_doc({"doctype": "Loan Top-Up", "loan": loan, "top_up_amount": 50000, "top_up_date": today(),
		"repayment_method": "Repay Over Number of Periods", "new_tenure_months": 24}).insert()
	frappe.db.set_value("Loan Top-Up", t.name, "owner", make_user("e2e.credit@example.com", ["SACCO Credit Officer"]))
	frappe.set_user(CTX.manager)
	t = frappe.get_doc("Loan Top-Up", t.name)
	t.submit()
	frappe.set_user("Administrator")
	old_status = frappe.db.get_value("Loan", loan, "status")
	rp = frappe.get_doc("Loan Repayment", frappe.db.get_value("Loan Top-Up", t.name, "settlement_repayment"))
	CTX.dbg = {k: rp.get(k) for k in ("amount_paid", "payable_amount", "pending_principal_amount", "principal_amount_paid",
		"total_interest_paid", "interest_payable", "excess_amount", "shortfall_amount", "repayment_type", "docstatus")}
	assert old_status in ("Closed", "Loan Closure Requested", "Settled"), f"old loan still {old_status} {CTX.dbg}"
	clearing = frappe.db.get_single_value("SACCO Settings", "loan_topup_clearing_account")
	t.reload()
	bal = frappe.db.sql("""SELECT SUM(debit-credit) FROM `tabGL Entry` WHERE account=%s AND is_cancelled=0""", clearing)[0][0]
	assert abs(flt(bal)) < 0.01, f"top-up clearing not zero: {bal}"
	return (f"old {loan} -> {old_status}; payoff {t.settlement_amount}; new loan {t.new_loan} {t.new_principal}; "
		f"net cash {t.net_disbursement}; clearing balance {flt(bal)}")


@check("System Manager has full rights (own approval, loan status)")
def t_system_manager():
	sm = make_user("e2e.sysmgr@example.com", ["System Manager"])
	frappe.set_user(sm)
	wr = frappe.get_doc({"doctype": "Withdrawal Request", "member": CTX.member, "savings_account": CTX.acc,
		"amount": 1000, "reason": "sysmgr test", "payment_mode": "Bank"}).insert()
	wr.submit()  # same user creates and approves: allowed for System Manager
	assert frappe.db.get_value("Withdrawal Request", wr.name, "status") == "Paid"
	if CTX.app:
		frappe.set_user("Administrator")
		frappe.db.set_value("Loan Application", CTX.app, "status", "Open")
		frappe.db.set_value("SACCO Loan Guarantor", {"loan_application": CTX.app}, "status", "Confirmed")
		frappe.set_user(sm)
		app = frappe.get_doc("Loan Application", CTX.app)
		app.status = "Approved"
		app.save()
		assert frappe.db.get_value("Loan Application", CTX.app, "status") == "Approved", "permlevel 1 missing"
	for dt in ("Loan", "Loan Repayment", "Journal Entry", "Loan Demand", "SACCO Savings Transaction", "SACCO Dividend Run"):
		for pt in ("read", "write", "create", "delete"):
			assert frappe.has_permission(dt, pt), f"{dt} {pt}"
	return "own withdrawal approved; loan application approved; full rights on core doctypes"


@check("statement PDF, movement report, management and AGM packs")
def t_reporting():
	from sacco_manager import management_pack, statements
	from sacco_manager.sacco_manager.report.sacco_monthly_movement.sacco_monthly_movement import movement

	pdf = statements.statement_pdf(CTX.member, add_days(today(), -365), today())
	assert pdf[:4] == b"%PDF" and len(pdf) > 3000, len(pdf)
	mv = movement(add_days(today(), -400), today())
	sav_check = [r for r in mv if r["section"] == "Savings" and r["item"].startswith("GL ")]
	assert sav_check and sav_check[0]["note"] == "OK", sav_check
	m_pdf = management_pack.build_pdf(add_days(today(), -30), today(), "Test pack")
	a_pdf = management_pack.build_pdf("2026-01-01", "2026-10-31", "Test AGM", agm=True, dividend_rate=8)
	assert m_pdf[:4] == b"%PDF" and a_pdf[:4] == b"%PDF"
	url = management_pack.generate_management_pack(10, 2026, send=0)
	assert url and frappe.db.exists("File", {"file_url": url})
	(pathlib.Path("/tmp") / "e2e_pack.pdf").write_bytes(m_pdf)
	(pathlib.Path("/tmp") / "e2e_agm.pdf").write_bytes(a_pdf)
	(pathlib.Path("/tmp") / "e2e_statement.pdf").write_bytes(pdf)
	return f"statement {len(pdf)//1024} KB; savings roll-forward ties to GL; pack {len(m_pdf)//1024} KB; AGM {len(a_pdf)//1024} KB"


@check("defaulters report, number cards, data export, consent")
def t_misc_features():
	from frappe.desk.query_report import run

	from sacco_manager import dashboard, data_protection

	res = run("Defaulters and Guarantors", filters={"as_at": today(), "min_days": 30}, ignore_prepared_report=True)
	assert res.get("result") is not None
	cards = (dashboard.loan_book()["value"], dashboard.par30()["value"], dashboard.pending_approvals()["value"])
	data_protection.record_consent(CTX.member, "Signed form")
	assert frappe.db.get_value("SACCO Member", CTX.member, "consent_data_processing") == 1
	data_protection.export_member_data(CTX.member)
	assert frappe.local.response.filecontent and '"savings_transactions"' in frappe.local.response.filecontent
	dq = run("Member Data Quality", filters={"scope": "Current or with balance"}, ignore_prepared_report=True)
	dq_all = run("Member Data Quality", filters={"scope": "All members"}, ignore_prepared_report=True)
	return (f"defaulters {len(res['result'])}; cards loan book {cards[0]:,.0f} PAR30 {cards[1]}% pending {cards[2]}; "
		f"data quality in scope {len(dq['result'])} vs all {len(dq_all['result'])}")


@check("board approval required for large loans")
def t_board_approval():
	if not CTX.app:
		raise AssertionError("no application")
	frappe.db.set_single_value("SACCO Settings", "board_approval_loan_limit", 50000)
	frappe.clear_cache(doctype="SACCO Settings")
	frappe.db.set_value("Loan Application", CTX.app, "status", "Open")
	frappe.db.set_value("SACCO Loan Guarantor", {"loan_application": CTX.app}, "status", "Confirmed")
	frappe.set_user(CTX.manager)
	app = frappe.get_doc("Loan Application", CTX.app)
	app.status = "Approved"
	msg = expect_error(app.save, contains="board approval")
	frappe.set_user("Administrator")
	ba = frappe.get_doc({"doctype": "SACCO Board Approval", "approval_type": "Loan above limit", "subject": "Test loan",
		"decision": "Approved", "meeting_date": today(), "minute_ref": "MIN/TEST/1",
		"reference_doctype": "Loan Application", "reference_name": CTX.app}).insert()
	ba.submit()
	frappe.set_user(CTX.manager)
	app = frappe.get_doc("Loan Application", CTX.app)
	app.status = "Approved"
	app.save()
	return f"blocked without approval ('{msg[:45]}'), approved after {ba.name}"


@check("SMS notifications queue without errors")
def t_sms():
	from sacco_manager import notifications
	from sacco_manager.sacco_manager.doctype.sacco_savings_transaction.sacco_savings_transaction import (
		create_savings_transaction,
	)

	frappe.db.set_single_value("SACCO Settings", "sms_enabled", 1)
	frappe.clear_cache(doctype="SACCO Settings")
	sent = []
	orig = notifications.queue_sms
	notifications.queue_sms = lambda phone, message, reference=None: sent.append((phone, message))
	try:
		create_savings_transaction(CTX.acc, "Deposit", 777, today(), remarks="sms test")
		notifications.on_savings_transaction_submit(frappe.get_last_doc("SACCO Savings Transaction"))
	finally:
		notifications.queue_sms = orig
	assert sent and sent[0][0] == "254712345678" and "777" in sent[0][1], sent
	return sent[0][1]


@check("audit fixes: remittance due date, share transfer, cancel guard, control-account guard")
def t_audit_fixes():

	# 1. remittance for October is due on the 5th of November; on-time receipt has no penalty
	r = frappe.get_doc({"doctype": "SACCO Branch Remittance", "branch": "DR. Plumber", "period_month": "October",
		"period_year": 2026, "received_date": "2026-11-04", "received_amount": 1000})
	r.append("members", {"member": CTX.member, "savings_account": CTX.acc, "expected_amount": 1000, "received_savings": 1000})
	r.run_method("validate")
	assert str(r.due_date) == "2026-11-05" and not r.is_late, (r.due_date, r.is_late)
	r.received_date = "2026-11-20"
	r.run_method("validate")
	assert r.is_late and 0 < r.penalty_amount <= 100, r.penalty_amount  # capped at 10% of 1,000
	# 2. share transfer moves shares and member-level GL between two members in one JE
	from sacco_manager.sacco_manager.doctype.sacco_share_ledger.sacco_share_ledger import create_share_entry

	create_share_entry(CTX.member, 1000, today(), frappe.db.get_single_value("SACCO Settings", "cash_account"))
	out = frappe.get_doc({"doctype": "SACCO Share Ledger", "member": CTX.member, "transaction_type": "Transfer Out",
		"shares": 20, "date": today(), "transfer_member": CTX.member2}).insert()
	out.submit()
	je = frappe.get_doc("Journal Entry", out.journal_entry)
	parties = {(a.party, flt(a.debit), flt(a.credit)) for a in je.accounts}
	assert (CTX.member, 400.0, 0.0) in parties and (CTX.member2, 0.0, 400.0) in parties, parties
	assert frappe.db.get_value("SACCO Member", CTX.member2, "total_shares") == 20
	expect_error(lambda: frappe.get_doc({"doctype": "SACCO Share Ledger", "member": CTX.member, "transaction_type": "Redemption",
		"shares": 1, "date": today()}).insert(), contains="exits") if "System Manager" not in frappe.get_roles() else None
	# 3. a savings transaction created by a run cannot be cancelled on its own
	st = frappe.get_all("SACCO Savings Transaction", filters={"reference_doctype": ["is", "set"], "docstatus": 1,
		"member": CTX.member}, pluck="name", limit=1)
	if st:
		expect_error(frappe.get_doc("SACCO Savings Transaction", st[0]).cancel, contains="cancel or reverse")
	# 4. manual JE to members' savings is refused for non-System Managers
	frappe.set_user(CTX.accountant)
	sav = frappe.db.get_single_value("SACCO Settings", "savings_liability_account")
	cash = frappe.db.get_single_value("SACCO Settings", "cash_account")
	je2 = frappe.get_doc({"doctype": "Journal Entry", "company": CTX.company, "posting_date": today(), "accounts": [
		{"account": cash, "debit_in_account_currency": 10}, {"account": sav, "credit_in_account_currency": 10,
		"party_type": "SACCO Member", "party": CTX.member}]})
	msg = expect_error(je2.insert, contains="SACCO transactions")
	frappe.set_user("Administrator")
	return f"due {r.due_date}, penalty {r.penalty_amount}; transfer JE {je.name}; manual JE blocked: {msg[:50]}"


@check("member file print formats render with data")
def t_print_formats():
	html = frappe.get_print("SACCO Member", CTX.member, print_format="SACCO Member File")
	no = frappe.db.get_value("SACCO Member", CTX.member, "member_no")
	assert "Member File" in html and "KYC" in html and str(no) in html, "member file missing content"
	cust = frappe.db.get_value("SACCO Member", CTX.member, "customer")
	html2 = frappe.get_print("Customer", cust, print_format="SACCO Customer File")
	assert "Member File" in html2, "customer file did not resolve the member"
	return f"member file {len(html)} chars, customer file {len(html2)} chars"


@check("group-company lending: setup, board approval and exposure cap")
def t_group_company_loans():
	co = CTX.company
	prod = frappe.db.get_value("Loan Product", "GRPCO01", ["loan_account", "interest_income_account", "suspense_interest_income"], as_dict=True)
	assert prod and prod.loan_account.startswith("1620") and prod.interest_income_account.startswith("4130"), prod
	recv = frappe.db.get_value("Company", co, "default_receivable_account")
	assert recv not in frappe.get_all("Loan Product", pluck="loan_account"), f"company receivable is a loan account: {recv}"
	assert frappe.db.get_value("Customer", "Trident Plumbers Limited", "customer_group") == "Group Companies"
	member_prod = frappe.get_all("Loan Product", filters={"name": ["!=", "GRPCO01"]}, fields=["suspense_interest_income",
		"interest_income_account"], limit=1)[0]
	assert member_prod.suspense_interest_income != member_prod.interest_income_account, "NPA interest still goes to income"
	app = frappe.get_doc({"doctype": "Loan Application", "applicant_type": "Customer", "applicant": "Trident Plumbers Limited",
		"company": co, "loan_product": "GRPCO01", "loan_amount": 500000, "is_term_loan": 1,
		"repayment_method": "Repay Over Number of Periods", "repayment_periods": 12, "posting_date": today()})
	app.flags.ignore_mandatory = True
	app.insert()
	frappe.set_user(CTX.manager)
	app = frappe.get_doc("Loan Application", app.name)
	app.status = "Approved"
	msg = expect_error(app.save, contains="group company")
	frappe.set_user("Administrator")
	ba = frappe.get_doc({"doctype": "SACCO Board Approval", "approval_type": "Loan above limit", "subject": "Group company loan",
		"decision": "Approved", "meeting_date": today(), "minute_ref": "MIN/TEST/GC", "reference_doctype": "Loan Application",
		"reference_name": app.name}).insert()
	ba.submit()
	frappe.db.set_single_value("SACCO Settings", "group_company_exposure_limit", 0.001)
	frappe.set_user(CTX.manager)
	app = frappe.get_doc("Loan Application", app.name)
	app.status = "Approved"
	cap = expect_error(app.save, contains="limit")
	frappe.set_user("Administrator")
	frappe.db.set_single_value("SACCO Settings", "group_company_exposure_limit", 0)
	frappe.set_user(CTX.manager)
	app = frappe.get_doc("Loan Application", app.name)
	app.status = "Approved"
	app.save()
	frappe.set_user("Administrator")
	return f"blocked ('{msg[:40]}'), capped ('{cap[:40]}'), approved with {ba.name}"


@check("board dashboard and AGM data build")
def t_analytics():
	from sacco_manager import analytics

	b = analytics.get_board()
	assert b["now"]["ratios"] and len(b["trends"]["labels"]) == 24, "board data incomplete"
	g = analytics.get_agm(year=getdate(today()).year, savings_rate=5, dividend_rate=10)
	assert "appropriation" in g and g["balance_sheet"]["Asset"] is not None
	frappe.set_user(CTX.teller)
	expect_error(analytics.get_board, contains="administrators")
	frappe.set_user("Administrator")
	r = {x["key"]: x["value"] for x in b["now"]["ratios"]}
	return f"{len(b['now']['ratios'])} ratios (liquidity {r.get('liquidity')}%, PAR30 {r.get('par30')}%), AGM surplus {g['surplus']:,.0f}"


@check("housekeeping: arrears-log pruning preview and version tracking off")
def t_housekeeping():
	from sacco_manager import housekeeping

	pv = housekeeping.dpd_prune_preview()
	assert pv["enabled"] and pv["removable"] <= pv["rows"], pv
	assert not frappe.get_meta("Loan Interest Accrual").track_changes, "accrual versions still tracked"
	r = housekeeping.storage_report()
	return f"{pv['removable']:,} of {pv['rows']:,} arrears-log rows removable; {r['cancelled_gl_rows']:,} cancelled GL rows"


@check("employer facility: Board approval required; deductions kept by the company become a drawdown")
def t_employer_facility():
	from sacco_manager.sacco_manager.doctype.sacco_employer.sacco_employer import create_facility

	emp = frappe.db.get_value("SACCO Employer", {"branch": "DR. Plumber"}, "name")
	assert emp and frappe.db.exists("Loan Product", "GRPFAC01"), "employer/facility product missing"
	frappe.db.set_value("SACCO Employer", emp, {"facility_limit": 2000000, "facility_rate": 10})
	msg = expect_error(lambda: create_facility(emp), contains="Board Approval")
	ba = frappe.get_doc({"doctype": "SACCO Board Approval", "approval_type": "Loan above limit", "subject": "Doctor Plumber facility",
		"decision": "Approved", "meeting_date": today(), "minute_ref": "MIN/TEST/FAC", "amount": 2000000,
		"reference_doctype": "SACCO Employer", "reference_name": emp}).insert()
	ba.submit()
	loan = create_facility(emp)
	r = frappe.get_doc({"doctype": "SACCO Branch Remittance", "branch": "DR. Plumber", "period_month": "November",
		"period_year": 2026, "received_amount": 1000, "received_date": "2026-12-20",
		"settlement": "Kept by the company as a facility drawdown"})
	r.append("members", {"member": CTX.member, "savings_account": CTX.acc, "expected_amount": 1000, "received_savings": 1000})
	r.insert()
	assert not r.is_late and not r.penalty_amount, "kept deductions must not be charged a late penalty"
	frappe.db.set_value("SACCO Branch Remittance", r.name, "owner", CTX.accountant)
	frappe.set_user(CTX.manager)
	r = frappe.get_doc("SACCO Branch Remittance", r.name)
	r.submit()
	frappe.set_user("Administrator")
	dis = frappe.db.get_value("SACCO Branch Remittance", r.name, "facility_disbursement")
	assert dis, "no facility drawdown"
	clearing = frappe.db.get_single_value("SACCO Settings", "branch_receivable_account")
	gl = {(g.account[:4], flt(g.debit), flt(g.credit)) for g in frappe.get_all("GL Entry", filters={"voucher_no": dis, "is_cancelled": 0},
		fields=["account", "debit", "credit"])}
	assert ("1620", 1000.0, 0.0) in gl and (clearing[:4], 0.0, 1000.0) in gl, gl
	assert flt(frappe.db.get_value("Loan", loan, "disbursed_amount")) == 1000
	return f"blocked without approval ('{msg[:35]}'); facility {loan}; drawdown {dis} Dr 1620 / Cr clearing"


class FakeERP:
	"""In-memory stand-in for a company's ERPNext."""

	def __init__(self):
		self.adds, self.slips = {}, {}

	def get_list(self, doctype, filters, fields, limit=0):
		if doctype == "Additional Salary":
			emp = next(f[2] for f in filters if f[0] == "employee")
			return [v for v in self.adds.values() if v["employee"] == emp]
		return [{"name": n, "employee": s["employee"]} for n, s in self.slips.items()]

	def get_doc(self, doctype, name):
		return self.slips[name]

	def insert(self, doctype, doc):
		name = f"HR-ADS-{len(self.adds) + 1}"
		self.adds[name] = {**doc, "name": name, "docstatus": 0}
		return self.adds[name]

	def update(self, doctype, name, values):
		self.adds[name].update(values)
		return self.adds[name]


@check("employer ERPNext: deductions pushed to payroll and payslips read back into a remittance")
def t_employer_erp():
	from sacco_manager import employer_erp

	emp = frappe.get_doc("SACCO Employer", {"branch": "DR. Plumber"})
	frappe.db.set_value("SACCO Member", CTX.member, "employee_id", "E2E-EMP-1")
	fake = FakeERP()
	frappe.flags.sacco_erp_client = fake
	try:
		out = employer_erp.push_deductions(emp.name, "December", 2026)
		mine = [a for a in fake.adds.values() if a["employee"] == "E2E-EMP-1"]
		expected = next(r.total for r in employer_erp.deduction_schedule("DR. Plumber") if r.member == CTX.member)
		assert out["created"] >= 1 and mine and flt(mine[0]["amount"]) == expected, (out, mine, expected)
		again = employer_erp.push_deductions(emp.name, "December", 2026)
		assert again["created"] == 0, "second push must update, not duplicate"
		comp = emp.salary_component
		fake.slips = {"SAL-1": {"employee": "E2E-EMP-1", "deductions": [{"salary_component": comp, "amount": 1500}]},
			"SAL-2": {"employee": "NOT-A-MEMBER", "deductions": [{"salary_component": comp, "amount": 300},
				{"salary_component": "PAYE", "amount": 9999}]}}
		res = employer_erp.pull_deductions(emp.name, "December", 2026)
	finally:
		frappe.flags.sacco_erp_client = None
	rem = frappe.get_doc("SACCO Branch Remittance", res["remittance"])
	got = sum(flt(r.received_savings) + flt(r.received_loan_repayment) for r in rem.members if r.member == CTX.member)
	assert flt(rem.received_amount) == 1800 and abs(got - 1500) < 0.01 and "NOT-A-MEMBER" in res["unmatched"], (rem.received_amount, got, res)
	return f"pushed {out['created']} drafts; read 2 slips -> {rem.name} {rem.received_amount}; member got {got}; 1 unmatched"


@check("online membership application: submit, duplicate blocked, approve creates member, reject")
def t_membership():
	import base64
	import json

	from sacco_manager import membership
	from sacco_manager.sacco_manager.doctype.sacco_membership_application import sacco_membership_application as A

	png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 64).decode()
	data = {"first_name": "Wanjiku", "last_name": "Testapplicant", "id_number": "E2E99887766", "phone": "0712000111",
		"email": "wanjiku.e2e@example.com", "employer": "DR. Plumber", "employee_id": "E2E-EMP-2", "monthly_contribution": 1500,
		"consent": 1, "kin_name": "Kamau", "kin_phone": "0712000222", "address": "Box 1, Industrial Area"}
	frappe.set_user("Guest")
	out = membership.create_application(json.dumps(data), json.dumps([{"field": "photo", "filename": "me.png",
		"content": "data:image/png;base64," + png}]))
	hp = membership.create_application(json.dumps({**data, "id_number": "E2E1", "website": "spam"}))
	frappe.set_user("Administrator")
	assert hp["name"] is None, "honeypot accepted"
	app = frappe.get_doc("SACCO Membership Application", out["name"])
	assert app.status == "Submitted" and app.source == "Online" and app.photo and app.phone == "254712000111", app.as_dict()
	expect_error(lambda: membership.create_application(json.dumps(data)), contains="already being processed")
	frappe.set_user(CTX.teller)
	expect_error(lambda: A.approve(app.name), contains="")
	frappe.set_user(CTX.manager)
	res = A.approve(app.name)
	frappe.set_user("Administrator")
	m = frappe.get_doc("SACCO Member", res["member"])
	acc = frappe.get_all("SACCO Savings Account", filters={"member": m.name}, fields=["monthly_contribution"])
	files = frappe.db.count("File", {"attached_to_doctype": "SACCO Member", "attached_to_name": m.name})
	assert m.consent_data_processing and m.branch == "DR. Plumber" and m.employee_id == "E2E-EMP-2" and m.contact_name == "Kamau"
	assert acc and flt(acc[0].monthly_contribution) == 1500 and files >= 1, (acc, files)
	app2 = membership.create_application(json.dumps({**data, "id_number": "E2E55554444", "phone": "0712000333"}))
	frappe.set_user(CTX.manager)
	A.reject(app2["name"], "Not on the payroll")
	frappe.set_user("Administrator")
	assert frappe.db.get_value("SACCO Membership Application", app2["name"], "status") == "Rejected"
	return f"{app.name} -> member {res['member_no']} with savings account and {files} document(s); {app2['name']} rejected"


@check("guarantors: request, partial accept, decline/replace, coverage rule on approval")
def t_guarantors():
	import json

	from sacco_manager import guarantors as G
	from sacco_manager.api import sacco_portal as P
	from sacco_manager.sacco_manager.doctype.sacco_member.sacco_member import create_portal_user
	from sacco_manager.sacco_manager.doctype.sacco_savings_transaction.sacco_savings_transaction import (
		create_savings_transaction,
	)

	acc2 = frappe.get_all("SACCO Savings Account", filters={"member": CTX.member2}, pluck="name")[0]
	create_savings_transaction(acc2, "Deposit", 50000, today(), payment_account=frappe.db.get_single_value("SACCO Settings", "cash_account"))
	email2 = "e2e.member2@example.com"
	make_user(email2, [], user_type="Website User")
	frappe.db.set_value("SACCO Member", CTX.member2, {"is_user": 1, "user_id": email2})
	create_portal_user(CTX.member2)
	frappe.set_user("e2e.member@example.com")
	app = P.submit_loan_application_v2("TSCCSL0001", 20000, 12, "School fees",
		guarantors=json.dumps([{"is_self": True, "pledge_value": 20000}]))["application"]
	req = G.request_guarantee(app, CTX.member2, 8000)
	frappe.set_user(email2)
	mine = G.my_guarantees()
	assert any(r["name"] == req["row"] for r in mine["requests"]), mine
	ok = G.respond(req["row"], "accept", 5000)
	assert ok["status"] == "Confirmed" and flt(frappe.db.get_value("SACCO Loan Guarantor", req["row"], "pledge_value")) == 5000
	frappe.set_user("e2e.member@example.com")
	expect_error(lambda: G.request_guarantee(app, CTX.member2, 1000), contains="already a guarantor")
	frappe.set_user("Administrator")
	# a declined guarantor no longer blocks approval when the confirmed pledges cover the loan
	frappe.db.set_value("SACCO Loan Guarantor", req["row"], "status", "Declined")
	self_row = frappe.db.get_value("SACCO Loan Guarantor", {"loan_application": app, "is_self": 1}, "name")
	frappe.db.set_value("SACCO Loan Guarantor", self_row, "pledge_value", 10000)
	frappe.set_user(CTX.manager)
	la = frappe.get_doc("Loan Application", app)
	la.status = "Approved"
	short = expect_error(la.save, contains="confirmed guarantees cover")
	frappe.set_user("Administrator")
	frappe.db.set_value("SACCO Loan Guarantor", self_row, "pledge_value", 20000)
	frappe.set_user(CTX.manager)
	la = frappe.get_doc("Loan Application", app)
	la.status = "Approved"
	la.save()
	frappe.set_user("Administrator")
	return f"{app}: requested 8,000, accepted 5,000; short cover blocked ('{short[:40]}'); approved once covered"


@check("member login: phone or email resolves only portal members; one-time code verifies once")
def t_member_login():
	from sacco_manager import member_login as L

	user = "e2e.member@example.com"
	phone = frappe.db.get_value("SACCO Member", CTX.member, "phone")
	assert L.resolve_user("0" + phone[3:]) == user and L.resolve_user("+" + phone) == user and L.resolve_user(user) == user
	assert L.resolve_user(CTX.manager) is None, "desk users must not log in through the member page"
	frappe.db.set_single_value("SACCO Settings", "member_login_otp", 1)
	assert "If an account matches" in L.send_login_code("0" + phone[3:], "email")["message"]
	assert L.send_login_code("0700000999", "email")["message"], "unknown numbers get the same answer"
	expect_error(lambda: L.verify_login_code(user, "abcdef"), contains="wrong")
	frappe.cache.set_value(L._key(user), {"h": L._hash(user, "123456"), "tries": 0}, expires_in_sec=300)

	class LM:
		user = None

		def login_as(self, u):
			self.user = u

	old = getattr(frappe.local, "login_manager", None)
	frappe.local.login_manager = LM()
	try:
		r = L.verify_login_code(phone, "123 456")
		logged = frappe.local.login_manager.user
	finally:
		frappe.local.login_manager = old
	assert r["redirect"] == "/sacco-portal" and logged == user
	assert not frappe.cache.get_value(L._key(user)), "code must work only once"
	return "phone/email resolve; staff refused; code works once"


@check("reports run")
def t_reports():
	from frappe.desk.query_report import run

	out = []
	for name, filters in [
		("Member Statement", {"member": CTX.member, "from_date": add_days(today(), -365), "to_date": today()}),
		("Savings Balances", {"as_at": today()}),
		("Loan Portfolio at Risk", {"as_at": today()}),
		("M-Pesa Reconciliation", {"from_date": add_days(today(), -2), "to_date": add_days(today(), 1)}),
		("Guarantor Exposure", {}),
		("SACCO Prudential Summary", {"as_at": today()}),
		("Member Data Quality", {}),
		("Defaulters and Guarantors", {"as_at": today(), "min_days": 30}),
		("SACCO Monthly Movement", {"from_date": add_days(today(), -30), "to_date": today()}),
		("Branch Remittance", {"year": 2026}),
		("Member Overview", {"month": 6, "year": 2026}),
		("Member Overview", {}),
	]:
		res = run(name, filters=filters, ignore_prepared_report=True)
		out.append(f"{name}: {len(res.get('result') or [])} rows")
	par = run("Loan Portfolio at Risk", filters={"as_at": today()}, ignore_prepared_report=True)
	CTX.par = par.get("report_summary")
	return "; ".join(out) + f" | PAR summary {[(s['label'], round(flt(s['value']), 2)) for s in CTX.par]}"


def run():
	frappe.flags.mute_emails = True
	frappe.flags.in_test = True
	try:
		for t in [t_setup, t_settings_guard, t_member, t_deposit, t_withdrawal, t_auditor, t_portal, t_token,
				t_approval, t_mpesa, t_interest, t_dividend, t_remittance, t_topup, t_system_manager, t_reporting, t_misc_features,
				t_board_approval, t_sms, t_audit_fixes, t_print_formats, t_group_company_loans, t_analytics, t_housekeeping,
				t_employer_facility, t_employer_erp, t_membership, t_guarantors, t_member_login, t_reports]:
			t()
	finally:
		frappe.db.rollback()
		frappe.flags.in_test = False
	for status, name, detail in RESULTS:
		print(f"[{status}] {name}\n    {detail}")
	print(f"\n{sum(1 for r in RESULTS if r[0] == 'PASS')}/{len(RESULTS)} passed (all changes rolled back)")


def debug_approval():
	frappe.flags.mute_emails = True
	frappe.flags.in_test = True
	for fn in (t_setup, t_member, t_deposit, t_withdrawal, t_portal):
		fn()
	print([r[:2] for r in RESULTS], CTX.app)
	from sacco_manager.overrides.loan_application import validate as gate

	u = make_user("e2e.credit@example.com", ["SACCO Credit Officer"])
	frappe.set_user(u)
	app = frappe.get_doc("Loan Application", CTX.app)
	print("draft status", app.status, app.docstatus, frappe.get_roles())
	app.status = "Approved"
	app._doc_before_save = frappe.get_doc("Loan Application", CTX.app)
	try:
		gate(app)
		print("gate: no error")
	except Exception as e:
		print("gate raised", type(e).__name__, e)
	app = frappe.get_doc("Loan Application", CTX.app)
	app.status = "Approved"
	try:
		app.save()
		print("save: no error; db status", frappe.db.get_value("Loan Application", CTX.app, "status"))
	except Exception as e:
		print("save raised", type(e).__name__, e)
	frappe.db.rollback()


def debug_closure():
	from lending.loan_management.doctype.loan_repayment.loan_repayment import calculate_amounts

	loan = frappe.db.sql("""SELECT l.name FROM `tabLoan` l WHERE l.docstatus=1 AND l.status='Disbursed'
		AND l.disbursed_amount - l.total_principal_paid > 20000 LIMIT 2""")[0][0]
	for vd in (today(), frappe.utils.now_datetime()):
		a = calculate_amounts(loan, vd, payment_type="Loan Closure", for_update=True)
		print(loan, vd, {k: round(flt(v), 2) for k, v in a.items() if isinstance(v, (int, float)) and v})
	rp = frappe.new_doc("Loan Repayment")
	rp.update({"against_loan": loan, "applicant_type": "Customer", "applicant": frappe.db.get_value("Loan", loan, "applicant"),
		"company": frappe.db.get_value("Loan", loan, "company"), "posting_date": today(), "value_date": today(),
		"repayment_type": "Loan Closure", "amount_paid": 10**7, "payment_account": frappe.db.get_single_value("SACCO Settings", "cash_account")})
	rp.flags.ignore_permissions = True
	rp.insert()
	print("excess", rp.excess_amount, "unbooked", rp.unbooked_interest_paid, "dpd", rp.days_past_due)
	print("lending computed payable_amount", rp.payable_amount, "pending_principal", rp.pending_principal_amount,
		"interest", rp.interest_payable, "penalty", rp.penalty_amount, "charges", rp.total_charges_payable)
	frappe.db.rollback()


def debug_topup():
	frappe.flags.mute_emails = True
	frappe.flags.in_test = True
	import sacco_manager.lending_utils as LU

	orig = LU.create_loan_repayment

	def spy(loan, amount, *a, **k):
		from lending.loan_management.doctype.loan_repayment.loan_repayment import calculate_amounts

		print("spy", loan, "amount", amount, "type", k.get("repayment_type"), "user", frappe.session.user,
			"payoff_now", calculate_amounts(loan, a[0], payment_type="Loan Closure").get("payable_amount"))
		return orig(loan, amount, *a, **k)

	import sacco_manager.sacco_manager.doctype.loan_top_up.loan_top_up as TU

	TU.create_loan_repayment = spy
	t_setup()
	t_topup()
	print(RESULTS[-1][0], RESULTS[-1][2][:1500])
	frappe.db.rollback()


def callback_fixture():
	"""Persistent (committed) M-Pesa test account on the TEST site for HTTP callback tests."""
	abbr = frappe.get_cached_value("Company", frappe.db.get_single_value("SACCO Settings", "company"), "abbr")
	mp = f"M-Pesa Paybill - {abbr}"
	if not frappe.db.exists("Account", mp):
		parent = frappe.db.get_value("Account", f"1201 - Guaranty Trust Bank - {abbr}", "parent_account")
		frappe.get_doc({"doctype": "Account", "account_name": "M-Pesa Paybill", "company": frappe.db.get_single_value("SACCO Settings", "company"),
			"parent_account": parent, "account_type": "Bank", "is_group": 0}).insert(ignore_permissions=True)
	if not frappe.db.exists("SACCO M-Pesa Account", "600888"):
		frappe.get_doc({"doctype": "SACCO M-Pesa Account", "title": "Callback test", "environment": "Sandbox",
			"shortcode_type": "Paybill", "business_shortcode": "600888", "gl_account": mp,
			"consumer_key": "x", "consumer_secret": "y", "enabled": 1}).insert(ignore_permissions=True)
	frappe.db.commit()
	acc = frappe.get_doc("SACCO M-Pesa Account", "600888")
	print("TOKEN=" + acc.get_password("callback_token"))
	print("MEMBERNO=" + frappe.db.get_value("SACCO Member", {"status": "Active"}, "member_no"))


def show_http_txns():
	for t in frappe.get_all("SACCO M-Pesa Transaction", filters={"name": ["like", "HTTPTEST%"]},
			fields=["name", "status", "member", "allocation_note"]):
		print(t)
		print(frappe.get_all("SACCO Savings Account", filters={"member": t.member}, fields=["name", "status"]))


def data_snapshot():
	"""Post-migration data facts for the report (read-only)."""
	m = frappe.get_all("SACCO Member", fields=["phone", "id_number", "email_id", "branch"])
	from sacco_manager.utils import normalize_ke_phone

	print("members", len(m), "valid KE mobile", sum(1 for r in m if normalize_ke_phone(r.phone) == r.phone),
		"no email", sum(1 for r in m if not r.email_id), "no branch", sum(1 for r in m if not r.branch))
	print("settings", {k: frappe.db.get_single_value("SACCO Settings", k) for k in (
		"company", "branch_receivable_account", "enforce_maker_checker", "interest_expense_account", "share_capital_account")})
	print("custom perms on SACCO doctypes", frappe.db.count("Custom DocPerm", {"parent": ["like", "SACCO%"]}))
	print("withdrawals", frappe.get_all("Withdrawal Request", fields=["name", "status", "docstatus"]))
	print("bogus demands still submitted", frappe.db.count("Loan Demand", {"demand_type": "Additional Interest", "docstatus": 1}))
	print("delinquency job", frappe.db.exists("Scheduled Job Type", {"method": ["like", "%delinquency%"]}))


def phone_sample():
	from collections import Counter

	from sacco_manager.utils import normalize_ke_phone

	bad = [r.phone for r in frappe.get_all("SACCO Member", fields=["phone"]) if normalize_ke_phone(r.phone) != r.phone]
	print(len(bad), Counter(len(str(b or "")) for b in bad), [str(b)[:3] + "***" for b in bad[:12]])
