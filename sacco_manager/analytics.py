# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Numbers behind the "SACCO Board" live dashboard and the "SACCO AGM" presentation.

Everything is read from the ledger and the SACCO sub-ledgers at request time, so the pages are
always live. Ratios reuse the Prudential Summary and Portfolio-at-Risk reports so the board sees
the same figures as the reports and the auditor."""

import frappe
from frappe import _
from frappe.utils import add_days, add_months, cint, flt, get_last_day, getdate, today

from sacco_manager.utils import get_company, get_settings

BOARD_ROLES = ("System Manager", "SACCO Administrator", "SACCO Manager", "SACCO Auditor")
OPEN_LOAN = ("Disbursed", "Partially Disbursed", "Active")
DEBIT_TYPES = ("Withdrawal", "Withholding Tax")


def history_from():
	"""First date the books are complete (SACCO Settings > Books Complete From). Year-on-year
	comparisons before it would compare against half-migrated books, so they are hidden."""
	d = frappe.db.get_single_value("SACCO Settings", "books_complete_from")
	d = getdate(d) if d else None
	return d if d and d.year > 1900 else None  # an empty Date single reads back as 0001-01-01


def _check():
	if not set(BOARD_ROLES) & set(frappe.get_roles()):
		frappe.throw(_("The SACCO board pages are for administrators, managers and auditors."), frappe.PermissionError)


def _one(sql, vals=None):
	return flt(frappe.db.sql(sql, vals or {})[0][0])


# ---------------------------------------------------------------------------
# point-in-time figures
# ---------------------------------------------------------------------------
def _gl(where, vals):
	return _one(f"""SELECT SUM(g.debit - g.credit) FROM `tabGL Entry` g JOIN `tabAccount` a ON a.name = g.account
		WHERE g.is_cancelled = 0 AND g.company = %(company)s AND g.posting_date <= %(t)s AND {where}""", vals)


def _flow(where, f, t):
	"""Income (positive) / expense (positive) for a period."""
	return _one(f"""SELECT SUM(g.credit - g.debit) FROM `tabGL Entry` g JOIN `tabAccount` a ON a.name = g.account
		WHERE g.is_cancelled = 0 AND g.company = %(company)s AND g.posting_date BETWEEN %(f)s AND %(t)s AND {where}""",
		{"company": get_company(), "f": f, "t": t})


def _savings_at(t):
	return _one(f"""SELECT SUM(CASE WHEN transaction_type IN {DEBIT_TYPES} THEN -amount ELSE amount END)
		FROM `tabSACCO Savings Transaction` WHERE docstatus = 1 AND posting_date <= %s""", t)


def _loan_book_at(t):
	disb = _one("""SELECT SUM(d.disbursed_amount) FROM `tabLoan Disbursement` d JOIN `tabLoan` l ON l.name = d.against_loan
		WHERE d.docstatus = 1 AND l.docstatus = 1 AND d.disbursement_date <= %s AND l.status NOT IN ('Written Off', 'Cancelled')""", t)
	paid = _one("""SELECT SUM(r.principal_amount_paid) FROM `tabLoan Repayment` r JOIN `tabLoan` l ON l.name = r.against_loan
		WHERE r.docstatus = 1 AND l.docstatus = 1 AND r.posting_date <= %s AND l.status NOT IN ('Written Off', 'Cancelled')""",
		f"{t} 23:59:59")
	return max(0, disb - paid)


def _members_at(t):
	return cint(_one("SELECT COUNT(*) FROM `tabSACCO Member` WHERE IFNULL(joining_date, DATE(creation)) <= %s", t))


def snapshot(as_at):
	"""Headline figures and ratios at a date (used for 'now' and 'a year ago')."""
	from sacco_manager.sacco_manager.report.loan_portfolio_at_risk.loan_portfolio_at_risk import execute as par
	from sacco_manager.sacco_manager.report.sacco_prudential_summary.sacco_prudential_summary import execute as prudential

	t = getdate(as_at)
	f12 = add_days(add_months(t, -12), 1)
	pr = {r["metric"]: flt(r["value"]) for r in prudential({"as_at": t})[1]}
	pick = lambda start: next((v for k, v in pr.items() if k.startswith(start)), 0)  # noqa: E731
	ps = {s["label"]: flt(s["value"]) for s in par({"as_at": t})[4]}
	income_12 = _flow("a.root_type = 'Income'", f12, t)
	expense_12 = -_flow("a.root_type = 'Expense'", f12, t)
	loan_interest_12 = _flow("a.root_type = 'Income' AND a.account_name LIKE '%%Interest%%'", f12, t)
	book, book_prev = _loan_book_at(t), _loan_book_at(add_months(t, -12))
	savings = _savings_at(t)
	members = _members_at(t)
	borrowers = cint(_one("""SELECT COUNT(DISTINCT applicant) FROM `tabLoan` WHERE docstatus = 1 AND status IN %s
		AND disbursement_date <= %s""", (OPEN_LOAN, t)))
	s = get_settings()
	vals = {"company": get_company(), "t": t}
	employer_due = _gl("a.name = %(a)s", {**vals, "a": s.branch_receivable_account}) if s.get("branch_receivable_account") else 0
	booked_prov = -_gl("a.name LIKE '%%Provision%%'", vals)
	required_prov = ps.get("Required provision", 0)
	top10 = frappe.db.sql("""SELECT SUM(x.bal) FROM (SELECT l.applicant, SUM(GREATEST(l.disbursed_amount - l.total_principal_paid, 0)) bal
		FROM `tabLoan` l WHERE l.docstatus = 1 AND l.status IN %s GROUP BY l.applicant ORDER BY bal DESC LIMIT 10) x""", (OPEN_LOAN,))
	top10 = flt(top10[0][0]) if top10 else 0
	open_book = _one("SELECT SUM(GREATEST(disbursed_amount - total_principal_paid, 0)) FROM `tabLoan` WHERE docstatus = 1 AND status IN %s",
		(OPEN_LOAN,))

	from sacco_manager.company_lending import group_name

	grp = group_name()
	group_loans = _one("""SELECT SUM(GREATEST(l.disbursed_amount - l.total_principal_paid, 0)) FROM `tabLoan` l
		JOIN `tabCustomer` c ON c.name = l.applicant WHERE l.docstatus = 1 AND c.customer_group = %s AND l.status IN %s
		AND l.disbursement_date <= %s""", (grp, OPEN_LOAN, t))
	group_interest_12 = _flow("a.account_number = '4130'", f12, t)

	def pct(a, b):
		return round(a / b * 100, 1) if b else 0

	float_target = flt(frappe.db.get_single_value("SACCO Settings", "liquidity_float_target"))
	liquid_now = pick("Liquid assets")

	return frappe._dict(
		float_target=float_target,
		available_to_lend=max(0, liquid_now - float_target),
		group_company_loans=group_loans,
		group_company_interest_12m=group_interest_12,
		as_at=str(t),
		members=members,
		borrowers=borrowers,
		savings=savings,
		share_capital=pick("Share capital"),
		loan_book=book,
		total_assets=pick("Total assets"),
		liquid=pick("Liquid assets"),
		core_capital=pick("Core capital (equity"),
		institutional=pick("Institutional capital (excl"),
		required_provision=required_prov,
		booked_provision=booked_prov,
		employer_due=employer_due,
		income_12m=income_12,
		expense_12m=expense_12,
		surplus_12m=income_12 - expense_12,
		ratios=[
			_ratio("core_assets", _("Core capital / total assets"), pick("Core capital / total assets"), 10, 8, ">= 10%"),
			_ratio("core_deposits", _("Core capital / deposits"), pick("Core capital / deposits"), 8, 6, ">= 8%"),
			_ratio("institutional", _("Institutional capital / assets"), pick("Institutional capital / total assets"), 8, 6, ">= 8%"),
			_ratio("liquidity", _("Liquidity"), pick("Liquidity"), 15, 10, ">= 15%"),
			_ratio("loans_deposits", _("Loans / deposits"), pct(book, savings), 100, 120, "<= 100%", lower_is_better=True),
			_ratio("par30", _("Portfolio at risk > 30 days"), ps.get("PAR 30", 0), 5, 10, "<= 5%", lower_is_better=True),
			_ratio("provision_cover", _("Provisions booked / required"), pct(booked_prov, required_prov) if required_prov else 100,
				100, 50, ">= 100%"),
			_ratio("cost_income", _("Cost-to-income (12 months)"), pct(expense_12, income_12), 60, 80, "<= 60%", lower_is_better=True),
			_ratio("yield", _("Interest yield on loans (12 months)"), pct(loan_interest_12, (book + book_prev) / 2), None, None, _("info")),
			_ratio("concentration", _("Top 10 borrowers / loan book"), pct(top10, open_book), 25, 40, "<= 25%", lower_is_better=True),
			_ratio("group_exposure", _("Lent to group companies / members' savings"), pct(group_loans, savings), None, None,
				_("board limit")),
			_ratio("borrower_share", _("Members with a loan"), pct(borrowers, members), None, None, _("info")),
		],
	)


def _ratio(key, label, value, good, warn, benchmark, lower_is_better=False):
	value = round(flt(value), 1)
	if good is None:
		status = "info"
	elif lower_is_better:
		status = "good" if value <= good else ("warn" if value <= warn else "bad")
	else:
		status = "good" if value >= good else ("warn" if value >= warn else "bad")
	return {"key": key, "label": label, "value": value, "benchmark": benchmark, "status": status}


# ---------------------------------------------------------------------------
# monthly trends
# ---------------------------------------------------------------------------
def trends(end, months=24):
	end = get_last_day(getdate(end))
	start = getdate(add_months(end, -(months - 1))).replace(day=1)
	labels = []
	m = start
	while m <= end:
		labels.append(m.strftime("%Y-%m"))
		m = getdate(add_months(m, 1))
	idx = {k: i for i, k in enumerate(labels)}
	z = lambda: [0.0] * len(labels)  # noqa: E731
	out = {k: z() for k in ("deposits", "withdrawals", "disbursed", "repaid", "income", "expense", "joined")}

	def fill(key, sql, vals=None, sign=1):
		for ym, v in frappe.db.sql(sql, {"s": start, "e": end, "eod": f"{end} 23:59:59", "company": get_company(), **(vals or {})}):
			if ym in idx:
				out[key][idx[ym]] += sign * flt(v)

	fill("deposits", """SELECT DATE_FORMAT(posting_date, '%%Y-%%m'), SUM(amount) FROM `tabSACCO Savings Transaction`
		WHERE docstatus = 1 AND transaction_type NOT IN ('Withdrawal', 'Withholding Tax') AND posting_date BETWEEN %(s)s AND %(e)s GROUP BY 1""")
	fill("withdrawals", """SELECT DATE_FORMAT(posting_date, '%%Y-%%m'), SUM(amount) FROM `tabSACCO Savings Transaction`
		WHERE docstatus = 1 AND transaction_type IN ('Withdrawal', 'Withholding Tax') AND posting_date BETWEEN %(s)s AND %(e)s GROUP BY 1""")
	fill("disbursed", """SELECT DATE_FORMAT(disbursement_date, '%%Y-%%m'), SUM(disbursed_amount) FROM `tabLoan Disbursement`
		WHERE docstatus = 1 AND disbursement_date BETWEEN %(s)s AND %(e)s GROUP BY 1""")
	fill("repaid", """SELECT DATE_FORMAT(posting_date, '%%Y-%%m'), SUM(principal_amount_paid) FROM `tabLoan Repayment`
		WHERE docstatus = 1 AND posting_date BETWEEN %(s)s AND %(eod)s GROUP BY 1""")
	fill("income", """SELECT DATE_FORMAT(g.posting_date, '%%Y-%%m'), SUM(g.credit - g.debit) FROM `tabGL Entry` g
		JOIN `tabAccount` a ON a.name = g.account WHERE g.is_cancelled = 0 AND g.company = %(company)s AND a.root_type = 'Income'
		AND g.posting_date BETWEEN %(s)s AND %(e)s GROUP BY 1""")
	fill("expense", """SELECT DATE_FORMAT(g.posting_date, '%%Y-%%m'), SUM(g.debit - g.credit) FROM `tabGL Entry` g
		JOIN `tabAccount` a ON a.name = g.account WHERE g.is_cancelled = 0 AND g.company = %(company)s AND a.root_type = 'Expense'
		AND g.posting_date BETWEEN %(s)s AND %(e)s GROUP BY 1""")
	fill("joined", """SELECT DATE_FORMAT(IFNULL(joining_date, DATE(creation)), '%%Y-%%m'), COUNT(*) FROM `tabSACCO Member`
		WHERE IFNULL(joining_date, DATE(creation)) BETWEEN %(s)s AND %(e)s GROUP BY 1""")

	# running balances: opening before the window + monthly movements
	before = add_days(start, -1)
	sav, book, mem = _savings_at(before), _loan_book_at(before), _members_at(before)
	out["savings"], out["loan_book"], out["members"] = z(), z(), z()
	for i in range(len(labels)):
		sav += out["deposits"][i] - out["withdrawals"][i]
		book = max(0, book + out["disbursed"][i] - out["repaid"][i])
		mem += out["joined"][i]
		out["savings"][i], out["loan_book"][i], out["members"][i] = round(sav, 2), round(book, 2), mem
	out["surplus"] = [round(a - b, 2) for a, b in zip(out["income"], out["expense"], strict=True)]
	out["labels"] = labels
	return out


# ---------------------------------------------------------------------------
# tables
# ---------------------------------------------------------------------------
def by_branch(t):
	rows = frappe.db.sql(f"""SELECT IFNULL(NULLIF(m.branch, ''), 'Not set') AS branch, COUNT(*) AS members,
			SUM(m.status = 'Active') AS active,
			SUM((SELECT IFNULL(SUM(CASE WHEN x.transaction_type IN {DEBIT_TYPES} THEN -x.amount ELSE x.amount END), 0)
				FROM `tabSACCO Savings Transaction` x WHERE x.member = m.name AND x.docstatus = 1 AND x.posting_date <= %(t)s)) AS savings,
			SUM(IFNULL(m.share_balance, 0)) AS shares
		FROM `tabSACCO Member` m GROUP BY 1 ORDER BY members DESC""", {"t": t}, as_dict=True)
	loans = {r.branch: r for r in frappe.db.sql("""SELECT IFNULL(NULLIF(m.branch, ''), 'Not set') AS branch,
			COUNT(DISTINCT l.name) AS loans, SUM(GREATEST(l.disbursed_amount - l.total_principal_paid, 0)) AS book
		FROM `tabLoan` l JOIN (SELECT customer, MIN(branch) branch FROM `tabSACCO Member` WHERE IFNULL(customer, '') != ''
			GROUP BY customer) m ON m.customer = l.applicant
		WHERE l.docstatus = 1 AND l.status IN %(o)s GROUP BY 1""", {"o": OPEN_LOAN}, as_dict=True)}
	arrears = {r.branch: flt(r.overdue) for r in frappe.db.sql("""SELECT IFNULL(NULLIF(m.branch, ''), 'Not set') AS branch,
			SUM(d.outstanding_amount) AS overdue FROM `tabLoan Demand` d JOIN `tabLoan` l ON l.name = d.loan
			JOIN (SELECT customer, MIN(branch) branch FROM `tabSACCO Member` WHERE IFNULL(customer, '') != '' GROUP BY customer) m
				ON m.customer = l.applicant
		WHERE d.docstatus = 1 AND d.outstanding_amount > 0.5 AND d.demand_date <= %(eod)s AND l.status IN %(o)s GROUP BY 1""",
		{"o": OPEN_LOAN, "eod": f"{t} 23:59:59"}, as_dict=True)}
	from sacco_manager.company_lending import group_name

	grp = frappe.db.sql("""SELECT COUNT(*) AS loans, SUM(GREATEST(l.disbursed_amount - l.total_principal_paid, 0)) AS book
		FROM `tabLoan` l JOIN `tabCustomer` c ON c.name = l.applicant WHERE l.docstatus = 1 AND c.customer_group = %s
		AND l.status IN %s""", (group_name(), OPEN_LOAN), as_dict=True)[0]
	if grp.loans:
		rows.append(frappe._dict(branch=_("Group companies (borrowers)"), members=0, active=0, savings=0, shares=0,
			loans=cint(grp.loans), loan_book=flt(grp.book), arrears=0))
		loans.pop(_("Group companies (borrowers)"), None)
	for r in rows:
		if r.get("loan_book") is not None and r.branch == _("Group companies (borrowers)"):
			continue
		ln = loans.get(r.branch) or frappe._dict(loans=0, book=0)
		r.loans, r.loan_book, r.arrears = cint(ln.loans), flt(ln.book), arrears.get(r.branch, 0)
	return rows


def top_borrowers(limit=10):
	return frappe.db.sql("""SELECT m.member_no, IFNULL(m.full_name, l.applicant_name) AS name, m.branch, COUNT(*) AS loans,
			SUM(GREATEST(l.disbursed_amount - l.total_principal_paid, 0)) AS balance
		FROM `tabLoan` l LEFT JOIN (SELECT customer, MIN(member_no) member_no, MIN(full_name) full_name, MIN(branch) branch
			FROM `tabSACCO Member` WHERE IFNULL(customer, '') != '' GROUP BY customer) m ON m.customer = l.applicant
		WHERE l.docstatus = 1 AND l.status IN %s GROUP BY l.applicant ORDER BY balance DESC LIMIT %s""",
		(OPEN_LOAN, limit), as_dict=True)


def accounts_flow(root, f, t, limit=8):
	sign = "g.credit - g.debit" if root == "Income" else "g.debit - g.credit"
	rows = frappe.db.sql(f"""SELECT a.account_name AS account, SUM({sign}) AS amount FROM `tabGL Entry` g
		JOIN `tabAccount` a ON a.name = g.account WHERE g.is_cancelled = 0 AND g.company = %s AND a.root_type = %s
		AND g.posting_date BETWEEN %s AND %s GROUP BY a.name HAVING ABS(amount) > 0.5 ORDER BY amount DESC""",
		(get_company(), root, f, t), as_dict=True)
	if len(rows) > limit:
		rest = sum(flt(r.amount) for r in rows[limit:])
		rows = [*rows[:limit], frappe._dict(account=_("Other"), amount=rest)]
	return rows


def balance_sheet(t, limit=7):
	rows = frappe.db.sql("""SELECT a.root_type, a.account_name AS account, SUM(g.debit - g.credit) AS bal FROM `tabGL Entry` g
		JOIN `tabAccount` a ON a.name = g.account WHERE g.is_cancelled = 0 AND g.company = %s AND g.posting_date <= %s
		AND a.root_type IN ('Asset', 'Liability', 'Equity') GROUP BY a.name HAVING ABS(bal) > 0.5""", (get_company(), t), as_dict=True)
	out = {}
	for root in ("Asset", "Liability", "Equity"):
		sign = 1 if root == "Asset" else -1
		items = sorted(({"account": r.account, "amount": sign * flt(r.bal)} for r in rows if r.root_type == root),
			key=lambda x: -abs(x["amount"]))
		if len(items) > limit:
			items = [*items[:limit], {"account": _("Other"), "amount": sum(x["amount"] for x in items[limit:])}]
		out[root] = items
	result = _flow("a.root_type IN ('Income', 'Expense')", "1900-01-01", t)  # unclosed result belongs to equity
	out["Equity"].append({"account": _("Current result (not yet closed)"), "amount": result})
	return out


def alerts(t):
	out = []

	def add(level, text, route=None):
		out.append({"level": level, "text": text, "route": route})

	n = frappe.db.count("SACCO M-Pesa Transaction", {"status": "Unallocated"})
	if n:
		add("warn", _("{0} M-Pesa payments are not yet allocated to a member").format(n), "List/SACCO M-Pesa Transaction?status=Unallocated")
	n = frappe.db.count("Withdrawal Request", {"docstatus": 0})
	if n:
		add("info", _("{0} withdrawal requests waiting for approval").format(n), "List/Withdrawal Request?docstatus=0")
	n = frappe.db.count("Loan Application", {"status": "Open", "docstatus": ["!=", 2]})
	if n:
		add("info", _("{0} loan applications open").format(n), "List/Loan Application?status=Open")
	n = frappe.db.count("SACCO Branch Remittance", {"docstatus": 0})
	if n:
		add("info", _("{0} employer remittances not yet submitted").format(n), "List/SACCO Branch Remittance?docstatus=0")
	if frappe.db.exists("DocType", "SACCO Board Approval"):
		n = frappe.db.count("SACCO Board Approval", {"docstatus": 0})
		if n:
			add("info", _("{0} board approvals pending").format(n), "List/SACCO Board Approval?docstatus=0")
	target = flt(frappe.db.get_single_value("SACCO Settings", "liquidity_float_target"))
	if target:
		liquid = _gl("a.account_type IN ('Cash', 'Bank')", {"company": get_company(), "t": t})
		if liquid < target:
			add("bad", _("Cash is below the liquid float ({0} of {1}): recall funds from the group companies before lending more").format(
				frappe.format_value(liquid, {"fieldtype": "Currency"}), frappe.format_value(target, {"fieldtype": "Currency"})))
		elif liquid > target * 1.5:
			add("info", _("{0} above the liquid float is idle and could be lent to the group companies").format(
				frappe.format_value(liquid - target, {"fieldtype": "Currency"})))
	n = frappe.db.count("SACCO Membership Application", {"status": ["in", ["Submitted", "Under Review"]]})
	if n:
		add("info", _("{0} membership applications to review").format(n), "List/SACCO Membership Application?status=Submitted")
	diff = _savings_at(t) - (-_gl("a.name = %(a)s", {"company": get_company(), "t": t, "a": get_settings().savings_liability_account}))
	if get_settings().savings_liability_account and abs(diff) >= 1:
		add("bad", _("Savings sub-ledger and GL differ by {0}").format(frappe.format_value(diff, {"fieldtype": "Currency"})),
			"query-report/Savings Balances")
	return out


def kyc(t=None):
	scope = frappe.db.sql("""SELECT m.name, m.id_number, m.kra_pin, m.phone, m.contact_name, m.contact_phone, m.consent_data_processing
		FROM `tabSACCO Member` m WHERE m.status = 'Active' """, as_dict=True)
	if not scope:
		return {"members": 0, "complete": 0, "pct": 0, "fields": []}
	checks = [("ID", "id_number"), ("KRA PIN", "kra_pin"), ("Mobile", "phone"), ("Next of kin", "contact_name"),
		("Kin phone", "contact_phone"), ("Consent", "consent_data_processing")]
	fields = [{"label": lab, "done": sum(1 for m in scope if m.get(fn)), "pct": round(sum(1 for m in scope if m.get(fn)) / len(scope) * 100)}
		for lab, fn in checks]
	complete = sum(1 for m in scope if all(m.get(fn) for _l, fn in checks))
	return {"members": len(scope), "complete": complete, "pct": round(complete / len(scope) * 100), "fields": fields}


# ---------------------------------------------------------------------------
# page endpoints
# ---------------------------------------------------------------------------
@frappe.whitelist()
def get_board(as_at=None, months=24):
	_check()
	t = getdate(as_at or today())
	now, prev = snapshot(t), snapshot(add_months(t, -12))
	fy_start = getdate(f"{t.year}-01-01")
	return {
		"company": get_company(),
		"as_at": str(t),
		"generated": frappe.utils.now_datetime().strftime("%d-%m-%Y %H:%M"),
		"now": now,
		"prev": prev,
		"prev_available": bool(history_from() and history_from() <= getdate(add_months(t, -12))),
		"history_from": str(history_from() or ""),
		"trends": trends(t, cint(months) or 24),
		"by_branch": by_branch(t),
		"top_borrowers": top_borrowers(),
		"income_ytd": accounts_flow("Income", fy_start, t),
		"expense_ytd": accounts_flow("Expense", fy_start, t),
		"alerts": alerts(t),
		"kyc": kyc(t),
	}


@frappe.whitelist()
def get_agm(year=None, savings_rate=None, dividend_rate=None):
	_check()
	year = cint(year) or (getdate(today()).year - 1)
	f, t = getdate(f"{year}-01-01"), getdate(f"{year}-12-31")
	t_eff = min(t, getdate(today()))
	pf, pt = getdate(f"{year - 1}-01-01"), getdate(f"{year - 1}-12-31")
	now, prev = snapshot(t_eff), snapshot(pt)
	income, expense = accounts_flow("Income", f, t_eff, 10), accounts_flow("Expense", f, t_eff, 10)
	inc_total, exp_total = sum(flt(r.amount) for r in income), sum(flt(r.amount) for r in expense)
	p_inc = _flow("a.root_type = 'Income'", pf, pt)
	p_exp = -_flow("a.root_type = 'Expense'", pf, pt)
	tr = trends(t_eff, 12 if t_eff.month == 12 else t_eff.month)
	from sacco_manager.sacco_manager.report.loan_portfolio_at_risk.loan_portfolio_at_risk import execute as par

	_c, par_rows, _m, _ch, _s = par({"as_at": t_eff})
	buckets = {}
	for r in par_rows:
		b = buckets.setdefault(r["classification"], {"class": r["classification"], "loans": 0, "principal": 0.0, "provision": 0.0})
		b["loans"] += 1
		b["principal"] += flt(r["principal_outstanding"])
		b["provision"] += flt(r["provision"])
	surplus = inc_total - exp_total
	# appropriation proposal: statutory reserve first, then interest on deposits / dividend on shares
	s = get_settings()
	reserve_pct = 20.0
	avg_savings = (prev.savings + now.savings) / 2
	sav_rate = flt(savings_rate) if savings_rate not in (None, "") else _default_savings_rate()
	div_rate = flt(dividend_rate)
	interest_cost = avg_savings * sav_rate / 100
	dividend = now.share_capital * div_rate / 100
	reserve = max(0, surplus) * reserve_pct / 100
	movement = {
		"members_start": _members_at(add_days(f, -1)), "members_end": now.members,
		"joined": cint(_one("SELECT COUNT(*) FROM `tabSACCO Member` WHERE IFNULL(joining_date, DATE(creation)) BETWEEN %s AND %s", (f, t_eff))),
		"disbursed": sum(tr["disbursed"]), "repaid": sum(tr["repaid"]),
		"deposits": sum(tr["deposits"]), "withdrawals": sum(tr["withdrawals"]),
	}
	return {
		"company": get_company(),
		"year": year,
		"period": f"{f.strftime('%d %b %Y')} – {t_eff.strftime('%d %b %Y')}",
		"partial": t_eff < t,
		"generated": frappe.utils.now_datetime().strftime("%d-%m-%Y %H:%M"),
		"now": now,
		"prev": prev,
		"prev_available": bool(history_from() and history_from() <= pt),
		"history_from": str(history_from() or ""),
		"movement": movement,
		"income": income, "expense": expense, "income_total": inc_total, "expense_total": exp_total, "surplus": surplus,
		"prev_income": p_inc, "prev_expense": p_exp, "prev_surplus": p_inc - p_exp,
		"balance_sheet": balance_sheet(t_eff),
		"trends": tr,
		"par": sorted(buckets.values(), key=lambda b: ["Performing", "Watch", "Substandard", "Doubtful", "Loss"].index(b["class"])
			if b["class"] in ["Performing", "Watch", "Substandard", "Doubtful", "Loss"] else 9),
		"by_branch": by_branch(t_eff),
		"kyc": kyc(t_eff),
		"appropriation": {
			"surplus": surplus, "reserve_pct": reserve_pct, "reserve": reserve,
			"savings_rate": sav_rate, "avg_savings": avg_savings, "interest": interest_cost,
			"interest_wht_rate": flt(s.get("interest_wht_rate")),
			"dividend_rate": div_rate, "share_capital": now.share_capital, "dividend": dividend,
			"dividend_wht_rate": flt(s.get("dividend_wht_rate")),
			"retained": surplus - reserve - interest_cost - dividend,
		},
		"years": list(range(getdate(today()).year, max(getdate(today()).year - 10, 2008), -1)),
	}


def _default_savings_rate():
	r = frappe.db.sql("SELECT AVG(interest_rate) FROM `tabSACCO Savings Account` WHERE IFNULL(interest_rate, 0) > 0")
	return round(flt(r[0][0]) if r else 0, 2)


# ---------------------------------------------------------------------------
# number cards for the workspace
# ---------------------------------------------------------------------------
def _ratio_card(key):
	_check_card()
	snap = frappe.cache.get_value("sacco_board_snapshot")
	if not snap:
		snap = snapshot(today())
		frappe.cache.set_value("sacco_board_snapshot", snap, expires_in_sec=300)
	r = next(x for x in snap["ratios"] if x["key"] == key)
	return {"value": r["value"], "fieldtype": "Percent"}


def _check_card():
	frappe.has_permission("SACCO Member", "read", throw=True)


@frappe.whitelist()
def card_core_capital(filters=None):
	return _ratio_card("core_assets")


@frappe.whitelist()
def card_liquidity(filters=None):
	return _ratio_card("liquidity")


@frappe.whitelist()
def card_loans_deposits(filters=None):
	return _ratio_card("loans_deposits")


@frappe.whitelist()
def card_cost_income(filters=None):
	return _ratio_card("cost_income")
