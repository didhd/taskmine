"""Drafts and tasks.

`build_draft` turns a candidate into a draft: a complete Harbor task directory except for the
two things only execution can decide, `tests/expected.json` (which tests are fail-to-pass and
pass-to-pass) and `instruction.md` (which names those tests). `finalize` adds them from a
validation result and writes the finished task.

A draft is reproducible: built twice from the same repository, candidate and recipe it has the
same `draft_digest`. Validation records the digest it ran against, and `finalize` refuses a
draft whose digest differs, so a task is only ever published with the exact bytes that were
executed.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

from taskmine import __version__, contract, instruction, leak, snapshot, tomlw
from taskmine.config import Config
from taskmine.deps import materialize
from taskmine.scan import Candidate
from taskmine.vcs import Repo

TEMPLATES = Path(__file__).parent / "templates"
DIGEST_PARTS = ("environment", "solution", "tests")
DIGEST_SKIP = {"tests/expected.json"}


class DraftError(RuntimeError):
    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _write(path: Path, data: bytes | str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode() if isinstance(data, str) else data)
    os.chmod(path, mode)


def _link_or_copy(src: str, dst: str) -> None:
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def draft_digest(task_dir: Path) -> str:
    """sha256 over every file the environment, the oracle and the verifier read."""
    h = hashlib.sha256()
    for part in DIGEST_PARTS:
        root = task_dir / part
        for p in sorted(root.rglob("*")):
            rel = p.relative_to(task_dir).as_posix()
            if p.is_dir() or rel in DIGEST_SKIP:
                continue
            mode = "x" if os.access(p, os.X_OK) else "-"
            h.update(rel.encode() + b"\0" + mode.encode() + b"\0" + hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()


def _dockerfile(cfg: Config, cand: Candidate, prof, has_deps: bool) -> str:
    env = "".join(f" \\\n    {k}={json.dumps(v)}" for k, v in sorted(prof.env.items()))
    setup = f"RUN {prof.setup}\n" if prof.setup else ""
    deps = ""
    if prof.install:
        deps = ("COPY deps/ /app/\n" if has_deps else "") + f"RUN {prof.install}\n"
    return (TEMPLATES / "Dockerfile.tmpl").read_text().format(
        version=__version__, task_id=cand.id, repo_name=cfg.repo_name, unit=cand.unit,
        base_short=cand.base[:12], image=prof.image, env=env, setup=setup, deps=deps,
    )


def build_draft(repo: Repo, cfg: Config, cand: Candidate, meta: dict | None, out_root: Path) -> Path:
    if cand.refusal or not cand.profile:
        raise DraftError("not-a-candidate", cand.refusal or "no profile")
    prof = cfg.profile(cand.profile)
    base_entries = repo.ls_tree(cand.base)
    after_entries = repo.ls_tree(cand.after)
    base_tree = {e.path: e.sha for e in base_entries if e.kind == "blob"}
    after_tree = {e.path: e.sha for e in after_entries if e.kind == "blob"}
    after_modes = {e.path: e.mode for e in after_entries if e.kind == "blob"}

    from taskmine.deps import expand

    dep_paths = expand(prof.deps, set(base_tree))
    keep = {p for p in cand.gold_files if p in base_tree} | {p for p in dep_paths if p in base_tree}
    snap = snapshot.build(repo, cfg, cand.base, keep)
    after_only = {after_tree[p] for p in cand.gold_files + cand.test_files if p in after_tree}
    after_only -= set(base_tree.values())
    problems = snapshot.verify_seal(snap.data, base_tree, after_only)
    if problems:
        raise DraftError("seal", "; ".join(problems[:5]))

    gold_patch = repo.patch(cand.base, cand.after, cand.gold_files)
    test_blobs = repo.cat_blobs([after_tree[p] for p in cand.test_files])
    missing_deps = [p for p in dep_paths if p not in base_tree]
    if missing_deps:
        raise DraftError("deps-missing", ", ".join(missing_deps))
    dep_blobs = repo.cat_blobs([base_tree[p] for p in dep_paths])
    dep_files, manifests = materialize({p: dep_blobs[base_tree[p]] for p in dep_paths}, prof.deps_normalize)
    # The cached layer is the base image, the setup step, the manifests and the install command.
    fingerprint = hashlib.sha256(
        "\0".join([prof.image, prof.setup or "", prof.install or "", manifests]).encode()).hexdigest()

    sources = {}
    for p in cand.test_files:
        data = test_blobs[after_tree[p]]
        if not snapshot.looks_binary(data):
            sources[p] = data.decode("utf-8", "replace")
    base_text = b"\n".join(snap.text[p] for p in sorted(snap.text))
    gold_added = "\n".join(contract.added_lines(gold_patch))
    con = contract.extract(sources, base_text, set(base_tree), set(after_tree), gold_added)
    gold_code = {p for p in cand.gold_files if not cfg.docs.match(p)}
    coupling = contract.source_text_reads(
        {p: s for p, s in sources.items() if p in cand.run_files}, gold_code, prof.workdir)

    d = out_root / cand.id
    if d.exists():
        shutil.rmtree(d)
    _write(d / "environment" / "Dockerfile", _dockerfile(cfg, cand, prof, bool(dep_files)))
    _write(d / "environment" / "repo.tar.gz", snap.data)
    for p, data in dep_files.items():
        _write(d / "environment" / "deps" / p, data)
    _write(d / "solution" / "solve.sh", (TEMPLATES / "solve.sh").read_text(), 0o755)
    _write(d / "solution" / "gold.patch", gold_patch)
    _write(d / "tests" / "test.sh", (TEMPLATES / "test.sh").read_text(), 0o755)
    _write(d / "tests" / "grader.py", (TEMPLATES / "grader.py").read_text(), 0o755)
    for p in cand.test_files:
        mode = 0o755 if after_modes.get(p) == "100755" else 0o644
        _write(d / "tests" / "files" / p, test_blobs[after_tree[p]], mode)
    spec = {
        "workdir": prof.workdir,
        "command": prof.command,
        "run_files": cand.run_files,
        "test_globs": cfg.tests.patterns,
        "deleted": cand.test_deleted,
        "timeout_sec": prof.timeout_sec,
        "env": prof.env,
    }
    _write(d / "tests" / "spec.json", json.dumps(spec, indent=2, sort_keys=True) + "\n")
    info = {
        "taskmine_version": __version__,
        "candidate": cand.to_json(),
        "meta": meta or {},
        "contract": con.to_json(),
        "source_text_reads": coupling,
        "snapshot": {"sha256": snap.sha256, "bytes": len(snap.data), "files": len(snap.members), "left_out": snap.left_out},
        "deps_fingerprint": fingerprint,
        "gold": {"files": len(cand.gold_files), "lines": cand.gold_lines, "patch_sha256": hashlib.sha256(gold_patch).hexdigest()},
        "image": prof.image,
        "draft_digest": draft_digest(d),
    }
    _write(d / "draft.json", json.dumps(info, indent=2, sort_keys=True) + "\n")
    return d


def _issue_for(cfg: Config, meta: dict) -> dict | None:
    if not cfg.include_issue:
        return None
    pr_created = meta.get("createdAt")
    for iss in meta.get("issues", []):
        if pr_created and iss.get("createdAt") and iss["createdAt"] <= pr_created:
            return iss
    return None


def finalize(cfg: Config, draft: Path, validation: dict, tasks_root: Path) -> dict:
    info = json.loads((draft / "draft.json").read_text())
    digest = draft_digest(draft)
    if validation.get("draft_digest") != digest:
        raise DraftError("digest", f"validated {validation.get('draft_digest')} but this draft is {digest}")
    if validation.get("verdict") != "accepted":
        raise DraftError("not-accepted", validation.get("reason", "?"))
    cand = Candidate.from_json(info["candidate"])
    prof = cfg.profile(cand.profile)
    meta = info.get("meta") or {}
    con = contract.Contract(**info["contract"])
    f2p, p2p = validation["fail_to_pass"], validation["pass_to_pass"]
    title = meta.get("title") or cand.subject

    gold_patch = (draft / "solution" / "gold.patch").read_bytes()
    code_lines = contract.added_lines(gold_patch, skip_paths=cfg.docs.match)
    allowed = con.new_names + con.new_modules + con.exact_text + leak.stated_paths(cand.run_files, prof.workdir)
    notes = []
    issue = _issue_for(cfg, meta)
    text, sources = instruction.render(
        repo_name=cfg.repo_name, title=title, issue=issue, issue_limit=cfg.issue_max_chars,
        workdir=prof.workdir, display_command=prof.display_command.replace(
            "{files}", " ".join(os.path.relpath(f, prof.workdir) for f in cand.run_files)),
        f2p=f2p, contract=con,
    )
    leaks = leak.find(text, code_lines, allowed)
    if leaks and issue:
        notes.append("issue text left out: " + leaks[0])
        issue = None
        text, sources = instruction.render(
            repo_name=cfg.repo_name, title=title, issue=None, issue_limit=cfg.issue_max_chars,
            workdir=prof.workdir, display_command=prof.display_command.replace(
                "{files}", " ".join(os.path.relpath(f, prof.workdir) for f in cand.run_files)),
            f2p=f2p, contract=con,
        )
        leaks = leak.find(text, code_lines, allowed)
    if leaks:
        raise DraftError("instruction-leak", leaks[0])

    out = tasks_root / cand.id
    if out.exists():
        shutil.rmtree(out)
    for part in DIGEST_PARTS:
        # Hard links: a finished task shares the draft's bytes (the snapshot alone is ~80 MB a task).
        shutil.copytree(draft / part, out / part, symlinks=True, copy_function=_link_or_copy)
    if (out / "tests" / "expected.json").exists():
        (out / "tests" / "expected.json").unlink()
    _write(out / "tests" / "expected.json", json.dumps(
        {"fail_to_pass": f2p, "pass_to_pass": p2p}, indent=1) + "\n")
    _write(out / "instruction.md", text)
    origin = {
        "repo": cfg.repo_name,
        "unit": cand.unit,
        "base": cand.base,
        "after": cand.after,
        "url": meta.get("url", ""),
        "profile": cand.profile,
        "instruction_sources": sources,
        "issue": issue["number"] if issue else 0,
        "fail_to_pass": len(f2p),
        "pass_to_pass": len(p2p),
        "fail_to_pass_source_text_dropped": len(validation.get("fail_to_pass_source_text", [])),
        "gold_files": len(cand.gold_files),
        "gold_lines": cand.gold_lines,
        "contract_names": len(con.new_names),
        "contract_modules": len(con.new_modules),
        "contract_text": len(con.exact_text),
        "contract": {"names": con.new_names, "modules": con.new_modules, "text": con.exact_text},
        "snapshot_sha256": info["snapshot"]["sha256"],
        "deps_fingerprint": info["deps_fingerprint"],
        "draft_digest": digest,
        "validation_runs": validation.get("runs_summary", ""),
        "taskmine_version": __version__,
    }
    if notes:
        origin["notes"] = notes
    doc = {
        "version": "1.0",
        "metadata": {
            "author_name": "taskmine",
            "category": "software-engineering",
            "tags": ["taskmine", cfg.repo_name.split("/")[-1], cand.profile],
            "taskmine": origin,
        },
        "verifier": {"timeout_sec": float(prof.timeout_sec + 300)},
        "agent": {"timeout_sec": float(cfg.agent_timeout_sec)},
        "environment": {
            "build_timeout_sec": 1800.0,
            "cpus": prof.cpus,
            "memory_mb": prof.memory_mb,
            "storage_mb": cfg.storage_mb,
        },
    }
    _write(out / "task.toml", tomlw.dumps(doc))
    return {"id": cand.id, "sources": sources, "notes": notes, "f2p": len(f2p), "p2p": len(p2p)}
