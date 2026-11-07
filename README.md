# Trident SACCO (`sacco_manager`)

SACCO membership, savings, shares, loans, check-off remittances, dividends and M-Pesa collections on
**ERPNext v16 + Frappe Lending**. Owned by Trident Savings and Credit Co-operative Society
(originally written by TSEA under the MIT licence; see `license.txt`).

The app keeps its module name `sacco_manager` so the live site (`tridentsacco.m.frappe.cloud`) upgrades in place.

---

## What it does

| Area | DocTypes / features |
|---|---|
| Members | SACCO Member (own Customer per member, KE phone/ID/KRA checks), Registration Fee, Share Ledger, member portal `/sacco-portal` |
| Savings | Savings Account, Savings Transaction (Deposit, Withdrawal, Interest, Dividend, Withholding Tax), Withdrawal Request (maker-checker, guarantee lock), Monthly Savings Run |
| Check-off | Branch Remittance: one receipt from the employer, then per member savings + loan repayment + shares, via an Employer Remittance Clearing account; late penalty capped |
| Loans | Frappe Lending. Portal loan applications with guarantors (signed, expiring email links), approval gate, Loan Top-Up as a refinance |
| Period-end | Interest Run (average daily balance, WHT), Dividend Run (AGM rate, WHT, credit to savings) |
| M-Pesa | Daraja C2B (paybill/till), STK Push from the portal, B2C payouts for withdrawals, auto-allocation by account reference, manual allocation queue |
| Reports | Member Statement, Savings Balances (reconciles to GL), Loan Portfolio at Risk (prudential classes + provisions), M-Pesa Reconciliation, Guarantor Exposure, SACCO Prudential Summary, Member Data Quality, Member Overview, Branch Remittance |

### Accounting (every posting is a balanced Journal Entry tagged with Party Type "SACCO Member")

| Event | Debit | Credit |
|---|---|---|
| Savings deposit (cash/bank/M-Pesa/check-off) | payment account | Members' Savings [member] |
| Withdrawal | Members' Savings [member] | payment account |
| Interest on savings | Interest on Deposits (expense) | Members' Savings [member] |
| WHT on interest/dividend | Members' Savings [member] | Withholding Tax Payable |
| Share purchase | payment account | Share Capital [member] |
| Registration fee | payment account | Registration Fee Income |
| Check-off receipt | bank | Employer Remittance Clearing |
| Check-off allocation | Employer Remittance Clearing | member savings / loan / shares |
| Late remittance penalty | Employer Remittance Clearing | Penalty Income |
| Dividend declaration | Dividend Appropriation (equity) | Dividends Payable [member] + WHT Payable |
| Top-up (refinance) | new loan disbursed to Top-Up Clearing; old loan closed from clearing; net cash from clearing to bank | |

SACCO Settings refuses wrong account types (e.g. an income account as remittance clearing).

---

## Roles (assign through **Role Profiles** of the same name)

| Role | Can |
|---|---|
| SACCO Administrator | Settings, M-Pesa setup, products, users; everything a Manager can |
| SACCO Manager | **Approves**: withdrawals, loan applications (status), interest/dividend runs, top-ups, remittances; cancels |
| SACCO Accountant | Posts savings, shares, remittances, repayments; prepares interest/dividend runs; allocates M-Pesa; disburses |
| SACCO Credit Officer | Members, loan applications, guarantors, top-up requests (cannot approve) |
| SACCO Teller | Captures deposits, withdrawal requests and repayments as drafts |
| SACCO Auditor | Read-only on SACCO, Lending and GL records and reports |
| Sacco Member | Portal only (no desk); sees only their own data |

**Maker-checker** (SACCO Settings → Enforce Maker-Checker): the person who creates a withdrawal, interest or
dividend run, top-up or remittance cannot submit it. Permissions on ERPNext/Lending doctypes, report access,
role profiles, custom fields and the SACCO Member party type are created by `sacco_manager/setup.py` on every
migrate. No hand configuration is needed.

---

## M-Pesa setup

1. Create a **Bank**-type ledger, e.g. `M-Pesa Paybill - TSCCL` (money held in the shortcode).
2. **SACCO M-Pesa Account**: shortcode, environment, Daraja consumer key/secret, STK passkey, B2C initiator +
   security credential, public site URL. Save, which generates a callback token.
3. Click **Test Daraja connection**, then **Register C2B URLs**.
4. Optional: list Safaricom's callback IPs in *Allowed Callback IPs*.

Members pay to the paybill with account number:
`<MemberNo>` savings · `<MemberNo>L` loan · `<LoanID>` that loan · `<MemberNo>S` shares · `<MemberNo>R` registration.
Unmatched payments stay **Unallocated** (workspace shortcut). An accountant allocates them with the **Allocate**
button. Use the **M-Pesa Reconciliation** report against the M-Pesa statement daily. Sweeps from M-Pesa to the
bank are ordinary Journal Entries.

Callback URLs live under `sacco_manager.mobile_money.callbacks.*` because Daraja rejects URLs that contain
"mpesa". Each URL carries a per-shortcode secret token; receipts are idempotent by M-Pesa receipt number.

---

## Upgrading the live site (go-live checklist)

1. **Back up** the site (Frappe Cloud → Backups → Take backup).
2. Point the Frappe Cloud bench at this repository and deploy. Patches then run:
   phones normalised, statuses remapped, bad settings mappings cleared, legacy Custom DocPerms removed,
   the conflicting "Loan Security" client script disabled.
3. **Reverse the wrong penalty charges** (749 demands, KES 1,043,269 at audit date). Dry run first:
   ```
   bench --site tridentsacco.m.frappe.cloud execute sacco_manager.maintenance.reverse_bogus_delinquency_demands
   bench --site tridentsacco.m.frappe.cloud execute sacco_manager.maintenance.reverse_bogus_delinquency_demands --kwargs "{'dry_run': False}"
   ```
4. SACCO Settings → **Create missing SACCO accounts**. Then review the mapping:
   - Share Capital should be a real share-capital account, not "Customer Deposits and Savings".
   - Interest on Deposits must be an expense account.
   - Employer Remittance Clearing must be an asset account.
5. Loan Product: set **Excess Amount Acceptance Limit** (e.g. 10) so Lending can close loans paid off to the cent.
   Set the **Penalty Interest Rate** and grace period if late-payment penalties are wanted (Lending charges them only on overdue instalments).
6. Assign Role Profiles to staff; disable former TSEA users.
7. Run **Member Data Quality** and fix the exceptions (at audit: 58 invalid mobiles, 61 without email, 29 without branch, 3 duplicate member numbers).
8. Configure M-Pesa (above) in Sandbox first, then Production.
9. `bench --site … execute sacco_manager.maintenance.savings_balance_check` must report no mismatches.

**Known data issue (not code):** the historical loan book was imported with repayments dated June–July 2026, so
Lending shows years of arrears on loans that were paid. PAR, NPA flags and "interest receivable vs overpayments"
stay wrong until the repayments are re-imported with their real value dates (or reposted). The new
**Loan Portfolio at Risk** report makes this visible.

---

## Testing

A disposable test bench restored from the production backup is built by `setup_test_bench.sh` (kept outside this repo, in the project's `05 App/dev/` folder, with `callback_test.sh` and `bench.sh`).

```
bench --site sacco.test migrate
bench --site sacco.test execute sacco_manager.tests.e2e.run      # 15 end-to-end checks, rolled back
```

`callback_test.sh` exercises the M-Pesa callback URLs over HTTP (token, idempotency, allocation).
Never run the e2e module on production.

## Licence

MIT. See `license.txt`.
