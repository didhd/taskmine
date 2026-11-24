"""The harness-venv side of `taskmine agentcore` (needs Harbor and boto3 >= 1.44.7, so it is not imported by taskmine).

    python taskmine_ac.py prebuild [--jobs N] TASK_DIR...   build and deploy each task's image, N at a time
    python taskmine_ac.py build ENV_DIR NAME                one image, for `taskmine verify --executor agentcore`
    python taskmine_ac.py run --tag T --tests D --out D ... one validation run in a fresh session
    python taskmine_ac.py remove TAG                        delete one image's runtime and image

prebuild and build print JSON lines on stdout. The images and runtimes are PR #120's
(ensure_runtime), with the platform version from TASKMINE_AC_PLATFORM (see taskmine_env.py).
"""

from __future__ import annotations

import argparse
import base64
import binascii
import concurrent.futures as cf
import hashlib
import io
import json
import os
import sys
import tarfile
import time
from pathlib import Path

import taskmine_env  # noqa: F401  (applies TASKMINE_AC_PLATFORM to p2.deploy)
import p2_v2 as p2
from harbor_env.agentcore_env import ensure_runtime, image_key


def prebuild(task_dirs: list[str], jobs: int) -> int:
    def one(d: str) -> dict:
        env = Path(d) / "environment"
        tag = image_key(env, Path(d).name, False)
        t0 = time.time()
        try:
            r = ensure_runtime(env, tag, log=lambda *a: None)
            r.update(task=Path(d).name, ok=True)
        except Exception as e:  # one task's failed build must not stop the others
            r = {"task": Path(d).name, "tag": tag, "ok": False, "error": p2.mask(f"{type(e).__name__}: {e}")[:2000]}
        r["secs"] = round(time.time() - t0, 1)
        print(json.dumps(r), flush=True)
        return r

    with cf.ThreadPoolExecutor(max(1, jobs)) as ex:
        results = list(ex.map(one, task_dirs))
    return 0 if all(r["ok"] for r in results) else 1


def build(env_dir: str, name: str) -> int:
    """One image for `taskmine verify`: the same key a bench of the finished task computes, so it is built once."""
    tag = image_key(Path(env_dir), name, False)
    t0 = time.time()
    try:
        r = ensure_runtime(Path(env_dir), tag, log=lambda *a: print(*a, file=sys.stderr, flush=True))
        r.update(ok=True)
    except Exception as e:
        r = {"tag": tag, "ok": False, "error": p2.mask(f"{type(e).__name__}: {e}")[:4000]}
    r["secs"] = round(time.time() - t0, 1)
    print(json.dumps(r), flush=True)
    return 0 if r["ok"] else 1


def _put_dir(s: "p2.Session", src: Path, dest: str) -> None:
    """A directory into the session, root-owned and world-readable (the agent user reads it, never writes it)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        tf.add(str(src), arcname=".")
    tmp = f"/run/tm-up-{os.urandom(6).hex()}.tgz"
    s.put(tmp, buf.getvalue(), "0600", "root")
    q = p2.shq(dest)
    out, code, _ = s.cmd(f"rm -rf {q} && mkdir -p {q} && tar xzf {tmp} -C {q} --no-same-owner && rm -f {tmp} && "
                         f"chown -R root:root {q} && chmod -R a+rX {q} && echo ok")
    if code != 0 or not out.strip().endswith("ok"):
        raise RuntimeError(f"upload {src} -> {dest} failed ({code}): {out[-400:]}")


def _fetch(s: "p2.Session", produce: str) -> bytes:
    """stdout of `produce`, checked against its sha256 (PR #120's download, which also retries a cut-short answer)."""
    tmp = f"/run/tm-dl-{os.urandom(6).hex()}"
    cmd = (f"( {produce} ) > {tmp} && echo \"sha=$(sha256sum < {tmp} | cut -c1-64)\" && base64 -w0 < {tmp}; "
           f"rc=$?; rm -f {tmp}; exit $rc")
    for _ in range(3):
        out, code, _ = s.cmd(cmd, 600)
        head, _, body = out.partition("\n")
        try:
            data = base64.b64decode(body.strip())
        except binascii.Error:
            continue
        if code == 0 and head.strip() == f"sha={hashlib.sha256(data).hexdigest()}":
            return data
    raise RuntimeError("the download from the session arrived cut short 3 times")


def run(a) -> int:
    """One validation run in a fresh session: what `docker run --rm --network none ... bash /tests/test.sh` does
    on a Docker host, with the commands run as the agent user in PR #120's empty network namespace."""
    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    s = p2.Session(a.tag)
    log = out_dir / "docker.log"
    try:
        first, _, _ = s.start("cat /run/fw.status")
        if not first.startswith("OK"):
            log.write_text(f"taskmine: firewall not ready in the session: {first[:300]}\n")
            return 1
        p2.set_phase(s, "", {"AWS_REGION": p2.R})  # an empty allowlist (no network) and no credentials
        _put_dir(s, Path(a.tests), "/tests")
        if a.solution:
            _put_dir(s, Path(a.solution), "/solution")
        if a.probe:
            s.cmd("mkdir -p /taskmine-probe")
            s.put("/taskmine-probe/" + Path(a.probe).name, Path(a.probe).read_bytes(), "0644", "root")
        s.cmd("rm -rf /logs/verifier && install -d -m 0755 -o agent -g agent /logs/verifier")
        exports = "".join(f"export {k}={p2.shq(v)}\n" for k, v in (kv.split("=", 1) for kv in a.env or []))
        script = exports + "cd /app || exit 1\n"
        if a.solution:
            script += ("bash /solution/solve.sh > /logs/verifier/solve.log 2>&1 || "
                       "{ echo solve-failed > /logs/verifier/solve-failed; exit 0; }\n")
        script += "bash /tests/test.sh\n"
        out, code, secs = s.cmd(f"/opt/firewall/as-agent {p2.shq(script)} </dev/null; rc=$?; pkill -KILL -u agent; exit $rc",
                                a.timeout, 1)
        log.write_text(out + f"\ntaskmine: exit {code} after {secs} s in session {s.sid}\n")
        data = _fetch(s, "cd /logs/verifier && tar czf - . 2>/dev/null; exit 0")
        skipped, note = p2.extract_session_tar(data, out_dir)
        if skipped or note:
            with log.open("a") as f:
                f.write(f"taskmine: skipped {skipped[:5]} {note or ''}\n")
        return 0
    except Exception as e:
        with log.open("a") as f:
            f.write(f"\ntaskmine: {p2.mask(f'{type(e).__name__}: {e}')[:2000]}\n")
        return 1
    finally:
        s.stop()


def remove(tag: str) -> int:
    """Delete one image's runtime and image (taskmine's own prefix only)."""
    r = p2.find_runtime(tag)
    if r and r["agentRuntimeName"].startswith(p2.Names.runtime("")) and p2.Names.prefix.startswith("evals-"):
        p2.ctl.delete_agent_runtime(agentRuntimeId=r["agentRuntimeId"])
    try:
        p2.ecr.batch_delete_image(repositoryName=p2.Names.repo(), imageIds=[{"imageTag": tag}])
    except Exception:
        pass
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prebuild")
    p.add_argument("--jobs", type=int, default=6)
    p.add_argument("tasks", nargs="+")
    p = sub.add_parser("build")
    p.add_argument("env_dir")
    p.add_argument("name")
    p = sub.add_parser("run")
    p.add_argument("--tag", required=True)
    p.add_argument("--tests", required=True)
    p.add_argument("--solution")
    p.add_argument("--probe")
    p.add_argument("--env", action="append")
    p.add_argument("--out", required=True)
    p.add_argument("--timeout", type=int, default=1800)
    p = sub.add_parser("remove")
    p.add_argument("tag")
    a = ap.parse_args()
    if a.cmd == "prebuild":
        return prebuild(a.tasks, a.jobs)
    if a.cmd == "build":
        return build(a.env_dir, a.name)
    if a.cmd == "run":
        return run(a)
    if a.cmd == "remove":
        return remove(a.tag)
    return 2


if __name__ == "__main__":
    sys.exit(main())
