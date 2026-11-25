# Releasing taskmine

## Versioning

taskmine uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html): `MAJOR.MINOR.PATCH`.

- While the major version is 0, a minor release (0.2.0) may break things: the command line, the
  recipe format (`taskmine.toml`) or the task layout. A patch release (0.1.1) only fixes bugs.
  From 1.0.0 on, only a major release breaks them.
- The interface that versions protect is the command line, the recipe keys, and the files of a
  finished task (including `[metadata.taskmine]` in `task.toml`). The Python modules are not a
  public API.
- The version is written in one place, `__version__` in `src/taskmine/__init__.py`. pyproject.toml
  reads it, `taskmine --version` prints it, and every task records it.
- A pre-release uses a PEP 440 suffix: `0.2.0rc1`, tagged `v0.2.0rc1`.

## Release checklist

1. `main` is green in CI.
2. On a branch, set the new version in `src/taskmine/__init__.py`, the only place it is written.
3. In CHANGELOG.md, give the release its own dated section: move the lines under
   `## [Unreleased]` into `## [X.Y.Z] - YYYY-MM-DD`, leave an empty `## [Unreleased]` above it, and
   update the two link references at the bottom. For 0.1.0, replace `Unreleased` in
   `## [0.1.0] - Unreleased` with the date.
4. Check locally:

   ```sh
   PYTHONPATH=src python3 -m unittest discover -s tests
   ruff check .
   python3 -m build
   python3 scripts/release.py check-dist dist
   python3 scripts/release.py notes X.Y.Z --dated
   ```

5. Open a pull request and merge it once CI is green.
6. Tag the merge commit on `main` and push the tag:

   ```sh
   git switch main && git pull
   git tag -a vX.Y.Z -m "taskmine X.Y.Z"
   git push origin vX.Y.Z
   ```

7. The release workflow (`.github/workflows/release.yml`) then:
   - fails unless the tag is `v` + the package version;
   - takes this version's CHANGELOG.md section as the release notes, and fails if the section is
     missing or has no date;
   - builds the sdist and the wheel and checks that both carry every package file, with scripts
     still executable;
   - creates the GitHub Release `taskmine X.Y.Z` with those notes and attaches `dist/*`;
   - would publish to PyPI with trusted publishing, but that job stays off (`if: false`) until the
     repository is public.
8. Check the release page, install the attached wheel in a fresh virtual environment, and run
   `taskmine --version`.

If the workflow fails before the GitHub Release exists, fix `main`, delete the tag
(`git push --delete origin vX.Y.Z` and `git tag -d vX.Y.Z`) and tag again. Once a release is
published, never move its tag: release a patch instead.

## Before the repository goes public

The repository is private. Making it public publishes the whole history, every branch and the
commit metadata, and that cannot be taken back. Go through this list first; the findings are as of
2026-11-24 and nothing in them has been changed or removed.

### 1. Scan the full history for secrets

Run both scanners on a mirror clone, so that every branch and tag is covered:

```sh
git clone --mirror https://github.com/didhd/taskmine.git taskmine-mirror.git
gitleaks git --log-opts="--all" --redact -v taskmine-mirror.git          # gitleaks 8.19 or later
trufflehog git file://$PWD/taskmine-mirror.git --results=verified,unknown --fail
```

Both are in Homebrew (`brew install gitleaks trufflehog`) and have container images. Every
finding needs a decision: a false positive, or a credential to revoke first and then remove from
history. A pattern search over every blob of all 22 commits (AWS access keys, GitHub, Slack,
OpenAI-style and Google API keys, private key blocks, JWTs) found nothing; it is not a
substitute for the two scanners.

### 2. Decide what to do with the history

- **Files deleted from the tree but present in history**: `docs/media/taskmine.mp4`,
  `docs/media/taskmine-poster.png`, `docs/media/taskmine-preview.gif`, `docs/media/src/make_video.py`,
  `docs/media/src/README.md`, `gen_numbers.py`, `numbers.json` and `qa.json`, removed in 8b0e74a.
  `docs/media/src/README.md` says the video was made with an internal video-generator plugin, its
  AWS 2026 slide theme and logo, Amazon Polly, Nova 2 Lite and the Amazon Ember fonts. The
  "The video" section of `README.md` on `main` adds "check brand guidance before publishing the
  video outside Amazon".
- **Commit metadata**: all 22 commits are authored and committed by `Sanghwa Na
  <sanghwa.me@gmail.com>`.
- **Commit messages** name the private recipe: 29e3dc6 and 41fbbc3 ("pilot recipe").
- The options are to rewrite history (for example `git filter-repo --invert-paths --path docs/media`,
  plus a mailmap for the author) or to publish a fresh repository from one squashed commit.

### 3. Private references in the tree

| what | where |
|---|---|
| The private repository `foxl-ai/pilot`, its paths, packages and PR numbers | `docs/DOGFOOD.md`:1, 5, 11, 58, 64, 71, 96, 103, 109-113; `docs/dogfood-report.md`:14; `examples/foxl-pilot/taskmine.toml`:1, 8, 9, 15, 18, 35, 38, 39, 41, 42, 44, 45, 54, 74 (and the directory name); `README.md`:106; `src/taskmine/deps.py`:9; `src/taskmine/globs.py`:3; `src/taskmine/snapshot.py`:5; `tests/test_units.py`:15-17, 42 (a test file path from that repository) |
| The terminal recording `docs/demo.cast` | event line 103 (GitHub account `didhd`); 188 (`foxl-ai/pilot`, account `didhd`); 189 (`~/workspaces/foxl-ai/pilot`, 7661 merge commits); 190 (the recipe's profile names); 480; 513-848 (67 lines with `pilot-prNNNN` task ids, which are private PR numbers); 767 and 810 (a private EC2 address, 172.31.43.161, the FIPS Bedrock endpoint, paths under `/work/demo`) |
| The demo video | `README.md`:12, a GitHub user-attachments upload of the same session as `docs/demo.cast` |
| A person's name | `LICENSE`:3 (copyright holder); the commit metadata above |
| The GitHub account `didhd` | `pyproject.toml`:47-50 and `CHANGELOG.md`:112-113, the repository's own URLs (expected while it lives there) |
| Private network addresses | `docs/DOGFOOD.md`:93 (a Docker address pool, `10.200.0.0/16`); `src/taskmine/workflow.py`:147 (`10.255.255.255`, used only to pick the outbound interface); test fixtures in `tests/test_workflow.py`:35-46 and `tests/test_gateway.py`:43. None of these names a real host. |
| AWS account IDs | none found (no 12-digit numbers outside the AWS documentation example `123456789012`) |
| Internal hostnames | none found; the only AWS hosts are the public Bedrock endpoints (`bedrock-runtime.<region>.amazonaws.com`, and the FIPS variant named at `src/taskmine/workflow.py`:294) |
| Email addresses | none in the tree except `t@example.com` in tests; the author address is in the commit metadata |

To repeat the search on the current tree:

```sh
git grep -n -I -i -E 'foxl|pilot|didhd|sanghwa|gmail|amazon\.com|a2z|corp\.|172\.31\.|[^0-9.][0-9]{12}([^0-9]|$)|user-attachments'
```

### 4. Other decisions

- Whether the dogfood numbers in `docs/DOGFOOD.md` and `docs/dogfood-report.md`, aggregates of a
  private repository, may be published, and whether to keep `examples/foxl-pilot/` or replace it
  with a recipe for a public repository.
- Third-party code: `src/taskmine/agentcore/pr120/` is vendored from
  `aws-samples/sample-gen-ai-evaluations-workshop` pull request #120. Confirm that the upstream
  license allows redistribution and that `PROVENANCE.md` carries its notice.
- Turn on private vulnerability reporting (Settings > Code security), which SECURITY.md points to.
- Check that the name `taskmine` is free on PyPI, add the trusted publisher, create the `pypi`
  environment, and remove `if: false` from the PyPI job in `release.yml`.
- Protect `main`: require the CI checks and a pull request.
