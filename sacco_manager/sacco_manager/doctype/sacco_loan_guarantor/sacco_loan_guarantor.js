// Copyright (c) 2026, Trident SACCO and contributors
// For license information, please see license.txt

frappe.ui.form.on("SACCO Loan Guarantor", {
	refresh(frm) {
		const color = { Pending: "orange", Confirmed: "green", Declined: "red" }[frm.doc.status];
		if (color) frm.page.set_indicator(__(frm.doc.status), color);
		if (frm.is_new() || frm.doc.is_self || frm.doc.status !== "Pending") return;
		frm.add_custom_button(__("Resend request (SMS and email)"), () =>
			frappe.call({ method: "sacco_manager.guarantors.resend_request", args: { row: frm.doc.name } })
				.then((r) => { frappe.show_alert({ message: r.message, indicator: "green" }); frm.reload_doc(); }), __("Guarantee"));
		frm.add_custom_button(__("Record answer from signed form"), () => {
			const d = new frappe.ui.Dialog({
				title: __("Record {0}'s answer", [frm.doc.guarantor_name]),
				fields: [
					{ fieldname: "action", fieldtype: "Select", label: __("Answer"), options: "accept\ndecline", default: "accept", reqd: 1 },
					{ fieldname: "pledge_value", fieldtype: "Currency", label: __("Amount guaranteed"), default: frm.doc.pledge_value,
						depends_on: "eval:doc.action=='accept'", description: __("Can be less than requested, not more.") },
					{ fieldname: "reference", fieldtype: "Data", label: __("Signed form reference"), reqd: 1 },
				],
				primary_action_label: __("Record"),
				primary_action: (v) => {
					d.hide();
					frappe.call({ method: "sacco_manager.guarantors.record_response", args: { row: frm.doc.name, ...v } })
						.then(() => frm.reload_doc());
				},
			});
			d.show();
		}, __("Guarantee"));
	},
});
