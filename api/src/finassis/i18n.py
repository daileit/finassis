"""Server-side message catalogue: generated (tags/units) + hand-written, with fallback chain
requested locale → en → humanised key. Minimal ICU subset: {name} placeholders and
{n, plural, =0 {..} one {..} other {..}} / {x, select, a {..} other {..}}."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import get_settings

_ARG = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)(?:\s*,\s*(plural|select)\s*,\s*(.*))?\}$", re.S)


class Catalogue:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._data: dict[str, dict[str, str]] = {}
        self.load()

    def load(self) -> None:
        self._data.clear()
        for path in sorted((self.root / "generated").glob("*.json")) if (self.root / "generated").exists() else []:
            self._data.setdefault(path.stem, {}).update(_load(path))
        for path in sorted(self.root.glob("*.json")):
            self._data.setdefault(path.stem, {}).update(_load(path))

    @property
    def locales(self) -> list[str]:
        return sorted(self._data)

    def t(self, locale: str, key: str, **params: Any) -> str:
        msg = self._data.get(locale, {}).get(key) or self._data.get("en", {}).get(key)
        if msg is None:
            return _humanise(key)
        return render(msg, params)


def _load(path: Path) -> dict[str, str]:
    with path.open(encoding="utf-8") as fh:
        raw = json.load(fh)
    return {k: v for k, v in raw.items() if not k.startswith("$")}


def _humanise(key: str) -> str:
    tail = key.rsplit(".", 1)[-1]
    return tail.replace("_", " ").capitalize()


def render(msg: str, params: dict[str, Any]) -> str:
    """Render a message with a minimal ICU subset."""
    out: list[str] = []
    i = 0
    while i < len(msg):
        ch = msg[i]
        if ch != "{":
            out.append(ch)
            i += 1
            continue
        j = _match_brace(msg, i)
        if j == -1:
            out.append(msg[i:])
            break
        out.append(_render_arg(msg[i : j + 1], params))
        i = j + 1
    return "".join(out)


def _match_brace(s: str, start: int) -> int:
    depth = 0
    for k in range(start, len(s)):
        if s[k] == "{":
            depth += 1
        elif s[k] == "}":
            depth -= 1
            if depth == 0:
                return k
    return -1


def _render_arg(token: str, params: dict[str, Any]) -> str:
    inner = token[1:-1]
    mm = re.match(r"\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*(?:,\s*(plural|select)\s*,\s*(.*))?$", inner, re.S)
    if not mm:
        return token
    name, kind, body = mm.group(1), mm.group(2), mm.group(3)
    val = params.get(name)
    if kind is None:
        return "" if val is None else str(val)
    branches = _branches(body or "")
    if kind == "plural":
        n = 0 if val is None else val
        chosen = branches.get(f"={n}") or (branches.get("one") if n == 1 else None) or branches.get("other", "")
        return render(chosen.replace("#", str(n)), params)
    chosen = branches.get(str(val).lower() if val is not None else "other", None)
    if chosen is None:
        chosen = branches.get("other", "")
    return render(chosen, params)


def _branches(body: str) -> dict[str, str]:
    res: dict[str, str] = {}
    i = 0
    while i < len(body):
        m = re.match(r"\s*(=\d+|[a-zA-Z_][a-zA-Z0-9_]*)\s*\{", body[i:])
        if not m:
            break
        sel = m.group(1)
        start = i + m.end() - 1
        end = _match_brace(body, start)
        if end == -1:
            break
        res[sel] = body[start + 1 : end]
        i = end + 1
    return res


@lru_cache(maxsize=1)
def catalogue() -> Catalogue:
    return Catalogue(get_settings().i18n_dir)


def t(locale: str, key: str, **params: Any) -> str:
    return catalogue().t(locale, key, **params)
