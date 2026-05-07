# Copyright (c) 2026, TSEA and contributors
# For license information, please see license.txt
import frappe
from frappe.model.document import Document
from frappe.utils import validate_email_address


def get_naming_code():
    naming_code = frappe.db.get_single_value("SACCO Settings", "naming_code")
    if not naming_code:
        frappe.throw(
            msg="Please set the <b>Naming Code</b> in SACCO Settings before creating members.",
            title="Naming Code Not Configured"
        )
    return naming_code.strip().upper()


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
    naming_code = get_naming_code()
    prefix = f"{naming_code}-"

    last = frappe.db.sql(
        """
        SELECT member_no FROM `tabSACCO Member`
        WHERE member_no LIKE %s
        ORDER BY member_no DESC
        LIMIT 1
        """,
        (f"{prefix}%",)
    )

    if last and last[0][0]:
        try:
            last_no = int(last[0][0].replace(prefix, "", 1))
            next_no = last_no + 1
        except ValueError:
            next_no = 1
    else:
        next_no = 1

    return f"{prefix}{next_no:04d}"