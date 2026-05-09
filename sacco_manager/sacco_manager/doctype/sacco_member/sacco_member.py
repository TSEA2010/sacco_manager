# Copyright (c) 2026, TSEA and contributors
# For license information, please see license.txt
import frappe
from frappe.model.document import Document
from frappe.utils import validate_email_address


MEMBER_PORTAL_DOCTYPES = [
    "SACCO Member",
    "SACCO Share Ledger",
    "SACCO Savings Transaction",
    "SACCO Savings Account",
    "Loan",
    "Loan Repayment",
]


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
        self.create_linked_customer()

    def validate(self):
        if self.email_id:
            if not validate_email_address(self.email_id):
                frappe.throw("Invalid email address: {0}".format(self.email_id))

        if self.is_user and not self.user_id:
            frappe.throw(
                msg="Please set the <b>User ID</b> (email) before enabling portal access.",
                title="User ID Required"
            )

    def on_update(self):
        # Only sync permissions if user was already created via the button
        if self.is_user and self.user_id and self.portal_user_created:
            self._sync_user_permissions()

        # Revoke access if portal access was turned off
        elif not self.is_user:
            before = self.get_doc_before_save()
            if before and before.user_id:
                self._revoke_portal_access(before.user_id)

    def on_trash(self):
        """Delete linked customer when member is deleted"""
        frappe.log_error(f"on_trash triggered for SACCO Member: {self.name}", "SACCO Member Deletion")
        
        if self.customer:
            frappe.log_error(f"Attempting to delete customer: {self.customer}", "SACCO Member Deletion")
            
            # First check if customer exists
            if frappe.db.exists("Customer", self.customer):
                try:
                    # Check if customer has any linked transactions
                    if self._customer_has_transactions():
                        frappe.throw(
                            f"Cannot delete Member: Customer {self.customer} has existing transactions. "
                            "Please cancel and delete them first."
                        )
                    
                    # Delete the customer
                    frappe.delete_doc("Customer", self.customer, force=True, ignore_permissions=True)
                    frappe.log_error(f"Successfully deleted customer: {self.customer}", "SACCO Member Deletion")
                    frappe.db.commit()
                    
                except frappe.LinkExistsError as e:
                    frappe.log_error(f"LinkExistsError: {str(e)}", "SACCO Member Deletion")
                    frappe.throw(
                        f"Cannot delete Member: Customer {self.customer} has existing transactions. "
                        "Please cancel and delete them first."
                    )
                except Exception as e:
                    frappe.log_error(frappe.get_traceback(), f"Failed to delete Customer {self.customer}")
                    frappe.msgprint(f"Warning: Could not delete linked Customer {self.customer}: {str(e)}")
            else:
                frappe.log_error(f"Customer {self.customer} does not exist", "SACCO Member Deletion")
        else:
            frappe.log_error("No customer linked to this member", "SACCO Member Deletion")

    def _customer_has_transactions(self):
        """Check if customer has any linked transactions"""
        if not self.customer:
            return False
        
        # Check for various transaction doctypes
        doctypes_to_check = [
            "Sales Invoice",
            "Payment Entry",
            "Journal Entry",
            "Quotation",
            "Sales Order",
            "Delivery Note"
        ]
        
        for doctype in doctypes_to_check:
            if frappe.db.exists(doctype, {"customer": self.customer}):
                frappe.log_error(f"Customer {self.customer} has transactions in {doctype}", "SACCO Member Deletion")
                return True
        
        # Also check using get_list for better performance
        for doctype in doctypes_to_check:
            docs = frappe.get_list(doctype, filters={"customer": self.customer}, limit=1)
            if docs:
                frappe.log_error(f"Customer {self.customer} has transactions in {doctype}", "SACCO Member Deletion")
                return True
        
        return False

    def _sync_user_permissions(self):
        filters = {
            "user": self.user_id,
            "allow": "SACCO Member",
            "for_value": self.name,
        }
        if not frappe.db.exists("User Permission", filters):
            frappe.get_doc({
                "doctype": "User Permission",
                "user": self.user_id,
                "allow": "SACCO Member",
                "for_value": self.name,
                "apply_to_all_doctypes": 1,
            }).insert(ignore_permissions=True)
            frappe.db.commit()

    def _revoke_portal_access(self, user_id):
        if frappe.db.exists("User", user_id):
            user = frappe.get_doc("User", user_id)
            user.roles = [r for r in user.roles if r.role != "Sacco Member"]
            user.save(ignore_permissions=True)

        frappe.db.delete("User Permission", {
            "user": user_id,
            "allow": "SACCO Member",
            "for_value": self.name,
        })
        frappe.db.commit()

    def create_linked_customer(self):
        # Ensure the "Sacco Member" customer group exists
        if not frappe.db.exists("Customer Group", "Sacco Member"):
            cg = frappe.get_doc({
                "doctype": "Customer Group",
                "customer_group_name": "Sacco Member",
                "parent_customer_group": "All Customer Groups"
            })
            cg.insert(ignore_permissions=True)
            frappe.db.commit()

        # Avoid duplicate customer creation on re-save
        if self.customer:
            return

        # Use full_name or member_name as the customer name
        customer_name = self.full_name or self.member_name

        # Check if a customer with this name already exists
        existing = frappe.db.get_value("Customer", {"customer_name": customer_name}, "name")
        if existing:
            self.customer = existing
            return

        customer = frappe.get_doc({
            "doctype": "Customer",
            "customer_name": customer_name,
            "customer_type": "Individual",
            "customer_group": "Sacco Member",
            "territory": frappe.db.get_single_value("Selling Settings", "territory") or "All Territories",
        })
        customer.insert(ignore_permissions=True)

        self.customer = customer.name
        self.customer_group = "Sacco Member"


@frappe.whitelist()
def create_portal_user(member_name):
    member = frappe.get_doc("SACCO Member", member_name)

    if not member.is_user or not member.user_id:
        frappe.throw("Enable portal access and set a User ID first.")

    if member.portal_user_created:
        frappe.throw("Portal user already exists for this member.")

    # Step 1: Create user if not exists
    if not frappe.db.exists("User", member.user_id):
        user = frappe.get_doc({
            "doctype": "User",
            "email": member.user_id,
            "first_name": member.first_name or member.full_name,
            "last_name": member.last_name or "",
            "enabled": 1,
            "user_type": "Website User",
            "send_welcome_email": 1,
        })
        user.insert(ignore_permissions=True)
        frappe.db.commit()  # Commit here so user is visible before role/permission inserts

    # Step 2: Assign role
    user = frappe.get_doc("User", member.user_id)
    if "Sacco Member" not in [r.role for r in user.get("roles")]:
        user.append("roles", {"role": "Sacco Member"})
        user.save(ignore_permissions=True)
        frappe.db.commit()

    # Step 3: Mark flag on member — on_update will handle permissions on next save
    frappe.db.set_value("SACCO Member", member_name, "portal_user_created", 1)
    frappe.db.commit()

    # Step 4: Sync permissions immediately
    member.reload()
    member._sync_user_permissions()

    return f"Portal user {member.user_id} created and permissions synced successfully."


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

@frappe.whitelist()
def create_customer_for_member(member_name):
    member = frappe.get_doc("SACCO Member", member_name)

    if member.customer:
        return f"Member {member_name} already has a linked Customer."

    member.create_linked_customer()
    member.save(ignore_permissions=True)

    return f"Customer {member.customer} created and linked to {member_name}."