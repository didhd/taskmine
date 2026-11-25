# Where `pr120/` comes from

`pr120/` is a byte-for-byte copy of the AgentCore environment for Harbor in
[aws-samples/sample-gen-ai-evaluations-workshop](https://github.com/aws-samples/sample-gen-ai-evaluations-workshop)
pull request [#120](https://github.com/aws-samples/sample-gen-ai-evaluations-workshop/pull/120).
- **Commit:** head `09dd8c7422d722301c79f776d76c657f11e6a816`.
- **Upstream folder:** `Workload Specific Evaluations/Coding Assistant/agentcore/`.
- **License:** MIT-0, the same as taskmine.

Only the files the Harbor environment needs were copied. Their paths under `pr120/` are the
upstream paths. Each file's git blob SHA equals the blob in that commit, and
`tests/test_agentcore.py` fails if one changes.

| file | blob |
|---|---|
| `p2_v2.py` | `b3bb1259967dc9688411bc3f1b01db7634f0ecac` |
| `codex-bedrock.toml` | `f01cfca38b4b7dd7c66a237119b37e0cabdb0843` |
| `image/main.py` | `4c7ab9f023fee70e3fc1c7bb21e4a5d7f50878a5` |
| `image/start.sh` | `a5874d84c2127fa333921ec9f212c4063614bbe0` |
| `image/proxy.py` | `9f44c62af5ab68761efa2e1fe4170c2386519e28` |
| `image/as-agent` | `727a8c7c28a6a39db1db7711af5a9dbcaaffcf5f` |
| `image/buildspec.yml` | `94d6893b1bebb64cd911d21f07595d1bbdd8e8e7` |
| `harbor_env/__init__.py` | `c178eb91db9d54b566ba9a8246763b5b8410a6f8` |
| `harbor_env/agentcore_env.py` | `94dc41c1c32dcecb4dea0e4e55dc520fd465c8d2` |
| `harbor_env/setup.py` | `e0ce3d95249a49dfe2e0e115a6452c783fbb4be7` |
| `harbor_env/image/layer.Dockerfile` | `2cacf64fa85039c3752981f38b795d5d852366ba` |
| `harbor_env/image/as-agent-open` | `231f820aeb3d1dd7adbf6dc3cac370298e407756` |
| `harbor_env/image/as-grader` | `307b691b8cda9537d97ef1624d439a2208d1a80e` |
| `harbor_env/image/record-baseline.sh` | `6abb1753eaf76eca58794c80d30adf07655ac56e` |

## What taskmine adds, outside `pr120/`

- `taskmine_env.py`: a subclass that reads the AgentCore platform version (V1 or V2) for new
  runtimes from `TASKMINE_AC_PLATFORM`. Upstream's `deploy` always uses V1.
- `taskmine_ac.py`: `prebuild`, which builds and deploys each task's image a bounded number at a
  time. Upstream's `setup.py prebuild` starts every build at once.
- `__init__.py` (taskmine's adapter):
  - rewrites each task for AgentCore: an allowlist network for the agent, no network for the
    grader, the repository owned by the agent user, harnesses baked into the image, and Docker
    Hub base images taken from AWS's ECR Public mirror;
  - passes upstream's tested Harbor flags for each harness.

## Updating

1. Copy the files again from the new upstream commit.
2. Replace the blob table above and `PR120_BLOBS` in `tests/test_agentcore.py`.
3. Run the tests.
