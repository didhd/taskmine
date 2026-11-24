#!/usr/bin/env python3
"""AWS setup for harbor_env: bootstrap, image prebuilds, and teardown.

  setup.py bootstrap [--agent-model ID]...   p2_v2 bootstrap plus an agent-role policy for each agent model
  setup.py policies  [--agent-model ID]...   print that policy
  setup.py prebuild  TASK_DIR...             build and deploy the images of the tasks
  setup.py resources                         list every AWS resource of the prefix
  setup.py teardown                          delete them (use this, not p2_v2.py cleanup)
  setup.py stop-sessions TRIAL_DIR...        stop the sessions of trials that hung

Prefix: --prefix, else $P2_PREFIX, else evals-hb-.
"""
import argparse, json, os, pathlib, sys, time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import p2_v2 as p2  # noqa: E402

AGENT_POLICY_NAME = "harbor-agent-models"


def model_arns(model):
    p = p2.bedrock.get_inference_profile(inferenceProfileIdentifier=model)
    return [p["inferenceProfileArn"]] + [m["modelArn"] for m in p["models"]]


def policy_agent_models(models):
    arns = [a for m in models for a in model_arns(m)]
    return {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"], "Resource": list(dict.fromkeys(arns))}]}


def task_env_dirs(task_dir):
    from harbor.models.task.task import Task
    from harbor.models.task.verifier_mode import VerifierEnvironmentMode, resolve_task_verifier_mode, resolve_verifier_environment_definition
    t = Task(task_dir)
    out = [(t.paths.environment_dir, False)]
    if resolve_task_verifier_mode(t.config) == VerifierEnvironmentMode.SEPARATE:
        out.append((resolve_verifier_environment_definition(t.config, t.paths).directory, True))
    return t.short_name, out


def runtimes():
    out, kw = [], {}
    while True:
        r = p2.ctl.list_agent_runtimes(maxResults=100, **kw)
        out += [x for x in r["agentRuntimes"] if x["agentRuntimeName"].startswith(p2.Names.runtime(""))]
        if not r.get("nextToken"):
            return out
        kw = {"nextToken": r["nextToken"]}


def roles():
    return [r["RoleName"] for page in p2.iam.get_paginator("list_roles").paginate() for r in page["Roles"]
            if r["RoleName"].startswith(p2.Names.prefix)]


def log_groups():
    out = []
    for pre in (p2.Names.loggroup(), "/aws/bedrock-agentcore/runtimes/" + p2.Names.runtime("")):
        for page in p2.logs.get_paginator("describe_log_groups").paginate(logGroupNamePrefix=pre):
            out += [g["logGroupName"] for g in page["logGroups"]]
    return out


def resources():
    rep = {"runtimes": [f'{r["agentRuntimeName"]} {r["status"]}' for r in runtimes()], "roles": roles(), "log_groups": log_groups()}
    try:
        imgs = [d for p in p2.ecr.get_paginator("describe_images").paginate(repositoryName=p2.Names.repo()) for d in p["imageDetails"]]
        rep["ecr"] = [f"{p2.Names.repo()} ({len(imgs)} images)"] + \
            [f'  {",".join(d.get("imageTags", ["-"]))} {d["imageSizeInBytes"] // 2**20} MB compressed' for d in imgs]
    except p2.ecr.exceptions.RepositoryNotFoundException:
        rep["ecr"] = []
    rep["codebuild"] = [p["name"] for p in p2.cb.batch_get_projects(names=[p2.Names.project()])["projects"]]
    try:
        p2.s3.head_bucket(Bucket=p2.bucket())
        rep["s3"] = [p2.mask(p2.bucket())]
    except Exception:
        rep["s3"] = []
    return rep


def teardown(wait_secs=900):
    assert p2.Names.prefix.startswith("evals-"), p2.Names.prefix
    failed = []

    def attempt(what, fn):
        try:
            fn()
            p2.log("deleted", p2.mask(what))
        except Exception as e:
            failed.append(what)
            p2.log(f"FAILED {p2.mask(what)}: {type(e).__name__}: {p2.mask(str(e))[:200]}")

    for r in runtimes():
        if r["status"] != "DELETING":
            attempt(f'runtime {r["agentRuntimeName"]}', lambda r=r: p2.ctl.delete_agent_runtime(agentRuntimeId=r["agentRuntimeId"]))

    def del_repo():
        try:
            p2.ecr.delete_repository(repositoryName=p2.Names.repo(), force=True)
        except p2.ecr.exceptions.RepositoryNotFoundException:
            pass
    attempt(f"ecr repository {p2.Names.repo()}", del_repo)

    def del_project():
        if p2.cb.batch_get_projects(names=[p2.Names.project()])["projects"]:
            p2.cb.delete_project(name=p2.Names.project())
    attempt(f"codebuild project {p2.Names.project()}", del_project)

    def del_role(name):
        for pn in p2.iam.list_role_policies(RoleName=name)["PolicyNames"]:
            p2.iam.delete_role_policy(RoleName=name, PolicyName=pn)
        for ap in p2.iam.list_attached_role_policies(RoleName=name)["AttachedPolicies"]:
            p2.iam.detach_role_policy(RoleName=name, PolicyArn=ap["PolicyArn"])
        p2.iam.delete_role(RoleName=name)
    for rn in roles():
        attempt(f"iam role {rn}", lambda rn=rn: del_role(rn))

    def del_bucket():
        b = p2.bucket()
        try:
            p2.s3.head_bucket(Bucket=b)
        except Exception:
            return
        for page in p2.s3.get_paginator("list_object_versions").paginate(Bucket=b):
            objs = [{"Key": o["Key"], "VersionId": o["VersionId"]} for o in page.get("Versions", []) + page.get("DeleteMarkers", [])]
            if objs:
                p2.s3.delete_objects(Bucket=b, Delete={"Objects": objs})
        p2.s3.delete_bucket(Bucket=b)
    attempt(f"s3 bucket {p2.bucket()}", del_bucket)

    for g in log_groups():
        attempt(f"log group {g}", lambda g=g: p2.logs.delete_log_group(logGroupName=g))

    t0 = time.time()  # a runtime delete takes minutes, and its log group can be written until it is gone
    while runtimes() and time.time() - t0 < wait_secs:
        time.sleep(15)
    for g in log_groups():
        attempt(f"log group {g}", lambda g=g: p2.logs.delete_log_group(logGroupName=g))
    return failed, resources()


def stop_sessions(trial_dirs):
    out = []
    for d in trial_dirs:
        f = pathlib.Path(d) / "agentcore.jsonl"
        if not f.exists():
            continue
        ev = [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
        stopped = {e["session_id"] for e in ev if e["event"] == "stop"}
        for e in ev:
            if e["event"] == "session_start" and e["session_id"] not in stopped:
                name = e["runtime"]
                assert name.startswith(p2.Names.runtime("")), name
                arn = p2.ctl.get_agent_runtime(agentRuntimeId=next(r["agentRuntimeId"] for r in runtimes()
                                                                   if r["agentRuntimeName"] == name))["agentRuntimeArn"]
                try:
                    p2.dp.stop_runtime_session(agentRuntimeArn=arn, runtimeSessionId=e["session_id"], qualifier="DEFAULT")
                    res = "stopped"
                except Exception as x:
                    res = f"{type(x).__name__}: {str(x)[:150]}"
                out.append({"trial": pathlib.Path(d).name, "session_id": e["session_id"], "result": p2.mask(res)})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["bootstrap", "policies", "prebuild", "resources", "teardown", "stop-sessions"])
    ap.add_argument("tasks", nargs="*")
    ap.add_argument("--prefix", default=None)
    ap.add_argument("--agent-model", action="append", default=[], help="Bedrock inference profile ID the agent role may invoke")
    a = ap.parse_args()
    p2.Names.prefix = a.prefix or os.environ.get("P2_PREFIX") or "evals-hb-"
    p2.load_models(p2.module_dir())
    if a.cmd == "policies":
        print("### agent role, extra inline policy", AGENT_POLICY_NAME)
        print(p2.mask(json.dumps(policy_agent_models(a.agent_model), indent=1)))
    elif a.cmd == "bootstrap":
        rep = p2.bootstrap()
        if a.agent_model:
            pol = policy_agent_models(a.agent_model)
            p2.iam.put_role_policy(RoleName=p2.Names.agent_role(), PolicyName=AGENT_POLICY_NAME, PolicyDocument=json.dumps(pol))
            rep["agent_models_policy"] = pol
        print(p2.mask(json.dumps(rep, indent=1)))
    elif a.cmd == "prebuild":
        import concurrent.futures as cf
        from harbor_env.agentcore_env import ensure_runtime, image_key
        jobs = []
        for t in a.tasks:
            short, dirs = task_env_dirs(t)
            jobs += [(d, image_key(d, short, v)) for d, v in dirs]
        with cf.ThreadPoolExecutor(len(jobs)) as ex:
            rs = list(ex.map(lambda j: ensure_runtime(j[0], j[1], log=p2.log), jobs))
        print(json.dumps(rs, indent=1))
    elif a.cmd == "resources":
        print(p2.mask(json.dumps(resources(), indent=1)))
    elif a.cmd == "stop-sessions":
        print(json.dumps(stop_sessions(a.tasks), indent=1))
    elif a.cmd == "teardown":
        failed, left = teardown()
        print(p2.mask(json.dumps({"failed": failed, "left": left}, indent=1)))
        if failed or any(left.values()):
            sys.exit("teardown incomplete: re-run it in a few minutes")


if __name__ == "__main__":
    main()
