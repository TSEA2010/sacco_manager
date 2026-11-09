# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Tests for passwordless member login (National ID + SMS one-time code).

Run: bench --site test_site run-tests --app sacco_manager --module sacco_manager.tests.test_otp
"""

import re
import secrets

import frappe

try:  # Frappe v16 renamed the base class
	from frappe.tests import IntegrationTestCase as _TestCase
except ImportError:  # Frappe v15
	from frappe.tests.utils import FrappeTestCase as _TestCase

from sacco_manager.auth import otp

def _uniq_phone():
	"""A distinct valid Kenyan MSISDN per member (the phone field is unique)."""
	return "2547" + f"{secrets.randbelow(10 ** 8):08d}"


def _make_member(**overrides):
	suffix = frappe.generate_hash(length=6)
	data = {
		"doctype": "SACCO Member",
		"first_name": "Test",
		"last_name": suffix,
		"id_number": "ID" + suffix,
		"phone": _uniq_phone(),
		"status": "Active",
	}
	data.update(overrides)
	return frappe.get_doc(data).insert(ignore_permissions=True)


class TestMemberOtpLogin(_TestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		# A bare ERPNext test site (no setup wizard) can lack the root nodes that creating a member's
		# Customer needs. Provision them so the test stands on its own (CI installs on a bare site too).
		if not frappe.db.exists("Customer Group", "All Customer Groups"):
			frappe.get_doc({"doctype": "Customer Group", "customer_group_name": "All Customer Groups",
				"is_group": 1}).insert(ignore_permissions=True)
		if not frappe.db.exists("Territory", "All Territories"):
			frappe.get_doc({"doctype": "Territory", "territory_name": "All Territories",
				"is_group": 1}).insert(ignore_permissions=True)
		# before_insert on SACCO Member needs a naming code; the portal gate needs portal_enabled.
		frappe.db.set_single_value("SACCO Settings", "naming_code", "TST")
		if frappe.get_meta("SACCO Settings").has_field("portal_enabled"):
			frappe.db.set_single_value("SACCO Settings", "portal_enabled", 1)

	def setUp(self):
		self.sent = []
		self._orig_deliver = otp._deliver_sms
		otp._deliver_sms = lambda phone, message: self.sent.append((phone, message))
		# In a real HTTP request frappe.local.login_manager exists; the test runner has none, so stub
		# it. login_as itself is Frappe's code — we only need to verify the right user is signed in.
		frappe.local.login_manager = type(
			"_LM", (), {"login_as": staticmethod(lambda user: frappe.set_user(user))}
		)()

	def tearDown(self):
		otp._deliver_sms = self._orig_deliver
		frappe.set_user("Administrator")

	def _code_from_sms(self):
		self.assertTrue(self.sent, "no SMS was sent")
		m = re.search(r"\b(\d{6})\b", self.sent[-1][1])
		self.assertIsNotNone(m, "no 6-digit code in the SMS")
		return m.group(1)

	# ---- request ----------------------------------------------------------
	def test_request_sends_and_stores_hashed_code(self):
		member = _make_member()
		res = otp.request_login_otp(member.id_number)
		self.assertTrue(res["sent"])
		code = self._code_from_sms()
		row = frappe.get_last_doc("SACCO Login Code", filters={"member": member.name})
		self.assertEqual(row.status, "Sent")
		self.assertNotIn(code, row.code_hash)  # stored hashed, never in the clear
		self.assertEqual(row.phone, member.phone)

	def test_unknown_id_is_generic_and_sends_nothing(self):
		res = otp.request_login_otp("NOT-A-MEMBER-999")
		self.assertTrue(res["sent"])  # same answer as a real member
		self.assertEqual(self.sent, [])

	def test_member_with_unusable_phone_sends_nothing(self):
		member = _make_member()
		# phone is mandatory, but an imported member can hold a number that is not a valid KE mobile.
		frappe.db.set_value("SACCO Member", member.name, "phone", "123")
		res = otp.request_login_otp(member.id_number)
		self.assertTrue(res["sent"])  # still generic
		self.assertEqual(self.sent, [])  # but nothing could be sent

	def test_rate_limit_is_silent_and_stops_sending(self):
		member = _make_member()
		for _ in range(otp.MAX_SENDS_PER_HOUR):
			otp.request_login_otp(member.id_number)
		self.assertEqual(len(self.sent), otp.MAX_SENDS_PER_HOUR)
		# The next request is throttled: same generic reply, but no new SMS (no distinct error that
		# would reveal membership).
		res = otp.request_login_otp(member.id_number)
		self.assertTrue(res["sent"])
		self.assertEqual(len(self.sent), otp.MAX_SENDS_PER_HOUR)

	def test_throttle_is_silent_for_unknown_id(self):
		# Hammering one unknown ID never yields a distinct error and never sends — identical to the
		# generic reply a member gets, so throttling can't be used to tell members apart.
		uid = "UNKNOWN-" + frappe.generate_hash(length=6)
		for _ in range(otp.MAX_SENDS_PER_HOUR + 2):
			res = otp.request_login_otp(uid)
			self.assertTrue(res["sent"])
		self.assertEqual(self.sent, [])

	# ---- verify -----------------------------------------------------------
	def test_verify_wrong_code_raises_and_counts_attempt(self):
		member = _make_member()
		otp.request_login_otp(member.id_number)
		with self.assertRaises(frappe.AuthenticationError):
			otp.verify_login_otp(member.id_number, "000000")
		row = frappe.get_last_doc("SACCO Login Code", filters={"member": member.name})
		self.assertEqual(row.attempts, 1)
		self.assertEqual(row.status, "Sent")

	def test_verify_correct_code_logs_in_and_creates_user(self):
		member = _make_member()
		otp.request_login_otp(member.id_number)
		code = self._code_from_sms()
		res = otp.verify_login_otp(member.id_number, code)
		self.assertEqual(res["redirect"], "/sacco-portal")

		member.reload()
		self.assertTrue(member.user_id)
		self.assertTrue(member.portal_user_created)
		self.assertEqual(frappe.session.user, member.user_id)
		# code is burned
		row = frappe.get_last_doc("SACCO Login Code", filters={"member": member.name})
		self.assertEqual(row.status, "Used")

	def test_code_cannot_be_reused(self):
		member = _make_member()
		otp.request_login_otp(member.id_number)
		code = self._code_from_sms()
		otp.verify_login_otp(member.id_number, code)
		frappe.set_user("Administrator")
		with self.assertRaises(frappe.AuthenticationError):
			otp.verify_login_otp(member.id_number, code)

	def test_login_user_is_dedicated_not_the_members_email(self):
		# Even when the member has a real email (which could be an employee's ESS login on a shared
		# ERP site), the SACCO login is a separate @sacco.* account carrying only the Sacco Member
		# role — so an employee's account and its PIN sign-in are never touched.
		email = f"member.{frappe.generate_hash(length=6)}@example.com"
		member = _make_member(email_id=email)
		otp.request_login_otp(member.id_number)
		code = self._code_from_sms()
		otp.verify_login_otp(member.id_number, code)
		member.reload()
		self.assertNotEqual(member.user_id, email)
		self.assertTrue(member.user_id.endswith("@" + otp.SYNTHETIC_EMAIL_DOMAIN))
		self.assertEqual(frappe.session.user, member.user_id)
		roles = set(frappe.get_roles(member.user_id))
		self.assertIn(otp.SACCO_MEMBER_ROLE, roles)
		self.assertNotIn("Employee", roles)
