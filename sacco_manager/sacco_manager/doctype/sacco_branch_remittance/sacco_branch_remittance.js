// Copyright (c) 2026, Trident SACCO and contributors
// For license information, please see license.txt

frappe.ui.form.on("SACCO Branch Remittance", {

    refresh(frm) {
        if (frm.doc.docstatus === 0) {
            frm.add_custom_button("Fetch Members", () => {
                if (!frm.doc.branch || !frm.doc.period_month || !frm.doc.period_year) {
                    frappe.msgprint("Please set Branch, Month and Year first.");
                    return;
                }
                frappe.confirm(
                    `Fetch all active members for ${frm.doc.branch} — ${frm.doc.period_month} ${frm.doc.period_year}?`,
                    () => frm.call("fetch_members").then(() => frm.refresh())
                );
            }, "Actions");
        }

        // Status indicator
        const colors = {
            "Draft": "grey", "Pending": "orange",
            "Paid": "green", "Overdue": "red", "Cancelled": "grey"
        };
        if (frm.doc.status) {
            frm.dashboard.add_indicator(
                frm.doc.status, colors[frm.doc.status] || "grey"
            );
        }

        // Penalty warning
        if (frm.doc.is_late && frm.doc.penalty_amount > 0) {
            frm.dashboard.add_comment(
                `⚠️ Late by ${frm.doc.days_late} days — Penalty: ${format_currency(frm.doc.penalty_amount)}`,
                "red", true
            );
        }
    },

    period_month(frm) {
        if (!frm.doc.period_year) {
            frm.set_value("period_year", new Date().getFullYear());
        }
    },

    setup(frm) {
        frm.set_query("payment_account", () => ({ filters: { account_type: ["in", ["Bank", "Cash"]], is_group: 0 } }));
        frm.set_query("loan", "members", (doc, cdt, cdn) => {
            const row = locals[cdt][cdn];
            return { query: "sacco_manager.lending_utils.loan_query", filters: { member: row.member } };
        });
    }
});