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
		frm.add_custom_button(__("Management pack (month)"), () => {
			const d = new frappe.ui.Dialog({ title: __("Management pack"), fields: [
				{ fieldname: "month", label: __("Month"), fieldtype: "Int", reqd: 1, default: new Date().getMonth() || 12 },
				{ fieldname: "year", label: __("Year"), fieldtype: "Int", reqd: 1, default: new Date().getMonth() ? new Date().getFullYear() : new Date().getFullYear() - 1 },
				{ fieldname: "send", label: __("Email to recipients"), fieldtype: "Check" },
			], primary_action_label: __("Generate"), primary_action(v) {
				frappe.call({ method: "sacco_manager.management_pack.generate_management_pack", args: v, freeze: true,
					freeze_message: __("Building pack...") }).then((r) => { d.hide(); window.open(r.message); });
			} });
			d.show();
		}, __("Reports"));
		frm.add_custom_button(__("AGM pack (year)"), () => {
			const y = new Date().getFullYear();
			const d = new frappe.ui.Dialog({ title: __("AGM pack"), fields: [
				{ fieldname: "from_date", label: __("From"), fieldtype: "Date", reqd: 1, default: `${y - 1}-01-01` },
				{ fieldname: "to_date", label: __("To"), fieldtype: "Date", reqd: 1, default: `${y - 1}-12-31` },
				{ fieldname: "dividend_rate", label: __("Proposed dividend rate (%)"), fieldtype: "Percent" },
			], primary_action_label: __("Generate"), primary_action(v) {
				frappe.call({ method: "sacco_manager.management_pack.generate_agm_pack", args: v, freeze: true,
					freeze_message: __("Building pack...") }).then((r) => { d.hide(); window.open(r.message); });
			} });
			d.show();
		}, __("Reports"));
		frm.add_custom_button(__("Database size"), () =>
			frappe.call("sacco_manager.housekeeping.storage_report").then((r) => {
				const d = r.message, dp = d.dpd || {};
				const rows = d.tables.map((x) => `<tr><td>${frappe.utils.escape_html(x.table)}</td><td class="text-right">${format_number(x.rows, null, 0)}</td>
					<td class="text-right">${x.mb}</td></tr>`).join("");
				const note = dp.enabled ? __("Daily arrears log: {0} rows, {1} can be removed (older than {2} days, not month-end).",
					[format_number(dp.rows, null, 0), format_number(dp.removable, null, 0), dp.keep_days]) : __("Arrears log pruning is off.");
				const dlg = frappe.msgprint({ title: __("Database size"), wide: true, message: `<p>${note}<br>${__("Cancelled GL rows")}:
					${format_number(d.cancelled_gl_rows, null, 0)} ${__("of")} ${format_number(d.gl_rows, null, 0)}</p>
					<table class="table table-sm"><tr><th>${__("Table")}</th><th class="text-right">${__("Rows")}</th><th class="text-right">MB</th></tr>${rows}</table>`,
					primary_action: dp.enabled && dp.removable ? { label: __("Prune arrears log now"), action: () =>
						frappe.call("sacco_manager.housekeeping.run_now").then(() => {
							dlg.hide();
							frappe.show_alert({ message: __("Pruning started in the background"), indicator: "green" });
						}) } : null });
			}), __("Tools"));
		frm.add_custom_button(__("Sync members to customers"), () =>
			frappe.call({ method: "sacco_manager.sacco_manager.doctype.sacco_member.sacco_member.bulk_sync_members_to_customers", freeze: true })
				.then((r) => frappe.msgprint(__("Synced {0} of {1}.", [r.message.success, r.message.total])))
		);
	},
});
