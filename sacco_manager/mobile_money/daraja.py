# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Minimal Safaricom Daraja client (OAuth, C2B URL registration, STK Push, B2C)."""

import base64
import secrets

import frappe
import requests
from frappe import _
from frappe.utils import cint, get_url, now_datetime

BASE = {"Sandbox": "https://sandbox.safaricom.co.ke", "Production": "https://api.safaricom.co.ke"}
TIMEOUT = 30
# Daraja rejects callback URLs that contain these words, so the callback module is "mobile_money".
FORBIDDEN_URL_WORDS = ("mpesa", "m-pesa", "safaricom", "exec", "cmd", "sql", "query")


class DarajaError(Exception):
	pass


class Daraja:
	def __init__(self, account):
		self.acc = account if not isinstance(account, str) else frappe.get_doc("SACCO M-Pesa Account", account)
		if not self.acc.enabled:
			frappe.throw(_("M-Pesa account {0} is disabled.").format(self.acc.name))
		self.base = BASE[self.acc.environment]

	# ------------------------------------------------------------------
	def _secret(self, field):
		return self.acc.get_password(field, raise_exception=False)

	def token(self):
		key = f"sacco_daraja_token:{self.acc.name}:{self.acc.environment}"
		cached = frappe.cache().get_value(key)
		if cached:
			return cached
		auth = base64.b64encode(f"{self._secret('consumer_key')}:{self._secret('consumer_secret')}".encode()).decode()
		r = requests.get(f"{self.base}/oauth/v1/generate", params={"grant_type": "client_credentials"},
			headers={"Authorization": f"Basic {auth}"}, timeout=TIMEOUT)
		if r.status_code != 200:
			raise DarajaError(f"OAuth failed ({r.status_code}): {r.text[:300]}")
		data = r.json()
		frappe.cache().set_value(key, data["access_token"], expires_in_sec=max(60, cint(data.get("expires_in", 3599)) - 120))
		return data["access_token"]

	def post(self, path, payload):
		r = requests.post(f"{self.base}{path}", json=payload, timeout=TIMEOUT,
			headers={"Authorization": f"Bearer {self.token()}", "Content-Type": "application/json"})
		try:
			data = r.json()
		except ValueError:
			data = {"raw": r.text[:1000]}
		if r.status_code >= 400:
			raise DarajaError(f"{path} failed ({r.status_code}): {frappe.as_json(data)[:500]}")
		return data

	# ------------------------------------------------------------------
	def callback_url(self, method):
		base = (self.acc.callback_base_url or get_url()).rstrip("/")
		token = self._secret("callback_token")
		if not token:
			frappe.throw(_("Save the M-Pesa account once to generate its callback token."))
		url = f"{base}/api/method/sacco_manager.mobile_money.callbacks.{method}?sc={self.acc.name}&t={token}"
		low = url.lower()
		bad = [w for w in FORBIDDEN_URL_WORDS if w in low.split("?")[0]]
		if bad:
			frappe.throw(_("Callback URL path contains words Safaricom rejects: {0}").format(", ".join(bad)))
		if not low.startswith("https://") and self.acc.environment == "Production":
			frappe.throw(_("Production callbacks must use https."))
		return url

	def register_c2b_urls(self):
		payload = {
			"ShortCode": self.acc.business_shortcode,
			"ResponseType": "Completed",
			"ConfirmationURL": self.callback_url("c2b_confirm"),
			"ValidationURL": self.callback_url("c2b_validate"),
		}
		return self.post("/mpesa/c2b/v2/registerurl", payload)

	def stk_push(self, phone, amount, account_reference, description):
		ts = now_datetime().strftime("%Y%m%d%H%M%S")
		shortcode = self.acc.business_shortcode
		passkey = self._secret("passkey")
		if not passkey:
			frappe.throw(_("STK Push is not configured (no passkey) on {0}.").format(self.acc.name))
		is_till = self.acc.shortcode_type.startswith("Till")
		payload = {
			"BusinessShortCode": shortcode,
			"Password": base64.b64encode(f"{shortcode}{passkey}{ts}".encode()).decode(),
			"Timestamp": ts,
			"TransactionType": "CustomerBuyGoodsOnline" if is_till else "CustomerPayBillOnline",
			"Amount": round(float(amount)),
			"PartyA": phone,
			"PartyB": self.acc.till_number if is_till and self.acc.till_number else shortcode,
			"PhoneNumber": phone,
			"CallBackURL": self.callback_url("stk_result"),
			"AccountReference": account_reference[:12],
			"TransactionDesc": description[:13],
		}
		return self.post("/mpesa/stkpush/v1/processrequest", payload)

	def b2c(self, phone, amount, remarks, occasion=""):
		if not (self.acc.initiator_name and self._secret("security_credential")):
			frappe.throw(_("B2C is not configured (initiator / security credential) on {0}.").format(self.acc.name))
		originator = f"SACCO-{secrets.token_hex(8)}"
		payload = {
			"OriginatorConversationID": originator,
			"InitiatorName": self.acc.initiator_name,
			"SecurityCredential": self._secret("security_credential"),
			"CommandID": "BusinessPayment",
			"Amount": round(float(amount)),
			"PartyA": self.acc.b2c_shortcode or self.acc.business_shortcode,
			"PartyB": phone,
			"Remarks": remarks[:100],
			"QueueTimeOutURL": self.callback_url("b2c_timeout"),
			"ResultURL": self.callback_url("b2c_result"),
			"Occasion": occasion[:100],
		}
		return originator, self.post("/mpesa/b2c/v3/paymentrequest", payload)
