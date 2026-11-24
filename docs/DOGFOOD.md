# Dogfood: foxl-ai/pilot, 2026-11-24

One run of taskmine over a private TypeScript monorepo: every pull request merged into `main`
between 2026-06-01 and 2026-11-24, with the recipe in
[examples/foxl-pilot/taskmine.toml](../examples/foxl-pilot/taskmine.toml). The dataset is not in
this repository (it contains the monorepo's source); only aggregate numbers are here.

## Result

- **173 Harbor tasks** from three test runners: `forge/server` (`tsx --test`, 88),
  `foxl/tests` (`tsx --test`, 76), `packages/code` (`node --test`, 9).
- **Harbor 0.27.0** over all 173: the `oracle` agent scored 1.0 on 173 of 173 trials and the `nop`
  agent 0.0 on 173 of 173, with 0 exceptions.
- **Harbor 0.22.0** (the version eval-harvester pins) over 12 of them: `oracle` 12 of 12 at 1.0,
  `nop` 12 of 12 at 0.0.
- **`taskmine check`**: 173 of 173 pass, on the box and again on the copy moved to the workstation.
- **Reproducibility**: the box built every draft from a filtered, shallow transfer clone. Ten tasks
  picked with a fixed seed were rebuilt on the workstation from the full clone; all ten have the same
  draft digest as the draft that was validated and as the task that shipped.
- No model was called to build, validate or grade anything.

## The funnel

| step | units | left out here, by reason |
|---|---:|---|
| change units in the history | 7661 | not-a-pr 5512, no-test-change 629, no-runnable-test 529, superseded 281, gold-too-large 120, no-code-change 31, too-many-run-files 7 |
| candidates (scan) | 552 | - |
| drafts built | 552 | stopped-at-the-time-limit 4 |
| validated in Docker | 548 | source-text-only 179, tests-fail-with-gold 167, no-fail-to-pass 26 |
| accepted by execution | 176 | instruction-leak 3 |
| finished tasks | 173 | |

`not-a-pr` is mostly the merge trains' own merges into `main`: their members are units already.
The full report, with the distributions, is [dogfood-report.md](dogfood-report.md).

## What the tasks look like

- Fail-to-pass tests per task: median 5 (p10 1, p90 19). Pass-to-pass: median 2 (p90 42).
- Gold change: median 7 files and 287 lines (p90 27 files, 1,151 lines).
- Instruction sources: the pull request title in 173, the fail-to-pass test names in 173, a computed
  interface contract (new names, modules or exact text) in 155, the closed issue in 42. In 23 more
  the issue was left out because the leak check found gold code in it.

## Findings

**Half of the fail-to-pass tests check text, not behaviour.** The text probe re-runs the gold state
giving test code the base version of every changed file it reads, while imports still load the gold
code. 1,424 of 2,759 fail-to-pass tests (52%) failed under it: they pass because the source TEXT
changed (a regular expression over a file, a string the code must contain). 179 drafts had no
behavioural fail-to-pass test left and were rejected; 115 accepted tasks had some such tests
removed from their required set. A benchmark that kept them would ask agents to guess a spelling.

**Most "fails even with the fix" are one missing install step.** Of the 167 drafts whose new tests
failed with the merged change applied, the gold run's log shows ([gold-failure-causes](#reading-the-causes)):

| cause | drafts |
|---|---:|
| a root-workspace package (`@foxl/*`) is not linked: the foxl profile installs `foxl/` only | 130 |
| an assertion fails on Linux (the suites are written on macOS) | 16 |
| needs a network or a local server (validation runs with `--network none`) | 9 |
| another package is missing from the install | 6 |
| Playwright's browser is not in the image | 6 |

Linking the root workspace in the foxl profile's install is the largest single lever for the next
run; it is a recipe change, not a tool change.

## Bugs the run found in taskmine, fixed during it

Each fix has a unit or end-to-end test in `tests/`.

1. A 1 MB snapshot cap left out `foxl/server/api/server.ts` (1.5 MB of source every server test
   imports). Text files now stay up to `max_file_bytes`; only binary files are always left out.
2. Dependency layers: ignoring the repository's own version fields turned 69 distinct installs
   into 23, which Docker then shares.
3. A file-level static check for source-reading tests threw away behavioural tests that shared a
   file with one grep. Replaced by the per-test, execution-based text probe.
4. The contract scan took minutes on a test with thousands of fixture strings (each check scans a
   300 MB snapshot). Bounded at 150 checks per kind, marked `truncated`.
5. The leak check reported the test file paths the instruction names on purpose, and comment lines
   that repeat a test's name. Contract text that is itself a line of the gold code (it comes from a
   test the probe already dropped) is now left out instead of refusing the task: refusals went from
   73 to 3.
6. Validation sorted drafts by dependency group, so every worker waited behind the same first
   install (75% of the box idle). Groups are now interleaved.

## Infrastructure notes

- One m7i.8xlarge (32 vCPU) for 2 h 6 min: 552 drafts built in about 13 min (24 processes),
  validated in about 35 min (24 workers), Harbor sweeps about 20 min.
- Harbor at `-n 16` needs more Docker networks than the default address pools allow after a few
  interrupted runs (27 had leaked). The first Harbor attempt lost 141 of 152 trials to
  `could not find an available, non-overlapping IPv4 address pool`; with
  `{"default-address-pools": [{"base": "10.200.0.0/16", "size": 24}]}` in `/etc/docker/daemon.json`
  the rerun had 0 exceptions.
- Four drafts were still running when the box reached its time limit and were stopped
  unvalidated: pilot-pr5355, pilot-pr5378, pilot-pr5473, pilot-pr8479.
- The log of the first validation segment was overwritten when validation was restarted with
  interleaving; every per-draft summary and run log is intact.

## Reading the causes

`gold-failure-causes` classifies each draft by the first matching signature in its gold run's test
output (Playwright install message, `Cannot find package '@foxl/...'`, other `ERR_MODULE_NOT_FOUND`,
`ECONNREFUSED`/`ENOTFOUND`, ...); a draft matching none is counted as an assertion failing on Linux.

## Reproduce

```sh
taskmine scan     --recipe examples/foxl-pilot/taskmine.toml --repo <clone> --out out
taskmine meta     --recipe examples/foxl-pilot/taskmine.toml --out out
taskmine build    --recipe examples/foxl-pilot/taskmine.toml --repo <clone> --out out --jobs 24
taskmine validate --recipe examples/foxl-pilot/taskmine.toml --out out --jobs 24 --keep-images
taskmine finalize --recipe examples/foxl-pilot/taskmine.toml --out out
taskmine check    out/tasks
harbor run -y -p out/tasks -a oracle -n 16 --no-delete
harbor run -y -p out/tasks -a nop -n 16
```
