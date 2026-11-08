// Copyright (c) 2026, Trident SACCO and contributors
// For license information, please see license.txt
// SACCO Board: live dashboard for administrators, managers and auditors.

frappe.provide("sacco");

sacco.fmt = {
	money(v) {
		return format_currency(flt(v), frappe.defaults.get_default("currency"), 0);
	},
	short(v) {
		v = flt(v);
		const a = Math.abs(v);
		const s = a >= 1e9 ? (v / 1e9).toFixed(2) + "B" : a >= 1e6 ? (v / 1e6).toFixed(2) + "M" : a >= 1e3 ? (v / 1e3).toFixed(1) + "K" : v.toFixed(0);
		return s;
	},
	pct(v) {
		return flt(v).toFixed(1) + "%";
	},
	change(now, prev) {
		now = flt(now);
		prev = flt(prev);
		if (!prev) return "";
		const c = ((now - prev) / Math.abs(prev)) * 100;
		const cls = c >= 0 ? "up" : "down";
		return `<span class="sb-chg ${cls}">${c >= 0 ? "▲" : "▼"} ${Math.abs(c).toFixed(1)}% vs last year</span>`;
	},
	esc(s) {
		return frappe.utils.escape_html(s == null ? "" : String(s));
	},
};

sacco.COLORS = { brand: "#1a7f5a", blue: "#2f6fb0", amber: "#c98a12", red: "#c0392b", grey: "#8a96a3", teal: "#14857f" };

sacco.chart = function (el, opts) {
	if (!el) return null;
	el.innerHTML = "";
	try {
		return new frappe.Chart(el, Object.assign({ height: 230, axisOptions: { xIsSeries: 1, shortenYAxisNumbers: 1 },
			tooltipOptions: { formatTooltipY: (v) => sacco.fmt.money(v) } }, opts));
	} catch (e) {
		el.innerHTML = `<div class="text-muted small">${__("Chart could not be drawn")}</div>`;
		return null;
	}
};

sacco.STYLE = `
.sb { --sb-good:#15803d; --sb-warn:#b7791f; --sb-bad:#c0392b; --sb-info:#5b6b7b; }
.sb .sb-head { display:flex; flex-wrap:wrap; gap:8px; align-items:center; justify-content:space-between; margin:4px 0 14px; }
.sb .sb-head .muted { color: var(--text-muted); font-size: 12px; }
.sb .sb-alerts { display:flex; flex-wrap:wrap; gap:8px; margin-bottom:14px; }
.sb .sb-alert { padding:6px 10px; border-radius:8px; font-size:12px; cursor:pointer; border:1px solid var(--border-color); background: var(--card-bg); }
.sb .sb-alert.bad { border-color: var(--sb-bad); color: var(--sb-bad); }
.sb .sb-alert.warn { border-color: var(--sb-warn); color: var(--sb-warn); }
.sb .sb-grid { display:grid; gap:12px; }
.sb .kpis { grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); margin-bottom:16px; }
.sb .ratios { grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); margin-bottom:16px; }
.sb .charts { grid-template-columns: repeat(auto-fill, minmax(420px, 1fr)); margin-bottom:16px; }
.sb .tables { grid-template-columns: repeat(auto-fill, minmax(420px, 1fr)); }
.sb .card2 { background: var(--card-bg); border:1px solid var(--border-color); border-radius:10px; padding:12px 14px; }
.sb .kpi .lab { font-size:11px; text-transform:uppercase; letter-spacing:.4px; color: var(--text-muted); }
.sb .kpi .val { font-size:22px; font-weight:700; margin-top:2px; color: var(--heading-color); }
.sb .kpi .sub { font-size:11px; color: var(--text-muted); margin-top:2px; }
.sb .sb-chg.up { color: var(--sb-good); } .sb .sb-chg.down { color: var(--sb-bad); }
.sb .ratio { border-left:4px solid var(--sb-info); }
.sb .ratio.good { border-left-color: var(--sb-good); } .sb .ratio.warn { border-left-color: var(--sb-warn); } .sb .ratio.bad { border-left-color: var(--sb-bad); }
.sb .ratio .val { font-size:20px; font-weight:700; }
.sb .ratio.good .val { color: var(--sb-good); } .sb .ratio.warn .val { color: var(--sb-warn); } .sb .ratio.bad .val { color: var(--sb-bad); }
.sb .ratio .bm { font-size:11px; color: var(--text-muted); }
.sb h5.sec { font-size:13px; font-weight:600; margin:18px 0 8px; color: var(--heading-color); text-transform:uppercase; letter-spacing:.5px; }
.sb .card2 h6 { font-size:12px; font-weight:600; margin:0 0 6px; color: var(--heading-color); }
.sb table.t { width:100%; font-size:12px; border-collapse:collapse; }
.sb table.t th { text-align:left; color: var(--text-muted); font-weight:500; border-bottom:1px solid var(--border-color); padding:4px 6px; }
.sb table.t td { padding:4px 6px; border-bottom:1px solid var(--border-color); }
.sb table.t .n { text-align:right; white-space:nowrap; }
.sb .bar { height:8px; background: var(--control-bg); border-radius:4px; overflow:hidden; }
.sb .bar > div { height:100%; background: var(--sb-good); }
.sb .note { font-size:11px; color: var(--text-muted); margin-top:6px; }
@media print { .sb .sb-head .btns, .page-head, .sb .sb-alerts { display:none !important; } .sb .card2 { break-inside: avoid; } }
`;

frappe.pages["sacco-board"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({ parent: wrapper, title: __("SACCO Board"), single_column: true });
	frappe.dom.set_style(sacco.STYLE, "sacco-board-style");
	const board = new sacco.Board(page);
	wrapper.sacco_board = board;
	board.load();
};

frappe.pages["sacco-board"].on_page_show = function (wrapper) {
	if (wrapper.sacco_board && wrapper.sacco_board.loaded_at && Date.now() - wrapper.sacco_board.loaded_at > 60000) {
		wrapper.sacco_board.load();
	}
};

sacco.Board = class {
	constructor(page) {
		this.page = page;
		this.as_at = frappe.datetime.get_today();
		this.$body = $(`<div class="sb"></div>`).appendTo(page.main);
		this.date = page.add_field({ fieldname: "as_at", label: __("As at"), fieldtype: "Date", default: this.as_at,
			change: () => { this.as_at = this.date.get_value() || frappe.datetime.get_today(); this.load(); } });
		page.set_primary_action(__("Refresh"), () => this.load(), "refresh");
		page.add_inner_button(__("AGM presentation"), () => frappe.set_route("sacco-agm"));
		page.add_inner_button(__("Print"), () => window.print());
		page.add_menu_item(__("Prudential Summary report"), () => frappe.set_route("query-report", "SACCO Prudential Summary"));
		page.add_menu_item(__("Portfolio at Risk report"), () => frappe.set_route("query-report", "Loan Portfolio at Risk"));
		page.add_menu_item(__("Defaulters & Guarantors"), () => frappe.set_route("query-report", "Defaulters and Guarantors"));
		// live: refresh every 5 minutes while the page is open
		this.timer = setInterval(() => { if (frappe.get_route_str() === "sacco-board" && !document.hidden) this.load(true); }, 300000);
	}

	load(quiet) {
		if (!quiet) this.$body.html(`<div class="text-muted p-4">${__("Loading live figures…")}</div>`);
		frappe.call({ method: "sacco_manager.analytics.get_board", args: { as_at: this.as_at } }).then((r) => {
			this.loaded_at = Date.now();
			this.render(r.message);
		});
	}

	render(d) {
		const f = Object.assign({}, sacco.fmt), n = d.now, p = d.prev;
		if (!d.prev_available) f.change = () => "";
		const kpi = (lab, val, sub) => `<div class="card2 kpi"><div class="lab">${lab}</div><div class="val">${val}</div><div class="sub">${sub || ""}</div></div>`;
		const alerts = (d.alerts || []).map((a, i) => `<div class="sb-alert ${a.level}" data-i="${i}">${f.esc(a.text)}</div>`).join("");
		const ratios = n.ratios.map((r) => {
			const pr = d.prev_available ? (p.ratios.find((x) => x.key === r.key) || {}).value : null;
			return `<div class="card2 ratio ${r.status}"><div class="lab small text-muted">${f.esc(r.label)}</div>
				<div class="val">${f.pct(r.value)}</div>
				<div class="bm">${__("Target")}: ${f.esc(r.benchmark)} · ${__("last year")} ${pr == null ? "–" : f.pct(pr)}</div></div>`;
		}).join("");
		const branchRows = d.by_branch.map((b) => `<tr><td>${f.esc(b.branch)}</td><td class="n">${b.members || ""}</td>
			<td class="n">${f.short(b.savings)}</td><td class="n">${b.loans || ""}</td><td class="n">${f.short(b.loan_book)}</td>
			<td class="n">${b.arrears ? f.short(b.arrears) : ""}</td></tr>`).join("");
		const top = d.top_borrowers.map((b, i) => `<tr><td>${i + 1}</td><td>${f.esc(b.name)} <span class="text-muted">${f.esc(b.member_no || "")}</span></td>
			<td>${f.esc(b.branch || "")}</td><td class="n">${b.loans}</td><td class="n">${f.money(b.balance)}</td></tr>`).join("");
		const mix = (rows) => rows.map((r) => `<tr><td>${f.esc(r.account)}</td><td class="n">${f.money(r.amount)}</td></tr>`).join("") ||
			`<tr><td colspan="2" class="text-muted">${__("Nothing posted this year yet")}</td></tr>`;
		const k = d.kyc;
		const kycRows = (k.fields || []).map((x) => `<tr><td style="width:30%">${f.esc(x.label)}</td>
			<td><div class="bar"><div style="width:${x.pct}%"></div></div></td><td class="n">${x.pct}%</td></tr>`).join("");

		this.$body.html(`
			<div class="sb-head"><div><b>${f.esc(d.company)}</b><div class="muted">${__("Live figures as at")} ${frappe.datetime.str_to_user(d.as_at)} ·
				${__("updated")} ${f.esc(d.generated)} · ${__("refreshes every 5 minutes")}
				${d.prev_available ? "" : " · " + __("year-on-year comparison off until SACCO Settings › Books Complete From is a year back")}</div></div></div>
			<div class="sb-alerts">${alerts}</div>
			<div class="sb-grid kpis">
				${kpi(__("Members"), n.members, f.change(n.members, p.members))}
				${kpi(__("Members' savings"), f.short(n.savings), f.change(n.savings, p.savings))}
				${kpi(__("Share capital"), f.short(n.share_capital), f.change(n.share_capital, p.share_capital))}
				${kpi(__("Loan book"), f.short(n.loan_book), `${n.borrowers} ${__("borrowers")} · ` + f.change(n.loan_book, p.loan_book))}
				${kpi(__("Lent to group companies"), f.short(n.group_company_loans), `${__("interest 12m")} ${f.short(n.group_company_interest_12m)}`)}
				${kpi(__("Total assets"), f.short(n.total_assets), f.change(n.total_assets, p.total_assets))}
				${kpi(__("Liquid funds"), f.short(n.liquid), n.float_target ? `${__("float")} ${f.short(n.float_target)} · ${__("free to lend")} ${f.short(n.available_to_lend)}` : __("cash, bank, M-Pesa"))}
				${kpi(__("Surplus (12 months)"), f.short(n.surplus_12m), `${__("income")} ${f.short(n.income_12m)} · ${__("costs")} ${f.short(n.expense_12m)}`)}
			</div>
			<h5 class="sec">${__("Business ratios")}</h5>
			<div class="sb-grid ratios">${ratios}</div>
			<h5 class="sec">${__("Trends (24 months)")}</h5>
			<div class="sb-grid charts">
				<div class="card2"><h6>${__("Savings vs loan book")}</h6><div class="c-bal"></div></div>
				<div class="card2"><h6>${__("Income vs costs per month")}</h6><div class="c-pl"></div></div>
				<div class="card2"><h6>${__("Money in and out of savings")}</h6><div class="c-sav"></div></div>
				<div class="card2"><h6>${__("Loans given vs principal repaid")}</h6><div class="c-loan"></div></div>
				<div class="card2"><h6>${__("Membership")}</h6><div class="c-mem"></div></div>
				<div class="card2"><h6>${__("Income this year by source")}</h6><div class="c-inc"></div></div>
			</div>
			<div class="sb-grid tables">
				<div class="card2"><h6>${__("By employer")}</h6><table class="t"><tr><th>${__("Employer")}</th><th class="n">${__("Members")}</th>
					<th class="n">${__("Savings")}</th><th class="n">${__("Loans")}</th><th class="n">${__("Loan book")}</th><th class="n">${__("Arrears")}</th></tr>${branchRows}</table></div>
				<div class="card2"><h6>${__("Largest borrowers (concentration)")}</h6><table class="t"><tr><th>#</th><th>${__("Borrower")}</th><th>${__("Employer")}</th>
					<th class="n">${__("Loans")}</th><th class="n">${__("Balance")}</th></tr>${top}</table></div>
				<div class="card2"><h6>${__("Income this year")}</h6><table class="t">${mix(d.income_ytd)}</table>
					<h6 class="mt-3">${__("Costs this year")}</h6><table class="t">${mix(d.expense_ytd)}</table></div>
				<div class="card2"><h6>${__("KYC completeness (active members)")} — ${k.complete}/${k.members} ${__("complete")} (${k.pct}%)</h6>
					<table class="t">${kycRows}</table>
					<div class="note">${__("Provision required")} ${f.money(n.required_provision)} · ${__("booked")} ${f.money(n.booked_provision)}</div></div>
			</div>
			<div class="note">${__("Ratios use the Prudential Summary and Portfolio at Risk reports; benchmarks follow the SACCO Societies Regulations and are a guide. Figures depend on the loan book being reconciled.")}</div>
		`);
		this.$body.find(".sb-alert").on("click", (e) => {
			const a = d.alerts[$(e.currentTarget).data("i")];
			if (a && a.route) frappe.set_route(a.route);
		});
		this.charts(d);
	}

	charts(d) {
		const t = d.trends, C = sacco.COLORS, el = (c) => this.$body.find(c)[0];
		const labels = t.labels.map((l) => moment(l + "-01").format("MMM YY"));
		sacco.chart(el(".c-bal"), { type: "line", colors: [C.brand, C.blue], lineOptions: { hideDots: 1, regionFill: 1 },
			data: { labels, datasets: [{ name: __("Savings"), values: t.savings }, { name: __("Loan book"), values: t.loan_book }] } });
		sacco.chart(el(".c-pl"), { type: "axis-mixed", colors: [C.teal, C.amber, C.brand],
			data: { labels, datasets: [{ name: __("Income"), values: t.income, chartType: "bar" }, { name: __("Costs"), values: t.expense, chartType: "bar" },
				{ name: __("Surplus"), values: t.surplus, chartType: "line" }] } });
		sacco.chart(el(".c-sav"), { type: "bar", colors: [C.brand, C.red],
			data: { labels, datasets: [{ name: __("Deposits & credits"), values: t.deposits }, { name: __("Withdrawals"), values: t.withdrawals }] } });
		sacco.chart(el(".c-loan"), { type: "bar", colors: [C.blue, C.teal],
			data: { labels, datasets: [{ name: __("Disbursed"), values: t.disbursed }, { name: __("Principal repaid"), values: t.repaid }] } });
		sacco.chart(el(".c-mem"), { type: "line", colors: [C.brand], lineOptions: { hideDots: 1 },
			tooltipOptions: { formatTooltipY: (v) => v },
			data: { labels, datasets: [{ name: __("Members"), values: t.members }] } });
		const inc = (d.income_ytd || []).filter((r) => flt(r.amount) > 0);
		if (inc.length) {
			sacco.chart(el(".c-inc"), { type: "donut", height: 250, colors: [C.brand, C.blue, C.amber, C.teal, C.grey, C.red],
				data: { labels: inc.map((r) => r.account), datasets: [{ values: inc.map((r) => flt(r.amount)) }] } });
		} else {
			el(".c-inc").innerHTML = `<div class="text-muted small">${__("No income posted this year yet")}</div>`;
		}
	}
};
