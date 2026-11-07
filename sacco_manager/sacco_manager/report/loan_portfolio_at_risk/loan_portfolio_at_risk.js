// Copyright (c) 2026, Trident SACCO and contributors
// For license information, please see license.txt

frappe.query_reports["Loan Portfolio at Risk"] = {
	filters: [
		{"fieldname": "as_at", "label": "As At", "fieldtype": "Date", "reqd": 1, "default": frappe.datetime.get_today()},
		{"fieldname": "branch", "label": "Branch", "fieldtype": "Link", "reqd": 0, "options": "Branch"},
		{"fieldname": "loan_product", "label": "Loan Product", "fieldtype": "Link", "reqd": 0, "options": "Loan Product"}
	],
};
