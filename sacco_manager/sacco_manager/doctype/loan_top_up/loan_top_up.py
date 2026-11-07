# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Loan top-up done as a refinance, using only standard Lending documents.

The previous version rewrote Lending's repayment schedule with raw SQL, which broke demand,
accrual and DPD processing. Now, on submit (maker-checker):

1. A new Loan is created for (old loan payable + top-up amount) on the chosen terms.
2. It is disbursed in full into the Loan Top-Up Clearing account.
3. A Loan Repayment on the old loan, paid from clearing, settles it in full.
4. A JE pays the net cash (the top-up) from clearing to the member's bank/M-Pesa account.
Clearing nets to zero; the old loan closes; the new loan carries one clean schedule.
"""

import math

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate, today

from sacco_manager.accounting import cancel_journal_entry, make_journal_entry
from sacco_manager.lending_utils import create_loan_repayment, loan_outstanding
from sacco_manager.utils import enforce_maker_checker, require_setting


class LoanTopUp(Document):
	def validate(self):
		loan = frappe.db.get_value("Loan", self.loan, ["docstatus", "status", "applicant", "loan_product",
			"rate_of_interest"], as_dict=True)
		if not loan or loan.docstatus != 1 or loan.status not in ("Disbursed", "Partially Disbursed", "Active"):
			frappe.throw(_("Loan {0} must be an active, disbursed loan.").format(self.loan))
		self.customer = loan.applicant
		self.top_up_date = self.top_up_date or today()
		if flt(self.top_up_amount) <= 0:
			frappe.throw(_("Top-up amount must be greater than zero."))
		if not self.new_interest_rate:
			self.new_interest_rate = loan.rate_of_interest
		out = loan_outstanding(self.loan, self.top_up_date)
		self.outstanding_principal = out["principal"]
		self.settlement_amount = round(out["payable"], 2)
		self.new_principal = round(flt(self.settlement_amount) + flt(self.top_up_amount), 2)
		self.net_disbursement = flt(self.top_up_amount)

		if self.repayment_method == "Repay Over Number of Periods":
			if not self.new_tenure_months:
				frappe.throw(_("Set the new tenure (months)."))
			self.new_installment_amount = emi(self.new_principal, self.new_interest_rate, self.new_tenure_months)
		else:
			amount = flt(self.new_monthly_repayment_amount) or flt(self.new_installment_amount)
			if not amount:
				frappe.throw(_("Set the new monthly repayment amount."))
			self.new_installment_amount = amount
			self.new_tenure_months = tenure(self.new_principal, self.new_interest_rate, amount)

		if self.docstatus == 0:
			self.status = "Draft"

	def before_submit(self):
		enforce_maker_checker(self, _("submit"))
		require_setting("loan_topup_clearing_account", "Loan Top-Up / Refinance Clearing")
		if not self.payment_account:
			self.payment_account = require_setting("cash_account", "Default Cash / Bank Account")
		# re-read the payable at submit time so the old loan closes exactly
		self.validate()

	def on_submit(self):
		clearing = require_setting("loan_topup_clearing_account")
		old = frappe.get_doc("Loan", self.loan)

		new_loan = frappe.get_doc({
			"doctype": "Loan",
			"applicant_type": old.applicant_type,
			"applicant": old.applicant,
			"company": old.company,
			"posting_date": self.top_up_date,
			"loan_product": old.loan_product,
			"loan_amount": self.new_principal,
			"rate_of_interest": self.new_interest_rate,
			"is_term_loan": 1,
			"repayment_method": self.repayment_method,
			"repayment_periods": self.new_tenure_months if self.repayment_method == "Repay Over Number of Periods" else None,
			"monthly_repayment_amount": self.new_installment_amount if self.repayment_method == "Repay Fixed Amount per Period" else None,
			"repayment_start_date": frappe.utils.add_months(getdate(self.top_up_date), 1),
			"is_secured_loan": old.is_secured_loan,
		})
		new_loan.flags.ignore_permissions = True
		new_loan.insert()
		new_loan.submit()
		self.db_set("new_loan", new_loan.name)

		disb = frappe.get_doc({
			"doctype": "Loan Disbursement",
			"against_loan": new_loan.name,
			"applicant_type": old.applicant_type,
			"applicant": old.applicant,
			"company": old.company,
			"disbursement_date": self.top_up_date,
			"posting_date": self.top_up_date,
			"disbursed_amount": self.new_principal,
			"disbursement_account": clearing,
		})
		disb.flags.ignore_permissions = True
		disb.insert()
		if disb.disbursement_account != clearing:
			disb.disbursement_account = clearing
			disb.save()
		disb.submit()
		self.db_set("loan_disbursement", disb.name)

		rp = create_loan_repayment(self.loan, self.settlement_amount, self.top_up_date, clearing,
			reference_number=self.name, remarks=f"Settled by top-up {self.name} / new loan {new_loan.name}")
		self.db_set("settlement_repayment", rp.name)

		self.db_set("cash_journal_entry", make_journal_entry(
			[{"account": clearing, "debit": flt(self.net_disbursement)},
			 {"account": self.payment_account, "credit": flt(self.net_disbursement)}],
			self.top_up_date, f"Top-up cash to member - {self.name}", reference=(self.doctype, self.name)))
		self.db_set("status", "Submitted")

	def on_cancel(self):
		self.ignore_linked_doctypes = ("Loan", "Loan Disbursement", "Loan Repayment", "Journal Entry", "GL Entry",
			"Payment Ledger Entry")
		cancel_journal_entry(self.cash_journal_entry)
		for dt, name in (("Loan Repayment", self.settlement_repayment), ("Loan Disbursement", self.loan_disbursement),
				("Loan", self.new_loan)):
			if name and frappe.db.get_value(dt, name, "docstatus") == 1:
				d = frappe.get_doc(dt, name)
				d.flags.ignore_permissions = True
				d.cancel()
		self.db_set("status", "Cancelled")


def emi(principal, annual_rate, months):
	months = int(months or 0)
	if not months:
		return 0
	r = flt(annual_rate) / 100 / 12
	if r == 0:
		return round(flt(principal) / months, 2)
	return round(flt(principal) * r * (1 + r) ** months / ((1 + r) ** months - 1), 2)


def tenure(principal, annual_rate, instalment):
	r = flt(annual_rate) / 100 / 12
	if r == 0:
		return math.ceil(flt(principal) / flt(instalment))
	val = 1 - flt(principal) * r / flt(instalment)
	if val <= 0:
		frappe.throw(_("Monthly repayment is too low to cover interest. Minimum is {0}.").format(
			round(flt(principal) * r + 1, 2)))
	return math.ceil(-math.log(val) / math.log(1 + r))
