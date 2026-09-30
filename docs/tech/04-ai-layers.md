# AI layers

Two distinct roles. Neither is on the ledger's critical path.

| Role | Who runs the model | When | Hot path? |
|------|--------------------|------|-----------|
| **A. Recipe compiler** | Finassis (our key, our cost) | Once per new input shape, user-triggered | No |
| **A2. Tag batch** | Finassis (free tier or our key), allowance-gated | Nightly, one call per user over that day's untagged rows; outputs are proposals | No |
| **B. Consumer** | The user's own AI (their key, their tools) | Whenever they ask a question | Reads our tools; never writes unstructured data to the ledger |

Optional **C. Narration** (personality voice) sits on top of B's outputs for our own summaries and alerts.

---

## A. Recipe compiler

Turns one messy sample into a deterministic recipe (see DSL in [03-ingestion-pipeline.md](03-ingestion-pipeline.md)). The LLM writes the parser; the machine runs it.

### Flow

```
sample (raw_event) ──▶ prompt: schema + DSL spec + user context ──▶ LLM ──▶ recipe draft
        │                                                                     │
        │                                                        interpret on sample
        │                                                                     │
        │                                                     schema valid & user-expected values match?
        │                                                        yes │              │ no (≤ 3 retries with error fed back)
        ▼                                                            ▼              ▼
   stored as fixture                                          recipe saved     fail → user edits or gives another sample
```

### Inputs to the compiler

- The raw sample (length-capped, HTML stripped to text + minimal structure).
- The target schema (`transaction.v1`), the DSL operation list with argument types, and 2–3 example recipes.
- User context needed for lookups: account aliases/tails, default currency, timezone, tag list. Nothing from other users.
- Optional user hints: "this is a debit", "amount is 250,000 VND", or expected values to assert on.

### Outputs

- A recipe (YAML/JSON) conforming to the DSL schema.
- A fingerprint proposal.
- A test expectation for the sample.

### Guardrails

- The LLM emits **data, not code**: a recipe is validated against the DSL JSON schema before the interpreter touches it. Unknown ops, unbounded regexes, or missing `require`d fields are rejected.
- Regexes are compiled with an RE2-compatible engine (`google-re2`) to avoid catastrophic backtracking; step and recipe timeboxes apply.
- The recipe must pass its own fixture. A recipe that "works" only by `const`-ing the amount is rejected by a heuristic (every required numeric field must derive from a capture).
- Compiler calls are rate-limited per user and logged with model, prompt version, tokens, and outcome for cost attribution.
- Prompt versions are stored; a recipe records which compiler version produced it.

### Why this instead of LLM-per-message

Cost is paid once per template instead of per transaction; latency is milliseconds; behaviour is deterministic and auditable; failures are visible (schema test) rather than silent (a plausible but wrong extraction). It also matches the VN reality: a handful of bank templates cover almost all notifications.

### Future compiler targets (same pattern)

- CSV/XLSX column mapping for broker or bank exports.
- Receipt OCR text → line items.
- Repairing a recipe when a template changes: compiler receives old recipe + failing sample + error.

---

## B. Data designed for agent consumption

The user's AI (Claude Desktop with our MCP, an n8n AI node, a custom agent) is the primary consumer. Our job is to make its answers *correct*, which means our tool outputs must be self-describing.

### Every numeric answer carries

| Field | Meaning |
|-------|---------|
| `amount`, `currency`, `decimals`, `display` | Integer minor units + currency + its decimals, and a formatted convenience string (ADR-017). Agents do arithmetic on `amount` |
| `as_of` | Point in time the value is valid for |
| `staleness` | `now − as_of` for mark-to-market items; `fresh` for ledger-derived values |
| `source` | `ledger`, `valuation:manual`, `valuation:feed:<name>`, `projection`, `snapshot` |
| `confidence` | `exact` for ledger, or a coarse band for estimates/projections |
| `fx` | When converted: `{from, to, rate, rate_as_of}` |
| `quantity` | For holdings: `{value, unit, decimals, name?}`; one entry per unit held, never auto-converted |
| `price_used` | For holdings: `{price, currency, per_unit, as_of, source}` |
| `annotations` | Attached notes relevant to this item (see below) |
| `assumptions` | For projections: the streams, recurring items and cut-off rules used |

### Annotations

Free-text notes with `author` (`user` or `agent:<name>`), `created_at`, optional `valid_until`, attached to an account, valuation, instrument, tag, period, or the profile. They are how a previous analysis informs the next one ("Q3 valuation based on neighbour's sale at 4.2 tỷ", "plan: clear MB loan by June"). Agents can read and write them via `list_annotations` / `add_annotation`.

### MCP tools (mirrored as REST)

Read
- `get_context_summary(currency?)` — compact whole-picture snapshot: accounts with balances and staleness, liabilities, income streams, last 3 months cash flow, open alerts, recent annotations. Sized (≤ ~2k tokens) for a system prompt.
- `get_net_worth(at?, currency?, breakdown_by?)`
- `get_balance(account, at?, currency?)`
- `query_spend(period, group_by, filters?, currency?)`, `query_income(...)`
- `list_transactions(filters, limit, cursor)`
- `list_assets(include_stale?)` — mark-to-market items with valuations, staleness, notes
- `project_cashflow(to_date, currency?, scenario?)` — liquid balance path to a date, with assumptions
- `get_budget_status(period?)`, `list_alerts(since?)`, `list_review_items()`, `list_annotations(target)`
- `list_tags()`, `suggest_tag(text)`, `list_untagged(limit)` — lets the user's agent do tagging on its own token budget

Write
- `record_transaction(structured)` — the only ledger write; strict schema
- `add_valuation(account|instrument, value, currency, as_of, note?)`
- `add_annotation(target, text, valid_until?)`
- `resolve_review_item(id, action, edits?)`
- `set_tag(posting_id, tag, pin?)`, `create_tag(name, root)` — confirmations that feed tagging memory
- `propose_recipe(raw_event_id, hints?)` — triggers compiler A
- `set_budget(...)`, `upsert_income_stream(...)`

Agents cannot bypass the review queue or push unstructured text into the ledger; `record_transaction` rejects incomplete payloads with a precise error so the agent can fix and retry.

### Events out

Webhooks (HMAC-signed) and SSE for `posting.committed`, `review.needed`, `alert.raised`, `projection.missed`, `snapshot.closed`, `recipe.failed`. This is how an n8n flow reacts to new data.

---

## C. Narration (optional)

Our own summaries, alerts and `/ask` answers can be rendered in a per-user `personality_profile` voice. Implemented as a final prompt over tool outputs. Never applied to tool data — an agent calling `query_spend` gets plain JSON.

---

## Privacy & cost

- No cross-tenant data in any prompt.
- Recipes are PII-free by construction (patterns, not values); fixtures are per-user and never shared without explicit consent and redaction.
- Compiler cost attributed per user; hard monthly cap configurable.
- All model calls logged with prompt version, tokens, latency, outcome.
