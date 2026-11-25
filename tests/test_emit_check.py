"""Drafts, finished tasks and the static check, built from a real repository (no test run needed)."""

from __future__ import annotations

import io
import json
import os
import shutil
import tarfile
import tomllib
from pathlib import Path

from support import History, SharedTempDir, needs_git, recipe

from taskmine import config, emit, scan, tomlw
from taskmine.check import check_task
from taskmine.vcs import Repo

RECIPE = """
[repo]
name = "example/shop"
github = "example/shop"

[[profiles]]
name = "node-test"
run = ["test/*.test.mjs"]
image = "node:24-bookworm"
command = "node --test --test-reporter=junit --test-reporter-destination={junit} {files}"
display_command = "node --test {files}"
deps = ["package.json"]
deps_normalize = "npm"
install = "npm install --no-audit"
setup = "apt-get install -y jq"
env = { B = "2", A = "1" }
timeout_sec = 120
"""

CART = "export function total(items) {\n  return items.reduce((a, b) => a + b, 0);\n}\n"
COUPON_LINE = 'export const COUPON_PREFIX = "SAVE-2026-WINTER-SALE";'
CART_AFTER = CART + (
    "\nexport function applyCoupon(sum, code) {\n"
    "  if (!code.startsWith(COUPON_PREFIX)) throw new Error('E_COUPON_UNKNOWN');\n"
    "  return sum * 0.9;\n}\n" + COUPON_LINE + "\n")
CART_TEST = ("import { test } from 'node:test';\nimport assert from 'node:assert/strict';\n"
             "import { total } from '../lib/cart.mjs';\n\ntest('adds', () => assert.equal(total([1, 2]), 3));\n")
COUPON_TEST = ("import { test } from 'node:test';\nimport assert from 'node:assert/strict';\n"
               "import { readFileSync } from 'node:fs';\n"
               "import { applyCoupon } from '../lib/cart.mjs';\nimport { CODES } from '../lib/codes.mjs';\n\n"
               "test('applies a coupon', () => assert.equal(applyCoupon(100, CODES[0]), 90));\n"
               "test('refuses an unknown coupon', () => assert.throws(() => applyCoupon(1, 'x'), "
               "{ message: 'E_COUPON_UNKNOWN' }));\n"
               "test('the prefix is a constant', () => assert.ok(readFileSync(new URL('../lib/cart.mjs', "
               f"import.meta.url), 'utf8').includes('{COUPON_LINE}')));\n")
F2P = ["test/coupon.test.mjs::applies a coupon", "test/coupon.test.mjs::refuses an unknown coupon"]
P2P = ["test/cart.test.mjs::adds"]
META = {"title": "[cart] feat: coupons (#11)", "url": "https://github.com/example/shop/pull/11",
        "createdAt": "2026-01-10T00:00:00Z",
        "issues": [{"number": 3, "title": "Coupons", "body": "Please support coupon codes at checkout.",
                    "createdAt": "2026-01-05T00:00:00Z"}]}


class ShopFixture(SharedTempDir):
    """One pull request (#11) of a small shop repository, built into a draft once per class."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        h = History(cls.tmp / "repo")
        h.commit("init", {
            "lib/cart.mjs": CART, "test/cart.test.mjs": CART_TEST, "test/old.test.mjs": "// gone\n",
            "package.json": '{\n  "name": "shop",\n  "version": "1.0.0"\n}\n', "README.md": "# shop\n",
            "test/run.sh": ("100755", "#!/bin/sh\nnode --test\n"),
        })
        h.pr(11, {"lib/cart.mjs": CART_AFTER, "lib/codes.mjs": "export const CODES = ['SAVE-2026-WINTER-SALE-1'];\n",
                  "test/coupon.test.mjs": COUPON_TEST, "test/cart.test.mjs": CART_TEST + "// touched\n",
                  "test/run.sh": ("100755", "#!/bin/sh\nnode --test test\n"), "README.md": "# shop\n\nCoupons.\n"},
              delete=("test/old.test.mjs",))
        cls.root = h.write()
        cls.repo = Repo(str(cls.root))
        cls.cfg = config.load(recipe(cls.tmp, RECIPE))
        (cls.cand,) = [c for c in scan.scan(cls.repo, cls.cfg) if c.unit == "pr11"]
        cls.draft = emit.build_draft(cls.repo, cls.cfg, cls.cand, META, cls.tmp / "drafts")
        cls.info = json.loads((cls.draft / "draft.json").read_text())

    @classmethod
    def validation(cls, draft=None, **kw):
        v = {"id": cls.cand.id, "verdict": "accepted", "reason": "", "draft_digest": emit.draft_digest(draft or cls.draft),
             "fail_to_pass": F2P, "pass_to_pass": P2P, "runs_summary": "base 2, gold 3, nop 1, oracle 1"}
        v.update(kw)
        return v

    @classmethod
    def finalize(cls, name: str, meta: dict | None = None, **kw) -> tuple[Path, dict]:
        draft = cls.tmp / "variants" / name
        if not draft.exists():
            shutil.copytree(cls.draft, draft)
            if meta is not None:
                info = dict(cls.info, meta=meta)
                (draft / "draft.json").write_text(json.dumps(info))
        r = emit.finalize(cls.cfg, draft, cls.validation(draft, **kw), cls.tmp / "tasks-" / name)
        return cls.tmp / "tasks-" / name / cls.cand.id, r


@needs_git
class Drafts(ShopFixture):

    def test_the_draft_is_a_harbor_task_without_its_expectations(self):
        d = self.draft
        for rel in ("environment/Dockerfile", "environment/repo.tar.gz", "environment/deps/package.json",
                    "solution/solve.sh", "solution/gold.patch", "tests/test.sh", "tests/grader.py", "tests/spec.json",
                    "tests/files/test/coupon.test.mjs", "tests/files/test/cart.test.mjs", "tests/files/test/run.sh"):
            self.assertTrue((d / rel).is_file(), rel)
        self.assertFalse((d / "tests" / "expected.json").exists())
        self.assertFalse((d / "instruction.md").exists())
        for rel in ("solution/solve.sh", "tests/test.sh", "tests/grader.py", "tests/files/test/run.sh"):
            self.assertTrue(os.access(d / rel, os.X_OK), rel)
        self.assertFalse(os.access(d / "tests/files/test/cart.test.mjs", os.X_OK))

    def test_the_spec_names_what_the_grader_runs(self):
        spec = json.loads((self.draft / "tests" / "spec.json").read_text())
        self.assertEqual(spec["run_files"], ["test/cart.test.mjs", "test/coupon.test.mjs"])
        self.assertEqual(spec["deleted"], ["test/old.test.mjs"])
        self.assertEqual(spec["env"], {"A": "1", "B": "2"})
        self.assertEqual((spec["workdir"], spec["timeout_sec"]), (".", 120.0))
        self.assertEqual(spec["test_globs"], self.cfg.tests.patterns)

    def test_the_dockerfile_installs_before_the_snapshot(self):
        df = (self.draft / "environment" / "Dockerfile").read_text()
        self.assertIn(f"for {self.cand.id}.", df)
        self.assertIn("FROM node:24-bookworm\nENV CI=1 \\\n    A=\"1\" \\\n    B=\"2\"\n", df)
        self.assertLess(df.index("RUN apt-get install -y jq"), df.index("COPY deps/ /app/"))
        self.assertLess(df.index("RUN npm install --no-audit"), df.index("COPY repo.tar.gz"))
        self.assertEqual(json.loads((self.draft / "environment" / "deps" / "package.json").read_text()), {"name": "shop"})

    def test_the_gold_patch_holds_the_code_side_only(self):
        patch = (self.draft / "solution" / "gold.patch").read_text()
        self.assertIn("+++ b/lib/cart.mjs", patch)
        self.assertIn("+++ b/lib/codes.mjs", patch)
        self.assertIn("+++ b/README.md", patch)
        self.assertNotIn("test/coupon.test.mjs", patch)

    def test_the_contract_and_provenance(self):
        con = self.info["contract"]
        self.assertEqual(con["new_names"], ["applyCoupon", "CODES"])
        self.assertEqual(con["new_modules"], ["lib/codes.mjs"])
        self.assertEqual(con["exact_text"], ["E_COUPON_UNKNOWN", COUPON_LINE])
        self.assertEqual(self.info["source_text_reads"], {"test/coupon.test.mjs": ["lib/cart.mjs"]})
        self.assertEqual(self.info["meta"], META)
        self.assertEqual(self.info["draft_digest"], emit.draft_digest(self.draft))
        self.assertEqual(self.info["gold"]["files"], 3)

    def test_a_rebuild_has_the_same_digest(self):
        again = emit.build_draft(self.repo, self.cfg, self.cand, META, self.tmp / "rebuild")
        self.assertEqual(json.loads((again / "draft.json").read_text())["draft_digest"], self.info["draft_digest"])

    def test_the_digest_follows_bytes_and_modes_but_not_expectations(self):
        d = self.tmp / "digest"
        shutil.copytree(self.draft, d)
        before = emit.draft_digest(d)
        (d / "tests" / "expected.json").write_text("{}")
        (d / "draft.json").write_text("{}")
        self.assertEqual(emit.draft_digest(d), before)
        os.chmod(d / "tests" / "spec.json", 0o755)
        self.assertNotEqual(emit.draft_digest(d), before)
        os.chmod(d / "tests" / "spec.json", 0o644)
        (d / "solution" / "gold.patch").write_text("changed")
        self.assertNotEqual(emit.draft_digest(d), before)

    def test_missing_dependency_files_refuse_the_draft(self):
        body = RECIPE.replace('deps = ["package.json"]', 'deps = ["package.json", "package-lock.json"]')
        cfg = config.load(recipe(self.tmp, body, "missing-deps.toml"))
        with self.assertRaises(emit.DraftError) as cm:
            emit.build_draft(self.repo, cfg, self.cand, META, self.tmp / "missing")
        self.assertEqual((cm.exception.code, cm.exception.detail), ("deps-missing", "package-lock.json"))

    def test_a_refused_unit_is_not_a_candidate(self):
        refused = scan.Candidate.from_json(dict(self.cand.to_json(), refusal="no-test-change"))
        with self.assertRaises(emit.DraftError) as cm:
            emit.build_draft(self.repo, self.cfg, refused, None, self.tmp / "refused")
        self.assertEqual(cm.exception.code, "not-a-candidate")

    # --- finishing

    def test_a_finished_task_states_its_sources_and_passes_check(self):
        task, r = self.finalize("plain")
        self.assertEqual(check_task(task), [])
        self.assertEqual(r["sources"], ["title", "tests", "issue", "contract"])
        self.assertEqual(r["notes"], ["exact text left out because it is a line of the gold code: 1"])
        text = (task / "instruction.md").read_text()
        self.assertTrue(text.startswith("# feat: coupons\n"))
        self.assertIn("> Please support coupon codes at checkout.", text)
        self.assertIn("    node --test test/cart.test.mjs test/coupon.test.mjs\n", text)
        self.assertIn("- `E_COUPON_UNKNOWN`", text)
        self.assertNotIn("SAVE-2026-WINTER-SALE", text)
        exp = json.loads((task / "tests" / "expected.json").read_text())
        self.assertEqual(exp, {"fail_to_pass": F2P, "pass_to_pass": P2P})
        self.assertEqual(os.stat(task / "environment" / "repo.tar.gz").st_ino,
                         os.stat(self.tmp / "variants" / "plain" / "environment" / "repo.tar.gz").st_ino)

    def test_task_toml_carries_float_timeouts_and_provenance(self):
        task, _ = self.finalize("toml")
        doc = tomllib.loads((task / "task.toml").read_text())
        self.assertEqual(doc["verifier"]["timeout_sec"], 420.0)
        self.assertEqual(doc["agent"]["timeout_sec"], 3600.0)
        self.assertEqual(doc["environment"], {"build_timeout_sec": 1800.0, "cpus": 2, "memory_mb": 4096, "storage_mb": 20480})
        m = doc["metadata"]["taskmine"]
        self.assertEqual((m["repo"], m["unit"], m["issue"], m["url"]), ("example/shop", "pr11", 3, META["url"]))
        self.assertEqual((m["fail_to_pass"], m["pass_to_pass"], m["contract_text"]), (2, 1, 1))
        self.assertEqual(m["contract"]["text"], ["E_COUPON_UNKNOWN"])
        self.assertEqual(m["draft_digest"], emit.draft_digest(task))
        self.assertEqual(doc["metadata"]["tags"], ["taskmine", "shop", "node-test"])

    def test_an_issue_opened_after_the_pull_request_is_not_used(self):
        late = dict(META, issues=[dict(META["issues"][0], createdAt="2026-02-01T00:00:00Z")])
        _, r = self.finalize("late-issue", meta=late)
        self.assertNotIn("issue", r["sources"])

    def test_an_issue_that_holds_gold_code_is_left_out(self):
        body = "Maybe:\n\n    if (!code.startsWith(COUPON_PREFIX)) throw new Error('E_COUPON_UNKNOWN');\n"
        leaky = dict(META, issues=[dict(META["issues"][0], body=body)])
        task, r = self.finalize("leaky-issue", meta=leaky)
        self.assertNotIn("issue", r["sources"])
        self.assertTrue(r["notes"][-1].startswith("issue text left out: gold line verbatim"), r["notes"])
        self.assertEqual(check_task(task), [])

    def test_include_issue_false(self):
        cfg = config.load(recipe(self.tmp, RECIPE + "\n[task]\ninclude_issue = false\n", "no-issue.toml"))
        r = emit.finalize(cfg, self.draft, self.validation(), self.tmp / "no-issue-tasks")
        self.assertNotIn("issue", r["sources"])

    def test_a_title_that_is_gold_code_refuses_the_task(self):
        with self.assertRaises(emit.DraftError) as cm:
            self.finalize("leaky-title", meta=dict(META, title="if (!code.startsWith(COUPON_PREFIX)) throw new Error('E_COUPON_UNKNOWN');"))
        self.assertEqual(cm.exception.code, "instruction-leak")

    def test_finalize_refuses_bytes_that_were_not_validated(self):
        with self.assertRaises(emit.DraftError) as cm:
            emit.finalize(self.cfg, self.draft, self.validation(draft_digest="0" * 64), self.tmp / "t")
        self.assertEqual(cm.exception.code, "digest")
        with self.assertRaises(emit.DraftError) as cm:
            emit.finalize(self.cfg, self.draft, self.validation(verdict="rejected", reason="no-fail-to-pass"), self.tmp / "t")
        self.assertEqual((cm.exception.code, cm.exception.detail), ("not-accepted", "no-fail-to-pass"))


@needs_git
class Check(ShopFixture):
    """Each static check fails on a copy of a good task broken in exactly that way."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.good, _ = cls.finalize("check")

    def broken(self, name: str) -> Path:
        t = self.tmp / name
        shutil.copytree(self.good, t)
        return t

    def test_the_good_task_passes(self):
        self.assertEqual(check_task(self.good), [])

    def test_a_missing_file(self):
        t = self.broken("missing")
        (t / "tests" / "spec.json").unlink()
        self.assertEqual(check_task(t), ["missing tests/spec.json"])

    def test_scripts_must_be_executable(self):
        t = self.broken("mode")
        os.chmod(t / "tests" / "test.sh", 0o644)
        self.assertEqual(check_task(t), ["tests/test.sh is not executable"])

    def test_timeouts_must_be_floats_and_provenance_present(self):
        t = self.broken("toml")
        doc = tomllib.loads((t / "task.toml").read_text())
        doc["agent"]["timeout_sec"] = 3600
        del doc["metadata"]["taskmine"]
        (t / "task.toml").write_text(tomlw.dumps(doc))
        self.assertEqual(check_task(t), ["task.toml [agent] timeout_sec is not a float",
                                         "task.toml has no [metadata.taskmine] provenance"])

    def test_task_toml_must_parse(self):
        t = self.broken("unparsable")
        (t / "task.toml").write_text("[verifier\n")
        problems = check_task(t)
        self.assertTrue(problems[0].startswith("task.toml does not parse"), problems)

    def test_expectations(self):
        t = self.broken("expected")
        (t / "tests" / "expected.json").write_text(json.dumps({"fail_to_pass": [], "pass_to_pass": ["lib/x.mjs::y"]}))
        self.assertEqual(check_task(t), ["expected.json has no fail-to-pass test",
                                         "test id outside the run files: lib/x.mjs::y"])

    def test_the_build_context_holds_nothing_else(self):
        t = self.broken("context")
        (t / "environment" / "notes.txt").write_text("x")
        self.assertEqual(check_task(t), ["unexpected build context entries: notes.txt"])

    def test_a_hidden_test_already_in_the_snapshot(self):
        t = self.broken("hidden")
        data = (t / "tests" / "files" / "test" / "coupon.test.mjs").read_bytes()
        raw = io.BytesIO()
        with tarfile.open(t / "environment" / "repo.tar.gz", "r:gz") as old, tarfile.open(fileobj=raw, mode="w:gz") as new:
            for m in old.getmembers():
                new.addfile(m, old.extractfile(m) if m.isfile() else None)
            info = tarfile.TarInfo("test/coupon.test.mjs")
            info.size = len(data)
            new.addfile(info, io.BytesIO(data))
        os.unlink(t / "environment" / "repo.tar.gz")
        (t / "environment" / "repo.tar.gz").write_bytes(raw.getvalue())
        self.assertEqual(check_task(t), ["hidden test test/coupon.test.mjs is already in the snapshot"])

    def test_an_instruction_leak(self):
        t = self.broken("leak")
        os.unlink(t / "instruction.md")
        (t / "instruction.md").write_text((self.good / "instruction.md").read_text() + "\n" + COUPON_LINE + "\n")
        problems = check_task(t)
        self.assertTrue(problems[0].startswith("instruction leak: gold line verbatim"), problems)
        self.assertTrue(all(p.startswith("instruction leak: ") for p in problems), problems)
