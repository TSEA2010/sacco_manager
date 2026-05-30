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


def _ensure_customer_group(branch=None):
    """Return customer group name — uses branch if provided, else 'Sacco Member'."""
    group_name = branch.strip() if branch and branch.strip() else "Sacco Member"
    if not frappe.db.exists("Customer Group", group_name):
        frappe.get_doc({
            "doctype": "Customer Group",
            "customer_group_name": group_name,
            "parent_customer_group": "All Customer Groups",
        }).insert(ignore_permissions=True)
        frappe.db.commit()
    return group_name


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

    def after_insert(self):
        self._sync_customer_fields()
        self._sync_customer_contact()

    def on_update(self):
        self._sync_customer_fields()
        self._sync_customer_contact()

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

            if frappe.db.exists("Customer", self.customer):
                try:
                    if self._customer_has_transactions():
                        frappe.throw(
                            f"Cannot delete Member: Customer {self.customer} has existing transactions. "
                            "Please cancel and delete them first."
                        )

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

    # ------------------------------------------------------------------
    # Customer field sync
    # ------------------------------------------------------------------

    def _sync_customer_fields(self):
        """Push SACCO Member fields to the linked Customer doctype."""
        if not self.customer or not frappe.db.exists("Customer", self.customer):
            return

        customer = frappe.get_doc("Customer", self.customer)

        # Customer group: follow branch
        customer.customer_group = _ensure_customer_group(self.branch)

        # Direct field mappings
        field_map = {
            "joining_date": "joining_date",
            "phone":        "mobile_no",
            "kra_pin":      "tax_id",
            "date_of_birth":"date_of_birth",
            "marital_status":"marital_status",
            "email_id":     "email_id",
            "branch":       "branch",
        }
        for member_field, customer_field in field_map.items():
            value = self.get(member_field)
            if value is not None:
                customer.set(customer_field, value)

        customer.save(ignore_permissions=True)
        frappe.db.commit()

    def _sync_customer_contact(self):
        """Create or update a Contact linked to the Customer with the emergency/next-of-kin details."""
        if not self.customer or not frappe.db.exists("Customer", self.customer):
            return

        contact_name_val  = self.get("contact_name")
        contact_email_val = self.get("contact_email")
        contact_phone_val = self.get("contact_phone")
        relation_val      = self.get("relation")

        # Nothing to sync if no contact details provided
        if not any([contact_name_val, contact_email_val, contact_phone_val]):
            return

        # Look for an existing Contact linked to this Customer
        existing_link = frappe.db.get_value(
            "Dynamic Link",
            {"link_doctype": "Customer", "link_name": self.customer, "parenttype": "Contact"},
            "parent",
        )

        if existing_link and frappe.db.exists("Contact", existing_link):
            contact = frappe.get_doc("Contact", existing_link)
        else:
            contact = frappe.new_doc("Contact")
            contact.append("links", {
                "link_doctype": "Customer",
                "link_name": self.customer,
            })

        # Parse first/last name from contact_name
        parts = (contact_name_val or "").split(" ", 1)
        contact.first_name = parts[0] if parts else ""
        contact.last_name  = parts[1] if len(parts) > 1 else ""

        if relation_val:
            contact.designation = relation_val  # reuse designation for relation label

        # Phone
        if contact_phone_val:
            existing_phone = next(
                (p for p in contact.get("phone_nos") if p.phone == contact_phone_val), None
            )
            if not existing_phone:
                contact.phone_nos = []  # reset and re-add to avoid duplicates
                contact.append("phone_nos", {"phone": contact_phone_val, "is_primary_phone": 1})

        # Email
        if contact_email_val:
            existing_email = next(
                (e for e in contact.get("email_ids") if e.email_id == contact_email_val), None
            )
            if not existing_email:
                contact.email_ids = []
                contact.append("email_ids", {"email_id": contact_email_val, "is_primary": 1})

        contact.save(ignore_permissions=True)
        frappe.db.commit()

    # ------------------------------------------------------------------
    # Transaction / permission helpers
    # ------------------------------------------------------------------

    def _customer_has_transactions(self):
        if not self.customer:
            return False

        doctypes_to_check = [
            "Sales Invoice", "Payment Entry", "Journal Entry",
            "Quotation", "Sales Order", "Delivery Note",
        ]
        for doctype in doctypes_to_check:
            if frappe.get_list(doctype, filters={"customer": self.customer}, limit=1):
                frappe.log_error(
                    f"Customer {self.customer} has transactions in {doctype}",
                    "SACCO Member Deletion"
                )
                return True
        return False

    def _sync_user_permissions(self):
        for allow_doctype, for_value in [
            ("SACCO Member", self.name),
            ("Customer",     self.customer),
        ]:
            if not for_value:
                continue
            filters = {"user": self.user_id, "allow": allow_doctype, "for_value": for_value}
            if not frappe.db.exists("User Permission", filters):
                frappe.get_doc({
                    "doctype": "User Permission",
                    "user": self.user_id,
                    "allow": allow_doctype,
                    "for_value": for_value,
                    "apply_to_all_doctypes": 1,
                }).insert(ignore_permissions=True)
        frappe.db.commit()

    def _revoke_portal_access(self, user_id):
        if frappe.db.exists("User", user_id):
            user = frappe.get_doc("User", user_id)
            user.roles = [r for r in user.roles if r.role != "Sacco Member"]
            user.save(ignore_permissions=True)

        for allow_doctype, for_value in [
            ("SACCO Member", self.name),
            ("Customer",     self.customer),
        ]:
            frappe.db.delete("User Permission", {
                "user": user_id,
                "allow": allow_doctype,
                "for_value": for_value,
            })
        frappe.db.commit()

    def create_linked_customer(self):
        if self.customer:
            return

        group_name    = _ensure_customer_group(self.branch)
        customer_name = self.full_name or self.member_name

        existing = frappe.db.get_value("Customer", {"customer_name": customer_name}, "name")
        if existing:
            self.customer       = existing
            self.customer_group = group_name
            return

        customer = frappe.get_doc({
            "doctype":        "Customer",
            "customer_name":  customer_name,
            "customer_type":  "Individual",
            "customer_group": group_name,
            "territory": (
                frappe.db.get_single_value("Selling Settings", "territory") or "All Territories"
            ),
        })
        customer.insert(ignore_permissions=True)

        self.customer       = customer.name
        self.customer_group = group_name


# ------------------------------------------------------------------
# Whitelisted API methods
# ------------------------------------------------------------------

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
            "doctype":           "User",
            "email":             member.user_id,
            "first_name":        member.first_name or member.full_name,
            "last_name":         member.last_name or "",
            "enabled":           1,
            "user_type":         "Website User",
            "send_welcome_email": 1,
        })
        user.insert(ignore_permissions=True)
        frappe.db.commit()

    # Step 2: Assign role
    user = frappe.get_doc("User", member.user_id)
    if "Sacco Member" not in [r.role for r in user.get("roles")]:
        user.append("roles", {"role": "Sacco Member"})
        user.save(ignore_permissions=True)
        frappe.db.commit()

    # Step 3: Mark flag
    frappe.db.set_value("SACCO Member", member_name, "portal_user_created", 1)
    frappe.db.commit()

    # Step 4: Sync permissions (SACCO Member + Customer)
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

@frappe.whitelist()
def bulk_sync_members_to_customers():
    """Sync all SACCO Members with linked customers. Called from SACCO Settings."""
    frappe.only_for("System Manager")

    members = frappe.get_all(
        "SACCO Member",
        filters={"customer": ["!=", ""]},
        fields=["name"],
    )
    total = len(members)
    success = 0
    errors = []

    for m in members:
        try:
            doc = frappe.get_doc("SACCO Member", m.name)
            doc._sync_customer_fields()
            doc._sync_customer_contact()
            success += 1
        except Exception as e:
            errors.append({"member": m.name, "error": str(e)})

    frappe.db.commit()

    return {
        "total": total,
        "success": success,
        "errors": errors,
    }