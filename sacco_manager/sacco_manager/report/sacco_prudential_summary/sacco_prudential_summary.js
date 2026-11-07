// Copyright (c) 2026, Trident SACCO and contributors
// For license information, please see license.txt

frappe.query_reports["SACCO Prudential Summary"] = {
	filters: [
		{"fieldname": "as_at", "label": "As At", "fieldtype": "Date", "reqd": 1, "default": frappe.datetime.get_today()}
	],
};
