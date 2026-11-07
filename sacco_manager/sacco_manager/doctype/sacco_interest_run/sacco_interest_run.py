# Copyright (c) 2026, TSEA and contributors
# For license information, please see license.txt
# sacco_interest_run.py

import frappe
from frappe.model.document import Document
from frappe.utils import date_diff, flt, getdate


class SACCOInterestRun(Document):

    def validate(self):
        if self.period_start >= self.period_end:
            frappe.throw("Period End must be after Period Start")
        if self.docstatus == 0:
            self.calculate_preview()

    # ------------------------------------------------------------------
    # Helper: per-account coverage map
    # ------------------------------------------------------------------

    def _get_coverage_map(self):
        """
        For every account that already has submitted interest posted
        within (or overlapping) the current period, return the furthest
        period_end already covered.

        Returns dict: { savings_account: "YYYY-MM-DD", ... }
        """
        rows = frappe.db.sql("""
            SELECT rd.savings_account, MAX(r.period_end) AS covered_up_to
            FROM `tabSACCO Interest Run Detail` rd
            INNER JOIN `tabSACCO Interest Run` r ON r.name = rd.parent
            WHERE r.docstatus = 1
              AND r.name        != %(self_name)s
              AND r.period_start <  %(period_end)s
              AND r.period_end   >  %(period_start)s
            GROUP BY rd.savings_account
        """, {
            "self_name":    self.name or "new",
            "period_start": self.period_start,
            "period_end":   self.period_end,
        }, as_dict=True)

        return {r.savings_account: getdate(r.covered_up_to) for r in rows}

    # ------------------------------------------------------------------

    def calculate_preview(self):
        filters = {"status": "Active"}
        if self.account_type:
            filters["account_type"] = self.account_type

        accounts = frappe.get_all(
            "SACCO Savings Account",
            filters=filters,
            fields=["name", "member", "balance", "interest_rate"]
        )

        # Per-account: how far interest has already been posted
        coverage = self._get_coverage_map()

        self.set("details", [])
        total        = 0
        skipped_full = 0

        for acc in accounts:
            # Determine effective start for this account
            covered_up_to = coverage.get(acc.name)

            if covered_up_to:
                if getdate(covered_up_to) >= getdate(self.period_end):
                    # Entire period already covered — skip completely
                    skipped_full += 1
                    continue
                # Partially covered — start from where we left off
                effective_start = covered_up_to
            else:
                effective_start = getdate(self.period_start)

            days = date_diff(getdate(self.period_end), effective_start)
            if days <= 0:
                skipped_full += 1
                continue

            rate = flt(acc.interest_rate) or flt(self.interest_rate)
            if not rate:
                continue

            interest = round(flt(acc.balance) * (rate / 100) * days / 365, 2)
            if interest <= 0:
                continue

            self.append("details", {
                "member":           acc.member,
                "savings_account":  acc.name,
                "opening_balance":  acc.balance,
                "interest_rate":    rate,
                "interest_earned":  interest,
                # Store actual start so on_submit uses the same window
                "effective_start":  effective_start,
            })
            total += interest

        self.total_interest = total

        if skipped_full:
            frappe.msgprint(
                f"{skipped_full} account(s) fully skipped — "
                f"interest already posted for the entire period.",
                indicator="orange",
                alert=True,
            )

    # ------------------------------------------------------------------

    def on_submit(self):
        if not self.details:
            frappe.throw(
                "No interest details to process. "
                "Save the document first to preview."
            )

        # Document-level duplicate guard (exact same period + account_type)
        existing = frappe.db.exists("SACCO Interest Run", {
            "period_start": self.period_start,
            "period_end":   self.period_end,
            "account_type": self.account_type,
            "docstatus":    1,
            "name":         ("!=", self.name),
        })
        if existing:
            frappe.throw(f"Interest already run for this period: {existing}")

        # Re-check coverage at submit time (safety net)
        coverage = self._get_coverage_map()

        for row in self.details:
            covered_up_to   = coverage.get(row.savings_account)
            effective_start = covered_up_to if covered_up_to else self.period_start

            if covered_up_to and covered_up_to >= getdate(self.period_end):
                frappe.msgprint(
                    f"Skipped {row.savings_account} ({row.member}) — "
                    f"fully covered up to {covered_up_to}.",
                    indicator="orange",
                )
                continue

            txn = frappe.new_doc("SACCO Savings Transaction")
            txn.savings_account  = row.savings_account
            txn.member           = row.member
            txn.transaction_type = "Interest"
            txn.amount           = row.interest_earned
            txn.posting_date     = self.period_end
            txn.remarks          = (
                f"Interest Run: {self.name} "
                f"({effective_start} to {self.period_end})"
            )
            txn.insert(ignore_permissions=True)
            txn.submit()

            frappe.db.set_value(
                "SACCO Interest Run Detail",
                row.name,
                "savings_transaction",
                txn.name,
            )

        frappe.db.set_value(
            self.doctype, self.name, "total_interest", self.total_interest
        )

    # ------------------------------------------------------------------

    def on_cancel(self):
        for row in self.details:
            if not row.savings_transaction:
                continue
            txn = frappe.get_doc(
                "SACCO Savings Transaction", row.savings_transaction
            )
            if txn.docstatus == 1:
                txn.cancel()