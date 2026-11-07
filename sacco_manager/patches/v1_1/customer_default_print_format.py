# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Make "SACCO Customer File" the default print for Customer, unless someone already chose one."""

import frappe
from frappe.custom.doctype.property_setter.property_setter import make_property_setter


def execute():
	if not frappe.db.exists("Print Format", "SACCO Customer File"):
		return
	if frappe.get_meta("Customer").default_print_format:
		return
	make_property_setter("Customer", None, "default_print_format", "SACCO Customer File", "Data", for_doctype=True,
		validate_fields_for_doctype=False)
