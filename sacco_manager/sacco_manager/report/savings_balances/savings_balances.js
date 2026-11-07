// Copyright (c) 2026, Trident SACCO and contributors
// For license information, please see license.txt

frappe.query_reports["Savings Balances"] = {
	filters: [
		{"fieldname": "as_at", "label": "As At", "fieldtype": "Date", "reqd": 1, "default": frappe.datetime.get_today()},
		{"fieldname": "account_type", "label": "Account Type", "fieldtype": "Link", "reqd": 0, "options": "SACCO Account Type"},
		{"fieldname": "branch", "label": "Branch", "fieldtype": "Link", "reqd": 0, "options": "Branch"}
	],
};
