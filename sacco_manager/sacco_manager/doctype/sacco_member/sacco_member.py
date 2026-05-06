# Copyright (c) 2026, TSEA and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import validate_email_address

class SACCOMember(Document):
    def before_insert(self):
        if not self.member_no:
            self.member_no = get_next_member_no()

    def validate(self):
        if self.email_id:
            if not validate_email_address(self.email_id):
                frappe.throw("Invalid email address: {0}".format(self.email_id))

@frappe.whitelist()
def get_next_member_no():
    last = frappe.db.sql("""
        SELECT member_no FROM `tabSACCO Member`
        WHERE member_no LIKE 'TSEA-%'
        ORDER BY member_no DESC
        LIMIT 1
    """)

    if last and last[0][0]:
        last_no = int(last[0][0].split('-')[1])
        next_no = last_no + 1
    else:
        next_no = 1

    return f"TSEA-{next_no:04d}"