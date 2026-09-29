# Install Extras

Agent Skill Assay ships as one wheel. The four v0.1 skill bundles are included
with the base install; extras add optional runtime dependencies.

## Quick Reference

| Goal | Install command |
| :--- | :--- |
| Framework and all four skills | `pip install agent-skill-assay` |
| One bundled skill | `pip install "agent-skill-assay[<category>_<skill>]"` |
| Every bundled skill dependency | `pip install "agent-skill-assay[all]"` |
| Development and tests | `pip install -e ".[dev,all]"` |
| Agent SDK examples | `pip install -e ".[dev,all,agents]"` |

Every extra installs the core package as well. Empty skill extras are retained
so documentation and install commands remain stable when a manifest gains a
dependency.

## Category extras

| Extra | Skills | Packages |
| :--- | :--- | :--- |
| `monitoring` | `monitoring/business_diagnostic`, `monitoring/kpi_gate` | *(none today)* |
| `security` | `security/prompt_injection_firewall` | *(none today)* |
| `wellness` | `wellness/mental_coach` | `google-genai` |

## Skill extras

| Extra | Registry ID | Packages |
| :--- | :--- | :--- |
| `monitoring_business_diagnostic` | `monitoring/business_diagnostic` | *(none today)* |
| `monitoring_kpi_gate` | `monitoring/kpi_gate` | *(none today)* |
| `security_prompt_injection_firewall` | `security/prompt_injection_firewall` | *(none today)* |
| `wellness_mental_coach` | `wellness/mental_coach` | `google-genai` |

## Meta extras

| Extra | Purpose | Packages |
| :--- | :--- | :--- |
| `all` | Every bundled skill runtime dependency | `google-genai` |
| `agents` | Agent SDK integrations | `anthropic`, `openai`, `google-genai` |
| `dev` | Development and test tooling | `pytest`, `pytest-mock`, `flake8`, `black`, `setuptools` |

## Agent SDK extras

| Extra | Package |
| :--- | :--- |
| `gemini` | `google-genai` |
| `claude` | `anthropic` |
| `openai` | `openai` |
| `bedrock` | `boto3` |
| `agents` | Gemini, Claude, and OpenAI SDKs |

## Loader Behavior

`SkillLoader.load_skill()` checks manifest requirements before loading a skill.
When a requirement is missing, the error includes the matching
`agent-skill-assay[...]` install hint. The loader never installs packages.
