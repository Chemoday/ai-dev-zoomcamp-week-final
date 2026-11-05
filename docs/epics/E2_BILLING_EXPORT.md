# E2. Billing and accounting export

- **Phase:** 2. **Status:** idea, decisions below. Detailed design before the first task.

## Decision: we calculate charges, accounting software handles money

A full accounting system (ledger, VAT, annual report XBRL, tax forms, bank integrations) is years of
work and not what this project is for. We do not build one. The line is:

| We do (module `billing`, country-agnostic) | External accounting does |
|---|---|
| Tariffs per resource and service | Payments and bank statements |
| Cost allocation, including water loss = master meter - sum of apartment meters | Balances, debts, late fees |
| Charges per apartment and period (`ChargeLine`: apartment, item, quantity, unit price, amount, all `Decimal`) | Ledger entries, reports, VAT, annual report |
| Export of charges through country plugins | Sending invoices to banks if it is not done by our plugin |

## Plugins

Same pattern as `ReadingExporter` in `src/plugins/registry.py`: a `ChargeExporter` protocol that
receives plain DTOs. The only personal data it gets is what an invoice legally needs (payer name,
reference number), and only inside the export file.

- `common.csv_export`: a generic CSV of charges.
- `countries.ee.e_arve_xml`: Estonian e-invoice (EN 16931) for banks and operators
  (Finbite/Omniva).
- `countries.ee.merit`: import file or API for Merit Aktiva; SmartAccounts and Directo later.

## Open questions

- Who maintains tariffs: the accountant in our UI, or imported from the accounting software?
- Do we need payment status back (for debt reminders, docs/BACKLOG.md B-6)? If yes: bank statement
  import (camt.053) or the accounting software API.
