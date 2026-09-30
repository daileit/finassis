# Tag taxonomy

The system tag tree lives in **[`seeds/tags.json`](../../seeds/tags.json)** — that file is the source of truth for the seed loader, the keyword dictionary, the tag-name embedding index and the `tag.*` i18n entries. This page holds the rules and the reasoning; it does not duplicate the data. Structure and the tagging cascade are in [03-tags.md](03-tags.md).

## Shape (as of seed version 3)

- **18 roots**: 13 expense (incl. `misc` and `untagged`, which have no children), 4 income, 1 `off_report`.
- **110 children**, 82 active by default (`on`), 26 available (`off`), 2 system-only (`sys`: `fx_loss`, `fx_gain`).
- Children marked `"region": "VN"` are Vietnam-specific (`lucky_money_given`, `ewallet_topup`, `parents_support` …) but harmless elsewhere.
- Each child carries en/vi `names`, a `default` state, and `keywords` (lower-case, Vietnamese without tone marks) that seed the dictionary and the suggestion index.

## Rules

- Roots are fixed. Users hide or rename them for display; they cannot add, remove or re-kind them.
- Tagging at **root level** is allowed and means "this root, unsure which child." There are therefore no `*_other` children.
- `misc` = understood but fits nowhere. `untagged` = not known yet. They are different states.
- Keys are never renamed once shipped (rules, recipes, memory and community content reference them). Deprecate and add.
- The `off_report` root is the exclusion mechanism: its children (transfer purposes such as savings deposit, investment buy/sell, loan principal, e-wallet top-up, lending/borrowing, plus `reimbursable` and `ignore`, plus any custom child) never count as spend or income. Savings rate and investment inflow read these children explicitly. There is no separate exclusion flag.
- `mortgage` exists as a convenience expense tag for users who don't model the loan as a liability; the proper form is `off_report.loan_principal` + `financial.loan_interest`.
- `investment_income.capital_gains` is generated automatically from `off_report.investment_sell` against cost basis; users don't tag it by hand.
- `lending_*` / `borrowing_*` need a receivable/payable account; Finassis auto-creates "People I lent to" / "People I owe" on first use.

## Default-active set

80 active is above the "about half" target. Before shipping the seed, trim toward ~60 with real data. Candidates to flip to `off`: `home_services`, `dental_optical`, `personal_care`, `hobbies`, `memberships`, `credit_card_interest`, `investment_fees`, `overtime`, `tips`, `bond_coupons`, `staking_yield`, `cash_deposit`, `savings_withdrawal`, `borrowing_repaid`.

## Earnings and assets

Earnings use the same tree (`kind = income`).

**Assets are not tagged** with this tree. An asset is a thing, not an event; it is classified by dimensions that already exist:

| Dimension | Where | Values |
|---|---|---|
| `type` / `asset_class` | accounts / instruments | cash, bank, term_deposit, credit_card, loan, equity, etf, bond, fund, crypto, precious_metal, real_estate, vehicle, pension, private_equity, receivable, payable, collectible, other |
| `liquidity` | accounts | liquid · semi · illiquid |
| `purpose` | accounts (optional) | emergency_fund · retirement · education · house · vehicle · travel · general · business |
| `labels` | accounts / instruments | free-form, uncontrolled (`#inherited`, `#joint-with-mai`) |

`purpose` answers "what is this money for" without borrowing the expense tree. The *events* around assets — buy, sell, deposit, principal, dividends — are postings and carry `off_report.*` / `investment_income.*` tags, which is how "money into investments this month" and "savings rate" are computed.

## Changing the taxonomy

Edit `seeds/tags.json`, bump `version`, run `make seeds-check` (schema + duplicate keys). Names flow to `i18n/` via `make i18n`. The loader upserts on startup and never touches users' custom tags. See [`seeds/README.md`](../../seeds/README.md).
