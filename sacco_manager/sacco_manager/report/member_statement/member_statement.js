// Copyright (c) 2026, Trident SACCO and contributors
// For license information, please see license.txt

frappe.query_reports["Member Statement"] = {
	filters: [
		{"fieldname": "member", "label": "Member", "fieldtype": "Link", "reqd": 1, "options": "SACCO Member"},
		{"fieldname": "from_date", "label": "From", "fieldtype": "Date", "reqd": 1, "default": frappe.datetime.add_months(frappe.datetime.get_today(), -12)},
		{"fieldname": "to_date", "label": "To", "fieldtype": "Date", "reqd": 1, "default": frappe.datetime.get_today()}
	],
};
