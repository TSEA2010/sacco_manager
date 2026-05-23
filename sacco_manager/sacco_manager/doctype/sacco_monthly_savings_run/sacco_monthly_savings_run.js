// Copyright (c) 2026, TSEA and contributors
// For license information, please see license.txt

// frappe.ui.form.on("SACCO Monthly Savings Run", {
// 	refresh(frm) {

// 	},
// });
frappe.ui.form.on("SACCO Monthly Savings Run", {
    refresh(frm) {
        if (frm.doc.docstatus === 0) {
            frm.add_custom_button("Fetch Members", () => {
                if (!frm.doc.period_month || !frm.doc.period_year) {
                    frappe.msgprint("Please set Month and Year first.");
                    return;
                }
                frappe.confirm(
                    `Fetch all active members for ${frm.doc.period_month} ${frm.doc.period_year}? This will replace the current list.`,
                    () => {
                        frm.call("fetch_members").then(() => {
                            frm.refresh();
                        });
                    }
                );
            }, "Actions");
        }

        // Show summary
        if (frm.doc.members && frm.doc.members.length) {
            const skipped = frm.doc.members.filter(r => r.status === "Skipped").length;
            const active = frm.doc.members.filter(r => r.status !== "Skipped").length;
            frm.dashboard.add_comment(
                `${active} members active · ${skipped} skipped · Total: ${format_currency(frm.doc.total_amount)}`,
                "blue", true
            );
        }
    },

    // Auto-set year when month is picked
    period_month(frm) {
        if (!frm.doc.period_year) {
            frm.set_value("period_year", new Date().getFullYear());
        }
    }
});

// Child table — mark row as skipped when skip_reason is entered
frappe.ui.form.on("SACCO Monthly Savings Run Member", {
    skip_reason(frm, cdt, cdn) {
        const row = locals[cdt][cdn];
        if (row.skip_reason) {
            frappe.model.set_value(cdt, cdn, "status", "Skipped");
        } else {
            frappe.model.set_value(cdt, cdn, "status", "Pending");
        }
        frm.refresh_field("members");
        // Recalculate totals client-side
        let total = 0;
        frm.doc.members.forEach(r => {
            if (r.status !== "Skipped") total += r.amount || 0;
        });
        frm.set_value("total_amount", total);
        frm.set_value("total_members", frm.doc.members.filter(r => r.status !== "Skipped").length);
    }
});