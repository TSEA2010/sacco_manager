# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Single place where sacco_manager writes to the general ledger.

Every posting goes through `make_journal_entry`, which:
- uses the SACCO company and its default cost center,
- tags member-side lines with Party Type "SACCO Member",
- links the JE back to the source document (reference_type/name on the JE header via user_remark
  and `cheque_no`), so auditors can trace every GL line to a SACCO transaction.
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate, today

from sacco_manager.utils import get_company

PARTY_TYPE = "SACCO Member"


def make_journal_entry(lines, posting_date=None, remark=None, reference=None, submit=True, voucher_type="Journal Entry"):
	"""lines: list of dicts {account, debit|credit, party (member), cost_center?}.

	reference: (doctype, name) of the SACCO document that caused the posting.
	Returns the Journal Entry name.
	"""
	company = get_company()
	if not company:
		frappe.throw(_("Set the Company in SACCO Settings first."))
	cost_center = frappe.get_cached_value("Company", company, "cost_center")

	total_dr = sum(flt(line.get("debit")) for line in lines)
	total_cr = sum(flt(line.get("credit")) for line in lines)
	if abs(total_dr - total_cr) > 0.005:
		frappe.throw(_("Unbalanced SACCO posting: debit {0} vs credit {1}").format(total_dr, total_cr))
	if total_dr <= 0:
		frappe.throw(_("Cannot post a zero-value journal entry."))

	je = frappe.new_doc("Journal Entry")
	je.voucher_type = voucher_type
	je.company = company
	je.posting_date = getdate(posting_date or today())
	je.user_remark = remark or ""
	if reference:
		je.cheque_no = f"{reference[0]}: {reference[1]}"[:140]
		je.cheque_date = je.posting_date

	for line in lines:
		account = line["account"]
		_check_company(account, company)
		row = {
			"account": account,
			"debit_in_account_currency": flt(line.get("debit")),
			"credit_in_account_currency": flt(line.get("credit")),
			"cost_center": line.get("cost_center") or cost_center,
			"user_remark": line.get("remark"),
		}
		if line.get("party"):
			row["party_type"] = PARTY_TYPE
			row["party"] = line["party"]
		je.append("accounts", row)

	je.flags.ignore_permissions = True
	frappe.flags.sacco_posting = True
	try:
		je.insert()
		if submit:
			je.submit()
	finally:
		frappe.flags.sacco_posting = False
	return je.name


def guard_control_accounts(doc, method=None):
	"""Journal Entry validate hook: members' savings, share capital and dividends payable may only be
	changed by SACCO documents, so the member sub-ledgers always equal the GL. System Managers can
	override (e.g. an auditor-approved correction); the override is recorded on the JE."""
	if frappe.flags.sacco_posting or frappe.flags.in_import or frappe.flags.in_patch:
		return
	s = frappe.get_cached_doc("SACCO Settings")
	control = {a for a in (s.savings_liability_account, s.share_capital_account, s.dividend_payable_account) if a}
	hit = sorted({row.account for row in doc.accounts if row.account in control})
	if not hit:
		return
	if "System Manager" in frappe.get_roles():
		doc.user_remark = ((doc.user_remark or "") + f" [Manual entry to SACCO control account(s) {', '.join(hit)} by {frappe.session.user}]").strip()
		return
	frappe.throw(_("{0} can only be changed through SACCO transactions (savings, shares, dividends), so member balances stay equal to the ledger.").format(
		", ".join(hit)), title=_("SACCO control account"))


def cancel_journal_entry(name):
	if not name or not frappe.db.exists("Journal Entry", name):
		return
	je = frappe.get_doc("Journal Entry", name)
	if je.docstatus == 1:
		je.flags.ignore_permissions = True
		je.cancel()


def _check_company(account, company):
	acc_company = frappe.get_cached_value("Account", account, "company")
	if not acc_company:
		frappe.throw(_("Account {0} does not exist.").format(account))
	if acc_company != company:
		frappe.throw(_("Account {0} belongs to {1}, not {2}.").format(account, acc_company, company))


# ---------------------------------------------------------------------------
# Account-type guards used by SACCO Settings.validate
# ---------------------------------------------------------------------------
def assert_account(account, label, root_types=None, account_types=None, allow_group=False):
	if not account:
		return
	acc = frappe.get_cached_value(
		"Account", account, ["root_type", "account_type", "is_group", "company"], as_dict=True
	)
	if not acc:
		frappe.throw(_("{0}: account {1} does not exist.").format(label, account))
	if acc.is_group and not allow_group:
		frappe.throw(_("{0}: {1} is a group account; pick a ledger account.").format(label, account))
	if root_types and acc.root_type not in root_types:
		frappe.throw(
			_("{0}: {1} is a {2} account, expected {3}.").format(
				label, account, acc.root_type, " / ".join(root_types)
			),
			title=_("Wrong account type"),
		)
	if account_types and acc.account_type not in account_types:
		frappe.throw(
			_("{0}: {1} must have Account Type {2}.").format(label, account, " / ".join(account_types)),
			title=_("Wrong account type"),
		)
