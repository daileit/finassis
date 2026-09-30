# seeds/

Machine-readable reference data that the application loads at startup and that the docs, the console and future conversations refer to by key. This folder is the **source of truth**; the markdown docs describe rules and point here for the data.

| File | Contents | Loaded into |
|------|----------|-------------|
| `tags.json` | System tag tree: roots (fixed), children (system), en/vi names, default on/off/sys, keywords | `tags` table (upsert by `system_key`), keyword dictionary, tag-name embedding index, `i18n` `tag.*` entries |
| `units.json` | Measures and global units with factors, decimals, symbols, names, aliases | `units` table (upsert by `code`), `i18n` `unit.*` entries, `parse_quantity` alias table |
| `schemas/*.schema.json` | JSON Schema for each seed file | CI validation |

## Loading rules

1. Schema is created/upgraded by Alembic (`alembic upgrade head`) on startup.
2. The seed loader then runs, **idempotently**: upsert by natural key (`system_key`, `code`); never delete; never touch rows with `user_id` set (custom tags, user units). A system tag that disappears from the seed is marked `is_deprecated` rather than removed.
3. `version` in each file is recorded in a `seed_versions` table; the loader skips files whose version and content hash are unchanged.
4. New users receive the tags whose `default = "on"` as active; `off` tags exist but are hidden until enabled; `sys` tags are never user-selectable.
5. `names` are extracted into `i18n/{locale}.json` at build time (`make i18n`) under `tag.<key>` and `unit.<code>`; the runtime does not read names from these files.

## Editing

- Add a child tag: append to the root's `children` with a unique key, both names, `default`, and keywords. Bump `version`.
- Never rename a key (it is referenced by rules, recipes, memory and community content); deprecate and add a new one.
- Keywords are lower-case; Vietnamese without tone marks (runtime normalises input the same way).
- Validate: `make seeds-check` (JSON Schema + duplicate-key check).
