# Reason codes: cause and what to do

Every pull request that taskmine leaves out has a reason code:
- `mine` prints them as a funnel;
- `verify` prints one per draft;
- `.taskmine/out/validation/<id>.json` holds the details.

"Fix" means the cause is the recipe or the environment: change it and rerun the step. "Leave"
means the code describes the pull request itself. Report it; don't work around it.

## While mining (scan and build)

| code | usual cause | do |
|---|---|---|
| `not-a-pr` | merge subjects don't match `[units] pr_patterns` | **Fix.** A squash-merge history: set `[units] kind = "commits"`. Otherwise add a pattern with one group for the PR number. |
| `not-two-parents` | an octopus or odd merge | Leave. |
| `no-test-change` | the PR changed no test file | Leave. If tests live somewhere `[tests] paths` misses, **fix** `paths`. |
| `no-runnable-test` | changed tests match no profile's `run` globs | **Fix.** Add a profile, or widen `run`, for that runner. |
| `no-code-change` | only documentation changed | Leave. |
| `gold-too-large`, `too-many-run-files` | over `[limits]` | Leave. Raise `[limits]` only if the user asks for bigger tasks. |
| `submodule-change` | a submodule pointer moved | Leave. |
| `superseded` | a later unit covers the same PR | Leave. |

## While verifying (validate)

| code | usual cause | do |
|---|---|---|
| `build-failed` | the image does not build: a missing system package, the wrong base image, an install that needs network or secrets, a lockfile mismatch | **Fix.** Read `build.log`, then fix `image`, `setup`, `install` or `deps` in the profile. |
| `gold-patch-does-not-apply` | the snapshot dropped a file the patch touches: too big, binary, or in `[snapshot] exclude` | **Fix**, if the file is source: raise `[snapshot] max_file_bytes` or narrow `exclude`. |
| `no-tests-reported` | the command wrote no JUnit at `{junit}` | **Fix** the reporter flags in `command` (see recipe.md). |
| `tests-fail-with-gold` | the tests fail even with the merged fix | **Fix** when the environment is the cause: a missing dependency or env var, the wrong working dir (`workdir`), a timeout (`timeout_sec`). **Leave** when the tests need a network, a database or a service: validation has no network. |
| `no-fail-to-pass` | the new tests already pass before the change | Leave. |
| `fail-to-pass-was-flaky` | a test flipped between repeated runs | Leave. Never retry until it passes. |
| `source-text-only` | the tests only check source text, not behaviour | Leave. This is a correct rejection. |
| `text-probe-failed` | the probe could not list changed files (often no `git` in the image) | **Fix** the image so it has `git`, then rerun the draft. |
| `nop-not-zero` | doing nothing scored above 0: tests that pass on the base, or state left over | Investigate the environment. Never touch the grader or the tests. |
| `oracle-not-one` | the merged fix did not score 1 under the real grader | Investigate the environment (as for `tests-fail-with-gold`). |
| `instruction-leak` | the instruction would reveal the fix | Leave. |
| `taskmine-error` | a crash in taskmine itself | Report it with the traceback (`detail`). |

## While benchmarking (bench)

`bench` counts these as errors, not failures. Fix them, then rerun with a new `--run` name.

- **The model refused the credentials** (`AccessDenied`). The agent's model is not in the agent
  role: run `taskmine agentcore setup` again after `taskmine agents add`.
- **Throttling.** Lower `-n`.
- **An image too large for AgentCore V1** (about 2 GB compressed). Slim the profile's install, or
  use `--platform V2`, which allows 10 GB but was measured slower.
- **A runtime not READY, or a CodeBuild failure.** Read the image line `bench` prints and the
  build log. Rerunning `bench` builds only the missing images.
- **Bedrock Converse refuses a model** with "data retention mode 'none' is not available". That
  model can't run through OpenCode or Strands in this account. Pick another model for them; Codex
  reaches OpenAI models through bedrock-mantle instead.
