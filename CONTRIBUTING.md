# Contributing to taskmine

Bug reports, recipes for new test runners and pull requests are welcome. AGENTS.md describes the
code, the conventions and the invariants; read it before a change that touches more than one
module.

## Set up

You need Python 3.11 or later and git. Node 24 runs the end-to-end and text-probe tests, which
skip without it. taskmine runs from the source tree with no install step:

```sh
git clone https://github.com/didhd/taskmine.git && cd taskmine
PYTHONPATH=src python3 -m taskmine --help
```

Docker, Harbor and AWS are only needed to verify tasks and run agents, never for the tests.

## Before you open a pull request

```sh
PYTHONPATH=src python3 -m unittest discover -s tests   # under a minute
ruff check .                                           # pip install ruff==0.17.6
```

- **Test what you change, for real.** Build a real git repository (`tests/support.py`), write
  real files and run real local processes. Mocks are not accepted. If a test needs a tool that
  may be missing, skip with a reason.
- **Keep the core standard library only.** A new dependency goes into an optional group in
  pyproject.toml and is imported inside the function that uses it.
- **Keep output deterministic**: the same inputs give the same bytes.
- **Write plain English**: short sentences, no em dashes, no emojis.
- **Add a line to CHANGELOG.md** under `## [Unreleased]` when users will notice the change.
- **Fix a bug in its own commit**, together with the test that shows it.

Two things are never merged: a path that hands a cloud credential to a task or agent container,
and any edit to the vendored `src/taskmine/agentcore/pr120/`.

## Reporting

Use the issue templates for bugs and feature requests. Include `taskmine --version`, the command
and its output, and the recipe, with anything private removed. Report security problems
privately, as SECURITY.md describes, not in an issue.

By contributing you agree that your contribution is licensed under the repository's license,
MIT-0.
