## What changes, and why

<!-- One paragraph. Link the issue if there is one. -->

## How it was checked

<!-- The tests you added or changed, and anything you ran by hand (a mine/verify/bench run, with its numbers). -->

- [ ] `PYTHONPATH=src python3 -m unittest discover -s tests` passes
- [ ] `ruff check .` passes
- [ ] New behaviour has a test with real git repositories, files or processes, and no mocks
- [ ] A bug fix is in its own commit, with the test that shows the bug
- [ ] CHANGELOG.md has a line under `## [Unreleased]`, if users will notice the change

## Invariants (AGENTS.md)

- [ ] No cloud credential can reach a task or agent container
- [ ] `src/taskmine/agentcore/pr120/` is unchanged
- [ ] The core still needs only the standard library
