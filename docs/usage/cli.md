# CLI Reference

Agent Skill Assay ships a `skill-assay` command-line tool for discovering and inspecting
skills installed locally. It mirrors the same path resolution order used by
`SkillLoader.load_skill()`, so the skills listed are exactly the ones your
agent can load.

## Splash

The interactive CLI opens with a gradient-styled **SKILL ASSAY** ASCII logo, a
version tagline, and footer links. The block art (from `skill_assay/cli.py`):

```text
  ███████╗██╗  ██╗██╗██╗     ██╗     ██╗    ██╗ █████╗ ██████╗ ███████╗
  ██╔════╝██║ ██╔╝██║██║     ██║     ██║    ██║██╔══██╗██╔══██╗██╔════╝
  ███████╗█████╔╝ ██║██║     ██║     ██║ █╗ ██║███████║██████╔╝█████╗
  ╚════██║██╔═██╗ ██║██║     ██║     ██║███╗██║██╔══██║██╔══██╗██╔══╝
  ███████║██║  ██╗██║███████╗███████╗╚███╔███╔╝██║  ██║██║  ██║███████╗
  ╚══════╝╚═╝  ╚═╝╚═╝╚══════╝╚══════╝ ╚══╝╚══╝ ╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝
```

Tagline: `Agent Skill Assay v{version} — Skill Management Framework`. Gradient colors
follow the active [color theme](#color-themes). See
[Interactive menu](#interactive-menu) for a terminal screenshot.

## Installation

Install Agent Skill Assay — `rich` is included as a core dependency:

    pip install agent-skill-assay

## Running the CLI

After installation, the `skill-assay` command is available directly:

    agent-skill-assay
    skill-assay list
    skill-assay doctor
    skill-assay config show
    skill-assay context show
    skill-assay chain list
    skill-assay theme ocean
    skill-assay mail addressbook show
    skill-assay test
    skill-assay examples
    agent-skill-assay --version

If `skill-assay` is not recognized, Python's `Scripts` directory may not be on
your PATH.

**Unix** — verify with:

    which agent-skill-assay

**Windows** — verify with:

    where agent-skill-assay

If the command is not found, use the module fallback (works on any OS as long
as Python is installed):

    python -m skill_assay
    python -m skill_assay list
    python -m skill_assay test monitoring/kpi_gate
    python -m skill_assay doctor wellness/mental_coach
    python -m skill_assay list --category compliance
    python -m skill_assay --help

**Windows PATH fix** — add both `Python3x\` and `Python3x\Scripts\` to your
system PATH, or use the `py` launcher:

    py -3 -m pip install agent-skill-assay
    py -3 -m skill-assay list

## Version advisory

On CLI startup, Agent Skill Assay checks the installed package version **once per process**.
If you are on an **unsupported** release (below `0.1.0`, for example `0.0.9`), a single
dim message is printed to stderr suggesting an upgrade to `>= 0.1.0`. Supported
installs (`0.1.0` and above) stay silent.

Library use (`import skill_assay`, `SkillLoader`) never prints this message.

To disable the check in CI or automation:

    export SKILL_ASSAY_NO_VERSION_CHECK=1

See [SECURITY.md](../../SECURITY.md) for the full supported-version policy.

## Interactive menu

Running `skill-assay` with no arguments launches an ASCII splash screen and an
interactive numbered menu:

    agent-skill-assay

The splash shows a gradient-styled **SKILL ASSAY** ASCII logo, a tagline in the
form `Agent Skill Assay v{version} — Skill Management Framework` (same version string
as `skill-assay --version`), and dim footer links to the project site and
repository. The menu accepts both number input (`1`) and command name (`list`).
After each command completes, the menu re-prints automatically.

**Navigation (all menu levels):**

| Input | Action |
| :--- | :--- |
| `0` | Exit Agent Skill Assay |
| `b` / `back` | Return to the previous menu (submenus only) |
| `q` | Same as `0` (exit) |
| `Ctrl+C` | Exit from the main menu |

Available commands:

| Input | Command | Status |
| :--- | :--- | :--- |
| `1` / `list` | List all locally installed skills | Available |
| `2` / `examples` | Browse runnable example scripts (index from `examples/README.md`, or from GitHub when no local copy exists) | Available |
| `3` / `test` | Run bundle tests (`test_skill.py`) for one or all skills | Available |
| `4` / `paths` | Paths submenu — view roots, edit project/external paths, shadowing, flat-layout diagnose | Available |
| `5` / `doctor` | Check manifest deps and skill.py import readiness | Available |
| `6` / `help` | Grouped help (Skills, Examples, Paths, Config, Context, Chains, Theme, Mail, General) with doc links | Available |
| `7` / `mail` | Mail submenu — address book and signature setup for `wellness/mental_coach` | Available |
| `8` / `theme` | Choose `pastel`, `ocean`, or `mono` and save it to global config | Available |

## Grouped help

`skill-assay --help` prints a **compact topic index**, **CLI usage examples**, and pointers to install/docs. For full command details per topic, run `skill-assay` and choose **`6` / `help`**, then pick a topic:

| Input | Topic |
| :--- | :--- |
| `1` / `skills` | `list`, `test`, `doctor` |
| `2` / `examples` | indexed runnable scripts |
| `3` / `paths` | resolution and paths submenu |
| `4` / `config` | merged YAML settings |
| `5` / `context` | `context show` — registry brief (SkillContext) |
| `6` / `chains` | `chain list`, `show`, `validate`, `run`, `dry-run` |
| `7` / `theme` | `theme` — CLI color theme picker or direct set |
| `8` / `mail` | address book and signature setup |
| `9` / `general` | menu, `--help`, `--version` |
| `10` / `install` | pip install agent-skill-assay |
| `11` / `docs` | link to this CLI guide |
| `12` / `interactive` | numbered splash menu |

Brief `--help` topic index matches the table above. Each topic expands to the command groups below when you choose **`6` / `help`** in the interactive menu:

| Group | Topics |
| :--- | :--- |
| **Skills** | `list`, `test`, `doctor` |
| **Examples** | `examples` |
| **Paths** | `paths`, interactive paths submenu |
| **Config** | `config show` |
| **Context** | `context show` — registry brief (SkillContext) |
| **Chains** | `chain list`, `chain show`, `chain validate`, `chain run`, `chain dry-run` |
| **Theme** | `theme`, interactive menu `8`, direct `theme <name>` |
| **Mail** | `mail`, interactive mail submenu |
| **General** | interactive menu, `--help`, `--version` |

## Commands

### skill-assay list

Print a table of all locally available skills.

    skill-assay list

Sample output:

    ID                           VERSION  CATEGORY    ISSUER      DESCRIPTION                                       REQUIREMENTS
    monitoring/business_diagnostic           0.1.0    monitoring  AO          Scenario ledger for a business: declared-weight odds vs operator marks; Brier.
    monitoring/kpi_gate                       0.1.0    monitoring  AO          Deterministic KPI gate: metrics snapshot vs policy charter, fail-closed findings…
    security/prompt_injection_firewall        0.1.0    security    AO          Offline prompt-injection firewall with local detectors and sanitization.
    wellness/mental_coach                     0.1.1    wellness    AO          Crisis triage and grounded coaching guardrails for wellness support.  pyyaml, google-genai

#### Flags

| Flag | Description |
| :--- | :--- |
| `--category <name>` | Show only skills in the given category. Discovered at runtime, never hardcoded. |
| `--issuer <handle>` | Show only skills by a given GitHub handle or issuer name. |
| `--skills-root <path>` | Override the skills directory for this command only. |
| `--examples` | Add an **EXAMPLES** column with the count of indexed scripts per skill (`-` when none). Works with `--category` and `--issuer`. |

#### Examples

    # Filter by category
    skill-assay list --category monitoring

    # Show example script counts per skill
    skill-assay list --examples
    skill-assay list --examples --category monitoring

    # Filter by issuer
    skill-assay list --issuer AO

    # Use a custom skills directory
    skill-assay list --skills-root /path/to/my/skills

### skill-assay examples

List runnable scripts indexed in `examples/README.md` (source of truth — the CLI does not scan `examples/*.py` directly). When no local `examples/README.md` is found (typical for `pip install`), the CLI loads the index from GitHub `main`.

    skill-assay examples
    skill-assay examples monitoring/business_diagnostic
    skill-assay examples monitoring/kpi_gate

#### Arguments

| Input | Description |
| :--- | :--- |
| *(no args)* | All indexed scripts (script-first view) |
| `<category>/<skill_name>` | Scripts linked to that skill ID only |

Columns: Script, Skill ID(s), Provider, Required extra (pip extra name), and **GITHUB** — the script filename as a clickable link to `main` (ctrl+click; full URL not repeated in the cell). Environment variables and longer notes stay in `examples/README.md`; a one-line pointer is printed below the table.

Unknown skill IDs exit with a helpful message and non-zero status.

In the interactive menu, choose **`2` / `examples`**, optionally enter a skill ID, then browse the same table with GitHub links.

### skill-assay test

Run skill **bundle tests** (`test_skill.py`) via pytest. Uses the same skill roots as `skill-assay list` (`SKILL_ASSAY_SKILL_PATH`, `--skills-root`, cwd `skills/`, bundled registry).

Requires pytest (`pip install -e ".[dev]"` or `pip install -e ".[dev,all]"`).

    skill-assay test
    skill-assay test monitoring/kpi_gate
    skill-assay test --category compliance
    skill-assay test --verbose
    skill-assay test monitoring/business_diagnostic --no-header

#### Arguments and flags

| Input | Description |
| :--- | :--- |
| *(no args)* | Run bundle tests under all resolved skill roots |
| `<category>/<skill_name>` | Run one skill's `test_skill.py` |
| `--category <name>` | Run all bundle tests in a category directory |
| `--skills-root <path>` | Override the skills directory for this command |
| `-v` / `--verbose` | Pass `-v` to pytest |
| `--no-header` | Pass `--no-header` to pytest |

Exit code matches pytest (non-zero on failures or missing test paths).

### skill-assay paths

Show where Agent Skill Assay looks for skills — same order as `SkillLoader.load_skill()` —
with tier labels (**project**, **external**, **bundled**; order depends on
legacy vs config — see [Path resolution](#path-resolution)), per-root skill
counts, and shadowing warnings when the same registry ID exists in multiple roots.
Only **existing** roots are searched at load time; missing project directories
are skipped, and bundled skills from `pip install agent-skill-assay` remain available.

    skill-assay paths
    skill-assay paths --skills-root /path/to/my/skills

#### Flags

| Flag | Description |
| :--- | :--- |
| `--skills-root <path>` | Override the skills directory for this command only (shows a single override root). |

Non-interactive `skill-assay paths` is read-only. To **persist** project and external roots, use the interactive **paths submenu** (menu **`4` / `paths`**) or edit `.skill-assay.yaml` manually.

#### Interactive paths submenu (menu `4`)

| Input | Action |
| :--- | :--- |
| `1` / `view` | Same table as `skill-assay paths` (resolution, tiers, shadowing) |
| `2` / `bundled` | Show bundled registry root (read-only; shipped with pip) |
| `3` / `project` | Set `paths.project` to `auto` or an explicit directory (saved to `.skill-assay.yaml`) |
| `4` / `external` | Add or remove `paths.external` entries (saved to project config) |
| `5` / `shadows` | Shadowing summary only |
| `6` / `flat` | List flat-layout skills (`<root>/<name>/`) that load but do not appear in `skill-assay list` |
| `b` / `back` | Return to the main menu |

Bundled registry paths cannot be edited. Global config (`~/.config/skill_assay/config.yaml`) is not modified by the submenu — use `skill-assay config show` to inspect merged settings.

### skill-assay doctor

Check whether skills can load in the current environment — manifest **requirements** (**DEPS**), `skill.py` import (**LOAD**), and required **`env_vars`** (**ENVS**) — without running `execute()`. Uses the same skill roots as `skill-assay list`.

    skill-assay doctor
    skill-assay doctor monitoring/kpi_gate
    skill-assay doctor --category compliance
    skill-assay doctor --skills-root /path/to/my/skills

#### Arguments and flags

| Input | Description |
| :--- | :--- |
| *(no args)* | Diagnose all registry skills visible to `list` |
| `<category>/<skill_name>` | Diagnose one skill |
| `--category <name>` | Diagnose all skills in a category |
| `--skills-root <path>` | Override the skills directory for discovery and load |

**DEPS** validates manifest `requirements`. **LOAD** imports `skill.py`; skipped (`—`) when **DEPS** fails. **ENVS** checks required manifest `env_vars` via `EnvSecretProvider` (your shell, `.env`, or CI secrets — see [API keys](api_keys.md)). Skills with no `env_vars` show `—`.

Exit code is non-zero when any skill fails **DEPS**, **LOAD**, or **ENVS**. For full bundle behavior, use `skill-assay test`.

Interactive menu: **`5` / `doctor`**.

### skill-assay config

Show merged global + project Agent Skill Assay configuration (read-only). The `paths`,
`mail`, and `presentation` sections are active; other top-level keys are
preserved for future settings.

    skill-assay config show

**Global config:** `~/.config/skill_assay/config.yaml` (Linux/macOS), `%APPDATA%/skill_assay/config.yaml` (Windows), or override with `SKILL_ASSAY_CONFIG_DIR`.

**Project config:** `.skill-assay.yaml` in the repository root (walks up from cwd). See [`.skill-assay.yaml.example`](../../.skill-assay.yaml.example).

Example project file:

```yaml
paths:
  project: auto
  external:
    - /path/to/private-skills
resolution:
  order:
    - project
    - external
    - bundled
legacy:
  honor_skill_assay_skill_path: true
presentation:
  theme: ocean
mail:
  addressbook_path: ~/.config/skill_assay/addressbook.yaml
  signature_path: ~/.config/skill_assay/mail_signature.txt
  signature_html_path: ~/.config/skill_assay/mail_signature.html
  signature_plain: |
    —
    Agent Name
    Agent Skill Assay Project
```

When no config file exists, resolution stays **legacy**: `SKILL_ASSAY_SKILL_PATH` → `./skills/` walk → bundled. When config exists, `resolution.order` applies (default: project → external → bundled). The **bundled** registry from `pip install agent-skill-assay` is always included and cannot be disabled. **Pip-only installs with no local `skills/` folder still resolve bundled registry skills** — only roots that exist on disk are searched; an empty project tier does not block bundled.

`skill-assay config show` reports the effective `presentation.theme`. To change
the global theme without editing YAML, use any of:

- `skill-assay theme ocean` — set directly
- `skill-assay theme` — interactive picker (non-menu)
- `skill-assay` → **`8` / `theme`** — same picker from the splash menu

The picker writes only `presentation.theme` in the global config and preserves unrelated settings.

A project `.skill-assay.yaml` value overrides the global selection while the CLI
runs inside that project. The picker still saves the global preference and
prints a notice that the project theme remains active. Remove or change the
project `presentation.theme` value to use the global selection there. Missing,
malformed, or unknown theme values fall back safely to `pastel`.

### skill-assay mail

Operator UX for **`wellness/mental_coach`** address book, email signatures (including multi-profile), and attachment path settings — without editing bundled skill files. **Full operator guide:** [`docs/skills/mental_coach.md`](../skills/mental_coach.md) (fresh install checklist, precedence, plain vs HTML MIME, attachments, persistence).

    skill-assay mail
    skill-assay mail addressbook init
    skill-assay mail addressbook add
    skill-assay mail addressbook add --name "Jane" --email jane@example.com
    skill-assay mail addressbook show
    skill-assay mail addressbook validate
    skill-assay mail addressbook set-path ~/.config/skill_assay/addressbook.yaml
    skill-assay mail signature init
    skill-assay mail signature init --force
    skill-assay mail signature show
    skill-assay mail signature set --file ./signature.txt
    skill-assay mail signature validate
    skill-assay mail signature clear
    skill-assay mail signature profiles
    skill-assay mail signature set-profile formal
    skill-assay mail signature add-profile formal --html ~/.config/skill_assay/signatures/formal.html

**Nothing is configured by default.** Run **`addressbook init`** and **`signature init`** once to create files under your user config dir (`~/.config/skill_assay/` or `%APPDATA%/skill_assay/`). That data **survives agent-skill-assay upgrades and reinstalls**; it is not stored inside the pip wheel.

**Precedence:** environment variables (`GMAIL_*`) → project `.skill-assay.yaml` → global `config.yaml` → skill bundled read-only defaults.

| Area | Actions |
|------|---------|
| Address book | **init**, **add** (wizard or `--name` / `--email` / `--aliases` / `--org` / `--id`), **show**, **validate**, **set-path** |
| Signature | **init** (plain + HTML text; `--force` overwrite), **show**, **set** (paste or `--file`), **validate**, **clear** |

**User config files** (after init):

| File | Purpose |
|------|---------|
| `addressbook.yaml` | Contacts |
| `mail_signature.txt` | Plain signature (`text/plain` MIME part) |
| `mail_signature.html` | HTML signature (text, `—`, links — what Gmail shows) |
| `config.yaml` | Registers `mail.*` paths |

**Env overrides** (optional): `GMAIL_ADDRESSBOOK_PATH`, `GMAIL_SIGNATURE_PATH`, `GMAIL_SIGNATURE_HTML_PATH`, `GMAIL_SIGNATURE_PLAIN`, `GMAIL_SCAN_STATE_PATH`, `GMAIL_SEND_LEDGER_PATH`.

Interactive menu: **`7` / `mail`**. Submenu mirrors the commands above.

Test signature in your inbox (requires `.env` credentials):

    python examples/gmail_signature_test_send.py --to you@example.com --preview-only
    python examples/gmail_signature_test_send.py --to you@example.com

Non-interactive `skill-assay mail` (no subcommand) prints resolved paths and signature source — similar to the `mail` block in `skill-assay config show`.

### skill-assay context

Show **SkillContext** registry brief (discovered skills, compact blurbs). Does not execute skills.

    skill-assay context show
    skill-assay context show --skill wellness/mental_coach
    skill-assay context show --categories security,compliance --roots bundled --mode brief
    skill-assay context show --export ctx.md

| `--mode` | Output |
| :--- | :--- |
| `brief` (default) | One-line summary per discovered skill |
| `tools_only` | Empty body (inspect skill list in header only) |
| `directives` | Full `instructions.md` per skill — use for small fixed sets |

See [Skill chaining](skill_chaining.md#skillcontext--discovery-filters).

### skill-assay chain

List, inspect, validate, and run **named chains** from merged config (`chains:` in `.skill-assay.yaml` / global `config.yaml`).

    skill-assay chain list
    skill-assay chain show sanitize_input
    skill-assay chain validate
    skill-assay chain validate sanitize_input
    skill-assay chain run sanitize_input --var source_text="hello"
    skill-assay chain run sanitize_input --var source_text=@./page.html --json
    skill-assay chain dry-run scan_then_gate --var source_text=hi --var task_id=t1 \
      --var current_token_count=1000 --var max_allowed_tokens=32000

| Subcommand | Description |
| :--- | :--- |
| `list` | Chain names, step counts, description, when |
| `show <name>` | Steps, bindings, required `host_input` keys |
| `validate [name]` | Structural + skill resolution; exit **1** on error (CI) |
| `run <name>` | Execute via `run_chain()`; `--var key=value` or `key=@file` |
| `dry-run <name>` | Resolve params and `when` without `execute()` |

See [Skill chaining](skill_chaining.md#named-chains-yaml--api).

## Path resolution

`skill-assay list`, `load_skill`, `test`, and `doctor` share the same roots as `SkillLoader`.

**Without config files (default):**

1. Roots listed in `SKILL_ASSAY_SKILL_PATH` (OS path separator between multiple entries)
2. A `skills/` directory under the current working directory and its parents
3. Bundled skills installed with the `skill-assay` package

**With `.skill-assay.yaml` and/or global config:**

1. Tiers in `resolution.order` (default: project → external → bundled)
2. `paths.project`: `auto` (same walk as above) or an explicit directory
3. `paths.external`: persisted private/proprietary skill roots
4. Bundled registry always last-resort fallback (always on)

Run `skill-assay paths` for a live view of resolved roots, tiers, and shadowing. Run `skill-assay config show` for merged YAML settings.

To point the CLI at custom roots without config files:

    export SKILL_ASSAY_SKILL_PATH=/path/to/my/skills
    skill-assay list

Or copy `.skill-assay.yaml.example` to `.skill-assay.yaml` and list paths under `paths.external`.

Only skills with both `manifest.yaml` and `skill.py` present are shown —
the same condition `SkillLoader` requires to load a skill successfully.

### Skill ID vs manifest `name` vs LLM tool name

| Concept | Source | Example (`monitoring/business_diagnostic`) |
| :--- | :--- | :--- |
| **CLI / loader ID** | Folder path `category/skill_name` | `monitoring/business_diagnostic` |
| **`manifest.yaml` `name`** | Should match the registry ID | `monitoring/business_diagnostic` |
| **Gemini tool name** | Sanitized via `to_gemini_tool()` / `_sanitize_gemini_tool_name()` | `monitoring_business_diagnostic` |
| **Claude tool name** | Sanitized via `to_claude_tool()` | `monitoring_business_diagnostic` |
| **OpenAI / DeepSeek tool name** | Sanitized adapter name | `monitoring_business_diagnostic` |
| **Ollama prompt `"tool"`** | Same as manifest when using full IDs | `monitoring/business_diagnostic` |

`skill-assay list` always shows the **path-derived ID**; it does not read `manifest["name"]` for the ID column. Keep manifest `name` aligned with that ID so agent loops and `SkillLoader.to_*_tool()` stay consistent. `SkillLoader.load_skill()` warns via `SkillAssayIdentityWarning` when a registry-layout skill has a missing or mismatched `name` (flat private skills under `<skill_root>/<skill_name>/` are not checked). See [Agent loops](agent_loops.md#tool-name-matching).

## Color themes

### skill-assay theme

Choose or set the CLI presentation theme (`pastel`, `ocean`, `mono`):

    skill-assay theme
    skill-assay theme ocean

Without a theme name, `skill-assay theme` opens the same interactive picker as splash menu **`8` / `theme`**. With a name, it saves globally immediately and prints a notice when project config overrides the effective theme in the current directory.

The selected theme is applied to tables, headings, categories, skill IDs,
menus, links, statuses, errors, and the splash gradient.

| Theme | Description | Splash gradient |
| :--- | :--- | :--- |
| `pastel` | Default and fallback; preserves the original Agent Skill Assay lavender, peach, mint, ice, sky, and blush palette | `#D4E4F1` → `#79B6D8` → `#EBD8DC` |
| `ocean` | Deep blue, sky blue, and cyan | `#0C4A6E` → `#0284C7` → `#7DD3FC` |
| `mono` | Grayscale presentation | `#F0F0F0` → `#A0A0A0` → `#606060` |

Choose a theme interactively:

```text
skill-assay theme
theme> ocean
```

Or set directly:

```text
skill-assay theme ocean
```

From the splash menu:

```text
agent-skill-assay
> 8
theme> ocean
```

The selection takes effect for subsequent output in the same session and is
used on the next CLI start. The splash is printed once at startup, so restart
the CLI to see the newly selected splash gradient.

## short_description field

Skill manifests can include a `short_description` field (max 80 chars) for
a concise one-line summary shown in `skill-assay list`:

    short_description: "Screens Ethereum wallets against OFAC sanctions and mixer lists."

If `short_description` is absent, the CLI falls back to the first sentence
of `description`, truncated to 80 characters.
