# Ingestion pipeline

## Principle

The ledger has **one write path**: the structured transactions API. Everything in this document sits *in front of* that path and produces structured calls into it. The ledger never knows whether a posting came from a human, an agent, or a recipe.

```
                 ┌───────────────────────── pre-processor (optional) ─────────────────────────┐
                 │                                                                            │
 raw input ──▶ raw_event ──▶ fingerprint ──▶ recipe match? ──yes──▶ run recipe ──▶ schema ok? ──yes──┐
                 │                               │                                   │              │
                 │                               no                                  no             │
                 │                               ▼                                   ▼              ▼
                 │                          review_item                         review_item    structured
                 │                     (offer /recipes/propose)             (offer re-propose)   payload
                 └────────────────────────────────────────────────────────────────────────────┘     │
                                                                                                    ▼
                                                            reconcile ──▶ POST /transactions ──▶ ledger
```

## 0. Three endpoints, one front door for mess

```
POST /transactions        structured write — the ONLY path into the ledger
POST /raw                 any text → raw_event → recipe → /transactions (or review queue). No LLM.
POST /recipes/propose     compile a recipe from a raw_event — the ONLY place an LLM runs
```

`/raw` is deliberately **input-type agnostic**. It does not know or care whether the text came from an email, an SMS, a Facebook post or someone typing. Anything that needs to unwrap a transport (MIME, IMAP, Telegram updates, CSV files) is a **connector** that lives outside the core and ends by calling `/raw` — see §8.

```
POST /raw
{
  "text":            "<required, length-capped (default 32 KB)>",
  "source":          "email:techcombank.com.vn",   // optional free-form hint, used by fingerprinting
  "received_at":     "2026-10-01T09:12:00+07:00",  // optional, default now()
  "idempotency_key": "<optional; default = content hash>"
}
→ 202 { "raw_event_id": "...", "status": "committed | duplicate | review | queued", "transaction_id": "..."? }
```

`status` lets the caller (a connector, an agent) decide whether to follow up with `/recipes/propose`.

## 1. Raw events

Every input is stored verbatim before anything looks at it.

- `text` is capped (per-plan limit; default 32 KB) to bound storage and interpreter cost. Connectors are expected to strip transport noise (MIME headers, quoted replies) before posting.
- `source` is an optional string, not an enum. Conventions: `email:<sender-domain>`, `sms:<short-code>`, `csv:<bank>`, `telegram`, `manual`, `agent:<name>`. Missing `source` is fine; fingerprinting just has fewer anchors.
- `content_hash` (per user) gives idempotency: re-forwarding the same email is a no-op.
- Structured calls to `/transactions` also create a raw_event (`source: api`) for audit but skip the pre-processor.
- Enqueued on Redis Stream `finassis:ingest` for the worker.

## 2. Fingerprinting

A fingerprint is a cheap, deterministic signature used to pick a recipe without running anything expensive. Computed from: the `source` hint (if any) and a normalised skeleton of the text (digits → `#`, whitespace collapsed, first N tokens). Recipes declare which fingerprint they own; matching is exact or by a small set of literal anchors (e.g. `"TCB"`, `"So du"`). Without a `source` hint the matcher tries anchors across all of the user's active recipes — still microseconds.

## 3. Recipes

A recipe is a small **deterministic extraction program** in a restricted DSL. It is compiled once (by an LLM, see [04-ai-layers.md](04-ai-layers.md)) and run forever as plain Python.

```yaml
recipe:
  id: tcb-debit-email-v3
  fingerprint:
    source_prefix: "email:techcombank.com.vn"    # optional; recipe still matches on anchors alone
    anchors: ["Thong bao giao dich", "So tien"]
  steps:
    - strip_html
    - regex: { name: amount_raw, pattern: "So tien:\\s*([\\-+]?[\\d\\.,]+)\\s*VND" }
    - regex: { name: date_raw,   pattern: "Thoi gian:\\s*(\\d{2}/\\d{2}/\\d{4} \\d{2}:\\d{2})" }
    - regex: { name: desc,       pattern: "Noi dung:\\s*(.+?)\\n" }
    - regex: { name: acct_tail,  pattern: "TK\\s*\\*+(\\d{4})" }
    - parse_amount: { from: amount_raw, to: amount, locale: vi_VN, currency: VND }
    - parse_date:   { from: date_raw, to: occurred_at, format: "%d/%m/%Y %H:%M", tz: Asia/Ho_Chi_Minh }
    - lookup:       { from: acct_tail, to: account_id, table: user.accounts_by_tail }
    - const:        { to: currency, value: VND }
    - sign:         { from: amount, direction: debit }        # debit → negative on asset account
  emit: transaction.v1
  tests:
    - fixture: raw_events/9f3c...      # stored sample
      expect: { amount: -250000, currency: VND, account_tail: "1234" }
```

**DSL operations (closed set):** `strip_html`, `regex` (single capture, RE2-compatible, timeboxed), `split`, `trim`, `lower/upper`, `parse_amount` (locale-aware; VND `250.000`, USD `1,250.50`), `parse_quantity` (number + unit, incl. mixed forms like `2 lượng 3 chỉ` → `{value: 23, unit: chi}` via smallest-unit normalisation; unit words resolved through a locale alias table), `parse_date`, `const`, `lookup` (against user tables: accounts by tail/alias, merchant aliases, tag by keyword, units by alias), `map` (literal value → value), `sign`, `default`, `require`. No loops, no arbitrary code, no network. Each step has a bounded cost; the whole recipe is timeboxed (e.g. 50 ms).

**Interpreter:** pure Python, no eval. Steps operate on a flat dict of named fields. Output is validated against a Pydantic schema (`transaction.v1`) before it leaves the interpreter. Validation failure → `review_item` with the partial field dict and the failing step for diagnostics.

**Versioning:** recipes are immutable once published; edits create a new version. Each raw event records which recipe version processed it, so re-processing is reproducible.

**Fixtures:** every recipe stores at least one raw sample and expected output. CI for recipes = run all fixtures. When a bank changes a template, the fixture shows exactly what broke.

**Sharing:** a recipe contains regexes, formats and literal anchors — no PII. Users can opt in to publish a recipe to a community library keyed by bank + source prefix; others can import and pin a version.

## 4. Structured fast path

`POST /transactions` and MCP `record_transaction` with a complete payload skip steps 2–3 entirely: raw_event stored (for audit), then straight to reconcile → commit. This is the path a user's own AI uses when it has already refined the data itself.

Semi-structured MCP input (agent omits currency or account) is completed by **defaults and lookups only** (user default currency, account by alias). If required fields are still missing, the call fails with a clear error — the agent, not Finassis, is expected to fix it.

## 5. Reconcile

Before committing, look for an existing posting that is probably the same money movement: same account & currency, equal `|amount|`, `occurred_at` within ±3 days, and description similarity above a threshold (or both empty). Also match `projected_income` (turns a projection into an actual).

Outcomes: **new** → commit · **duplicate** → link raw_event to existing transaction · **enrichment** → fill missing tag/merchant on the existing posting · **ambiguous** → review_item.

Typical VN case: the same purchase appears as a bank SMS *and* a bank email; reconciliation must collapse them.

## 5b. Tagging

Runs after reconcile, before commit, for any posting without a caller-supplied tag. Cheapest first, stop at the first confident hit; full design in [product/03-tags.md](../product/03-tags.md).

```
rules → fingerprint cache (Redis) → merchant memory (user → global) → keyword dict → kNN (pgvector) → untagged
```

Each step is bounded (rules and dict are in-memory per user; cache is one Redis GET; kNN is one indexed query on ≤ a few thousand vectors). Target < 10 ms total. Results below the user's confidence threshold are committed as `untagged` with a `tag_proposals` row so the console/agent can confirm. The LLM never runs here; a nightly job batches the day's untagged rows per user into one call if the plan allows.

Confirmations (console, `set_tag` via MCP, caller-supplied tags) write back to: fingerprint cache, `merchant_memory_user`, `tag_examples` (with embedding), and — after N distinct users agree — `merchant_memory_global`.

## 6. Commit

One DB transaction: insert `transactions` + `postings`, update `holdings`/`lots` if an instrument is involved, mark `raw_event` committed, insert `dirty_periods`. Publish `posting.committed` to `finassis:events`.

## 7. Review queue

`review_items` hold: the raw_event, the recipe (if any) and version, the partial output, and the reason (`no_recipe`, `schema_failed:<field>`, `ambiguous_match`). Exposed via REST and MCP. Resolutions: accept-as-is (with edits), reject, or **propose recipe** — which hands the sample to the compiler in [04-ai-layers.md](04-ai-layers.md). Resolving by editing a field on a recipe-produced item suggests a recipe fix (new version) rather than a one-off correction.

## Failure handling

- Worker failure → message stays pending in the stream; retry with backoff; after N attempts `raw_event.processing_status = failed` + alert.
- Recipe timeout / regex catastrophe → step aborted, review_item, recipe flagged.
- Re-processing: `POST /raw-events/{id}/reprocess?recipe_version=...` produces a new candidate; if the event was already committed, accepting the new candidate reverses the old transaction.

## Performance notes

- Fingerprint + recipe run target: < 5 ms typical, 50 ms hard cap. Thousands of events/minute on one worker is fine.
- LLM is invoked only by `/recipes/propose` (user-triggered, rate-limited per user) — never by `/raw`.
- Redis Stream consumer groups let workers scale horizontally later; a single worker is expected for a long time.

## 8. Connectors (outside the core)

A connector is anything that obtains text from somewhere and calls `/raw`. It handles **transport and unwrapping only** — never interpretation. Connectors are not part of the core service and should be separate small processes, or simply n8n / Zapier templates, each authenticated with a scoped API key (`raw:write`, optionally `recipes:propose`).

| Connector | Does | Then |
|-----------|------|------|
| Email forwarder | Per-user forwarding address or IMAP poll; picks the text/plain or stripped HTML body; drops signatures/quoted replies | `POST /raw {text, source:"email:<sender-domain>"}` |
| SMS relay | Android app / Tasker / iOS shortcut forwards bank SMS | `POST /raw {text, source:"sms:<sender>"}` |
| Telegram | *not a connector* — a channel inside the API (see [07](07-interactions-and-channels.md)); it calls the same raw-ingest domain service with `source:"telegram"` |
| Zalo OA bot (later) | Message → text | `POST /raw {text, source:"zalo"}` or a channel like Telegram |
| CSV / statement upload | Splits file into rows | one `POST /raw` per row, `source:"csv:<bank>"` (batch endpoint later if needed) |
| Browser extension | Selected text on a page | `POST /raw {text, source:"web:<domain>"}` |

Rules of thumb: if a connector is nothing more than "HTTP in, `/raw` out," it should be an n8n template, not code we maintain. Connectors may auto-call `/recipes/propose` when `/raw` returns `status: review` with `reason: no_recipe`, subject to the user's plan allowance. Connectors never write to `/transactions` directly unless they already hold fully structured data (e.g. a broker API), in which case they are just API clients.
