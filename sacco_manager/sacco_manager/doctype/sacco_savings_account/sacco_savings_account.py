# Copyright (c) 2026, TSEA and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

class SACCOSavingsAccount(Document):
    def autoname(self):
        member = frappe.db.get_value("SACCO Member", self.member, "member_no")
        count = frappe.db.count("SACCO Savings Account", {"member": self.member})
        self.name = f"{member}-SAV-{str(count+1).zfill(2)}"

    def validate(self):
        if not self.interest_rate:
            defaults = {"Ordinary": 3.0, "Fixed": 8.0, "Holiday": 2.0}
            self.interest_rate = defaults.get(self.account_type, 3.0)