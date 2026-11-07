app_name = "sacco_manager"
app_title = "Trident SACCO"
app_publisher = "Trident Savings and Credit Co-operative Society"
app_description = "SACCO membership, savings, shares, loans and M-Pesa collections on ERPNext + Lending"
app_email = "finance@astralind.com"
app_license = "mit"

required_apps = ["frappe/erpnext", "frappe/lending"]

# ---------------------------------------------------------------------------
# Install / migrate: roles, role profiles, permissions, custom fields, party type
# ---------------------------------------------------------------------------
after_install = "sacco_manager.setup.after_install"
after_migrate = ["sacco_manager.setup.after_migrate"]

# ---------------------------------------------------------------------------
# Lending integration
# ---------------------------------------------------------------------------
override_doctype_class = {
	"Loan Repayment": "sacco_manager.overrides.loan_repayment.CustomLoanRepayment",
}

doc_events = {
	"Loan Application": {
		"validate": "sacco_manager.overrides.loan_application.validate",
	},
}

# The old daily "delinquency interest" job was removed: it charged 1% of the original loan to
# every loan, paid or not. Late-payment penalties now come from Lending itself (Loan Product
# -> Penalty Interest Rate and grace period), which charges only on overdue instalments.
scheduler_events = {
	"hourly": ["sacco_manager.mobile_money.allocation.retry_unallocated"],
	"daily": ["sacco_manager.tasks.mark_dormant_accounts"],
}

website_route_rules = [
	{"from_route": "/guarantor_confirm", "to_route": "guarantor-confirm"},
]
