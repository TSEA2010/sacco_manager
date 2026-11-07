// Copyright (c) 2026, Trident SACCO and contributors
// For license information, please see license.txt
// SACCO AGM Presentation: year-end figures as slides. "Present" goes full screen (arrow keys / click
// to move), "Print" gives one slide per landscape page for the meeting pack.

frappe.provide("sacco.agm");

const A = {
	money: (v) => format_currency(flt(v), frappe.defaults.get_default("currency"), 0),
	short(v) {
		v = flt(v);
		const a = Math.abs(v);
		return a >= 1e9 ? (v / 1e9).toFixed(2) + "B" : a >= 1e6 ? (v / 1e6).toFixed(2) + "M" : a >= 1e3 ? (v / 1e3).toFixed(1) + "K" : v.toFixed(0);
	},
	pct: (v) => flt(v).toFixed(1) + "%",
	esc: (s) => frappe.utils.escape_html(s == null ? "" : String(s)),
	chg(now, prev) {
		now = flt(now); prev = flt(prev);
		if (!prev) return `<span class="ag-muted">${__("new")}</span>`;
		const c = ((now - prev) / Math.abs(prev)) * 100;
		return `<span class="${c >= 0 ? "ag-up" : "ag-down"}">${c >= 0 ? "▲" : "▼"} ${Math.abs(c).toFixed(1)}%</span>`;
	},
	C: { brand: "#1a7f5a", blue: "#2f6fb0", amber: "#c98a12", red: "#c0392b", grey: "#8a96a3", teal: "#14857f" },
};

const AGM_STYLE = `
.ag { --ag-brand:#1a3c2e; --ag-accent:#1a7f5a; }
.ag .ag-slide { background: var(--card-bg); border:1px solid var(--border-color); border-radius:12px; padding:28px 34px; margin:0 auto 18px;
	max-width:1100px; min-height:560px; position:relative; }
.ag .ag-slide h1 { font-size:30px; color: var(--ag-accent); margin:0 0 4px; font-weight:700; }
.ag .ag-slide h2 { font-size:22px; color: var(--heading-color); margin:0 0 16px; font-weight:700; border-bottom:3px solid var(--ag-accent);
	padding-bottom:6px; display:inline-block; }
.ag .ag-num { position:absolute; right:22px; bottom:14px; font-size:11px; color: var(--text-muted); }
.ag .ag-cover { display:flex; flex-direction:column; justify-content:center; align-items:flex-start; min-height:500px; }
.ag .ag-cover .big { font-size:44px; font-weight:800; color: var(--ag-accent); line-height:1.1; }
.ag .ag-cover .co { font-size:18px; color: var(--heading-color); margin-top:14px; font-weight:600; }
.ag .ag-muted { color: var(--text-muted); }
.ag .ag-up { color:#15803d; font-weight:600; } .ag .ag-down { color:#c0392b; font-weight:600; }
.ag .ag-tiles { display:grid; grid-template-columns: repeat(3, 1fr); gap:14px; }
.ag .ag-tile { border:1px solid var(--border-color); border-radius:10px; padding:14px 16px; }
.ag .ag-tile .lab { font-size:12px; text-transform:uppercase; letter-spacing:.5px; color: var(--text-muted); }
.ag .ag-tile .val { font-size:28px; font-weight:800; color: var(--heading-color); margin:4px 0; }
.ag .ag-tile .sub { font-size:12px; }
.ag .ag-two { display:grid; grid-template-columns: 1fr 1fr; gap:22px; align-items:start; }
.ag table.ag-t { width:100%; border-collapse:collapse; font-size:14px; }
.ag table.ag-t th { text-align:left; font-weight:600; color: var(--text-muted); border-bottom:2px solid var(--border-color); padding:6px 8px; font-size:12px; text-transform:uppercase; }
.ag table.ag-t td { padding:6px 8px; border-bottom:1px solid var(--border-color); }
.ag table.ag-t .n { text-align:right; white-space:nowrap; }
.ag table.ag-t tr.tot td { font-weight:700; border-top:2px solid var(--heading-color); }
.ag .ag-flow { display:flex; align-items:center; gap:10px; flex-wrap:wrap; margin:6px 0 16px; font-size:15px; }
.ag .ag-flow .box { border:1px solid var(--border-color); border-radius:8px; padding:10px 14px; text-align:center; min-width:120px; }
.ag .ag-flow .box b { display:block; font-size:20px; }
.ag .ag-pill { display:inline-block; padding:2px 10px; border-radius:10px; font-size:12px; font-weight:700; }
.ag .good { background:#e3f4e8; color:#15803d; } .ag .warn { background:#fff4e0; color:#8a5300; } .ag .bad { background:#fde8e7; color:#c0392b; }
.ag .info { background: var(--control-bg); color: var(--text-muted); }
.ag .ag-note { font-size:12px; color: var(--text-muted); margin-top:10px; }
.ag textarea.ag-pri { width:100%; min-height:300px; font-size:16px; line-height:1.6; border:1px dashed var(--border-color); border-radius:8px; padding:12px;
	background: transparent; color: var(--text-color); }
.ag .ag-bar { height:10px; background: var(--control-bg); border-radius:5px; overflow:hidden; } .ag .ag-bar > div { height:100%; background: var(--ag-accent); }
/* present mode */
body.ag-presenting { overflow:hidden; }
.ag.present { position:fixed; inset:0; z-index:1050; background: var(--bg-color); padding:2vh 2vw; display:flex; align-items:center; justify-content:center; }
.ag.present .ag-slide { display:none; width:96vw; max-width:none; height:94vh; margin:0; overflow:auto; font-size:1.15em; }
.ag.present .ag-slide.active { display:block; }
.ag.present .ag-slide h2 { font-size:30px; } .ag.present .ag-tile .val { font-size:38px; } .ag.present table.ag-t { font-size:17px; }
.ag .ag-exit { display:none; } .ag.present .ag-exit { display:block; position:fixed; top:10px; right:16px; z-index:1060; }
@media print {
	@page { size: A4 landscape; margin: 10mm; }
	.page-head, .navbar, .ag .ag-exit, .ag-controls { display:none !important; }
	.ag .ag-slide { break-after: page; border:none; min-height:0; max-width:none; padding:0 4mm; }
	.ag textarea.ag-pri { border:none; }
}
`;

frappe.pages["sacco-agm"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({ parent: wrapper, title: __("AGM Presentation"), single_column: true });
	frappe.dom.set_style(AGM_STYLE, "sacco-agm-style");
	wrapper.agm = new sacco.agm.Deck(page);
};

sacco.agm.Deck = class {
	constructor(page) {
		this.page = page;
		const last = new Date().getFullYear() - 1;
		this.year = page.add_field({ fieldname: "year", label: __("Financial year"), fieldtype: "Select",
			options: Array.from({ length: 10 }, (_, i) => String(new Date().getFullYear() - i)).join("\n"), default: String(last),
			change: () => this.load() });
		this.sav = page.add_field({ fieldname: "savings_rate", label: __("Interest on deposits %"), fieldtype: "Float",
			change: () => this.load() });
		this.div = page.add_field({ fieldname: "dividend_rate", label: __("Dividend on shares %"), fieldtype: "Float",
			change: () => this.load() });
		page.set_primary_action(__("Present"), () => this.present(), "view");
		page.add_inner_button(__("Print / PDF"), () => window.print());
		page.add_inner_button(__("Live board"), () => frappe.set_route("sacco-board"));
		this.$deck = $(`<div class="ag"></div>`).appendTo(page.main);
		this.idx = 0;
		$(document).on("keydown.sacco_agm", (e) => {
			if (!this.$deck.hasClass("present")) return;
			if (["ArrowRight", "PageDown", " "].includes(e.key)) { this.go(1); e.preventDefault(); }
			if (["ArrowLeft", "PageUp"].includes(e.key)) { this.go(-1); e.preventDefault(); }
			if (e.key === "Escape") this.exit();
		});
		this.load();
	}

	load() {
		this.$deck.html(`<div class="text-muted p-4">${__("Preparing the year-end figures…")}</div>`);
		const sr = this.sav.get_value(), dr = this.div.get_value();
		frappe.call({ method: "sacco_manager.analytics.get_agm",
			args: { year: this.year.get_value(), savings_rate: sr === "" || sr == null ? null : sr, dividend_rate: dr || 0 } })
			.then((r) => this.render(r.message));
	}

	render(d) {
		this.d = d;
		if (this.sav.get_value() === "" || this.sav.get_value() == null) this.sav.set_input(d.appropriation.savings_rate);
		const n = d.now, p = d.prev, m = d.movement, ap = d.appropriation;
		const chg = d.prev_available ? A.chg : () => `<span class="ag-muted">${__("first year on the system")}</span>`;
		const tile = (lab, val, sub) => `<div class="ag-tile"><div class="lab">${lab}</div><div class="val">${val}</div><div class="sub">${sub}</div></div>`;
		const rows = (list) => list.map((r) => `<tr><td>${A.esc(r.account)}</td><td class="n">${A.money(r.amount)}</td></tr>`).join("");
		const bsRows = (list) => (list || []).map((r) => `<tr><td>${A.esc(r.account)}</td><td class="n">${A.money(r.amount)}</td></tr>`).join("");
		const bsTot = (list) => (list || []).reduce((s, r) => s + flt(r.amount), 0);
		const ratioRows = n.ratios.map((r) => {
			const pr = d.prev_available ? (p.ratios.find((x) => x.key === r.key) || {}).value : null;
			return `<tr><td>${A.esc(r.label)}</td><td class="n">${pr == null ? "–" : A.pct(pr)}</td><td class="n"><b>${A.pct(r.value)}</b></td>
				<td>${A.esc(r.benchmark)}</td><td><span class="ag-pill ${r.status}">${{ good: __("On target"), warn: __("Watch"), bad: __("Below target"), info: __("Info") }[r.status]}</span></td></tr>`;
		}).join("");
		const branch = d.by_branch.map((b) => `<tr><td>${A.esc(b.branch)}</td><td class="n">${b.members || ""}</td><td class="n">${A.money(b.savings)}</td>
			<td class="n">${A.money(b.loan_book)}</td></tr>`).join("");
		const par = d.par.map((b) => `<tr><td>${A.esc(b.class)}</td><td class="n">${b.loans}</td><td class="n">${A.money(b.principal)}</td>
			<td class="n">${A.money(b.provision)}</td></tr>`).join("");
		const kyc = (d.kyc.fields || []).map((x) => `<tr><td style="width:35%">${A.esc(x.label)}</td><td><div class="ag-bar"><div style="width:${x.pct}%"></div></div></td>
			<td class="n">${x.pct}%</td></tr>`).join("");
		const key = `sacco_agm_priorities_${d.year}`;
		let pri = "";
		try { pri = localStorage.getItem(key) || ""; } catch (e) { pri = ""; }
		if (!pri) pri = [__("1. Complete the loan-book rebuild and auditor sign-off"), __("2. 100% member KYC and signed consent forms"),
			__("3. Formal loan agreements and limits for group-company lending"), __("4. Monthly employer remittances on time, every month"),
			__("5. Member statements by email / SMS every quarter")].join("\n");

		const slides = [
			`<div class="ag-cover"><div class="ag-muted">${__("Annual General Meeting")}</div>
				<div class="big">${__("Financial year")} ${d.year}</div>
				<div class="co">${A.esc(d.company)}</div>
				<div class="ag-muted mt-2">${A.esc(d.period)}${d.partial ? " · " + __("year to date") : ""} · ${__("prepared")} ${A.esc(d.generated)}</div>
				<div class="ag-note">${__("Figures are from the SACCO system and are subject to the external audit.")}</div></div>`,
			`<h2>${__("The year at a glance")}</h2><div class="ag-tiles">
				${tile(__("Members"), n.members, chg(n.members, p.members))}
				${tile(__("Members' savings"), A.short(n.savings), chg(n.savings, p.savings))}
				${tile(__("Loan book"), A.short(n.loan_book), chg(n.loan_book, p.loan_book))}
				${tile(__("Total income"), A.short(d.income_total), chg(d.income_total, d.prev_income))}
				${tile(__("Surplus"), A.short(d.surplus), chg(d.surplus, d.prev_surplus))}
				${tile(__("Total assets"), A.short(n.total_assets), chg(n.total_assets, p.total_assets))}
			</div>`,
			`<h2>${__("Membership")}</h2><div class="ag-two"><div>
				<div class="ag-flow"><div class="box">${__("Start of year")}<b>${m.members_start}</b></div>＋<div class="box">${__("Joined")}<b>${m.joined}</b></div>
				＝<div class="box">${__("End of year")}<b>${m.members_end}</b></div></div>
				<table class="ag-t"><tr><th>${__("Employer")}</th><th class="n">${__("Members")}</th><th class="n">${__("Savings")}</th><th class="n">${__("Loans")}</th></tr>${branch}</table></div>
				<div><div class="ch-mem"></div></div></div>`,
			`<h2>${__("Members' savings")}</h2>
				<div class="ag-flow"><div class="box">${__("Opening")}<b>${A.short(n.savings - m.deposits + m.withdrawals)}</b></div>＋<div class="box">${__("Deposits & interest")}<b>${A.short(m.deposits)}</b></div>
				−<div class="box">${__("Withdrawals")}<b>${A.short(m.withdrawals)}</b></div>＝<div class="box">${__("Closing")}<b>${A.short(n.savings)}</b></div></div>
				<div class="ch-sav"></div>`,
			`<h2>${__("Lending")}</h2>
				<div class="ag-flow"><div class="box">${__("Loans given")}<b>${A.short(m.disbursed)}</b></div><div class="box">${__("Principal repaid")}<b>${A.short(m.repaid)}</b></div>
				<div class="box">${__("Loan book at year end")}<b>${A.short(n.loan_book)}</b></div><div class="box">${__("Borrowers")}<b>${n.borrowers}</b></div>
				<div class="box">${__("Lent to group companies")}<b>${A.short(n.group_company_loans)}</b></div></div>
				<div class="ch-loan"></div>`,
			`<h2>${__("Loan portfolio quality")}</h2><div class="ag-two"><div>
				<table class="ag-t"><tr><th>${__("Classification")}</th><th class="n">${__("Loans")}</th><th class="n">${__("Principal")}</th><th class="n">${__("Provision")}</th></tr>${par}</table>
				<div class="ag-note">${__("Provision required")} ${A.money(n.required_provision)} · ${__("booked in the accounts")} ${A.money(n.booked_provision)}</div></div>
				<div><div class="ch-par"></div></div></div>`,
			`<h2>${__("Income and expenditure")}</h2><div class="ag-two">
				<table class="ag-t"><tr><th>${__("Income")}</th><th class="n">${d.year}</th></tr>${rows(d.income)}
					<tr class="tot"><td>${__("Total income")}</td><td class="n">${A.money(d.income_total)}</td></tr></table>
				<table class="ag-t"><tr><th>${__("Expenditure")}</th><th class="n">${d.year}</th></tr>${rows(d.expense)}
					<tr class="tot"><td>${__("Total expenditure")}</td><td class="n">${A.money(d.expense_total)}</td></tr>
					<tr class="tot"><td>${__("Surplus for the year")}</td><td class="n">${A.money(d.surplus)}</td></tr>
					${d.prev_available ? `<tr><td class="ag-muted">${__("Last year")}</td><td class="n ag-muted">${A.money(d.prev_surplus)}</td></tr>` : ""}</table></div>`,
			`<h2>${__("Financial position at year end")}</h2><div class="ag-two">
				<table class="ag-t"><tr><th>${__("Assets")}</th><th class="n"></th></tr>${bsRows(d.balance_sheet.Asset)}
					<tr class="tot"><td>${__("Total assets")}</td><td class="n">${A.money(bsTot(d.balance_sheet.Asset))}</td></tr></table>
				<table class="ag-t"><tr><th>${__("Liabilities")}</th><th class="n"></th></tr>${bsRows(d.balance_sheet.Liability)}
					<tr class="tot"><td>${__("Total liabilities")}</td><td class="n">${A.money(bsTot(d.balance_sheet.Liability))}</td></tr>
					<tr><th>${__("Members' funds & reserves")}</th><th class="n"></th></tr>${bsRows(d.balance_sheet.Equity)}
					<tr class="tot"><td>${__("Total funds")}</td><td class="n">${A.money(bsTot(d.balance_sheet.Equity))}</td></tr></table></div>`,
			`<h2>${__("Key ratios")}</h2><table class="ag-t"><tr><th>${__("Ratio")}</th><th class="n">${d.year - 1}</th><th class="n">${d.year}</th>
				<th>${__("Target")}</th><th></th></tr>${ratioRows}</table>
				<div class="ag-note">${__("Targets follow the SACCO Societies Regulations and good practice; they are a guide for the Board.")}</div>`,
			`<h2>${__("Proposed appropriation of surplus")}</h2><table class="ag-t" style="max-width:760px">
				<tr><td>${__("Surplus for the year")}</td><td class="n">${A.money(ap.surplus)}</td></tr>
				<tr><td>${__("Statutory reserve")} (${ap.reserve_pct}%)</td><td class="n">(${A.money(ap.reserve)})</td></tr>
				<tr><td>${__("Interest on members' deposits")} — ${A.pct(ap.savings_rate)} ${__("on average savings of")} ${A.money(ap.avg_savings)}</td>
					<td class="n">(${A.money(ap.interest)})</td></tr>
				<tr><td>${__("Dividend on share capital")} — ${A.pct(ap.dividend_rate)} ${__("on")} ${A.money(ap.share_capital)}</td><td class="n">(${A.money(ap.dividend)})</td></tr>
				<tr class="tot"><td>${__("Retained / carried forward")}</td><td class="n">${A.money(ap.retained)}</td></tr></table>
				<div class="ag-note">${__("Withholding tax")}: ${__("interest")} ${A.pct(ap.interest_wht_rate)}, ${__("dividends")} ${A.pct(ap.dividend_wht_rate)} —
					${__("deducted from each member's payment.")} ${__("Change the rates at the top of the page to model the Board's proposal. Subject to audit and AGM approval.")}</div>`,
			`<h2>${__("Member records and governance")}</h2><div class="ag-two"><div>
				<div class="ag-tiles" style="grid-template-columns:1fr 1fr"><div class="ag-tile"><div class="lab">${__("Active members with complete KYC")}</div>
				<div class="val">${d.kyc.pct}%</div><div class="sub ag-muted">${d.kyc.complete} ${__("of")} ${d.kyc.members}</div></div></div>
				<table class="ag-t mt-3">${kyc}</table></div>
				<div><b>${__("Priorities for next year")}</b><textarea class="ag-pri">${A.esc(pri)}</textarea>
				<div class="ag-note">${__("Edit the list before the meeting; it is kept in this browser.")}</div></div></div>`,
			`<div class="ag-cover"><div class="big">${__("Thank you")}</div><div class="co">${__("Questions and discussion")}</div>
				<div class="ag-muted mt-2">${A.esc(d.company)}</div></div>`,
		];
		this.$deck.html(`<button class="btn btn-default btn-sm ag-exit">${__("Exit")} (Esc)</button>` +
			slides.map((s, i) => `<div class="ag-slide" data-i="${i}">${s}<div class="ag-num">${i + 1} / ${slides.length}</div></div>`).join(""));
		this.$deck.find(".ag-exit").on("click", () => this.exit());
		this.$deck.find(".ag-pri").on("input", (e) => { try { localStorage.setItem(key, e.target.value); } catch (x) { /* private mode */ } });
		this.$deck.find(".ag-slide").on("click", (e) => {
			if (this.$deck.hasClass("present") && !$(e.target).is("textarea, input, button")) this.go(1);
		});
		this.drawAll();
	}

	drawAll() {
		const d = this.d, t = d.trends, C = A.C, el = (c) => this.$deck.find(c)[0];
		const labels = t.labels.map((l) => moment(l + "-01").format("MMM"));
		const mk = (e, o) => { if (!e) return; e.innerHTML = ""; try { new frappe.Chart(e, Object.assign({ height: 300,
			axisOptions: { xIsSeries: 1, shortenYAxisNumbers: 1 }, tooltipOptions: { formatTooltipY: (v) => A.money(v) } }, o)); } catch (x) { /* ignore */ } };
		mk(el(".ch-mem"), { type: "bar", colors: [C.brand], tooltipOptions: { formatTooltipY: (v) => v },
			data: { labels: d.by_branch.map((b) => b.branch), datasets: [{ name: __("Members"), values: d.by_branch.map((b) => b.members) }] } });
		mk(el(".ch-sav"), { type: "axis-mixed", colors: [C.brand, C.red, C.blue],
			data: { labels, datasets: [{ name: __("Deposits & credits"), values: t.deposits, chartType: "bar" },
				{ name: __("Withdrawals"), values: t.withdrawals, chartType: "bar" }, { name: __("Savings balance"), values: t.savings, chartType: "line" }] } });
		mk(el(".ch-loan"), { type: "axis-mixed", colors: [C.blue, C.teal, C.amber],
			data: { labels, datasets: [{ name: __("Disbursed"), values: t.disbursed, chartType: "bar" },
				{ name: __("Principal repaid"), values: t.repaid, chartType: "bar" }, { name: __("Loan book"), values: t.loan_book, chartType: "line" }] } });
		const par = d.par.filter((b) => flt(b.principal) > 0);
		if (par.length) mk(el(".ch-par"), { type: "pie", colors: [C.brand, C.amber, C.red, "#7b2d26", C.grey],
			data: { labels: par.map((b) => b.class), datasets: [{ values: par.map((b) => flt(b.principal)) }] } });
	}

	present() {
		if (!this.d) return;
		this.$deck.addClass("present");
		$("body").addClass("ag-presenting");
		this.idx = 0;
		this.show();
		const el = document.documentElement;
		if (el.requestFullscreen) el.requestFullscreen().catch(() => {});
		$(document).off("fullscreenchange.sacco_agm").on("fullscreenchange.sacco_agm", () => {
			if (!document.fullscreenElement && this.$deck.hasClass("present")) this.exit();
		});
	}

	exit() {
		$(document).off("fullscreenchange.sacco_agm");
		this.$deck.removeClass("present");
		$("body").removeClass("ag-presenting");
		this.$deck.find(".ag-slide").removeClass("active");
		if (document.fullscreenElement && document.exitFullscreen) document.exitFullscreen().catch(() => {});
		this.drawAll();
	}

	go(step) {
		const n = this.$deck.find(".ag-slide").length;
		this.idx = Math.max(0, Math.min(n - 1, this.idx + step));
		this.show();
	}

	show() {
		const $s = this.$deck.find(".ag-slide").removeClass("active");
		$s.eq(this.idx).addClass("active");
		this.drawAll(); // charts size to the visible slide
	}
};
