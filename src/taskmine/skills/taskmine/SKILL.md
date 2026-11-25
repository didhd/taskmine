---
name: taskmine
description: Build a coding benchmark from a repository's own merged pull requests, and compare coding agents on it, with the taskmine CLI. Use when someone wants to turn their repo's pull requests into Harbor (Terminal-Bench) tasks, verify mined tasks, benchmark Claude Code, Codex, OpenCode or Strands on their own code, read taskmine results, or get a failing taskmine run working (recipe, test runner, install, AgentCore setup).
---

# taskmine

taskmine is a deterministic command-line tool. Given the same commits and the same recipe, it
builds the same tasks and reaches the same verdicts:
- it mines merged pull requests into Harbor tasks;
- it keeps only those that execution proves;
- it benchmarks coding agents on them.

You are the operator. You run the steps and read what each one reports. When a step fails, you
find the cause, fix it in the recipe or the environment, and rerun only that step. The tool stays
deterministic; you supply the judgment it does not have.

## The rule that keeps the benchmark honest

You MAY change the recipe (`taskmine.toml`) and the environment: installs, base images,
credentials, the AWS setup.

You MUST NOT change anything that decides a task:
- the hidden tests;
- `tests/expected.json` or the grader;
- validation results or verdicts;
- anything under `.taskmine/out/tasks/` or `.taskmine/out/validation/`.

Never rerun a flaky check until it passes. Never raise a limit to let a rejected draft in unless
the user asks. Never write a task by hand. A draft that execution rejects stays rejected: report
its reason.

## Before you start

- `taskmine --version`. If it is missing: `uv tool install git+https://github.com/didhd/taskmine`.
- Python 3.11+, git and uv. `gh auth status` must be signed in, unless the user points at a local
  clone.
- `verify` and `bench` run on Amazon Bedrock AgentCore Runtime (`us-east-1`) by default. They need:
  - AWS credentials that may create IAM roles, ECR, S3, CodeBuild and AgentCore runtimes;
  - Bedrock access to the agent models.
- Without AWS, they run on a Docker host with `--env docker` and `taskmine gateway`.
- Before `agentcore setup`, `verify` or `bench`, tell the user that they create AWS resources (all
  named `evals-tm-*`) and that `bench` calls models, which costs money. Ask before `taskmine
  agentcore teardown`.

## The workflow

1. `taskmine login`. Skip it for a local clone.
2. `taskmine init owner/repo`, or `taskmine init owner/repo --clone path/to/clone`. It writes
   `taskmine.toml`. Open it and check each `[[profiles]]` against the repository (see
   [references/recipe.md](references/recipe.md)):
   - `run` globs match real test files;
   - `image` has the runtime;
   - `install` installs the dependencies;
   - `command` writes JUnit XML to `{junit}`.
3. `taskmine agents add <agent> --provider bedrock --model <model id>`, once per agent. On
   AgentCore these run: `claude-code`, `codex`, `opencode`, `strands`.
4. `taskmine mine`, or `taskmine mine --limit 50` for a first look. Read the funnel: how many
   candidates, and how many were left out for which reason.
5. `taskmine agentcore setup`. Once per AWS account, and again after adding an agent whose model
   is new.
6. `taskmine verify`. Each draft prints `accepted` or `rejected` with its reason.
7. `taskmine bench`, or `taskmine bench --tasks 20 -n 4`. Then `taskmine results`.
8. When the user is done: `taskmine agentcore resources`, then, with their OK,
   `taskmine agentcore teardown`.

## When a step fails: the healing loop

1. **Read the exact failure.** Look in the command's own output, then:
   - `.taskmine/out/validation/<id>.json` (verdict and reason);
   - the run logs in `.taskmine/out/drafts/<id>/validation/NN-<kind>/` (`docker.log`,
     `test-output.log`) and `build.log`;
   - for `bench`, `.taskmine/out/bench/<run>/<agent>.harbor.log` and the trial folders.
2. **Classify it:**
   - a recipe or environment problem: fix it;
   - or a real property of the pull request: leave it.
   [references/healing.md](references/healing.md) maps every reason code to its likely cause and
   what to do.
3. **Change the smallest thing** in the recipe or the environment, and say what you changed and
   why.
4. **Rerun only what failed:**
   - For one draft, from the project folder:
     `taskmine validate --recipe taskmine.toml --out .taskmine/out --executor agentcore --ids <id> --keep-images`
   - then `taskmine finalize --recipe taskmine.toml --out .taskmine/out`;
   - after a recipe change that affects which tests or files count, run `taskmine mine` again;
   - for `bench`, start a new `--run` name.
5. **Stop after three attempts at the same cause.** Report what you tried and what you saw.

## Reading results

- `solved` counts trials whose hidden tests all passed.
- `errors` are infrastructure failures: credentials, throttling, an agent that could not start.
  They are kept out of `rate`, so they never count as unsolved. Fix them and rerun; don't explain
  them away.
- Every agent sees the same tasks, so the per-task grid compares agents directly.
- Cost is shown when the agent reports it. Codex and Strands on Bedrock do not.

## Safety

- Never pass cloud credentials to an agent with `--env`. On AgentCore each session gets
  short-lived, model-scoped credentials. On a Docker host the gateway signs requests on the host.
- The tasks contain the repository's source code. Don't publish `.taskmine/out/` unless the user
  says the repository is public.
- `teardown` deletes only `evals-tm-*` resources. Still confirm with the user first.
