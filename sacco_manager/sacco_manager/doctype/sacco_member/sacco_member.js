// Copyright (c) 2026, TSEA and contributors
// For license information, please see license.txt

// frappe.ui.form.on("SACCO Member", {
// 	refresh(frm) {

// 	},
// });
// sacco_manager/sacco_manager/doctype/sacco_member/sacco_member.js

frappe.ui.form.on('SACCO Member', {
    onload(frm) {
        if (frm.is_new() && !frm.doc.member_no) {
            frappe.call({
                method: 'sacco_manager.sacco_manager.doctype.sacco_member.sacco_member.get_next_member_no',
                callback(r) {
                    if (r.message) {
                        frm.set_value('member_no', r.message);
                    }
                }
            });
        }
    },

    refresh(frm) {
        if (!frm.is_new()) {
            frm.add_custom_button('Ledger', function () {
                frappe.set_route('query-report', 'General Ledger', {
                    party_type: 'SACCO Member',
                    party: frm.doc.name,
                    include_default_book_entries: 1,
                    show_cancelled_entries: 0,
                    from_date: frappe.datetime.add_months(frappe.datetime.get_today(), -12),
                    to_date: frappe.datetime.get_today()
                });
            }, 'View');
        }
    },

    first_name(frm)  { make_full_name(frm); },
    middle_name(frm) { make_full_name(frm); },
    last_name(frm)   { make_full_name(frm); }
});

function make_full_name(frm) {
    const full_name = [frm.doc.first_name, frm.doc.middle_name, frm.doc.last_name]
        .filter(part => part && part.trim())
        .join(' ');
    frm.set_value('full_name', full_name);
}