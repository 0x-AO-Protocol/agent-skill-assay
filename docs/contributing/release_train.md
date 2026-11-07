# Release train

New and updated skills ship in weekly releases of the `agent-skill-assay` package.

## Weekly cadence

- There is one fixed weekly cut-off. Work finished by the cut-off ships that week; later work takes the next release.
- A week with no finished work has no release.
- Patch versions follow in order: v0.1.4, v0.1.5, and so on.

## How a skill reaches a release

- Each skill is prepared on its own branch and changes only `skills/<category>/<name>/` and `docs/skills/<name>.md`.
- The shared files are generated once per release with `python scripts/registry_sync.py --write`: the catalog index, the ROADMAP release table, the install-extras tables, the category table, the `cat:` labels and the extras in `pyproject.toml`. `--check` runs in the test suite.
- To test a skill branch locally, run `python scripts/registry_sync.py --write`, then `pytest skills/` and `pytest tests/`, then restore the files the generator changed (`git restore`) and commit only the skill paths.
- Every new skill must meet the [standard profile](../../CONTRIBUTING.md#standard-profile): deterministic, no model call. Its pre-merge guards run by machine and look for common mistakes; maintainer review still applies.

## What a release pull request contains

- The bundles and their catalog pages, and the generated shared files.
- Any new category, registered with `python scripts/registry_sync.py --add-category <name> --purpose "<text>"`.
- The version in `pyproject.toml` and `CITATION.cff`, and the `CHANGELOG.md` section. Its Added list has one line per skill that starts with `` `<category>/<name>`: ``.
- In the body: the checklist below, the closing keywords for the shipped skill requests, and one short note per skill on any difference from its request.

Before opening it, run `python scripts/registry_sync.py --release-check v0.1.N`.

## Checklist

- [ ] The seven required checks are green on the pull request head.
- [ ] Each set of skills in this release closed its review with no open Critical or High finding.
- [ ] The per-skill standard-profile checks ran by machine and passed.
- [ ] The public-text lint is clean.
- [ ] No file outside the skill paths changed, other than generated shared files and the release files (version, changelog, citation).

## Approval, merge and publication

- One approval, then **Squash and merge**.
- The tag is pushed only after the required checks of the merge commit are confirmed.
- The GitHub Release publishes the package to PyPI. An install-and-load check then runs in a fresh virtual environment.

## After approval

- Nothing is pushed to an approved release pull request.
- A Medium or Low defect found later ships and is fixed in a later release.
- Only a valid Critical or High defect may withdraw a skill, and that needs a new approval.
