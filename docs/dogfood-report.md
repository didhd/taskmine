# taskmine dataset report

| step | units | left out here, by reason |
|---|---:|---|
| change units in the history | 7661 | not-a-pr 5512, no-test-change 629, no-runnable-test 529, superseded 281, gold-too-large 120, no-code-change 31, too-many-run-files 7 |
| candidates (scan) | 552 | - |
| drafts built | 552 | stopped-at-the-time-limit 4 |
| validated in Docker | 548 | source-text-only 179, tests-fail-with-gold 167, no-fail-to-pass 26 |
| accepted by execution | 176 | instruction-leak 3 |
| finished tasks | 173 | |

## Finished tasks

- by profile: forge-server-node-test 88, foxl-node-test 76, code-pages-node-test 9
- fail-to-pass tests per task: median 5, p10 1, p90 19, max 48
- pass-to-pass tests per task: median 2, p10 0, p90 42, max 160
- gold change, files: median 7, p10 3, p90 27, max 39
- gold change, lines: median 287, p10 53, p90 1151, max 1971
- instruction sources: title 173, tests 173, contract 155, issue 42
- tasks naming a new name, module or exact text: 155
- tasks whose issue text was left out by the leak check: 23

## Source-text tests

Fail-to-pass tests that pass only because the source TEXT changed (the text probe gives test code the base text of changed files; these tests then fail): 1424 of 2759 fail-to-pass tests in 355 drafts. They are never required. Drafts left with no behavioural fail-to-pass test: 179. Accepted tasks that had some dropped: 115.

Validation time per accepted task (all runs, seconds): median 38.75, p10 9.9, p90 158.2, max 964.8

