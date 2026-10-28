# Upgrade Gate

**Domain:** `dev_tools`
**Skill ID:** `dev_tools/upgrade_gate`
**Issuer:** [@mrmasa88](https://github.com/mrmasa88) ([AO](https://github.com/0x-AO-Protocol)) · **Project contact:** m@orblabs.ch
<!-- skill-doc-meta:begin -->
**Version**: `0.1.0` — 16 Oct 2026
<!-- skill-doc-meta:end -->
**Maturity:** Provisional — available for evaluation; interfaces or behavior may change in a compatible or documented breaking release.
**Recommended install:** `pip install "agent-skill-assay[dev_tools_upgrade_gate]"`. See [Install extras](../usage/install_extras.md).

[Skill Library](README.md) · [Testing](../TESTING.md)

Deterministic **upgrade gate** for pinned dependencies: `{watched events, since, as_of}` → a digest per repository, and `{dependencies, releases, advisories, policy, as_of}` → a verdict per dependency. `digest` aggregates host-fetched events (`release`, `tag`, `commit`) inside the window from `since` to `as_of`: counts by type, version jumps read from release and tag titles, breaking terms matched on whole tokens, and commits touching paths of interest. `assess` gives every pinned dependency a verdict `hold`, `upgrade`, `test` or `unknown`, with a target and reason codes from a closed list, decided by eight ordered rules over a release snapshot and optional advisories. Two ecosystems, `pypi` and `npm`, with the skill's own version comparator (semver 2.0.0 and a canonical PEP 440 subset); anything unsupported fails closed. Closed error registry, fail-closed contract errors; `execute()` is a pure function: no network, no clock, no file write, identical input → identical output. The v0.1 interface is defined by this bundle.

The skill **never fetches**: the host supplies manifests, release snapshots, advisories and events. The verdicts are **advice** to the operator and the agent; they block nothing, install nothing and open no pull request. Same design split as `monitoring/kpi_gate`: the skill computes, the model reads the changelog, and only when something moved.

> **Skill chains:** Run after export or middleware steps in host-defined order — see [Skill chaining](../usage/skill_chaining.md).

## When not to use

- **Not a package manager.** It installs nothing and changes no manifest.
- **Not a vulnerability scanner.** Advisories are an optional input the host supplies; the skill finds none on its own.
- **Not a fetcher.** It never contacts a registry or a repository; for a one-off "should I upgrade X?" question, a language model reading the changelog is enough. The skill exists for the repeated, unattended check across many dependencies, where the verdicts must be reproducible.

## Design split

- **The host owns the data**: it fetches manifests, release snapshots, advisories and repository events, and decides whether to run `assess` after `digest` (the intended chain runs `assess` only when `digest` returns `fired_any`).
- **The operator owns the policy and every upgrade decision**: `allow_major`, `allow_prerelease`, `min_age_days`, `max_snapshot_age_days`, `strict` and `paths_of_interest` are inputs; the verdicts are advice.
- **The skill is deterministic transformation only**: the same manifests and snapshot give the same verdicts, and the breaking-term list (`kb/breaking_terms.json`) is versioned data of this repository.

## Agent-loop contract

- `digest` runs first on the events of the watched repositories. When `fired_any` is true, something moved: the host may then run `assess` on the pinned set with a fresh snapshot, and the model reads the changelog of the dependencies whose verdict is `upgrade` or `test`.
- `assess` returns one result per dependency, in input order, with the verdict, the target and the reasons; `summary` counts the verdicts, `fired_any` says whether any dependency is `upgrade` or `test`, `insufficient` whether any is `unknown`, and `stale_snapshot` is a flag only.
- An error envelope means the request did not meet the contract: correct the input rather than retry with guessed values.

## Validation order (fail-closed, deterministic)

Each action runs a numbered check list in execution order and returns the first failing check's code with a `detail.reason` that names the check; the full lists are in the bundle's `instructions.md`. In outline:

1. Request envelope: an object, a known `action`, no foreign key, `as_of` present (`MISSING_AS_OF`) and a real date.
2. `assess`: `snapshot_at` not after the end of `as_of`; the policy (`INVALID_POLICY`); the list types; at least one dependency (`EMPTY_DEPENDENCIES`); the size caps; then dependency by dependency (`UNKNOWN_ECOSYSTEM`, the name pattern, `DUPLICATE_DEPENDENCY`, `INVALID_VERSION`, `INVALID_RANGE`), release entry by release entry with its versions (`FUTURE_RELEASE` for a version published after `as_of`), and advisory by advisory.
3. `digest`: the window (`INVALID_WINDOW`), `watched` and the policy, the size caps, the path patterns (`INVALID_POLICY`), then repository by repository and event by event; an event after `as_of` is `FUTURE_EVENT`, with `detail` `{event_id, at}` as the published example shows.

`unknown` with `CURRENT_OUT_OF_RANGE` is the one fail-closed step outside the eight rules: the pin and the range disagree, and the manifest is to be corrected before the gate is re-run.

## Bundle layout

The skill lives in `skills/dev_tools/upgrade_gate/`. [Skill anatomy](../introduction.md#skill-anatomy). **Contract** — see Manifest Details below. **Assurance** — `test_skill.py` in the bundle.

### Effect (`skill.py`)

Pure Python: the request checks, the two version parsers and comparators, the two range grammars, name normalisation, the token scan for breaking terms, the path-pattern compiler and matcher, the window rules and the eight verdict rules. Standard library only; the closed error registry, the one-code fault registry (`INTERNAL_ERROR`), the rule table and the evaluation order are module constants.

### Directive (`instructions.md`)

Registry id, when not to use, what the host and the operator own, the agent-loop contract, inputs, the eight verdict rules with their kinds and the evaluation order, the reason codes, the version and range grammars with the unsupported forms, the window rules, the breaking terms and path patterns, the no-echo rule, the size caps, and both validation check lists verbatim.

### Reference (`schemas/`)

JSON Schemas for the `assess` request, the `digest` request and the output (documentation; the runtime uses explicit standard-library checks).

### Corpus (`kb/`)

`kb/breaking_terms.json` holds the six fixed breaking-change terms, matched as whole upper-cased tokens of ASCII letters and digits.

## Manifest Details

**Parameters Schema:**
* `action` (string, required): `digest` or `assess`.
* `as_of` (string): the evaluation date, `YYYY-MM-DD`; required by both actions.
* `snapshot_at` (string, `assess`): when the snapshot was taken, `YYYY-MM-DDTHH:MM:SSZ`, not after the end of `as_of`.
* `since` (string, `digest`): the first day of the window, not after `as_of`.
* `policy` (object): `assess`: `allow_major`, `allow_prerelease`, `min_age_days`, `max_snapshot_age_days` and the optional `strict`; `digest`: the optional `paths_of_interest`.
* `dependencies` (array, `assess`): objects with `name`, `ecosystem`, `current` and `range`.
* `releases` (array, `assess`, optional): entries with `name`, `ecosystem` and `versions` (`version`, `published_at`, optional `yanked` and `notes`).
* `advisories` (array, `assess`, optional): objects with `id`, `name`, `ecosystem` and `affected`.
* `watched` (array, `digest`): objects with `repo` and `events` (`type`, `id`, `at`, optional `title` and `paths`).

The parameter schema is flat: each property carries one `type` and a description, with no nested item schema, and only `action` is required at the manifest level, so that every contract code can be reached through `validate_params()` hosts; the per-action requirements and the element shapes are enforced in code.

**Outputs Schema:**
* `status` (string): `completed`, or `error` for a contract violation.
* `results` (array, `assess`): per dependency `name`, `ecosystem`, `current`, `latest`, `latest_compatible`, `drift` (`major`, `minor`, `patch`), `verdict`, `target`, `reasons[]`, `signals` (`breaking_terms[]`, `yanked_skipped`, `advisory`, `major_available`).
* `summary` (object, `assess`): the counts of `hold`, `upgrade`, `test` and `unknown`.
* `window` (object, `digest`): `since` and `as_of`.
* `per_repo` (array, `digest`): per repository `repo`, `counts` (`release`, `tag`, `commit`), `version_jumps[]` (`from`, `to`, `kind`), `breaking_hits[]` (`event_id`, `term`), `path_hits[]` (`event_id`, `pattern`), `notable[]`, `dropped_before_since`, `unparsed_versions`.
* `fired_any`, `insufficient` (boolean, both actions) and `stale_snapshot` (boolean, `assess`).

Release notes and paths appear in no output field; of an event title, only a version read from it appears, in `version_jumps`, without its leading `v`, at most 64 characters and valid semver, and a host treats those version strings as untrusted data like the titles they come from. Outputs carry ids, versions, counts, the operator's own patterns and matched fixed terms only. Contract violations return `{"status": "error", "error": {"code", "detail"}}` instead; `detail` is an object with at least `reason` (for `FUTURE_EVENT` exactly `{event_id, at}`) and never echoes free text; a policy integer outside its range carries `reason`, `path` and `limit`, without `actual`; `INVALID_VERSION` and `INVALID_RANGE` give the path of the field, never the string.

**Error registry (closed):** `INVALID_REQUEST`, `MISSING_AS_OF`, `INVALID_WINDOW`, `FUTURE_EVENT`, `EMPTY_DEPENDENCIES`, `DUPLICATE_DEPENDENCY`, `UNKNOWN_ECOSYSTEM`, `INVALID_VERSION`, `INVALID_RANGE`, `FUTURE_RELEASE`, `INVALID_POLICY`. **Fault registry (one code):** `INTERNAL_ERROR`, returned only when an unexpected exception escapes the checks.

**Verdict rules:** the eight published rules in their published order, each typed as a decider (1 to 5), a modifier (6), a filter (7) or an absence rule (8), evaluated in the order `8, current_out_of_range, 7, 1, 2, 3, 4, 5, 6`; a target that an advisory for the dependency contains is never proposed: the verdict is then `hold` with `ADVISORY_ON_TARGET`. **Reason codes (closed):** `ADVISORY`, `NO_FIX_IN_RANGE`, `UP_TO_DATE`, `PATCH_AVAILABLE`, `MINOR_AVAILABLE`, `MAJOR_AVAILABLE`, `MAJOR_ALLOWED`, `MIN_AGE_MET`, `MIN_AGE_NOT_MET`, `BREAKING_TERM`, `YANKED_SKIPPED`, `PRERELEASE_SKIPPED`, `NO_RELEASE_SNAPSHOT`, `CURRENT_OUT_OF_RANGE`, `ADVISORY_ON_TARGET`.

**Size caps:** every cap is a module constant with one test and one row in the table of `instructions.md`: 2,000 dependencies, release entries and advisories, 2,000 versions per entry and 50,000 in all, 20 advisories per dependency, 214 characters per name, 64 per version, 256 characters and 8 clauses per range, 20,000 characters per note and 2,000,000 in all; 200 repositories, 5,000 events per repository and 50,000 in all, 500 characters per title and 1,000,000 in all, 500 paths per event, 1,024 characters per path, 5,000 paths and 50,000 path characters in all, 20 patterns of 256 characters with at most 4 wildcards, and 100,000 path-pattern pairs. The clause count is taken only for `pypi` and `npm`; with any other ecosystem the ecosystem check reports `UNKNOWN_ECOSYSTEM`, whatever the clause count.

## Environment

No environment variables. Fully offline; all inputs are passed to `execute()`. Reads only files inside its bundle.

## Example Usage (Direct)

The bundle ships the reference fixtures (`fixtures/example_digest_params.json`, `fixtures/example_assess_params.json`, `fixtures/example_future_event_params.json`):

```python
import json
import os

from skill_assay.core.loader import SkillLoader

bundle = SkillLoader.load_skill("dev_tools/upgrade_gate")
skill = bundle["class"]()
root = os.path.join("skills", "dev_tools", "upgrade_gate", "fixtures")

with open(os.path.join(root, "example_digest_params.json")) as f:
    digest = skill.execute(json.load(f))
print(digest["status"], digest["fired_any"])
for entry in digest["per_repo"]:
    print(entry["repo"], entry["version_jumps"], entry["notable"])

with open(os.path.join(root, "example_assess_params.json")) as f:
    assessed = skill.execute(json.load(f))
for result in assessed["results"]:
    print(result["name"], result["verdict"], result["target"], result["reasons"])
print(assessed["summary"])
```

## Usage Examples

Guides: [Usage index](../usage/README.md) · [Agent loops](../usage/agent_loops.md). No skill-specific API keys.

Use `bundle["class"]()` in the snippets below; explicit `bundle["module"].UpgradeGateSkill()` also works.

Sample user message: *Run the weekly dependency check on this snapshot and tell me what to upgrade.*

The provider snippets share this compact setup:

```python
ASSESS_REQUEST = {
    "action": "assess",
    "as_of": "2026-09-12",
    "snapshot_at": "2026-09-11T02:00:00Z",
    "dependencies": [
        {"name": "demo_http", "ecosystem": "pypi", "current": "2.3.1", "range": ">=2.3,<3"}
    ],
    "releases": [
        {
            "name": "demo_http",
            "ecosystem": "pypi",
            "versions": [{"version": "2.3.4", "published_at": "2026-09-01T00:00:00Z"}],
        }
    ],
    "policy": {
        "allow_major": False,
        "allow_prerelease": False,
        "min_age_days": 3,
        "max_snapshot_age_days": 7,
    },
}
USER_MESSAGE = (
    "Assess my pinned dependencies with the upgrade gate tool. "
    f"request={ASSESS_REQUEST}"
)
```

### Runnable examples

- Local execute: [`examples/upgrade_gate_demo.py`](../../examples/upgrade_gate_demo.py) — runs the in-bundle fixtures fully offline: the digest of one watched repository, the assessment of two pinned dependencies, and two fail-closed contract errors (an event after `as_of`, an unsupported range)

### Gemini

```python
import google.genai as genai
from google.genai import types
from skill_assay.core.env import load_env_file
from skill_assay.core.loader import SkillLoader

load_env_file()
bundle = SkillLoader.load_skill("dev_tools/upgrade_gate")
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
        print(result["status"], result.get("summary", result.get("error")))
```

### Claude

```python
import os

import anthropic
from skill_assay.core.env import load_env_file
from skill_assay.core.loader import SkillLoader

load_env_file()
bundle = SkillLoader.load_skill("dev_tools/upgrade_gate")
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
        print(result["status"], result.get("summary", result.get("error")))
```

### OpenAI

```python
import json
import os

from openai import OpenAI
from skill_assay.core.env import load_env_file
from skill_assay.core.loader import SkillLoader

load_env_file()
bundle = SkillLoader.load_skill("dev_tools/upgrade_gate")
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
    print(result["status"], result.get("summary", result.get("error")))
```

### DeepSeek

```python
import json
import os

from openai import OpenAI
from skill_assay.core.env import load_env_file
from skill_assay.core.loader import SkillLoader

load_env_file()
bundle = SkillLoader.load_skill("dev_tools/upgrade_gate")
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
    print(result["status"], result.get("summary", result.get("error")))
```

### Ollama (prompt mode)

```python
import json

from skill_assay.core.loader import SkillLoader

bundle = SkillLoader.load_skill("dev_tools/upgrade_gate")
skill = bundle["class"]()
prompt = (
    "You may call tools as JSON blocks.\n"
    f"Tool: {bundle['manifest']['name']}\n"
    f"Instructions:\n{bundle['instructions']}\n"
    f"User: {USER_MESSAGE}"
)
print(prompt)
# When the model emits JSON tool args, pass them to execute:
result = skill.execute(ASSESS_REQUEST)
print(json.dumps(result, indent=2))
```

## Limitations (v0.1)

- **Two ecosystems**: `pypi` and `npm`. Other ecosystems, manifest parsing and transitive exposure are outside this version.
- **No fetching and no actuation**: the host supplies every snapshot and event; the skill opens no pull request and runs no test.
- **A canonical version subset**: `pypi` versions with an epoch, a local version or an alternate spelling, and ranges with `===`, hyphen ranges, tags or URLs are contract errors, never guesses.
- **Prereleases in `npm` ranges** are compared by plain precedence when the policy admits them, with caret, tilde and x-range upper bounds closed by the next line's lowest prerelease as node-semver desugars them; this differs from node-semver's default, which admits a prerelease only beside a comparator on the same release.
- **Breaking terms are six fixed tokens**; a term in a note is a signal to read the changelog, not a fact about the release.
- **Path patterns** use a closed character set with `?`, `*` and `**` only, at most four wildcards per pattern.

---

<!-- skill-history:begin -->
## Skill history

This bundle was written in this repository. Its provenance is anchored in
this repository (`anchor: repository` in `manifest.yaml`, with the content
hash refreshed whenever a bundle file changes). It is published with the
package release v0.1.3.
<!-- skill-history:end -->
