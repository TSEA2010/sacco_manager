// Copyright (c) 2026, Trident SACCO and contributors
// For license information, please see license.txt

frappe.listview_settings["SACCO Member"] = {
	onload(listview) {
		listview.page.add_menu_item(__("Email statements to all members"), () => {
			const d = new frappe.ui.Dialog({
				title: __("Email statements"),
				fields: [
					{ fieldname: "from_date", label: __("From"), fieldtype: "Date", reqd: 1,
						default: frappe.datetime.add_months(frappe.datetime.get_today(), -12) },
					{ fieldname: "to_date", label: __("To"), fieldtype: "Date", reqd: 1, default: frappe.datetime.get_today() },
					{ fieldname: "branch", label: __("Only this employer / branch"), fieldtype: "Link", options: "Branch" },
				],
				primary_action_label: __("Queue emails"),
				primary_action(v) {
					frappe.call({ method: "sacco_manager.statements.email_all_statements", args: v, freeze: true })
						.then((r) => { d.hide(); frappe.msgprint(r.message); });
				},
			});
			d.show();
		});
	},
};
