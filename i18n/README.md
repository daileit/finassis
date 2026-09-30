# i18n/

Message catalogues for everything Finassis says to a user that isn't the user's own data. Read by both `api/` (Telegram replies, narration, error messages) and `console/` (UI). English is the source; Vietnamese is the first-class translation. Rules in ADR-020.

```
i18n/
├── en.json              hand-written source catalogue
├── vi.json              hand-written translation (every en key must exist here)
└── generated/           DO NOT EDIT — built from seeds/ by `make i18n`
    ├── en.json          tag.<key>, unit.<code>, unit.<code>.symbol
    └── vi.json
```

Runtime loads `generated/<locale>.json` then `<locale>.json` on top (hand-written wins on conflict, which the checker forbids anyway). Lookup falls back: requested locale → `en` → humanised key.

## Key conventions

- Stable identifiers, dot-namespaced: `common.*`, `kind.*`, `enum.<enum_name>.<value>`, `error.*`, `interaction.<kind>.*`, `telegram.*`, `alert.*`, `digest.*`, `console.*`, and generated `tag.*`, `unit.*`.
- Never encode the English sentence in the key. Never build sentences by concatenating fragments.
- **ICU MessageFormat**: `{name}` placeholders, `{count, plural, =0 {…} one {…} other {…}}`, `{flag, select, true {…} other {…}}`. Vietnamese has no plural inflection: use `other` (and `=0` when the zero case reads differently).
- Money, dates and quantities are formatted *before* insertion by the caller (money envelope `decimals`, unit `decimals`, user locale); the catalogue never contains number formats.
- HTML is allowed only in `telegram.*` strings and only Telegram's subset (`<code>`, `<b>`, `<i>`).

## Adding a string

1. Add the key to `en.json`. 2. Add the same key to `vi.json`. 3. `make i18n-check`.

## Adding a locale

Copy `en.json` to `<locale>.json`, translate, add `names.<locale>` to seeds where you want translated tag/unit names (missing ones fall back to `en`), run `make i18n && make i18n-check`.

## Checks (`make i18n-check`)

- Generated catalogues are fresh with respect to `seeds/`.
- Every `en` key exists in every other hand-written locale; no orphan keys.
- ICU top-level argument names match between source and translation.
- Every tag key and unit code in `seeds/` has a generated entry per locale.
- No key is defined in both hand-written and generated files.

`make check` runs this together with `seeds-check`.
