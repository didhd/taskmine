# Changelog

All notable changes to taskmine are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and taskmine uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). While the major version is 0, a minor
release may change the command line, the recipe format or the task layout; RELEASING.md says how
releases are made.

## [Unreleased]

## [0.1.0] - Unreleased

The first tagged release, so everything below is new. Changed, Removed and Fixed describe the
differences from the untagged code that was on `main` before it.

### Added

- Everyday commands for one repository, with their state in `.taskmine/`:
  - `taskmine login` signs in to GitHub through the GitHub CLI (`gh`); taskmine stores no token.
  - `taskmine init owner/repo` clones the repository without blobs over 9 MB (or uses `--clone`)
    and writes a starter recipe: one profile per directory of node:test files, up to four, and one
    for pytest.
  - `taskmine agents add|list|remove` keeps the agents to benchmark in `.taskmine/agents.toml`.
    An agent is a Harbor agent, a provider and a model; its default name comes from the model id
    without the region and vendor prefixes, for example `claude-code-sonnet-5-5`.
  - `taskmine mine` runs `scan`, `meta` (when the recipe names a GitHub repository) and `build`.
  - `taskmine verify` runs `validate` (2 base runs, 3 gold runs, images kept, drafts already
    validated skipped), then `finalize`, `check` and the report.
  - `taskmine bench` runs every agent over the same tasks with Harbor, all agents at once, and
    prints each trial as it finishes. `--tasks N --seed S` takes a seeded sample of N tasks, and on
    AgentCore `--agents oracle,nop` runs Harbor's reference agents. A trial that ended in an
    exception is counted as an error, not as unsolved.
  - `taskmine results` prints a run as a table: solved, rate over the trials without errors,
    errors and cost per agent, and a grid of tasks when there are 30 or fewer.
- Amazon Bedrock AgentCore Runtime as the default place `verify` and `bench` run trials
  (`--env agentcore`), with a Docker host as the other option (`--env docker`):
  - `taskmine agentcore setup|resources|teardown` creates, lists and deletes the AWS side in
    `us-east-1` (IAM roles, a private ECR repository, a private S3 bucket and a CodeBuild
    project, all named `evals-tm-*`), from its own Python 3.12 environment with Harbor 0.27.0 and
    boto3 1.44.7 or later;
  - each task is rewritten for AgentCore (the agent may reach only the Bedrock endpoints, the
    grader no network, base images from AWS's ECR Public mirror) and its image is built once on
    CodeBuild (`--build-jobs`); `--platform V1|V2` picks the runtime platform;
  - every trial and every validation run gets a fresh session, and the repeat validation runs of
    a draft run at the same time;
  - the AgentCore environment for Harbor is vendored byte for byte from
    aws-samples/sample-gen-ai-evaluations-workshop pull request #120, and a test checks every
    file against its upstream blob.
- `taskmine gateway`, so that no cloud credential enters an agent container on a Docker host. It
  runs there with the host's AWS credentials:
  - a Bedrock pass-through for Claude Code, which signs each request with SigV4 and streams the
    answer back;
  - an OpenAI-compatible endpoint through LiteLLM for the Codex, OpenCode and Strands agents on
    Bedrock, started on the next port and used only once it answers;
  - the pass-through serves only loopback and private addresses, and the LiteLLM endpoint needs a
    key taskmine generates; `--detach` runs the gateway in the background with its log in
    `.taskmine/gateway.log`, and `.taskmine/gateway.toml` (mode 0600) says where it listens.
- AgentCore platform V2 as an option (`--platform V2`; V1 stays the default, measured faster for
  test runs that read many small files). V2 runtimes are named `<V1 name>_v2`, so both can
  serve one image. `TASKMINE_AC_WARMUP=1` adds AgentCore's build hook, which reads the image
  files sessions need while the V2 snapshot is built.
- Agents:
  - Run end to end:
    - `claude-code`, `codex`, `opencode` and `strands` on AgentCore;
    - `claude-code` and `codex` on a Docker host through the gateway.
  - `cursor`, `gemini`, `aider` and `goose` are passed to Harbor as they are.
  - Strands runs with taskmine's factory (`templates/taskmine_strands.py`):
    - the runner's shell and file editor and a short system prompt;
    - up to 32,000 output tokens per call;
    - on AgentCore, its Bedrock model is built inside the session;
    - a line on stdout a minute, so AgentCore does not end a quiet command.
- The pipeline commands, each with `--recipe` and `--out`: `scan`, `meta`, `build`, `validate`,
  `finalize`, `check` and `report`. Every step records a code and a reason for each unit it leaves
  out, and `report` writes the funnel as `report.md` and `report.json`.
- The recipe, `taskmine.toml`: which commits are change units (merge commits, or first-parent
  commits for squash-merge histories, with the pull request number read by `pr_patterns`), which
  files are the test side, what to leave out of the snapshot, size limits, and one `[[profiles]]`
  entry per test runner (image, install, dependency files, test command with `{files}` and
  `{junit}`, resources, environment). Unknown keys are errors.
- Tasks in Harbor's (Terminal-Bench 2.x) layout:
  - the repository at the base commit as a reproducible archive: no history, no file from the
    change, binary files, oversized files and the recipe's exclusions left out unless the gold
    patch touches them, and every member checked against the base commit's blob after the
    archive is written;
  - an instruction built only from the pull request title, the closing issue (quoted, only when it
    was opened before the pull request and passes the leak check), the fail-to-pass test names and
    the interface contract (new names, new modules and exact text the hidden tests use);
  - a grader that writes reward 0 first, restores every test-side file the agent changed, runs the
    tests, reads their JUnit report, and gives reward 1 only when every fail-to-pass and
    pass-to-pass test passed.
- Validation by execution, with `--network none`: a fail-to-pass test passes in no base run and
  in every gold run, a test whose result changes between runs of the same kind is left out, and
  the task's own grader must give 0 with no solution and 1 with it.
- The text probe (`text_probe = "node"`): a Node preload that re-runs the gold state while test
  code reads the base text of changed files, so tests that only check source text are never
  required. Other runtimes use a static, file-level check.
- The leak check: no gold code line of 30 or more non-space characters, no run of 12 consecutive
  gold code tokens and no diff syntax in an instruction.
- Reproducible drafts: a draft built twice has the same digest, and `finalize` refuses a draft
  whose bytes differ from the ones validated.
- Builds from filtered (partial) clones, which never fetch a missing blob.
- Dependency layers shared between tasks: `deps` globs, and `deps_normalize = "npm"`, which drops
  the repository's own version fields from `package.json` and the lockfile.
- `taskmine --version`, and the optional dependency groups `gateway` (botocore, LiteLLM) and
  `agentcore` (boto3, Harbor 0.27.0).
- `demo/record.py`, which records a real terminal session in a pseudo-terminal, and
  `demo/combine.py`, which joins recordings and can drop whole commands.
- Continuous integration (tests on Linux and macOS, lint, build, a check of the built wheel and
  sdist) and a release workflow that publishes a GitHub Release for each `v*` tag.
- AGENTS.md, CONTRIBUTING.md, RELEASING.md, SECURITY.md, and issue and pull request templates.

### Changed

- The pipeline commands print paths relative to the working directory when they are inside it.
- `meta` also reports how many closing issues it read, and `scan --brief` prints refusal codes
  without their explanations.
- Packaging: the version is written once, in `taskmine.__version__`; the license is the SPDX
  expression `MIT-0`; building needs setuptools 77 or later.

### Removed

- The narrated overview video and its generator (`docs/media/`). The README embeds the terminal
  recording instead.

### Fixed

- A recipe value of the wrong type is a recipe error that names the key. A string where a list
  belongs was read as a list of characters (`run = "test/*.test.mjs"` made every top-level file a
  runnable test), `include_issue = "no"` was true, `cpus = "two"` or `repo = 5` ended in a
  traceback, and a `pr_patterns` entry that does not compile or has no group failed later, in
  `scan`.
- With the static source-text check, a test file that imported a changed module and read any file,
  such as a fixture, lost all its fail-to-pass tests: the import was taken for a read of the
  module's text.
- task.toml stays readable when a value holds U+007F, and keys that are not bare TOML keys are
  quoted.
- The report no longer lists a reason with a count of 0.

[Unreleased]: https://github.com/didhd/taskmine/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/didhd/taskmine/releases/tag/v0.1.0
