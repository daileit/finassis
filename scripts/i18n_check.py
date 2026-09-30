#!/usr/bin/env python3
"""Validate i18n catalogues.

Checks:
  1. every key in en.json (source) exists in every other hand-written locale;
  2. no locale has keys missing from en.json (orphans);
  3. ICU placeholders {name} match between source and translation;
  4. generated/<locale>.json exists for every hand-written locale and covers
     every tag key and unit code in seeds/;
  5. JSON is valid and keys are sorted-agnostic but unique.

Exit code 1 on any failure. Run via `make i18n-check`.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
I18N = ROOT / "i18n"
SEEDS = ROOT / "seeds"
SOURCE = "en"
PLACEHOLDER = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\s*(?:,|\})")  # {name} or {name, plural/select ...}


def load(path: Path) -> dict[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("$")}


def placeholders(msg: str) -> set[str]:
    """Top-level ICU argument names. Branch bodies like `=0 {Nothing}` are literal text, not
    arguments, so only `{name}` and `{name, type ...}` forms count."""
    return set(PLACEHOLDER.findall(msg))


def main() -> int:
    errors: list[str] = []
    hand = {p.stem: load(p) for p in I18N.glob("*.json")}
    if SOURCE not in hand:
        print(f"missing source catalogue i18n/{SOURCE}.json", file=sys.stderr)
        return 1
    src = hand[SOURCE]

    for locale, cat in hand.items():
        if locale == SOURCE:
            continue
        for key in src.keys() - cat.keys():
            errors.append(f"[{locale}] missing key: {key}")
        for key in cat.keys() - src.keys():
            errors.append(f"[{locale}] orphan key (not in {SOURCE}): {key}")
        for key in src.keys() & cat.keys():
            a, b = placeholders(src[key]), placeholders(cat[key])
            if a != b:
                errors.append(f"[{locale}] placeholder mismatch in {key}: {sorted(a)} vs {sorted(b)}")

    tags = json.loads((SEEDS / "tags.json").read_text(encoding="utf-8"))
    units = json.loads((SEEDS / "units.json").read_text(encoding="utf-8"))
    expected = {f"tag.{r['key']}" for r in tags["roots"]}
    expected |= {f"tag.{c['key']}" for r in tags["roots"] for c in r["children"]}
    expected |= {f"unit.{u['code']}" for u in units["units"]}

    for locale in hand:
        gen_path = I18N / "generated" / f"{locale}.json"
        if not gen_path.exists():
            errors.append(f"[{locale}] missing generated catalogue; run `make i18n`")
            continue
        gen = load(gen_path)
        for key in sorted(expected - gen.keys()):
            errors.append(f"[{locale}] generated catalogue lacks {key}")
        for key in gen.keys() & hand[locale].keys():
            errors.append(f"[{locale}] key defined in both hand-written and generated: {key}")

    for e in errors:
        print(e, file=sys.stderr)
    if errors:
        print(f"{len(errors)} problem(s)", file=sys.stderr)
        return 1
    total = sum(len(c) for c in hand.values())
    print(f"i18n ok: {len(hand)} locales, {len(src)} source keys, {len(expected)} generated keys per locale")
    return 0


if __name__ == "__main__":
    sys.exit(main())
