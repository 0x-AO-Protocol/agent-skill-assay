# Changelog

All notable changes to Agent Skill Assay are documented here.

The project uses Semantic Versioning. The public fork release line starts at v0.1.0.

## [Unreleased]

### Fixed

- `python scripts/provenance.py --check` also reports a bundle whose `manifest.yaml` cannot be read or constructed (bytes that are not valid UTF-8, an impossible date, an integer too long to convert, a tagged value the YAML constructors reject, nesting too deep to parse) as a failure of that bundle, and goes on to the next bundle. A recorded `content_sha256` that is not a string is shown by its type, such as `recorded=<int>`, so that a value Python cannot print, such as a very long hexadecimal integer, no longer stops the check.

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
