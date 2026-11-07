// Copyright (c) 2026, Trident SACCO and contributors
// For license information, please see license.txt

frappe.query_reports["Defaulters and Guarantors"] = {
	filters: [
		{"fieldname": "as_at", "label": "As At", "fieldtype": "Date", "reqd": 1, "default": frappe.datetime.get_today()},
		{"fieldname": "min_days", "label": "Minimum Days in Arrears", "fieldtype": "Int", "reqd": 0, "default": 30},
		{"fieldname": "branch", "label": "Branch", "fieldtype": "Link", "reqd": 0, "options": "Branch"}
	],
};
