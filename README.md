# taskmine

**Turn the pull requests your team already merged into a Terminal-Bench task set, then benchmark
coding agents on it.**

Public benchmarks tell you how an agent does on someone else's code. taskmine builds the benchmark
from yours: every merged pull request that changed code together with its tests becomes a
[Harbor](https://github.com/laude-institute/harbor) task (the Terminal-Bench 2.x format), and it is
kept only when execution proves it. The new tests must fail without the change, pass with it in every
repeated run, and Harbor's do-nothing agent must score 0 while its oracle scores 1.

https://github.com/user-attachments/assets/237d1e33-05db-476f-84aa-8eb8b06a2e5a

**About the demo.** It is a real terminal session: every command was typed and then run, and every
line of output is what the command printed. Part 1 ran on a laptop and part 2 on a Docker host.
- Waits longer than 1.5 s were cut to 1.5 s. For example, `bench` took 10 min 22 s, as its own
  `[mm:ss]` stamps show.
- Two whole commands were dropped:
  - a first `gateway` start whose LiteLLM process had not come up;
  - a `results` that showed the older run (since fixed, and re-recorded).

To replay the recording in a terminal, run `asciinema play docs/demo.cast`.

## Quick start

```sh
taskmine login                                   # GitHub, through the GitHub CLI (gh)
taskmine init acme/shop                          # clone it, write a starter recipe (taskmine.toml)
taskmine agents add claude-code --provider bedrock --model us.anthropic.claude-sonnet-5-5
taskmine agents add codex       --provider bedrock --model us.openai.gpt-6.1-terra
taskmine agents add opencode    --provider bedrock --model us.openai.gpt-6.1-terra
taskmine agents add strands     --provider bedrock --model us.openai.gpt-6.1-terra
taskmine mine                                    # merged PRs and the issues they close -> draft tasks
taskmine agentcore setup                         # once: the AWS side (IAM roles, ECR, CodeBuild)
taskmine verify                                  # run every draft on AgentCore; keep what execution proves
taskmine bench --tasks 20                        # every agent over the same tasks, with Harbor
taskmine results                                 # who solved what, and what it cost
taskmine agentcore teardown                      # delete every AWS resource that setup made
```

- `mine` runs anywhere git and gh work.
- `verify` and `bench` run each trial in an
  [Amazon Bedrock AgentCore Runtime](https://docs.aws.amazon.com/bedrock-agentcore/) session, so
  the machine you type on needs no Docker. A Docker host is the second option (`--env docker`,
  below).
- The tasks are plain Harbor task directories, so `harbor run -p .taskmine/out/tasks -a <agent>`
  works on any Harbor environment too.

Install Python 3.11+, git, gh and [uv](https://docs.astral.sh/uv/). `taskmine agentcore setup`
makes its own Python 3.12 venv with Harbor 0.27.0 and boto3. Run taskmine in place with
`PYTHONPATH=src python3 -m taskmine`, or install it with `uv tool install .`. The core is standard
library only.

## What a task is

- **The repository at the commit before the change.** It holds the base commit's files only, with no
  history, so the merged answer is not in the image. The snapshot is re-read after it is written,
  and every file is proven to equal the base commit's.
- **An instruction.** It is built from the pull request title, the issue the PR closed (quoted,
  only when it predates the PR and passes the leak check), the fail-to-pass test names verbatim,
  and the *interface contract*. The contract is the names, modules and exact strings that the
  hidden tests use and that the change adds. No sentence is written by a model, and PR bodies are
  never used, because they describe the fix.
- **Hidden tests and a grader.** Harbor copies them in after the agent finishes. The grader first
  restores every test-side file the agent touched, then runs the tests and reads their JUnit
  report. The reward is 1 only if every fail-to-pass and pass-to-pass test passes.

Tests that pass only because the source *text* changed are never required. These are a regular
expression over a file, or a string the code must contain. A probe re-runs the fixed state while
handing test code the old text of changed files; a test that then fails was checking spelling,
not behaviour. In the dogfood run that was 52% of all fail-to-pass tests.

## Agents

| agent | providers | run end to end |
|---|---|---|
| `claude-code` | `bedrock`, `anthropic` | AgentCore and Docker, Sonnet 5.5 |
| `codex` | `bedrock`, `openai` | AgentCore (bedrock-mantle) and Docker (the gateway's LiteLLM), GPT-6.1 Terra |
| `opencode` | `bedrock`, `anthropic`, `openai` | AgentCore, Sonnet 5.5 |
| `strands` | `bedrock`, `openai` | AgentCore, Sonnet 5.5, with taskmine's default coding agent (`templates/taskmine_strands.py`) |
| `cursor`, `gemini`, `aider`, `goose` | as Harbor supports them | passed to Harbor as they are; not run by taskmine yet |

On 8 tasks from the dogfood set, on AgentCore V1:
- Claude Code, OpenCode and Strands (all on Sonnet 5.5) each solved 3.
- Codex on GPT-6.1 Terra solved 2.
- On V2, all four solved 3.

See [docs/DOGFOOD.md](docs/DOGFOOD.md). In the account we measured, Bedrock Converse refused GPT-6.1
Terra (`data retention mode 'none' is not available for this model`), so OpenCode and Strands ran
on Claude.

## Where trials run

### AgentCore Runtime (default)

The AgentCore side is the Harbor environment from
[aws-samples/sample-gen-ai-evaluations-workshop#120](https://github.com/aws-samples/sample-gen-ai-evaluations-workshop/pull/120).
It is vendored unchanged in `src/taskmine/agentcore/pr120/` (see its PROVENANCE.md).

- **Images.** Each task's image is built on CodeBuild (arm64) and deployed as one AgentCore
  runtime. `verify` builds it once and `bench` reuses it.
- **Sessions.** Every trial, and every validation run, gets a fresh microVM session.
- **The agent's sandbox.** The agent runs as an unprivileged user in an empty network namespace.
  - Its only way out is an allowlist proxy to the two Bedrock endpoints.
  - Its AWS credentials last one hour and can call only the agent models named at setup.
- **The grader.** It runs with no network at all.

taskmine rewrites a copy of each task for this:
- the allowlist in task.toml;
- the repository owned by the agent user;
- Claude Code, Codex and Strands baked into the image (PR #120's layer adds OpenCode);
- Docker Hub base images pulled from AWS's ECR Public mirror.

`taskmine agentcore setup` creates IAM roles, a private ECR repository, a private S3 bucket and a
CodeBuild project in `us-east-1`, all named `evals-tm-*`. `taskmine agentcore teardown` deletes
them, along with the runtimes. Nothing gets a public endpoint: sessions are reached only through
the IAM-signed AgentCore API.

Limits:
- AgentCore microVMs are **arm64**. A task whose dependencies have no arm64 build needs x86_64:
  AgentCore runs that on EC2 instances (a capacity provider in your VPC), which taskmine does not
  set up yet. Use a Docker host instead.
- `--platform V1` is the default (what PR #120 tested) and caps an image near 2 GB compressed.
  `--platform V2` allows 10 GB and restores each session from a snapshot. On this workload V2 was
  slower, because a restored session fetches image blocks on first read and the tests read
  thousands of small files:
  - session start p50 7.8 s against 1.0 s;
  - a validation test run 166-217 s against 69-71 s.

  `TASKMINE_AC_WARMUP=1` adds AgentCore's build hook to pre-read the files (V2 test runs:
  125-139 s). The numbers are in AGENTS.md ("Decisions and why").
- Grading runs in the trial's own session, as on a Docker host. PR #120's staged, sealed grading
  needs a `test.sh --stage` that taskmine's grader does not have yet. Its trials are logged as
  `unsealed` in `agentcore.jsonl`.

### A Docker host

Use `taskmine verify --env docker` and `taskmine bench --env docker` on a machine with Docker and
Harbor (`uv tool install harbor`). Start `taskmine gateway --detach` first.

**The gateway.** Agents run inside task containers, next to code they did not write. taskmine
never hands them a cloud credential. `taskmine gateway` runs on the Docker host with the host's
credentials, an instance role or your profile. It needs `botocore`, and its OpenAI-compatible
endpoint needs `litellm[proxy]`.

- **Claude Code** runs in its own Bedrock mode. `ANTHROPIC_BEDROCK_BASE_URL` points at the gateway
  and `CLAUDE_CODE_SKIP_BEDROCK_AUTH=1` is set, so the gateway signs each request (SigV4) and
  streams Bedrock's answer back unchanged.
- **Codex** speaks the OpenAI Responses API. The gateway serves it through LiteLLM, which turns
  those calls into Bedrock Converse calls.

Containers reach only the gateway. Block the instance metadata endpoint from them, for example
`iptables -I DOCKER-USER -d 169.254.169.254 -j DROP`.

## Any language

The grader reads JUnit XML, which every mainstream test runner can write. A recipe profile names
the image, the dependency install and the test command:

| runner | command in a profile |
|---|---|
| node:test, tsx | `node --test --test-reporter=junit --test-reporter-destination={junit} {files}` |
| pytest | `python -m pytest -o junit_family=xunit1 --junitxml={junit} {files}` |
| vitest | `npx vitest run --reporter=junit --outputFile={junit} {files}` |
| jest | `npx jest --reporters=jest-junit {files}` (`JEST_JUNIT_OUTPUT_FILE={junit}`) |
| go | `gotestsum --junitfile {junit} -- {files}` |

Run end to end so far:
- `node --test` and `tsx --test` on a TypeScript monorepo (173 tasks);
- pytest on a small Python repository (2 merged PRs, 2 tasks; Harbor oracle 2/2 and nop 0/2).

The other runners go through the same JUnit path but have not been run yet. The source-text probe
is Node-only; other runtimes fall back to a static, file-level check.

`taskmine init` writes a starter recipe for node and pytest repositories. A monorepo needs one
profile per test runner; [examples/foxl-pilot/taskmine.toml](examples/foxl-pilot/taskmine.toml) has
three.

```toml
[repo]
name = "acme/shop"
github = "acme/shop"

[units]
kind = "merges"           # or "commits" for squash-merge histories
branch = "main"
since = "2026-01-01"

[[profiles]]
name = "pytest"
run = ["tests/test_*.py"]
image = "python:3.12-bookworm"
deps = ["requirements.txt"]
install = "pip install -r /app/requirements.txt"
command = "python -m pytest -o junit_family=xunit1 --junitxml={junit} {files}"
display_command = "python -m pytest {files}"
```

## Scope

taskmine makes **coding** tasks: the agent changes the repository, and tests grade it. It does not
make review tasks (approve or reject a diff). Those need labelled review history, not tests.

## Dogfood

On a 28,000-commit TypeScript monorepo, 552 pull requests became 173 tasks. Each was validated in
Docker, then run under Harbor 0.27.0: oracle 1.0 and nop 0.0 on all 173. A sample of 10 was
rebuilt byte-for-byte on a second machine.

On the demo set (8 tasks from the newest pull requests), Claude Code with Sonnet 5.5 and Codex with
GPT-6.1 Terra each solved 4 of 8, with 0 errors, for $1.38 and $1.89. Both solved the same four.
See [docs/DOGFOOD.md](docs/DOGFOOD.md) for the funnel, the findings and the bugs the run found.

## Lower-level commands

`scan`, `meta`, `build`, `validate`, `finalize`, `check` and `report` are the steps that `mine` and
`verify` chain. Each takes `--recipe` and `--out` for scripting and CI. Every step records a code
and a reason for every pull request it leaves out.

## Development

```sh
PYTHONPATH=src python3 -m unittest discover -s tests   # real git repos and real test runs, nothing mocked
```

`demo/record.py` records the terminal demo. Commands are typed and then run for real in a
pseudo-terminal. Only waiting is shortened, and output is never edited.

License: MIT-0.

## Contributing and releases

- [CONTRIBUTING.md](CONTRIBUTING.md): setting up, the tests, and what a pull request needs.
- [AGENTS.md](AGENTS.md): the code layout, conventions and invariants, for coding agents and people.
- [CHANGELOG.md](CHANGELOG.md): what changed in each version.
- [RELEASING.md](RELEASING.md): versioning and the release checklist.
- [SECURITY.md](SECURITY.md): reporting a vulnerability privately.
