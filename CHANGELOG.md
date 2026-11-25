# Changelog

All notable changes to taskmine are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and taskmine uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). While the major version is 0, a minor
release may change the command line, the recipe format or the task layout; RELEASING.md says how
releases are made.

## [Unreleased]

### Changed

- **Staged grading on AgentCore** (`bench --env agentcore`), after PR #120's
  `test.sh --stage untrusted|trusted` contract.
  - Before, the tasks' `test.sh` had no stages. So PR #120's environment graded every trial
    "unsealed": in one command, as the agent user, with a reward file the agent's code could
    write.
  - Now the tests run as the agent user on a copy of the repository and write only a JUnit report.
    The environment checks that the repository did not change. A trusted stage, as the grader user
    after every agent process has ended, then computes the reward from that report and the task's
    root-owned files only.
  - The trusted stage runs a root-owned python3 in isolated mode, takes no reward, grade, status or
    exit code from the untrusted stage, scores 0 for a report with a DTD, and fails a required test
    that the report holds twice unless both copies passed.
  - The AgentCore copy of a task puts the repository at `/work/repo`, where PR #120's layer keeps
    a root-only baseline, behind an `/app` link. It removes group and world write from `PATH`
    folders, such as the ruby image's `GEM_HOME`, so the agent cannot plant a program there that
    the trusted stage would run. The image tags change.
  - A trial that cannot be graded in stages is now an error instead of an unsealed grade. That
    covers a task made before this change, `--ek staged_grader=false`, and an image without the
    baseline.
  - Harbor's own verifier (`bench --env docker`, plain `harbor run`) still runs `test.sh` without
    `--stage`, in one stage, and `bench` now says so.

### Fixed

- The grader used `str.removeprefix`, which Python 3.7 and 3.8 do not have, in the JUnit file
  mapping added in 0.1.1. A test now checks the grader for Python 3.7.

## [0.1.1] - 2026-11-25

### Added

- Eight languages, checked end to end on AgentCore:
  - `demo/langs/make_repo.py` writes a synthetic repository in Python, TypeScript, Go, Java, Rust,
    C#, Ruby or PHP;
  - `demo/langs/<lang>.toml` is its recipe: pytest, vitest, `go test` through gotestsum, Maven
    with JUnit 5, cargo-nextest, `dotnet test` with xUnit, RSpec, PHPUnit.
- The skill needs no install: without `taskmine` on PATH, it runs every command through
  `uvx --from git+https://github.com/didhd/taskmine@v0.1.1 taskmine`.
- The README leads with `npx skills add didhd/taskmine`, which installs the skill for any agent
  that reads Agent Skills.

### Fixed

- **Test files for runners whose JUnit has no file attribute.** Maven and Gradle (surefire),
  gotestsum, cargo-nextest and `dotnet test` name a testcase by class, package or binary. The grader
  filed such tests under the class name, so every run file read as "no tests". It now finds the
  run file in this order:
  - the class as a path (`com.acme.CartTest` -> `.../com/acme/CartTest.java`);
  - the class, binary or package name as the file stem or its directory;
  - the file that defines the test's identifier;
  - the only run file.
  A path in the class or the suite name (vitest, jest) is taken as is.
- **The interface contract for languages other than JavaScript and Python.**
  - Before, it read Go, Java, Kotlin, Rust, C#, Swift, C/C++, PHP and Ruby test code without
    removing strings and comments. The words of an error message (`unknown`, `code`) were listed as
    new names, and no exact text was found.
  - Those languages now have their own comment and string forms: Go raw strings, Rust raw strings
    and lifetimes, C# verbatim and interpolated strings, text blocks, and Ruby and PHP `#`
    comments.
  - The strings that are test titles are known by what precedes them: rspec and minitest `it` and
    `test`, Go `t.Run`, JUnit `@DisplayName`, xUnit `DisplayName =`.
- **Exact text the change formats.** A test that checks `"unknown code: FREE"` against a change
  that writes `"unknown code: %s"` now gets that text in its instruction. This also covers
  `${...}`, `#{...}`, `\(...)`, `$name`, and any `{...}` group: `{}`, `{0}`, `{:?}`, and f-string
  or C# expressions such as `{code.strip()}`. A string the change concatenates counts too. Before,
  only text the gold code held verbatim was listed, so such tasks asked agents to guess the
  message.
- **New modules in any language.** A test that imports a file the change creates is told that
  file's path, from an import statement in any language (`from shop.discount import`,
  `use shop::discount::`, `use Acme\Shop\Discount;`, `require "shop/discount"`). Before, only
  relative JavaScript and Python imports counted, so agents put a correct function in the wrong
  module and every test failed to import it.
- **Short names the change declares.** Names under 4 characters were never listed, so a new
  method such as Ruby's `def self.of` went unmentioned. They are now listed when the change
  declares them and the base neither calls nor declares them.
- **How the tests call each new name.** The instruction now quotes the first call of each new
  name in the tests, as written (`Discount::of(1000, 'SAVE10')`), so the argument order is not a
  guess. Only the call is quoted, never the expected result.
- **AgentCore: `localhost` in the session.** A session's `/etc/hosts` is empty and read-only, and
  the agent's namespace has no DNS, so `localhost` did not resolve: vitest could not start, nor
  could any test that serves on localhost. The image now uses glibc's `myhostname` module.
- **AgentCore: images whose base already has Python 3.12** (python, maven, Ubuntu 24.04) failed
  to build in the Strands step, which changed a folder uv had not created. Strands' venv is now
  also compiled to bytecode at build time.
- `bench` with no verified tasks says so instead of failing with a traceback.
- **The starter pytest profile for repositories without `requirements.txt`.** It required that
  file, so a project that declares its dependencies in `pyproject.toml` (pallets/itsdangerous)
  had every draft refused as `deps-missing`. The install now reads each base's own files in the
  image: `pyproject.toml`'s dependencies with its `tests`, `test`, `testing` or `dev` extra and
  dependency group when they exist, then the first test requirements file and
  `requirements.txt`. One recipe covers old and new commits, which declare their dependencies
  differently. A `src/` layout puts `src` on `PYTHONPATH`, and `*_test.py` files count as tests.
- **Toolchains outside PATH on AgentCore.** PR #120's layer gives a session only PATH, VIRTUAL_ENV,
  UV_*, LANG, LC_ALL and HOME. Rust's RUSTUP_HOME and CARGO_HOME, Ruby's GEM_HOME and BUNDLE_*,
  JAVA_HOME, NUGET_PACKAGES, the DOTNET_*, PHP_* and other toolchain variables are now added to
  the session's environment, for the agent and the grader. The image tag changes with that step.

## [0.1.0] - 2026-11-25

The first tagged release, so everything below is new. Changed, Removed and Fixed describe the
differences from the untagged code that was on `main` before it.

### Added

- An agent skill, `skills/taskmine/`, in the Agent Skills format (SKILL.md):
  - Claude Code, Codex, Cursor and Kiro can load it, and through it a coding agent drives the CLI.
  - When a step fails, the agent reads the reason code and logs, fixes the recipe or the
    environment, and reruns only that step.
  - It is told never to change the tests, the grader, validation results or verdicts.
  - `references/healing.md` maps every reason code to its cause and what to do.
- `taskmine skill install [--for claude,codex,cursor,kiro] [--project] [--dry-run]` copies the
  skill to where each tool reads skills: `.claude/skills`, `.agents/skills` (Codex and Cursor),
  `.kiro/skills`. `taskmine skill path` prints the packaged copy.
- The repository is a Claude Code plugin marketplace: `/plugin install taskmine --marketplace
  didhd/taskmine`. The manifests are `.claude-plugin/plugin.json` and
  `.claude-plugin/marketplace.json`.

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
- AgentCore platform V2 as an explicit option (`--platform V2`).
  - V1 stays the default for `verify` and `bench`: in a paired measurement, V2 with AgentCore's
    guidance applied matched V1's test time but set up about 3 s slower per run.
  - V2 runtimes are named `<V1 name>_v2`, so both can serve one image.
  - `TASKMINE_AC_WARMUP=ram|disk` adds AgentCore's build hook. `ram` holds the repository in the
    memory snapshot (a tmpfs bound over `/app`); `disk` only reads the files sessions need.
- `demo/make_shop.py` and `demo/shop.toml`: a synthetic repository for a demo that shows no real
  code. `demo/record.py --live` records a session with wall-clock times.
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
- The dogfood example recipe is `examples/acme-web/taskmine.toml`. The dogfood repository is
  private, so its names are replaced by `acme/web` there and in the docs, comments and tests.

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
- The source-text probe fails closed. Before the fix, the local executor given a relative
  `--scratch` handed the probe a relative path; its git diff failed and it substituted nothing, so
  a test that greps source text was accepted as a behaviour test. Now the scratch path is
  absolute, and a probe that cannot list the changed files rejects the draft
  (`text-probe-failed`).
- `init` on a local clone whose recipe names no GitHub repository no longer requires a GitHub
  sign-in, and shows the clone path relative to the current directory.
- The report no longer lists a reason with a count of 0.

[Unreleased]: https://github.com/didhd/taskmine/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/didhd/taskmine/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/didhd/taskmine/releases/tag/v0.1.0
