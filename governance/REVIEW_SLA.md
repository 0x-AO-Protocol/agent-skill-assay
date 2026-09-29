# Review SLA

Version: `1.0.0`

## Service level

The target for the first response is seven calendar days. The target for a
decision is twenty-one calendar days after the change is reviewable. A
security report is handled through the private security channel and is not
discussed in a public issue.

## Separated roles

- **Criteria owner:** maintains the acceptance criteria and handles appeals.
- **Reviewer:** checks scope, tests, provenance, and public wording.
- **Release owner:** performs the release or visibility change.
- **Roadmap owner:** maintains planned work and release sequencing.

One person may hold more than one role only when the repository owner records
the exception in the pull request.

## Escalation

If the response or decision target is missed, the contributor may link the
request to the repository owner for escalation. The dates and any reason for
delay are recorded in the pull request.

## Required review

Runtime, skill, packaging, security, and governance changes require one
qualified review before merge. Documentation-only changes may use maintainer
review. The acceptance criteria remain binding for every category.
