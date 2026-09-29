# Merge Acceptance Criteria

Version: `1.0.0`

Every pull request that changes runtime code, a skill bundle, packaging, or
public governance material must satisfy the criteria below before merge.

## Binding at merge

These criteria are published before admission decisions and are binding at
merge. A later revision cannot retroactively reclassify work that has already
merged. Appeals must identify the criterion at issue and be filed in writing
in the repository.

1. The change has a focused description and an owner.
2. Relevant tests are added or updated and pass locally and in CI.
3. Public text contains no private mechanisms, private findings, or
   non-public business details.
4. A changed skill keeps its manifest, card, catalog page, examples, and tests
   synchronized.
5. Security-sensitive changes include a negative test and a fail-closed
   behavior check.
6. The pull request has the required review under the review policy.
7. The final merge commit is the accepted version of the change; follow-up
   fixes require a new pull request or an explicitly recorded amendment.

## Evidence model (from v0.2)

The v0.2 evidence model has three axes:

1. replaceability score;
2. declared economic effect;
3. observed opt-in usage after the applicable grace period.

v0.1 admission is decided by the published criteria above. The v0.2 axes are
declared here so that they cannot be introduced as an undisclosed admission
condition later.

## Appeals

An appeal must cite the published criterion, the relevant evidence, and the
requested remedy. The criteria owner records the response in the pull request
or an issue linked from it.
