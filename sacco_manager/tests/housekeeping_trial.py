"""Dev-only: time a real prune on the test site and report size before/after.
bench --site sacco.test execute sacco_manager.tests.housekeeping_trial.run"""

import time

import frappe


def _size():
	return frappe.db.sql("""SELECT table_rows, ROUND((data_length+index_length)/1048576,1), ROUND(data_free/1048576,1)
		FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name='tabDays Past Due Log'""")[0]


def run():
	from sacco_manager import housekeeping

	before = frappe.db.count("Days Past Due Log")
	print("size before", _size())
	t = time.time()
	removed = housekeeping.prune_days_past_due_log()
	took = time.time() - t
	frappe.db.sql("OPTIMIZE TABLE `tabDays Past Due Log`")
	frappe.db.sql("ANALYZE TABLE `tabDays Past Due Log`")
	print(f"rows {before:,} -> {frappe.db.count('Days Past Due Log'):,}; removed {removed:,} in {took:.0f}s; size now {_size()}")
	bad = frappe.db.sql("""SELECT COUNT(*) FROM `tabLoan` l WHERE l.docstatus=1 AND l.status IN ('Disbursed','Partially Disbursed','Active')
		AND NOT EXISTS (SELECT 1 FROM `tabDays Past Due Log` d WHERE d.loan=l.name)""")[0][0]
	print("open loans without any DPD row:", bad)
	t = time.time()
	again = housekeeping.prune_days_past_due_log()
	print(f"second run removed {again} in {time.time() - t:.1f}s")
