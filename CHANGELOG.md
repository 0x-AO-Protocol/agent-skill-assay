# Changelog

All notable changes to Agent Skill Assay are documented here.

The project uses Semantic Versioning. The public fork release line starts at v0.1.0.

## [Unreleased]

## [0.1.5] - 2026-11-25

### Added

- `compliance/scoped_risk_literature_scan`: for one review cycle, `screen` takes the publications the caller's agent collected and reported per source, keeps the items inside the cycle window, the declared jurisdictions and the declared risk domains, drops repeats, orders and caps the rest, flags the items relevant to clients with a follow-up date, counts the in-scope items, and returns one annotation slot per kept item; `compile` checks that exactly the kept items are annotated within the caps, credits competence minutes only on attestation, and returns the review record, held as pending review, for the caller to store. The annotation check covers presence, keys, enums and caps, not what a summary says. It makes no model call and uses no network, clock, file or environment access.
- `office/underwriting_condition_reply_tracker`: for one loan file, `track` counts the whole days to closing and the days each condition has been open and flags every open condition inside the lead window; `scaffold` adds one draft slot per open condition; `compile` checks the cited documents against the verified file and the citation markers in the texts the caller supplies, applies a figure rule to those texts, composes any text left out from fixed templates, and returns every draft held for operator review. It sends nothing, makes no model call and uses no network, clock, file or environment access.

## [0.1.4] - 2026-11-18

### Added

- `python scripts/registry_sync.py` generates the shared registry files from the bundles under `skills/`: the catalog index, the ROADMAP release table, the install-extras tables, the CONTRIBUTING category table, the `cat:` labels, the extras block of `pyproject.toml` and each category's `__init__.py`. `--check` runs in the test suite, `--add-category` registers a category and `--release-check` checks a release.
- The standard profile for every bundle outside the seven shipped by v0.1.3, checked by `tests/test_standard_profile.py`. `scripts/determinism_guard.py` adds a static guard (a closed import allow-list and forbidden built-ins and attributes, read from the source) and a dynamic guard (every `fixtures/*_params.json` under two hash seeds and an audit hook); sockets are disabled in the tests of these bundles. The guards are pre-merge checks for common mistakes, not proof and not isolation.
- AO manifest schema: the optional fields `origin` (`ao_standard`, `ao_defined`, `third_party`) and `serves_dwa` (O*NET 31.0 Detailed Work Activity identifiers); a `third_party` bundle names an `issuer.org` other than AO.
- `docs/contributing/release_train.md` describes the weekly release process.
- `finance/approved_financial_model_runner`: `run` evaluates one model version from a registry document supplied with the call on prepared tables, each quantity a formula in a closed arithmetic grammar, and returns the requested figures as rounded decimal strings with an archive record for the caller to store; it requires every table to be marked `passed` upstream and to match its declared SHA-256. `verify` rebuilds a run and compares its archive document with a stored copy, character for character. It makes no model call and uses no network, clock, file or environment.

### Changed

- The catalog index and the CONTRIBUTING category table are generated. The index lists categories in alphabetical order; a new skill's description is the first sentence of its manifest `short_description`, and the seven skills shipped by v0.1.3 keep their descriptions. The category table counts the registered skills.
- The New Skill Proposal form takes the category as text, with a link to the category table. Example scripts and agent-loop rows are optional for new skills.

## [0.1.3] - 2026-10-28

### Added

- `dev_tools/upgrade_gate`: `digest` summarises host-supplied repository events since a date, with version jumps, breaking terms and commits touching paths of interest; `assess` gives each pinned dependency a verdict, `hold`, `upgrade`, `test` or `unknown`, with a target and reason codes from eight ordered rules. It never fetches; `pypi` and `npm` only, with its own version comparator; offline and deterministic.

### Fixed

- `python scripts/provenance.py --check` also reports a bundle whose `manifest.yaml` cannot be read or constructed (bytes that are not valid UTF-8, an impossible date, an integer too long to convert, a tagged value the YAML constructors reject, nesting too deep to parse) as a failure of that bundle, and goes on to the next bundle. A recorded `content_sha256` that is neither a string nor null is shown by its type, such as `recorded=<int>`, so that a value Python cannot print, such as a very long hexadecimal integer, no longer stops the check.

## [0.1.2] - 2026-10-22

### Added

- `finance/period_close`: `validate` returns the findings of a period's journal against its chart of accounts; `close` adds a trial balance, income statement, balance sheet, direct-method cash flow and ratios, fails closed on a blocking finding unless `soft_close` is set, and names recurring items that are missing or out of line against prior periods without filling them in. Single currency and single entity; offline and deterministic.

### Fixed

- `python scripts/provenance.py --check` reports a bundle whose `manifest.yaml` is not valid YAML, or whose manifest or `provenance` block is not a mapping, as a failure of that bundle and goes on to the next bundle. It no longer stops with a traceback before printing its report.

## [0.1.1] - 2026-10-15

### Added

- AO manifest schema: a second `provenance` form for bundles written in this repository, `anchor: repository` with `content_sha256` and `authored_on` (the UTC date of the commit that last refreshed the hash). The archive form used by the four imported bundles is unchanged.
- `python scripts/provenance.py --hash <bundle_dir>` prints the content hash of one bundle.
- `office/outreach_manager`: `compile_search` turns a target specification and a platform profile into a partitioned search plan, with a ready search URL per query and an explicit fit rubric; `ingest_leads` de-duplicates, scores and tabulates exported rows against that plan and returns lead records and a review table. Offline and deterministic; bundled profiles `linkedin` and `demo`.

### Removed

- `.github/workflows/codeql.yml`. Its results could not be processed while CodeQL default setup is enabled; default setup stays on and continues to scan the repository.

## [0.1.0] - 2026-09-25

### Added

- Initial AO fork under the Agent Skill Assay name.
- Runtime package `skill_assay` and CLI command `skill-assay`.
- v0.1 registry containing four AO-authored skill bundles: `security/prompt_injection_firewall`, `monitoring/kpi_gate`, `wellness/mental_coach`, and `monitoring/business_diagnostic`.
- Prompt Injection Firewall hardening for fail-closed resource caps, documented detector coverage, profile-consistent `is_safe` semantics, and redacted unsafe decoded text in outputs and telemetry.
- Governance artifacts for merge acceptance criteria, contributor contract, norm labels, deprecation path, review SLA, support policy, and runtime support-status reporting.
- `THIRD_PARTY_NOTICES` for MIT framework inheritance.

### Changed

- Public package, import, CLI, environment, and project configuration names now use Agent Skill Assay identifiers.
- The public registry is scoped to AO-authored v0.1 skills; later skills are tracked in `ROADMAP.md`.
