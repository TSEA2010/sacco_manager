# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""The live "Loan Security" client script valued savings security at balance x 4 while the server
uses the SACCO Settings multiplier (3). Security values are now maintained server-side only."""

import frappe


def execute():
	for name in frappe.get_all("Client Script", filters={"dt": "Loan Security", "enabled": 1}, pluck="name"):
		script = frappe.db.get_value("Client Script", name, "script") or ""
		if "get_sacco_savings_balance" in script:
			frappe.db.set_value("Client Script", name, "enabled", 0)
