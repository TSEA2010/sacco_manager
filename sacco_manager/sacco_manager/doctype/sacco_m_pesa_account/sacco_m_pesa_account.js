// Copyright (c) 2026, Trident SACCO and contributors
// For license information, please see license.txt

frappe.ui.form.on("SACCO M-Pesa Account", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(__("Test Daraja connection"), () =>
			frappe.call({ method: "sacco_manager.mobile_money.api.test_connection", args: { account: frm.doc.name }, freeze: true })
				.then((r) => frappe.msgprint(r.message))
		);
		frm.add_custom_button(__("Register C2B URLs"), () =>
			frappe.confirm(__("Register the confirmation/validation URLs for shortcode {0} with Safaricom?", [frm.doc.name]), () =>
				frappe.call({ method: "sacco_manager.mobile_money.api.register_c2b_urls", args: { account: frm.doc.name }, freeze: true })
					.then((r) => { frappe.msgprint("<pre>" + JSON.stringify(r.message, null, 2) + "</pre>"); frm.reload_doc(); })
			)
		);
	},
});
