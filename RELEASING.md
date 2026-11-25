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
