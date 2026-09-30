#!/usr/bin/env python3
"""Validate seeds/*.json against seeds/schemas/*.schema.json and check key uniqueness.

Requires `jsonschema` (pip install jsonschema). Exit 1 on failure.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEEDS = ROOT / "seeds"

try:
    import jsonschema
except ImportError:  # pragma: no cover
    print("pip install jsonschema", file=sys.stderr)
    sys.exit(2)


def main() -> int:
    errors: list[str] = []
    for seed in sorted(SEEDS.glob("*.json")):
        schema_path = SEEDS / "schemas" / f"{seed.stem}.schema.json"
        if not schema_path.exists():
            errors.append(f"{seed.name}: no schema at {schema_path.relative_to(ROOT)}")
            continue
        data = json.loads(seed.read_text(encoding="utf-8"))
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        try:
            jsonschema.validate(data, schema)
        except jsonschema.ValidationError as e:
            errors.append(f"{seed.name}: {e.message} at {'/'.join(map(str, e.absolute_path))}")
            continue

        if seed.stem == "tags":
            keys = [r["key"] for r in data["roots"]] + [c["key"] for r in data["roots"] for c in r["children"]]
            for k, n in Counter(keys).items():
                if n > 1:
                    errors.append(f"tags.json: duplicate key {k}")
            for r in data["roots"]:
                if r["key"] in ("misc", "untagged") and r["children"]:
                    errors.append(f"tags.json: {r['key']} must have no children")
        if seed.stem == "units":
            codes = [u["code"] for u in data["units"]]
            for c, n in Counter(codes).items():
                if n > 1:
                    errors.append(f"units.json: duplicate code {c}")
            for u in data["units"]:
                is_money = u["measure"] == "money"
                if is_money != (u["factor_to_base"] is None):
                    errors.append(f"units.json: {u['code']}: factor_to_base must be null iff measure is money")
            for m, spec in data["measures"].items():
                if spec["base"] and spec["base"] not in codes:
                    errors.append(f"units.json: measure {m} base unit {spec['base']} not defined")
        print(f"{seed.name}: ok")

    for e in errors:
        print(e, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
