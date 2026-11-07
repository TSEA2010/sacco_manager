# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Daily M-Pesa reconciliation: what Safaricom reported vs what was posted to the M-Pesa GL
account. Use it beside the M-Pesa statement: Received should equal the statement's paid-in,
and Unallocated should be cleared every day."""

import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
	f = frappe._dict(filters or {})
	cond = ["DATE(t.transaction_time) BETWEEN %(from)s AND %(to)s"]
	vals = {"from": f.from_date, "to": f.to_date}
	if f.mpesa_account:
		cond.append("t.mpesa_account=%(acc)s")
		vals["acc"] = f.mpesa_account
	rows = frappe.db.sql(
		f"""SELECT DATE(t.transaction_time) AS day, t.mpesa_account,
			SUM(CASE WHEN t.direction='Inbound' AND t.status IN ('Allocated','Unallocated') THEN t.amount ELSE 0 END) AS received,
			SUM(CASE WHEN t.direction='Inbound' AND t.status='Allocated' THEN t.amount ELSE 0 END) AS allocated,
			SUM(CASE WHEN t.direction='Inbound' AND t.status='Unallocated' THEN t.amount ELSE 0 END) AS unallocated,
			SUM(CASE WHEN t.direction='Inbound' AND t.status='Unallocated' THEN 1 ELSE 0 END) AS unallocated_count,
			SUM(CASE WHEN t.direction='Outbound' AND t.status='Paid' THEN t.amount ELSE 0 END) AS paid_out,
			SUM(CASE WHEN t.status='Failed' THEN 1 ELSE 0 END) AS failed_count,
			COUNT(*) AS txns
		FROM `tabSACCO M-Pesa Transaction` t
		WHERE {" AND ".join(cond)}
		GROUP BY day, t.mpesa_account ORDER BY day""",
		vals,
		as_dict=True,
	)
	gl_map = {a.name: a.gl_account for a in frappe.get_all("SACCO M-Pesa Account", fields=["name", "gl_account"])}
	for r in rows:
		res = frappe.db.sql(
			"""SELECT SUM(debit), SUM(credit) FROM `tabGL Entry`
			WHERE account=%s AND posting_date=%s AND is_cancelled=0""",
			(gl_map.get(r.mpesa_account), r.day),
		)[0]
		r["gl_debit"], r["gl_credit"] = flt(res[0]), flt(res[1])
		r["difference"] = flt(r.allocated) - flt(r.gl_debit)
	unallocated = sum(flt(r.unallocated) for r in rows)
	summary = [
		{"label": _("Received"), "value": sum(flt(r.received) for r in rows), "datatype": "Currency"},
		{"label": _("Unallocated"), "value": unallocated, "datatype": "Currency",
			"indicator": "Red" if unallocated else "Green"},
		{"label": _("Paid out (B2C)"), "value": sum(flt(r.paid_out) for r in rows), "datatype": "Currency"},
	]
	return _columns(), rows, None, None, summary


def _col(fieldname, label, fieldtype="Currency", width=120):
	return {"label": _(label), "fieldname": fieldname, "fieldtype": fieldtype, "width": width}


def _columns():
	return [
		_col("day", "Date", "Date", 100),
		{"label": _("Shortcode"), "fieldname": "mpesa_account", "fieldtype": "Link",
			"options": "SACCO M-Pesa Account", "width": 100},
		_col("txns", "Txns", "Int", 60),
		_col("received", "Received"),
		_col("allocated", "Allocated"),
		_col("unallocated", "Unallocated"),
		_col("unallocated_count", "# Unallocated", "Int", 100),
		_col("paid_out", "Paid Out (B2C)"),
		_col("failed_count", "# Failed", "Int", 80),
		_col("gl_debit", "GL Debit"),
		_col("gl_credit", "GL Credit"),
		_col("difference", "Allocated - GL Debit"),
	]
