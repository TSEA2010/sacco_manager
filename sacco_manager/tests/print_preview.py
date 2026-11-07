"""Dev helper: render the member file for a few real members to PDFs in /tmp/member_files.
bench --site sacco.test execute sacco_manager.tests.print_preview.run"""

import os

import frappe
from frappe.utils.pdf import get_pdf


def _save(path, html):
	with open(path + ".html", "w") as fh:
		fh.write(html)
	try:
		with open(path + ".pdf", "wb") as fh:
			fh.write(get_pdf(html))
	except Exception as e:  # wkhtmltopdf cannot reach the test host name; HTML is still saved
		print("pdf skipped:", str(e).splitlines()[-1][:80])


def run(limit=3):
	out = "/tmp/member_files"
	os.makedirs(out, exist_ok=True)
	picks = frappe.db.sql_list("""SELECT m.name FROM `tabSACCO Member` m JOIN `tabLoan` l ON l.applicant = m.customer
		AND l.docstatus = 1 AND l.status IN ('Disbursed','Partially Disbursed','Active') GROUP BY m.name
		ORDER BY COUNT(*) DESC LIMIT %s""", int(limit))
	picks += frappe.db.sql_list("SELECT name FROM `tabSACCO Member` WHERE customer IS NULL OR customer='' LIMIT 1")
	for name in picks:
		html = frappe.get_print("SACCO Member", name, print_format="SACCO Member File")
		_save(f"{out}/{name}", html)
		print("member", name, len(html))
	cust = frappe.db.get_value("SACCO Member", picks[0], "customer")
	html = frappe.get_print("Customer", cust, print_format="SACCO Customer File")
	_save(f"{out}/customer_{picks[0]}", html)
	plain = frappe.db.sql_list("SELECT c.name FROM `tabCustomer` c LEFT JOIN `tabSACCO Member` m ON m.customer=c.name WHERE m.name IS NULL LIMIT 1")
	if plain:
		html = frappe.get_print("Customer", plain[0], print_format="SACCO Customer File")
		print("non-member customer ok", "not linked to a SACCO member" in html)
	print("default", frappe.get_meta("Customer").default_print_format, frappe.get_meta("SACCO Member").default_print_format)
	frappe.db.rollback()
