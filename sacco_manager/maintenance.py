# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""One-off data repairs for the live site. Run deliberately, dry-run first:

    bench --site <site> execute sacco_manager.maintenance.reverse_bogus_delinquency_demands
    bench --site <site> execute sacco_manager.maintenance.reverse_bogus_delinquency_demands --kwargs "{'dry_run': False}"

Nothing here runs automatically on migrate.
"""

import frappe
from frappe.utils import flt



def reverse_bogus_delinquency_demands(dry_run=True):
	"""Cancel the 'Additional Interest' demands the old daily job created.

	The job charged loan_amount x rate/12 once per loan, regardless of arrears. Demands not yet
	paid are cancelled; demands already (part) paid by a repayment are listed for the credit team
	to waive (Lending blocks cancelling a demand a repayment has consumed).
	"""
	frappe.only_for(("System Manager", "SACCO Administrator"))
	demands = frappe.get_all(
		"Loan Demand",
		filters={
			"demand_type": "Additional Interest",
			"docstatus": 1,
			# signature of the old job: created by the scheduler (Administrator), not by Lending's
			# own Process Loan Demand and not tied to a repayment schedule row
			"owner": "Administrator",
			"process_loan_demand": ["is", "not set"],
			"loan_repayment_schedule": ["is", "not set"],
		},
		fields=["name", "loan", "demand_amount", "paid_amount", "outstanding_amount", "demand_date"],
		order_by="loan",
	)
	unpaid = [d for d in demands if flt(d.paid_amount) <= 0]
	paid = [d for d in demands if flt(d.paid_amount) > 0]
	summary = {
		"found": len(demands),
		"total_amount": sum(flt(d.demand_amount) for d in demands),
		"cancellable_unpaid": len(unpaid),
		"cancellable_amount": sum(flt(d.demand_amount) for d in unpaid),
		"already_paid_need_waiver": [(d.name, d.loan, d.paid_amount) for d in paid],
		"dry_run": dry_run,
	}
	if dry_run:
		print(frappe.as_json(summary))
		return summary
	cancelled, failed = 0, []
	for d in unpaid:
		try:
			doc = frappe.get_doc("Loan Demand", d.name)
			doc.flags.ignore_permissions = True
			doc.cancel()
			frappe.db.commit()
			cancelled += 1
		except Exception as e:
			frappe.db.rollback()
			failed.append((d.name, str(e)[:200]))
	summary.update({"cancelled": cancelled, "failed": failed})
	print(frappe.as_json(summary))
	return summary


def savings_balance_check():
	"""Compare each account's stored balance with the sum of its posted transactions, and the
	total with the Members' Savings GL balance. Read-only."""
	rows = frappe.db.sql(
		"""SELECT a.name, a.balance,
			COALESCE(SUM(CASE WHEN t.transaction_type IN ('Withdrawal','Withholding Tax') THEN -t.amount
				ELSE t.amount END), 0) AS txn_total
		FROM `tabSACCO Savings Account` a
		LEFT JOIN `tabSACCO Savings Transaction` t ON t.savings_account = a.name AND t.docstatus = 1
		GROUP BY a.name, a.balance""",
		as_dict=True,
	)
	mismatched = [r for r in rows if abs(flt(r.balance) - flt(r.txn_total)) > 0.5]
	acc = frappe.db.get_single_value("SACCO Settings", "savings_liability_account")
	gl = flt(frappe.db.sql(
		"SELECT SUM(credit - debit) FROM `tabGL Entry` WHERE account=%s AND is_cancelled=0", acc)[0][0])
	result = {
		"accounts": len(rows),
		"mismatched_accounts": [(r.name, r.balance, r.txn_total) for r in mismatched],
		"sum_of_balances": sum(flt(r.balance) for r in rows),
		"gl_balance": gl,
	}
	print(frappe.as_json(result))
	return result
