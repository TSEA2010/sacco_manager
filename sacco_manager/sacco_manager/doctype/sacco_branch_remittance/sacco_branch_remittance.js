// Copyright (c) 2026, TSEA and contributors
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
            "Paid": "green", "Overdue": "red"
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

    received_date(frm) {
        // Trigger penalty recalculation on save
        frm.save();
    },

    received_amount(frm) {
        if (frm.doc.received_amount >= frm.doc.expected_amount) {
            frm.set_value("status", "Paid");
        }
    }
});