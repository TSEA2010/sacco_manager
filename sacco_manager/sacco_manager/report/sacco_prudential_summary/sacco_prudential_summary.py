# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Key SACCO prudential ratios from the general ledger as at a date.

Benchmarks (SACCO Societies (Deposit-Taking) Regulations, 2010) shown for reference:
core capital / total assets >= 10%, core capital / total deposits >= 8%, liquidity >= 15%.
Core capital here = share capital (if held in equity or liability) + all other equity.
Confirm the exact definitions with the external auditor before filing."""

import frappe
from frappe import _
from frappe.utils import flt

from sacco_manager.utils import get_company, get_settings


def _balance(where, vals):
	return flt(frappe.db.sql(
		f"""SELECT SUM(g.debit - g.credit) FROM `tabGL Entry` g JOIN `tabAccount` a ON a.name = g.account
		WHERE g.is_cancelled=0 AND g.company=%(company)s AND g.posting_date <= %(as_at)s AND {where}""",
		vals)[0][0])


def execute(filters=None):
	f = frappe._dict(filters or {})
	s = get_settings()
	vals = {"company": get_company(), "as_at": f.as_at}
	assets = _balance("a.root_type='Asset'", vals)
	liquid = _balance("a.account_type IN ('Cash','Bank')", vals)
	equity = -_balance("a.root_type='Equity'", vals)
	pnl = -_balance("a.root_type IN ('Income','Expense')", vals)  # current-year result not yet closed
	deposits = -_balance("a.name=%(sav)s", {**vals, "sav": s.savings_liability_account}) if s.savings_liability_account else 0
	share_cap = -_balance("a.name=%(sh)s", {**vals, "sh": s.share_capital_account}) if s.share_capital_account else 0
	share_in_liab = s.share_capital_account and frappe.get_cached_value("Account", s.share_capital_account, "root_type") == "Liability"
	core = equity + pnl + (share_cap if share_in_liab else 0)
	loan_accounts = list({p.loan_account for p in frappe.get_all("Loan Product", fields=["loan_account"]) if p.loan_account})
	loans = sum(_balance("a.name=%(la)s", {**vals, "la": la}) for la in loan_accounts)
	interest_recv = sum(_balance("a.name=%(ia)s", {**vals, "ia": p.interest_receivable_account})
		for p in frappe.get_all("Loan Product", fields=["interest_receivable_account"]) if p.interest_receivable_account)

	def ratio(a, b):
		return round(a / b * 100, 2) if b else 0

	rows = [
		{"metric": _("Total assets"), "value": assets},
		{"metric": _("Liquid assets (cash, bank, M-Pesa)"), "value": liquid},
		{"metric": _("Members' savings / deposits"), "value": deposits},
		{"metric": _("Share capital"), "value": share_cap},
		{"metric": _("Core capital (equity + current result + share capital)"), "value": core},
		{"metric": _("Gross loans (principal ledger)"), "value": loans},
		{"metric": _("Interest receivable"), "value": interest_recv},
		{"metric": _("Core capital / total assets (%)"), "value": ratio(core, assets), "benchmark": ">= 10", "is_ratio": 1},
		{"metric": _("Core capital / deposits (%)"), "value": ratio(core, deposits), "benchmark": ">= 8", "is_ratio": 1},
		{"metric": _("Liquidity: liquid assets / deposits (%)"), "value": ratio(liquid, deposits), "benchmark": ">= 15", "is_ratio": 1},
		{"metric": _("Loans / deposits (%)"), "value": ratio(loans, deposits), "benchmark": "", "is_ratio": 1},
	]
	cols = [
		{"label": _("Metric"), "fieldname": "metric", "width": 380},
		{"label": _("Value"), "fieldname": "value", "fieldtype": "Float", "width": 180},
		{"label": _("Benchmark"), "fieldname": "benchmark", "width": 120},
	]
	return cols, rows
