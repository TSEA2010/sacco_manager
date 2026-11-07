// Copyright (c) 2026, Trident SACCO and contributors
// For license information, please see license.txt

frappe.query_reports["Guarantor Exposure"] = {
	filters: [
		{"fieldname": "member", "label": "Member", "fieldtype": "Link", "reqd": 0, "options": "SACCO Member"}
	],
};
