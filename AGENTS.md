# AGENTS.md

Notes for coding agents, and for people, working on taskmine itself. README.md is for people who
use taskmine; this file is for changing it.

taskmine turns a repository's merged pull requests into Harbor (Terminal-Bench 2.x) coding tasks,
keeps only the ones execution proves, and benchmarks coding agents on them. The core is the Python
standard library; Docker, Harbor and the cloud are only needed to verify tasks and run agents.

## Layout

| path | role |
|---|---|
| `src/taskmine/cli.py` | the entry point (`taskmine`, `python -m taskmine`) and the pipeline commands: scan, meta, build, validate, finalize, check, report |
| `src/taskmine/workflow.py` | the everyday commands: login, init, agents, gateway, mine, verify, bench, results; they chain the pipeline commands for a project in `.taskmine/` |
| `src/taskmine/config.py` | the recipe, `taskmine.toml`: every key and its type; unknown keys are errors |
| `src/taskmine/vcs.py` | the only module that runs git, and only to read |
| `src/taskmine/scan.py` | change units, the split into test side and gold side, and every refusal code |
| `src/taskmine/forge.py` | pull request titles and closing issues, through `gh api graphql` |
| `src/taskmine/snapshot.py` | the sealed, reproducible `repo.tar.gz` of the base commit, and the seal check |
| `src/taskmine/deps.py` | dependency manifests copied before the snapshot, so Docker shares the install layer |
| `src/taskmine/contract.py` | the interface contract (new names, modules, exact text) and the static source-text check |
| `src/taskmine/instruction.py` | `instruction.md`, from named sources only |
| `src/taskmine/leak.py` | the answer-absence check on an instruction |
| `src/taskmine/emit.py` | drafts, the draft digest, and finished tasks (`task.toml`, `expected.json`) |
| `src/taskmine/validate.py` | the Docker and local executors and the fail-to-pass / pass-to-pass decision |
| `src/taskmine/check.py`, `report.py` | static checks on finished tasks; the dataset funnel |
| `src/taskmine/agents.py`, `bench.py` | the agent catalog and Harbor arguments; bench runs and the results table |
| `src/taskmine/gateway.py` | the model gateway on the Docker host (SigV4 pass-through, LiteLLM endpoint) |
| `src/taskmine/project.py`, `tomlw.py`, `globs.py` | `.taskmine/` files; a small TOML writer; the path matcher |
| `src/taskmine/agentcore/` | running verify and bench on Amazon Bedrock AgentCore Runtime (`--env agentcore`, `--platform V1/V2`); `pr120/` is a vendored upstream harness, see `PROVENANCE.md` |
| `src/taskmine/templates/` | files copied into every task: `Dockerfile.tmpl`, `solve.sh`, `test.sh`, `grader.py`; `textprobe.mjs`, a Node preload used only during validation; `taskmine_strands.py`, the factory Harbor's Strands agent runs with |
| `tests/` | the test suite; `tests/support.py` builds real git repositories |
| `scripts/release.py` | release checks: tag against version, release notes, contents of the wheel and sdist |
| `demo/` | the terminal recorder behind `docs/demo.cast` |
| `docs/`, `examples/` | the dogfood report and the recipe it used |

`templates/grader.py` runs inside task containers, on whatever `python3` the image has: keep it
to the standard library and to Python 3.7 syntax, and do not import taskmine from it. The glob
matcher exists three times (`globs.py`, `grader.py`, `textprobe.mjs`); `tests/test_globs.py` runs
all three over the same cases, so change them together.

## Running the tests

```sh
PYTHONPATH=src python3 -m unittest discover -s tests          # the whole suite
PYTHONPATH=src python3 -m unittest discover -s tests -p test_scan.py   # one module
ruff check .                                                  # lint, configured in pyproject.toml
```

The suite has about 210 tests and takes 45 to 50 seconds on a laptop. Two tests are most of
that, because they run the whole pipeline with real `node --test` runs: `test_end_to_end.py`
(about 15 s) and the parallel-validation test in `test_agentcore.py` (about 20 s). Everything
else takes about 12 s. It needs Python
3.11 or later and git. Some tests skip, with the reason, when a tool is missing: Node 24 for the
end-to-end and text-probe tests, botocore and openssl for the gateway signer test. No test needs
Docker, Harbor, AWS credentials or a model, and none may call them. CI runs the suite on Linux
with Python 3.11, 3.12 and 3.13 and on macOS with 3.12, plus ruff and a build check
(`.github/workflows/ci.yml`).

## Conventions

- **The core stays standard library only.** `[project] dependencies` is empty. botocore,
  LiteLLM, boto3 and Harbor are optional groups (`gateway`, `agentcore`) and are imported inside
  the function that needs them, never at module level.
- **Tests use real things.** Real git repositories (`tests/support.py`: `GitRepo` drives a work
  tree, `History` writes a whole history with one `git fast-import`), real files, real local
  processes and real local servers. No mocks: no `unittest.mock`, no replacing taskmine functions.
  A test that needs a tool skips with a reason when it is absent, and a bug found by a test is
  fixed in its own commit together with that test.
- **Deterministic output.** The same inputs give the same bytes: sorted keys and file lists,
  fixed archive mtimes and owners, seeded samples. A draft built twice has the same digest, and
  that is tested.
- **Every refusal has a code and a sentence.** A unit, draft or task that is left out records
  why, in words a person can act on.
- **Plain English** in messages, docs and comments: short sentences, no em dashes, no emojis,
  no marketing adjectives. Numbers come from runs, not estimates.
- **Commits** are small and say what changed and why; a bug fix and its test go together.

## Adding an agent kind

1. Add a `Kind` to `CATALOG` in `src/taskmine/agents.py`: Harbor's agent name, the providers it
   supports, a one-line note.
2. If it speaks an OpenAI API and runs on Bedrock, add it to `OPENAI_PROTOCOL`, so it reaches
   the model through the gateway's LiteLLM endpoint.
3. In `harbor_args`, say how it reaches its model: gateway URLs and the gateway's generated key,
   or the provider's own API key from the environment. Never an AWS credential.
4. If it should run on AgentCore, add its flags in `src/taskmine/agentcore/`.
5. Add a test that checks its Harbor arguments and that no `AWS_*` secret appears in them
   (`tests/test_workflow.py` and `tests/test_agentcore.py` show the pattern).
6. Add it to `VERIFIED` only after an end-to-end run, and update the agents table in README.md.

## Adding a recipe profile

A profile is one `[[profiles]]` entry in a repository's `taskmine.toml`; taskmine itself needs no
change for a new test runner that can write JUnit XML.

- `run` globs pick the runnable test files; `command` must contain `{files}` and `{junit}`, and
  `display_command` is what the instruction shows.
- `deps` and `install` install dependencies before the snapshot is added, so tasks with the same
  manifests share a Docker layer; `deps_normalize = "npm"` ignores the repository's own versions.
- `text_probe = "node"` for Node runners; other runtimes use the static check.
- If `taskmine init` should write it, extend `_starter_recipe` in `workflow.py` and test that the
  starter recipe loads (`tests/test_project.py`).
- A new recipe key goes into `_SECTIONS` or `_PROFILE_KEYS` in `config.py`, is read with `_get`
  and its type, and gets a test in `tests/test_config.py` and a line in README.md.

## Invariants: never break these

1. **No cloud credential inside a task or agent container.** taskmine never passes one in:
   Harbor arguments, agent environments, task images and `task.toml` never carry
   `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN` or
   `AWS_BEARER_TOKEN_BEDROCK`. With Docker, Bedrock agents reach their model through the gateway,
   which signs on the host. Tests check this; extend them when you add a way to run an agent.
   On AgentCore the runtime itself gives each session short-lived credentials that can call only
   the agent models named at setup; that is the one exception, and widening it is a decision for
   the maintainer (see the end of this file).
2. **`src/taskmine/agentcore/pr120/` stays byte-identical to its upstream.** Do not format, lint
   or edit it; ruff excludes it, and `tests/test_agentcore.py` checks every file against its
   upstream blob. Changes go in taskmine's own adapter next to it.
3. **The snapshot holds the base commit and nothing else**: no `.git`, no blob that exists only
   after the change. `verify_seal` re-reads the archive to prove it.
4. **An instruction contains no part of the answer.** Pull request bodies are never used, and the
   leak check runs before a task is written.
5. **A task is published only with the bytes that were validated** (the draft digest).
6. **The grader fails closed**: the reward is 0 until every required test has passed.

## Releases

See [RELEASING.md](RELEASING.md): SemVer, the version in `src/taskmine/__init__.py` only, a dated
CHANGELOG.md entry, then a `vX.Y.Z` tag that the release workflow turns into a GitHub Release.

## Decisions and why

(Filled in by the maintainer.)
