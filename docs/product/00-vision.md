# Vision

Finassis is the **financial memory an AI agent reads from**. It is a ledger-first backend that holds a person's expenses, income and wealth as clean, provenance-tagged data, and exposes that data as tools (REST + MCP) so the user's own AI — Claude, an n8n flow, a custom bot — can answer real questions: *what is my net worth right now, how much cash will I have at Tết, if I sell the house and buy a villa to rent out can I retire?*

The user's AI does the thinking. Finassis makes sure it thinks with good numbers.

## Market: Vietnam first

The first target user is Vietnamese. That shapes the product more than any technical choice:

- **USD and VND** are the first two currencies. VND has no minor unit and everyday amounts run to hundreds of millions; decimal handling is per-currency, never assumed.
- **No Plaid.** Bank data arrives as SMS and email notifications, each bank with its own template (Techcombank, Vietcombank, MB, TPBank, VIB, ...). Ingesting that mess cheaply and deterministically is the core ingestion problem.
- **Wealth is heterogeneous.** Gold (SJC bars, rings, priced per *lượng/chỉ*), land and houses (often without formal valuation), USD cash savings, bank term deposits, and increasingly stocks and crypto. The asset model must treat "a thing with a value and a staleness" as first-class, not an afterthought.
- Personal finance culture leans toward cash flow and savings goals; budgets per tag matter less than "how much can I safely put aside this month."

Nothing here is Vietnam-*only*; it is Vietnam-*first*, and the abstractions (currency-aware amounts, template-driven ingestion, generic assets) carry to any market.

## What "autonomous" means here

1. **It accepts messy input once, then handles it mechanically forever.** AI is used to *compile* a parser for a new input shape (a bank's email template, a broker's CSV); the compiled recipe runs as plain code on every subsequent message. No LLM in the hot path.
2. **It keeps itself consistent.** One append-only ledger is truth; balances, rollups and net-worth snapshots derive from it and rebuild automatically when history is corrected.
3. **It knows what it doesn't know.** Every number carries `as_of`, staleness, source and confidence. An agent can see that the house valuation is 14 months old before it reasons about it.
4. **It remembers what was said about the data.** Annotations — from the user or from a previous AI session — travel with accounts, assets and periods, so the next conversation starts with context instead of from zero.

## Principles

- **Ledger first.** Expense, income and wealth are *views* over one journal of postings.
- **The core accepts only structured data.** The ledger has one write path: the structured API. Everything that turns mess into structure is a pre-processor in front of it, optional and replaceable.
- **Native currency in, user currency out.** Amounts stored in the currency they occurred in; conversion is a read-time concern.
- **Raw input is never thrown away.** What arrived is stored immutably; recipes can be re-run when they improve.
- **Corrections are appends.** Fixes are reversing postings, never edits.
- **Multi-user from day one, single instance from day one.** Every row is tenant-scoped; the whole thing runs on one Postgres and one Redis and must stay efficient there.
- **Designed for agent consumption.** Tool outputs are compact, typed, and self-describing (units, rates used, staleness, provenance). Personality and narration are optional layers on top; the data path is plain.

## Non-goals (for now)

- Being a bank aggregator itself.
- Doing the financial analysis. Finassis provides data; the user's AI (with its other tools and context) does the reasoning.
- Tax computation.
- Trading / order execution.
- A first-party UI. Clients are welcome; the backend is the product.
