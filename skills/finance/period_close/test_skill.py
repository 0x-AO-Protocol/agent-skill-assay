"""Bundle tests for finance/period_close.

All tests are offline and deterministic: the fixtures under fixtures/
exercise the published v0.1 interface, including the issue's examples, every
closed-registry error code in its documented order, every finding code with
its severity, every size cap, the accounting identities, the gap rules at
their boundaries, the rendering rule, the private decimal context and
bit-identical repeat execution across hash seeds. No network access is
required or permitted.
"""

import ast
import copy
import decimal
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from decimal import Decimal

import jsonschema
import pytest
import yaml

from skill_assay import SkillContext
from skill_assay.core.loader import SkillLoader

from . import skill as skill_module
from .skill import (
    ACTIONS,
    AMOUNT_DEVIATION,
    AMOUNT_DEVIATION_NOTE_KEYS,
    BALANCE_SHEET_KEYS,
    CAP_TABLE,
    CASH_FLOW_KEYS,
    CHECKS,
    CLASSES,
    CLOSE_BEFORE_PERIOD_END,
    CLOSE_BLOCKED,
    CLOSE_CHECKS,
    CLOSE_OUTPUT_KEYS,
    CURRENCY_MISMATCH,
    DEFAULT_SUBCLASS,
    DUPLICATE_ENTRY_ID,
    EMPTY_JOURNAL,
    ENTRY_AFTER_AS_OF,
    ENTRY_OUT_OF_PERIOD,
    ERROR_REGISTRY,
    EXPECTED_MISSING,
    EXPECTED_MISSING_NOTE_KEYS,
    FAULT_REGISTRY,
    FINDING_CODES,
    FINDING_SEVERITY,
    FINDINGS_REGISTRY,
    INCOME_STATEMENT_KEYS,
    INTERNAL_ERROR,
    INVALID_AMOUNT,
    INVALID_DATE,
    INVALID_PERIOD,
    INVALID_REQUEST,
    MAX_CHART_ROWS,
    MAX_JOURNAL_ENTRIES,
    MAX_LINES_PER_ENTRY,
    MAX_OPENING_LINES,
    MAX_PRIOR_ENTRIES_PER_PERIOD,
    MAX_PRIOR_PERIODS,
    MAX_RECURRENCE_MIN_PERIODS,
    MAX_TOTAL_LINES,
    MISSING_AS_OF,
    NEGATIVE_CASH,
    OBSERVATION_METRICS,
    OPENING_UNBALANCED,
    RATIO_KEYS,
    SEVERITY_BLOCKING,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    SUBCLASSES,
    SUSPECTED_DUPLICATE,
    TRIAL_BALANCE_KEYS,
    UNBALANCED_ENTRY,
    UNKNOWN_ACCOUNT_CLASS,
    UNKNOWN_SUBCLASS,
    UNMAPPED_ACCOUNT,
    UNUSED_ACCOUNT,
    VALIDATE_CHECKS,
    VALIDATE_OUTPUT_KEYS,
    ZERO_LINE,
    PeriodCloseSkill,
)

BUNDLE_DIR = os.path.dirname(os.path.abspath(__file__))
FIXTURES_DIR = os.path.join(BUNDLE_DIR, "fixtures")
SCHEMAS_DIR = os.path.join(BUNDLE_DIR, "schemas")
REPO_ROOT = os.path.abspath(os.path.join(BUNDLE_DIR, "..", "..", ".."))
REGISTRY_ID = "finance/period_close"

# Reference fixtures: (params file, expected file). Every expected file is the
# exact output of its params file.
REFERENCE_FIXTURES = (
    ("example_close_params.json", "expected_close.json"),
    ("example_blocked_params.json", "expected_blocked.json"),
    ("soft_close_params.json", "expected_soft_close.json"),
    ("validate_params.json", "expected_validate.json"),
    ("full_chart_params.json", "expected_full_chart.json"),
    ("deviation_at_params.json", "expected_deviation_at.json"),
    ("deviation_below_params.json", "expected_deviation_below.json"),
    ("deviation_above_params.json", "expected_deviation_above.json"),
    ("recurrence_at_params.json", "expected_recurrence_at.json"),
    ("recurrence_below_params.json", "expected_recurrence_below.json"),
    ("rendering_params.json", "expected_rendering.json"),
)
# The close fixtures that complete with no blocking finding: the accounting
# identities hold on every one of them.
IDENTITY_FIXTURES = (
    "example_close_params.json",
    "full_chart_params.json",
    "deviation_at_params.json",
    "deviation_below_params.json",
    "deviation_above_params.json",
    "recurrence_at_params.json",
    "recurrence_below_params.json",
    "rendering_params.json",
)
EXPECTED_FIXTURE_FILES = {name for pair in REFERENCE_FIXTURES for name in pair}
EXPECTED_SCHEMA_FILES = {"request.schema.json", "output.schema.json"}
EXPECTED_BUNDLE_FILES = {
    "__init__.py",
    "manifest.yaml",
    "skill.py",
    "instructions.md",
    "test_skill.py",
    "card.json",
}

AMOUNT_RENDERING = re.compile(r"^(0|-?(0|[1-9][0-9]*)\.[0-9]{2})$")
RATIO_RENDERING = re.compile(r"^(0\.0000|-?(0|[1-9][0-9]*)\.[0-9]{4})$")
LONE_SURROGATE = "\ud800"
MARKER = "zq-marker-text"


# --------------------------------------------------------------------------
# Helpers and fixtures
# --------------------------------------------------------------------------


def _load_json(directory, name):
    with open(os.path.join(directory, name), "r", encoding="utf-8") as handle:
        return json.load(handle)


def _run(skill, params):
    return skill.execute(copy.deepcopy(params))


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _assert_error(result, code, reason=None):
    """The error envelope and nothing else; CLOSE_BLOCKED carries the issue's detail."""
    assert set(result) == {"status", "error"}
    assert result["status"] == "error"
    assert set(result["error"]) == {"code", "detail"}
    assert result["error"]["code"] == code
    detail = result["error"]["detail"]
    assert isinstance(detail, dict)
    if code == CLOSE_BLOCKED:
        assert set(detail) == {"findings"}
        assert reason is None
        return detail
    assert isinstance(detail["reason"], str) and detail["reason"]
    if reason is not None:
        assert detail["reason"] == reason
    return detail


def _assert_completed(result):
    assert result["status"] == "completed", result
    assert "error" not in result
    return result


def _instructions():
    with open(os.path.join(BUNDLE_DIR, "instructions.md"), "r", encoding="utf-8") as f:
        return f.read()


def _section(text, heading):
    """The text under a markdown heading up to the next heading of the same level."""
    level = heading.split(" ")[0]
    start = text.index("\n" + heading + "\n") + 1
    rest = text[start + len(heading) :]
    match = re.search(r"^" + re.escape(level) + r" ", rest, re.MULTILINE)
    return rest[: match.start()] if match else rest


def _flat(text):
    """Prose with line wraps removed, for phrase checks."""
    return " ".join(text.split())


def _line(account, debit="0", credit="0"):
    return {"account": account, "debit": debit, "credit": credit}


def _entry(entry_id, when, lines, counterparty=None, **more):
    entry = {"id": entry_id, "date": when}
    if counterparty is not None:
        entry["counterparty"] = counterparty
    entry["lines"] = lines
    entry.update(more)
    return entry


def _rent(when, amount="500.00", counterparty="demo_landlord"):
    return _entry(
        "rent-" + when[:7],
        when,
        [_line("rent", amount, "0"), _line("cash", "0", amount)],
        counterparty,
    )


def _params(f, action=None, **over):
    """A deep copy of the issue's example with top-level overrides."""
    params = copy.deepcopy(f["close"])
    if action is not None:
        params["action"] = action
    params.update(over)
    return params


def _decimal(text):
    return Decimal(text)


def _stripped(parameters):
    """The parameter schema with each description stripped of its line wraps."""
    schema = copy.deepcopy(parameters)
    for prop in schema["properties"].values():
        prop["description"] = " ".join(prop["description"].split())
    return schema


@pytest.fixture
def skill():
    return PeriodCloseSkill()


@pytest.fixture
def manifest():
    with open(os.path.join(BUNDLE_DIR, "manifest.yaml"), "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture
def close_params():
    return _load_json(FIXTURES_DIR, "example_close_params.json")


@pytest.fixture
def blocked_params():
    return _load_json(FIXTURES_DIR, "example_blocked_params.json")


@pytest.fixture
def validate_params():
    return _load_json(FIXTURES_DIR, "validate_params.json")


@pytest.fixture
def error_inputs(close_params, blocked_params):
    return {"close": close_params, "blocked": blocked_params}


# --------------------------------------------------------------------------
# Bundle shape, manifest, loader, actions
# --------------------------------------------------------------------------


def test_bundle_file_set_is_complete():
    def listing(root):
        names = set()
        for folder, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for name in files:
                if name == ".DS_Store":
                    continue
                names.add(os.path.relpath(os.path.join(folder, name), root))
        return names

    everything = listing(BUNDLE_DIR)
    assert {n for n in everything if os.sep not in n} == EXPECTED_BUNDLE_FILES
    assert listing(FIXTURES_DIR) == EXPECTED_FIXTURE_FILES
    assert listing(SCHEMAS_DIR) == EXPECTED_SCHEMA_FILES
    assert everything == (
        EXPECTED_BUNDLE_FILES
        | {os.path.join("fixtures", n) for n in EXPECTED_FIXTURE_FILES}
        | {os.path.join("schemas", n) for n in EXPECTED_SCHEMA_FILES}
    )
    assert not os.path.isdir(os.path.join(BUNDLE_DIR, "kb"))
    python_files = {n for n in everything if n.endswith(".py")}
    assert python_files == {"__init__.py", "skill.py", "test_skill.py"}
    assert os.path.isfile(os.path.join(BUNDLE_DIR, "..", "__init__.py"))


def test_manifest_declares_the_common_ao_fields(manifest):
    with open(
        os.path.join(REPO_ROOT, "skills", "monitoring", "kpi_gate", "manifest.yaml"),
        "r",
        encoding="utf-8",
    ) as handle:
        shipped = yaml.safe_load(handle)
    assert manifest["name"] == REGISTRY_ID
    assert manifest["version"] == "0.1.0"
    assert manifest["category"] == "finance"
    assert manifest["trust_tier"] == "L0"
    assert manifest["issuer"] == shipped["issuer"]
    assert manifest["issuer"]["org"] == "AO"
    assert manifest["norm_version"] == "norms-1.0.0"
    assert manifest["economic_effect"] == shipped["economic_effect"]
    assert manifest["requirements"] == []
    assert "env_vars" not in manifest
    provenance = manifest["provenance"]
    assert set(provenance) == {"anchor", "content_sha256", "authored_on"}
    assert provenance["anchor"] == "repository"
    assert re.fullmatch(r"[0-9a-f]{64}", provenance["content_sha256"])
    assert re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", provenance["authored_on"])
    lines = [line for line in manifest["constitution"].splitlines() if line.strip()]
    assert [line.split(".")[0] for line in lines] == ["1", "2", "3", "4", "5"]
    assert set(manifest["presentation"]) == {"icon", "color"}
    assert manifest["parameters"]["required"] == ["action"]
    assert manifest["parameters"]["additionalProperties"] is False
    for prop in manifest["parameters"]["properties"].values():
        assert isinstance(prop["type"], str)
        assert prop["description"]
    assert set(manifest["outputs"]) == set(VALIDATE_OUTPUT_KEYS) | set(
        CLOSE_OUTPUT_KEYS
    )


def test_skill_manifest_consistency(skill, manifest, close_params, validate_params):
    assert skill.manifest["name"] == manifest["name"] == REGISTRY_ID
    assert skill.manifest["version"] == manifest["version"]
    # The skill reads no file: its in-code manifest carries the same flat
    # parameter schema as manifest.yaml, so framework pre-validation agrees.
    assert _stripped(skill.manifest["parameters"]) == _stripped(manifest["parameters"])
    assert "output" not in manifest
    seen = set()
    for params in (close_params, validate_params):
        seen.update(_assert_completed(_run(skill, params)))
    assert set(manifest["outputs"]) == seen


def test_manifest_identity_matches_folder(manifest):
    category = os.path.basename(os.path.dirname(BUNDLE_DIR))
    folder = os.path.basename(BUNDLE_DIR)
    assert manifest["name"] == f"{category}/{folder}"


def test_manifest_parameters_validate_fixtures(skill):
    for params_name, _ in REFERENCE_FIXTURES:
        assert skill.validate_params(_load_json(FIXTURES_DIR, params_name)) is True


def test_manifest_parameters_are_flat(manifest):
    parameters = manifest["parameters"]
    assert parameters["type"] == "object"
    assert parameters["required"] == ["action"]
    assert parameters["additionalProperties"] is False
    for name, prop in parameters["properties"].items():
        expected = {"type", "description"} | ({"enum"} if name == "action" else set())
        assert set(prop) == expected, name
        assert isinstance(prop["type"], str)
        assert "items" not in prop
    assert parameters["properties"]["action"]["enum"] == list(ACTIONS)


def test_framework_path_reaches_the_skill_checks(close_params):
    context = SkillContext(skill=REGISTRY_ID)
    params = {**close_params, "journal": [MARKER]}
    result = context.execute(REGISTRY_ID, params)
    detail = _assert_error(result, INVALID_REQUEST, "not_an_object")
    assert detail["path"] == "journal[0]"
    assert MARKER not in json.dumps(result)
    assert _assert_completed(context.execute(REGISTRY_ID, close_params))["closed"]


def test_skill_loader_can_import():
    bundle = SkillLoader.load_skill(REGISTRY_ID)
    assert bundle["manifest"]["name"] == REGISTRY_ID
    assert hasattr(bundle["module"], "PeriodCloseSkill")
    assert bundle["class"] is bundle["module"].PeriodCloseSkill
    assert bundle["instructions"].strip()
    assert bundle["card"]["issuer"]["email"] == bundle["manifest"]["issuer"]["email"]


def test_actions_are_exactly_the_published_set(
    skill, manifest, close_params, validate_params
):
    assert ACTIONS == ("validate", "close")
    assert manifest["parameters"]["properties"]["action"]["enum"] == list(ACTIONS)
    assert _assert_completed(_run(skill, close_params))["closed"] is True
    assert _assert_completed(_run(skill, validate_params))["blocking"] is False


@pytest.mark.parametrize("action", ["bogus", "", None, 3, ["close"], "Close"])
def test_unknown_action_is_a_contract_error(skill, close_params, action):
    params = dict(close_params)
    if action is None:
        del params["action"]
    else:
        params["action"] = action
    detail = _assert_error(skill.execute(params), INVALID_REQUEST, "unknown_action")
    assert detail["path"] == "action"


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------


@pytest.mark.parametrize("params_name,expected_name", REFERENCE_FIXTURES)
def test_reference_fixtures_repeat_byte_identically(skill, params_name, expected_name):
    params = _load_json(FIXTURES_DIR, params_name)
    expected = _load_json(FIXTURES_DIR, expected_name)
    first = _run(skill, params)
    second = _run(skill, params)
    assert first == expected
    assert _canonical(first) == _canonical(second) == _canonical(expected)


HASH_SEED_SCRIPT = """
import hashlib
import json
import sys

from skill_assay.core.loader import SkillLoader

bundle = SkillLoader.load_skill("finance/period_close")
skill = bundle["class"]()
digests = []
for path in json.loads(sys.argv[1]):
    with open(path, "r", encoding="utf-8") as handle:
        params = json.load(handle)
    text = json.dumps(
        skill.execute(params), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    digests.append(hashlib.sha256(text.encode("utf-8")).hexdigest())
print(json.dumps(digests))
"""


def test_output_is_identical_across_hash_seeds():
    paths = [os.path.join(FIXTURES_DIR, name) for name, _ in REFERENCE_FIXTURES]
    outputs = []
    for seed in ("0", "4242"):
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = seed
        env["PYTHONPATH"] = REPO_ROOT
        proc = subprocess.run(
            [sys.executable, "-c", HASH_SEED_SCRIPT, json.dumps(paths)],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        outputs.append(json.loads(proc.stdout))
    assert outputs[0] == outputs[1]
    expected = []
    for _, expected_name in REFERENCE_FIXTURES:
        result = _load_json(FIXTURES_DIR, expected_name)
        expected.append(hashlib.sha256(_canonical(result).encode("utf-8")).hexdigest())
    assert outputs[0] == expected


# --------------------------------------------------------------------------
# Source checks (offline, stateless, standard library only, no float)
# --------------------------------------------------------------------------

ALLOWED_IMPORTS = {"re"}
ALLOWED_FROM_IMPORTS = {
    "collections": {"defaultdict"},
    "datetime": {"date"},
    "decimal": {"Context", "Decimal", "ROUND_HALF_UP"},
    "typing": None,
    "skill_assay.core.base_skill": {"BaseSkill"},
}
FORBIDDEN_ATTRIBUTES = {
    "environ",
    "getenv",
    "credential",
    "now",
    "utcnow",
    "today",
    "write",
    "write_text",
    "write_bytes",
    "writelines",
    "remove",
    "unlink",
    "mkdir",
    "makedirs",
    "rmdir",
    "rename",
    "truncate",
    "getcontext",
    "setcontext",
    "localcontext",
}
FORBIDDEN_NAMES = {
    "hash",
    "eval",
    "exec",
    "compile",
    "__import__",
    "input",
    "float",
    "getcontext",
    "setcontext",
    "localcontext",
}


def _skill_source():
    with open(os.path.join(BUNDLE_DIR, "skill.py"), "r", encoding="utf-8") as handle:
        return handle.read()


def _disallowed_imports(source):
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name not in ALLOWED_IMPORTS:
                    found.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level or module not in ALLOWED_FROM_IMPORTS:
                found.append(module)
                continue
            allowed_names = ALLOWED_FROM_IMPORTS[module]
            for alias in node.names:
                if allowed_names is not None and alias.name not in allowed_names:
                    found.append(f"{module}.{alias.name}")
    return found


def test_source_imports_only_the_allowed_modules():
    assert _disallowed_imports(_skill_source()) == []
    # The allow-list proves itself: this bundle reads no data file, so even
    # yaml is rejected, as are json, math, statistics and fractions.
    assert _disallowed_imports("import yaml\n") == ["yaml"]
    for module in ("json", "math", "statistics", "fractions", "os", "sys"):
        assert _disallowed_imports(f"import {module}\n") == [module]
    assert _disallowed_imports("from statistics import median\n") == ["statistics"]
    assert _disallowed_imports("from decimal import getcontext\n") == [
        "decimal.getcontext"
    ]
    assert _disallowed_imports("from decimal import localcontext\n") == [
        "decimal.localcontext"
    ]
    for module in ("socket", "http", "requests", "ssl", "ftplib", "subprocess"):
        assert _disallowed_imports(f"import {module}\n") == [module]
    assert _disallowed_imports("from . import helper\n") == [""]


def test_source_has_no_network_clock_environment_or_write_access():
    tree = ast.parse(_skill_source())
    open_calls = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            assert node.attr not in FORBIDDEN_ATTRIBUTES, node.attr
        if isinstance(node, ast.Name):
            assert node.id not in FORBIDDEN_NAMES, node.id
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "open":
                open_calls += 1
    # The bundle has no kb/ and the skill reads no file, not even its own
    # manifest: the count of open calls is zero.
    assert open_calls == 0
    for name in ("socket", "http", "urllib", "requests", "ssl", "os", "yaml"):
        assert not hasattr(skill_module, name)


def test_source_has_no_float():
    source = _skill_source()
    assert "float(" not in source
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant):
            assert not isinstance(node.value, float), node.value
        if isinstance(node, ast.Name):
            assert node.id != "float"


def _walk_values(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_values(item)
    else:
        yield value


def test_no_float_in_any_output(skill):
    for params_name, expected_name in REFERENCE_FIXTURES:
        for payload in (
            _load_json(FIXTURES_DIR, expected_name),
            _run(skill, _load_json(FIXTURES_DIR, params_name)),
        ):
            for value in _walk_values(payload):
                assert not isinstance(value, float), (expected_name, value)


# --------------------------------------------------------------------------
# Error registry, per-code cases, documented order
# --------------------------------------------------------------------------


def test_error_registry_is_closed_and_final():
    assert ERROR_REGISTRY == frozenset(
        {
            "INVALID_REQUEST",
            "MISSING_AS_OF",
            "INVALID_PERIOD",
            "CLOSE_BEFORE_PERIOD_END",
            "EMPTY_JOURNAL",
            "INVALID_AMOUNT",
            "UNKNOWN_ACCOUNT_CLASS",
            "UNKNOWN_SUBCLASS",
            "CURRENCY_MISMATCH",
            "INVALID_DATE",
            "CLOSE_BLOCKED",
        }
    )
    assert len(ERROR_REGISTRY) == 11
    assert FAULT_REGISTRY == frozenset({"INTERNAL_ERROR"})
    assert INTERNAL_ERROR not in ERROR_REGISTRY
    assert "UNKNOWN_ACTION" not in ERROR_REGISTRY
    assert {code for code, _ in CHECKS} == ERROR_REGISTRY
    assert len(CHECKS) == 33
    assert VALIDATE_CHECKS == CHECKS[:31]
    assert CLOSE_CHECKS == CHECKS
    assert CLOSE_BEFORE_PERIOD_END not in {code for code, _ in VALIDATE_CHECKS}
    assert CLOSE_BLOCKED not in {code for code, _ in VALIDATE_CHECKS}


def _with_chart_row(f, index, **changes):
    params = _params(f)
    row = params["chart_of_accounts"][index]
    for key, value in changes.items():
        if value is None:
            row.pop(key, None)
        else:
            row[key] = value
    return params


ERROR_CASES = {
    INVALID_REQUEST: lambda f: _params(f, bogus=1),
    MISSING_AS_OF: lambda f: {k: v for k, v in _params(f).items() if k != "as_of"},
    INVALID_PERIOD: lambda f: _params(
        f, period={"start": "2026-07-01", "end": "2026-06-30"}
    ),
    CLOSE_BEFORE_PERIOD_END: lambda f: _params(f, as_of="2026-06-29"),
    EMPTY_JOURNAL: lambda f: _params(f, journal=[]),
    INVALID_AMOUNT: lambda f: _params(
        f,
        journal=[
            _entry(
                "j1",
                "2026-04-03",
                [_line("cash", "9800.000", "0"), _line("sales", "0", "9800.00")],
                "demo_customer",
            )
        ],
    ),
    UNKNOWN_ACCOUNT_CLASS: lambda f: _with_chart_row(f, 0, **{"class": "bogus"}),
    UNKNOWN_SUBCLASS: lambda f: _with_chart_row(f, 1, subclass="cash"),
    CURRENCY_MISMATCH: lambda f: _params(
        f, journal=[{**f["close"]["journal"][0], "currency": "EUR"}]
    ),
    INVALID_DATE: lambda f: _params(f, as_of="2026-02-30"),
    CLOSE_BLOCKED: lambda f: copy.deepcopy(f["blocked"]),
}


def test_error_cases_cover_registry():
    assert set(ERROR_CASES) == ERROR_REGISTRY


@pytest.mark.parametrize("code", sorted(ERROR_CASES))
def test_each_error_code(skill, error_inputs, code):
    _assert_error(skill.execute(ERROR_CASES[code](error_inputs)), code)


def _unreachable(reason):
    return reason


def _many_rows(count):
    return [{"account": f"a{i}", "class": "asset"} for i in range(count)]


def _prior(f, **changes):
    """The example's first prior period, edited."""
    prior = copy.deepcopy(f["close"]["prior_journals"][0])
    prior.update(changes)
    return prior


def _prior_entry(f, **changes):
    entry = copy.deepcopy(f["close"]["prior_journals"][0]["journal"][0])
    entry.update(changes)
    return entry


def _bad_line_entry(f, **changes):
    entry = copy.deepcopy(f["close"]["journal"][0])
    entry["lines"][0]["debit"] = "x"
    entry.update(changes)
    return entry


# An exercised pair is (input builder, reason of check i) or, where check i
# has a fixed path, (input builder, reason, path); a string records why no
# single input can violate both checks.
PAIRS = {
    (1, 2): _unreachable("check 2 needs an object, which check 1 rejects"),
    (2, 3): (lambda f: _params(f, action="bogus", bogus=1), "unknown_action", "action"),
    (3, 4): (
        lambda f: {k: v for k, v in _params(f, bogus=1).items() if k != "as_of"},
        "unknown_key",
    ),
    (4, 5): _unreachable(
        "check 5 needs a present `as_of`, which check 4 requires absent or null"
    ),
    (5, 6): (
        lambda f: {
            k: v for k, v in _params(f, as_of="2026-13-01").items() if k != "period"
        },
        "invalid_date",
        "as_of",
    ),
    (6, 7): (
        lambda f: {
            k: v
            for k, v in _params(f, period={"start": "2026-04-01"}).items()
            if k != "currency"
        },
        "missing_field",
        "currency",
    ),
    (7, 8): (
        lambda f: _params(f, period={"start": "bad"}),
        "missing_field",
        "period.end",
    ),
    (8, 9): _unreachable(
        "check 9 compares two parsed dates, and check 8 fails when either is malformed"
    ),
    (9, 10): (
        lambda f: _params(
            f, period={"start": "2026-07-01", "end": "2026-06-30"}, currency="usd"
        ),
        "start_after_end",
        "period",
    ),
    (10, 11): (
        lambda f: _params(f, currency="usd", options={"bogus": 1}),
        "currency_pattern",
        "currency",
    ),
    (11, 12): (
        lambda f: _params(
            f,
            options={"soft_close": "no"},
            chart_of_accounts=_many_rows(MAX_CHART_ROWS + 1),
        ),
        "invalid_type",
        "options.soft_close",
    ),
    (12, 13): (
        lambda f: _params(
            f, chart_of_accounts=["not an object"] + _many_rows(MAX_CHART_ROWS)
        ),
        "size_cap",
        "chart_of_accounts",
    ),
    (13, 14): (
        lambda f: _params(f, chart_of_accounts=[{"account": "cash", "bogus": 1}]),
        "unknown_key",
        "chart_of_accounts[0]",
    ),
    (14, 15): (
        lambda f: _params(
            f,
            chart_of_accounts=[
                {"account": "cash", "class": "bogus", "subclass": "cash"}
            ],
        ),
        "unknown_class",
        "chart_of_accounts[0].class",
    ),
    (15, 16): (
        lambda f: {
            **_with_chart_row(f, 1, subclass="cash"),
            "opening_balances": ["not an object"],
        },
        "unknown_subclass",
        "chart_of_accounts[1].subclass",
    ),
    (16, 17): (
        lambda f: _params(
            f, opening_balances=[{**_line("cash", "x", "0"), "bogus": 1}]
        ),
        "unknown_key",
        "opening_balances[0]",
    ),
    (17, 18): (
        lambda f: _params(f, opening_balances=[_line("cash", "x", "0")], journal=[]),
        "invalid_amount",
        "opening_balances[0].debit",
    ),
    (18, 19): _unreachable(
        "check 19 needs an entry, and check 18 fails only on an empty journal"
    ),
    (19, 20): (
        lambda f: _params(
            f, journal=[{**f["close"]["journal"][0], "date": "bad", "bogus": 1}]
        ),
        "unknown_key",
        "journal[0]",
    ),
    (20, 21): (
        lambda f: _params(
            f, journal=[{**f["close"]["journal"][0], "date": "bad", "currency": "EUR"}]
        ),
        "invalid_date",
        "journal[0].date",
    ),
    (21, 22): (
        lambda f: _params(f, journal=[_bad_line_entry(f, currency="EUR")]),
        "currency_mismatch",
        "journal[0].currency",
    ),
    (22, 23): (
        lambda f: _params(
            f, journal=[_bad_line_entry(f)], prior_journals=["not an object"]
        ),
        "invalid_amount",
        "journal[0].lines[0].debit",
    ),
    (23, 24): (
        lambda f: _params(
            f,
            prior_journals=[
                _prior(f, period={"start": "bad", "end": "2025-09-30"}, bogus=1)
            ],
        ),
        "unknown_key",
        "prior_journals[0]",
    ),
    (24, 25): _unreachable(
        "check 25 compares parsed dates, and check 24 fails when either is malformed"
    ),
    (25, 26): (
        lambda f: _params(
            f,
            prior_journals=[
                _prior(
                    f,
                    period={"start": "2025-09-30", "end": "2025-07-01"},
                    currency="EUR",
                )
            ],
        ),
        "start_after_end",
        "prior_journals[0].period",
    ),
    (26, 27): (
        lambda f: _params(
            f,
            prior_journals=[
                _prior(f, currency="EUR", journal=[_prior_entry(f, bogus=1)])
            ],
        ),
        "currency_mismatch",
        "prior_journals[0].currency",
    ),
    (27, 28): (
        lambda f: _params(
            f,
            prior_journals=[_prior(f, journal=[_prior_entry(f, date="bad", bogus=1)])],
        ),
        "unknown_key",
        "prior_journals[0].journal[0]",
    ),
    (28, 29): _unreachable(
        "check 29 compares a parsed date, and check 28 fails when it is malformed"
    ),
    (29, 30): (
        lambda f: _params(
            f,
            prior_journals=[
                _prior(f, journal=[_prior_entry(f, date="2025-01-01", currency="EUR")])
            ],
        ),
        "entry_outside_period",
        "prior_journals[0].journal[0].date",
    ),
    (30, 31): (
        lambda f: _params(
            f,
            prior_journals=[
                _prior(
                    f,
                    journal=[
                        {
                            **_prior_entry(f, currency="EUR"),
                            "lines": [_line("rent", "x", "0"), _line("cash", "0", "x")],
                        }
                    ],
                )
            ],
        ),
        "currency_mismatch",
        "prior_journals[0].journal[0].currency",
    ),
    (31, 32): (
        lambda f: _params(
            f,
            as_of="2026-06-29",
            prior_journals=[
                _prior(
                    f,
                    journal=[
                        {
                            **_prior_entry(f),
                            "lines": [_line("rent", "x", "0"), _line("cash", "0", "x")],
                        }
                    ],
                )
            ],
        ),
        "invalid_amount",
        "prior_journals[0].journal[0].lines[0].debit",
    ),
    (32, 33): (
        lambda f: {**copy.deepcopy(f["blocked"]), "as_of": "2026-06-29"},
        "close_before_period_end",
        "as_of",
    ),
}


def _pair_cases():
    cases = []
    for (first, second), entry in sorted(PAIRS.items()):
        actions = ["close"] if second > len(VALIDATE_CHECKS) else ["validate", "close"]
        for action in actions:
            cases.append(
                pytest.param(
                    action, first, second, entry, id=f"{action}-{first}-{second}"
                )
            )
    return cases


def test_pairwise_table_covers_every_adjacent_pair():
    expected = {(i, i + 1) for i in range(1, len(CLOSE_CHECKS))}
    assert set(PAIRS) == expected
    for entry in PAIRS.values():
        if isinstance(entry, str):
            assert entry
            continue
        builder, reason, *path = entry
        assert callable(builder)
        assert isinstance(reason, str) and reason
        assert len(path) <= 1
        assert all(isinstance(item, str) and item for item in path)


@pytest.mark.parametrize("action,first,second,entry", _pair_cases())
def test_first_violation_wins_in_documented_order(
    skill, error_inputs, action, first, second, entry
):
    checks = CLOSE_CHECKS if action == "close" else VALIDATE_CHECKS
    if isinstance(entry, str):
        assert entry  # recorded reason: no single input violates both checks
        return
    builder, reason, *path = entry
    params = builder(error_inputs)
    if "action" in params and params["action"] in ACTIONS:
        params["action"] = action
    detail = _assert_error(skill.execute(params), checks[first - 1][0], reason)
    if path:
        assert detail["path"] == path[0]


def test_internal_fault_returns_an_error_envelope(skill, monkeypatch, close_params):
    def boom(request, books, findings):
        raise RuntimeError("synthetic fault")

    monkeypatch.setattr(skill_module, "_close", boom)
    result = skill.execute(close_params)
    assert result == {
        "status": "error",
        "error": {
            "code": INTERNAL_ERROR,
            "detail": {"reason": "internal_error", "exception": "RuntimeError"},
        },
    }

    def interrupt(request, findings):
        raise KeyboardInterrupt()

    monkeypatch.setattr(skill_module, "_validate", interrupt)
    with pytest.raises(KeyboardInterrupt):
        skill.execute({**close_params, "action": "validate"})


def test_error_envelope_and_completed_run_never_mix(
    skill, close_params, validate_params, blocked_params
):
    for params in (close_params, validate_params):
        _assert_completed(_run(skill, params))
    for params in (blocked_params, {**close_params, "currency": "usd"}):
        errored = _run(skill, params)
        assert set(errored) == {"status", "error"}
        assert errored["error"]["code"] in ERROR_REGISTRY


@pytest.mark.parametrize(
    "mutate,code,reason",
    [
        (lambda p: p.update(as_of=20260705), INVALID_DATE, "invalid_date"),
        (lambda p: p.update(as_of=None), MISSING_AS_OF, "missing_as_of"),
        (lambda p: p.update(currency=840), INVALID_REQUEST, "invalid_type"),
        (
            lambda p: p.update(options={"soft_close": 1}),
            INVALID_REQUEST,
            "invalid_type",
        ),
        (
            lambda p: p.update(options={"recurrence_min_periods": True}),
            INVALID_REQUEST,
            "invalid_type",
        ),
        (
            lambda p: p.update(options={"recurrence_min_periods": "2"}),
            INVALID_REQUEST,
            "invalid_type",
        ),
        (
            lambda p: p.update(options={"deviation_threshold": 0.2}),
            INVALID_REQUEST,
            "invalid_type",
        ),
        (
            lambda p: p["journal"][0]["lines"][0].update(debit=9800),
            INVALID_AMOUNT,
            "invalid_amount",
        ),
        (
            lambda p: p["opening_balances"][0].update(credit=0),
            INVALID_AMOUNT,
            "invalid_amount",
        ),
        (lambda p: p["journal"][0].update(id=1), INVALID_REQUEST, "invalid_type"),
        (lambda p: p["journal"][0].update(date=None), INVALID_DATE, "invalid_date"),
    ],
)
def test_type_rules_are_strict(skill, close_params, mutate, code, reason):
    params = copy.deepcopy(close_params)
    mutate(params)
    _assert_error(skill.execute(params), code, reason)


@pytest.mark.parametrize(
    "value,reason",
    [
        ("0", "out_of_range"),
        ("0.0000", "out_of_range"),
        ("-0.2", "threshold_pattern"),
        ("0.00001", "threshold_pattern"),
        ("1e2", "threshold_pattern"),
        ("1", None),
        ("0.0001", None),
        ("999999999999999.9999", None),
        ("1000000000000000", "threshold_pattern"),
    ],
)
def test_deviation_threshold_grammar(skill, close_params, value, reason):
    params = {**close_params, "options": {"deviation_threshold": value}}
    result = skill.execute(params)
    if reason is None:
        _assert_completed(result)
        return
    detail = _assert_error(result, INVALID_REQUEST, reason)
    assert detail["path"] == "options.deviation_threshold"


@pytest.mark.parametrize("value,ok", [(0, False), (1, True), (24, True), (25, False)])
def test_recurrence_min_periods_range(skill, close_params, value, ok):
    params = {**close_params, "options": {"recurrence_min_periods": value}}
    result = skill.execute(params)
    if ok:
        _assert_completed(result)
    else:
        detail = _assert_error(result, INVALID_REQUEST, "out_of_range")
        assert detail["path"] == "options.recurrence_min_periods"


LINE_FEED_CASES = {
    "as_of": lambda f: (_params(f, as_of="2026-07-05\n"), INVALID_DATE, "invalid_date"),
    "journal[].lines[].debit": lambda f: (
        _params(
            f,
            journal=[
                _entry(
                    "j1",
                    "2026-04-03",
                    [_line("cash", "9800.00\n", "0"), _line("sales", "0", "9800.00")],
                    "demo_customer",
                )
            ],
        ),
        INVALID_AMOUNT,
        "invalid_amount",
    ),
    "journal[].id": lambda f: (
        _params(f, journal=[{**f["close"]["journal"][0], "id": "j1\n"}]),
        INVALID_REQUEST,
        "id_pattern",
    ),
    "chart_of_accounts[].account": lambda f: (
        _with_chart_row(f, 0, account="cash\n"),
        INVALID_REQUEST,
        "account_pattern",
    ),
    "journal[].lines[].account": lambda f: (
        _params(
            f,
            journal=[
                _entry(
                    "j1",
                    "2026-04-03",
                    [_line("cash\n", "9800.00", "0"), _line("sales", "0", "9800.00")],
                    "demo_customer",
                )
            ],
        ),
        INVALID_REQUEST,
        "account_pattern",
    ),
    "journal[].counterparty": lambda f: (
        _params(
            f, journal=[{**f["close"]["journal"][0], "counterparty": "demo_customer\n"}]
        ),
        INVALID_REQUEST,
        "counterparty_pattern",
    ),
    "currency": lambda f: (
        _params(f, currency="USD\n"),
        INVALID_REQUEST,
        "currency_pattern",
    ),
    "options.deviation_threshold": lambda f: (
        _params(f, options={"deviation_threshold": "0.20\n"}),
        INVALID_REQUEST,
        "threshold_pattern",
    ),
}


@pytest.mark.parametrize("field", sorted(LINE_FEED_CASES))
def test_patterns_reject_a_trailing_line_feed(skill, error_inputs, field):
    params, code, reason = LINE_FEED_CASES[field](error_inputs)
    _assert_error(skill.execute(params), code, reason)


def _huge():
    return 10**5000


CRAFTED_CASES = {
    "recurrence-5000-digit-integer": lambda p: p.update(
        options={"recurrence_min_periods": _huge()}
    ),
    "recurrence-negative-huge-integer": lambda p: p.update(
        options={"recurrence_min_periods": -_huge()}
    ),
    "recurrence-boolean": lambda p: p.update(options={"recurrence_min_periods": False}),
    "amount-json-number": lambda p: p["journal"][0]["lines"][0].update(debit=9800),
    "amount-exponent": lambda p: p["journal"][0]["lines"][0].update(debit="9.8E3"),
    "amount-third-decimal": lambda p: p["journal"][0]["lines"][0].update(
        debit="9800.000"
    ),
    "amount-sign": lambda p: p["journal"][0]["lines"][0].update(debit="-9800.00"),
    "amount-huge-string": lambda p: p["journal"][0]["lines"][0].update(
        debit="9" * 5000
    ),
    "surrogate-id": lambda p: p["journal"][0].update(id="j1" + LONE_SURROGATE),
    "surrogate-chart-account": lambda p: p["chart_of_accounts"][0].update(
        account="cash" + LONE_SURROGATE
    ),
    "surrogate-line-account": lambda p: p["journal"][0]["lines"][0].update(
        account="cash" + LONE_SURROGATE
    ),
    "surrogate-counterparty": lambda p: p["journal"][0].update(
        counterparty="demo" + LONE_SURROGATE
    ),
    "surrogate-currency": lambda p: p.update(currency="US" + LONE_SURROGATE),
    "surrogate-as-of": lambda p: p.update(as_of="2026-07-0" + LONE_SURROGATE),
    "surrogate-amount": lambda p: p["journal"][0]["lines"][0].update(
        debit="1" + LONE_SURROGATE
    ),
    "surrogate-threshold": lambda p: p.update(
        options={"deviation_threshold": "0.2" + LONE_SURROGATE}
    ),
    "list-for-period": lambda p: p.update(period=[]),
    "list-for-options": lambda p: p.update(options=[]),
    "list-for-chart-row": lambda p: p["chart_of_accounts"].__setitem__(0, []),
    "list-for-opening-line": lambda p: p["opening_balances"].__setitem__(0, []),
    "list-for-entry": lambda p: p["journal"].__setitem__(0, []),
    "list-for-line": lambda p: p["journal"][0]["lines"].__setitem__(0, []),
    "list-for-prior-period": lambda p: p["prior_journals"].__setitem__(0, []),
    "list-for-prior-period-dates": lambda p: p["prior_journals"][0].update(period=[]),
    "list-for-prior-entry": lambda p: p["prior_journals"][0]["journal"].__setitem__(
        0, []
    ),
    "object-for-chart": lambda p: p.update(chart_of_accounts={}),
    "object-for-opening": lambda p: p.update(opening_balances={}),
    "object-for-journal": lambda p: p.update(journal={}),
    "object-for-prior-journals": lambda p: p.update(prior_journals={}),
    "object-for-lines": lambda p: p["journal"][0].update(lines={}),
    "object-for-prior-journal": lambda p: p["prior_journals"][0].update(journal={}),
    "object-for-prior-lines": lambda p: p["prior_journals"][0]["journal"][0].update(
        lines={}
    ),
    "null-period": lambda p: p.update(period=None),
    "null-journal": lambda p: p.update(journal=None),
    "null-lines": lambda p: p["journal"][0].update(lines=None),
    "null-subclass": lambda p: p["chart_of_accounts"][0].update(subclass=None),
    "null-counterparty": lambda p: p["journal"][0].update(counterparty=None),
    "null-options": lambda p: p.update(options=None),
    "nul-in-id": lambda p: p["journal"][0].update(id="j\x001"),
    "control-character-date": lambda p: p.update(as_of="2026-07-05\x00"),
}


@pytest.mark.parametrize("case", sorted(CRAFTED_CASES))
def test_crafted_inputs_never_reach_internal_error(skill, close_params, case):
    params = copy.deepcopy(close_params)
    CRAFTED_CASES[case](params)
    result = skill.execute(params)
    assert result["status"] == "error", result
    assert result["error"]["code"] in ERROR_REGISTRY, result
    assert result["error"]["code"] != INTERNAL_ERROR
    _assert_error(result, result["error"]["code"])
    text = json.dumps(result, ensure_ascii=False)
    assert LONE_SURROGATE not in text
    assert "9" * 100 not in text


def test_error_details_never_echo_input_text(skill, close_params):
    marker = "ZQ MARKER"
    cases = [
        {**close_params, "zq_marker_key": 1},
        {**close_params, "currency": marker},
        {**close_params, "options": {"zq_marker_key": 1}},
        {**close_params, "options": {"deviation_threshold": marker}},
        {
            **close_params,
            "chart_of_accounts": [{"account": marker, "class": "asset"}],
        },
        {
            **close_params,
            "chart_of_accounts": [{"account": "cash", "class": marker}],
        },
        {
            **close_params,
            "chart_of_accounts": [
                {"account": "cash", "class": "asset", "subclass": marker}
            ],
        },
        {
            **close_params,
            "chart_of_accounts": [
                {"account": "cash", "class": "asset", "zq_marker_key": 1}
            ],
        },
        {**close_params, "journal": [{**close_params["journal"][0], "id": marker}]},
        {**close_params, "journal": [{**close_params["journal"][0], "date": marker}]},
        {
            **close_params,
            "journal": [{**close_params["journal"][0], "currency": marker}],
        },
        {
            **close_params,
            "journal": [{**close_params["journal"][0], "counterparty": marker}],
        },
        {
            **close_params,
            "journal": [
                _entry(
                    "j1",
                    "2026-04-03",
                    [_line(marker, "1.00", "0"), _line("sales", "0", "1.00")],
                )
            ],
        },
        {
            **close_params,
            "journal": [
                _entry(
                    "j1",
                    "2026-04-03",
                    [_line("cash", marker, "0"), _line("sales", "0", "1.00")],
                )
            ],
        },
    ]
    for params in cases:
        result = skill.execute(copy.deepcopy(params))
        assert result["status"] == "error", params
        assert result["error"]["code"] in ERROR_REGISTRY
        assert marker not in json.dumps(result)
        assert "zq_marker_key" not in json.dumps(result)


def _in_both_orders(build, items):
    """``build`` applied to ``items`` in the given order and in reverse."""
    return build(list(items)), build(list(reversed(items)))


def test_error_details_do_not_depend_on_key_order(skill, close_params):
    def request(extra):
        return skill.execute({**close_params, **dict(extra)})

    def chart_row(cells):
        return skill.execute({**close_params, "chart_of_accounts": [dict(cells)]})

    def options(cells):
        return skill.execute({**close_params, "options": dict(cells)})

    first, second = _in_both_orders(request, [("zz_unknown", 1), ("aa_unknown", 2)])
    assert first == second
    assert _assert_error(first, INVALID_REQUEST, "unknown_key") == {
        "reason": "unknown_key"
    }
    first, second = _in_both_orders(
        chart_row, [("account", "cash"), ("zz_unknown", 1), ("subclass", "bogus")]
    )
    assert first == second
    assert _assert_error(first, INVALID_REQUEST, "unknown_key")["path"] == (
        "chart_of_accounts[0]"
    )
    first, second = _in_both_orders(
        options, [("recurrence_min_periods", "2"), ("soft_close", "no")]
    )
    assert first == second
    assert _assert_error(first, INVALID_REQUEST, "invalid_type")["path"] == (
        "options.soft_close"
    )
    first, second = _in_both_orders(
        request,
        [("journal", [_entry("j1", "bad", [])]), ("currency", "usd")],
    )
    assert first == second
    assert _assert_error(first, INVALID_REQUEST, "currency_pattern")["path"] == (
        "currency"
    )


# --------------------------------------------------------------------------
# Size caps
# --------------------------------------------------------------------------


def _entries(count, lines=2):
    return [
        _entry(
            f"e{i}",
            "2026-04-03",
            [_line("cash", "1.00", "0"), _line("sales", "0", "1.00")] * (lines // 2),
        )
        for i in range(count)
    ]


def _prior_with(f, entries):
    return _prior(f, journal=entries)


def _total_lines_case(f):
    journal = _entries(MAX_TOTAL_LINES // MAX_LINES_PER_ENTRY, MAX_LINES_PER_ENTRY)
    prior = _prior_with(f, [_entry("p1", "2025-07-31", [_line("rent", "1.00", "0")])])
    return _params(f, journal=journal, prior_journals=[prior])


SIZE_CAP_CASES = {
    ("chart_of_accounts", "entries"): lambda f: (
        _params(f, chart_of_accounts=_many_rows(MAX_CHART_ROWS + 1)),
        "chart_of_accounts",
        MAX_CHART_ROWS,
    ),
    ("opening_balances", "entries"): lambda f: (
        _params(
            f,
            opening_balances=[
                _line(f"a{i}", "1.00", "0") for i in range(MAX_OPENING_LINES + 1)
            ],
        ),
        "opening_balances",
        MAX_OPENING_LINES,
    ),
    ("journal", "entries"): lambda f: (
        _params(f, journal=_entries(MAX_JOURNAL_ENTRIES + 1)),
        "journal",
        MAX_JOURNAL_ENTRIES,
    ),
    ("journal[].lines", "entries"): lambda f: (
        _params(f, journal=_entries(1, MAX_LINES_PER_ENTRY + 2)),
        "journal[0].lines",
        MAX_LINES_PER_ENTRY,
    ),
    ("prior_journals", "entries"): lambda f: (
        _params(f, prior_journals=[_prior_with(f, [])] * (MAX_PRIOR_PERIODS + 1)),
        "prior_journals",
        MAX_PRIOR_PERIODS,
    ),
    ("prior_journals[].journal", "entries"): lambda f: (
        _params(
            f,
            prior_journals=[_prior_with(f, _entries(MAX_PRIOR_ENTRIES_PER_PERIOD + 1))],
        ),
        "prior_journals[0].journal",
        MAX_PRIOR_ENTRIES_PER_PERIOD,
    ),
    ("prior_journals[].journal[].lines", "entries"): lambda f: (
        _params(
            f,
            prior_journals=[_prior_with(f, _entries(1, MAX_LINES_PER_ENTRY + 2))],
        ),
        "prior_journals[0].journal[0].lines",
        MAX_LINES_PER_ENTRY,
    ),
    ("lines (journal and prior journals together)", "entries"): lambda f: (
        _total_lines_case(f),
        None,
        MAX_TOTAL_LINES,
    ),
}


def test_every_cap_has_a_case():
    assert set(SIZE_CAP_CASES) == {(field, unit) for field, unit, _, _ in CAP_TABLE}
    assert len(CAP_TABLE) == len({(field, unit) for field, unit, _, _ in CAP_TABLE})
    assert {code for _, _, _, code in CAP_TABLE} == {INVALID_REQUEST}


@pytest.mark.parametrize(
    "field,unit",
    sorted(SIZE_CAP_CASES),
    ids=[f"{f}-{u}" for f, u in sorted(SIZE_CAP_CASES)],
)
def test_size_caps(skill, error_inputs, field, unit):
    params, path, limit = SIZE_CAP_CASES[(field, unit)](error_inputs)
    detail = _assert_error(skill.execute(params), INVALID_REQUEST, "size_cap")
    assert detail["limit"] == limit
    assert detail["actual"] > limit
    if path is None:
        assert "path" not in detail
    else:
        assert detail["path"] == path


def _parse_cap_table(text):
    rows = []
    for line in _section(text, "## Size caps").splitlines():
        if not line.startswith("| `") or line.startswith("| Field"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        field = cells[0].strip("`")
        unit = cells[1]
        limit = int(cells[2]) if cells[2].isdigit() else cells[2].strip("`")
        code = cells[3].strip("`")
        rows.append((field, unit, limit, code))
    return rows


def test_cap_table_matches_instructions():
    assert _parse_cap_table(_instructions()) == list(CAP_TABLE)


def _deadline(seconds):
    """Interrupt a runaway call so that a failing test cannot exhaust time."""

    def interrupt(signum, frame):
        raise TimeoutError(f"the call did not return within {seconds} seconds")

    previous = signal.signal(signal.SIGALRM, interrupt)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def test_line_cap_is_checked_before_lines_are_parsed(skill, error_inputs):
    params = _total_lines_case(error_inputs)
    # Every line carries an amount that would fail the grammar if parsed; the
    # cap is reported first, from a count alone.
    for entry in params["journal"]:
        for line in entry["lines"]:
            line["debit"] = "x"
    started = time.perf_counter()
    guard = _deadline(10)
    next(guard)
    try:
        result = skill.execute(params)
    finally:
        with pytest.raises(StopIteration):
            next(guard)
    elapsed = time.perf_counter() - started
    detail = _assert_error(result, INVALID_REQUEST, "size_cap")
    assert detail["limit"] == MAX_TOTAL_LINES
    assert detail["actual"] == MAX_TOTAL_LINES + 1
    assert elapsed < 2.0


# --------------------------------------------------------------------------
# Check lists, class sets and the findings registry against instructions.md
# --------------------------------------------------------------------------


def _parse_check_list(text, heading, start):
    checks = []
    for line in _section(text, heading).splitlines():
        match = re.match(r"^(\d+)\. `([A-Z_]+)` — (.*)$", line)
        if match:
            assert int(match.group(1)) == start + len(checks)
            checks.append((match.group(2), match.group(3)))
    return checks


def test_check_lists_match_instructions():
    text = _instructions()
    both = _parse_check_list(text, "### Both actions", 1)
    close_only = _parse_check_list(text, "### close only", len(both) + 1)
    assert both == list(VALIDATE_CHECKS)
    assert both + close_only == list(CLOSE_CHECKS)
    assert len(both) == 31 and len(close_only) == 2


def _parse_class_table(text):
    rows = {}
    for line in _section(text, "## Account classes and subclasses").splitlines():
        if not line.startswith("| `"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        cls = cells[0].strip("`")
        subclasses = tuple(re.findall(r"`([a-z_]+)`", cells[1]))
        default = re.findall(r"`([a-z_]+)`", cells[2])
        rows[cls] = (subclasses, default[0] if default else None)
    return rows


def test_class_sets_match_instructions():
    rows = _parse_class_table(_instructions())
    assert tuple(rows) == CLASSES
    for cls in CLASSES:
        assert rows[cls] == (SUBCLASSES[cls], DEFAULT_SUBCLASS[cls]), cls
    assert CLASSES == ("asset", "liability", "equity", "revenue", "expense")
    assert SUBCLASSES["equity"] == ()
    assert DEFAULT_SUBCLASS["asset"] == DEFAULT_SUBCLASS["liability"] == "other_current"
    assert DEFAULT_SUBCLASS["revenue"] == DEFAULT_SUBCLASS["expense"] == "operating"


def test_findings_registry_is_closed():
    assert FINDING_CODES == (
        "UNBALANCED_ENTRY",
        "UNMAPPED_ACCOUNT",
        "ENTRY_OUT_OF_PERIOD",
        "ENTRY_AFTER_AS_OF",
        "DUPLICATE_ENTRY_ID",
        "OPENING_UNBALANCED",
        "SUSPECTED_DUPLICATE",
        "ZERO_LINE",
        "NEGATIVE_CASH",
        "EXPECTED_MISSING",
        "AMOUNT_DEVIATION",
        "UNUSED_ACCOUNT",
    )
    assert len(FINDINGS_REGISTRY) == 12
    assert not set(FINDING_CODES) & ERROR_REGISTRY
    text = _instructions()
    table = _section(text, "## Findings registry")
    listed = re.findall(r"^\| `([A-Z_]+)` \| (blocking|warning|info) \|", table, re.M)
    assert listed == list(FINDINGS_REGISTRY)


def test_severities_are_six_five_one():
    severities = [severity for _, severity in FINDINGS_REGISTRY]
    assert severities.count(SEVERITY_BLOCKING) == 6
    assert severities.count(SEVERITY_WARNING) == 5
    assert severities.count(SEVERITY_INFO) == 1
    assert severities == sorted(
        severities, key=[SEVERITY_BLOCKING, SEVERITY_WARNING, SEVERITY_INFO].index
    )


FINDING_CASES = {
    UNBALANCED_ENTRY: (
        lambda f: _params(
            f,
            journal=f["close"]["journal"]
            + [_entry("j2", "2026-04-15", [_line("cash", "250.00", "0")])],
        ),
        ["j2"],
    ),
    UNMAPPED_ACCOUNT: (
        lambda f: _params(
            f,
            journal=[
                _entry(
                    "j1",
                    "2026-04-03",
                    [
                        _line("cash", "9800.00", "0"),
                        _line("other_sales", "0", "9800.00"),
                    ],
                    "demo_customer",
                )
            ],
        ),
        ["other_sales"],
    ),
    ENTRY_OUT_OF_PERIOD: (
        lambda f: _params(
            f, journal=[{**f["close"]["journal"][0], "date": "2026-03-31"}]
        ),
        ["j1"],
    ),
    ENTRY_AFTER_AS_OF: (
        lambda f: _params(
            f, journal=[{**f["close"]["journal"][0], "date": "2026-07-10"}]
        ),
        ["j1"],
    ),
    DUPLICATE_ENTRY_ID: (
        lambda f: _params(
            f,
            journal=[
                f["close"]["journal"][0],
                _entry(
                    "j1",
                    "2026-05-03",
                    [_line("cash", "1.00", "0"), _line("sales", "0", "1.00")],
                ),
            ],
        ),
        ["j1"],
    ),
    OPENING_UNBALANCED: (
        lambda f: _params(
            f,
            opening_balances=[
                _line("cash", "4000.00", "0"),
                _line("owner_equity", "0", "3000.00"),
            ],
        ),
        [],
    ),
    SUSPECTED_DUPLICATE: (
        lambda f: _params(
            f,
            journal=[
                f["close"]["journal"][0],
                {**copy.deepcopy(f["close"]["journal"][0]), "id": "j2"},
            ],
        ),
        ["j1", "j2"],
    ),
    ZERO_LINE: (
        lambda f: _params(
            f,
            journal=[
                _entry(
                    "j1",
                    "2026-04-03",
                    [
                        _line("cash", "9800.00", "0"),
                        _line("sales", "0", "9800.00"),
                        _line("rent", "0", "0"),
                    ],
                    "demo_customer",
                )
            ],
        ),
        ["j1"],
    ),
    NEGATIVE_CASH: (
        lambda f: _params(
            f,
            journal=f["close"]["journal"]
            + [
                _entry(
                    "j2",
                    "2026-05-01",
                    [_line("rent", "15000.00", "0"), _line("cash", "0", "15000.00")],
                    "demo_landlord",
                )
            ],
        ),
        ["cash"],
    ),
    EXPECTED_MISSING: (lambda f: _params(f), ["rent/demo_landlord"]),
    AMOUNT_DEVIATION: (
        lambda f: _params(
            f, journal=f["close"]["journal"] + [_rent("2026-04-30", "700.00")]
        ),
        ["rent/demo_landlord"],
    ),
    UNUSED_ACCOUNT: (
        lambda f: _params(
            f,
            chart_of_accounts=f["close"]["chart_of_accounts"]
            + [{"account": "unused_reserve", "class": "liability"}],
        ),
        ["unused_reserve"],
    ),
}


def test_finding_cases_cover_registry():
    assert set(FINDING_CASES) == set(FINDING_CODES)


@pytest.mark.parametrize("code", FINDING_CODES)
def test_each_finding_code(skill, error_inputs, code):
    builder, refs = FINDING_CASES[code]
    params = builder(error_inputs)
    params["action"] = "validate"
    result = _assert_completed(skill.execute(params))
    assert set(result) == set(VALIDATE_OUTPUT_KEYS)
    found = {item["code"]: item for item in result["findings"]}
    assert code in found, result
    item = found[code]
    assert set(item) == {"code", "severity", "count", "refs"}
    assert item["severity"] == FINDING_SEVERITY[code]
    assert item["refs"] == refs
    assert item["count"] == (len(refs) if refs else 1)
    assert result["blocking"] is any(
        found[c]["severity"] == SEVERITY_BLOCKING for c in found
    )
    # Findings are reported in registry order, one element per code.
    codes = [item["code"] for item in result["findings"]]
    assert codes == [c for c in FINDING_CODES if c in codes]


@pytest.mark.parametrize(
    "code",
    [code for code, severity in FINDINGS_REGISTRY if severity == SEVERITY_BLOCKING],
)
def test_close_is_blocked_on_any_blocking_finding(skill, error_inputs, code):
    builder, refs = FINDING_CASES[code]
    params = builder(error_inputs)
    params["action"] = "close"
    params["options"] = {"soft_close": False}
    detail = _assert_error(skill.execute(params), CLOSE_BLOCKED)
    assert {"code": code, "refs": refs} in detail["findings"]
    for item in detail["findings"]:
        assert set(item) == {"code", "refs"}
        assert FINDING_SEVERITY[item["code"]] == SEVERITY_BLOCKING
    codes = [item["code"] for item in detail["findings"]]
    assert codes == [c for c in FINDING_CODES if c in codes]
    # Warning and info findings never block.
    params["options"] = {"soft_close": True}
    assert _assert_completed(skill.execute(params))["provisional"] is True


@pytest.mark.parametrize(
    "code",
    [code for code, severity in FINDINGS_REGISTRY if severity != SEVERITY_BLOCKING],
)
def test_warnings_and_info_do_not_block(skill, error_inputs, code):
    builder, refs = FINDING_CASES[code]
    params = builder(error_inputs)
    params["action"] = "close"
    result = _assert_completed(skill.execute(params))
    assert result["closed"] is True and result["provisional"] is False
    assert code in [item["code"] for item in result["checks"]]
    assert result["fired_any"] is (code != UNUSED_ACCOUNT) or any(
        item["severity"] != SEVERITY_INFO for item in result["checks"]
    )


# --------------------------------------------------------------------------
# The issue's examples, the soft close, validate against close
# --------------------------------------------------------------------------


def test_issue_example_close(skill, close_params):
    result = _run(skill, close_params)
    assert result == _load_json(FIXTURES_DIR, "expected_close.json")
    assert list(result) == list(CLOSE_OUTPUT_KEYS)
    assert result["status"] == "completed"
    assert result["closed"] is True and result["provisional"] is False
    assert [row["account"] for row in result["trial_balance"]] == [
        "cash",
        "sales",
        "rent",
        "owner_equity",
    ]
    assert result["trial_balance"][0] == {
        "account": "cash",
        "class": "asset",
        "opening": "4000.00",
        "debit": "9800.00",
        "credit": "0",
        "closing": "13800.00",
    }
    assert result["income_statement"] == {
        "revenue": "9800.00",
        "cogs": None,
        "gross_profit": None,
        "expenses": "0",
        "net_income": "9800.00",
    }
    assert result["balance_sheet"]["balanced"] is True
    assert result["balance_sheet"]["equity_movements"] == "0"
    assert result["cash_flow"] == {
        "cash_opening": "4000.00",
        "inflows": "9800.00",
        "outflows": "0",
        "cash_closing": "13800.00",
    }
    assert result["checks"] == [
        {
            "code": "EXPECTED_MISSING",
            "severity": "warning",
            "count": 1,
            "refs": ["rent/demo_landlord"],
        }
    ]
    assert result["notes"] == [
        {
            "code": "EXPECTED_MISSING",
            "account": "rent",
            "counterparty": "demo_landlord",
            "cadence": "monthly",
            "last_seen": "2026-03-31",
            "expected_amount": "1500.00",
            "detail": "seen in 3 of the last 3 periods, absent in this one",
        }
    ]
    assert result["ratios"] == {
        "gross_margin": None,
        "operating_margin": "1.0000",
        "net_margin": "1.0000",
        "current_ratio": None,
        "runway_months": None,
        "expense_concentration_top1": "0.0000",
    }
    assert result["fired_any"] is True and result["insufficient"] is True


def test_issue_example_close_blocked(skill, blocked_params):
    result = _run(skill, blocked_params)
    assert result == _load_json(FIXTURES_DIR, "expected_blocked.json")
    assert result == {
        "status": "error",
        "error": {
            "code": "CLOSE_BLOCKED",
            "detail": {"findings": [{"code": "UNBALANCED_ENTRY", "refs": ["j2"]}]},
        },
    }


def test_soft_close_is_provisional_with_notes(skill, blocked_params):
    params = copy.deepcopy(blocked_params)
    params["options"]["soft_close"] = True
    result = _assert_completed(_run(skill, params))
    assert result == _load_json(FIXTURES_DIR, "expected_soft_close.json")
    assert result["closed"] is False and result["provisional"] is True
    # The unbalanced entry is dated in the period and posts to a chart
    # account, so its figures are included as given and the sheet does not balance.
    assert result["trial_balance"][0]["debit"] == "10050.00"
    assert result["balance_sheet"]["assets"] == "14050.00"
    assert result["balance_sheet"]["equity_closing"] == "13800.00"
    assert result["balance_sheet"]["balanced"] is False
    assert result["cash_flow"]["inflows"] == "10050.00"
    assert [item["code"] for item in result["checks"]] == [
        UNBALANCED_ENTRY,
        EXPECTED_MISSING,
    ]
    assert result["notes"][0] == {"code": UNBALANCED_ENTRY, "refs": ["j2"]}
    assert result["notes"][1]["code"] == EXPECTED_MISSING
    assert len(result["notes"]) == 2
    # Without a blocking finding a soft close gives the same figures as a hard close.
    example = _load_json(FIXTURES_DIR, "example_close_params.json")
    hard = _run(skill, example)
    example["options"]["soft_close"] = True
    soft = _assert_completed(_run(skill, example))
    assert soft["closed"] is False and soft["provisional"] is True
    for key in CLOSE_OUTPUT_KEYS:
        if key not in ("closed", "provisional"):
            assert soft[key] == hard[key], key


def test_validate_returns_findings_only(skill, validate_params):
    result = _assert_completed(_run(skill, validate_params))
    assert result == _load_json(FIXTURES_DIR, "expected_validate.json")
    assert list(result) == list(VALIDATE_OUTPUT_KEYS)
    assert [item["code"] for item in result["findings"]] == [
        SUSPECTED_DUPLICATE,
        ZERO_LINE,
        NEGATIVE_CASH,
        EXPECTED_MISSING,
        AMOUNT_DEVIATION,
        UNUSED_ACCOUNT,
    ]
    assert result["blocking"] is False
    for item in result["findings"]:
        assert item["refs"] == sorted(set(item["refs"]))
        assert item["count"] == len(item["refs"])


@pytest.mark.parametrize("params_name", [name for name, _ in REFERENCE_FIXTURES])
def test_close_checks_equal_validate_findings(skill, params_name):
    params = _load_json(FIXTURES_DIR, params_name)
    params["action"] = "validate"
    validated = _assert_completed(_run(skill, params))
    params["action"] = "close"
    params.setdefault("options", {})["soft_close"] = True
    closed = _assert_completed(_run(skill, params))
    assert closed["checks"] == validated["findings"]
    assert validated["blocking"] is any(
        item["severity"] == SEVERITY_BLOCKING for item in closed["checks"]
    )


def test_close_before_period_end_with_and_without_soft_close(skill, close_params):
    for soft_close in (False, True):
        params = copy.deepcopy(close_params)
        params["as_of"] = "2026-06-29"
        params["options"]["soft_close"] = soft_close
        detail = _assert_error(
            skill.execute(params), CLOSE_BEFORE_PERIOD_END, "close_before_period_end"
        )
        assert detail["path"] == "as_of"
        params["action"] = "validate"
        _assert_completed(skill.execute(params))
    params = copy.deepcopy(close_params)
    params["as_of"] = "2026-06-30"
    assert _assert_completed(skill.execute(params))["closed"] is True


def test_figures_use_in_period_entries_and_chart_lines_only(skill, close_params):
    params = copy.deepcopy(close_params)
    params["options"]["soft_close"] = True
    params["journal"] += [
        _entry(
            "early",
            "2026-03-15",
            [_line("cash", "100.00", "0"), _line("sales", "0", "100.00")],
            "demo_customer",
        ),
        _entry(
            "j3",
            "2026-05-05",
            [_line("cash", "50.00", "0"), _line("other_sales", "0", "50.00")],
            "demo_customer",
        ),
        _entry(
            "late",
            "2026-07-02",
            [_line("cash", "7.00", "0"), _line("sales", "0", "7.00")],
        ),
    ]
    result = _assert_completed(skill.execute(params))
    assert [item["code"] for item in result["checks"]] == [
        UNMAPPED_ACCOUNT,
        ENTRY_OUT_OF_PERIOD,
        EXPECTED_MISSING,
    ]
    assert result["checks"][1]["refs"] == ["early", "late"]
    assert result["trial_balance"][0]["debit"] == "9850.00"
    assert result["trial_balance"][0]["closing"] == "13850.00"
    assert result["trial_balance"][1]["credit"] == "9800.00"
    assert result["balance_sheet"]["assets"] == "13850.00"
    assert result["balance_sheet"]["balanced"] is False
    assert result["cash_flow"]["inflows"] == "9850.00"
    assert result["notes"][:2] == [
        {"code": UNMAPPED_ACCOUNT, "refs": ["other_sales"]},
        {"code": ENTRY_OUT_OF_PERIOD, "refs": ["early", "late"]},
    ]


# --------------------------------------------------------------------------
# Statements, identities, gap rules, rendering
# --------------------------------------------------------------------------


def test_full_chart_matches_expected_exactly(skill):
    params = _load_json(FIXTURES_DIR, "full_chart_params.json")
    result = _run(skill, params)
    assert result == _load_json(FIXTURES_DIR, "expected_full_chart.json")
    assert result["checks"] == [] and result["notes"] == []
    assert result["fired_any"] is False and result["insufficient"] is False
    assert all(value is not None for value in result["ratios"].values())
    assert result["income_statement"] == {
        "revenue": "4030.00",
        "cogs": "2200.00",
        "gross_profit": "1830.00",
        "expenses": "4620.00",
        "net_income": "-2790.00",
    }
    assert result["balance_sheet"] == {
        "assets": "15510.00",
        "liabilities": "7300.00",
        "equity_opening": "10500.00",
        "equity_movements": "500.00",
        "net_income": "-2790.00",
        "equity_closing": "8210.00",
        "balanced": True,
    }
    # A transfer between two cash accounts counts as neither inflow nor outflow.
    assert result["cash_flow"] == {
        "cash_opening": "10000.00",
        "inflows": "3030.00",
        "outflows": "5620.00",
        "cash_closing": "7410.00",
    }


def _identities(result):
    rows = result["trial_balance"]
    debits = sum(_decimal(row["debit"]) for row in rows)
    credits = sum(_decimal(row["credit"]) for row in rows)
    assert debits == credits
    sheet = result["balance_sheet"]
    assert _decimal(sheet["assets"]) == _decimal(sheet["liabilities"]) + _decimal(
        sheet["equity_closing"]
    )
    assert sheet["balanced"] is True
    assert _decimal(sheet["equity_closing"]) == (
        _decimal(sheet["equity_opening"])
        + _decimal(sheet["equity_movements"])
        + _decimal(sheet["net_income"])
    )
    assert sheet["net_income"] == result["income_statement"]["net_income"]
    cash = result["cash_flow"]
    if cash["cash_closing"] is not None:
        assert _decimal(cash["cash_closing"]) == (
            _decimal(cash["cash_opening"])
            + _decimal(cash["inflows"])
            - _decimal(cash["outflows"])
        )
    statement = result["income_statement"]
    revenue = _decimal(statement["revenue"] or "0")
    cogs = _decimal(statement["cogs"] or "0")
    expenses = _decimal(statement["expenses"] or "0")
    assert _decimal(statement["net_income"]) == revenue - cogs - expenses
    if statement["gross_profit"] is not None:
        assert _decimal(statement["gross_profit"]) == revenue - cogs


@pytest.mark.parametrize("params_name", IDENTITY_FIXTURES)
def test_accounting_identities_hold_on_every_valid_fixture(skill, params_name):
    params = _load_json(FIXTURES_DIR, params_name)
    result = _assert_completed(_run(skill, params))
    assert not any(item["severity"] == SEVERITY_BLOCKING for item in result["checks"])
    _identities(result)


def test_amount_deviation_thresholds(skill):
    at = _run(skill, _load_json(FIXTURES_DIR, "deviation_at_params.json"))
    below = _run(skill, _load_json(FIXTURES_DIR, "deviation_below_params.json"))
    above = _run(skill, _load_json(FIXTURES_DIR, "deviation_above_params.json"))
    assert at == _load_json(FIXTURES_DIR, "expected_deviation_at.json")
    assert below == _load_json(FIXTURES_DIR, "expected_deviation_below.json")
    assert above == _load_json(FIXTURES_DIR, "expected_deviation_above.json")
    # Median 500.00 and threshold 0.20: the line is at 600.00, compared exactly.
    assert at["income_statement"]["expenses"] == "600.00"
    assert at["checks"] == [] and at["notes"] == []
    assert below["checks"] == [] and below["notes"] == []
    assert [item["code"] for item in above["checks"]] == [AMOUNT_DEVIATION]
    assert above["notes"] == [
        {
            "code": AMOUNT_DEVIATION,
            "account": "rent",
            "counterparty": "demo_landlord",
            "amount": "600.01",
            "median": "500.00",
            "deviation": "0.2000",
        }
    ]
    assert list(above["notes"][0]) == list(AMOUNT_DEVIATION_NOTE_KEYS)


def test_amount_deviation_uses_the_quantised_median(skill, close_params):
    # Prior amounts 500.00 and 500.01: the exact median is 500.005, the shown
    # median 500.01, and the decision uses the shown value.
    params = copy.deepcopy(close_params)
    params["prior_journals"] = [
        {
            "period": {"start": "2025-10-01", "end": "2025-12-31"},
            "journal": [_rent("2025-12-31", "500.00")],
        },
        {
            "period": {"start": "2026-01-01", "end": "2026-03-31"},
            "journal": [_rent("2026-03-31", "500.01")],
        },
    ]
    # Against the exact median, 600.01 would fire (100.005 > 100.001);
    # against the shown median it does not (100.00 is not above 100.002).
    params["journal"].append(_rent("2026-04-30", "600.01"))
    assert _assert_completed(skill.execute(params))["notes"] == []
    params["journal"][-1] = _rent("2026-04-30", "600.02")
    result = _assert_completed(skill.execute(params))
    assert result["notes"] == [
        {
            "code": AMOUNT_DEVIATION,
            "account": "rent",
            "counterparty": "demo_landlord",
            "amount": "600.02",
            "median": "500.01",
            "deviation": "0.2000",
        }
    ]


def test_expected_missing_recurrence(skill):
    at = _run(skill, _load_json(FIXTURES_DIR, "recurrence_at_params.json"))
    below = _run(skill, _load_json(FIXTURES_DIR, "recurrence_below_params.json"))
    assert at == _load_json(FIXTURES_DIR, "expected_recurrence_at.json")
    assert below == _load_json(FIXTURES_DIR, "expected_recurrence_below.json")
    assert at["notes"] == [
        {
            "code": EXPECTED_MISSING,
            "account": "rent",
            "counterparty": "demo_landlord",
            "cadence": "quarterly",
            "last_seen": "2026-03-31",
            "expected_amount": "500.00",
            "detail": "seen in 3 of the last 3 periods, absent in this one",
        }
    ]
    assert list(at["notes"][0]) == list(EXPECTED_MISSING_NOTE_KEYS)
    assert below["checks"] == [] and below["notes"] == []


@pytest.mark.parametrize(
    "entries_per_period,length_days,cadence",
    [(13, 91, "weekly"), (3, 91, "monthly"), (1, 91, "quarterly"), (1, 365, "longer")],
)
def test_cadence_bands(skill, close_params, entries_per_period, length_days, cadence):
    params = copy.deepcopy(close_params)
    if length_days == 365:
        params["period"] = {"start": "2026-01-01", "end": "2026-12-31"}
        params["as_of"] = "2027-01-05"
        periods = [("2024-01-01", "2024-12-31"), ("2025-01-01", "2025-12-31")]
    else:
        periods = [("2025-10-01", "2025-12-31"), ("2026-01-01", "2026-03-31")]
    params["prior_journals"] = []
    for start, end in periods:
        journal = []
        for n in range(entries_per_period):
            day = f"{start[:7]}-{(n % 28) + 1:02d}"
            journal.append(
                _entry(
                    f"r{n}",
                    day,
                    [_line("rent", "10.00", "0"), _line("cash", "0", "10.00")],
                    "demo_landlord",
                )
            )
        params["prior_journals"].append(
            {"period": {"start": start, "end": end}, "journal": journal}
        )
    result = _assert_completed(skill.execute(params))
    assert result["notes"][0]["cadence"] == cadence


def test_gaps_are_never_filled(skill, close_params):
    result = _run(skill, close_params)
    rent = result["trial_balance"][2]
    assert rent == {
        "account": "rent",
        "class": "expense",
        "opening": "0",
        "debit": "0",
        "credit": "0",
        "closing": "0",
    }
    assert result["income_statement"]["expenses"] == "0"
    assert result["notes"][0]["expected_amount"] == "1500.00"
    above = _run(skill, _load_json(FIXTURES_DIR, "deviation_above_params.json"))
    assert above["income_statement"]["expenses"] == "600.01"
    assert above["notes"][0]["median"] == "500.00"


def test_pairs_need_a_counterparty_and_a_profit_and_loss_account(skill, close_params):
    params = copy.deepcopy(close_params)
    for prior in params["prior_journals"]:
        for entry in prior["journal"]:
            del entry["counterparty"]
    assert _assert_completed(skill.execute(params))["checks"] == []
    params = copy.deepcopy(close_params)
    params["chart_of_accounts"].append({"account": "deposit", "class": "asset"})
    for prior in params["prior_journals"]:
        for entry in prior["journal"]:
            entry["lines"][0]["account"] = "deposit"
    result = _assert_completed(skill.execute(params))
    assert [item["code"] for item in result["checks"]] == [UNUSED_ACCOUNT]
    assert result["checks"][0]["refs"] == ["rent"]


def test_rendering_rule(skill):
    result = _run(skill, _load_json(FIXTURES_DIR, "rendering_params.json"))
    assert result == _load_json(FIXTURES_DIR, "expected_rendering.json")
    assert result["income_statement"]["net_income"] == "-200.50"
    assert result["balance_sheet"]["net_income"] == "-200.50"
    assert result["ratios"]["net_margin"] == "-2.0050"
    assert result["ratios"]["operating_margin"] == "-2.0000"
    # The even-count median 500.005 rounds half up to 500.01.
    assert result["notes"][0]["expected_amount"] == "500.01"
    assert result["income_statement"]["cogs"] is None
    assert result["cash_flow"] == {
        "cash_opening": None,
        "inflows": None,
        "outflows": None,
        "cash_closing": None,
    }
    for params_name, _ in REFERENCE_FIXTURES:
        output = _run(skill, _load_json(FIXTURES_DIR, params_name))
        if output["status"] != "completed" or "trial_balance" not in output:
            continue
        for row in output["trial_balance"]:
            for key in ("opening", "debit", "credit", "closing"):
                assert AMOUNT_RENDERING.fullmatch(row[key]), row
        for block in ("income_statement", "balance_sheet", "cash_flow"):
            for key, value in output[block].items():
                if isinstance(value, str):
                    assert AMOUNT_RENDERING.fullmatch(value), (block, key, value)
        for value in output["ratios"].values():
            assert value is None or RATIO_RENDERING.fullmatch(value), value
        for note in output["notes"]:
            for key in ("expected_amount", "amount", "median"):
                if key in note:
                    assert AMOUNT_RENDERING.fullmatch(note[key])
            if "deviation" in note:
                assert RATIO_RENDERING.fullmatch(note["deviation"])


def test_null_cases_of_the_issue_example(skill, close_params):
    result = _run(skill, close_params)
    assert result["income_statement"]["cogs"] is None
    assert result["income_statement"]["gross_profit"] is None
    assert result["income_statement"]["expenses"] == "0"
    assert result["ratios"]["gross_margin"] is None
    assert result["ratios"]["current_ratio"] is None
    assert result["ratios"]["runway_months"] is None
    assert result["insufficient"] is True
    # No expense account at all: expenses is null, not zero.
    params = copy.deepcopy(close_params)
    params["chart_of_accounts"] = [
        row for row in params["chart_of_accounts"] if row["account"] != "rent"
    ]
    params["prior_journals"] = []
    result = _assert_completed(skill.execute(params))
    assert result["income_statement"]["expenses"] is None
    assert result["income_statement"]["net_income"] == "9800.00"
    assert result["ratios"]["expense_concentration_top1"] is None
    # No revenue account: revenue is null and the margins are null.
    params = copy.deepcopy(close_params)
    params["chart_of_accounts"] = [
        row for row in params["chart_of_accounts"] if row["account"] != "sales"
    ]
    params["journal"] = [_rent("2026-04-30", "100.00")]
    params["prior_journals"] = []
    result = _assert_completed(skill.execute(params))
    assert result["income_statement"]["revenue"] is None
    assert result["income_statement"]["net_income"] == "-100.00"
    assert result["ratios"]["operating_margin"] is None
    assert result["ratios"]["net_margin"] is None
    assert result["observations"][0]["value"] is None


def _quantize(value):
    return str(value.quantize(Decimal("0.0001"), rounding=decimal.ROUND_HALF_UP))


def test_ratio_operands_follow_the_per_ratio_definitions(skill):
    example = _run(skill, _load_json(FIXTURES_DIR, "example_close_params.json"))
    # Operating margin uses line sums (zero where the chart has no such
    # account); gross margin uses the statement figure, which is null.
    assert example["ratios"]["gross_margin"] is None
    assert example["ratios"]["operating_margin"] == "1.0000"
    assert example["ratios"]["net_margin"] == "1.0000"
    assert example["ratios"]["current_ratio"] is None
    assert example["ratios"]["runway_months"] is None
    assert example["ratios"]["expense_concentration_top1"] == "0.0000"

    full = _run(skill, _load_json(FIXTURES_DIR, "full_chart_params.json"))
    closing = {
        row["account"]: _decimal(row["closing"]) for row in full["trial_balance"]
    }
    revenue = _decimal(full["income_statement"]["revenue"])
    assert full["ratios"]["gross_margin"] == _quantize(
        _decimal(full["income_statement"]["gross_profit"]) / revenue
    )
    operating = closing["sales"] - closing["cogs"] - closing["rent"]
    assert full["ratios"]["operating_margin"] == _quantize(operating / revenue)
    assert full["ratios"]["net_margin"] == _quantize(
        _decimal(full["income_statement"]["net_income"]) / revenue
    )
    current_assets = (
        closing["cash"] + closing["bank"] + closing["receivable"] + closing["inventory"]
    )
    assert full["ratios"]["current_ratio"] == _quantize(
        current_assets / closing["payable"]
    )
    cash = full["cash_flow"]
    burn = (
        (_decimal(cash["outflows"]) - _decimal(cash["inflows"]))
        * Decimal("30.4375")
        / Decimal(91)
    )
    assert full["ratios"]["runway_months"] == _quantize(
        _decimal(cash["cash_closing"]) / burn
    )
    expense_total = closing["cogs"] + closing["rent"] + closing["interest_expense"]
    assert full["ratios"]["expense_concentration_top1"] == _quantize(
        closing["rent"] / expense_total
    )


def _context_snapshot():
    context = decimal.getcontext()
    return (
        context.prec,
        context.rounding,
        context.Emin,
        context.Emax,
        context.capitals,
        context.clamp,
        dict(context.flags),
        dict(context.traps),
    )


def test_thread_decimal_context_is_unchanged(skill, error_inputs):
    before = _context_snapshot()
    for params_name, _ in REFERENCE_FIXTURES:
        for action in ACTIONS:
            params = _load_json(FIXTURES_DIR, params_name)
            params["action"] = action
            skill.execute(params)
            assert _context_snapshot() == before, (params_name, action)
    for code, builder in ERROR_CASES.items():
        skill.execute(builder(error_inputs))
        assert _context_snapshot() == before, code
    for (first, second), entry in PAIRS.items():
        if not isinstance(entry, str):
            skill.execute(entry[0](error_inputs))
            assert _context_snapshot() == before, (first, second)
    # A hostile thread context changes nothing: the skill's arithmetic is private.
    with decimal.localcontext() as hostile:
        hostile.prec = 3
        hostile.rounding = decimal.ROUND_DOWN
        hostile.traps[decimal.Inexact] = True
        hostile.traps[decimal.Rounded] = True
        result = _run(skill, _load_json(FIXTURES_DIR, "full_chart_params.json"))
        assert result == _load_json(FIXTURES_DIR, "expected_full_chart.json")
        assert hostile.prec == 3 and hostile.rounding == decimal.ROUND_DOWN
    assert _context_snapshot() == before


def test_zero_renders_unsigned_after_quantisation(skill, close_params):
    assert skill_module._render_amount(Decimal("-0.004")) == "0"
    assert skill_module._render_amount(Decimal("0.004")) == "0"
    assert skill_module._render_amount(Decimal("-0.005")) == "-0.01"
    assert skill_module._render_amount(Decimal("-0")) == "0"
    assert skill_module._render_ratio(Decimal("-0.00004")) == "0.0000"
    assert skill_module._render_ratio(Decimal("-0.00005")) == "-0.0001"
    assert skill_module._render_ratio(Decimal("0.00004")) == "0.0000"
    # End to end: a net margin of -0.00004 renders as 0.0000, never -0.0000.
    params = copy.deepcopy(close_params)
    params["journal"] = [
        _entry(
            "j1",
            "2026-04-03",
            [_line("cash", "100000.00", "0"), _line("sales", "0", "100000.00")],
            "demo_customer",
        ),
        _rent("2026-04-30", "100004.00"),
    ]
    params["prior_journals"] = []
    result = _assert_completed(skill.execute(params))
    assert result["income_statement"]["net_income"] == "-4.00"
    assert result["ratios"]["net_margin"] == "0.0000"
    assert result["ratios"]["operating_margin"] == "0.0000"
    assert "-0.0000" not in json.dumps(result) and '"-0"' not in json.dumps(result)


def test_observations_shape(skill, close_params):
    result = _run(skill, close_params)
    assert result["observations"] == [
        {"metric": "revenue", "value": "9800.00", "as_of": "2026-06-30"},
        {"metric": "cash_closing", "value": "13800.00", "as_of": "2026-06-30"},
    ]
    assert OBSERVATION_METRICS == ("revenue", "cash_closing")
    rendering = _run(skill, _load_json(FIXTURES_DIR, "rendering_params.json"))
    assert rendering["observations"] == [
        {"metric": "revenue", "value": "100.00", "as_of": "2026-06-30"},
        {"metric": "cash_closing", "value": None, "as_of": "2026-06-30"},
    ]
    for params_name, _ in REFERENCE_FIXTURES:
        params = _load_json(FIXTURES_DIR, params_name)
        params["action"] = "close"
        params.setdefault("options", {})["soft_close"] = True
        output = _assert_completed(_run(skill, params))
        assert [item["metric"] for item in output["observations"]] == list(
            OBSERVATION_METRICS
        )
        for item in output["observations"]:
            assert set(item) == {"metric", "value", "as_of"}
            assert item["as_of"] == params["period"]["end"]


def test_suspected_duplicate_without_counterparty(skill, close_params):
    lines = [_line("cash", "100.00", "0"), _line("sales", "0", "100.00")]
    params = copy.deepcopy(close_params)
    params["action"] = "validate"
    params["journal"] = [
        _entry("a1", "2026-05-01", lines),
        _entry("a2", "2026-05-01", list(reversed(lines))),
    ]
    result = _assert_completed(skill.execute(params))
    assert {item["code"]: item["refs"] for item in result["findings"]}[
        SUSPECTED_DUPLICATE
    ] == ["a1", "a2"]
    # The same two entries with a counterparty on only one of them form no group.
    params["journal"][1]["counterparty"] = "demo_customer"
    result = _assert_completed(skill.execute(params))
    assert SUSPECTED_DUPLICATE not in [item["code"] for item in result["findings"]]


def test_suspected_duplicate_compares_amounts_by_value(skill, close_params):
    params = copy.deepcopy(close_params)
    params["action"] = "validate"
    params["journal"] = [
        _entry(
            "a1",
            "2026-05-01",
            [_line("cash", "100", "0"), _line("sales", "0", "100")],
            "demo_customer",
        ),
        _entry(
            "a2",
            "2026-05-01",
            [_line("cash", "100.00", "0"), _line("sales", "0", "100.00")],
            "demo_customer",
        ),
        _entry(
            "a3",
            "2026-05-01",
            [_line("cash", "100.01", "0"), _line("sales", "0", "100.01")],
            "demo_customer",
        ),
        _entry(
            "a4",
            "2026-05-02",
            [_line("cash", "100.00", "0"), _line("sales", "0", "100.00")],
            "demo_customer",
        ),
    ]
    result = _assert_completed(skill.execute(params))
    found = {item["code"]: item["refs"] for item in result["findings"]}
    assert found[SUSPECTED_DUPLICATE] == ["a1", "a2"]
    # Two entries with the same id are a duplicate id, not a suspected duplicate.
    params["journal"] = [
        _entry(
            "a1", "2026-05-01", [_line("cash", "100", "0"), _line("sales", "0", "100")]
        ),
        _entry(
            "a1", "2026-05-01", [_line("cash", "100", "0"), _line("sales", "0", "100")]
        ),
    ]
    result = _assert_completed(skill.execute(params))
    found = {item["code"]: item["refs"] for item in result["findings"]}
    assert found[DUPLICATE_ENTRY_ID] == ["a1"]
    assert SUSPECTED_DUPLICATE not in found


def test_line_with_debit_and_credit_counts_both(skill, close_params):
    params = copy.deepcopy(close_params)
    params["journal"].append(
        _entry(
            "j2",
            "2026-05-01",
            [_line("cash", "100.00", "40.00"), _line("sales", "0", "60.00")],
            "demo_customer",
        )
    )
    result = _assert_completed(skill.execute(params))
    assert [item["code"] for item in result["checks"]] == [EXPECTED_MISSING]
    cash, sales = result["trial_balance"][0], result["trial_balance"][1]
    assert cash["debit"] == "9900.00" and cash["credit"] == "40.00"
    assert cash["closing"] == "13860.00"
    assert sales["credit"] == "9860.00" and sales["closing"] == "9860.00"
    assert result["cash_flow"]["inflows"] == "9860.00"
    assert result["cash_flow"]["outflows"] == "0"
    _identities(result)


def test_unused_account_counts_prior_journal_use(skill, close_params):
    params = copy.deepcopy(close_params)
    params["action"] = "validate"
    result = _assert_completed(skill.execute(params))
    assert UNUSED_ACCOUNT not in [item["code"] for item in result["findings"]]
    params["prior_journals"] = []
    result = _assert_completed(skill.execute(params))
    assert {item["code"]: item["refs"] for item in result["findings"]}[
        UNUSED_ACCOUNT
    ] == ["rent"]
    # A retired account used only in a prior journal must stay in the chart.
    params = copy.deepcopy(close_params)
    params["chart_of_accounts"] = [
        row for row in params["chart_of_accounts"] if row["account"] != "rent"
    ]
    detail = _assert_error(skill.execute(params), INVALID_REQUEST, "unmapped_account")
    assert detail["path"] == "prior_journals[0].journal[0].lines[0].account"


@pytest.mark.parametrize(
    "period,reason",
    [
        ({"start": "2026-01-01", "end": "2026-04-01"}, "not_before_current_period"),
        ({"start": "2026-01-01", "end": "2026-03-27"}, "length_differs"),
        ({"start": "2025-09-30", "end": "2026-03-31"}, "overlaps_prior_period"),
        ({"start": "2026-01-01", "end": "2026-03-29"}, None),
        ({"start": "2025-12-29", "end": "2026-03-31"}, None),
    ],
)
def test_prior_period_rules(skill, close_params, period, reason):
    params = copy.deepcopy(close_params)
    # The first listed prior period is the third quarter of the year before,
    # so the edited one can start as early as the end of that quarter.
    params["prior_journals"] = [
        params["prior_journals"][0],
        {**params["prior_journals"][2], "period": period},
    ]
    params["prior_journals"][1]["journal"] = [
        entry
        for entry in params["prior_journals"][1]["journal"]
        if period["start"] <= entry["date"] <= period["end"]
    ]
    result = skill.execute(params)
    if reason is None:
        _assert_completed(result)
        return
    detail = _assert_error(result, INVALID_PERIOD, reason)
    assert detail["path"] == "prior_journals[1].period"
    if reason == "length_differs":
        assert detail["limit"] == 3 and detail["actual"] == 5


def test_opening_balance_rules(skill, close_params):
    params = copy.deepcopy(close_params)
    params["opening_balances"].append(_line("sales", "0", "1.00"))
    detail = _assert_error(
        skill.execute(params), INVALID_REQUEST, "not_a_balance_sheet_account"
    )
    assert detail["path"] == "opening_balances[2].account"
    params = copy.deepcopy(close_params)
    params["opening_balances"].append(_line("cash", "1.00", "0"))
    detail = _assert_error(skill.execute(params), INVALID_REQUEST, "duplicate_account")
    assert detail["path"] == "opening_balances[2].account"
    # An opening line on an account the chart does not hold is a finding, not an error.
    params = copy.deepcopy(close_params)
    params["action"] = "validate"
    params["opening_balances"] = [
        _line("cash", "4000.00", "0"),
        _line("old_equity", "0", "4000.00"),
    ]
    result = _assert_completed(skill.execute(params))
    found = {item["code"]: item["refs"] for item in result["findings"]}
    assert found[UNMAPPED_ACCOUNT] == ["old_equity"]
    assert result["blocking"] is True


# --------------------------------------------------------------------------
# Documentation schemas and instructions
# --------------------------------------------------------------------------


def _schema(name):
    return _load_json(SCHEMAS_DIR, name)


def test_fixtures_match_the_documentation_schemas():
    request_schema = _schema("request.schema.json")
    output_schema = _schema("output.schema.json")
    jsonschema.Draft202012Validator.check_schema(request_schema)
    jsonschema.Draft202012Validator.check_schema(output_schema)
    for params_name, expected_name in REFERENCE_FIXTURES:
        jsonschema.validate(_load_json(FIXTURES_DIR, params_name), request_schema)
        jsonschema.validate(_load_json(FIXTURES_DIR, expected_name), output_schema)


def test_instructions_have_the_required_sections():
    text = _instructions()
    assert text.startswith("# ")
    assert "`finance/period_close`" in text
    for heading in (
        "## When not to use",
        "## What the host and the operator own",
        "## Agent-loop contract",
        "## Inputs",
        "## Account classes and subclasses",
        "## Findings registry",
        "## Statement rules",
        "## Gap rules",
        "## Rounding and rendering",
        "## Null rule and ratios",
        "## Observations",
        "## Size caps",
        "## Validation order (fail-closed, deterministic)",
        "### Both actions",
        "### close only",
        "## Example",
    ):
        assert "\n" + heading + "\n" in text, heading
    not_for = _flat(_section(text, "## When not to use")).lower()
    for phrase in (
        "tax",
        "jurisdiction-specific presentation",
        "payroll",
        "consolidation",
        "bank-feed",
        "spreadsheet",
    ):
        assert phrase in not_for, phrase
    owners = _flat(_section(text, "## What the host and the operator own"))
    for statement in (
        "The host supplies the journal, the chart of accounts and, optionally, the prior periods",
        "decides whether to call the model",
        "The operator owns the books, the chart mapping and the decision to rely on a provisional close",
    ):
        assert statement in owners, statement
    loop = _flat(_section(text, "## Agent-loop contract"))
    assert "`CLOSE_BLOCKED` stops" in loop
    assert "question for the operator, never a number to fill" in loop
    inputs = _flat(_section(text, "## Inputs"))
    assert "Retired accounts must stay in the chart" in inputs
    observations = _flat(_section(text, "## Observations"))
    assert "exactly `revenue` and `cash_closing`" in observations
    gaps = _flat(_section(text, "## Gap rules"))
    assert "named" in gaps and "never filled" in gaps
    assert "seen in K of the last N periods, absent in this one" in gaps
    assert "`" + skill_module.INTERNAL_ERROR + "`" in text
    for code in sorted(ERROR_REGISTRY):
        assert "`" + code + "`" in text
    for code in FINDING_CODES:
        assert "`" + code + "`" in text
    for key in RATIO_KEYS:
        assert "`" + key + "`" in text
    for keys in (
        TRIAL_BALANCE_KEYS,
        INCOME_STATEMENT_KEYS,
        BALANCE_SHEET_KEYS,
        CASH_FLOW_KEYS,
    ):
        for key in keys:
            assert "`" + key + "`" in text
    assert "`" + str(MAX_RECURRENCE_MIN_PERIODS) + "`" in text or "24" in text
    assert re.search(r"[0-9]+\s*%", text) is None
    assert chr(0xA7) not in text  # no section sign left from a cross-reference
