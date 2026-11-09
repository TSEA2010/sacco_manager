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
	"Journal Entry": {"validate": "sacco_manager.accounting.guard_control_accounts"},
	"Loan Write Off": {
		"before_submit": "sacco_manager.sacco_manager.doctype.sacco_board_approval.sacco_board_approval.check_write_off",
	},
	# SMS notifications (only when enabled in SACCO Settings; queued, never block the posting)
	"SACCO Savings Transaction": {"on_submit": "sacco_manager.notifications.on_savings_transaction_submit"},
	"Loan Repayment": {"on_submit": "sacco_manager.notifications.on_loan_repayment_submit"},
	"Loan Disbursement": {"validate": "sacco_manager.company_lending.apply_disbursement_account"},
	"SACCO Loan Guarantor": {"after_insert": "sacco_manager.notifications.on_guarantor_insert"},
}

# The old daily "delinquency interest" job was removed: it charged 1% of the original loan to
# every loan, paid or not. Late-payment penalties now come from Lending itself (Loan Product
# -> Penalty Interest Rate and grace period), which charges only on overdue instalments.
scheduler_events = {
	"hourly": ["sacco_manager.mobile_money.allocation.retry_unallocated"],
	"daily": [
		"sacco_manager.tasks.mark_dormant_accounts",
		"sacco_manager.auth.otp.clear_expired_codes",
	],
	"weekly": ["sacco_manager.notifications.weekly_arrears_reminders", "sacco_manager.housekeeping.weekly"],
	"cron": {
		# 07:00 on the 5th: previous month's management pack to the recipients in SACCO Settings
		"0 7 5 * *": ["sacco_manager.management_pack.monthly_job"],
	},
}

website_route_rules = [
	{"from_route": "/guarantor_confirm", "to_route": "guarantor-confirm"},
]

# ---------------------------------------------------------------------------
# Print formats: "SACCO Member File" / "SACCO Customer File" read their data through these
# ---------------------------------------------------------------------------
jinja = {
	"methods": [
		"sacco_manager.print_file.sacco_member_file",
		"sacco_manager.print_file.sacco_customer_file",
	],
}
