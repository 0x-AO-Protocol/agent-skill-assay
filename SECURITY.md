# Security Policy

## Supported Versions

Use a current Agent Skill Assay release to receive security fixes. We patch vulnerabilities only for supported versions.

| Installed version | Security support | CLI advisory |
| :--- | :--- | :--- |
| **>= 0.1.0** | Supported. Security reports accepted and patched here. | Silent |
| **< 0.1.0** | Unsupported. | Upgrade required |

The support window is defined in the machine-readable
[`support_policy.json`](support_policy.json) file and is checked by the runtime.

## Skill execution model

Loading a skill runs its `skill.py` in your host process, with full filesystem and environment access. Agent Skill Assay does not sandbox skills; trust is based on provenance (where a skill came from and who reviewed it), not runtime isolation. Before loading skills you did not write, review the [skill trust model](docs/security/skill-trust-model.md).

## Reporting a Vulnerability

We take security seriously. If you discover a vulnerability in Agent Skill Assay:

1. **Do not create a public GitHub issue.**
2. Email m@orblabs.ch or use the repository's private security reporting channel.
3. Include a proof of concept if possible.

Please do not include secrets or personal data in a public issue.
