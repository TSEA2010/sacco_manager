# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class SACCOMPesaTransaction(Document):
	def validate(self):
		if not self.is_new() and self.has_value_changed("amount") and self.status == "Allocated":
			frappe.throw(_("Amount of an allocated M-Pesa payment cannot change."))

	def on_trash(self):
		if self.status in ("Allocated", "Paid"):
			frappe.throw(_("Allocated / paid M-Pesa records cannot be deleted."))
