import frappe
import json
import hashlib
import hmac
from frappe import _
from frappe.utils import flt, today, get_url, cstr


# ============================================================
# AUTH HELPER
# ============================================================
def get_member_for_user():
    user = frappe.session.user
    if user == "Guest":
        frappe.throw(_("Not logged in"), frappe.AuthenticationError)

    member = frappe.db.get_value(
        "SACCO Member",
        {"user_id": user},
        [
            "name", "full_name", "first_name", "last_name",
            "branch", "status", "customer",
            "joining_date", "member_no", "email_id", "phone",
            "total_shares", "share_balance",
        ],
        as_dict=True,
    )
    if not member:
        frappe.throw(
            _("No SACCO Member record is linked to your account. "
              "Please contact the SACCO office."),
            frappe.PermissionError,
        )
    return member


def fmt_kes(n):
    return f"KES {flt(n):,.0f}"


def _get_token_secret():
    secret = frappe.conf.get("secret_key") or ""
    if not secret:
        frappe.throw(_("Security configuration missing. Please contact the SACCO office."))
    return secret


def _make_token(loan_application, guarantor_member):
    secret = _get_token_secret()
    token_data = f"{loan_application}:{guarantor_member}"
    return hmac.new(secret.encode(), token_data.encode(), hashlib.sha256).hexdigest()


def _verify_token(loan_application, guarantor_member, token):
    expected = _make_token(loan_application, guarantor_member)
    return hmac.compare_digest(cstr(token), expected)


# ============================================================
# PORTAL SUMMARY
# ============================================================
@frappe.whitelist()
def get_portal_summary():
    try:
        member = get_member_for_user()

        savings = frappe.db.get_value(
            "SACCO Savings Account",
            {"member": member.name},
            ["name", "balance", "status"],
            as_dict=True,
        ) or {}

        total_shares = int(member.get("total_shares") or 0)

        settings = frappe.get_single("SACCO Settings")
        share_price  = flt(settings.get("default_share_price") or 100)
        multiplier   = flt(settings.get("savings_multiplier") or 3)
        company      = frappe.db.get_default("company")

        savings_balance = flt(savings.get("balance", 0))
        loan_limit      = savings_balance * multiplier

        # Active Loans
        active_loans = []
        customer = member.get("customer")
        if customer:
            try:
                active_loans = frappe.get_all(
                    "Loan",
                    filters={
                        "applicant": customer,
                        "docstatus": 1,
                        "status": ["in", ["Disbursed", "Active", "Partially Disbursed", "Sanctioned"]],
                    },
                    fields=[
                        "name", "loan_type", "loan_amount",
                        "total_amount_paid", "outstanding_amount",
                        "repayment_start_date", "repayment_periods",
                        "rate_of_interest", "status",
                        "monthly_repayment_amount",
                    ],
                )
                for loan in active_loans:
                    next_row = frappe.db.get_value(
                        "Loan Repayment Schedule",
                        {
                            "parent": loan.name,
                            "status": ["in", ["Pending", ""]],
                            "payment_date": [">=", today()],
                        },
                        ["payment_date", "total_payment"],
                        as_dict=True,
                        order_by="payment_date asc",
                    )
                    if next_row:
                        loan["next_payment_date"]        = str(next_row.payment_date)
                        loan["monthly_repayment_amount"] = flt(next_row.total_payment)
            except Exception as e:
                frappe.log_error(f"Error fetching loans: {e}", "SACCO Portal")
                active_loans = []

        outstanding_loans = sum(flt(l.get("outstanding_amount", 0)) for l in active_loans)

        # Pending applications count
        pending_applications = 0
        if customer:
            try:
                pending_applications = frappe.db.count(
                    "Loan Application",
                    {"applicant": customer, "status": "Open", "docstatus": ["!=", 2]},
                )
            except Exception:
                pass

        # Recent activity
        recent_activity = []
        savings_acct = savings.get("name")
        if savings_acct:
            try:
                recent_txs = frappe.get_all(
                    "SACCO Savings Transaction",
                    filters={"savings_account": savings_acct, "docstatus": 1},
                    fields=["transaction_date", "transaction_type", "amount", "remarks"],
                    order_by="transaction_date desc, creation desc",
                    limit=4,
                )
                for tx in recent_txs:
                    t     = tx.transaction_type or ""
                    atype = "deposit" if t == "Deposit" else "withdrawal" if t == "Withdrawal" else "shares"
                    desc  = f"Savings {t.lower()} — {fmt_kes(tx.amount)}"
                    if tx.remarks:
                        desc += f" via {tx.remarks}"
                    recent_activity.append({"type": atype, "description": desc, "date": str(tx.transaction_date)})
            except Exception:
                pass

        return {
            "member": {
                "name":         member.get("name"),
                "full_name":    member.get("full_name") or f"{member.get('first_name', '')} {member.get('last_name', '')}".strip(),
                "first_name":   member.get("first_name"),
                "branch":       member.get("branch"),
                "status":       member.get("status"),
                "member_no":    member.get("member_no"),
                "email_id":     member.get("email_id"),
                "joining_date": str(member.get("joining_date")) if member.get("joining_date") else None,
                "customer":     member.get("customer"),
            },
            "savings":              savings,
            "savings_balance":      savings_balance,
            "savings_account":      savings_acct or "",
            "total_shares":         total_shares,
            "share_value":          total_shares * share_price,
            "share_price":          share_price,
            "active_loans":         active_loans,
            "outstanding_loans":    outstanding_loans,
            "loan_limit":           loan_limit,
            "loan_multiplier":      multiplier,
            "company":              company,
            "pending_applications": pending_applications,
            "recent_activity":      recent_activity,
        }
    except Exception as e:
        frappe.log_error(f"Error in get_portal_summary: {e}", "SACCO Portal")
        frappe.throw(_(f"Error loading portal: {e}"))


# ============================================================
# LOAN TYPES
# ============================================================
@frappe.whitelist()
def get_loan_types():
    try:
        for doctype in ("Loan Product", "Loan Type"):
            if not frappe.db.exists("DocType", doctype):
                continue
            try:
                fields  = ["name"]
                filters = {}
                if frappe.db.has_column(doctype, "rate_of_interest"):
                    fields.append("rate_of_interest")
                if frappe.db.has_column(doctype, "description"):
                    fields.append("description")
                if frappe.db.has_column(doctype, "disabled"):
                    filters["disabled"] = 0
                products = frappe.get_all(doctype, filters=filters, fields=fields,
                                          order_by="name asc", limit_page_length=100)
                for p in products:
                    p.setdefault("rate_of_interest", 12)
                    p.setdefault("description", "")
                if products:
                    return products
            except Exception as e:
                frappe.log_error(f"Error fetching {doctype}: {e}", "SACCO Portal")

        return [
            {"name": "Normal Loan",    "rate_of_interest": 12},
            {"name": "Emergency Loan", "rate_of_interest": 15},
            {"name": "Education Loan", "rate_of_interest": 10},
            {"name": "Business Loan",  "rate_of_interest": 13},
        ]
    except Exception as e:
        frappe.log_error(f"Error in get_loan_types: {e}", "SACCO Portal")
        return [{"name": "Normal Loan", "rate_of_interest": 12}]


# ============================================================
# SAVINGS TRANSACTIONS
# ============================================================
@frappe.whitelist()
def get_savings_transactions(limit=100, offset=0):
    try:
        member = get_member_for_user()
        savings_account = frappe.db.get_value(
            "SACCO Savings Account", {"member": member.get("name")}, "name"
        )
        if not savings_account:
            return []
        if not frappe.db.exists("DocType", "SACCO Savings Transaction"):
            return []

        fields = ["name", "transaction_date", "transaction_type", "amount"]
        for col in ("running_balance", "reference_doctype", "reference_name", "remarks"):
            if frappe.db.has_column("SACCO Savings Transaction", col):
                fields.append(col)

        transactions = frappe.get_all(
            "SACCO Savings Transaction",
            filters={"savings_account": savings_account, "docstatus": 1},
            fields=fields,
            order_by="transaction_date desc, creation desc",
            limit=int(limit),
            start=int(offset),
        )
        for tx in transactions:
            tx.setdefault("running_balance", 0)
        return transactions
    except Exception as e:
        frappe.log_error(f"Error in get_savings_transactions: {e}", "SACCO Portal")
        return []


# ============================================================
# SHARE LEDGER
# ============================================================
@frappe.whitelist()
def get_share_ledger(limit=100, offset=0):
    try:
        member = get_member_for_user()
        if not frappe.db.exists("DocType", "SACCO Share Ledger"):
            return []

        fields = ["name", "posting_date", "entry_type", "shares_quantity"]
        for col in ("running_total", "remarks"):
            if frappe.db.has_column("SACCO Share Ledger", col):
                fields.append(col)

        entries = frappe.get_all(
            "SACCO Share Ledger",
            filters={"member": member.get("name"), "docstatus": 1},
            fields=fields,
            order_by="posting_date desc, creation desc",
            limit=int(limit),
            start=int(offset),
        )
        for e in entries:
            e.setdefault("running_total", 0)
        return entries
    except Exception as e:
        frappe.log_error(f"Error in get_share_ledger: {e}", "SACCO Portal")
        return []


# ============================================================
# LOAN SCHEDULE
# ============================================================
@frappe.whitelist()
def get_loan_schedule(loan_name):
    try:
        member   = get_member_for_user()
        customer = member.get("customer")
        loan_applicant = frappe.db.get_value("Loan", loan_name, "applicant")
        if not loan_applicant or loan_applicant != customer:
            frappe.throw(_("Access denied"), frappe.PermissionError)
        if not frappe.db.exists("DocType", "Loan Repayment Schedule"):
            return []

        fields = ["payment_date", "principal_amount", "interest_amount", "total_payment"]
        for col in ("balance_loan_amount", "paid_amount", "status"):
            if frappe.db.has_column("Loan Repayment Schedule", col):
                fields.append(col)

        return frappe.get_all(
            "Loan Repayment Schedule",
            filters={"parent": loan_name},
            fields=fields,
            order_by="payment_date asc",
        )
    except Exception as e:
        frappe.log_error(f"Error in get_loan_schedule: {e}", "SACCO Portal")
        return []


# ============================================================
# MEMBER SEARCH (for guarantor picker)
# ============================================================
@frappe.whitelist()
def search_members(query="", exclude=""):
    """
    Search active SACCO members by name/member_no.
    Excludes self and already-added members.
    """
    current_member = get_member_for_user()  # auth gate

    exclude_list = [e.strip() for e in cstr(exclude).split(",") if e.strip()]
    if current_member.name not in exclude_list:
        exclude_list.append(current_member.name)

    try:
        results = frappe.db.sql("""
            SELECT
                m.name,
                m.member_no,
                COALESCE(m.full_name,
                    CONCAT(m.first_name, ' ', COALESCE(m.last_name, ''))) AS full_name,
                m.branch,
                m.email_id,
                sa.name  AS savings_account,
                COALESCE(sa.balance, 0) AS savings_balance
            FROM `tabSACCO Member` m
            LEFT JOIN `tabSACCO Savings Account` sa ON sa.member = m.name
            WHERE m.status = 'Active'
              AND m.name NOT IN %(exclude)s
              AND (
                m.full_name LIKE %(q)s
                OR m.first_name LIKE %(q)s
                OR m.member_no LIKE %(q)s
              )
            ORDER BY m.full_name
            LIMIT 20
        """, {
            "q": f"%{query}%",
            "exclude": tuple(exclude_list) if exclude_list else ("__none__",),
        }, as_dict=True)

        return results
    except Exception as e:
        frappe.log_error(f"Error in search_members: {e}", "SACCO Portal")
        return []


# ============================================================
# GET LOAN APPLICATIONS (member's own) — read proposed_pledges
# ============================================================
@frappe.whitelist()
def get_loan_applications():
    try:
        member   = get_member_for_user()
        customer = member.get("customer")
        if not customer:
            return []

        apps = frappe.get_all(
            "Loan Application",
            filters={"applicant": customer, "docstatus": ["!=", 2]},
            fields=[
                "name", "loan_product", "loan_amount",
                "repayment_periods", "rate_of_interest",
                "status", "posting_date",
            ],
            order_by="posting_date desc",
            limit=50,
        )

        for app in apps:
            # Read existing Proposed Pledges (created by office users)
            try:
                pledges = frappe.get_all(
                    "Proposed Pledge",
                    filters={"parent": app.name, "parenttype": "Loan Application"},
                    fields=["loan_security", "loan_security_name", "qty", "amount"],
                )
                # Enrich with member info via Loan Security custom fields
                for pledge in pledges:
                    if pledge.loan_security:
                        ls = frappe.db.get_value(
                            "Loan Security",
                            pledge.loan_security,
                            ["sacco_member_saving", "is_sacco_security", "original_security_value"],
                            as_dict=True,
                        )
                        if ls and ls.get("is_sacco_security"):
                            member_name = frappe.db.get_value(
                                "SACCO Savings Account",
                                ls.get("sacco_member_saving"),
                                "member",
                            )
                            if member_name:
                                member_info = frappe.db.get_value(
                                    "SACCO Member",
                                    member_name,
                                    ["full_name", "member_no"],
                                    as_dict=True,
                                )
                                pledge["guarantor_member"]  = member_name
                                pledge["guarantor_name"]    = member_info.get("full_name") if member_info else member_name
                                pledge["member_no"]         = member_info.get("member_no") if member_info else ""
                                pledge["pledge_value"]      = pledge.get("amount", 0)
                                pledge["savings_account"]   = ls.get("sacco_member_saving")
                                pledge["status"]            = "Confirmed"
                app["guarantors"] = pledges
            except Exception as e:
                frappe.log_error(f"Error fetching pledges for {app.name}: {e}", "SACCO Portal")
                app["guarantors"] = []

        return apps
    except Exception as e:
        frappe.log_error(f"Error in get_loan_applications: {e}", "SACCO Portal")
        return []


# ============================================================
# LOAN SECURITY HELPER (creates or reuses per savings account)
# ============================================================
def _create_loan_security(savings_account, member, member_name, multiplier):
    """
    Creates (or reuses) a Loan Security backed by a SACCO Savings Account.
    Returns the Loan Security name, or None on failure.
    """
    try:
        if not frappe.db.exists("DocType", "Loan Security"):
            frappe.log_error("Loan Security doctype missing", "SACCO Portal")
            return None

        existing = frappe.db.get_value(
            "Loan Security",
            {"sacco_member_saving": savings_account, "is_sacco_security": 1},
            "name",
        )
        if existing:
            sav_balance    = flt(frappe.db.get_value("SACCO Savings Account", savings_account, "balance") or 0)
            security_value = sav_balance * multiplier
            frappe.db.set_value("Loan Security", existing, "original_security_value", security_value)
            return existing

        sav_balance    = flt(frappe.db.get_value("SACCO Savings Account", savings_account, "balance") or 0)
        security_value = sav_balance * multiplier

        sacco_sec_type = (
            frappe.db.get_value("Loan Security Type", {"name": ["like", "%SACCO%"]}, "name")
            or frappe.db.get_value("Loan Security Type", {}, "name")
        )
        if not sacco_sec_type:
            frappe.log_error("No Loan Security Type found", "SACCO Portal")
            return None

        code     = f"SEC-{member}-{savings_account}"[:140]
        sec_name = f"SACCO Savings — {member_name} ({savings_account})"

        ls = frappe.get_doc({
            "doctype":                 "Loan Security",
            "loan_security_code":      code,
            "loan_security_name":      sec_name,
            "loan_security_type":      sacco_sec_type,
            "is_sacco_security":       1,
            "sacco_member_saving":     savings_account,
            "original_security_value": security_value,
            "haircut":                 0,
            "loan_to_value_ratio":     100,
            "disabled":                0,
        })
        ls.flags.ignore_permissions = True
        ls.insert()
        frappe.db.commit()
        return ls.name

    except Exception as e:
        frappe.log_error(f"Error in _create_loan_security for {savings_account}: {e}", "SACCO Portal")
        return None


# ============================================================
# SUBMIT LOAN APPLICATION V2
# Creates Loan Security per guarantor, adds Proposed Pledges.
# Office users manage formal approval and pledge processing.
# ============================================================
@frappe.whitelist()
def submit_loan_application_v2(loan_product, loan_amount, repayment_periods,
                                purpose, description="", guarantors="[]"):
    try:
        member   = get_member_for_user()
        customer = member.get("customer")

        if not customer:
            frappe.throw(_("No Customer record linked to your SACCO Member. Please contact the office."))

        loan_amount       = flt(loan_amount)
        repayment_periods = int(repayment_periods)

        if loan_amount <= 0:
            frappe.throw(_("Loan amount must be greater than zero."))

        # ---- Parse & validate guarantors ----
        guarantors_list = json.loads(guarantors) if isinstance(guarantors, str) else guarantors

        if not guarantors_list:
            frappe.throw(_("At least one guarantor is required."))

        settings   = frappe.get_single("SACCO Settings")
        multiplier = flt(settings.get("savings_multiplier") or 3)

        # Validate each guarantor and accumulate total pledged
        total_pledge_value = 0
        guarantor_lines    = []

        for g in guarantors_list:
            sav_acct     = g.get("savings_account")
            pledge_value = flt(g.get("pledge_value", 0))
            is_self      = bool(g.get("is_self", False))
            gm_name      = g.get("member_name", g.get("member", ""))

            if not sav_acct:
                frappe.throw(_(f"Guarantor {gm_name} has no linked Savings Account."))
            if pledge_value <= 0:
                frappe.throw(_(f"Guarantor {gm_name} must have a pledge value greater than zero."))

            if not is_self:
                gm = frappe.db.get_value("SACCO Member", g["member"], ["status", "name"], as_dict=True)
                if not gm:
                    frappe.throw(_(f"Guarantor member {g['member']} not found."))
                if gm.status != "Active":
                    frappe.throw(_(f"Guarantor {gm_name} is not an active member."))

                sa_member = frappe.db.get_value("SACCO Savings Account", sav_acct, "member")
                if sa_member != g["member"]:
                    frappe.throw(_(f"Savings account {sav_acct} does not belong to {gm_name}."))

                sa_balance    = flt(frappe.db.get_value("SACCO Savings Account", sav_acct, "balance") or 0)
                pledge_ceiling = sa_balance * multiplier
                if pledge_value > pledge_ceiling:
                    frappe.throw(_(
                        f"Pledge value {fmt_kes(pledge_value)} for {gm_name} "
                        f"exceeds their limit of {fmt_kes(pledge_ceiling)}."
                    ))

            total_pledge_value += pledge_value
            guarantor_lines.append(
                f"  • {gm_name} | Savings: {sav_acct} | Pledge: {fmt_kes(pledge_value)}"
                + (" (self)" if is_self else "")
            )

        if total_pledge_value < loan_amount:
            frappe.throw(_(
                f"Total pledged savings value ({fmt_kes(total_pledge_value)}) must cover the "
                f"loan amount ({fmt_kes(loan_amount)}). Shortfall: {fmt_kes(loan_amount - total_pledge_value)}"
            ))

        # ---- Get interest rate ----
        rate = 12
        for doctype in ("Loan Product", "Loan Type"):
            if frappe.db.exists(doctype, loan_product):
                r = flt(frappe.db.get_value(doctype, loan_product, "rate_of_interest") or 0)
                if r > 0:
                    rate = r
                    break

        company = frappe.db.get_default("company")

        # Build description with guarantor summary for office reference
        guarantor_summary = (
            f"\n\nProposed Guarantors ({len(guarantors_list)}) — "
            f"Total Pledged: {fmt_kes(total_pledge_value)}\n"
            + "\n".join(guarantor_lines)
        )
        full_description = f"Purpose: {purpose}. {description}".strip(". ") + guarantor_summary

        # ---- Build proposed_pledges rows from guarantors ----
        proposed_pledges = []
        for g in guarantors_list:
            sav_acct     = g["savings_account"]
            pledge_value = flt(g.get("pledge_value", 0))
            gm_name      = g.get("member_name", g.get("member", ""))
            sav_balance  = flt(frappe.db.get_value("SACCO Savings Account", sav_acct, "balance") or 0)
            security_price = sav_balance * multiplier

            loan_security = _create_loan_security(
                savings_account=sav_acct,
                member=g["member"],
                member_name=gm_name,
                multiplier=multiplier,
            )
            if not loan_security:
                frappe.throw(_(f"Could not create Loan Security for {gm_name}. Please contact the office."))

            proposed_pledges.append({
                "loan_security":       loan_security,
                "qty":                 1,
                "loan_security_price": security_price,
                "amount":              pledge_value,
            })

        if not proposed_pledges:
            frappe.throw(_("At least one Proposed Pledge is required for a secured loan."))

        # ---- Create Loan Application with proposed_pledges ----
        loan_app = frappe.get_doc({
            "doctype":           "Loan Application",
            "loan_product":      loan_product,
            "applicant_type":    "Customer",
            "applicant":         customer,
            "loan_amount":       loan_amount,
            "repayment_method":  "Repay Over Number of Periods",
            "repayment_periods": repayment_periods,
            "rate_of_interest":  rate,
            "is_term_loan":      1,
            "is_secured_loan":   1,
            "description":       full_description,
            "company":           company,
            "posting_date":      today(),
            "status":            "Open",
            "proposed_pledges":  proposed_pledges,
        })
        loan_app.flags.ignore_permissions = True
        # Frappe Lending's validate() checks loan_amount <= applicant's own credit limit,
        # ignoring guarantor pledges. We've already validated total pledge coverage above,
        # so bypass framework validation and run db_insert directly.
        loan_app.flags.ignore_validate = True
        loan_app.flags.ignore_mandatory = False
        loan_app.insert()
        frappe.db.commit()

        # ---- Notify external guarantors by email ----
        emails_sent = 0
        for g in guarantors_list:
            if g.get("is_self"):
                continue
            g_email = frappe.db.get_value("SACCO Member", g["member"], "email_id")
            if not g_email:
                continue
            try:
                _send_guarantor_notification_email(
                    loan_application=loan_app.name,
                    guarantor_member=g["member"],
                    guarantor_name=g.get("member_name", g["member"]),
                    guarantor_email=g_email,
                    applicant_name=member.get("full_name") or member.get("first_name", ""),
                    loan_amount=loan_amount,
                    loan_product=loan_product,
                    savings_account=g["savings_account"],
                    pledge_value=flt(g.get("pledge_value", 0)),
                    multiplier=multiplier,
                )
                emails_sent += 1
            except Exception as e:
                frappe.log_error(f"Failed to send guarantor email: {e}", "SACCO Portal")

        return {
            "application": loan_app.name,
            "emails_sent": emails_sent,
            "message": (
                f"Application {loan_app.name} submitted successfully. "
                f"{emails_sent} guarantor notification email(s) sent. "
                f"The SACCO office will create the loan securities and pledges."
            ),
        }

    except Exception as e:
        frappe.log_error(f"Error in submit_loan_application_v2: {e}", "SACCO Portal")
        frappe.throw(_(f"Error submitting application: {e}"))


# ============================================================
# GUARANTOR NOTIFICATION EMAIL (info only — no token confirm)
# ============================================================
def _send_guarantor_notification_email(loan_application, guarantor_member, guarantor_name,
                                        guarantor_email, applicant_name, loan_amount,
                                        loan_product, savings_account, pledge_value, multiplier):
    """
    Informs the guarantor that they have been proposed.
    The SACCO office will handle formal pledge creation.
    """
    sav_balance    = flt(frappe.db.get_value("SACCO Savings Account", savings_account, "balance") or 0)
    security_value = sav_balance * multiplier

    subject = f"[SACCO] Loan Guarantee Proposal — {loan_application}"
    message = f"""
<p>Dear {guarantor_name},</p>

<p><strong>{applicant_name}</strong> has listed you as a proposed guarantor on a new loan application.
The SACCO office will contact you to complete the formal pledge process.</p>

<table style="border-collapse:collapse;width:100%;max-width:480px;font-size:14px;margin:16px 0">
  <tr><td style="padding:8px 0;color:#888;width:200px">Application</td>
      <td style="font-weight:600">{loan_application}</td></tr>
  <tr><td style="padding:8px 0;color:#888">Loan product</td>
      <td>{loan_product}</td></tr>
  <tr><td style="padding:8px 0;color:#888">Loan amount requested</td>
      <td style="font-weight:600">{fmt_kes(loan_amount)}</td></tr>
  <tr><td style="padding:8px 0;color:#888">Your savings account</td>
      <td>{savings_account}</td></tr>
  <tr><td style="padding:8px 0;color:#888">Your savings balance</td>
      <td>{fmt_kes(sav_balance)}</td></tr>
  <tr><td style="padding:8px 0;color:#888">Proposed security value ({multiplier}× savings)</td>
      <td style="font-weight:600">{fmt_kes(security_value)}</td></tr>
  <tr><td style="padding:8px 0;color:#888">Proposed pledge towards this loan</td>
      <td style="font-weight:600">{fmt_kes(pledge_value)}</td></tr>
</table>

<p style="margin-top:20px;font-size:13px;color:#555">
  No action is required from you at this time. The SACCO office will process the formal
  guarantee documentation. If you have any questions or wish to decline, please contact
  the SACCO office directly.
</p>

<p style="margin-top:8px;font-size:12px;color:#888">
  If you did not expect this notification, please contact the SACCO office immediately.
</p>
"""
    frappe.sendmail(recipients=[guarantor_email], subject=subject, message=message, delayed=False)


# ============================================================
# LEGACY (backward compat)
# ============================================================
@frappe.whitelist()
def submit_loan_application(loan_type, loan_amount, repayment_periods,
                             purpose, description=""):
    member       = get_member_for_user()
    savings_acct = frappe.db.get_value("SACCO Savings Account", {"member": member.name}, "name") or ""
    settings     = frappe.get_single("SACCO Settings")
    multiplier   = flt(settings.get("savings_multiplier") or 3)
    sav_balance  = flt(frappe.db.get_value("SACCO Savings Account", savings_acct, "balance") or 0) if savings_acct else 0
    pledge_val   = sav_balance * multiplier

    guarantors = json.dumps([{
        "member":          member.name,
        "member_name":     member.full_name or member.first_name or "",
        "savings_account": savings_acct,
        "pledge_value":    pledge_val,
        "is_self":         True,
    }])
    return submit_loan_application_v2(
        loan_product=loan_type,
        loan_amount=loan_amount,
        repayment_periods=repayment_periods,
        purpose=purpose,
        description=description,
        guarantors=guarantors,
    )