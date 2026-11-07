// Copyright (c) 2026, Trident SACCO and contributors
// For license information, please see license.txt

frappe.query_reports["Member Data Quality"] = {
	filters: [
		{"fieldname": "scope", "label": "Scope", "fieldtype": "Select", "reqd": 0, "options": "Current or with balance\nAll members", "default": "Current or with balance"}
	],
};
