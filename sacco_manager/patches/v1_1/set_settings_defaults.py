# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Singles do not get field defaults: a new setting that was never stored is saved as 0/blank the
first time anyone saves SACCO Settings. Store each missing field's declared default once."""

import frappe


def execute():
	meta = frappe.get_meta("SACCO Settings")
	stored = set(frappe.db.sql_list("SELECT field FROM `tabSingles` WHERE doctype='SACCO Settings'"))
	for df in meta.fields:
		if df.fieldname in stored or df.default in (None, "") or df.fieldtype in ("Section Break", "Column Break", "Tab Break"):
			continue
		frappe.db.set_single_value("SACCO Settings", df.fieldname, df.default)
	frappe.clear_cache(doctype="SACCO Settings")
