# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""The live site had Custom DocPerms on SACCO doctypes (incl. the education role "Academics User")
which override the role matrix shipped in the DocType JSON. Remove them so the app's
SACCO roles apply; adjust later only through the Role Permission Manager with care."""

import frappe


def execute():
	doctypes = frappe.get_all("DocType", filters={"module": "Sacco Manager"}, pluck="name")
	if doctypes:
		frappe.db.delete("Custom DocPerm", {"parent": ["in", doctypes]})
	frappe.clear_cache()
