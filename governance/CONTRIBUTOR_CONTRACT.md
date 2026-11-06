# Contributor Contract

Version: `norms-1.1.0`

Contributors agree to:

### [stable] N-1 Pull requests

Submit changes through a pull request. Direct pushes to the protected default
branch are not part of the contribution path.

### [stable] N-2 Test evidence

Keep code, tests, manifests, catalog pages, and examples consistent. Add or
update tests for behavior changes.

### [stable] N-3 Provenance and notices

Avoid credentials and private data. Document provenance for imported or
third-party material and preserve the declared license and notices.

### [provisional] N-4 Review timing

Review timing follows `governance/REVIEW_SLA.md` and may be adjusted by a
versioned contract update.

### [stable] N-5 Security reporting

Report security issues through the repository's private security channel.

### [stable] N-6 Standard profile

Every skill bundle outside the frozen legacy set must meet the standard
profile: it must be deterministic and make no model call; its runtime modules
must not use a network, a process, randomness, a clock, the environment or
file access; and it must carry the manifest fields, catalog page and tests
that the profile lists in `CONTRIBUTING.md`. The legacy set is the seven
bundles shipped by v0.1.3. It never grows, and a bundle leaves it only through
a recorded upgrade, after which the bundle meets the profile. The CI checks
for the profile are pre-merge checks for common mistakes; they are not proof,
and they do not isolate anything.

Maintainers may reject changes that violate these terms or the repository's
security and publication rules.
