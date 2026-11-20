# Underwriting Condition Reply Tracker

**Domain:** `office`
**Skill ID:** `office/underwriting_condition_reply_tracker`
**Issuer:** [@mrmasa88](https://github.com/mrmasa88) ([AO](https://github.com/0x-AO-Protocol)) · **Project contact:** m@orblabs.ch
<!-- skill-doc-meta:begin -->
**Version**: `0.1.0`
<!-- skill-doc-meta:end -->
**Maturity:** Provisional — available for evaluation; interfaces or behavior may change in a compatible or documented breaking release.
**Recommended install:** `pip install "agent-skill-assay[office_underwriting_condition_reply_tracker]"`. See [Install extras](../usage/install_extras.md).

[Skill Library](README.md) · [Testing](../TESTING.md)

Keeps the written exchange with underwriting on schedule and grounded in the verified file. For one loan file, `track` counts the whole days to closing and the days each condition has been open, and flags every open condition inside the lead window. `scaffold` adds one draft slot per open condition. `compile` takes one draft per open condition: your agent chooses the cited documents and may write the issue summary, the proposed resolution and the reply body; the skill checks the cited ids and the citation markers in the texts you supply and applies the figure rule below to those texts, composes any text you leave out from fixed templates, and returns every draft held for operator review.

The skill **sends nothing, stores nothing and decides nothing about the loan**. `execute()` makes no model call: the models in the examples below belong to your agent, which calls the skill as a tool. Under the standard profile, identical input returns identical output on one Python version (see the decimal-digit test below).

## When not to use

- **Not a decision on a condition.** Whether a document satisfies a condition stays with the operator and the underwriter.
- **Not a sender.** Replies are returned, never sent, and every draft is held for review.
- **Not a fact checker.** For each figure of the forms listed below, the figure rule checks that it has a source in the request; it does not verify that a figure is correct or that a reply is accurate.

## Design split

- **Your agent chooses and may write:** it picks the verified documents that support each open condition and may write the three draft texts.
- **The skill applies fixed rules:** day counts, lead-window flags, the citation rule, the figure rule, the templates and the cut at each cap.
- **The operator reviews and sends:** every draft comes back with `review_state: awaiting_operator_review`; the operator also vouches for the verified file, which the skill takes as given.

## Citation markers

A document is cited as `[doc:<document_id>]`. In the texts you supply, every `[doc:` must start a well-formed marker, every marker must name a document of the verified file that the draft lists in `cited_document_ids`, and a supplied `reply_body` must carry a marker for every cited document. The marker checks read the texts you supply; a composed text repeats the condition's description and the cited titles as they are.

## What the figure rule verifies

Each text you supply is a factual slot. A decimal digit other than `0`-`9` fails, and citation markers are set aside. The rest is read in words: runs of ASCII letters, digits and `_` joined by `-`, `.` and `,`. A run of `-`, `.` and `,` that no ASCII letter, digit or `_` follows ends the word, so `8,000.` reads as `8,000`. Before the words are read, a `.` that directly precedes a digit and directly follows no ASCII letter, digit or `_` is read as `0.`, so `.5` is the number 0.5. Then:

- **Ids.** A word that is a `document_id` or `condition_id` of the request is supported.
- **Dates.** A word `YYYY-MM-DD` must equal `requested_on`, `as_of_date`, `closing_deadline` or a date in an admitted text.
- **Numbers.** With commas, a first group of one to three digits, then groups of a comma and three digits; without commas, any digit run; either with at most one decimal part. A number must equal a computed value (days to closing, days open, lead window, prior reply count) or a number in an admitted text. `1,500`, `1500` and `1500.00` are equal; a sign is not part of a number.
- **Pieces.** Any other word is read in pieces split at `-`, so `1,500-2,000` holds the numbers 1,500 and 2,000. A comma or point next to such a `-` stays in its piece: `5.-6` holds `5.` and 6.
- **Runs.** A piece made only of digits, commas and points is read whole. When it is not a number of the forms above (`5,0000`, `6.0.0`, `5,,000`), it is supported only when an admitted text holds the same run whole, not inside a longer one: its parts never support it, and an admitted `15.2.0` does not support `5.2.0`.
- **Joined figures.** A piece with an ASCII letter or `_` is split into tokens at each run of `.` and `,`; two neighbouring tokens are joined when a digit ends the first and a digit begins the second. Joined tokens of digits only are one run, read as above: `approx.5,0000` holds the run `5,0000`, and `No.1,500` holds the number 1,500. Joined tokens that include a token with an ASCII letter or `_` are one figure, so a run joined to such a token is read with it (`v6.0.0`, `1.5M`, `Q4.5`, `FY2027,000`); such a figure is supported only when it appears whole in an admitted text. Tokens that are not joined are read apart: `Q1,Q2` holds `Q1` and `Q2`, and `approx.5` holds 5.
- **Tokens.** A lone token that joins a digit with an ASCII letter or `_` (`Q4`, `5M`, `3x`, `FY2027`, `8e3`) must equal an id or a token of an admitted text, also one inside a longer figure: an admitted `v6.0.0` supports `v6`, but neither `0` nor `6.0.0`.
- **Number words.** The English number words `two` to `twenty`, the tens, `hundred`, `thousand`, `million`, `billion`, `trillion`, `double`, `twice`, `triple`, `doubled`, `tripled` and `halved` (ASCII case folded) must be tokens of an admitted text.

- **Admitted texts** are the condition's `description` and the `title` and `summary` of each document the draft cites; their words are read by the same rules. A figure found only in a document the draft does not cite fails.
- **Attribution is not checked:** the rule does not check which cited document a figure came from.
- **Not read as figures:** superscript and circled digits, fractions, Roman numerals and CJK numerals.
- **The decimal-digit test** uses Python's `str.isdecimal()`, so its verdict follows the Unicode database of the Python version; no identity across Python versions is claimed for it. The same test rejects titles and descriptions that hold a decimal digit other than `0`-`9`.
- Every text the skill composes from its templates passes this rule for its draft.

## Templates

When a text is left out, the skill composes it:

- `issue_summary`: `Condition {condition_id}, raised {requested_on}: {description}`
- `proposed_resolution`: `Review the cited verified documents ({ids}) and clear condition {condition_id} if they satisfy it.`
- `reply_body`: an opening line naming the condition and its date, one line `- {title} [doc:{document_id}]` per cited document in inventory order, and a closing request for review.

A composed text longer than its cap (600, 600 and 4000 characters) is cut to the longest prefix of at most cap − 3 characters that ends before a space, or hard after the first cap − 3 characters when there is no such prefix, and ends with `...`. A long composed `reply_body` can lose its last document lines; `cited_document_ids` still lists every cited document.

The templates copy ids, so the whole value of each `document_id` and `condition_id` must match both `[A-Za-z0-9_-]{1,64}` and `[A-Za-z0-9_]+(?:-[A-Za-z0-9_]+)*`: ASCII letters, digits and `_`, joined by single hyphens, 1 to 64 characters. Each id is then read as one word in a composed text.

## Agent-loop contract

- `escalations` lists the open conditions inside the lead window: surface them first.
- `UNSUPPORTED_FIGURE` and the citation codes mean the draft text needs rewording or other cited documents; the detail names the field, never the text.
- An error envelope means the request did not meet the contract: correct the input rather than retry it unchanged.

## Validation order (fail-closed, deterministic)

The first failing check is returned with a `detail` of `reason` and `path` (and `limit` and `actual` for a size cap). In order: the request object, `action`, unknown keys, required fields and types, an empty verified file, list caps, field shapes, real dates, repeated ids, the dates against the run date, `drafts` only with `compile`, an open condition, each draft's condition, a draft per open condition, the citations, and the figure rule. The bundle's `instructions.md` lists every step.

## Bundle layout

The skill lives in `skills/office/underwriting_condition_reply_tracker/`. [Skill anatomy](../introduction.md#skill-anatomy). **Contract** — see Manifest Details below. **Assurance** — `test_skill.py` in the bundle.

### Effect (`skill.py`)

Pure Python, standard library only: the check list, the day counts, the citation and figure rules, the templates and the cut rule. Every number is an integer. The closed error registry and the one-code fault registry (`INTERNAL_ERROR`) are module constants; the skill reads no file, not even its own manifest.

### Directive (`instructions.md`)

Registry id, the three actions, the request fields, how to write a draft that passes the checks, the outputs and the check list in order.

### Reference (`schemas/`)

JSON Schemas for the request and the output (documentation; the runtime uses explicit standard-library checks).

### Fixtures (`fixtures/`)

Four reference pairs on synthetic data: a `track` with nothing at risk, a `scaffold` inside the lead window, a `compile` composed from the templates and a `compile` with checked caller text. `card_ui_samples.json` holds the `track` output.

## Manifest Details

**Parameters Schema:**
* `action` (string, required): `track`, `scaffold` or `compile`.
* `as_of_date`, `closing_deadline` (string): real dates `YYYY-MM-DD`; the deadline is not before the run date.
* `escalation_lead_days` (integer, optional): 0 to 365, default 7.
* `verified_file` (array): 1 to 200 documents with `document_id`, `title`, `document_type` and an optional `summary`.
* `condition_requests` (array): 1 to 100 conditions with `condition_id`, `requested_on`, `description`, `status` and an optional `prior_reply_count`.
* `drafts` (array, `compile` only): one per open condition with `condition_id`, `cited_document_ids` and optional `issue_summary`, `proposed_resolution` and `reply_body`.

The parameter schema is flat: each property carries one `type` and a description, and only `action` is required at the manifest level, so that every contract code can be reached through `validate_params()` hosts; the per-action requirements and the element shapes are enforced in code.

**Outputs Schema:**
* `status`, `days_until_closing`, `lead_window_days`.
* `tracker` (array): per condition `condition_id`, `status`, `days_until_closing`, `days_open`, `prior_reply_count`, `at_risk`.
* `escalations` (array): per at-risk condition `condition_id`, `days_until_closing`, `lead_window_days`.
* `operator_review_pending` (always `true`) and `run_summary`.
* `slots` (`scaffold`): `condition_id`, `description`, `allowed_document_ids`, `citation_marker`, `caps`, `review_state`.
* `drafts` (`compile`): `condition_id`, `issue_summary`, `cited_document_ids`, `proposed_resolution`, `reply_body`, `composed_by`, `review_state`.

Contract violations return `{"status": "error", "error": {"code", "detail"}}` instead.

**Error registry (closed):** `INVALID_REQUEST`, `INVALID_DATE`, `EMPTY_VERIFIED_FILE`, `DEADLINE_BEFORE_AS_OF`, `REQUEST_AFTER_AS_OF`, `DUPLICATE_ID`, `NO_OPEN_CONDITION`, `UNKNOWN_CONDITION`, `DRAFT_FOR_CLOSED_CONDITION`, `DRAFT_MISSING`, `UNKNOWN_DOCUMENT`, `MALFORMED_CITATION`, `CITATION_MISMATCH`, `UNSUPPORTED_FIGURE`. **Fault registry (one code):** `INTERNAL_ERROR`, returned only when an unexpected exception escapes the checks, with the exception class name only.

## Environment

No environment variables. Fully offline; all inputs are passed to `execute()`. Reads no file, not even inside its bundle.

## Usage Examples

Guides: [Usage index](../usage/README.md) · [Agent loops](../usage/agent_loops.md). No skill-specific API keys.

Use `bundle["class"]()` in the snippets below; explicit `bundle["module"].UnderwritingConditionReplyTrackerSkill()` also works. In each snippet the model belongs to your agent; the skill's `execute()` makes no model call.

Sample user message: *Which underwriting conditions are at risk before closing, and what would the replies say?*

The provider snippets share this compact setup:

```python
import json
import os

with open(
    os.path.join(
        "skills", "office", "underwriting_condition_reply_tracker", "fixtures", "compile_template_params.json"
    )
) as f:
    COMPILE_PARAMS = json.load(f)
USER_MESSAGE = (
    "Check the open underwriting conditions with the reply tracker tool and show the drafts. "
    f"Request: {json.dumps(COMPILE_PARAMS)}"
)
```

### Gemini

```python
import google.genai as genai
from google.genai import types
from skill_assay.core.env import load_env_file
from skill_assay.core.loader import SkillLoader

load_env_file()
bundle = SkillLoader.load_skill("office/underwriting_condition_reply_tracker")
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
        print(result["status"], result.get("escalations", result.get("error")))
```

### Claude

```python
import os

import anthropic
from skill_assay.core.env import load_env_file
from skill_assay.core.loader import SkillLoader

load_env_file()
bundle = SkillLoader.load_skill("office/underwriting_condition_reply_tracker")
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
        print(result["status"], result.get("escalations", result.get("error")))
```

### OpenAI

```python
import json
import os

from openai import OpenAI
from skill_assay.core.env import load_env_file
from skill_assay.core.loader import SkillLoader

load_env_file()
bundle = SkillLoader.load_skill("office/underwriting_condition_reply_tracker")
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
    print(result["status"], result.get("escalations", result.get("error")))
```

### DeepSeek

```python
import json
import os

from openai import OpenAI
from skill_assay.core.env import load_env_file
from skill_assay.core.loader import SkillLoader

load_env_file()
bundle = SkillLoader.load_skill("office/underwriting_condition_reply_tracker")
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
    print(result["status"], result.get("escalations", result.get("error")))
```

### Ollama (prompt mode)

```python
import json

from skill_assay.core.loader import SkillLoader

bundle = SkillLoader.load_skill("office/underwriting_condition_reply_tracker")
skill = bundle["class"]()
prompt = (
    "You may call tools as JSON blocks.\n"
    f"Tool: {bundle['manifest']['name']}\n"
    f"Instructions:\n{bundle['instructions']}\n"
    f"User: {USER_MESSAGE}"
)
print(prompt)
# When the model emits JSON tool args, pass them to execute:
result = skill.execute(COMPILE_PARAMS)
print(json.dumps(result, indent=2))
```

## Limitations (v0.1)

- **One loan file per call;** condition states are updated by the caller between runs.
- **The verified file is taken as given;** the operator vouches that every entry was checked.
- **The figure rule reads the forms listed above only,** and checks a source, not correctness or attribution.
- **A long composed reply body is cut at 4000 characters** and can lose its last document lines.
- **Copied text is not re-checked:** a description or title that holds `[doc:` reaches a composed text unchanged.

---

<!-- skill-history:begin -->
## Skill history

This bundle was written in this repository. Its provenance is anchored in
this repository (`anchor: repository` in `manifest.yaml`, with the content
hash refreshed whenever a bundle file changes). It is published with the
package release whose changelog lists it.
<!-- skill-history:end -->
