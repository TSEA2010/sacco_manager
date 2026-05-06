import frappe

@frappe.whitelist()
def get_sacco_savings_balance(savings_account):
    balance = frappe.db.get_value("SACCO Savings Account", savings_account, "balance")
    return balance or 0