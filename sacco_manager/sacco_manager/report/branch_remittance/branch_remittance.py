# Copyright (c) 2026, TSEA and contributors
# For license information, please see license.txt

import frappe
from frappe import _

def execute(filters=None):
    filters = filters or {}
    columns = get_columns()
    data = get_data(filters)
    return columns, data


def get_columns():
    return [
        {"label": _("Branch"), "fieldname": "branch", "fieldtype": "Link", "options": "Branch", "width": 120},
        {"label": _("Member"), "fieldname": "member_name", "fieldtype": "Data", "width": 160},
        {"label": _("Contribution Due"), "fieldname": "expected_amount", "fieldtype": "Currency", "width": 140},
        {"label": _("Loan Repayment Due"), "fieldname": "loan_repayment_due", "fieldtype": "Currency", "width": 150},
        {"label": _("Total Remittance"), "fieldname": "total_remittance", "fieldtype": "Currency", "width": 150},
        {"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 100},
    ]


def get_data(filters):
    conditions = ["r.branch = %(branch)s", "r.period_month = %(month)s", "r.period_year = %(year)s"]
    values = {
        "branch": filters.get("branch"),
        "month": filters.get("month"),
        "year": filters.get("year"),
    }

    return frappe.db.sql(f"""
        SELECT
            r.branch,
            rm.member_name,
            rm.expected_amount,
            COALESCE(due.loan_repayment_due, 0) AS loan_repayment_due,
            rm.expected_amount + COALESCE(due.loan_repayment_due, 0) AS total_remittance,
            rm.status
        FROM `tabSACCO Branch Remittance` r
        INNER JOIN `tabSACCO Branch Remittance Member` rm ON rm.parent = r.name
        LEFT JOIN `tabSACCO Member` m ON m.name = rm.member
        LEFT JOIN (
            SELECT l.applicant AS customer, SUM(ld.demand_amount) AS loan_repayment_due
            FROM `tabLoan Demand` ld
            INNER JOIN `tabLoan` l ON l.name = ld.loan
            WHERE MONTH(ld.demand_date) = %(month)s AND YEAR(ld.demand_date) = %(year)s
                AND ld.demand_type IN ('Principal', 'Interest')
            GROUP BY l.applicant
        ) due ON due.customer = m.customer
        WHERE {" AND ".join(conditions)}
        ORDER BY rm.member_name
    """, values, as_dict=True)