// Copyright (c) 2026, Trident SACCO and contributors
// For license information, please see license.txt

frappe.ui.form.on("SACCO Settings", {
	setup(frm) {
		const q = (root, types) => () => {
			const filters = { company: frm.doc.company, is_group: 0, root_type: ["in", root] };
			if (types) filters.account_type = ["in", types];
			return { filters };
		};
		frm.set_query("cash_account", q(["Asset"], ["Bank", "Cash"]));
		frm.set_query("savings_liability_account", q(["Liability"]));
		frm.set_query("share_capital_account", q(["Equity", "Liability"]));
		frm.set_query("income_account", q(["Income"]));
		frm.set_query("interest_expense_account", q(["Expense"]));
		frm.set_query("penalty_income_account", q(["Income"]));
		frm.set_query("registration_fee_income_account", q(["Income"]));
		frm.set_query("branch_receivable_account", q(["Asset", "Liability"]));
		frm.set_query("loan_topup_clearing_account", q(["Asset", "Liability"]));
		frm.set_query("withholding_tax_account", q(["Liability"]));
		frm.set_query("dividend_appropriation_account", q(["Equity"]));
		frm.set_query("dividend_payable_account", q(["Liability"]));
	},
	refresh(frm) {
		frm.add_custom_button(__("Create missing SACCO accounts"), () =>
			frappe.call({ method: "sacco_manager.sacco_manager.doctype.sacco_settings.sacco_settings.create_missing_accounts", freeze: true })
				.then((r) => { frappe.msgprint(r.message && r.message.length ? __("Created: {0}", [r.message.join(", ")]) : __("Nothing to create.")); frm.reload_doc(); })
		);
		frm.add_custom_button(__("Sync members to customers"), () =>
			frappe.call({ method: "sacco_manager.sacco_manager.doctype.sacco_member.sacco_member.bulk_sync_members_to_customers", freeze: true })
				.then((r) => frappe.msgprint(__("Synced {0} of {1}.", [r.message.success, r.message.total])))
		);
	},
});
