# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Member phone / ID columns were Int, which stripped leading zeros (0720... -> 720...).
After the column becomes Data, rewrite phones to 2547XXXXXXXX so M-Pesa matching works."""

import frappe

from sacco_manager.utils import normalize_ke_phone


def execute():
	for m in frappe.get_all("SACCO Member", fields=["name", "phone", "contact_phone", "id_number", "email_id"]):
		updates = {}
		for field in ("phone", "contact_phone"):
			raw = m.get(field)
			if raw in (None, "", "0", 0):
				if raw is not None and str(raw) in ("0",):
					updates[field] = None
				continue
			norm = normalize_ke_phone(str(raw).split(".")[0])
			if norm and norm != str(raw):
				updates[field] = norm
		if m.id_number is not None and str(m.id_number).endswith(".0"):
			updates["id_number"] = str(m.id_number)[:-2]
		if m.email_id and m.email_id != m.email_id.strip().lower():
			updates["email_id"] = m.email_id.strip().lower()
		if updates:
			if updates.get("phone") and frappe.db.exists("SACCO Member", {"phone": updates["phone"], "name": ["!=", m.name]}):
				updates.pop("phone")  # unique index; leave for the Member Data Quality report
			frappe.db.set_value("SACCO Member", m.name, updates, update_modified=False)
