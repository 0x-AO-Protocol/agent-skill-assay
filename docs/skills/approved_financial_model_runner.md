# Approved Financial Model Runner

**Domain:** `finance`
**Skill ID:** `finance/approved_financial_model_runner`
**Issuer:** [@mrmasa88](https://github.com/mrmasa88) ([AO](https://github.com/0x-AO-Protocol)) · **Project contact:** m@orblabs.ch
<!-- skill-doc-meta:begin -->
**Version**: `0.1.0`
<!-- skill-doc-meta:end -->
**Maturity:** Provisional — available for evaluation; interfaces or behavior may change in a compatible or documented breaking release.
**Recommended install:** `pip install "agent-skill-assay[finance_approved_financial_model_runner]"`. See [Install extras](../usage/install_extras.md).

[Skill Library](README.md) · [Testing](../TESTING.md)

Runs one model version from a registry document supplied with the call on prepared tables, and returns the requested figures as rounded decimal strings with an archive record for the caller to store. The skill checks the request and the whole registry document, looks up the model, the version and the parameter revision, requires every table to be marked `passed` upstream and to match its declared SHA-256, parses the tables, and evaluates every quantity of the version in declaration order. Each quantity is a formula in a closed arithmetic grammar over frozen parameter values and table columns: a model is data, never code. The `verify` action rebuilds a run and compares its archive document with a stored copy, character for character.

`execute()` makes no model call and uses no network, clock, file or environment; identical requests return identical output. Every figure is computed in one private decimal context and returned as a decimal string, so no binary floating-point value is read or returned.

## When not to use

- **Not for data preparation or model design.** Cleaning data, writing or calibrating a model and choosing between models stay outside the skill.
- **Not for interpretation.** The result holds figures only; narrative, recommendations and client text belong to later steps.
- **Not for one-off parameter tuning.** Parameter values come only from a revision recorded in the registry document.
- **Not a storage layer.** The caller reads the registry and the tables from its own storage and stores the archive document; the skill reads and writes nothing.

## Design split

- **The caller supplies the data**: the operator's registry document and the exact text of each prepared table, with its declared SHA-256 and its upstream validation status.
- **The skill computes**: the checks, the lookups, the table parsing, the evaluation, the rounding, and a canonical archive document with its digests.
- **The caller keeps custody**: it passes the operator's registry (the archive record carries the SHA-256 of the selected version entry, so a run can be checked against it later), stores the archive document before relying on the figures, and may call `verify` with it.

## The formula grammar

```
expr  := term (("+" | "-") term)*
term  := unary (("*" | "/") unary)*
unary := "-" unary | power
power := atom ("^" exponent)?          exponent: [+|-] NUMBER, no point, at most 64
atom  := NUMBER | "param." NAME | "q." NAME
       | AGG "(" ALIAS "." NAME ")"     AGG: sum mean min max first last count
       | "sumproduct" "(" ALIAS "." NAME "," ALIAS "." NAME ")"
       | "abs" "(" expr ")"
       | ("least" | "greatest") "(" expr ("," expr)* ")"   2 to 10 arguments
       | "round" "(" expr "," DIGITS ")" | "(" expr ")"
```

A `NUMBER` is unsigned and matches `(?:0|[1-9][0-9]{0,17})(?:\.[0-9]{1,12})?` with ASCII rules; the whole token must match, and a sign comes from unary minus. An exponent is an integer from -64 to 64 and `DIGITS` one from 0 to 12, each written as a `NUMBER` with no point. `x ^ 0` is 1 for every `x`, 0 included. Only spaces may stand between tokens: a formula that holds a control character, such as a tab or a line feed, fails with `INVALID_REQUEST` where the registry check reads it, before any formula is parsed. `q.NAME` reads an earlier quantity at its rounded value. `abs` takes one argument, and `least` and `greatest` take 2 to 10. `sumproduct` takes two columns of one input. Caps: 2,000 characters, 400 tokens and 32 levels of parentheses. The full rules, including the table format, are in the bundle's `instructions.md`.

## Rounding

Every quantity is rounded half-up to its declared decimals (ties away from zero: -0.125 at two decimals is -0.13) and shown in plain notation; a negative zero is shown as zero. Every value the evaluation produces must stay below 10^24 in magnitude.

## Check order (fail-closed)

The first failing check returns its code, and nothing after it is computed:

1. `INVALID_REQUEST` — the request shape, field by field. A `file_ref` or an `archive_document` that holds a control character fails here, never as a mismatch.
2. `REGISTRY_INVALID` — the structure of the whole registry document, every version included.
3. `MODEL_DEFINITION_INVALID` — every formula in the document parses, stays within the caps and names only declared parameters, columns and earlier quantities.
4. `MODEL_NOT_REGISTERED`, 5. `VERSION_NOT_REGISTERED`, 6. `REVISION_NOT_REGISTERED`, 7. `QUANTITY_NOT_DEFINED` — the lookups.
8. `INPUT_BINDING_MISMATCH` — the request's input aliases equal the version's declared inputs.
9. `INPUT_NOT_VALIDATED` — every table is marked `passed`.
10. `DIGEST_MISMATCH` — every table has its declared SHA-256.
11. `INPUT_PARSE_ERROR` — every table follows the table format.
12. `MODEL_EVALUATION_ERROR` — division by zero, a value reaching 10^24, or an invalid decimal operation, in any quantity of the version.
13. `ARCHIVE_MISMATCH` — `verify` only: the stored document differs from the rebuilt one.

## Bundle layout

The skill lives in `skills/finance/approved_financial_model_runner/`. [Skill anatomy](../introduction.md#skill-anatomy). **Contract** — see Manifest Details below. **Assurance** — `test_skill.py` in the bundle.

### Effect (`skill.py`)

Pure Python with the standard library modules `re`, `hashlib`, `json`, `decimal` and `typing`: request and registry checks written out in code, a hand-written tokenizer and parser for the formula grammar, the table parser, the evaluator in one private decimal context, and the archive document. The closed error registry and the one-code fault registry (`INTERNAL_ERROR`) are module constants; the skill reads no file, not even its own manifest.

### Directive (`instructions.md`)

Registry id, when not to use, what the caller owns, the request and registry document, the formula grammar, the table format, evaluation and rounding, the output definitions, and the error registry with every reason, in check order.

### Reference (`schemas/`)

JSON Schemas for the request and the output (documentation; the runtime checks every rule in code).

### Fixtures (`fixtures/`)

Nine synthetic reference pairs: a capital-impact run, a labelled revenue projection with an unrequested intermediate quantity, a verify of the stored capital-impact document, and one request for each of a table that failed upstream validation, a changed table with its old digest, an unregistered revision, an undefined quantity, a stored document with one character changed and a division by zero. `card_ui_samples.json` is the output of the capital-impact run.

## Manifest Details

**Parameters Schema:**
* `action` (string, required): `run` or `verify`.
* `model_name`, `model_version`, `parameter_revision` (strings, required): the model (`[a-z][a-z0-9_]{0,63}`), its version (`(?:0|[1-9][0-9]{0,5})\.(?:0|[1-9][0-9]{0,5})\.(?:0|[1-9][0-9]{0,5})`) and the revision (`rev-[1-9][0-9]{0,8}`). Each pattern is applied with ASCII rules, and the whole value must match.
* `input_files` (array, required): 1 to 20 entries of `file_ref`, `input_alias`, `sha256`, `validation_status` and `content`.
* `requested_quantities` (array, required): 1 to 100 distinct quantity names.
* `archive_label` (string, optional): the label of the suggested archive location.
* `registry` (object, required): the registry document.
* `archive_document` (string, `verify` only): the stored archive document.

The manifest parameter schema is flat (one `type` and a description per field, with `enum` on `action`); the nested rules are checked in code, so every error code can be reached through hosts that validate parameters first.

**Outputs Schema:**
* `run`: `status`, `model_name`, `model_version`, `parameter_revision`, `results` (each `quantity`, `value` and, when declared, `unit`, in request order) and `archive_record` (`run_id`, `input_digest`, `registry_entry_sha256`, `model_reference`, `inputs_reference`, `outputs_reference`, `document`, `document_sha256`).
* `verify`: `status`, `verified` (`true`), `run_id`, `document_sha256`.

Contract violations return `{"status": "error", "error": {"code", "detail"}}`. `detail` holds a fixed `reason` and, when one applies, the `field` path, written with names and indexes. No detail repeats a table cell, a parameter value or any other input text.

**Archive definitions:** canonical JSON is `json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)` encoded as UTF-8. `input_digest` is the SHA-256 of each file's digest and a line feed, in request order. `registry_entry_sha256` is the SHA-256 of the canonical JSON of the selected version. `run_id` is `run-` and the first 16 hex characters of the SHA-256 of the run's identity (label, input digest, model, version, revision, registry entry digest and requested quantities), so identical requests share one run id. `document` references every table by digest and never copies its content.

**Error registry (closed, in check order):** `INVALID_REQUEST`, `REGISTRY_INVALID`, `MODEL_DEFINITION_INVALID`, `MODEL_NOT_REGISTERED`, `VERSION_NOT_REGISTERED`, `REVISION_NOT_REGISTERED`, `QUANTITY_NOT_DEFINED`, `INPUT_BINDING_MISMATCH`, `INPUT_NOT_VALIDATED`, `DIGEST_MISMATCH`, `INPUT_PARSE_ERROR`, `MODEL_EVALUATION_ERROR`, `ARCHIVE_MISMATCH`. **Fault registry (one code):** `INTERNAL_ERROR`, returned only when an unexpected exception escapes the checks.

## Environment

No environment variables. Fully offline; all inputs are passed to `execute()`. Reads no file, not even inside its bundle.

## Example Usage (Direct)

```python
import json
import os

from skill_assay.core.loader import SkillLoader

bundle = SkillLoader.load_skill("finance/approved_financial_model_runner")
skill = bundle["class"]()
root = os.path.join("skills", "finance", "approved_financial_model_runner", "fixtures")

with open(os.path.join(root, "capital_impact_run_params.json")) as f:
    request = json.load(f)
run = skill.execute(request)
for item in run["results"]:
    print(item["quantity"], item["value"], item.get("unit", ""))
stored = run["archive_record"]["document"]  # store this before relying on the figures

check = skill.execute({**request, "action": "verify", "archive_document": stored})
print(check["verified"], check["run_id"])
```

## Usage Examples

Guides: [Usage index](../usage/README.md) · [Agent loops](../usage/agent_loops.md). No skill-specific API keys.

The skill's `execute()` makes no model call: in the snippets below the model belongs to the caller's agent, which calls the skill as a tool.

Use `bundle["class"]()` in the snippets below; explicit `bundle["module"].ApprovedFinancialModelRunnerSkill()` also works.

Sample user message: *Run the approved capital impact model on the prepared exposures and give me the figures.*

The provider snippets share this compact setup:

```python
import json
import os

with open(
    os.path.join(
        "skills",
        "finance",
        "approved_financial_model_runner",
        "fixtures",
        "capital_impact_run_params.json",
    )
) as f:
    RUN_PARAMS = json.load(f)
USER_MESSAGE = (
    "Run the registered model with the model runner tool and report the figures. "
    f"Request: {json.dumps(RUN_PARAMS)}"
)
```

### Gemini

```python
import google.genai as genai
from google.genai import types
from skill_assay.core.env import load_env_file
from skill_assay.core.loader import SkillLoader

load_env_file()
bundle = SkillLoader.load_skill("finance/approved_financial_model_runner")
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
        print(result["status"], result.get("results", result.get("error")))
```

### Claude

```python
import os

import anthropic
from skill_assay.core.env import load_env_file
from skill_assay.core.loader import SkillLoader

load_env_file()
bundle = SkillLoader.load_skill("finance/approved_financial_model_runner")
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
        print(result["status"], result.get("results", result.get("error")))
```

### OpenAI

```python
import json
import os

from openai import OpenAI
from skill_assay.core.env import load_env_file
from skill_assay.core.loader import SkillLoader

load_env_file()
bundle = SkillLoader.load_skill("finance/approved_financial_model_runner")
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
    print(result["status"], result.get("results", result.get("error")))
```

### DeepSeek

```python
import json
import os

from openai import OpenAI
from skill_assay.core.env import load_env_file
from skill_assay.core.loader import SkillLoader

load_env_file()
bundle = SkillLoader.load_skill("finance/approved_financial_model_runner")
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
    print(result["status"], result.get("results", result.get("error")))
```

### Ollama (prompt mode)

```python
import json

from skill_assay.core.loader import SkillLoader

bundle = SkillLoader.load_skill("finance/approved_financial_model_runner")
skill = bundle["class"]()
prompt = (
    "You may call tools as JSON blocks.\n"
    f"Tool: {bundle['manifest']['name']}\n"
    f"Instructions:\n{bundle['instructions']}\n"
    f"User: {USER_MESSAGE}"
)
print(prompt)
# When the model emits JSON tool args, pass them to execute:
result = skill.execute(RUN_PARAMS)
print(json.dumps(result, indent=2))
```

## Limitations (v0.1)

- **Formulas only.** A model is a list of formulas in the closed grammar; iterative solvers, lookups across rows and user code are outside this version.
- **Tables are a strict comma-separated subset.** No quoting, no thousands separators, no exponents; cells are decimal strings.
- **The caller holds the data.** The skill reads no storage and stores nothing: the registry, the tables and the archive document all pass through the call.
- **`verify` compares exactly.** A stored document that was re-serialised (spacing or key order changed) fails, even when its content is the same.

---

<!-- skill-history:begin -->
## Skill history

This bundle was written in this repository. Its provenance is anchored in
this repository (`anchor: repository` in `manifest.yaml`, with the content
hash refreshed whenever a bundle file changes). It is published with the
package release whose changelog lists it.
<!-- skill-history:end -->
