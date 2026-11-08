// Copyright (c) 2026, Trident SACCO and contributors
// For license information, please see license.txt

const SMA = "sacco_manager.sacco_manager.doctype.sacco_membership_application.sacco_membership_application";

frappe.ui.form.on("SACCO Membership Application", {
	refresh(frm) {
		const s = frm.doc.status;
		frm.page.set_indicator(__(s), { Submitted: "blue", "Under Review": "orange", Approved: "green", Rejected: "red" }[s]);
		if (frm.is_new()) return;
		if (s === "Approved" && frm.doc.member) {
			frm.add_custom_button(__("Open member"), () => frappe.set_route("Form", "SACCO Member", frm.doc.member));
			return;
		}
		if (s === "Submitted") {
			frm.add_custom_button(__("Start review"), () =>
				frappe.call({ method: `${SMA}.start_review`, args: { name: frm.doc.name } }).then(() => frm.reload_doc()));
		}
		if (s === "Submitted" || s === "Under Review") {
			frm.add_custom_button(__("Approve and create member"), () => {
				const d = new frappe.ui.Dialog({
					title: __("Approve {0}", [frm.doc.full_name]),
					fields: [
						{ fieldtype: "HTML", options: `<p>${__("This creates the SACCO member, a savings account with a monthly deduction of {0}, copies the documents and records consent.",
							[format_currency(frm.doc.monthly_contribution)])}</p>` },
						{ fieldname: "portal", fieldtype: "Check", label: __("Also create a member portal login"),
							default: frm.doc.wants_portal && frm.doc.email ? 1 : 0, read_only: frm.doc.email ? 0 : 1,
							description: frm.doc.email ? "" : __("No email on the application.") },
					],
					primary_action_label: __("Approve"),
					primary_action: (v) => {
						d.hide();
						frappe.call({ method: `${SMA}.approve`, args: { name: frm.doc.name, create_portal_user: v.portal }, freeze: true })
							.then((r) => { frappe.show_alert({ message: __("Member {0} created", [r.message.member_no]), indicator: "green" }); frm.reload_doc(); });
					},
				});
				d.show();
			}).addClass("btn-primary");
			frm.add_custom_button(__("Reject"), () =>
				frappe.prompt({ fieldname: "reason", fieldtype: "Small Text", label: __("Reason (sent to the applicant)"), reqd: 1 },
					(v) => frappe.call({ method: `${SMA}.reject`, args: { name: frm.doc.name, reason: v.reason } }).then(() => frm.reload_doc()),
					__("Reject application")));
		}
	},
});
