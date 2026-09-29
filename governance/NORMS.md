# Norm Labels

Version: `1.0.0`

Public registry entries use one of these maturity labels:

- **Provisional**: available for evaluation; interfaces or behavior may change
  in a compatible or documented breaking release.
- **Stable**: reviewed for the stated release contract; breaking changes
  require a deprecation notice and migration guidance.

Every skill page and manifest must make its current version visible. A
provisional label does not waive tests, provenance, license, or security
requirements.

Only norms marked `[stable]` in `CONTRIBUTOR_CONTRACT.md` are enforced by CI.
Provisional norms are review guidance until promoted by a contract release.
