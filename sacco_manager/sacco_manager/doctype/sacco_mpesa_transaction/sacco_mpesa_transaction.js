// Copyright (c) 2026, Trident SACCO and contributors
// For license information, please see license.txt

frappe.ui.form.on("SACCO M-Pesa Transaction", {
	refresh(frm) {
		const colors = { Allocated: "green", Paid: "green", Unallocated: "orange", Pending: "blue", Failed: "red", Reversed: "grey" };
		frm.page.set_indicator(frm.doc.status, colors[frm.doc.status] || "grey");
		if (frm.doc.status !== "Unallocated" || frm.doc.direction !== "Inbound") return;
		frm.add_custom_button(__("Allocate"), () => {
			const d = new frappe.ui.Dialog({
				title: __("Allocate {0} - KES {1}", [frm.doc.receipt_number || frm.doc.name, format_number(frm.doc.amount)]),
				fields: [
					{ fieldname: "member", label: __("Member"), fieldtype: "Link", options: "SACCO Member", reqd: 1, default: frm.doc.member },
					{ fieldname: "target", label: __("Allocate To"), fieldtype: "Select", reqd: 1,
						options: "Savings\nLoan Repayment\nShares\nRegistration Fee", default: "Savings" },
					{ fieldname: "savings_account", label: __("Savings Account"), fieldtype: "Link", options: "SACCO Savings Account",
						depends_on: "eval:doc.target=='Savings'",
						get_query: () => ({ filters: { member: d.get_value("member"), status: "Active" } }) },
					{ fieldname: "loan", label: __("Loan"), fieldtype: "Link", options: "Loan",
						depends_on: "eval:doc.target=='Loan Repayment'" },
					{ fieldname: "note", label: __("Note"), fieldtype: "Small Text" },
				],
				primary_action_label: __("Allocate"),
				primary_action(v) {
					frappe.call({ method: "sacco_manager.mobile_money.api.allocate_manually", freeze: true,
						args: { txn: frm.doc.name, target: v.target, member: v.member, savings_account: v.savings_account, loan: v.loan, note: v.note } })
						.then(() => { d.hide(); frm.reload_doc(); });
				},
			});
			d.show();
		}).addClass("btn-primary");
	},
});
