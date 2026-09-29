# Changelog

All notable changes to Agent Skill Assay are documented here.

The project uses Semantic Versioning. The public fork release line starts at v0.1.0.

## [Unreleased]

- No user-facing changes yet.

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
