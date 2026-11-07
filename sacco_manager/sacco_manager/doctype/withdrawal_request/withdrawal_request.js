// Copyright (c) 2026, Trident SACCO and contributors
// For license information, please see license.txt

frappe.ui.form.on("Withdrawal Request", {
	setup(frm) {
		frm.set_query("savings_account", () => ({ filters: { member: frm.doc.member, status: ["!=", "Closed"] } }));
		frm.set_query("payment_account", () => ({ filters: { account_type: ["in", ["Bank", "Cash"]], is_group: 0 } }));
	},
	refresh(frm) {
		const colors = { "Pending Approval": "orange", Approved: "blue", Paid: "green", Rejected: "red" };
		if (frm.doc.status) frm.page.set_indicator(__(frm.doc.status), colors[frm.doc.status] || "grey");
		if (frm.doc.docstatus === 0 && !frm.is_new()) {
			frm.dashboard.set_headline(__("Submitting this request approves it. The approver must be a different user from the one who captured it."));
		}
		if (frm.doc.docstatus === 1 && frm.doc.payment_mode === "M-Pesa" && frm.doc.status === "Approved") {
			frm.add_custom_button(__("Retry M-Pesa payout"), () =>
				frappe.call({ method: "sacco_manager.mobile_money.b2c.retry_payout", args: { withdrawal: frm.doc.name }, freeze: true })
					.then(() => frm.reload_doc())
			);
		}
	},
	member(frm) {
		frm.set_value("savings_account", "");
	},
	savings_account(frm) {
		if (!frm.doc.savings_account) return;
		frappe.call({
			method: "sacco_manager.sacco_manager.doctype.withdrawal_request.withdrawal_request.get_withdrawable",
			args: { savings_account: frm.doc.savings_account },
		}).then((r) => frm.set_value("withdrawable_balance", r.message));
	},
});
