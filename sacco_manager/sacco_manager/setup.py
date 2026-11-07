# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
# sacco_manager/sacco_manager/setup.py
"""
Idempotent setup steps for sacco_manager.

Journal Entries posted by this app use "SACCO Member" as a custom
Party Type (party_type / party on the Journal Entry Account row) so
that member balances show up correctly in the Party ledger / GL.

Party Type isn't a fixture-friendly doctype in the usual sense (it's
config, not export data), so instead of relying on someone creating it
by hand in the UI, we create it here and wire it to run after every
`bench migrate` and right after the app is installed. That way a fresh
site (or the client's own instance later) never breaks GL posting for
lack of this one record.
"""
import frappe


def ensure_party_type():
    """Create the 'SACCO Member' Party Type if it doesn't already exist."""
    if frappe.db.exists("Party Type", "SACCO Member"):
        return

    party_type = frappe.new_doc("Party Type")
    party_type.party_type = "SACCO Member"
    party_type.account_type = "Receivable"
    party_type.insert(ignore_permissions=True)
    frappe.db.commit()
    frappe.logger("sacco_manager").info("Created Party Type: SACCO Member")
