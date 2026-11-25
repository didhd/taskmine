"""Dependency manifests: which files are copied, what npm normalisation removes, and the fingerprint."""

from __future__ import annotations

import json
import unittest

from taskmine import deps


class Expand(unittest.TestCase):
    PATHS = {"package.json", "packages/a/package.json", "packages/b/package.json", "packages/b/src/x.ts", "go.sum"}

    def test_literals_are_kept_even_when_absent(self):
        self.assertEqual(deps.expand(["package.json", "missing.lock"], self.PATHS), ["package.json", "missing.lock"])

    def test_globs_match_zero_or_more_in_sorted_order(self):
        self.assertEqual(deps.expand(["packages/*/package.json"], self.PATHS),
                         ["packages/a/package.json", "packages/b/package.json"])
        self.assertEqual(deps.expand(["nothing/*.json"], self.PATHS), [])
        self.assertEqual(deps.expand(["go.su?"], self.PATHS), ["go.sum"])

    def test_duplicates_keep_their_first_place(self):
        got = deps.expand(["packages/b/package.json", "packages/*/package.json", "package.json"], self.PATHS)
        self.assertEqual(got, ["packages/b/package.json", "packages/a/package.json", "package.json"])


class NormalizeNpm(unittest.TestCase):
    def test_package_json_loses_only_its_version(self):
        data = json.dumps({"name": "x", "version": "1.2.3", "dependencies": {"a": "^1"}}).encode()
        out = json.loads(deps.normalize_npm("web/package.json", data))
        self.assertEqual(out, {"name": "x", "dependencies": {"a": "^1"}})

    def test_lockfile_keeps_third_party_versions(self):
        lock = {"name": "root", "version": "2.0.0", "lockfileVersion": 3, "packages": {
            "": {"name": "root", "version": "2.0.0"},
            "packages/a": {"name": "@x/a", "version": "0.4.0"},
            "node_modules/left-pad": {"version": "1.3.0"},
            "packages/a/node_modules/tiny": {"version": "9.9.9"},
            "node_modules/@x/a": {"resolved": "packages/a", "link": True},
        }}
        out = json.loads(deps.normalize_npm("package-lock.json", json.dumps(lock).encode()))
        self.assertNotIn("version", out)
        self.assertNotIn("version", out["packages"][""])
        self.assertNotIn("version", out["packages"]["packages/a"])
        self.assertEqual(out["packages"]["node_modules/left-pad"]["version"], "1.3.0")
        self.assertEqual(out["packages"]["packages/a/node_modules/tiny"]["version"], "9.9.9")
        self.assertEqual(out["lockfileVersion"], 3)

    def test_other_files_are_untouched(self):
        for path in ("tsconfig.json", "requirements.txt", "packages/a/package.json.bak"):
            raw = b'{"version": "1"}  \n'
            self.assertIs(deps.normalize_npm(path, raw), raw)

    def test_output_is_stable_text(self):
        data = json.dumps({"name": "é", "version": "1"}).encode()
        out = deps.normalize_npm("package.json", data)
        self.assertEqual(out, '{\n  "name": "é"\n}\n'.encode())


class Materialize(unittest.TestCase):
    def test_the_fingerprint_follows_content_and_paths(self):
        a = {"x/package.json": b'{"name": "x", "version": "1"}'}
        files, fp = deps.materialize(a, None)
        self.assertEqual(files, a)
        self.assertEqual(deps.materialize(dict(a), None)[1], fp)
        self.assertNotEqual(deps.materialize({"y/package.json": a["x/package.json"]}, None)[1], fp)
        self.assertNotEqual(deps.materialize({"x/package.json": b'{"name": "x", "version": "2"}'}, None)[1], fp)
        self.assertEqual(deps.materialize({"x/package.json": b'{"name": "x", "version": "2"}'}, "npm")[1],
                         deps.materialize(a, "npm")[1])

    def test_insertion_order_does_not_matter(self):
        one = deps.materialize({"a": b"1", "b": b"2"}, None)
        two = deps.materialize({"b": b"2", "a": b"1"}, None)
        self.assertEqual(one[1], two[1])
        self.assertEqual(list(one[0]), ["a", "b"])


if __name__ == "__main__":
    unittest.main()
