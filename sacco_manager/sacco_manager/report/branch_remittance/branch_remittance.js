// Copyright (c) 2026, Trident SACCO and contributors
// For license information, please see license.txt

frappe.query_reports["Branch Remittance"] = {
	filters: [
		{ fieldname: "branch", label: __("Employer / Branch"), fieldtype: "Link", options: "Branch" },
		{ fieldname: "month", label: __("Month"), fieldtype: "Select",
			options: "\nJanuary\nFebruary\nMarch\nApril\nMay\nJune\nJuly\nAugust\nSeptember\nOctober\nNovember\nDecember" },
		{ fieldname: "year", label: __("Year"), fieldtype: "Int", default: new Date().getFullYear() },
	],
};
