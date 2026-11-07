# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import add_months, today


def mark_dormant_accounts():
	"""Flag Active savings accounts with no posted transaction for 12 months as Dormant (and back)."""
	cutoff = add_months(today(), -12)
	frappe.db.sql(
		"""UPDATE `tabSACCO Savings Account` a SET a.status='Dormant'
		WHERE a.status='Active' AND a.creation < %(c)s AND NOT EXISTS (
			SELECT 1 FROM `tabSACCO Savings Transaction` t
			WHERE t.savings_account=a.name AND t.docstatus=1 AND t.posting_date >= %(c)s)""",
		{"c": cutoff},
	)
	frappe.db.sql(
		"""UPDATE `tabSACCO Savings Account` a SET a.status='Active'
		WHERE a.status='Dormant' AND EXISTS (
			SELECT 1 FROM `tabSACCO Savings Transaction` t
			WHERE t.savings_account=a.name AND t.docstatus=1 AND t.posting_date >= %(c)s)""",
		{"c": cutoff},
	)
