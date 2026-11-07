# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Keep the database lean as the SACCO grows.

Frappe Lending writes one "Days Past Due Log" row per loan per day, forever. Lending itself only
reads the latest row for a loan (and the row for a given date when it reprocesses), so years of
daily rows are dead weight: on live they were 1.3M rows / 413 MB for ~750 loans.

prune_days_past_due_log keeps
  * every row from the last N days (SACCO Settings > Keep Daily Arrears Log For, default 90),
  * the month-end row of every month (arrears history for audit and trends),
  * the latest row of every loan (what Lending reads),
and deletes the rest in small batches. It runs weekly and can be started from SACCO Settings.

Version tracking is also switched off for system-generated Lending documents (interest accruals,
security shortfalls, days-past-due rows); the GL is their audit trail. People's changes (loans,
repayments, journals, members) keep their history."""

import frappe
from frappe.utils import add_days, cint, getdate, today

NO_VERSION_DOCTYPES = ("Loan Interest Accrual", "Loan Security Shortfall", "Days Past Due Log", "Process Loan Interest Accrual",
	"Process Loan Demand", "Process Loan Classification")


def _keep_days():
	v = frappe.db.get_single_value("SACCO Settings", "dpd_log_keep_days")
	return 90 if v in (None, "") else cint(v)


def dpd_prune_preview():
	keep = _keep_days()
	if keep <= 0:
		return {"enabled": False}
	cutoff = add_days(getdate(today()), -keep)
	total = frappe.db.count("Days Past Due Log")
	removable = frappe.db.sql("""SELECT COUNT(*) FROM `tabDays Past Due Log` d
		LEFT JOIN (SELECT loan, MAX(posting_date) AS last_date FROM `tabDays Past Due Log` GROUP BY loan) m
			ON m.loan = d.loan AND m.last_date = d.posting_date
		WHERE d.posting_date < %s AND d.posting_date != LAST_DAY(d.posting_date) AND m.loan IS NULL""", cutoff)[0][0]
	return {"enabled": True, "keep_days": keep, "cutoff": str(cutoff), "rows": total, "removable": removable}


def prune_days_past_due_log(max_loans=None):
	"""Per-loan deletes on the (loan) index: fast and light on locks even for millions of rows."""
	keep = _keep_days()
	if keep <= 0:
		return 0
	cutoff = add_days(getdate(today()), -keep)
	removed = 0
	loans = frappe.db.sql("""SELECT loan, MAX(posting_date) FROM `tabDays Past Due Log` GROUP BY loan""")
	for i, (loan, last_date) in enumerate(loans[: max_loans or None], 1):
		frappe.db.sql("""DELETE FROM `tabDays Past Due Log` WHERE loan = %s AND posting_date < %s
			AND posting_date != LAST_DAY(posting_date) AND posting_date != %s""", (loan, cutoff, last_date))
		removed += frappe.db._cursor.rowcount or 0
		if i % 25 == 0:
			frappe.db.commit()  # batched housekeeping: each batch stands alone
	frappe.db.commit()
	if removed:
		frappe.logger("sacco_housekeeping").info(f"Days Past Due Log: removed {removed} rows older than {cutoff}")
	return removed


def weekly():
	"""Scheduler entry point."""
	prune_days_past_due_log()


@frappe.whitelist()
def run_now():
	frappe.only_for(("System Manager", "SACCO Administrator"))
	preview = dpd_prune_preview()
	if not preview.get("enabled"):
		frappe.throw("Pruning is switched off (Keep Daily Arrears Log For = 0).")
	frappe.enqueue("sacco_manager.housekeeping.prune_days_past_due_log", queue="long", timeout=7200,
		job_id="sacco_dpd_prune", deduplicate=True)
	return preview


@frappe.whitelist()
def storage_report():
	"""Largest tables with rows and size, for SACCO Settings > Database housekeeping."""
	frappe.only_for(("System Manager", "SACCO Administrator", "SACCO Auditor"))
	rows = frappe.db.sql("""SELECT table_name AS `table`, table_rows AS `rows`,
			ROUND((data_length + index_length) / 1048576, 1) AS mb, ROUND(data_free / 1048576, 1) AS free_mb
		FROM information_schema.tables WHERE table_schema = DATABASE()
		ORDER BY (data_length + index_length) DESC LIMIT 15""", as_dict=True)
	extra = {
		"cancelled_gl_rows": frappe.db.count("GL Entry", {"is_cancelled": 1}),
		"gl_rows": frappe.db.count("GL Entry"),
		"dpd": dpd_prune_preview(),
	}
	return {"tables": rows, **extra}


def disable_version_tracking():
	"""after_migrate: no Version rows for machine-generated Lending documents."""
	from frappe.custom.doctype.property_setter.property_setter import make_property_setter

	for dt in NO_VERSION_DOCTYPES:
		if not frappe.db.exists("DocType", dt):
			continue
		if cint(frappe.get_meta(dt).track_changes):
			make_property_setter(dt, None, "track_changes", 0, "Check", for_doctype=True, validate_fields_for_doctype=False)
