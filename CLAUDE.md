# Repository Instructions

## Repository role

Agent Skill Assay is AO's open Hard-Skill registry and runtime. It contains
the registry framework, public governance, and the four v0.1 AO skill bundles.

## Test discipline

Add or update tests before changing behavior. Run the relevant focused tests and
the full suite before merge. Do not accept an automated patch without reading
the diff and reproducing the result locally.

## Open boundary

This repository must not contain private or proprietary AO code, content, or
assets. Public files should describe only the open framework, open registry
bundles, and reproducible usage.

## Public text

Public files contain effects, interfaces, evidence, and reproducible usage
only. Do not commit private findings, credentials, private business details,
unsupported claims, or internal decision records.

## Terminology

Use `bundle` for the public registry artifact. Use `evaluate` for skill-local
evaluation actions. Keep business-charter terminology limited to the user's
own declared business charter.

## Contribution norms

The acceptance criteria are binding at merge. Follow
`governance/CONTRIBUTOR_CONTRACT.md`, record provenance for imported material,
and use pull requests for all changes to the protected default branch.

## Secrets

Never commit credentials or `.env` files. The CI secret scan covers the
working tree. Findings in inherited history are triaged without rewriting history;
new findings must be fixed before merge.

## Repository interaction

Use the AO-owned repository and its read-only source archive as configured for
this stage. Do not contact unrelated external repositories or copy unreviewed
material into the registry.
