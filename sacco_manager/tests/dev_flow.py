"""Dev-only: approve a browser-submitted application on the test site and give the new portal user a
throwaway password, so the member login page can be tried end to end. Commits (test site only).
bench --site sacco.test execute sacco_manager.tests.dev_flow.approve --kwargs "{'name': 'MAPP-...', 'pw': '...'}" """

import frappe
from frappe.utils.password import update_password


def approve(name, pw):
	from sacco_manager.sacco_manager.doctype.sacco_membership_application.sacco_membership_application import approve as do

	frappe.set_user("Administrator")
	res = do(name, create_portal_user=1)
	user = frappe.db.get_value("SACCO Member", res["member"], "user_id")
	update_password(user, pw)
	frappe.db.commit()
	print(res["member_no"], user, frappe.db.get_value("SACCO Member", res["member"], "phone"))
