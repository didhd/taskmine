# The recipe, `taskmine.toml`

`taskmine init` writes a starter recipe. Unknown keys are errors, so a typo fails loudly.

```toml
[repo]
name = "acme/shop"
github = "acme/shop"      # leave it out for a local clone: no pull request metadata is read

[units]
kind = "merges"           # or "commits" for squash-merge histories
branch = "main"
since = "2026-01-01"      # optional
# pr_patterns = ['^Merge pull request #(\d+) from ']   # one group: the PR number

[tests]
# paths = ["**/tests/**", "**/test/**", "**/*.test.*", "**/test_*.py"]   # what counts as test side

[snapshot]
# max_file_bytes = 5000000     # larger text files are left out of the task image
# exclude = ["docs/media/**"]  # never in the image

[limits]
# max_gold_files = 40
# max_gold_lines = 2000
# max_run_files = 6

[[profiles]]              # one per test runner
name = "node-test"
run = ["test/*.test.mjs"]           # the test files this runner owns (globs; * never crosses /)
image = "node:24-bookworm"
# workdir = "packages/api"          # where the command runs, relative to the repo root
# deps = ["package.json", "package-lock.json"]   # copied before install, so installs are cached
# install = "npm ci"
command = "node --test --test-reporter=junit --test-reporter-destination={junit} {files}"
display_command = "node --test {files}"
# timeout_sec = 900
# env = { CI = "1" }
text_probe = "node"                 # node for JavaScript runners; "static" for everything else
```

## Test commands that write JUnit

`{files}` is the test files and `{junit}` the report path. The grader reads only JUnit XML.

| runner | `command` |
|---|---|
| node:test, tsx | `node --test --test-reporter=junit --test-reporter-destination={junit} {files}` |
| pytest | `python -m pytest -p no:cacheprovider -o junit_family=xunit1 --junitxml={junit} {files}` (`xunit1` puts the file path in the report) |
| vitest | `npx vitest run --reporter=junit --outputFile={junit} {files}` |
| jest | `npx jest --reporters=jest-junit {files}`, with `JEST_JUNIT_OUTPUT_FILE={junit}` in `env` |
| go | `gotestsum --junitfile {junit} -- {files}` |

Monorepos need one profile per runner, each with its own `workdir`, `deps` and `install`.
`examples/acme-web/taskmine.toml` in the taskmine repository is a three-profile TypeScript
example.
