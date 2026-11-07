// Copyright (c) 2026, Trident SACCO and contributors
// For license information, please see license.txt

frappe.query_reports["SACCO Monthly Movement"] = {
	filters: [
		{"fieldname": "from_date", "label": "From", "fieldtype": "Date", "reqd": 1, "default": frappe.datetime.month_start()},
		{"fieldname": "to_date", "label": "To", "fieldtype": "Date", "reqd": 1, "default": frappe.datetime.get_today()}
	],
};
