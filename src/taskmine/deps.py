"""Dependency manifests, copied into the image before the snapshot so Docker can share the layer.

The Dockerfile copies `environment/deps/` and runs the profile's install command BEFORE it adds the
repository snapshot. Two tasks whose manifests are byte-identical then share the installed layer.

In an npm package the manifests also carry the package's own `version`, which a release bump
changes without changing a single dependency. `deps_normalize = "npm"` removes that one field from
package.json and from the lockfile's root entry; the snapshot, extracted afterwards, restores the
real files. On foxl-ai/pilot's 497 foxl test candidates this turned 69 distinct installs into 23.
"""

from __future__ import annotations

import hashlib
import json


def normalize_npm(path: str, data: bytes) -> bytes:
    name = path.rsplit("/", 1)[-1]
    if name not in ("package.json", "package-lock.json"):
        return data
    obj = json.loads(data)
    obj.pop("version", None)
    if name == "package-lock.json":
        root = obj.get("packages", {}).get("")
        if isinstance(root, dict):
            root.pop("version", None)
    return (json.dumps(obj, indent=2, ensure_ascii=False) + "\n").encode()


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
