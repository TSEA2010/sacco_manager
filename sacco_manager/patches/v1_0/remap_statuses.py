# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Map old free-form statuses onto the new workflow statuses."""

import frappe


def execute():
	frappe.db.sql("""UPDATE `tabSACCO Savings Transaction`
		SET status = CASE docstatus WHEN 1 THEN 'Posted' WHEN 2 THEN 'Cancelled' ELSE 'Draft' END""")
	frappe.db.sql("""UPDATE `tabSACCO Savings Transaction` SET mode_of_payment='Bank'
		WHERE IFNULL(mode_of_payment,'')='' AND transaction_type IN ('Deposit','Withdrawal')""")
	# Withdrawal Request: submitted = paid out of savings; drafts wait for approval
	frappe.db.sql("UPDATE `tabWithdrawal Request` SET status='Paid' WHERE docstatus=1")
	frappe.db.sql("UPDATE `tabWithdrawal Request` SET status='Pending Approval' WHERE docstatus=0")
	frappe.db.sql("UPDATE `tabWithdrawal Request` SET status='Rejected' WHERE docstatus=2")
	frappe.db.sql("""UPDATE `tabSACCO Savings Account` SET status='Active'
		WHERE IFNULL(status,'') NOT IN ('Active','Dormant','Closed')""")
	frappe.db.sql("""UPDATE `tabSACCO Member` SET marital_status='Widowed' WHERE marital_status='Windowed'""")
	frappe.db.sql("""UPDATE `tabSACCO Branch Remittance` SET status='Draft' WHERE docstatus=0""")
