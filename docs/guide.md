# Taskmine usage guide

## Set up a project

Install Python 3.11 or newer, Git, the GitHub CLI (`gh`), and [uv](https://docs.astral.sh/uv/). From the taskmine checkout, run `uv tool install .` or use `PYTHONPATH=src python3 -m taskmine` there. The core uses only Python's standard library.

Run `taskmine login` for GitHub access, then `taskmine init your-org/your-repo` from a separate project directory. To use a local checkout instead, pass `--clone /path/to/repo` (and `--recipe /path/to/recipe.toml` if needed). You don't need GitHub sign-in for a local clone if the recipe has no `github` field. Review the generated `taskmine.toml`, especially the branch and test profiles, then run `taskmine mine`. Set `[units] kind = "commits"` for first-parent, single-parent changes when the repo has no merge commits; this does not group commits by issue key.

`verify` turns validated drafts into standard Harbor directories under `.taskmine/out/tasks`. `bench` runs every configured agent over the same tasks. Add `--tasks 20` to sample tasks with a fixed seed. `results` shows the latest run's scores and reported costs. The finished tasks can also be used with Harbor directly:

```sh
harbor run -p .taskmine/out/tasks -a oracle
harbor run -p .taskmine/out/tasks -a nop
```

## Configure the agents

| Agent | Providers | Tested end to end |
| --- | --- | --- |
| `claude-code` | `bedrock`, `anthropic` | AgentCore and Docker with Sonnet 5.5 |
| `codex` | `bedrock`, `openai` | AgentCore via Bedrock Mantle and Docker via LiteLLM, with GPT-6.1 Terra |
| `opencode` | `bedrock`, `anthropic`, `openai` | AgentCore with Sonnet 5.5 |
| `strands` | `bedrock`, `openai` | AgentCore with Sonnet 5.5 and the bundled coding agent |
| `cursor`, `gemini`, `aider`, `goose` | Varies by Harbor agent | Passed to Harbor as is; not tested by taskmine |

```sh
taskmine agents add claude-code --provider bedrock --model us.anthropic.claude-sonnet-5-5
taskmine agents add codex --provider bedrock --model us.openai.gpt-6.1-terra
taskmine agents add opencode --provider bedrock --model us.anthropic.claude-sonnet-5-5
taskmine agents add strands --provider bedrock --model us.anthropic.claude-sonnet-5-5
```

Add the agents you plan to run before `taskmine agentcore setup` so it can scope the runtime's model permissions. Bedrock Converse rejected Terra in the test account because of its data retention setting. The measured OpenCode and Strands runs used Sonnet instead; Codex used the Bedrock Mantle Responses API.

## Run on AgentCore or Docker

### AgentCore Runtime

`verify` and `bench` default to AgentCore V1. You need AWS credentials with permission to set up the project resources in `us-east-1` and access the chosen models. `taskmine agentcore setup` creates a local Python 3.12 environment with Harbor 0.27.0 and boto3, plus the cloud resources needed to build and run the tasks. It creates IAM roles, a private ECR repository, a private S3 bucket and a CodeBuild project under the `evals-tm-` prefix. `taskmine agentcore resources` lists taskmine resources. When the project is finished, use `taskmine agentcore teardown` to remove the prefixed resources, including runtimes. Review the list first; teardown is scoped by prefix, not by project directory.

The [vendored Harbor environment](../src/taskmine/agentcore/PROVENANCE.md) builds arm64 images on CodeBuild and runs each agent trial and each validation run in a fresh session. `bench` can reuse images built by `verify`. Taskmine's copy of each task configures repository ownership, the network allowlist and the preinstalled harnesses. Base images are pulled via ECR Public. The agent runs unprivileged, can only reach the allowed Bedrock endpoints, and gets short-lived, model-scoped credentials. The grader runs without network access, in two stages (the workshop harness's `test.sh --stage` contract): the tests run as the agent user on a copy of the repository, then a separate trusted stage, as another user and after every agent process has ended, computes the reward from the test report and the task's own files. Nothing the agent can write becomes the reward, and a trial that cannot be graded this way is reported as an error instead of being graded in one stage. See [where grading runs](#where-grading-runs).

Use Docker for tasks with x86-only dependencies unless you set up an x86_64 AgentCore capacity provider separately. V1 supports images up to about 2 GB compressed, while `--platform V2` supports images up to 10 GB. With `TASKMINE_AC_WARMUP=ram`, V2 puts the repository in its memory snapshot; `TASKMINE_AC_WARMUP=disk` only primes file reads. In two matched runs of one task, V1 took 301 to 307 s and RAM-tuned V2 took 330 s, excluding runtime deployment. See the [measurements and their limits](../AGENTS.md#where-trials-run).

### Where grading runs

| where | how the reward is made | status |
|---|---|---|
| `--env agentcore` (`bench`) | staged: `test.sh --stage untrusted` as the agent user, then `--stage trusted` as the grader user; only the trusted stage's `reward.json` counts | supported, the default |
| `--env agentcore`, a task made before staged grading, or `--ek staged_grader=false` | none: the trial ends in an error | refused on purpose |
| `--env docker` (`bench`) | one stage, `test.sh` in the agent's container, as Harbor's own verifier runs it; `reward.txt` | supported; `bench` says it is single-stage |
| plain `harbor run` with another environment | one stage, as above | works; nothing is staged |
| `verify` (any executor) | one stage, no agent involved: the base and the gold patch only | supported |

The staged grade keeps every file the agent can write out of the score, but the tests still run the agent's code: code planted on the gold side can still write a false test report. The [decision record](../AGENTS.md#where-trials-run) has the details and what each stage reads.

### Docker

On a host with Docker, install Harbor (`uv tool install harbor`). From the taskmine checkout, install taskmine with its gateway dependencies (`uv tool install '.[gateway]'`; add `--force` if replacing an existing tool install). In your taskmine project, run `taskmine gateway --detach`, then `taskmine verify --env docker` and `taskmine bench --env docker` on that host.

The gateway keeps AWS credentials on the host. It signs Claude Code's Bedrock calls in its native Bedrock mode and serves Codex through LiteLLM's OpenAI-compatible Responses endpoint. Use a private or loopback gateway address and block Docker task containers from reaching the host's instance metadata endpoint. See [The Docker path](../AGENTS.md#the-docker-path) for configuration and isolation details.

## Add a test profile

Taskmine reads JUnit XML, so any test runner that writes it works. These have run end to end on AgentCore: `mine`, `verify` (nop 0, oracle 1), then `bench` with Claude Code (Sonnet 5.5) and Codex (GPT-6.1 Terra).

| language | runner | recipe | verified | Claude Code | Codex |
|---|---|---|---|---|---|
| Python | pytest | [python.toml](../demo/langs/python.toml) | 2 of 2 | 2/2 | 2/2 |
| TypeScript | Vitest | [typescript.toml](../demo/langs/typescript.toml) | 2 of 2 | 2/2 | 2/2 |
| Go | `go test` through gotestsum | [go.toml](../demo/langs/go.toml) | 2 of 2 | 2/2 | 2/2 |
| Java | Maven, JUnit 5 | [java.toml](../demo/langs/java.toml) | 2 of 2 | 2/2 | 2/2 |
| Rust | cargo-nextest | [rust.toml](../demo/langs/rust.toml) | 2 of 2 | 2/2 | 2/2 |
| C# | `dotnet test`, xUnit | [csharp.toml](../demo/langs/csharp.toml) | 2 of 2 | 2/2 | 2/2 |
| Ruby | RSpec | [ruby.toml](../demo/langs/ruby.toml) | 2 of 2 | 2/2 | 2/2 |
| PHP | PHPUnit | [php.toml](../demo/langs/php.toml) | 2 of 2 | 2/2 | 2/2 |

Each runs on a synthetic repository that [make_repo.py](../demo/langs/make_repo.py) writes in that language: the same three pull requests (a new module with new tests, a change to existing code that keeps an old test passing, and a README edit, which `mine` leaves out as `no-test-change`). JavaScript with `node:test` and Python with pytest have also run on real repositories. The per-test source text probe is for Node; other runners fall back to a coarser static check.

`taskmine init` supplies starter profiles for Node and pytest repos. Give each runner its own profile in a monorepo; see the [TypeScript example](../examples/acme-web/taskmine.toml). A minimal pytest profile looks like this:

```toml
[repo]
name = "acme/shop"
github = "acme/shop"

[units]
kind = "merges"
branch = "main"
since = "2026-01-01"

[[profiles]]
name = "pytest"
run = ["tests/test_*.py"]
image = "python:3.12-bookworm"
deps = ["requirements.txt"]
install = "pip install -r /app/requirements.txt pytest"
command = "python -m pytest -p no:cacheprovider -o junit_family=xunit1 --junitxml={junit} {files}"
display_command = "python -m pytest {files}"
```

Update `deps` and `install` for your project. Dependencies must be installed in the image before tests run offline. Keep `{files}` and `{junit}` in the command. For Node, use the `junit` test reporter; for other runners, start from the recipe for your language in the table above.

## What goes into a task

A task contains a checked snapshot of the base commit, without git history; an instruction; and hidden tests. The instruction draws from the PR title, a linked issue that predates the PR if it passes the leak check, test names, and the names and interfaces the tests expect. Taskmine doesn't copy PR bodies or use a model to write the instruction or score a submission.

The grader restores test-side files, runs the tests and reads JUnit XML. It awards a point only when all required tests pass. Validation repeats the base and fixed runs, rejects text-only tests, and checks that Harbor's `nop` and `oracle` get 0 and 1 respectively. Taskmine makes coding tasks, not review tasks. See [the dogfood benchmark](DOGFOOD.md) for results, test exclusions and known limits.

## Replay the demo

```sh
asciinema play docs/demo.cast
```

The demo uses the repository generated by [make_shop.py](../demo/make_shop.py). The [recorder](../demo/record.py) captures live terminal output and wall-clock timing. The session took 304 seconds. The README video replaces 2:52 of waiting during `verify` with a transition; the [full MP4](https://github.com/didhd/taskmine/issues/1#issuecomment-7152029102) and terminal cast preserve the wait. `verify` built four images and checked five drafts in 200 seconds, accepting four and rejecting one with only text checks. `bench` ran four agents in 69 seconds, and all four solved all four tasks. These small tasks show the workflow, not relative agent quality.

## Scripting and development

The lower-level stages are `scan`, `meta`, `build`, `validate`, `finalize`, `check`, and `report`. Use each command's `--help` for its inputs and environment options. Rejected candidates and drafts carry reasons you can act on.

```sh
PYTHONPATH=src python3 -m unittest discover -s tests
```

See [CONTRIBUTING](../CONTRIBUTING.md), [engineering notes](../AGENTS.md), [release steps](../RELEASING.md), and [security reporting](../SECURITY.md).
