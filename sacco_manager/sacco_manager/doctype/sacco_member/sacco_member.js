// Copyright (c) 2026, TSEA and contributors
// For license information, please see license.txt
frappe.ui.form.on('SACCO Member', {
    onload(frm) {
        toggle_user_id_field(frm);
        if (frm.is_new() && !frm.doc.member_no) {
            frappe.call({
                method: 'sacco_manager.sacco_manager.doctype.sacco_member.sacco_member.get_next_member_no',
                callback(r) {
                    if (r.message) frm.set_value('member_no', r.message);
                }
            });
        }
    },

    refresh(frm) {
        toggle_user_id_field(frm);
        
        if (!frm.is_new()) {
            // Ledger button
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

            // Create Portal User button
            if (frm.doc.is_user && frm.doc.user_id && !frm.doc.portal_user_created) {
                frm.add_custom_button('Create Portal User', function () {
                    frappe.confirm(
                        `Create portal user for <b>${frm.doc.user_id}</b>?`,
                        () => {
                            const do_call = () => {
                                frappe.call({
                                    method: 'sacco_manager.sacco_manager.doctype.sacco_member.sacco_member.create_portal_user',
                                    args: { member_name: frm.doc.name },
                                    freeze: true,
                                    freeze_message: 'Creating portal user...',
                                    callback(r) {
                                        if (!r.exc) {
                                            frappe.msgprint(r.message);
                                            frm.reload_doc();
                                        }
                                    }
                                });
                            };

                            if (frm.is_dirty()) {
                                frm.save().then(() => do_call());
                            } else {
                                do_call();
                            }
                        }
                    );
                }, 'Portal');
            }

            // View Customer button
            if (frm.doc.customer) {
                frm.add_custom_button(__('View Customer'), () => {
                    frappe.set_route('Form', 'Customer', frm.doc.customer);
                }, __('Links'));
            }
        }
    },

    before_save(frm) {
        // Auto-set customer_group field on the form if not already set
        if (!frm.doc.customer_group) {
            frm.set_value('customer_group', 'Sacco Member');
        }
    },

    is_user(frm) {
        toggle_user_id_field(frm);
        if (frm.doc.is_user && frm.doc.email_id && !frm.doc.user_id) {
            frm.set_value('user_id', frm.doc.email_id);
        }
        if (!frm.doc.is_user) {
            frm.set_value('user_id', '');
        }
    },

    email_id(frm) {
        if (frm.doc.is_user && frm.doc.email_id) {
            frm.set_value('user_id', frm.doc.email_id);
        }
    },

    first_name(frm)  { make_full_name(frm); },
    middle_name(frm) { make_full_name(frm); },
    last_name(frm)   { make_full_name(frm); },
    
    full_name(frm) {
        // Warn user if a customer will be auto-created with this name
        if (frm.is_new() && frm.doc.full_name) {
           // frm.set_intro(
           //     __('A Customer record will be auto-created for <b>{0}</b> on save.', [frm.doc.full_name]),
           //     'blue'
           // );
        }
    }
});

function make_full_name(frm) {
    const full_name = [frm.doc.first_name, frm.doc.middle_name, frm.doc.last_name]
        .filter(part => part && part.trim())
        .join(' ');
    frm.set_value('full_name', full_name);
}

function toggle_user_id_field(frm) {
    frm.set_df_property('user_id', 'hidden', !frm.doc.is_user);
    frm.set_df_property('user_id', 'reqd', !!frm.doc.is_user);
}