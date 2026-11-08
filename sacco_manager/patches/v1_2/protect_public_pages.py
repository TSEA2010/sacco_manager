# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Keep the SACCO's member-facing pages out of search engines and away from strangers:
a random joining code for /join (if none is set) and Disallow rules in robots.txt."""

import secrets

import frappe

PATHS = ("/join", "/member-login", "/sacco-portal", "/guarantor-confirm")
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I to avoid misreading


def execute():
	if not (frappe.db.get_single_value("SACCO Settings", "join_code") or "").strip():
		frappe.db.set_single_value("SACCO Settings", "join_code", "".join(secrets.choice(ALPHABET) for _ in range(8)))
	robots = frappe.db.get_single_value("Website Settings", "robots_txt") or ""
	missing = [p for p in PATHS if f"Disallow: {p}" not in robots]
	if missing:
		block = ("" if not robots.strip() else robots.rstrip() + "\n\n")
		if "User-agent: *" not in robots:
			block += "User-agent: *\n"
		block += "\n".join(f"Disallow: {p}" for p in missing) + "\n"
		frappe.db.set_single_value("Website Settings", "robots_txt", block)
