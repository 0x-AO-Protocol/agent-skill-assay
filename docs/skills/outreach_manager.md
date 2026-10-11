# Outreach Manager

**Domain:** `office`
**Skill ID:** `office/outreach_manager`
**Issuer:** [@mrmasa88](https://github.com/mrmasa88) ([AO](https://github.com/0x-AO-Protocol)) · **Project contact:** m@orblabs.ch
<!-- skill-doc-meta:begin -->
**Version**: `0.1.0` — 11 Oct 2026
<!-- skill-doc-meta:end -->
**Maturity:** Provisional — available for evaluation; interfaces or behavior may change in a compatible or documented breaking release.
**Recommended install:** `pip install "agent-skill-assay[office_outreach_manager]"`. See [Install extras](../usage/install_extras.md).

[Skill Library](README.md) · [Testing](../TESTING.md)

Deterministic **outreach planner and lead ingester** for platforms that have both account search and direct messaging: `{target_spec, platform}` → `plan`, and `{plan, rows}` → `leads[]` + `review_table`. `compile_search` turns a structured target specification and a versioned platform profile into a search plan whose queries are partitioned so that the profile's per-query result cap is respected, each with a ready `search_url`, plus pacing taken from the profile and an explicit fit rubric. `ingest_leads` normalises exported rows through the profile's column mapping, de-duplicates them on `profile_key`, scores every row with its reasons, flags off-target rows and returns lead records (`stage` `NEW`) with a spreadsheet-safe review table. Closed error registry, fail-closed contract errors; `execute()` is a pure function: no network, no clock, no file write, identical input → identical output. The v0.1 interface is defined by this bundle.

The skill **never touches the platform** and **sends nothing**. Searches are executed and results are extracted by an operator-side actuator (browser automation or an existing export tool); the host model owns the targeting conversation whose output is the `target_spec`; nothing proceeds to messaging before operator approval of the review table.

> **Skill chains:** Run after export or middleware steps in host-defined order — see [Skill chaining](../usage/skill_chaining.md).

## When not to use

- **Not an actuator.** It never runs a search, opens a URL or extracts a result. `search_url` is for the operator's actuator; the host does not open it.
- **Not a message writer.** It generates no prose; message text is outside this version.
- **Not for platforms without a profile.** A `platform` with no bundled profile and no `profile_override` is a contract error, never a guess.

## Design split

- **The platform profile is versioned data** (`kb/platforms/<platform>.json`): search grammar, URL form, filter ids, per-query result cap, pacing and export column mapping, each file timestamped and sourced. Platform changes land as data-only revisions. An operator may pass a full profile in the request as `profile_override`; it is validated exactly like a bundled file, and the plan records `profile_origin` (`bundled` or `override`).
- **The skill is deterministic transformation only**: `target_spec` → plan; rows → scored leads. The rubric is explicit in the plan, and every lead carries its scoring reasons.
- **The host model owns targeting**; the actuator and the approval step are operator-side and outside the skill.

## Agent-loop contract

- The `compile_search` output (the plan) is what the host hands to the operator's actuator; the skill does not execute it. The operator confirms the host of the generated URLs before the actuator runs a plan.
- The `ingest_leads` output (lead records, review table, flags) goes to the operator for approval; nothing proceeds to sequencing or sending before that approval. This version sends nothing.
- An error envelope means the request did not meet the contract: correct the input rather than retry with guessed values.

## Validation order (fail-closed, deterministic)

Each action runs a numbered check list in execution order and returns the first failing check's code with a `detail.reason` that names the check; the full lists are in the bundle's `instructions.md`. In outline:

1. Request envelope: an object, a known `action`, no foreign key, a well-formed `platform`, the action's inputs of the right type.
2. Profile: the override's checks (or an unknown bundled id), then the bundled file's checks.
3. `compile_search`: the specification's shape, its size caps, its values against the closed sets, patterns and profile filter lists; then the generated plan's caps (query count, query length, `search_url` length).
4. `ingest_leads`: the plan's shape and query cap; `PLAN_MISMATCH` when `platform`, `profile_version`, `profile_origin` or `plan_id` disagree with the resolved profile; `existing_leads`; then the rows, row by row, with every check of one row before the next.

`PLAN_MISMATCH` is a consistency check between a plan and its id: `plan_id` is a digest of the plan body that anyone can recompute, so an edited plan is rejected. A bad row fails the whole call; a silently dropped row would be a guess, and the count identity `rows_in = deduped + off_target + kept` leaves no bucket for one.

## Bundle layout

The skill lives in `skills/office/outreach_manager/`. [Skill anatomy](../introduction.md#skill-anatomy). **Contract** — see Manifest Details below. **Assurance** — `test_skill.py` in the bundle.

### Effect (`skill.py`)

Pure Python: profile checks, specification checks, term sanitisation and query construction from the profile's grammar, the partition rule, search URL construction, profile-key derivation, scoring, CSV neutralisation. Standard library only; the closed error registry and the one-code fault registry (`INTERNAL_ERROR`) are module constants.

### Directive (`instructions.md`)

Registry id, when not to use, what the host and the operator own, the agent-loop contract, inputs, the partition rule, query grammar and escaping, search URL construction, profile origin, pacing, the scoring table with its row-or-query column, de-duplication and ids, the review table rules, platform profiles and overrides, the size caps, and both validation check lists verbatim.

### Reference (`schemas/`)

JSON Schemas for the target specification, the plan, the lead record and the platform profile (documentation; the runtime uses explicit standard-library checks).

### Corpus (`kb/`)

`kb/platforms/` holds the platform profiles; `kb/seniority_terms.json` holds the generic English title terms per seniority level used by the row rule of the `seniority` component.

#### Platform profiles

Two profiles ship, both validated against `schemas/platform_profile.schema.json` and the in-code checks on every call:

- `kb/platforms/linkedin.json`: every value comes from the maintainer's source record. Its `sources[]` entries (`title`, `reference`, `retrieved_on`) let a reader tell platform observations (the per-query result cap, the grammar, filter ids and values, the fixed URL parameters, the export column names, the profile-key host and path) from operator policy values (`max_query_length`, `pacing`, `search.url.max_length`, the rejection of typographic quotes, `partition_order`). Platform values appear only as data in this file, never in this page.
- `kb/platforms/demo.json`: synthetic, built to carry the reference fixtures on the reserved host `demo.example`; its single source says so.

A profile holds `platform`, `profile_version`, `verified_on`, `sources`, `search` (`per_query_result_cap`, `max_query_length`, `grammar`, `url`), `filters` (per dimension a `filter_id` and a value list or `null`), `partition_order`, `pacing`, `export_columns` and `profile_key`. Only `company_size` and `network` need a profile filter, because their values are platform-defined; `seniority`, `industries` and `languages` validate by their closed set, the term rule and the language pattern, and serve scoring only when the profile has no filter for them; `geos` validate by `^[A-Z]{2}$`, or by the profile's `geo` value list when it gives one.

## Manifest Details

**Parameters Schema:**
* `action` (string, required): `compile_search` or `ingest_leads`.
* `platform` (string): platform id, `^[a-z][a-z0-9_]{0,31}$`; selects a bundled profile (`demo`, `linkedin`) unless `profile_override` is given.
* `target_spec` (object): `schema_version` 1, `titles` (required), and the optional lists `keywords_include`, `keywords_exclude`, `industries`, `seniority`, `geos`, `languages`, `company_size`, `network`, plus the optional integer `estimated_matches`.
* `profile_override` (object, optional): a full platform profile passed as operator configuration.
* `plan` (object): the plan `compile_search` returned, unmodified.
* `rows` (array of objects): exported rows; the profile's `export_columns` name the columns read.
* `existing_leads` (array of objects, optional): the complete known set of leads (`profile_key`, `lead_id`).

Only `action` is required at the manifest level so that every contract code can be reached through `validate_params()` hosts; the per-action requirements are enforced in code.

**Outputs Schema:**
* `status` (string): `completed`, or `error` for a contract violation.
* `plan` (object, `compile_search`): `plan_id`, `platform`, `profile_version`, `profile_origin`, `queries[]` (`id`, `partition`, `keywords`, `filters`, `expected_cap`, `search_url`), `pacing` (`per_run_max_queries`, `per_day_max_rows`, `source`), `rubric`, `criteria`, `partitioning` (`dimensions`, `estimated_matches`, `cap_risk`).
* `leads` (array, `ingest_leads`): `lead_id`, `profile_key`, `score`, `reasons`, `off_target`, `stage`, `source_query`.
* `review_table` (object, `ingest_leads`): the eight `columns` and the `csv` text.
* `stats` (object, `ingest_leads`): `rows_in`, `deduped`, `off_target`, `kept`.

A person's full name and profile URL appear in no output field; the profile URL survives only as the slug inside `profile_key`. Contract violations return `{"status": "error", "error": {"code", "detail"}}` instead; `detail` is an object with at least `reason` and never echoes row text.

**Error registry (closed):** `INVALID_REQUEST`, `LIMIT_EXCEEDED`, `UNKNOWN_PLATFORM`, `INVALID_PROFILE`, `INVALID_TARGET_SPEC`, `INVALID_PLAN`, `PLAN_MISMATCH`, `INVALID_EXISTING_LEADS`, `INVALID_ROWS`, `UNKNOWN_SOURCE_QUERY`. **Fault registry (one code):** `INTERNAL_ERROR`, returned only when an unexpected exception escapes the checks.

**Rubric:** `title_match` 40, `seniority` 20, `industry` 20, `geo` 10, `language` 10, `exclusion_hit` -100, `off_target_below` 50. A component is inherited when its dimension is a filter of the row's source query, otherwise read from the row where a row rule exists (`title_match`, `seniority`, `industry`); `network` carries no weight.

**Size caps:** every cap is a module constant with one test and one row in the table of `instructions.md`: the per-list caps of the specification, 100 characters and 10 tokens per term, the estimate, 200 generated queries, the profile's `max_query_length` and `search.url.max_length`, 5,000 rows with 64 keys and 2,000 characters per cell and 2,000,000 characters across all cells, 50,000 existing leads, the first 200 characters of a scored text cell, and the profile's own caps.

## Environment

No environment variables. Fully offline; all inputs are passed to `execute()`. Reads only files inside its bundle.

## Example Usage (Direct)

The bundle ships the reference fixtures (`fixtures/example_compile_params.json`, `fixtures/example_ingest_params.json`) on the synthetic `demo` profile:

```python
import json
import os

from skill_assay.core.loader import SkillLoader

bundle = SkillLoader.load_skill("office/outreach_manager")
skill = bundle["class"]()
root = os.path.join("skills", "office", "outreach_manager", "fixtures")

with open(os.path.join(root, "example_compile_params.json")) as f:
    compile_params = json.load(f)
compiled = skill.execute(compile_params)
plan = compiled["plan"]
print(compiled["status"], plan["plan_id"], plan["profile_origin"])
for query in plan["queries"]:
    print(query["id"], query["partition"], query["search_url"])

with open(os.path.join(root, "example_ingest_params.json")) as f:
    ingest_params = json.load(f)
ingest_params["plan"] = plan
ingested = skill.execute(ingest_params)
print(ingested["stats"])
print(ingested["review_table"]["csv"])
```

## Usage Examples

Guides: [Usage index](../usage/README.md) · [Agent loops](../usage/agent_loops.md). No skill-specific API keys.

Use `bundle["class"]()` in the snippets below; explicit `bundle["module"].OutreachManagerSkill()` also works.

Sample user message: *Plan a search for heads of operations in warehouse automation across DE and NL on the demo platform.*

The provider snippets share this compact setup:

```python
TARGET_SPEC = {
    "schema_version": 1,
    "titles": ["Head of Operations", "Operations Director"],
    "geos": ["DE", "NL"],
    "company_size": ["51-200", "201-500"],
    "keywords_include": ["warehouse automation"],
    "keywords_exclude": ["recruiter"],
}
USER_MESSAGE = (
    "Compile a search plan with the outreach manager tool on platform demo. "
    f"target_spec={TARGET_SPEC}"
)
```

### Runnable examples

- Local execute: [`examples/outreach_manager_demo.py`](../../examples/outreach_manager_demo.py) — runs the in-bundle fixtures fully offline: the plan compiled on the demo profile, the two reference rows ingested against it, and two fail-closed contract errors (unknown platform, edited plan)

### Gemini

```python
import google.genai as genai
from google.genai import types
from skill_assay.core.env import load_env_file
from skill_assay.core.loader import SkillLoader

load_env_file()
bundle = SkillLoader.load_skill("office/outreach_manager")
skill = bundle["class"]()
client = genai.Client()
gemini_tool = SkillLoader.to_gemini_tool(bundle)
response = client.models.generate_content(
    model="gemini-3.5-flash-lite",
    contents=USER_MESSAGE,
    config=types.GenerateContentConfig(
        tools=[gemini_tool],
        system_instruction=bundle["instructions"],
    ),
)
for part in response.candidates[0].content.parts:
    if part.function_call:
        result = skill.execute(dict(part.function_call.args))
        print(result["status"], result.get("plan", result.get("error")))
```

### Claude

```python
import os

import anthropic
from skill_assay.core.env import load_env_file
from skill_assay.core.loader import SkillLoader

load_env_file()
bundle = SkillLoader.load_skill("office/outreach_manager")
skill = bundle["class"]()
client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
tools = [SkillLoader.to_claude_tool(bundle)]
response = client.messages.create(
    model="claude-haiku-4-5-20251001",
    max_tokens=1024,
    system=bundle["instructions"],
    tools=tools,
    messages=[{"role": "user", "content": USER_MESSAGE}],
)
for block in response.content:
    if block.type == "tool_use":
        result = skill.execute(dict(block.input))
        print(result["status"], result.get("plan", result.get("error")))
```

### OpenAI

```python
import json
import os

from openai import OpenAI
from skill_assay.core.env import load_env_file
from skill_assay.core.loader import SkillLoader

load_env_file()
bundle = SkillLoader.load_skill("office/outreach_manager")
skill = bundle["class"]()
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
tool = SkillLoader.to_openai_tool(bundle)
response = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[
        {"role": "system", "content": bundle["instructions"]},
        {"role": "user", "content": USER_MESSAGE},
    ],
    tools=[tool],
)
message = response.choices[0].message
if message.tool_calls:
    args = json.loads(message.tool_calls[0].function.arguments)
    result = skill.execute(args)
    print(result["status"], result.get("plan", result.get("error")))
```

### DeepSeek

```python
import json
import os

from openai import OpenAI
from skill_assay.core.env import load_env_file
from skill_assay.core.loader import SkillLoader

load_env_file()
bundle = SkillLoader.load_skill("office/outreach_manager")
skill = bundle["class"]()
client = OpenAI(
    api_key=os.environ.get("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com",
)
tool = SkillLoader.to_deepseek_tool(bundle)
response = client.chat.completions.create(
    model="deepseek-chat",
    messages=[
        {"role": "system", "content": bundle["instructions"]},
        {"role": "user", "content": USER_MESSAGE},
    ],
    tools=[tool],
)
message = response.choices[0].message
if message.tool_calls:
    args = json.loads(message.tool_calls[0].function.arguments)
    result = skill.execute(args)
    print(result["status"], result.get("plan", result.get("error")))
```

### Ollama (prompt mode)

```python
import json

from skill_assay.core.loader import SkillLoader

bundle = SkillLoader.load_skill("office/outreach_manager")
skill = bundle["class"]()
prompt = (
    "You may call tools as JSON blocks.\n"
    f"Tool: {bundle['manifest']['name']}\n"
    f"Instructions:\n{bundle['instructions']}\n"
    f"User: {USER_MESSAGE}"
)
print(prompt)
# When the model emits JSON tool args, pass them to execute:
result = skill.execute(
    {"action": "compile_search", "platform": "demo", "target_spec": TARGET_SPEC}
)
print(json.dumps(result, indent=2))
```

## Limitations (v0.1)

- **No actuation and no sending**: the plan is executed by the operator's actuator, and messaging stays with the operator; this version has no sequence, draft or send step.
- **No result counts**: the skill cannot see how many profiles a query returns; `estimated_matches` is what the operator reads off the platform, and `cap_risk` is the skill's arithmetic on it.
- **Exact source-query matching**: a row's source query must equal a query `id` or the exact `search_url`; an actuator that alters the URL makes the row fail closed, and the host maps the URL to the `id` before the call.
- **Inherited components trust the row's source-query attribution**, which the host supplies.
- **`lead_id` is unique only relative to the `existing_leads` passed**, which must be the complete known set; `profile_key` is the identity of a lead.
- **Two profiles ship**; other platforms need an override profile or a data-only revision.

---

<!-- skill-history:begin -->
## Skill history

This bundle was written in this repository. Its provenance is anchored in
this repository (`anchor: repository` in `manifest.yaml`, with the content
hash refreshed whenever a bundle file changes). It is published with the
package release v0.1.1.
<!-- skill-history:end -->
