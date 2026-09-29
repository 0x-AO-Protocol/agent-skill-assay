# Deprecation Policy

Version: `1.0.0`

When a public skill, API, manifest field, or CLI behavior is deprecated:

1. Record the change in `CHANGELOG.md`.
2. Mark the affected documentation and manifest as deprecated.
3. Explain the replacement and migration path.
4. Keep the old behavior for the support window stated in
   `support_policy.json`, unless it creates a security risk.
5. Remove it only in a documented release with a final migration notice.

Security removals may happen sooner when retaining the behavior would create
unacceptable risk; the release notes must explain the reason at a high level.
