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
| `src/taskmine/contract.py` | the interface contract (new names, modules, exact text, calls) and the static source-text check |
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

Each entry says what was decided, why, and what it costs. They are measured where a number is
given; "measured" means on the dogfood repository (a 28,000-commit TypeScript monorepo) unless it
says otherwise. Before changing one of these, read its reason. Several look like accidents and are
not.

### What a task is

**Coding tasks only.** The agent changes the repository and tests grade it. Review tasks (approve or
reject a diff) are out of scope. They need labelled review history, not tests, and other tools make
them.

**The repository at the base commit, with no history.** The image holds the files of the commit
before the change and a fresh one-commit git repository tagged `taskmine-base`.
- Why: the merged answer is in the history (the merge commit, later commits, reflogs, packed
  objects). A clone with history leaks the answer.
- How it is checked: the snapshot is re-read after it is written, and every file is compared with
  the base commit's blob.
- Cost: an agent cannot use `git log` or `git blame` for context.

**The instruction is assembled, never written by a model.** Its parts:
- the PR title;
- the issue the PR closed, quoted, only when it was opened before the PR and passes the leak check;
- the fail-to-pass test names, verbatim;
- the interface contract.

Why:
- A model-written instruction is not reproducible.
- It can paraphrase the fix into the prompt.
- It reads as generated text.

**PR bodies are never used.** They describe the fix ("this adds X to Y by calling Z"). Using them
leaks the solution.

**The interface contract.**
- What it is: the names, modules and exact strings that the hidden tests use and that the change
  adds, and the first call of each new name in the tests, as written.
- Why: without it, a correct fix under a different function name fails every test, and the task
  measures guessing.
- Measured on the eight synthetic language repositories (`demo/langs`): before modules, short
  names, calls and formatted text were covered for languages other than JavaScript, Claude Code and
  Codex failed the same task in Python, Rust, C#, Ruby and PHP. Each time the cause was something
  the instruction did not say: the module the tests import, a 2-letter method name, the argument
  order, or a message the change formats with `{code.strip()}`.
- Calls show the test's arguments, never its expected result. Tests are hidden for their
  assertions, not for the shape of the interface they use.
- Bounded at 150 checks (`contract.MAX_CHECKS`). Without the bound, a large test fixture stalled a
  build.
- Contract strings that are lines of the gold patch's code are dropped. Quoting them would hand the
  agent the fix.

**The leak check.** The quoted issue and the contract are checked against the gold patch.
- Paths the agent must touch anyway are allowed (`leak.stated_paths`), and comment lines are
  skipped.
- Without these two, issues were rejected only for naming the file they were about.

### Validation (what makes a draft a task)

**Base twice, gold three times.**
- A test is fail-to-pass only if it fails in both base runs and passes in all three gold runs.
- One run each let flaky tests through as fail-to-pass, and a flaky required test makes the task
  unsolvable at random.
- The counts are the `verify` defaults (`--base-runs`, `--gold-runs` on `validate`).

**The source-text probe (Node).**
- It re-runs the gold state with a preload (`templates/textprobe.mjs`). The preload hands test
  code the base commit's text whenever it reads a changed source file.
- A fail-to-pass test that then fails was checking the source text (a regex over a file, a string
  the code must contain), not behaviour. Such tests are never required, because they reward
  spelling the fix the same way.
- Measured: 52% of all fail-to-pass tests were source-text tests.
- Other runtimes fall back to a static, file-level check, which is coarser.

**Harbor's nop agent must score 0, and its oracle agent 1.**
- This is the last gate, run through the task's real `test.sh`.
- It catches a grader that passes an empty patch, and a gold patch that does not apply to the
  snapshot.

**JUnit XML is the only interface to a test runner.**
- Every mainstream runner writes it, which is what makes taskmine language-agnostic.
- pytest writes the file path into its report only with `-o junit_family=xunit1`, so the starter
  recipe sets that.

**The grader restores test-side files before it runs the tests.** An agent can edit or delete the
hidden tests' neighbours (fixtures, helpers, configuration). The grader puts back every test-side
file from `taskmine-base` first.

**The reward file starts at 0.** `test.sh` writes 0 before anything else, so a missing python3 or
a crashed grader fails closed.

**Deterministic output.**
- Snapshots, drafts and tasks carry digests (`snapshot_sha256`, `draft_digest`,
  `deps_fingerprint`).
- Measured: a sample of 10 tasks rebuilt byte for byte on a second machine.
- Why: a benchmark nobody can rebuild is not a benchmark, and the digests make reviews of a
  dataset change mechanical.

### Mining

**A blob-filtered clone (`--filter=blob:limit=9m`).**
- Large repositories clone in a fraction of the time.
- Blobs over 9 MB stay on GitHub.
- A blob the clone does not hold gets `MISSING_SIZE`, which sorts above every size cap. While the
  clone's filter is above the snapshot's `max_file_bytes` (5 MB by default), a draft from a filtered
  clone is byte-identical to one from a full clone.
- Measured: a 1 MB cap dropped real source files from snapshots, so the cap is a recipe setting.

**Every exclusion has a code and a reason.** `scan`, `build`, `validate` and `finalize` record why
each PR was left out (`no-test-change`, `source-text-only`, `nop-not-zero` and the rest). The
funnel in `report.md` is built from those records, so it can be audited.

### Where trials run

**AgentCore Runtime is the default; a Docker host is the second option.**
- Docker needed a big host for parallel validation: image builds, a gateway, address pools, disk.
  People who had made the same switch for review tasks advised it: Docker had been too big, too
  complicated and too slow.
- On AgentCore the laptop needs no Docker. Each trial is a fresh microVM, and parallelism is a
  quota, not a machine size.
- Docker stays for x86_64-only dependencies and for machines without AWS.

**Reuse PR #120 instead of writing an AgentCore environment.**
- The AgentCore environment for Harbor in aws-samples/sample-gen-ai-evaluations-workshop PR #120
  already has the hard parts:
  - an agent in an empty network namespace behind an allowlist proxy;
  - per-phase credentials;
  - checked downloads;
  - staged grading;
  - a teardown.
- Its author had tested it.
- Writing a second one would split the effort and the bug reports.

**Vendored byte for byte, with a test that checks each file's git blob.**
- The PR is not merged and not packaged, so it cannot be a dependency.
- A byte-identical copy makes an update mechanical (copy, replace the blob table, run the tests),
  and it stops anyone from "fixing" upstream code here silently.
- taskmine's changes live outside `pr120/`:
  - `taskmine_env.py`: the platform version and the V2 runtime name;
  - `taskmine_ac.py`: bounded prebuild, and the validation build/run/remove;
  - `agentcore/__init__.py`: task rewrites and Harbor flags.

**Region `us-east-1`, prefix `evals-tm-`.**
- PR #120 creates its clients and endpoints for `us-east-1` at import.
- Its teardown refuses any prefix that does not start with `evals-`, and it touches only
  resources of its prefix. A distinct prefix keeps taskmine's resources apart from anyone else's
  PR #120 deployment in the same account.

**Short-lived, scoped credentials in the agent's session, not a gateway.**
- Nothing on the operator's machine is reachable from an AgentCore microVM, so the Docker path's
  gateway cannot work here.
- The agent gets PR #120's agent-role credentials instead:
  - they last 1 hour;
  - they allow only `bedrock:InvokeModel` and `InvokeModelWithResponseStream` on the models named
    at setup, plus the one Codex model on bedrock-mantle;
  - they are written by root into a per-phase file the agent cannot change.
- The grader phase switches to the runtime role's credentials and the agent's processes are
  killed first.
- Cost: an agent could spend model tokens on the allowed models for an hour, and nothing more.

**Rewrites of each task for AgentCore.** `prepare()` makes the copies; the source tasks never
change. Each rewrite has a reason:
- **The agent's network is an allowlist of the two Bedrock hosts.** PR #120 refuses `public` for
  the agent: outside the firewall the agent could read the runtime role from the metadata service.
- **The grader gets no network.** taskmine's grader needs none, so it gets none.
- **`/app` is owned by uid 2000.** PR #120 runs the agent as `agent` (uid 2000), and the agent
  must be able to change the repository. Cost: `chown -R` copies `/app` into one more image layer.
- **The harnesses are baked in, at pinned versions:**
  - Claude Code 2.1.327, Codex 0.162.1, Strands Agents 1.54.0 in `/installed-agent/strands/venv`;
  - OpenCode 1.19.6, which PR #120's layer adds.

  Harbor installs harnesses as the agent user, and behind the firewall those installs fail.
  `skip_install=true` makes Harbor use what the image has.
- **Docker Hub base images come from `public.ecr.aws/docker/library`.** CodeBuild shares egress
  IPs and hits Docker Hub's anonymous pull limit. The mirror serves the same digests, so the base
  stays pinned.

**Harbor 0.27.0, though PR #120 tested 0.26.0.** The owner chose 0.27.0, and the environment's API
surface was compared before switching:
- the 9 abstract methods;
- the `exec` signature;
- the phase network policy API;
- `_resolve_user` and `_merge_env`;
- the verifier command format `(/tests/test.sh) > ... 2>&1`, which PR #120's staged grader
  matches.

The smoke run (oracle 1.0, nop 0.0) and the bench ran on 0.27.0.

**Grading is "unsealed" on AgentCore for now.**
- PR #120 seals grading only for a `test.sh` that supports `--stage untrusted|trusted`.
  taskmine's does not yet, so the test run happens in the trial's own session as the agent user.
  That is the same trust level as Harbor on a Docker host, where the verifier runs in the agent's
  container.
- The grader still restores test-side files first, and runs with no network.
- Next step: a staged `test.sh`, whose untrusted stage runs the tests and whose trusted stage
  reads JUnit and writes the reward.

**Images are built before any agent starts.**
- PR #120 locks a build per image within one process. `bench` runs one Harbor process per agent,
  so four agents would build the same image four times and race to create its runtime.
- `taskmine_ac.py prebuild` builds each image once, a bounded number at a time (`--build-jobs`).
  PR #120's own `setup.py prebuild` starts every build at once.

**`verify` and `bench` share images.**
- PR #120 names an image by a hash of the environment directory, its layer and the firewall files,
  prefixed with the task name.
- The validation executor builds from the draft's directory name, which is the final task's name,
  with the same rewritten Dockerfile. `bench` therefore finds the image that `verify` built.

**One fresh session per validation run.** The Docker executor runs every base, gold, probe, nop and
oracle run in a new container (`docker run --rm --network none`). The AgentCore executor does the
same with a new session per run, as the agent user, with an empty allowlist and no credentials.

**V1 is the default; V2 is an explicit option (`--platform V2`).** With AgentCore's own guidance
applied, V2 matched V1 on test time but not on session setup. It was not faster.

Sources:
- AWS Machine Learning Blog, "Architecture patterns for fast session startup on Amazon Bedrock
  AgentCore runtime" (2026-11-13),
  https://aws.amazon.com/blogs/machine-learning/architecture-patterns-for-fast-session-startup-on-amazon-bedrock-agentcore-runtime/
- Its linked guides:
  - https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-v2-optimize.html
  - https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-lifecycle-hooks.html

Why V2 can be slow here:
- V2 restores each session from a snapshot instead of pulling the image.
- Files in the snapshot are fetched block by block the first time a session reads them, "most
  noticeable in the first sessions after you create or update your agent".
- A test run here reads thousands of small files under `node_modules`.

What taskmine applies, and what it does not:

| guidance | in taskmine |
|---|---|
| do the init before the health check passes | yes: PR #120's shim starts the firewall, then answers /ping |
| a build hook; in the memory call, load what sessions need (a RAM-backed file system is part of the memory snapshot) | `TASKMINE_AC_WARMUP=ram`: `/app` (830 MB for the measured task) copied into a tmpfs bound over `/app`, in 1.3-1.7 s |
| in the disk call, do once what a session does first | `ram` and `disk` modes: git, node and python, then the harness files for up to 90 s |
| ship compiled bytecode (`UV_COMPILE_BYTECODE=1`) | no: it only speeds Strands' own imports, not validation |
| snapshot-safe randomness (no programs holding TLS state at snapshot time) | nothing to do: the proxy only relays bytes, and agents start in the session |
| pre-warm sessions before a user engages | not applicable: runs are batch jobs |

How the first comparison went wrong. It was not a paired comparison. Reconstructed from the raw
logs and CloudTrail:
- **Cold V2 only.** Both V2 validations ran on a runtime created minutes before, so their 8 runs
  were its first sessions.
  - V2: 166-217 s per test command.
  - V2 with the disk-only hook: 125-139 s.
- **V1 under heavier load, still fast.** V1 ran with 30 to 48 bench trials of other tasks running
  at the same time, and its first, cold use measured the same as later uses (69-71 s).
- **The bench comparison did not match either.** V2 ran all 4 agents at once (16 sessions) on
  fresh runtimes; V1 ran in three smaller batches.
- **The runtimes were gone.** The low-level `validate` deletes an accepted draft's runtime unless
  `--keep-images`, so those V2 runtimes no longer existed for a warm re-run. (`verify` keeps them.)

The paired measurement (2026-11-25, one draft of the dogfood repository, 8 runs per variant):
- Each variant ran taskmine's real `validate_one`: the same stages, at most 3 runs at once.
- Both variants ran at the same time, on images that already existed.
- Round 1 was the RAM-tuned runtime's first sessions; round 2 ran right after.

| round | variant | verdict | errors | session setup per run, p50 (max) | test command, p50 (range) | end to end |
|---|---|---|---|---|---|---|
| 1 | V1 | accepted, f2p 2 / p2p 6 | 0 | 5.2 s (6.5) | 70.5 s (69.6-71.6) | 307 s |
| 1 | V2 + ram hook | accepted, f2p 2 / p2p 6 | 0 | 7.9 s (14.2) | 73.2 s (70.4-75.1) | 330 s |
| 2 | V1 | accepted, f2p 2 / p2p 6 | 0 | 4.8 s (5.5) | 69.6 s (68.5-71.2) | 301 s |
| 2 | V2 + ram hook | accepted, f2p 2 / p2p 6 | 0 | 7.5 s (13.1) | 73.1 s (70.2-74.0) | 330 s |

- Session setup covers the session start, the uploads of tests/ and solution/, and the download of
  the logs.
- nop scored 0 and oracle 1 in every round, for both variants.
- Tuned V2 had no cold penalty: round 1 equalled round 2, because the repository was in memory.
- It was still about 3 s slower per run to set up, and about 8-10% slower end to end, in both
  rounds.
- Deploying it took 677 s against about 8 s for V1. PR #120's deploy creates the runtime and then
  updates it (to require IMDSv2), and on V2 each step builds a snapshot.

So V1 stays the default for `verify` and `bench`. V2 remains available:
- `--platform V2` alone;
- with `TASKMINE_AC_WARMUP=ram`, or `disk` for the read-only hook.

A V2 runtime is named `<V1 name>_v2`, and a hooked image gets its own tag (`w` plus a hash of the
hook, its mode and the Dockerfile tail). So every variant can serve the same task side by side.
Re-measure if AgentCore's V2 changes: the paired script's method is above.

**The build hook's details:**
- It must answer within the shared 120 s.
- A first version checked its deadline only between files, ran one call to 121 s, and failed the
  runtime's creation. The deadline is now checked between 1 MB reads.
- A hook error is reported in the build log and answered with 200: an optimization never fails a
  runtime.
- In `ram` mode, git in the disk call exits 128. Root runs it in a repository owned by the agent
  user (git's ownership check). The git binary is read anyway, which is what the call is for.

**Validation runs at once on AgentCore.** Every validation run is its own microVM, so an
executor with `parallel_runs` runs the repeat base and gold runs together, and then nop with
oracle.
- Measured: 501 s instead of about 830 s for one draft.
- A test checks that the verdicts and the recorded run order equal the sequential ones.
- The Docker and local executors keep running one at a time: they share one machine.

**OpenCode and Strands run on Claude, not GPT-6.1 Terra.**
- In this account, Bedrock Converse refuses Terra: `ValidationException: data retention mode
  'none' is not available for this model`. That was measured with a plain one-message Converse
  call and the scoped agent credentials.
- It is an account-level data retention setting, which taskmine does not change.
- Codex reaches Terra through bedrock-mantle (the Responses API), which is not affected.
- So OpenCode and Strands run on Sonnet 5.5. Three harnesses on one model also make a cleaner
  comparison of the harnesses themselves.

**Strands needs three things from taskmine that Harbor does not supply:**
- **A factory.** Harbor's runner imports `agent:create_agent` by default and ships no such
  module. `templates/taskmine_strands.py` gives Strands the runner's shell and file editor and a
  short system prompt.
- **Its Bedrock model, built in the session.**
  - On AgentCore the credentials exist only inside the session, so Harbor resolves no provider
    and falls back to its litellm path, which is not installed.
  - taskmine passes the model ID in `factory_kwargs`, and the factory builds a `BedrockModel`
    with the session's credentials.
- **Room to answer, and a heartbeat:**
  - 32,000 output tokens per call. The default stopped 4 of 8 trials with
    `MaxTokensReachedException`.
  - A line on stdout a minute, because AgentCore ends a command that sends no byte for 15
    minutes. The runner prints nothing, and 1 of 8 trials was cut this way.

**The one place an agent holds a cloud credential.** taskmine's rule is that an agent never holds
a cloud credential, and on a Docker host the gateway keeps it that way. On AgentCore there is no
host a microVM can reach, so the agent gets PR #120's short-lived, model-scoped credentials. This
is the only exception, and its limits are above. If AgentCore gains a way to sign requests outside
the session, use it.

**x86_64 is not on AgentCore yet.**
- AgentCore microVMs are arm64.
- x86_64 on AgentCore means EC2 instances from a capacity provider (`LINUX_X86_64`) in a VPC,
  with subnets, security groups and an operator role. That is new network infrastructure in the
  user's account.
- Until that is designed and reviewed, a task that needs x86_64 runs on a Docker host.

**The models.**
- Claude Code runs on Claude (Sonnet 5.5).
- Codex reaches GPT models on bedrock-mantle, which takes the bare model ID
  (`openai.gpt-6.1-terra`), not a `us.` inference profile, so `codex_model()` strips the prefix.
  PR #120 allows one Codex model per setup.
- OpenCode and Strands call Bedrock Converse with the `us.` inference profile.
- The non-Claude agents all use GPT-6.1 Terra, so their results compare on one model.

**`AWS_BEARER_TOKEN_BEDROCK` is removed from the harness environment.** With it set, PR #120's
bootstrap builds the grader policy for the wrong model ARNs, and judge or grader calls are refused.

### The Docker path

**A gateway, so containers never hold a credential.**
- Claude Code runs in its own Bedrock mode against the gateway (`ANTHROPIC_BEDROCK_BASE_URL`,
  `CLAUDE_CODE_SKIP_BEDROCK_AUTH=1`). The gateway signs each request with SigV4 and streams
  Bedrock's answer back unchanged.
- Agents that speak an OpenAI API (Codex, OpenCode, Strands) go through LiteLLM on the gateway.
- The gateway serves only private and loopback clients.
- Containers must be blocked from the instance metadata endpoint
  (`iptables -I DOCKER-USER -d 169.254.169.254 -j DROP`), or the gateway's isolation means
  nothing.

**LiteLLM is started through its own CLI, and taskmine waits until it answers.**
`python -m litellm` does not start the proxy. A gateway that printed its URL before LiteLLM was up
failed the first bench.

**Build groups are interleaved.**
- The first draft of each dependency fingerprint builds alone, and the rest of its group reuses
  Docker's cache (`BuildGate`).
- Sorted by group, every worker waited behind one install: measured, 75% of a 32-vCPU box sat
  idle. Round-robin keeps workers on different groups.
- On AgentCore there is no shared cache, so the gate is off (`shares_build_cache = False`).

### Benchmarks and results

**An error is not a failure.**
- A trial that ended in an exception (an install failed, a model refused the credentials, a
  timeout) counts as an error, not as unsolved.
- Why: infrastructure problems must be visible, not hidden in a lower score.

**`oracle` and `nop` are built-in bench agents.** They sanity-check a task set on a new
environment before real agents spend tokens.

**`results` shows the run that finished last.** It picks by the mtime of `results.json`. Sorting by
name once showed an older run ("smoke").

### Engineering

**The core is standard library only.**
- `mine` runs anywhere Python, git and gh do.
- Heavy dependencies are isolated:
  - the gateway (`botocore`, `litellm[proxy]`) is an optional extra;
  - the AgentCore harness has its own venv (Harbor, boto3 >= 1.44.7), which
    `taskmine agentcore setup` makes with uv.

**Tests use real git repositories and real test runs, never mocks.** The failures that matter come
from git (filtered clones, merge shapes, dubious ownership) and from test runners (JUnit dialects,
probes). A mock of either tests the mock.

**The demo is a real terminal session.**
- `demo/record.py` types each command and runs it in a pseudo-terminal, recording output with
  the times it arrived. Nothing is edited.
- `--idle 0` keeps every interval as it happened. Any shortening is stated next to the video.
