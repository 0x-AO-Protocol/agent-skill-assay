# Period Close

**Domain:** `finance`
**Skill ID:** `finance/period_close`
**Issuer:** [@mrmasa88](https://github.com/mrmasa88) ([AO](https://github.com/0x-AO-Protocol)) · **Project contact:** m@orblabs.ch
<!-- skill-doc-meta:begin -->
**Version**: `0.1.0` — 16 Oct 2026
<!-- skill-doc-meta:end -->
**Maturity:** Provisional — available for evaluation; interfaces or behavior may change in a compatible or documented breaking release.
**Recommended install:** `pip install "agent-skill-assay[finance_period_close]"`. See [Install extras](../usage/install_extras.md).

[Skill Library](README.md) · [Testing](../TESTING.md)

Deterministic **period close** over a journal and a chart of accounts supplied by the host: `{journal, chart_of_accounts, period, as_of}` → findings, and with `close` → trial balance, income statement, balance sheet, direct-method cash flow, checks, notes, ratios and observations. `validate` returns the findings of the journal against the chart from a closed registry of twelve codes with three severities. `close` runs `validate` first and fails closed on any blocking finding (`CLOSE_BLOCKED`) unless `soft_close` is set; a soft close returns the statements marked `provisional: true` with the blocking findings repeated in `notes`. Given prior periods' journals it names recurring items that are missing (`EXPECTED_MISSING`) or out of line with their past median (`AMOUNT_DEVIATION`) and never fills them in. Single currency, single entity; amounts are decimal strings and every figure is computed with one private decimal context, so no binary floating-point value reaches the output. Closed error registry, fail-closed contract errors; `execute()` is a pure function: no network, no clock, no file access, identical input → identical output. The v0.1 interface is defined by this bundle.

The skill **computes; the model interprets**. The close runs unattended over the local journal, and the model is called only when something is off: an unbalanced entry, a missing recurring item, an amount out of line.

> **Skill chains:** Run after export or middleware steps in host-defined order — see [Skill chaining](../usage/skill_chaining.md).

## When not to use

- **Not for tax computation, jurisdiction-specific presentation rules, payroll, multi-entity consolidation or bank-feed retrieval.** Those live in the bookkeeping system or with the accountant.
- **Not a fetcher.** It does not fetch or sort anything: the host supplies the journal, the chart of accounts and, optionally, prior periods.
- **Not for a one-off look at a spreadsheet of transactions.** A language model reading the file is enough; this skill exists for the repeated, unattended close whose numbers must be reproducible and whose gaps must be named.

## Design split

- **The host supplies the data**: the journal, the chart of accounts with its closed classes and subclasses, the opening balances and, optionally, the prior periods. It decides whether to call the model.
- **The skill is deterministic computation only**: findings, statements, ratios and gap notes. Every rule is written in `instructions.md`, and every figure is reproducible from the input.
- **The operator owns the books**, the chart mapping and the decision to rely on a provisional close. A gap note is a question for the operator, never a number to fill.

## Agent-loop contract

- `CLOSE_BLOCKED` stops the loop: the operator corrects the journal and the host calls `close` again. A soft close is a working figure, not a closed period.
- Warnings are surfaced with the statements and never change a figure.
- An error envelope means the request did not meet the contract: correct the input rather than retry with guessed values.

## Validation order (fail-closed, deterministic)

Both actions run one numbered check list in execution order and return the first failing check's code with a `detail.reason` that names the check; the full list is in the bundle's `instructions.md`. In outline:

1. Request envelope: an object, a known `action`, no unknown key, `as_of` present and a real date, the inputs of the right type, a well-formed `period`, a three-letter `currency`, well-formed `options`.
2. Size caps, compared before any line is parsed, the one figure for all lines included.
3. The chart, row by row: shape, unique account, class and subclass from the closed sets.
4. The opening balances, line by line; the journal, entry by entry, with every check of one entry before the next; the prior periods, period by period and then entry by entry, with every line's account required to be in the chart.
5. `close` only, after the findings are computed: `CLOSE_BEFORE_PERIOD_END` when `as_of` is before `period.end`, with or without `soft_close`; `CLOSE_BLOCKED` when `soft_close` is false and a blocking finding exists, with `detail.findings` listing each as `{code, refs}`.

Duplicate entry ids and unmapped accounts in the current journal or the opening balances are findings, not errors; an account used in a prior journal but absent from the chart is a contract error, so retired accounts stay in the chart.

## Bundle layout

The skill lives in `skills/finance/period_close/`. [Skill anatomy](../introduction.md#skill-anatomy). **Contract** — see Manifest Details below. **Assurance** — `test_skill.py` in the bundle.

### Effect (`skill.py`)

Pure Python, standard library only: the check list, the findings registry, the trial balance and statements, the direct-method cash flow, the per-ratio operands, the gap rules with their medians and cadence bands, and one rendering rule over a private `decimal` context. The closed error registry and the one-code fault registry (`INTERNAL_ERROR`) are module constants; the skill reads no file, not even its own manifest.

### Directive (`instructions.md`)

Registry id, when not to use, what the host and the operator own, the agent-loop contract, inputs, the closed class and subclass sets, the findings registry with severities, statement rules, gap rules, rounding and rendering, the null rule with the per-ratio table, the closed observations list, the size caps, and the check list verbatim.

### Reference (`schemas/`)

JSON Schemas for the request and the output (documentation; the runtime uses explicit standard-library checks).

### Fixtures (`fixtures/`)

Eleven reference pairs on synthetic data (`demo_bakery` style counterparties `demo_customer`, `demo_landlord`, `demo_supplier`): the issue's example completed, its fail-closed example, the soft close, a findings-only `validate` run, a full chart with liabilities, inventory, cost of goods sold, non-operating items, an equity movement and a cash transfer, the deviation threshold at, below and above the line, the recurrence threshold at and below, and a rendering case with a negative net income, an even-count median and a chart with no cash and no `cogs` account.

## Manifest Details

**Parameters Schema:**
* `action` (string, required): `validate` or `close`.
* `as_of` (string): the date of the run as `YYYY-MM-DD`; no clock is read. `close` needs `as_of` on or after `period.end`.
* `period` (object): `start` and `end`, `start` not after `end`; both ends belong to the period.
* `currency` (string): `^[A-Z]{3}$`; every entry and prior journal that names a currency must name this one.
* `chart_of_accounts` (array): rows of `account` (`^[a-z][a-z0-9_]{0,63}$`, unique), `class` (`asset`, `liability`, `equity`, `revenue`, `expense`) and an optional `subclass` from the closed set of that class.
* `opening_balances` (array, optional): one line per balance-sheet account with `account`, `debit` and `credit`.
* `journal` (array): entries of `id`, `date`, optional `counterparty`, optional `currency` and `lines[]` of `account`, `debit`, `credit`; amounts are non-negative decimal strings with at most two decimals.
* `prior_journals` (array, optional): earlier periods, oldest first, each with `period`, `journal` and optional `currency`; comparable in length to the current period within three days.
* `options` (object, optional): `soft_close` (default false), `recurrence_min_periods` (1 to 24, default 2), `deviation_threshold` (decimal string greater than zero with at most four decimals, default `"0.20"`).

The parameter schema is flat: each property carries one `type` and a description, with no nested item schema, and only `action` is required at the manifest level, so that every contract code (`MISSING_AS_OF` included) can be reached through `validate_params()` hosts; the per-action requirements and the element shapes are enforced in code.

**Outputs Schema:**
* `status` (string): `completed`, or `error` for a contract violation.
* `findings` (array, `validate`) and `blocking` (boolean, `validate`): one element per finding code that fired, in registry order, each with `code`, `severity`, `count` and `refs`.
* `closed`, `provisional` (booleans, `close`): a hard close is `closed: true`; a soft close is `closed: false`, `provisional: true`.
* `trial_balance` (array, `close`): one row per chart account in chart order with `account`, `class`, `opening`, `debit`, `credit`, `closing`.
* `income_statement` (object, `close`): `revenue`, `cogs`, `gross_profit`, `expenses`, `net_income`.
* `balance_sheet` (object, `close`): `assets`, `liabilities`, `equity_opening`, `equity_movements`, `net_income`, `equity_closing`, `balanced`.
* `cash_flow` (object, `close`): `cash_opening`, `inflows`, `outflows`, `cash_closing`, direct method.
* `checks` (array, `close`): the findings of `validate` for the same input.
* `notes` (array, `close`): the `EXPECTED_MISSING` and `AMOUNT_DEVIATION` notes and, in a soft close, first one `{code, refs}` element per blocking finding.
* `ratios` (object, `close`): `gross_margin`, `operating_margin`, `net_margin`, `current_ratio`, `runway_months`, `expense_concentration_top1`, each a four-decimal string or `null`.
* `observations` (array, `close`): exactly `revenue` and `cash_closing`, each with `metric`, `value` and `as_of` equal to `period.end`.
* `fired_any` (boolean, `close`): any blocking or warning finding exists. `insufficient` (boolean, `close`): any ratio is `null`.

Contract violations return `{"status": "error", "error": {"code", "detail"}}` instead; `detail` is an object with at least `reason` and never echoes input text other than validated ids and dates, except that `CLOSE_BLOCKED` carries exactly `{"findings": [{"code", "refs"}, ...]}`.

**Error registry (closed):** `INVALID_REQUEST`, `MISSING_AS_OF`, `INVALID_PERIOD`, `CLOSE_BEFORE_PERIOD_END`, `EMPTY_JOURNAL`, `INVALID_AMOUNT`, `UNKNOWN_ACCOUNT_CLASS`, `UNKNOWN_SUBCLASS`, `CURRENCY_MISMATCH`, `INVALID_DATE`, `CLOSE_BLOCKED`. **Fault registry (one code):** `INTERNAL_ERROR`, returned only when an unexpected exception escapes the checks.

**Findings registry (closed):** blocking `UNBALANCED_ENTRY`, `UNMAPPED_ACCOUNT`, `ENTRY_OUT_OF_PERIOD`, `ENTRY_AFTER_AS_OF`, `DUPLICATE_ENTRY_ID`, `OPENING_UNBALANCED`; warning `SUSPECTED_DUPLICATE`, `ZERO_LINE`, `NEGATIVE_CASH`, `EXPECTED_MISSING`, `AMOUNT_DEVIATION`; info `UNUSED_ACCOUNT`.

**Numbers:** every amount renders with exactly two decimals (`0` for zero), every ratio and deviation with four, `ROUND_HALF_UP`, from one private decimal context; `null` means the chart declares nothing the figure could come from, never zero. The accounting identities (debits equal credits, assets equal liabilities plus closing equity, closing cash equals opening cash plus inflows less outflows, one `net_income` in both statements) hold whenever no blocking finding exists.

**Observations:** `observations[]` is shaped for a planned upgrade of `monitoring/business_diagnostic`, which today accepts only yes/no observations; no working chain exists today, and the list is closed at `revenue` and `cash_closing`.

**Size caps:** every cap is a module constant with one test and one row in the table of `instructions.md`: 2,000 chart rows, 2,000 opening lines, 20,000 journal entries, 200 lines per entry, 24 prior periods with 20,000 entries each, and one figure of 200,000 lines across the journal and every prior journal together, compared before any line is parsed.

## Environment

No environment variables. Fully offline; all inputs are passed to `execute()`. Reads no file, not even inside its bundle.

## Example Usage (Direct)

The bundle ships the issue's example completed (`fixtures/example_close_params.json`) and its fail-closed companion (`fixtures/example_blocked_params.json`):

```python
import json
import os

from skill_assay.core.loader import SkillLoader

bundle = SkillLoader.load_skill("finance/period_close")
skill = bundle["class"]()
root = os.path.join("skills", "finance", "period_close", "fixtures")

with open(os.path.join(root, "example_close_params.json")) as f:
    params = json.load(f)
closed = skill.execute(params)
print(closed["status"], closed["closed"], closed["balance_sheet"]["balanced"])
for row in closed["trial_balance"]:
    print(row["account"], row["opening"], row["debit"], row["credit"], row["closing"])
print(closed["income_statement"])
print(closed["checks"])
print(closed["notes"])

with open(os.path.join(root, "example_blocked_params.json")) as f:
    blocked = json.load(f)
print(skill.execute(blocked))
```

## Usage Examples

Guides: [Usage index](../usage/README.md) · [Agent loops](../usage/agent_loops.md). No skill-specific API keys.

Use `bundle["class"]()` in the snippets below; explicit `bundle["module"].PeriodCloseSkill()` also works.

Sample user message: *Close the second quarter from this journal and tell me what is off.*

The provider snippets share this compact setup:

```python
import json
import os

with open(
    os.path.join("skills", "finance", "period_close", "fixtures", "example_close_params.json")
) as f:
    CLOSE_PARAMS = json.load(f)
USER_MESSAGE = (
    "Close the quarter with the period close tool and tell me what is off. "
    f"Request: {json.dumps(CLOSE_PARAMS)}"
)
```

### Runnable examples

- Local execute: [`examples/period_close_demo.py`](../../examples/period_close_demo.py) — runs the in-bundle fixtures fully offline: the issue's example closed, the same journal validated with an unbalanced entry, the fail-closed `CLOSE_BLOCKED`, the soft close, and a close attempted before the period end

### Gemini

```python
import google.genai as genai
from google.genai import types
from skill_assay.core.env import load_env_file
from skill_assay.core.loader import SkillLoader

load_env_file()
bundle = SkillLoader.load_skill("finance/period_close")
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
        print(result["status"], result.get("checks", result.get("error")))
```

### Claude

```python
import os

import anthropic
from skill_assay.core.env import load_env_file
from skill_assay.core.loader import SkillLoader

load_env_file()
bundle = SkillLoader.load_skill("finance/period_close")
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
        print(result["status"], result.get("checks", result.get("error")))
```

### OpenAI

```python
import json
import os

from openai import OpenAI
from skill_assay.core.env import load_env_file
from skill_assay.core.loader import SkillLoader

load_env_file()
bundle = SkillLoader.load_skill("finance/period_close")
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
    print(result["status"], result.get("checks", result.get("error")))
```

### DeepSeek

```python
import json
import os

from openai import OpenAI
from skill_assay.core.env import load_env_file
from skill_assay.core.loader import SkillLoader

load_env_file()
bundle = SkillLoader.load_skill("finance/period_close")
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
    print(result["status"], result.get("checks", result.get("error")))
```

### Ollama (prompt mode)

```python
import json

from skill_assay.core.loader import SkillLoader

bundle = SkillLoader.load_skill("finance/period_close")
skill = bundle["class"]()
prompt = (
    "You may call tools as JSON blocks.\n"
    f"Tool: {bundle['manifest']['name']}\n"
    f"Instructions:\n{bundle['instructions']}\n"
    f"User: {USER_MESSAGE}"
)
print(prompt)
# When the model emits JSON tool args, pass them to execute:
result = skill.execute(CLOSE_PARAMS)
print(json.dumps(result, indent=2))
```

## Limitations (v0.1)

- **Single currency, single entity**: an entry or prior journal in another currency is a contract error; consolidation and supplied exchange rates are outside this version.
- **No fetching and no posting**: the host supplies every input, and the skill writes nothing back; a gap is named, never filled.
- **No close before the period end**, soft or hard: a host that wants a month-to-date figure passes a shorter period.
- **Prior periods must be comparable**: each ends before the current period, overlaps no other and is within three days of the current period's length, and every account a prior journal uses stays in the chart.
- **Gap rules see pairs only**: entries without a counterparty form no pair, and `cadence` is derived from the prior periods, not supplied.
- **`observations[]` has no consumer yet**: the list is closed at two metrics and shaped for a planned upgrade of `monitoring/business_diagnostic`.

---

<!-- skill-history:begin -->
## Skill history

This bundle was written in this repository. Its provenance is anchored in
this repository (`anchor: repository` in `manifest.yaml`, with the content
hash refreshed whenever a bundle file changes). It is published with the
package release v0.1.2.
<!-- skill-history:end -->
