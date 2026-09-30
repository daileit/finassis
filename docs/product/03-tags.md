# Tags

A **tag** is the controlled label that puts a transaction into a budget and a report. Every posting has exactly one primary tag. Users may also attach free-form **labels** (`#trip-danang`) that are never controlled and never aggregated by the system.

## Structure

- **Root tags** are fixed by Finassis. Each carries a `kind`: `expense`, `income` or `off_report`. Users cannot add, remove or re-kind roots; they can hide or rename them for display.
- **Child tags** are either **system** (shipped by us, extensible via seed migrations) or **custom** (created by the user under a system root). Customs exist only at the child level; this keeps every rule simple: every tag has a root, every root has a kind.
- The list a user sees is the **combined** list: system children ∪ their customs, minus hidden ones, with their renames applied.
- Every system tag has an English `system_key`; recipes, rules, community content and reports reference the key, never the display name.
- Custom tags are gated by plan allowance (`tag.custom` kind).

## Default taxonomy

The complete tree — every system key with en/vi names, default on/off state and keyword seeds — is machine-readable in [`seeds/tags.json`](../../seeds/tags.json) (18 roots, 108 children, 80 active by default with a note to trim toward ~60); rules and reasoning in [04-tag-taxonomy.md](04-tag-taxonomy.md). Summary below for orientation only.

Tagging at **root level** is allowed and means "this root, unsure which child," so there are no `*_other` children. `misc` is for "understood but fits nowhere"; `untagged` is "not known yet."

### Expense roots

| Root (`system_key`) | Children |
|---|---|
| `food` — Ăn uống | `groceries` chợ/siêu thị · `dining` ăn ngoài · `coffee_drinks` cà phê/trà sữa · `delivery` giao đồ ăn · `snacks` ăn vặt |
| `housing` — Nhà ở | `rent` · `mortgage` · `utilities` điện nước · `internet_tv` · `maintenance` sửa chữa · `furnishing` nội thất · `building_fees` phí quản lý |
| `transport` — Đi lại | `fuel` xăng · `ride_hailing` Grab/Be/Xanh · `public_transit` · `parking_tolls` · `vehicle_maintenance` · `vehicle_fees` đăng kiểm/bảo hiểm xe · `ev_charging` |
| `shopping` — Mua sắm | `clothing` · `electronics` · `household` đồ gia dụng · `online_marketplace` Shopee/Lazada/Tiki · `beauty` |
| `health` — Sức khỏe | `medical` khám chữa · `pharmacy` thuốc · `health_insurance` · `fitness` |
| `family` — Gia đình | `children` · `kids_education` học phí · `parents_support` biếu cha mẹ · `pets` |
| `personal` — Cá nhân | `self_education` · `hobbies` · `subscriptions` Netflix/Spotify/iCloud · `gifts_given` · `charity` |
| `social` — Xã hội | `entertainment` · `travel` du lịch · `weddings_funerals` đám cưới/đám giỗ · `lucky_money_given` lì xì |
| `financial` — Tài chính | `bank_fees` · `loan_interest` · `life_insurance` · `taxes` · `fx_loss` |
| `work` — Công việc | `work_expenses` · `tools_software` · `freelance_costs` |
| `untagged` — Chưa gắn | — (a first-class state, never an error) |

### Income roots

| Root | Children |
|---|---|
| `salary` — Lương | `salary_main` · `bonus` thưởng/lương tháng 13 · `allowances` phụ cấp |
| `business_income` — Kinh doanh | `freelance` · `sales` · `commission` |
| `investment_income` — Đầu tư | `dividends` · `interest` lãi tiết kiệm · `rental` cho thuê · `capital_gains` · `staking_yield` |
| `other_income` — Khác | `gifts_received` · `lucky_money_received` · `refunds` hoàn tiền · `debt_repaid` thu nợ · `government` |

### Off-report root (excluded from spend and income)

| Root | Children |
|---|---|
| `off_report` — Không tính vào báo cáo | `self_transfer` · `cash_withdrawal` · `ewallet_topup` · `savings_deposit` · `investment_buy` / `investment_sell` · `loan_principal` · `credit_card_payment` · `lending_out` / `lending_repaid` · `borrowing_in` / `borrowing_repaid` · `reimbursable` · `ignore` · … |

Exclusion is a *tag*, not a hidden flag: anything under `off_report` (system or custom) never counts as spend or income. Transfers are tagged by *purpose* because savings rate and investment inflow — the numbers VN users care about most — read these children explicitly. A matched −X/+X pair between two of your accounts is auto-tagged `self_transfer` (or `credit_card_payment` when one side is a card).

### Earnings and assets

Earnings use the same tree (`kind = income`). **Assets are not tagged** with this tree; they are classified by `asset_class`, `liquidity` and a small `purpose` enum (emergency fund, retirement, education, house, …) plus free labels. The *events* around assets (buy, sell, deposit, principal, dividends) are postings and carry `off_report.*` / `investment_income.*` tags. See the end of [04-tag-taxonomy.md](04-tag-taxonomy.md#assets-are-not-tagged).

## How a transaction gets its tag

Cheapest first; stop at the first confident hit. Every result records `(tag, confidence, source)` so the console can say "tagged by: merchant memory" and a correction knows which layer to teach.

```
0. tag supplied by caller (API / MCP / recipe)     → stored, done. Nothing else runs.
1. user rules                                      → exact / regex on merchant or description
2. exact fingerprint cache (user)                  → hash of normalised description → tag  (Redis, sub-ms)
3. merchant memory (user, then global anonymised)  → normalised merchant → tag
4. keyword dictionary (vi + en, shipped)           → "grab" → ride_hailing, "EVN" → utilities, "lì xì" → lucky_money_given
5. vector kNN over the user's confirmed examples   → nearest normalised descriptions; confidence = neighbour agreement
6. untagged                                        → visible, queryable, budget-safe
7. LLM, nightly batch, allowance-gated             → proposes tags for untagged rows; proposals await confirmation
```

**Normalised description** = description with digits, dates, reference codes, card tails and amounts stripped, lower-cased, whitespace collapsed. `GRAB*A1B2 250000 01/10` and `GRAB*Z9Y8 180000 15/10` normalise to `grab*`.

**Proposed vs confirmed.** Steps 5 and 7 produce *proposals* unless confidence is above the user's threshold. A proposal is an **interaction** (see [tech/07-interactions-and-channels.md](../tech/07-interactions-and-channels.md)) delivered via Telegram, MCP or the console with the top suggestions as options. Only a **confirmed** tag (explicit user action, an agent's `set_tag`, or a caller-supplied tag) writes to memory. This stops wrong guesses from reinforcing themselves.

**"Always tag like this."** The user can pin their choice for a transaction: we store a high-weight example (its fingerprint → tag) and, if a merchant is identifiable, a merchant-memory row. Pinned examples outrank LLM-confirmed ones in kNN.

**Global merchant memory** stores `(normalised_merchant, tag, votes)` with no user id, fed only by confirmations, counted only after N distinct users agree. Grab, Shopee, EVN, Highlands are the same for everyone; a few thousand merchants cover most VN retail.

**The LLM step** runs at most once per user per day over that day's untagged rows in one call (compact list in, tags out, user's combined tag list in the prompt). Free-tier or paid model; either way best-effort. If it fails, rows stay untagged until tomorrow or until the user's own agent tags them via MCP (`list_untagged`, `set_tag`), on the user's token budget.

## Suggestions (no LLM)

The system embeds the **tag list itself** once — each system tag's key, `vi`/`en` names, synonyms and a one-line description, plus the user's customs — a tiny index of ~80 vectors. Combined with trigram matching, it powers three cases:

| Case | Behaviour |
|---|---|
| Caller sends an unknown tag (`"milktea"`) | Trigram match against existing tags first (typo → `milk tea` custom). Then embed the word and return the top 3 nearest tags with scores and the nearest root as suggested parent: `409 unknown_tag {suggestions: [coffee_drinks 0.81, snacks 0.74, dining 0.61], create_as_child_of: "food"}`. Caller picks one or repeats with `create_if_missing=true`. |
| Auto-tag is low confidence | Return the top 3 kNN candidates from description examples merged with the top 3 from the tag-name index; console shows them as one-click chips. |
| Console tag picker | As the user types, trigram + tag-name embedding ranks the combined list. |

## Translation

- System tags: console catalogue keyed by `system_key`: `tag.food.coffee_drinks` → "Cà phê / trà sữa". Fallback chain: requested locale → `en` → humanised key ("Coffee drinks").
- Custom tags: only the user's `name`; never translated.
- The API returns `{id, system_key?, name?, root_key, kind, is_custom}`; the console shows `name` when present, else the catalogue entry.
- User renames of system tags are stored as `display_name_override` on the user's side and win over the catalogue.

## Budgets and reports

Budgets attach to any tag (root or child). Reports roll up by root by default with drill-down to children. Postings under the `off_report` root are excluded from spend and income totals. `untagged` appears in reports with a count and a link to the triage screen.
