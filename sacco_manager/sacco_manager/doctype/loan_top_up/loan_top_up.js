// Copyright (c) 2026, Trident SACCO and contributors
// For license information, please see license.txt

frappe.ui.form.on('Loan Top-Up', {
    loan: function(frm) {
        if (!frm.doc.loan) return;
        frappe.db.get_doc('Loan', frm.doc.loan).then(loan => {
            let outstanding = flt(loan.disbursed_amount) - flt(loan.total_principal_paid);
            frm.set_value('outstanding_principal', outstanding);
            frm.set_value('new_interest_rate', loan.rate_of_interest);
            frm.set_value('new_tenure_months', loan.repayment_periods);
            frm.set_value('repayment_method', loan.repayment_method);
            frm.set_value('customer', loan.applicant);
            frm.set_value('disbursement_account', loan.disbursement_account);
            // Pre-fill fixed amount from current loan
            frm.set_value('new_monthly_repayment_amount', loan.monthly_repayment_amount);
            refresh_emi(frm);
        });
    },

    repayment_method: function(frm) { refresh_emi(frm); },
    top_up_amount: function(frm) { refresh_emi(frm); },
    new_tenure_months: function(frm) { refresh_emi(frm); },
    new_interest_rate: function(frm) { refresh_emi(frm); },
    new_monthly_repayment_amount: function(frm) { refresh_emi(frm); }
});

function refresh_emi(frm) {
    let principal = flt(frm.doc.outstanding_principal) + flt(frm.doc.top_up_amount);
    frm.set_value('new_principal', principal);
    if (!principal) return;

    let r = (flt(frm.doc.new_interest_rate) / 100) / 12;

    if (frm.doc.repayment_method === 'Repay Over Number of Periods') {
        let n = flt(frm.doc.new_tenure_months);
        if (!n) return;
        let emi = (r === 0)
            ? principal / n
            : principal * r * Math.pow(1+r, n) / (Math.pow(1+r, n) - 1);
        frm.set_value('new_installment_amount', Math.round(emi * 100) / 100);
        frm.set_df_property('new_installment_amount', 'description',
            'Computed EMI over ' + n + ' months');

    } else if (frm.doc.repayment_method === 'Repay Fixed Amount per Period') {
        let emi = flt(frm.doc.new_monthly_repayment_amount);
        if (!emi) return;
        frm.set_value('new_installment_amount', emi);
        // Calculate tenure
        if (r === 0) {
            let n = Math.ceil(principal / emi);
            frm.set_value('new_tenure_months', n);
        } else {
            let val = 1 - (principal * r / emi);
            if (val <= 0) {
                frappe.msgprint('Monthly amount too low to cover interest.');
                return;
            }
            let n = Math.ceil(-Math.log(val) / Math.log(1 + r));
            frm.set_value('new_tenure_months', n);
            frm.set_df_property('new_tenure_months', 'description',
                'Computed tenure for fixed installment of KES ' +
                format_currency(emi, 'KES'));
        }
    }
}