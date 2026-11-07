# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt

import secrets

import frappe
from frappe import _
from frappe.model.document import Document

from sacco_manager.accounting import assert_account


class SACCOMPesaAccount(Document):
	def validate(self):
		self.business_shortcode = (self.business_shortcode or "").strip()
		if not self.business_shortcode.isdigit():
			frappe.throw(_("Business shortcode must be numeric."))
		assert_account(self.gl_account, _("M-Pesa Clearing Account"), root_types=("Asset",), account_types=("Bank", "Cash"))
		assert_account(self.charges_account, _("M-Pesa Charges"), root_types=("Expense",))
		if not self.callback_token:
			self.callback_token = secrets.token_urlsafe(24)
		if self.environment == "Production" and self.callback_base_url and not self.callback_base_url.startswith("https://"):
			frappe.throw(_("Production callback URL must start with https://"))
