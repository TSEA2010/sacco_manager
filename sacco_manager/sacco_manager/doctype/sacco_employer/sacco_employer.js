// Copyright (c) 2026, Trident SACCO and contributors
// For license information, please see license.txt

const SACCO_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

function sacco_period_dialog(title, primary, fn) {
	const now = new Date();
	const d = new frappe.ui.Dialog({
		title,
		fields: [
			{ fieldname: "month", label: __("Month"), fieldtype: "Select", options: SACCO_MONTHS.join("\n"), default: SACCO_MONTHS[now.getMonth()], reqd: 1 },
			{ fieldname: "year", label: __("Year"), fieldtype: "Int", default: now.getFullYear(), reqd: 1 },
		],
		primary_action_label: primary,
		primary_action: (v) => { d.hide(); fn(v); },
	});
	d.show();
}

frappe.ui.form.on("SACCO Employer", {
	refresh(frm) {
		if (frm.is_new()) return;
		if (!frm.doc.facility_loan) {
			frm.add_custom_button(__("Open facility"), () =>
				frappe.confirm(__("Open a revolving facility for {0}: limit {1} at {2}% p.a.? A submitted SACCO Board Approval for this employer is required.",
					[frm.doc.customer || frm.doc.name, format_currency(frm.doc.facility_limit), frm.doc.facility_rate || 0]), () =>
					frappe.call({ method: "sacco_manager.sacco_manager.doctype.sacco_employer.sacco_employer.create_facility",
						args: { employer: frm.doc.name }, freeze: true }).then((r) => { frappe.show_alert(__("Facility {0} opened", [r.message])); frm.reload_doc(); })),
				__("Facility"));
		} else {
			frappe.call({ method: "sacco_manager.sacco_manager.doctype.sacco_employer.sacco_employer.get_facility_status",
				args: { employer: frm.doc.name } }).then((r) => {
				const s = r.message || {};
				frm.dashboard.add_comment(__("Facility {0}: owes {1} of a {2} limit at {3}% — {4} still available ({5}).",
					[frm.doc.facility_loan, format_currency(s.outstanding), format_currency(s.maximum_limit_amount), s.rate_of_interest,
						format_currency(s.available_limit_amount), s.status]), "blue", true);
			});
			frm.add_custom_button(__("Facility loan"), () => frappe.set_route("Form", "Loan", frm.doc.facility_loan), __("Facility"));
			frm.add_custom_button(__("Record a repayment"), () => frappe.new_doc("Loan Repayment", { against_loan: frm.doc.facility_loan }), __("Facility"));
			frm.add_custom_button(__("Lend cash (drawdown)"), () => frappe.new_doc("Loan Disbursement", { against_loan: frm.doc.facility_loan }), __("Facility"));
		}
		if (frm.doc.erp_enabled) {
			frm.add_custom_button(__("Send deductions to payroll"), () =>
				sacco_period_dialog(__("Send SACCO deductions to {0} payroll", [frm.doc.name]), __("Send"), (v) =>
					frappe.call({ method: "sacco_manager.employer_erp.push_deductions", args: { employer: frm.doc.name, ...v }, freeze: true,
						freeze_message: __("Sending to the company's ERPNext…") }).then((r) => {
						const o = r.message;
						const list = (t, a) => (a.length ? `<p><b>${t}</b><br>${a.map(frappe.utils.escape_html).join("<br>")}</p>` : "");
						frappe.msgprint({ title: __("Deductions sent"), message:
							`<p>${__("Created {0}, updated {1}, unchanged {2} (drafts in their payroll — their HR submits them).", [o.created, o.updated, o.unchanged])}</p>`
							+ list(__("Already submitted with a different amount"), o.submitted_differs)
							+ list(__("Members without Staff / Employee No"), o.no_employee_id) + list(__("Errors"), o.errors) });
						frm.reload_doc();
					})), __("Payroll"));
			frm.add_custom_button(__("Read payroll into remittance"), () =>
				sacco_period_dialog(__("Read {0} payroll deductions", [frm.doc.name]), __("Read"), (v) =>
					frappe.call({ method: "sacco_manager.employer_erp.pull_deductions", args: { employer: frm.doc.name, ...v }, freeze: true,
						freeze_message: __("Reading salary slips…") }).then((r) => {
						frappe.show_alert({ message: __("Draft remittance {0}: {1} from {2} employees", [r.message.remittance,
							format_currency(r.message.total), r.message.employees]), indicator: "green" });
						frappe.set_route("Form", "SACCO Branch Remittance", r.message.remittance);
					})), __("Payroll"));
		}
	},
});
