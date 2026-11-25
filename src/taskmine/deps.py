"""Dependency manifests, copied into the image before the snapshot so Docker can share the layer.

The Dockerfile copies `environment/deps/` and runs the profile's install command BEFORE it adds the
repository snapshot. Two tasks whose manifests are byte-identical then share the installed layer.

In an npm package the manifests also carry the package's own `version`, which a release bump
changes without changing a single dependency. `deps_normalize = "npm"` removes that one field from
package.json and from the lockfile's root entry; the snapshot, extracted afterwards, restores the
real files. On the dogfood monorepo's 497 app test candidates this turned 69 distinct installs into 23.
"""

from __future__ import annotations

import hashlib
import json


def normalize_npm(path: str, data: bytes) -> bytes:
    """Drop the versions of the repository's OWN packages: the root, and in a lockfile every entry
    that is a workspace folder (a key not under node_modules/). Third-party versions are kept."""
    name = path.rsplit("/", 1)[-1]
    if name not in ("package.json", "package-lock.json"):
        return data
    obj = json.loads(data)
    obj.pop("version", None)
    if name == "package-lock.json":
        for key, entry in (obj.get("packages") or {}).items():
            if isinstance(entry, dict) and not key.startswith("node_modules/") and "/node_modules/" not in key:
                entry.pop("version", None)
    return (json.dumps(obj, indent=2, ensure_ascii=False) + "\n").encode()


def expand(patterns: list[str], paths: set[str]) -> list[str]:
    """Literal paths are required; a pattern with `*` matches zero or more files."""
    from taskmine.globs import compile_glob

    out: list[str] = []
    for pat in patterns:
        if "*" in pat or "?" in pat:
            rx = compile_glob(pat)
            out += sorted(p for p in paths if rx.match(p))
        else:
            out.append(pat)
    return list(dict.fromkeys(out))


def materialize(files: dict[str, bytes], normalize: str | None) -> tuple[dict[str, bytes], str]:
    """Returns (path -> content to write under deps/, fingerprint)."""
    out = {}
    for path in sorted(files):
        data = files[path]
        if normalize == "npm":
            data = normalize_npm(path, data)
        out[path] = data
    h = hashlib.sha256()
    for path in sorted(out):
        h.update(path.encode() + b"\0" + hashlib.sha256(out[path]).digest())
    return out, h.hexdigest()
