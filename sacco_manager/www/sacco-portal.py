import frappe

def get_context(context):
    if frappe.session.user == "Guest":
        frappe.local.flags.redirect_location = "/login?redirect-to=/sacco-portal"
        raise frappe.Redirect
    context.no_cache = 1
    context.show_sidebar = 0