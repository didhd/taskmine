"""A small TOML writer for task.toml (the standard library reads TOML but does not write it).

Floats are always written with a decimal point: Harbor's schema types the timeouts as floats.
"""

from __future__ import annotations

import json
import re

_BARE = re.compile(r"[A-Za-z0-9_-]+")


def _key(k: str) -> str:
    """A bare key when TOML allows one, else a quoted key (`a.b` unquoted would be a dotted key)."""
    return k if _BARE.fullmatch(k) else _string(k)


def _string(s: str) -> str:
    # JSON escapes every control character TOML forbids in a basic string except DEL (U+007F).
    return json.dumps(s, ensure_ascii=False).replace("\x7f", "\\u007f")


def _value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        s = repr(v)
        return s if ("." in s or "e" in s or "inf" in s or "nan" in s) else s + ".0"
    if isinstance(v, str):
        return _string(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_value(x) for x in v) + "]"
    raise TypeError(f"cannot write {type(v).__name__} to TOML")


def dumps(doc: dict) -> str:
    """`doc` maps top-level keys and tables; nested dicts become [a.b] tables, in insertion order."""
    lines: list[str] = []

    def table(prefix: str, d: dict) -> None:
        scalars = [(k, v) for k, v in d.items() if not isinstance(v, dict)]
        subs = [(k, v) for k, v in d.items() if isinstance(v, dict)]
        if prefix and (scalars or not subs):
            if lines:
                lines.append("")
            lines.append(f"[{prefix}]")
        for k, v in scalars:
            lines.append(f"{_key(k)} = {_value(v)}")
        for k, v in subs:
            table(f"{prefix}.{_key(k)}" if prefix else _key(k), v)

    table("", doc)
    return "\n".join(lines) + "\n"
