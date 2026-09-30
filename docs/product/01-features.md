# Features

Three tracking capabilities over one ledger, one ingestion feature in front of it, and one consumption surface on top. See [tech/01-data-model.md](../tech/01-data-model.md) for storage.

## 1. Expense tracking

- Record a spend via the structured API (the only path to the ledger), or via an ingestion recipe that turns a bank SMS/email/CSV into a structured call.
- **Tagging** into a controlled tree: fixed roots (ăn uống, nhà ở, đi lại, ...), system children we ship, and custom children the user adds. A tag is set by the caller, a recipe, a user rule, merchant memory, the user's own confirmed history (vector fingerprint), or — last and cheapest per row — a nightly LLM batch whose proposals the user confirms. Untagged is a valid state. See [03-tags.md](03-tags.md).
- Splits, refunds linked to the original spend.
- Nothing you don't want counted pollutes spend or income: the **Not counted** tag group holds self-transfers, savings deposits, investment buys, loan principal, card payments, reimbursables and test entries. A matching −X/+X pair between two of your accounts is detected and tagged automatically; anything else you pick from suggestions or add your own child under that group.
- Budgets per tag per period, with pace ("70% of Dining, 10 days left").
- Recurring-expense detection from statistics (same merchant, similar amount, regular interval).
- Merchant normalisation (`GRAB*A1B2C3` → Grab) via recipe or rule.

## 2. Earning tracking

- **Earnings are what you expect and what your assets pay.** The Earnings view is built from **IncomeStreams**: ones you declare (salary on the 25th, rent monthly, freelance retainer) and ones Finassis derives from your assets (a term deposit's rate and maturity, a bond coupon, a fund distribution, dividends per share). Each shows expected vs. received; missed or short income raises an alert. This powers "cash at Tết" style questions.
- Any other money in — a refund, lì xì, a friend paying you back — is still recorded as a transaction with an income tag and counts in cash-flow income as *ad-hoc*, but it doesn't clutter Earnings.
- Held assets don't produce income by going up in value; that shows in Wealth as unrealised gain. Income is what arrives: interest, dividends, rent, and realised gains when something is sold.

## 3. Wealth tracking

Anything with a value: cash & bank, term deposits, credit cards and loans (liabilities), brokerage holdings, crypto, **gold** (by weight: lượng, chỉ, phân, gram, troy ounce; priced by SJC/PNJ buy price), real estate (by m², sào, ha), vehicles, livestock, collectibles, pensions, private investments.

**Units, not just currencies.** Every quantity is a number plus a unit, and units belong to a measure (money, mass, count, area). Currencies are simply the units of *money*. Users pick the unit per account, stream or asset and can change it any time for new entries; a global unit list is seeded (including regional sào) and users can add their own inside a measure ("Tiger 24-pack" = 24 units). Unit names are shown in the user's language (`lượng` / `tael`) while the stored code stays neutral. Mixed input like `2 lượng 3 chỉ` is accepted and stored exactly (as 23 chỉ); showing it back as "2 lượng 3 chỉ" is a display preference.

**Aggregate per unit, convert on request.** If you hold 3 troy ounces of gold and later add 3 chỉ, Wealth shows `3 oz` and `3 chỉ` as two lines, each valued at its own price per unit; the money values are summed into net worth. We never silently convert what you typed. Converting the history to one unit, or a combined single-unit snapshot, is an explicit action and a candidate paid feature.

- **Ledger-valued** accounts (balance = sum of postings) vs **mark-to-market** accounts (balance = latest Valuation) vs **priced holdings** (quantity × latest price per unit).
- **Valuation** and **price** records carry `as_of`, `source` (manual, feed, imported), and a free-text note. The API returns **staleness** alongside every value so an agent knows a 14-month-old house price is 14 months old.
- Holdings with lots (quantity in unit, cost basis) for brokerage/crypto/gold: unrealised and realised gains, expected yield → feeds Earning.
- Net worth as a time series (from snapshots), broken down by class, liquidity, native currency; converted to the user's default currency with the rate used stated in the response.

## 4. Ingestion recipes (AI compiles, machine runs)

The feature that makes VN bank notifications usable.

1. User (or their agent) posts a raw sample — a bank email body, an SMS, the first rows of a CSV — to `/recipes/propose`.
2. Finassis asks an LLM **once** to produce a **recipe**: a fingerprint that recognises this input shape, plus a small deterministic extraction program in a restricted DSL (strip HTML, regex capture, parse date with format, parse amount with locale, constant label, lookup table).
3. The recipe is tested against the sample and the target schema. If it passes, it is stored with the sample as a fixture. The user can inspect/edit it.
4. Every subsequent input to `/raw` (any text, from any connector — email, SMS relay, Telegram, a pasted Facebook post) is fingerprinted; a matching recipe runs in pure Python in milliseconds and emits a structured transaction. No LLM.
5. If a recipe fails schema validation on a new input (bank changed its template), the item goes to a **review queue** and re-proposal is offered.

Recipes contain no personal data, so bank-template recipes can be shared as a community library (opt-in, versioned).

## 5. Agent-ready data (the consumption surface)

Everything above is exposed via REST and MCP with the same semantics. Design targets, phrased as questions the user's AI must be able to answer from tool output alone:

| Question | What Finassis must return |
|----------|---------------------------|
| What's my net worth? | Total in default currency, breakdown, per-item `as_of` and staleness, FX rates used |
| How much cash do I have at Christmas? | Current liquid balance + projected income − recurring/committed expenses to that date, with assumptions listed |
| Can I afford a car loan of X? | Free cash flow by month (last 12), existing liabilities, savings rate |
| If I sell the houses and buy a rental villa, can I retire? | Asset list with values, staleness and notes; income streams; expense baseline; annotations from earlier analyses |

Supporting features:

- **Annotations**: free-text notes attached to an account, asset, tag, period or the whole profile, authored by the user or an AI session ("valued by neighbour sale Mar 2026", "plan: pay off MB loan by Q2"). Returned with the data they describe.
- **Context summary tool**: one call returning a compact whole-picture snapshot (accounts, balances, top streams, staleness flags, recent annotations) sized to fit an agent's system prompt.
- **Projection tool**: cash-flow projection to a date from streams, recurring expenses and budgets.
- Optional **narration** in a configurable personality voice for summaries and alerts. Never applied to tool data.

## 6. Talking to the user: interactions and the Telegram bot

Whenever Finassis has a question — confirm this tag, resolve this review item, this house value is 14 months old, this key reveal needs a second tap — it creates an **interaction** with a few options. Any channel can show and answer it: Telegram, the user's AI via MCP, the console later. Answer it anywhere, it closes everywhere.

The **Telegram bot** is the first channel and, for a long time, the only UI: `/start` registers you; forward a bank SMS and get back a transaction card with its tag and `Change · Split · Undo` buttons; `/balance`, `/spend`, `/networth`, `/assets`; `/untagged` and `/review` as tap-to-resolve lists; `/keys`, `/lang`, `/currency`, `/plan` for account ops. It runs inside the API service and speaks in your language and, for summaries, your chosen voice.

## 7. Plans, allowance and console

- Every costly action (API calls, raw items, recipe runs, recipe compiles, storage) is metered. Each user has a plan with per-kind limits and an **allowance** (limit + top-ups − usage); the assistant may call it *mana*. Reads of your own ledger are never blocked.
- Donations or paid plans add top-ups; the mechanism exists from day one even if the first plan is free.
- A **console** (Next.js app under `console/`, built later) covers stats dashboards, ledger and wealth browsing, recipes and review queue, API keys, webhooks/connectors, MCP setup, usage & allowance, and settings. Operators get tenant, pipeline and spend views in the same app.

## 8. Multi-currency

- User default currency: USD unless changed; VND is the other day-one currency. Any ISO 4217 code plus a controlled crypto/commodity list is accepted.
- Each account and stream has its own currency, changeable per object; history is never rewritten.
- Per-unit decimal precision (VND 0, USD 2, BTC 8, gold grams 3) applied to parsing, display and rounding.
- Daily FX rates; historical reports use the posting-date rate, current values use the latest, and every converted figure states the rate used.
