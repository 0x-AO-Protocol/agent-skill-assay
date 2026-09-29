# Agent Skill Assay Roadmap

This roadmap describes public registry work.

## v0.1

The v0.1 registry contains four AO-authored skills:

| Skill | Status |
| :--- | :--- |
| `security/prompt_injection_firewall` | Registered |
| `monitoring/kpi_gate` | Registered |
| `wellness/mental_coach` | Registered |
| `monitoring/business_diagnostic` | Registered |

## Planned skills

These skills are planned for separate v0.1.x releases. They are not v0.1 catalog entries.

| Skill | Planned release | Economic-effect declaration |
| :--- | :--- | :--- |
| `outreach_manager` | v0.1.1 | **Claim:** Performs the task in deterministic code instead of LLM generation, reducing LLM token consumption.<br>**Baseline task:** The same task performed by an LLM agent without this skill (prompt-only).<br>**Metric:** LLM tokens (input + output) per task run.<br>**Method:** Run the skill's reference fixtures through (a) the baseline agent and (b) the same agent calling the skill; publish the median tokens per run for both and the reduction. |
| `period_close` | v0.1.2 | **Claim:** Performs the task in deterministic code instead of LLM generation, reducing LLM token consumption.<br>**Baseline task:** The same task performed by an LLM agent without this skill (prompt-only).<br>**Metric:** LLM tokens (input + output) per task run.<br>**Method:** Run the skill's reference fixtures through (a) the baseline agent and (b) the same agent calling the skill; publish the median tokens per run for both and the reduction. |
| `upgrade_gate` | v0.1.3 | **Claim:** Performs the task in deterministic code instead of LLM generation, reducing LLM token consumption.<br>**Baseline task:** The same task performed by an LLM agent without this skill (prompt-only).<br>**Metric:** LLM tokens (input + output) per task run.<br>**Method:** Run the skill's reference fixtures through (a) the baseline agent and (b) the same agent calling the skill; publish the median tokens per run for both and the reduction. |

These are declarations, not measurements. No token-reduction numbers will be
published until measurements are complete.

## Near-term registry work

- Keep every v0.1 skill at trust tier L0 until a later registry audit raises it.
- Publish the governance and support-policy artifacts.
- Maintain deterministic offline tests and package smoke tests.
- Add planned skills only after their implementation, tests, and declarations
  are reviewed.
