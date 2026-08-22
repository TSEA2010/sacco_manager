# Copyright (c) 2026, TSEA and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import getdate, get_first_day, get_last_day

def execute(filters=None):
    filters = filters or {}
    columns = get_columns()
    data = get_data(filters)
    return columns, data


def get_columns():
    return [
        {"label": _("Member No"), "fieldname": "member_no", "fieldtype": "Data", "width": 100},
        {"label": _("Member Name"), "fieldname": "full_name", "fieldtype": "Data", "width": 160},
        {"label": _("Branch"), "fieldname": "branch", "fieldtype": "Link", "options": "Branch", "width": 120},
        {"label": _("Monthly Contribution"), "fieldname": "monthly_contribution", "fieldtype": "Currency", "width": 150},
        {"label": _("Loan Repayment Due"), "fieldname": "loan_repayment_due", "fieldtype": "Currency", "width": 150},
        {"label": _("Total Saved"), "fieldname": "total_saved", "fieldtype": "Currency", "width": 130},
        {"label": _("Pending Principal"), "fieldname": "pending_principal", "fieldtype": "Currency", "width": 140},
        {"label": _("Pending Interest"), "fieldname": "pending_interest", "fieldtype": "Currency", "width": 140},
        {"label": _("Pending Total"), "fieldname": "pending_total", "fieldtype": "Currency", "width": 140},
    ]


def get_data(filters):
    month = int(filters.get("month") or getdate().month)
    year = int(filters.get("year") or getdate().year)
    from_date = get_first_day(f"{year}-{month:02d}-01")
    to_date = get_last_day(from_date)

    conditions = ["m.status = 'Active'"]
    values = {"from_date": from_date, "to_date": to_date}

    if filters.get("branch"):
        conditions.append("m.branch = %(branch)s")
        values["branch"] = filters.get("branch")
    if filters.get("member"):
        conditions.append("m.name = %(member)s")
        values["member"] = filters.get("member")

    where_clause = " AND ".join(conditions)

    return frappe.db.sql(f"""
        SELECT
            m.name AS member,
            m.member_no,
            m.full_name,
            m.branch,
            m.customer,
            COALESCE(sav.monthly_contribution, 0) AS monthly_contribution,
            COALESCE(sav.total_saved, 0) AS total_saved,
            COALESCE(due.loan_repayment_due, 0) AS loan_repayment_due,
            COALESCE(pending.pending_principal, 0) AS pending_principal,
            COALESCE(pending.pending_interest, 0) AS pending_interest,
            COALESCE(pending.pending_principal, 0) + COALESCE(pending.pending_interest, 0) AS pending_total
        FROM `tabSACCO Member` m

        -- monthly contribution + running savings balance
        LEFT JOIN (
            SELECT
                t1.member,
                SUM(CASE WHEN t1.transaction_type = 'Contribution'
                         AND t1.posting_date BETWEEN %(from_date)s AND %(to_date)s
                    THEN t1.amount ELSE 0 END) AS monthly_contribution,
                (SELECT t2.balance_after FROM `tabSACCO Savings Transaction` t2
                 WHERE t2.member = t1.member
                 ORDER BY t2.posting_date DESC, t2.creation DESC LIMIT 1) AS total_saved
            FROM `tabSACCO Savings Transaction` t1
            GROUP BY t1.member
        ) sav ON sav.member = m.name

        -- amount due THIS MONTH from active loans
        LEFT JOIN (
            SELECT
                l.applicant AS customer,
                SUM(ld.demand_amount) AS loan_repayment_due
            FROM `tabLoan Demand` ld
            INNER JOIN `tabLoan` l ON l.name = ld.loan
            WHERE ld.demand_date BETWEEN %(from_date)s AND %(to_date)s
                AND ld.demand_type IN ('Principal', 'Interest')
            GROUP BY l.applicant
        ) due ON due.customer = m.customer

        -- all-time outstanding balance (not month-bound)
        LEFT JOIN (
            SELECT
                l.applicant AS customer,
                SUM(CASE WHEN ld.demand_type = 'Principal' THEN ld.outstanding_amount ELSE 0 END) AS pending_principal,
                SUM(CASE WHEN ld.demand_type = 'Interest' THEN ld.outstanding_amount ELSE 0 END) AS pending_interest
            FROM `tabLoan Demand` ld
            INNER JOIN `tabLoan` l ON l.name = ld.loan
            WHERE ld.outstanding_amount > 0
                AND l.status NOT IN ('Closed', 'Written Off')
            GROUP BY l.applicant
        ) pending ON pending.customer = m.customer

        WHERE {where_clause}
        ORDER BY m.branch, m.full_name
    """, values, as_dict=True)