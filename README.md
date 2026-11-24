# taskmine

Turn a repository's merged pull requests into a Terminal-Bench / Harbor coding benchmark, where
every task has been proven by execution.

A task is the repository at the commit just before a merged change, an instruction, and a hidden
test suite. It is kept only when execution shows all of the following, in the image the task
ships with:

- the change's tests **fail** without the change, in every repeated run;
- they **pass** with the change, in every repeated run;
- Harbor's do-nothing agent (`nop`) scores **0** and its `oracle` agent scores **1**.

The output is a directory of standard Harbor task directories (`instruction.md`, `task.toml`,
`environment/`, `solution/`, `tests/`), the format Terminal-Bench 2.x uses. Run it like any
Terminal-Bench dataset and compare harnesses and models on your own code:

```sh
harbor run -p out/tasks -a claude-code -m <model>
harbor run -p out/tasks -a codex -m <model>
```

## Why another tool

[sample-code-eval-harvester](https://github.com/aws-samples/sample-code-eval-harvester) mines pull
request **review** history into approve/deny datapoints for a PR-review agent. It is careful work
(answer-absence sealing, a model-free CLI), and it answers a different question. taskmine answers
"how well does this agent or harness write code in our repository?":

| | eval-harvester | taskmine |
|---|---|---|
| What the agent does | reads a diff, says approve or block | edits the repository until hidden tests pass |
| Which PRs qualify | ones that went through review iterations (a rejected push, comments, an approved push) | any merged PR that adds or changes a runnable test and changes code |
| Who decides the label | a coding agent fills the "defect vs nit" judgment slots of each candidate | execution: fail before, pass after, repeated; no model anywhere |
| How a run is graded | an LLM judge (pinned Claude Haiku 4.5 on Bedrock) matches the agent's findings to the mined ones | the repository's own tests, from the runner's JUnit report |
| Dependencies | none needed (nothing runs) | installed in the image from the repository's own lockfiles, cached across tasks |
| Answer absence | scans the datapoint for its own answer | the snapshot is re-read and every file is proven to be the base commit's; the instruction is checked against the gold patch |
| Reproducibility | | a draft rebuilt from the same inputs has the same sha256, so a dataset validated on one machine is rebuilt byte-for-byte on another |
| Runtime | Lambda MicroVMs, public network only | any Docker host; validation runs with `--network none` |

## Principles

1. **Execution decides.** Fail-to-pass and pass-to-pass tests are read from the test runner's own
   JUnit report, across repeated runs. A test whose result changes between runs is flaky and is in
   neither set. Nothing is labelled by a model.
2. **No sentence in an instruction is written by a model.** The instruction is assembled from
   the pull request title, the issue the pull request closed (quoted, only when it was opened
   before the pull request and passes the leak check), the names of the fail-to-pass tests
   verbatim, and the interface contract. Pull request bodies are never used: they describe the fix.
3. **Hidden tests are fair.** The *interface contract* is computed, not judged: identifiers the
   tests use that occur nowhere in the base repository but that the change adds, modules the tests
   import that the change creates, and string literals the tests check that only the change adds.
   The instruction names them. This is the failure mode behind most of the tasks SWE-bench
   Verified's human review removed (tests that require a name or message the issue never states).
4. **Sealed.** The image holds the base commit's files and a fresh one-commit git repository, no
   history. `verify_seal` re-reads the finished archive and proves every member equals the base
   tree's blob at that path. Hidden tests live in `tests/`, which Harbor copies in only after the
   agent finishes; the grader then restores every test-side file the agent touched.
5. **Every refusal has a reason.** `scan`, `build`, `validate` and `finalize` each write the code
   and a sentence for every unit they leave out; `report` prints the whole funnel.

## How it works

```
taskmine scan      --recipe taskmine.toml --repo CLONE --out out   # change units -> candidates.jsonl
taskmine meta      --recipe taskmine.toml --out out                # optional: PR titles + closing issues (gh)
taskmine build     --recipe taskmine.toml --repo CLONE --out out   # a draft Harbor task per candidate
taskmine validate  --recipe taskmine.toml --out out --jobs 14      # Docker: base x2, gold x3, nop, oracle
taskmine finalize  --recipe taskmine.toml --out out                # instruction.md + task.toml -> out/tasks/
taskmine check     out/tasks                                       # static checks, no Docker needed
taskmine report    --out out                                       # the funnel
```

- A **change unit** is a merge commit (base = first parent) or, for squash-merge histories, a
  first-parent commit. Files split into the **test side** (`[tests] paths`) and the **gold side**.
- A **profile** names one test runner: which test files it runs, the image, the dependency
  manifests and install command, and the command, which must write JUnit XML to `{junit}`. Every
  mainstream runner can: `node --test --test-reporter=junit`, `vitest --reporter=junit`,
  `pytest --junitxml`, `go test` via `gotestsum --junitfile`, `jest` via `jest-junit`.
- The **Dockerfile** copies the dependency manifests and installs them before it adds the
  snapshot, so tasks with the same manifests share the install layer. `deps_normalize = "npm"`
  ignores a release's own version bump when deciding "same manifests".

Requirements: Python 3.11+ (standard library only), git, and Docker for `validate`. Run in place
with `PYTHONPATH=src python3 -m taskmine ...` or install with `uv tool install .`.

## The recipe

One file per repository. See [examples/foxl-pilot/taskmine.toml](examples/foxl-pilot/taskmine.toml)
for a real one (three profiles in a TypeScript monorepo). Unknown keys are an error.

```toml
[repo]
name = "acme/shop"
github = "acme/shop"            # optional: titles and closing issues through `gh`

[units]
kind = "merges"                 # or "commits" for squash-merge histories
branch = "main"
since = "2026-01-01"

[[profiles]]
name = "pytest"
run = ["tests/test_*.py"]
image = "python:3.12-bookworm@sha256:..."
deps = ["requirements.txt"]
install = "pip install -r /app/requirements.txt"
command = "python -m pytest -p no:cacheprovider --junitxml={junit} {files}"
display_command = "python -m pytest {files}"
```

## What a verifier can and cannot rule out

The grader runs in the agent's container (Harbor's shared verifier mode, as in Terminal-Bench).
It writes reward 0 first, restores the test side from the base commit, installs the hidden tests,
and rewards 1 only if every fail-to-pass and pass-to-pass test passes. An agent cannot pass by
editing tests or test helpers. It could still tamper with the runtime itself (the interpreter,
installed packages), which no verifier sharing its container can rule out.

## Status

Verified end to end on `node --test` and `tsx --test` (see [docs/DOGFOOD.md](docs/DOGFOOD.md)).
Other runners go through the same JUnit path but have not been run yet.

License: MIT-0.
