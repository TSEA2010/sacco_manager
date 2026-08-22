// Copyright (c) 2026, TSEA and contributors
// For license information, please see license.txt

frappe.ui.form.on("Withdrawal Request", {
	setup(frm) {
		frm.set_query("savings_account", function () {
			return {
				filters: {
					member: frm.doc.member || "",
				},
			};
		});
	},

	member(frm) {
		// clear a previously selected account that no longer matches
		if (frm.doc.savings_account) {
			frm.set_value("savings_account", "");
		}
	},
});