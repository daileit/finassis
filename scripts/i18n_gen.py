#!/usr/bin/env python3
"""Generate i18n/generated/<locale>.json from seeds/*.json.

Produces tag.<key>, tag.<key>.kind (roots only) and unit.<code> entries for every
locale present in the seed names. Hand-written catalogues in i18n/<locale>.json are
never touched; the runtime merges generated + hand-written (hand-written wins).

Usage:  python scripts/i18n_gen.py            # write files
        python scripts/i18n_gen.py --check    # exit 1 if generated files are stale
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEEDS = ROOT / "seeds"
OUT = ROOT / "i18n" / "generated"


def load(name: str) -> dict:
    return json.loads((SEEDS / f"{name}.json").read_text(encoding="utf-8"))


def build() -> dict[str, dict[str, str]]:
    catalogues: dict[str, dict[str, str]] = {}

    def put(locale: str, key: str, value: str) -> None:
        catalogues.setdefault(locale, {})[key] = value

    tags = load("tags")
    for root in tags["roots"]:
        for locale, name in root["names"].items():
            put(locale, f"tag.{root['key']}", name)
        for child in root["children"]:
            for locale, name in child["names"].items():
                put(locale, f"tag.{child['key']}", name)

    units = load("units")
    for unit in units["units"]:
        for locale, name in unit["names"].items():
            put(locale, f"unit.{unit['code']}", name)
        if unit.get("symbol"):
            for locale in unit["names"]:
                put(locale, f"unit.{unit['code']}.symbol", unit["symbol"])

    for locale in catalogues:
        catalogues[locale] = dict(sorted(catalogues[locale].items()))
    return catalogues


def render(locale: str, entries: dict[str, str]) -> str:
    doc = {
        "$meta": {
            "locale": locale,
            "role": "generated",
            "generator": "scripts/i18n_gen.py",
            "sources": ["seeds/tags.json", "seeds/units.json"],
            "notes": "Do not edit by hand. Regenerate with `make i18n`.",
        },
        **entries,
    }
    return json.dumps(doc, ensure_ascii=False, indent=2) + "\n"


def main(argv: list[str]) -> int:
    check = "--check" in argv
    catalogues = build()
    OUT.mkdir(parents=True, exist_ok=True)
    stale = []
    for locale, entries in catalogues.items():
        path = OUT / f"{locale}.json"
        content = render(locale, entries)
        if check:
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                stale.append(path)
        else:
            path.write_text(content, encoding="utf-8")
            print(f"wrote {path.relative_to(ROOT)} ({len(entries)} entries)")
    if check and stale:
        for p in stale:
            print(f"stale: {p.relative_to(ROOT)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
