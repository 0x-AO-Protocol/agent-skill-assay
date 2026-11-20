# Scoped Risk Literature Scan

**Domain:** `compliance`
**Skill ID:** `compliance/scoped_risk_literature_scan`
**Issuer:** [@mrmasa88](https://github.com/mrmasa88) ([AO](https://github.com/0x-AO-Protocol)) · **Project contact:** m@orblabs.ch
<!-- skill-doc-meta:begin -->
**Version**: `0.1.0`
<!-- skill-doc-meta:end -->
**Maturity:** Provisional — available for evaluation; interfaces or behavior may change in a compatible or documented breaking release.
**Recommended install:** `pip install "agent-skill-assay[compliance_scoped_risk_literature_scan]"`. See [Install extras](../usage/install_extras.md).

[Skill Library](README.md) · [Testing](../TESTING.md)

A recurring, scoped **literature and regulation scan** for a risk advisory practice, in two steps. Your agent reads the configured sources with its own tools and reports, per source, whether it was reachable and which items it found. `screen` keeps the items inside the cycle window, the declared jurisdictions and the declared risk domains, drops repeats, orders and caps the rest, flags the items relevant to clients with a follow-up date, counts the in-scope items, and returns one annotation slot per kept item. Your agent writes a summary and an impact note for each kept item; `compile` checks that exactly the kept items are annotated within the caps, credits competence minutes only on attestation, and returns the review record, held as pending review, for your agent to store.

The skill **fetches nothing, opens no link and writes nothing**. `execute()` makes no model call: the models in the examples below belong to your agent, which calls the skill as a tool. Under the standard profile, identical input returns identical output.

## When not to use

- **Not a search engine or a feed reader.** It reads only what your agent reports; collection, and any judgement of what a publication means, stay with your agent and the practitioner.
- **Not a reviewer of summaries.** The check covers presence, keys, enums and caps. It does not read the publications, so it cannot verify that a summary or an impact note is accurate.
- **Not for changing methods, models or assumptions**, for advice on a specific engagement, or when the window or the scope is undefined.

## Design split

- **Your agent collects and writes:** it reads the configured sources, tags each item with its domains, jurisdiction and exposure tags, writes every `summary` and `impact.note`, and stores the returned record wherever the practice keeps its review log.
- **The skill applies fixed rules:** screening, de-duplication, ordering, the cap, relevance flags, follow-up dates, tallies, the annotation check and the minutes rule, all in code from the request alone.
- **The practitioner decides:** every record comes back with `review_status: pending_review`, and competence minutes count only when the request attests them.

## How the screen works

1. **Exclusion, first reason wins:** `out_of_window`, then `out_of_jurisdiction`, then `out_of_domain` (none of the item's domains is in scope).
2. **Order:** relevant to clients first (an item's exposure tag is one of `client_exposure_tags`), then newest `published_date` first, then the source's position in `sources`, then the normalized title by code point, then the position in `collection`.
3. **Duplicates:** walking that order, an item whose normalized title, date and jurisdiction equal an earlier item's is excluded as `duplicate`. The normalized title folds the ASCII letters `A`-`Z` to `a`-`z` and collapses each run of U+0020 spaces to one; no other character changes, so titles that differ only in `É` / `é`, or in U+00A0 or U+3000 against a space, are not duplicates.
4. **Cap:** items after the first `max_items` are excluded as `over_cap`; kept items get `item_ref` `R001`, `R002`, ... in order.
5. **Follow-up:** a kept item relevant to clients gets `follow_up_due_by` = `cycle_end` plus the window length.
6. **Tallies:** over the in-scope items after duplicates and before the cap, per scope domain, jurisdiction and source kind, with `change_by_domain` against `previous_tallies` (a missing previous count is 0).

## The annotation check

- `summary` and `impact.note` are free text written by your agent. Their content is not checked; the skill checks that each kept item has exactly one annotation, that each annotation names a kept `item_ref`, that `area` and `likelihood` come from their lists, and that the texts stay within their caps (2000 and 1000 characters) and hold no control character.
- A missing annotation, an unknown `item_ref` or a repeated one fails the whole `compile`; nothing is credited and no record is returned.

## Agent-loop contract

- `SOURCE_UNREACHABLE` or `SOURCE_NOT_REPORTED` stops the cycle: fix the collection and run `screen` again.
- `ANNOTATION_MISSING`, `UNKNOWN_ITEM_REF` and `DUPLICATE_KEY` on annotations mean the draft does not match the kept items: annotate exactly the `item_ref` values that `screen` returned.
- An error envelope means the request did not meet the contract: correct the input rather than retry it unchanged.

## Validation order (fail-closed, deterministic)

The first failing check is returned with a `detail` of `reason` and `path` (and, where useful, a `limit` or a skill-assigned `item_ref`); the detail never repeats request text. In order: the request object, `action`, unknown top-level keys, required fields and top-level types, empty scope (`EMPTY_SCOPE`), list-length caps, every field's shape and text cap, real dates (`INVALID_DATE`), the window (`WINDOW_INVALID`, 1 to 92 days), repeated keys (`DUPLICATE_KEY`), `previous_tallies` domains, the collection reports (`UNKNOWN_SOURCE`, `SOURCE_NOT_REPORTED`, `SOURCE_UNREACHABLE`), compile-only fields, and for `compile` the annotations (`UNKNOWN_ITEM_REF`, `DUPLICATE_KEY`, `ANNOTATION_MISSING`). The bundle's `instructions.md` lists every step.

## Bundle layout

The skill lives in `skills/compliance/scoped_risk_literature_scan/`. [Skill anatomy](../introduction.md#skill-anatomy). **Contract** — see Manifest Details below. **Assurance** — `test_skill.py` in the bundle.

### Effect (`skill.py`)

Pure Python, standard library only: the check list, the screening and ordering rules, the duplicate rule, the cap, the tallies and the annotation check. Every number is an integer. The closed error registry and the one-code fault registry (`INTERNAL_ERROR`) are module constants; the skill reads no file, not even its own manifest.

### Directive (`instructions.md`)

Registry id, when to call and when not to, the request fields, the screening rules, how to write the annotations, and the check list in order.

### Reference (`schemas/`)

JSON Schemas for the request and the output (documentation; the runtime uses explicit standard-library checks).

### Fixtures (`fixtures/`)

Three reference pairs on synthetic data: a weekly `screen` with one item out of the window and one outside the jurisdictions, the matching `compile` with two annotations and attested minutes, and a monthly `screen` with a repeated title, the cap and the change from the previous cycle. `card_ui_samples.json` holds the weekly `screen` output.

## Manifest Details

**Parameters Schema:**
* `action` (string, required): `screen` or `compile`.
* `cycle_start`, `cycle_end` (string): real dates `YYYY-MM-DD`; the window holds 1 to 92 days, both ends included.
* `risk_domains` (array): 1 to 20 unique codes; the whole of each code must match `[a-z][a-z0-9_]{1,63}`.
* `jurisdictions` (array): 1 to 50 unique codes; the whole of each code must match `[A-Z][A-Z0-9-]{1,7}`.
* `sources` (array): 1 to 50, each with a unique `name` (the whole name must match `[A-Za-z0-9_-]{1,64}`), a `url` (at most 512 characters, never opened; the whole value must match `https://[A-Za-z0-9./_-]*`) and a `kind`.
* `max_items` (integer): 1 to 200.
* `client_exposure_tags` (array, optional): up to 50 unique tags.
* `previous_tallies` (object, optional): `by_domain`, scope domains to counts from 0 to 100000.
* `collection` (array): one report per configured source with `source_name`, `status` and `items` (at most 500 per report, 2000 in all).
* `log_destination` (string, optional): echoed in the `compile` record.
* `attestation` (object, `compile` only, optional): `attested` and `minutes` (0 to 1440).
* `annotations` (array, `compile` only, required there): up to 200, each with `item_ref`, `summary` and `impact`.

The parameter schema is flat: each property carries one `type` and a description, and only `action` is required at the manifest level, so that every contract code can be reached through `validate_params()` hosts; the per-action requirements and the element shapes are enforced in code.

**Outputs Schema:**
* `status`, `cycle_start`, `cycle_end`, `window_days`, `scope`.
* `items` (array): kept items with `item_ref`, `title`, `source_name`, `kind`, `published_date`, `jurisdiction`, `matched_domains`, `url`, `client_relevant`, `follow_up_required`, `follow_up_due_by`; `compile` adds `summary` and `impact`.
* `excluded_count`, `excluded_by_reason` (`out_of_window`, `out_of_jurisdiction`, `out_of_domain`, `duplicate`, `over_cap`), `flagged_count`.
* `tallies`: `in_scope_count`, `by_domain`, `by_jurisdiction`, `by_kind`, `change_by_domain`.
* `screen` only: `annotation_slots`, `impact_areas`, `likelihoods`, `review_status: not_annotated`.
* `compile` only: `competence_minutes_logged`, `review_status: pending_review`, `log_status: returned_to_caller`, `log_destination`.

Contract violations return `{"status": "error", "error": {"code", "detail"}}` instead.

**Error registry (closed):** `INVALID_REQUEST`, `INVALID_DATE`, `EMPTY_SCOPE`, `WINDOW_INVALID`, `DUPLICATE_KEY`, `UNKNOWN_SOURCE`, `SOURCE_NOT_REPORTED`, `SOURCE_UNREACHABLE`, `UNKNOWN_ITEM_REF`, `ANNOTATION_MISSING`. **Fault registry (one code):** `INTERNAL_ERROR`, returned only when an unexpected exception escapes the checks, with the exception class name only.

## Environment

No environment variables. Fully offline; all inputs are passed to `execute()`. Reads no file, not even inside its bundle.

## Usage Examples

Guides: [Usage index](../usage/README.md) · [Agent loops](../usage/agent_loops.md). No skill-specific API keys.

Use `bundle["class"]()` in the snippets below; explicit `bundle["module"].ScopedRiskLiteratureScanSkill()` also works. In each snippet the model belongs to your agent; the skill's `execute()` makes no model call.

Sample user message: *Screen this week's collection against our scope and list what needs a summary.*

The provider snippets share this compact setup:

```python
import json
import os

with open(
    os.path.join(
        "skills", "compliance", "scoped_risk_literature_scan", "fixtures", "screen_week_params.json"
    )
) as f:
    SCREEN_PARAMS = json.load(f)
USER_MESSAGE = (
    "Screen this week's collection with the literature scan tool and list the kept items. "
    f"Request: {json.dumps(SCREEN_PARAMS)}"
)
```

### Gemini

```python
import google.genai as genai
from google.genai import types
from skill_assay.core.env import load_env_file
from skill_assay.core.loader import SkillLoader

load_env_file()
bundle = SkillLoader.load_skill("compliance/scoped_risk_literature_scan")
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
        print(result["status"], result.get("annotation_slots", result.get("error")))
```

### Claude

```python
import os

import anthropic
from skill_assay.core.env import load_env_file
from skill_assay.core.loader import SkillLoader

load_env_file()
bundle = SkillLoader.load_skill("compliance/scoped_risk_literature_scan")
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
        print(result["status"], result.get("annotation_slots", result.get("error")))
```

### OpenAI

```python
import json
import os

from openai import OpenAI
from skill_assay.core.env import load_env_file
from skill_assay.core.loader import SkillLoader

load_env_file()
bundle = SkillLoader.load_skill("compliance/scoped_risk_literature_scan")
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
    print(result["status"], result.get("annotation_slots", result.get("error")))
```

### DeepSeek

```python
import json
import os

from openai import OpenAI
from skill_assay.core.env import load_env_file
from skill_assay.core.loader import SkillLoader

load_env_file()
bundle = SkillLoader.load_skill("compliance/scoped_risk_literature_scan")
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
    print(result["status"], result.get("annotation_slots", result.get("error")))
```

### Ollama (prompt mode)

```python
import json

from skill_assay.core.loader import SkillLoader

bundle = SkillLoader.load_skill("compliance/scoped_risk_literature_scan")
skill = bundle["class"]()
prompt = (
    "You may call tools as JSON blocks.\n"
    f"Tool: {bundle['manifest']['name']}\n"
    f"Instructions:\n{bundle['instructions']}\n"
    f"User: {USER_MESSAGE}"
)
print(prompt)
# When the model emits JSON tool args, pass them to execute:
result = skill.execute(SCREEN_PARAMS)
print(json.dumps(result, indent=2))
```

## Limitations (v0.1)

- **No collection:** the skill sees only the reported items; a source your agent did not read cannot be screened, and an unreachable source stops the cycle.
- **Content is not checked:** summaries and impact notes are checked for presence, keys, enums and caps only.
- **Fixed rules:** the duplicate rule folds ASCII letters and collapses U+0020 runs only; relevance is an exact tag match; the follow-up date is the end of the next window of the same length.
- **One cycle per call:** trends across cycles come from `previous_tallies`, which your agent keeps.
- **Dates end at 9999-12-31:** when a client-relevant item's follow-up date would fall after that day, the skill returns `INTERNAL_ERROR` in this version.

---

<!-- skill-history:begin -->
## Skill history

This bundle was written in this repository. Its provenance is anchored in
this repository (`anchor: repository` in `manifest.yaml`, with the content
hash refreshed whenever a bundle file changes). It is published with the
package release whose changelog lists it.
<!-- skill-history:end -->
