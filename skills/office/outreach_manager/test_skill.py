"""Bundle tests for office/outreach_manager.

All tests are offline and deterministic: fixtures under fixtures/, profiles
under kb/platforms/ and the lexicon under kb/ exercise the published v0.1
interface, including the two reference examples, every closed-registry error
code in its documented order, every size cap, the partition rule, query
escaping, search URL construction, de-duplication, every rubric component,
CSV neutralisation and bit-identical repeat execution across hash seeds. No
network access is required or permitted.
"""

import ast
import contextlib
import copy
import csv
import hashlib
import io
import json
import os
import re
import signal
import subprocess
import sys
import time
from urllib.parse import parse_qsl, unquote, urlsplit

import jsonschema
import pytest
import yaml

from skill_assay import SkillContext
from skill_assay.core.loader import SkillLoader

from . import skill as skill_module
from .skill import (
    ACTIONS,
    BUNDLE_READS,
    BUNDLED_PLATFORMS,
    CAP_TABLE,
    COMPILE_SEARCH_CHECKS,
    CRITERIA_FIELDS,
    ERROR_REGISTRY,
    FAULT_REGISTRY,
    FILTER_DIMENSIONS,
    INGEST_LEADS_CHECKS,
    INTERNAL_ERROR,
    INVALID_EXISTING_LEADS,
    INVALID_PLAN,
    INVALID_PROFILE,
    INVALID_REQUEST,
    INVALID_ROWS,
    INVALID_TARGET_SPEC,
    LEAD_KEYS,
    LIMIT_EXCEEDED,
    LIST_CAPS,
    MAX_BASE_URL_CHARS,
    MAX_CELL_CHARS,
    MAX_ESTIMATED_MATCHES,
    MAX_EXISTING_LEADS,
    MAX_FIXED_PARAM_VALUE_CHARS,
    MAX_FIXED_PARAMS,
    MAX_PROFILE_INTEGER,
    MAX_PROFILE_LIST_ENTRIES,
    MAX_PROFILE_SOURCES,
    MAX_PROFILE_TEXT_CHARS,
    MAX_QUERIES,
    MAX_ROW_KEYS,
    MAX_ROWS,
    MAX_SEARCH_URL_LENGTH_SETTING,
    MAX_TERM_CHARS,
    MAX_TERM_TOKENS,
    MAX_TOTAL_CELL_CHARS,
    PACING_KEYS,
    PARTITIONING_KEYS,
    PLAN_KEYS,
    PLAN_MISMATCH,
    QUERY_KEYS,
    REVIEW_COLUMNS,
    RUBRIC,
    RUBRIC_KEYS,
    SCORING_TEXT_CHARS,
    SENIORITY_LEVELS,
    STATS_KEYS,
    UNKNOWN_PLATFORM,
    UNKNOWN_SOURCE_QUERY,
    OutreachManagerSkill,
)

BUNDLE_DIR = os.path.dirname(os.path.abspath(__file__))
FIXTURES_DIR = os.path.join(BUNDLE_DIR, "fixtures")
KB_DIR = os.path.join(BUNDLE_DIR, "kb")
SCHEMAS_DIR = os.path.join(BUNDLE_DIR, "schemas")
REPO_ROOT = os.path.abspath(os.path.join(BUNDLE_DIR, "..", "..", ".."))
REGISTRY_ID = "office/outreach_manager"

# Reference fixtures: (params file, expected file). A params file holds one
# request or a list of requests; the expected file mirrors that shape.
REFERENCE_FIXTURES = (
    ("example_compile_params.json", "expected_compile.json"),
    ("linkedin_profile_params.json", "expected_linkedin_profile.json"),
    ("multi_dimension_params.json", "expected_multi_dimension.json"),
    (
        "multi_dimension_cap_risk_params.json",
        "expected_multi_dimension_cap_risk.json",
    ),
    ("example_ingest_params.json", "expected_ingest.json"),
    ("ingest_dedup_params.json", "expected_ingest_dedup.json"),
    ("ingest_components_params.json", "expected_ingest_components.json"),
    ("ingest_hostile_params.json", "expected_ingest_hostile.json"),
)
EXPECTED_FIXTURE_FILES = {name for pair in REFERENCE_FIXTURES for name in pair} | {
    "override_profile.json"
}
EXPECTED_SCHEMA_FILES = {
    "target_spec.schema.json",
    "plan.schema.json",
    "lead_record.schema.json",
    "platform_profile.schema.json",
}
EXPECTED_KB_FILES = {
    "platforms/demo.json",
    "platforms/linkedin.json",
    "seniority_terms.json",
}
EXPECTED_BUNDLE_FILES = {
    "__init__.py",
    "manifest.yaml",
    "skill.py",
    "instructions.md",
    "test_skill.py",
    "card.json",
}

ISSUE_Q01_KEYWORDS = (
    '("Head of Operations" OR "Operations Director") AND "warehouse automation" '
    "NOT recruiter NOT student"
)
ISSUE_SEARCH_URLS = [
    "https://demo.example/search/people/?keywords=%28%22Head%20of%20Operations"
    "%22%20OR%20%22Operations%20Director%22%29%20AND%20%22warehouse%20automation"
    "%22%20NOT%20recruiter%20NOT%20student&geo=%5B%22DE%22%5D&company_size=%5B"
    "%2251-200%22%2C%22201-500%22%5D",
    "https://demo.example/search/people/?keywords=%28%22Head%20of%20Operations"
    "%22%20OR%20%22Operations%20Director%22%29%20AND%20%22warehouse%20automation"
    "%22%20NOT%20recruiter%20NOT%20student&geo=%5B%22NL%22%5D&company_size=%5B"
    "%2251-200%22%2C%22201-500%22%5D",
    "https://demo.example/search/people/?keywords=%28%22Head%20of%20Operations"
    "%22%20OR%20%22Operations%20Director%22%29%20AND%20%22warehouse%20automation"
    "%22%20NOT%20recruiter%20NOT%20student&geo=%5B%22BE%22%5D&company_size=%5B"
    "%2251-200%22%2C%22201-500%22%5D",
]
LINKEDIN_SEARCH_URL = (
    "https://www.linkedin.com/search/results/people/?keywords=%28%22Account%20"
    "Executive%22%20OR%20%22Sales%20Manager%22%29&origin=FACETED_SEARCH&geoUrn="
    "%5B%22106693272%22%5D&network=%5B%22S%22%5D"
)
ISSUE_CSV = (
    "lead_id,score,off_target,title,company,location,reasons,source_query\n"
    'L0001,90,false,Head of Operations,Demo Logistics GmbH,"Hamburg, Germany",'
    "title_match;seniority;industry;geo,q01\n"
    'L0002,0,true,Technical Recruiter,Demo Staffing BV,"Amsterdam, Netherlands",'
    "exclusion_hit:recruiter,q02"
)

FULL_WIDTH_EQUALS = chr(0xFF1D)
BIDI_OVERRIDE = chr(0x202E)
ZERO_WIDTH_SPACE = chr(0x200B)
LEFT_QUOTE = chr(0x201C)
RIGHT_QUOTE = chr(0x201D)


# --------------------------------------------------------------------------
# Helpers and fixtures
# --------------------------------------------------------------------------


def _load_json(directory, name):
    with open(os.path.join(directory, name), "r", encoding="utf-8") as handle:
        return json.load(handle)


def _load_profile(platform):
    return _load_json(os.path.join(KB_DIR, "platforms"), f"{platform}.json")


def _cases(params):
    return params if isinstance(params, list) else [params]


def _run(skill, params):
    if isinstance(params, list):
        return [skill.execute(copy.deepcopy(case)) for case in params]
    return skill.execute(copy.deepcopy(params))


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _assert_error(result, code, reason=None):
    assert set(result) == {"status", "error"}
    assert result["status"] == "error"
    assert set(result["error"]) == {"code", "detail"}
    assert result["error"]["code"] == code
    detail = result["error"]["detail"]
    assert isinstance(detail, dict)
    assert isinstance(detail["reason"], str) and detail["reason"]
    if reason is not None:
        assert detail["reason"] == reason
    return detail


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


def _compile_params(platform="demo", override=None, **spec):
    target_spec = {"schema_version": 1, "titles": ["Operations Manager"]}
    target_spec.update(spec)
    params = {
        "action": "compile_search",
        "platform": platform,
        "target_spec": target_spec,
    }
    if override is not None:
        params["profile_override"] = override
    return params


def _compile_plan(skill, platform="demo", override=None, **spec):
    result = skill.execute(_compile_params(platform, override, **spec))
    assert result["status"] == "completed", result
    return result["plan"]


def _row(url="https://demo.example/in/lead-0500", title="Operations Manager", **more):
    row = {"profileUrl": url, "title": title, "query": "q01"}
    row.update(more)
    return row


def _ingest_params(plan, rows, platform="demo", override=None, existing=None):
    params = {
        "action": "ingest_leads",
        "platform": platform,
        "plan": plan,
        "rows": rows,
    }
    if existing is not None:
        params["existing_leads"] = existing
    if override is not None:
        params["profile_override"] = override
    return params


def _ingest_one(skill, plan, row, platform="demo", override=None):
    result = skill.execute(_ingest_params(plan, [row], platform, override))
    assert result["status"] == "completed", result
    assert len(result["leads"]) == 1
    return result["leads"][0]


def _geo_codes(count):
    codes = []
    for first in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        for second in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            codes.append(first + second)
            if len(codes) == count:
                return codes
    return codes


@pytest.fixture
def skill():
    return OutreachManagerSkill()


@pytest.fixture
def manifest():
    with open(os.path.join(BUNDLE_DIR, "manifest.yaml"), "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture
def compile_params():
    return _load_json(FIXTURES_DIR, "example_compile_params.json")


@pytest.fixture
def example_plan():
    return _load_json(FIXTURES_DIR, "expected_compile.json")["plan"]


@pytest.fixture
def ingest_params():
    return _load_json(FIXTURES_DIR, "example_ingest_params.json")


@pytest.fixture
def override():
    return _load_json(FIXTURES_DIR, "override_profile.json")


@pytest.fixture
def demo_override():
    """A copy of the bundled demo profile, usable as an override."""
    return _load_profile("demo")


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
    assert listing(KB_DIR) == EXPECTED_KB_FILES
    assert everything == (
        EXPECTED_BUNDLE_FILES
        | {os.path.join("fixtures", n) for n in EXPECTED_FIXTURE_FILES}
        | {os.path.join("schemas", n) for n in EXPECTED_SCHEMA_FILES}
        | {os.path.join("kb", n) for n in EXPECTED_KB_FILES}
    )
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
    assert manifest["category"] == "office"
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
    assert set(manifest["outputs"]) == {
        "status",
        "plan",
        "leads",
        "review_table",
        "stats",
    }


def test_skill_manifest_consistency(skill, manifest, compile_params, ingest_params):
    assert skill.manifest["name"] == manifest["name"] == REGISTRY_ID
    assert skill.manifest["version"] == manifest["version"]
    assert "output" not in manifest
    seen = set()
    for params in (compile_params, ingest_params):
        result = skill.execute(params)
        assert result["status"] == "completed"
        seen.update(result)
    assert set(manifest["outputs"]) == seen


def test_manifest_identity_matches_folder(manifest):
    category = os.path.basename(os.path.dirname(BUNDLE_DIR))
    folder = os.path.basename(BUNDLE_DIR)
    assert manifest["name"] == f"{category}/{folder}"


def test_manifest_parameters_validate_fixtures(skill):
    for params_name, _ in REFERENCE_FIXTURES:
        for case in _cases(_load_json(FIXTURES_DIR, params_name)):
            assert skill.validate_params(case) is True


def test_manifest_parameters_are_flat(manifest):
    parameters = manifest["parameters"]
    assert parameters["type"] == "object"
    assert parameters["required"] == ["action"]
    assert parameters["additionalProperties"] is False
    for name, prop in parameters["properties"].items():
        expected = {"type", "description"} | ({"enum"} if name == "action" else set())
        assert set(prop) == expected, name
        assert isinstance(prop["type"], str)
    assert parameters["properties"]["action"]["enum"] == list(ACTIONS)


def test_framework_path_reaches_row_checks(example_plan):
    marker = "zq-row-marker-text"
    context = SkillContext(skill=REGISTRY_ID)
    result = context.execute(REGISTRY_ID, _ingest_params(example_plan, [marker]))
    detail = _assert_error(result, INVALID_ROWS, "row_not_object")
    assert detail["row"] == 0
    assert marker not in json.dumps(result)
    result = context.execute(
        REGISTRY_ID, _ingest_params(example_plan, [_row()], existing=[marker])
    )
    _assert_error(result, INVALID_EXISTING_LEADS, "not_an_object")
    assert marker not in json.dumps(result)


def test_skill_loader_can_import():
    bundle = SkillLoader.load_skill(REGISTRY_ID)
    assert bundle["manifest"]["name"] == REGISTRY_ID
    assert hasattr(bundle["module"], "OutreachManagerSkill")
    assert bundle["class"] is bundle["module"].OutreachManagerSkill
    assert bundle["instructions"].strip()
    assert bundle["card"]["issuer"]["email"] == bundle["manifest"]["issuer"]["email"]


def test_actions_are_exactly_the_published_set(
    skill, manifest, compile_params, ingest_params
):
    assert ACTIONS == ("compile_search", "ingest_leads")
    assert manifest["parameters"]["properties"]["action"]["enum"] == list(ACTIONS)
    assert skill.execute(compile_params)["status"] == "completed"
    assert skill.execute(ingest_params)["status"] == "completed"


@pytest.mark.parametrize("action", ["bogus", "", None, 3, ["compile_search"]])
def test_unknown_action_is_a_contract_error(skill, compile_params, action):
    params = dict(compile_params)
    if action is None:
        del params["action"]
    else:
        params["action"] = action
    _assert_error(skill.execute(params), INVALID_REQUEST, "unknown_action")


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

bundle = SkillLoader.load_skill("office/outreach_manager")
skill = bundle["class"]()
digests = []
for path in json.loads(sys.argv[1]):
    with open(path, "r", encoding="utf-8") as handle:
        params = json.load(handle)
    cases = params if isinstance(params, list) else [params]
    results = [skill.execute(case) for case in cases]
    text = json.dumps(results, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
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
        results = _cases(_load_json(FIXTURES_DIR, expected_name))
        expected.append(hashlib.sha256(_canonical(results).encode("utf-8")).hexdigest())
    assert outputs[0] == expected


# --------------------------------------------------------------------------
# Source checks (offline, stateless, standard library only)
# --------------------------------------------------------------------------

ALLOWED_IMPORTS = {"csv", "hashlib", "io", "json", "os", "re", "unicodedata", "yaml"}
ALLOWED_FROM_IMPORTS = {
    "datetime": {"date"},
    "typing": None,
    "urllib.parse": {"quote", "urlsplit"},
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
}
FORBIDDEN_NAMES = {"hash", "eval", "exec", "compile", "__import__", "input"}


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
    # The allow-list proves itself: the urllib root and every other submodule
    # are rejected, and only the two named helpers of urllib.parse pass.
    assert _disallowed_imports("import urllib\n") == ["urllib"]
    assert _disallowed_imports("import urllib.parse\n") == ["urllib.parse"]
    assert _disallowed_imports("from urllib.request import urlopen\n") == [
        "urllib.request"
    ]
    assert _disallowed_imports("from urllib import parse\n") == ["urllib"]
    assert _disallowed_imports("from urllib.parse import urlencode\n") == [
        "urllib.parse.urlencode"
    ]
    assert _disallowed_imports("from urllib.parse import quote, urlsplit\n") == []
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
            if node.func.id != "open":
                continue
            open_calls += 1
            modes = [arg for arg in node.args[1:2]]
            modes += [kw.value for kw in node.keywords if kw.arg == "mode"]
            for mode in modes:
                assert isinstance(mode, ast.Constant) and mode.value == "r"
    assert open_calls == len(BUNDLE_READS)
    for name in ("socket", "http", "urllib", "requests", "ssl"):
        assert not hasattr(skill_module, name)


# --------------------------------------------------------------------------
# Error registry, per-code cases, documented order
# --------------------------------------------------------------------------


def test_error_registry_is_closed_and_final():
    assert ERROR_REGISTRY == frozenset(
        {
            "INVALID_REQUEST",
            "LIMIT_EXCEEDED",
            "UNKNOWN_PLATFORM",
            "INVALID_PROFILE",
            "INVALID_TARGET_SPEC",
            "INVALID_PLAN",
            "PLAN_MISMATCH",
            "INVALID_EXISTING_LEADS",
            "INVALID_ROWS",
            "UNKNOWN_SOURCE_QUERY",
        }
    )
    assert FAULT_REGISTRY == frozenset({"INTERNAL_ERROR"})
    assert INTERNAL_ERROR not in ERROR_REGISTRY
    assert "UNKNOWN_ACTION" not in ERROR_REGISTRY
    assert {code for code, _ in COMPILE_SEARCH_CHECKS} <= ERROR_REGISTRY
    assert {code for code, _ in INGEST_LEADS_CHECKS} <= ERROR_REGISTRY
    assert len(COMPILE_SEARCH_CHECKS) == 12
    assert len(INGEST_LEADS_CHECKS) == 18


def _bad_override(override):
    broken = copy.deepcopy(override)
    del broken["sources"]
    return broken


ERROR_CASES = {
    INVALID_REQUEST: lambda f: {**f["compile"], "action": "bogus"},
    LIMIT_EXCEEDED: lambda f: _compile_params(
        titles=[f"Title {i}" for i in range(LIST_CAPS["titles"] + 1)]
    ),
    UNKNOWN_PLATFORM: lambda f: _compile_params(platform="nowhere"),
    INVALID_PROFILE: lambda f: _compile_params(
        platform="override_demo", override=_bad_override(f["override"])
    ),
    INVALID_TARGET_SPEC: lambda f: _compile_params(titles=[""]),
    INVALID_PLAN: lambda f: _ingest_params(
        {k: v for k, v in f["plan"].items() if k != "criteria"}, [_row()]
    ),
    PLAN_MISMATCH: lambda f: _ingest_params(
        {**f["plan"], "criteria": {**f["plan"]["criteria"], "titles": ["Edited"]}},
        [_row()],
    ),
    INVALID_EXISTING_LEADS: lambda f: _ingest_params(
        f["plan"], [_row()], existing=[{"profile_key": "demo:x"}]
    ),
    INVALID_ROWS: lambda f: _ingest_params(
        f["plan"], [{"profileUrl": "https://demo.example/in/x", "query": "q01"}]
    ),
    UNKNOWN_SOURCE_QUERY: lambda f: _ingest_params(f["plan"], [_row(query="q99")]),
}


@pytest.fixture
def error_inputs(compile_params, example_plan, override):
    return {"compile": compile_params, "plan": example_plan, "override": override}


def test_error_cases_cover_registry():
    assert set(ERROR_CASES) == ERROR_REGISTRY


@pytest.mark.parametrize("code", sorted(ERROR_CASES))
def test_each_error_code(skill, error_inputs, code):
    _assert_error(skill.execute(ERROR_CASES[code](error_inputs)), code)


def _unreachable(reason):
    return reason


def _many_queries_plan(plan, count):
    """A copy of the plan with ``count`` well-formed queries and a stale id."""
    many = copy.deepcopy(plan)
    query = many["queries"][0]
    many["queries"] = []
    for index in range(count):
        copy_query = copy.deepcopy(query)
        copy_query["id"] = "q" + str(index + 1).zfill(2)
        many["queries"].append(copy_query)
    return many


# An exercised pair is (input builder, reason of check i) or, where check i
# has a fixed path, (input builder, reason, path); a string records why no
# single input can violate both checks of the pair.
COMPILE_PAIRS = {
    (1, 2): _unreachable("check 2 needs an object, which check 1 rejects"),
    (2, 3): (
        lambda f: {**f["compile"], "action": "bogus", "bogus_key": 1},
        "unknown_action",
        "action",
    ),
    (3, 4): (
        lambda f: {**f["compile"], "platform": "Bad Platform", "bogus_key": 1},
        "unknown_key",
    ),
    (4, 5): (
        lambda f: {"action": "compile_search", "platform": "Bad Platform"},
        "invalid_platform",
        "platform",
    ),
    (5, 6): (
        lambda f: {
            "action": "compile_search",
            "platform": "override_demo",
            "target_spec": "not an object",
            "profile_override": _bad_override(f["override"]),
        },
        "invalid_type",
        "target_spec",
    ),
    (6, 7): _unreachable(
        "check 7 applies only without an override, which check 6 needs"
    ),
    (7, 8): _unreachable(
        "check 8 needs a bundled profile, whose existence check 7 denies"
    ),
    (8, 9): _unreachable(
        "no request input can make a bundled profile file fail its checks"
    ),
    (9, 10): (
        lambda f: _compile_params(
            bogus=1, titles=[f"Title {i}" for i in range(LIST_CAPS["titles"] + 1)]
        ),
        "unknown_key",
        "target_spec",
    ),
    (10, 11): (
        lambda f: _compile_params(titles=["x" * (MAX_TERM_CHARS + 1), ""]),
        "size_cap",
        "target_spec.titles[0]",
    ),
    (11, 12): (
        lambda f: _compile_params(
            titles=[""],
            geos=_geo_codes(LIST_CAPS["geos"]),
            company_size=_load_profile("demo")["filters"]["company_size"]["values"],
            estimated_matches=MAX_ESTIMATED_MATCHES,
        ),
        "empty_term",
        "target_spec.titles[0]",
    ),
}

INGEST_PAIRS = {
    (1, 2): _unreachable("check 2 needs an object, which check 1 rejects"),
    (2, 3): (
        lambda f: {**f["ingest"], "action": "bogus", "bogus_key": 1},
        "unknown_action",
        "action",
    ),
    (3, 4): (
        lambda f: {**f["ingest"], "platform": "Bad Platform", "bogus_key": 1},
        "unknown_key",
    ),
    (4, 5): (
        lambda f: {"action": "ingest_leads", "platform": "Bad Platform"},
        "invalid_platform",
        "platform",
    ),
    (5, 6): (
        lambda f: {
            "action": "ingest_leads",
            "platform": "override_demo",
            "plan": "not an object",
            "rows": [],
            "profile_override": _bad_override(f["override"]),
        },
        "invalid_type",
        "plan",
    ),
    (6, 7): _unreachable(
        "check 7 applies only without an override, which check 6 needs"
    ),
    (7, 8): _unreachable(
        "check 8 needs a bundled profile, whose existence check 7 denies"
    ),
    (8, 9): _unreachable(
        "no request input can make a bundled profile file fail its checks"
    ),
    (9, 10): (
        lambda f: _ingest_params(
            {**_many_queries_plan(f["plan"], MAX_QUERIES + 1), "bogus": 1}, [_row()]
        ),
        "unknown_key",
        "plan",
    ),
    (10, 11): (
        lambda f: _ingest_params(
            _many_queries_plan(f["plan"], MAX_QUERIES + 1), [_row()]
        ),
        "size_cap",
        "plan.queries",
    ),
    (11, 12): (
        lambda f: _ingest_params(
            {**f["plan"], "criteria": {**f["plan"]["criteria"], "titles": ["Edited"]}},
            [_row()],
            existing=[
                {"profile_key": f"demo:k{i}", "lead_id": "L1"}
                for i in range(MAX_EXISTING_LEADS + 1)
            ],
        ),
        "plan_id_mismatch",
        "plan.plan_id",
    ),
    (12, 13): (
        lambda f: _ingest_params(
            f["plan"],
            [_row()],
            existing=[{"profile_key": "demo:k", "lead_id": "L1"}] * MAX_EXISTING_LEADS
            + ["not an object"],
        ),
        "size_cap",
        "existing_leads",
    ),
    (13, 14): (
        lambda f: _ingest_params(
            f["plan"], [_row()] * (MAX_ROWS + 1), existing=[{"bad": 1}]
        ),
        "missing_or_not_a_string",
        "existing_leads[0].profile_key",
    ),
    (14, 15): (
        lambda f: _ingest_params(f["plan"], ["not an object"] * (MAX_ROWS + 1)),
        "size_cap",
        "rows",
    ),
    (15, 16): (
        lambda f: _ingest_params(
            f["plan"],
            ["not an object", _row(**{f"k{i}": "v" for i in range(MAX_ROW_KEYS)})],
        ),
        "row_not_object",
        "rows[0]",
    ),
    (16, 17): (
        lambda f: _ingest_params(
            f["plan"],
            [
                {
                    "profileUrl": "https://demo.example/in/lead-0500",
                    "query": "q01",
                    **{f"k{i}": "v" for i in range(MAX_ROW_KEYS)},
                }
            ],
        ),
        "size_cap",
        "rows[0]",
    ),
    (17, 18): (
        lambda f: _ingest_params(
            f["plan"], [_row(url="https://elsewhere.example/in/x", query="q99")]
        ),
        "invalid_profile_url",
        "rows[0]",
    ),
}


def _pair_cases():
    cases = []
    for action, table, checks in (
        ("compile_search", COMPILE_PAIRS, COMPILE_SEARCH_CHECKS),
        ("ingest_leads", INGEST_PAIRS, INGEST_LEADS_CHECKS),
    ):
        for (first, second), entry in sorted(table.items()):
            cases.append(
                pytest.param(
                    action, first, second, entry, id=f"{action}-{first}-{second}"
                )
            )
    return cases


def test_pairwise_table_covers_every_adjacent_pair():
    for table, checks in (
        (COMPILE_PAIRS, COMPILE_SEARCH_CHECKS),
        (INGEST_PAIRS, INGEST_LEADS_CHECKS),
    ):
        expected = {(i, i + 1) for i in range(1, len(checks))}
        assert set(table) == expected
        for entry in table.values():
            if isinstance(entry, str):
                assert entry
                continue
            builder, reason, *path = entry
            assert callable(builder)
            assert isinstance(reason, str) and reason
            assert len(path) <= 1
            assert all(isinstance(item, str) and item for item in path)


@pytest.fixture
def pair_inputs(compile_params, ingest_params, example_plan, override):
    return {
        "compile": compile_params,
        "ingest": ingest_params,
        "plan": example_plan,
        "override": override,
    }


@pytest.mark.parametrize("action,first,second,entry", _pair_cases())
def test_first_violation_wins_in_documented_order(
    skill, pair_inputs, action, first, second, entry
):
    checks = (
        COMPILE_SEARCH_CHECKS if action == "compile_search" else INGEST_LEADS_CHECKS
    )
    if isinstance(entry, str):
        assert entry  # recorded reason: no single input violates both checks
        return
    builder, reason, *path = entry
    detail = _assert_error(
        skill.execute(builder(pair_inputs)), checks[first - 1][0], reason
    )
    if path:
        assert detail["path"] == path[0]


def test_internal_fault_returns_an_error_envelope(skill, monkeypatch, compile_params):
    def boom(params):
        raise RuntimeError("synthetic fault")

    monkeypatch.setattr(skill_module, "_compile_search", boom)
    result = skill.execute(compile_params)
    assert result == {
        "status": "error",
        "error": {
            "code": INTERNAL_ERROR,
            "detail": {"reason": "internal_error", "exception": "RuntimeError"},
        },
    }

    def interrupt(params):
        raise KeyboardInterrupt()

    monkeypatch.setattr(skill_module, "_ingest_leads", interrupt)
    with pytest.raises(KeyboardInterrupt):
        skill.execute({"action": "ingest_leads", "platform": "demo"})


def test_error_envelope_and_completed_run_never_mix(
    skill, compile_params, ingest_params
):
    for params in (compile_params, ingest_params):
        completed = skill.execute(params)
        assert completed["status"] == "completed"
        assert "error" not in completed
    errored = skill.execute({**compile_params, "platform": "nowhere"})
    assert set(errored) == {"status", "error"}
    assert errored["error"]["code"] in ERROR_REGISTRY


@pytest.mark.parametrize(
    "mutate,code",
    [
        (lambda p: p["target_spec"].update(schema_version=True), INVALID_TARGET_SPEC),
        (lambda p: p["target_spec"].update(estimated_matches="5"), INVALID_TARGET_SPEC),
        (
            lambda p: p["target_spec"].update(estimated_matches=True),
            INVALID_TARGET_SPEC,
        ),
        (lambda p: p["target_spec"].update(estimated_matches=2.0), INVALID_TARGET_SPEC),
        (
            lambda p: p["target_spec"].update(titles="Head of Operations"),
            INVALID_TARGET_SPEC,
        ),
        (lambda p: p["target_spec"].update(titles=["Head", 1]), INVALID_TARGET_SPEC),
        (lambda p: p.update(platform=1), INVALID_REQUEST),
        (lambda p: p.update(target_spec=None), INVALID_REQUEST),
        (lambda p: p.update(target_spec=[]), INVALID_REQUEST),
        (lambda p: p.update(profile_override=None), INVALID_REQUEST),
    ],
)
def test_type_rules_are_strict(skill, compile_params, mutate, code):
    params = copy.deepcopy(compile_params)
    mutate(params)
    _assert_error(skill.execute(params), code)


@pytest.mark.parametrize(
    "mutate,code",
    [
        (lambda p: p.update(existing_leads=None), INVALID_REQUEST),
        (lambda p: p.update(rows={}), INVALID_REQUEST),
        (lambda p: p["plan"]["queries"][0].update(expected_cap=True), INVALID_PLAN),
        (lambda p: p["plan"]["queries"][0].update(expected_cap="1000"), INVALID_PLAN),
        (lambda p: p["plan"]["partitioning"].update(cap_risk=0), INVALID_PLAN),
        (lambda p: p["plan"]["pacing"].update(per_run_max_queries=7.0), INVALID_PLAN),
        (
            lambda p: p["rows"].append({"profileUrl": 5, "title": "x", "query": "q01"}),
            INVALID_ROWS,
        ),
        (lambda p: p["rows"].append({**p["rows"][0], "company": None}), INVALID_ROWS),
    ],
)
def test_ingest_type_rules_are_strict(skill, ingest_params, mutate, code):
    params = copy.deepcopy(ingest_params)
    mutate(params)
    _assert_error(skill.execute(params), code)


# --------------------------------------------------------------------------
# Check order within target_spec, the string rule, whole-string patterns and
# crafted plans
# --------------------------------------------------------------------------


def test_empty_titles_is_rejected(skill):
    detail = _assert_error(
        skill.execute(_compile_params(titles=[])), INVALID_TARGET_SPEC, "empty_titles"
    )
    assert detail["path"] == "target_spec.titles"
    # The shape check decides before any cap or value rule of a later field.
    _assert_error(
        skill.execute(
            _compile_params(titles=[], geos=_geo_codes(LIST_CAPS["geos"] + 1))
        ),
        INVALID_TARGET_SPEC,
        "empty_titles",
    )
    assert skill.execute(_compile_params(titles=["Zed"]))["status"] == "completed"


@pytest.mark.parametrize(
    "spec,code,reason,path",
    [
        (
            {"titles": [""], "estimated_matches": 2 * MAX_ESTIMATED_MATCHES},
            LIMIT_EXCEEDED,
            "size_cap",
            "target_spec.estimated_matches",
        ),
        (
            {
                "titles": [" "],
                "keywords_include": [
                    f"kw {i}" for i in range(LIST_CAPS["keywords_include"] + 1)
                ],
            },
            LIMIT_EXCEEDED,
            "size_cap",
            "target_spec.keywords_include",
        ),
        (
            {
                "titles": ["x" * (MAX_TERM_CHARS + 1)],
                "geos": _geo_codes(LIST_CAPS["geos"] + 1),
            },
            LIMIT_EXCEEDED,
            "size_cap",
            "target_spec.geos",
        ),
        (
            {
                "titles": ["Zed"],
                "geos": ["bad"],
                "estimated_matches": 2 * MAX_ESTIMATED_MATCHES,
            },
            LIMIT_EXCEEDED,
            "size_cap",
            "target_spec.estimated_matches",
        ),
        (
            {"titles": ["", "x" * (MAX_TERM_CHARS + 1)]},
            INVALID_TARGET_SPEC,
            "empty_term",
            "target_spec.titles[0]",
        ),
        (
            {"titles": ["x" * (MAX_TERM_CHARS + 1), ""]},
            LIMIT_EXCEEDED,
            "size_cap",
            "target_spec.titles[0]",
        ),
        (
            {"titles": ["Zed", ""], "geos": ["bad"]},
            INVALID_TARGET_SPEC,
            "empty_term",
            "target_spec.titles[1]",
        ),
        (
            {"titles": [""], "estimated_matches": -1},
            INVALID_TARGET_SPEC,
            "empty_term",
            "target_spec.titles[0]",
        ),
        (
            {"titles": ["Zed"], "estimated_matches": -1},
            INVALID_TARGET_SPEC,
            "negative_estimated_matches",
            "target_spec.estimated_matches",
        ),
    ],
)
def test_list_caps_precede_value_checks(skill, spec, code, reason, path):
    detail = _assert_error(skill.execute(_compile_params(**spec)), code, reason)
    assert detail["path"] == path


def test_target_spec_check_order_is_documented():
    section = _flat(
        _section(_instructions(), "## Validation order (fail-closed, deterministic)")
    )
    for phrase in (
        "each list longer than its cap",
        "then `estimated_matches` above 10,000,000",
        "every rule of one item runs before the next item",
        "`estimated_matches` below 0",
    ):
        assert phrase in section, phrase


LONE_SURROGATE = "\ud800"


def _with_recomputed_plan_id(plan):
    """``plan`` with ``plan_id`` recomputed from its body, whatever the body holds."""
    body = {key: value for key, value in plan.items() if key != "plan_id"}
    limit = sys.get_int_max_str_digits()
    sys.set_int_max_str_digits(0)
    try:
        plan_id = skill_module._plan_id(body)
    finally:
        sys.set_int_max_str_digits(limit)
    return {"plan_id": plan_id, **body}


def _plan_with(plan, mutate):
    edited = copy.deepcopy(plan)
    mutate(edited)
    return _with_recomputed_plan_id(edited)


def _override_source_title(override, title):
    return _override_with(
        override, "sources", [{**override["sources"][0], "title": title}]
    )


SURROGATE_CASES = {
    "platform": lambda f: (
        _compile_params(platform="demo" + LONE_SURROGATE),
        INVALID_REQUEST,
    ),
    "target_spec.titles[]": lambda f: (
        _compile_params(titles=["Head" + LONE_SURROGATE]),
        INVALID_TARGET_SPEC,
    ),
    "target_spec.geos[]": lambda f: (
        _compile_params(geos=["D" + LONE_SURROGATE]),
        INVALID_TARGET_SPEC,
    ),
    "override.search.url.fixed_params[].value": lambda f: (
        _compile_params(
            platform="override_demo",
            override=_override_with(
                f["override"],
                "search.url.fixed_params",
                [{"name": "mode", "value": "people" + LONE_SURROGATE}],
            ),
        ),
        INVALID_PROFILE,
    ),
    "override.search.grammar.and": lambda f: (
        _compile_params(
            platform="override_demo",
            override=_override_with(
                _override_with(
                    f["override"], "search.grammar.and", "AND" + LONE_SURROGATE
                ),
                "search.grammar.reserved_words",
                ["AND" + LONE_SURROGATE, "OR", "NOT"],
            ),
        ),
        INVALID_PROFILE,
    ),
    "override.sources[].title": lambda f: (
        _compile_params(
            platform="override_demo",
            override=_override_source_title(
                f["override"], "Synthetic" + LONE_SURROGATE
            ),
        ),
        INVALID_PROFILE,
    ),
    "override.filters.company_size.values[]": lambda f: (
        _compile_params(
            platform="override_demo",
            override=_override_with(
                f["override"],
                "filters.company_size.values",
                ["1-50", "51-500" + LONE_SURROGATE],
            ),
        ),
        INVALID_PROFILE,
    ),
    "plan.queries[].keywords": lambda f: (
        _ingest_params(
            _plan_with(
                f["plan"],
                lambda p: p["queries"][0].update(keywords="x" + LONE_SURROGATE),
            ),
            [_row()],
        ),
        INVALID_PLAN,
    ),
    "plan.queries[].partition.geo": lambda f: (
        _ingest_params(
            _plan_with(
                f["plan"],
                lambda p: p["queries"][0]["partition"].update(geo="D" + LONE_SURROGATE),
            ),
            [_row()],
        ),
        INVALID_PLAN,
    ),
    "plan.criteria.titles[]": lambda f: (
        _ingest_params(
            _plan_with(
                f["plan"],
                lambda p: p["criteria"].update(titles=["Head" + LONE_SURROGATE]),
            ),
            [_row()],
        ),
        INVALID_PLAN,
    ),
    "existing_leads[].lead_id": lambda f: (
        _ingest_params(
            f["plan"],
            [_row()],
            existing=[{"profile_key": "demo:a", "lead_id": "L1" + LONE_SURROGATE}],
        ),
        INVALID_EXISTING_LEADS,
    ),
    "existing_leads[].profile_key": lambda f: (
        _ingest_params(
            f["plan"],
            [_row()],
            existing=[{"profile_key": "demo:a" + LONE_SURROGATE, "lead_id": "L1"}],
        ),
        INVALID_EXISTING_LEADS,
    ),
    "rows[].title": lambda f: (
        _ingest_params(f["plan"], [_row(title="Head" + LONE_SURROGATE)]),
        INVALID_ROWS,
    ),
    "rows[].profileUrl": lambda f: (
        _ingest_params(
            f["plan"], [_row(url="https://demo.example/in/x" + LONE_SURROGATE)]
        ),
        INVALID_ROWS,
    ),
    "rows[].query": lambda f: (
        _ingest_params(f["plan"], [_row(query="q01" + LONE_SURROGATE)]),
        INVALID_ROWS,
    ),
    "rows[].company": lambda f: (
        _ingest_params(f["plan"], [_row(company="Demo" + LONE_SURROGATE)]),
        INVALID_ROWS,
    ),
    # An unmapped column is never read, so its content cannot fail a check.
    "rows[] unmapped column": lambda f: (
        _ingest_params(f["plan"], [_row(note="x" + LONE_SURROGATE)]),
        None,
    ),
}


@pytest.mark.parametrize("field", sorted(SURROGATE_CASES))
def test_lone_surrogates_are_contract_errors(skill, error_inputs, field):
    params, code = SURROGATE_CASES[field](error_inputs)
    result = skill.execute(params)
    if code is None:
        assert result["status"] == "completed", result
        return
    _assert_error(result, code)
    assert LONE_SURROGATE not in json.dumps(result, ensure_ascii=False)


LINE_FEED_CASES = {
    "platform": lambda f: (
        _compile_params(platform="demo\n"),
        INVALID_REQUEST,
        "invalid_platform",
    ),
    "target_spec.geos[]": lambda f: (
        _compile_params(geos=["US\n"]),
        INVALID_TARGET_SPEC,
        "invalid_geo",
    ),
    "target_spec.languages[]": lambda f: (
        _compile_params(languages=["en\n"]),
        INVALID_TARGET_SPEC,
        "invalid_language",
    ),
    "override.platform": lambda f: (
        _compile_params(
            platform="override_demo",
            override=_override_with(f["override"], "platform", "override_demo\n"),
        ),
        INVALID_PROFILE,
        "invalid_platform",
    ),
    "override.search.url.base": lambda f: (
        _compile_params(
            platform="override_demo",
            override=_override_with(
                f["override"],
                "search.url.base",
                "https://people.override.example/search/\n",
            ),
        ),
        INVALID_PROFILE,
        "invalid_url",
    ),
    "override.search.url.keywords_param": lambda f: (
        _compile_params(
            platform="override_demo",
            override=_override_with(f["override"], "search.url.keywords_param", "q\n"),
        ),
        INVALID_PROFILE,
        "invalid_parameter_name",
    ),
    "override.filters.geo.filter_id": lambda f: (
        _compile_params(
            platform="override_demo",
            override=_override_with(f["override"], "filters.geo.filter_id", "region\n"),
        ),
        INVALID_PROFILE,
        "invalid_parameter_name",
    ),
    "override.search.url.fixed_params[].name": lambda f: (
        _compile_params(
            platform="override_demo",
            override=_override_with(
                f["override"],
                "search.url.fixed_params",
                [{"name": "mode\n", "value": "people"}],
            ),
        ),
        INVALID_PROFILE,
        "invalid_parameter_name",
    ),
    "override base ending in a percent sign": lambda f: (
        _compile_params(
            platform="override_demo",
            override=_override_with(
                f["override"],
                "search.url.base",
                "https://people.override.example/search/%",
            ),
        ),
        INVALID_PROFILE,
        "invalid_url",
    ),
    "override base with a non-hexadecimal escape": lambda f: (
        _compile_params(
            platform="override_demo",
            override=_override_with(
                f["override"],
                "search.url.base",
                "https://people.override.example/search/%zz/",
            ),
        ),
        INVALID_PROFILE,
        "invalid_url",
    ),
    # A percent sign followed by two hexadecimal digits is a valid escape.
    "override base with a hexadecimal escape": lambda f: (
        _compile_params(
            platform="override_demo",
            override=_override_with(
                f["override"],
                "search.url.base",
                "https://people.override.example/search/%7Ex/",
            ),
        ),
        None,
        None,
    ),
}


@pytest.mark.parametrize("field", sorted(LINE_FEED_CASES))
def test_patterns_reject_a_trailing_line_feed(skill, error_inputs, field):
    params, code, reason = LINE_FEED_CASES[field](error_inputs)
    result = skill.execute(params)
    if code is None:
        assert result["status"] == "completed", result
        return
    _assert_error(result, code, reason)


CRAFTED_PLAN_MUTATIONS = {
    "unknown-seniority": lambda p: p["criteria"].update(seniority=["boss"]),
    "non-list-criteria": lambda p: p["criteria"].update(titles="Head of Operations"),
    "non-string-criteria-item": lambda p: p["criteria"].update(geos=["DE", 2]),
    "criteria-list-over-cap": lambda p: p["criteria"].update(
        titles=[f"T{i}" for i in range(LIST_CAPS["titles"] + 1)]
    ),
    "criteria-term-over-cap": lambda p: p["criteria"].update(
        keywords_exclude=["x" * (MAX_TERM_CHARS + 1)]
    ),
    "criteria-term-too-many-tokens": lambda p: p["criteria"].update(
        industries=[" ".join(f"w{i}" for i in range(MAX_TERM_TOKENS + 1))]
    ),
    "huge-expected-cap": lambda p: p["queries"][0].update(expected_cap=10**5000),
    "huge-pacing": lambda p: p["pacing"].update(per_day_max_rows=10**5000),
    "huge-estimate": lambda p: p["partitioning"].update(estimated_matches=10**5000),
    "negative-expected-cap": lambda p: p["queries"][0].update(expected_cap=-1),
    "negative-pacing": lambda p: p["pacing"].update(per_run_max_queries=-1),
    "negative-estimate": lambda p: p["partitioning"].update(estimated_matches=-1),
    "boolean-expected-cap": lambda p: p["queries"][0].update(expected_cap=True),
    "boolean-pacing": lambda p: p["pacing"].update(per_run_max_queries=False),
    "boolean-estimate": lambda p: p["partitioning"].update(estimated_matches=True),
    "integer-cap-risk": lambda p: p["partitioning"].update(cap_risk=1),
    "unknown-filter-dimension": lambda p: p["queries"][0]["filters"].update(
        bogus=["x"]
    ),
    "non-string-filter-value": lambda p: p["queries"][0]["filters"].update(geo=[1]),
    "non-list-filter-value": lambda p: p["queries"][0]["filters"].update(geo="DE"),
    "unknown-partition-dimension": lambda p: p["queries"][0]["partition"].update(
        bogus="x"
    ),
    "non-string-partition-value": lambda p: p["queries"][0]["partition"].update(geo=1),
    "non-string-query-id": lambda p: p["queries"][0].update(id=1),
    "nul-query-id": lambda p: p["queries"][0].update(id="q\x0001"),
    "nul-criteria-term": lambda p: p["criteria"].update(
        keywords_exclude=["recruiter\x00"]
    ),
}


@pytest.mark.parametrize("mutation", sorted(CRAFTED_PLAN_MUTATIONS))
def test_crafted_plans_never_reach_internal_error(skill, example_plan, mutation):
    plan = _plan_with(example_plan, CRAFTED_PLAN_MUTATIONS[mutation])
    result = skill.execute(_ingest_params(plan, [_row()]))
    assert result["status"] == "error", result
    assert result["error"]["code"] != INTERNAL_ERROR, result
    _assert_error(result, INVALID_PLAN)


# Ids that are strings but not `q` plus the query's one-based position, two
# digits minimum; each replaces the first query's id.
QUERY_ID_CASES = {
    "formula": "=1+1",
    "carriage-return-formula": "\r=1+1",
    "nul": "q\x0001",
    "profile-url": "https://demo.example/in/lead-0500",
    "one-digit": "q1",
    "upper-case": "Q01",
}


@pytest.mark.parametrize("case", sorted(QUERY_ID_CASES) + ["duplicate", "swapped"])
def test_query_ids_must_follow_the_documented_form(skill, example_plan, case):
    def mutate(plan):
        first, second = plan["queries"][0], plan["queries"][1]
        if case == "duplicate":
            second["id"] = first["id"]
        elif case == "swapped":
            first["id"], second["id"] = second["id"], first["id"]
        else:
            first["id"] = QUERY_ID_CASES[case]

    plan = _plan_with(example_plan, mutate)
    # The row names the crafted id, so a plan that passed would echo it.
    row = _row(query=plan["queries"][0]["id"])
    result = skill.execute(_ingest_params(plan, [row]))
    detail = _assert_error(result, INVALID_PLAN, "invalid_query_id")
    position = 1 if case == "duplicate" else 0
    assert detail["path"] == f"plan.queries[{position}].id"
    assert "review_table" not in result


# (criteria field, term, a row title the term would match, expected reason);
# a reason of None means the term passes the shape check.
CRITERIA_TERM_CASES = {
    "carriage-return": (
        "keywords_exclude",
        "x\r=1+1",
        "x 1 1",
        "term_not_sanitised",
    ),
    "nul": (
        "keywords_exclude",
        "recruiter\x00",
        "Technical Recruiter",
        "term_not_sanitised",
    ),
    "forbidden-characters": ("titles", 'x"(y)', "x y", "term_not_sanitised"),
    "leading-space": (
        "titles",
        " Head of Operations",
        "Head of Operations",
        "term_not_sanitised",
    ),
    "double-space": (
        "industries",
        "ware  housing",
        "Operations Manager",
        "term_not_sanitised",
    ),
    "empty": ("keywords_include", "", "Operations Manager", "term_not_sanitised"),
    "formula-segment": ("keywords_exclude", "x;=1+1", "x 1 1", None),
}


@pytest.mark.parametrize("case", sorted(CRITERIA_TERM_CASES))
def test_criteria_terms_must_be_in_sanitised_form(skill, example_plan, case):
    field, term, title, reason = CRITERIA_TERM_CASES[case]
    plan = _plan_with(example_plan, lambda p: p["criteria"].update({field: [term]}))
    result = skill.execute(_ingest_params(plan, [_row(title=title)]))
    if reason is None:
        assert result["status"] == "completed", result
        return
    detail = _assert_error(result, INVALID_PLAN, reason)
    assert detail["path"] == f"plan.criteria.{field}[0]"
    assert "review_table" not in result


# --------------------------------------------------------------------------
# Size caps
# --------------------------------------------------------------------------


def _override_with(override, path, value):
    """Return a copy of ``override`` with the dotted ``path`` set to ``value``."""
    copied = copy.deepcopy(override)
    target = copied
    parts = path.split(".")
    for part in parts[:-1]:
        target = target[part]
    target[parts[-1]] = value
    return copied


def _list_cap_case(field):
    def build(f):
        values = {
            "titles": [f"Title {i}" for i in range(LIST_CAPS[field] + 1)],
            "keywords_include": [f"kw {i}" for i in range(LIST_CAPS[field] + 1)],
            "keywords_exclude": [f"kx {i}" for i in range(LIST_CAPS[field] + 1)],
            "industries": [f"ind {i}" for i in range(LIST_CAPS[field] + 1)],
            "seniority": ["director"] * (LIST_CAPS[field] + 1),
            "geos": _geo_codes(LIST_CAPS[field] + 1),
            "languages": ["en"] * (LIST_CAPS[field] + 1),
            "company_size": ["51-200"] * (LIST_CAPS[field] + 1),
            "network": ["S"] * (LIST_CAPS[field] + 1),
        }[field]
        platform = "linkedin" if field == "network" else "demo"
        return (
            _compile_params(platform=platform, **{field: values}),
            LIMIT_EXCEEDED,
            LIST_CAPS[field],
        )

    return build


def _rows_total_chars_case(f):
    cell = "x" * MAX_CELL_CHARS
    count = MAX_TOTAL_CELL_CHARS // MAX_CELL_CHARS + 1
    rows = [
        _row(url=f"https://demo.example/in/lead-{i}", note=cell) for i in range(count)
    ]
    return _ingest_params(f["plan"], rows), LIMIT_EXCEEDED, MAX_TOTAL_CELL_CHARS


SIZE_CAP_CASES = {
    ("target_spec.titles", "entries"): _list_cap_case("titles"),
    ("target_spec.keywords_include", "entries"): _list_cap_case("keywords_include"),
    ("target_spec.keywords_exclude", "entries"): _list_cap_case("keywords_exclude"),
    ("target_spec.industries", "entries"): _list_cap_case("industries"),
    ("target_spec.seniority", "entries"): _list_cap_case("seniority"),
    ("target_spec.geos", "entries"): _list_cap_case("geos"),
    ("target_spec.languages", "entries"): _list_cap_case("languages"),
    ("target_spec.company_size", "entries"): _list_cap_case("company_size"),
    ("target_spec.network", "entries"): _list_cap_case("network"),
    ("term", "characters"): lambda f: (
        _compile_params(titles=["x" * (MAX_TERM_CHARS + 1)]),
        LIMIT_EXCEEDED,
        MAX_TERM_CHARS,
    ),
    ("term", "tokens"): lambda f: (
        _compile_params(
            titles=[" ".join("w" + str(i) for i in range(MAX_TERM_TOKENS + 1))]
        ),
        LIMIT_EXCEEDED,
        MAX_TERM_TOKENS,
    ),
    ("target_spec.estimated_matches", "value"): lambda f: (
        _compile_params(estimated_matches=MAX_ESTIMATED_MATCHES + 1),
        LIMIT_EXCEEDED,
        MAX_ESTIMATED_MATCHES,
    ),
    ("plan.queries", "entries"): lambda f: (
        _compile_params(
            geos=_geo_codes(LIST_CAPS["geos"]),
            company_size=_load_profile("demo")["filters"]["company_size"]["values"],
            estimated_matches=MAX_ESTIMATED_MATCHES,
        ),
        LIMIT_EXCEEDED,
        MAX_QUERIES,
    ),
    ("plan.queries[].keywords", "characters"): lambda f: (
        _compile_params(
            platform="override_demo",
            override=_override_with(f["override"], "search.max_query_length", 10),
        ),
        LIMIT_EXCEEDED,
        10,
    ),
    ("plan.queries[].search_url", "characters"): lambda f: (
        _compile_params(
            platform="override_demo",
            override=_override_with(f["override"], "search.url.max_length", 60),
        ),
        LIMIT_EXCEEDED,
        60,
    ),
    ("rows", "entries"): lambda f: (
        _ingest_params(f["plan"], [_row()] * (MAX_ROWS + 1)),
        LIMIT_EXCEEDED,
        MAX_ROWS,
    ),
    ("rows[]", "keys"): lambda f: (
        _ingest_params(
            f["plan"], [_row(**{f"k{i}": "v" for i in range(MAX_ROW_KEYS)})]
        ),
        LIMIT_EXCEEDED,
        MAX_ROW_KEYS,
    ),
    ("rows[] cell", "characters"): lambda f: (
        _ingest_params(f["plan"], [_row(note="x" * (MAX_CELL_CHARS + 1))]),
        LIMIT_EXCEEDED,
        MAX_CELL_CHARS,
    ),
    ("rows (all cells together)", "characters"): _rows_total_chars_case,
    ("existing_leads", "entries"): lambda f: (
        _ingest_params(
            f["plan"],
            [_row()],
            existing=[{"profile_key": "demo:k", "lead_id": "L1"}]
            * (MAX_EXISTING_LEADS + 1),
        ),
        LIMIT_EXCEEDED,
        MAX_EXISTING_LEADS,
    ),
    ("scored text cell", "characters read"): lambda f: (
        _ingest_params(
            f["plan"],
            [_row(title="x" * SCORING_TEXT_CHARS + " Head of Operations")],
        ),
        None,
        SCORING_TEXT_CHARS,
    ),
    ("profile sources", "entries"): lambda f: (
        _compile_params(
            platform="override_demo",
            override=_override_with(
                f["override"],
                "sources",
                f["override"]["sources"] * (MAX_PROFILE_SOURCES + 1),
            ),
        ),
        INVALID_PROFILE,
        MAX_PROFILE_SOURCES,
    ),
    ("profile search.url.base", "characters"): lambda f: (
        _compile_params(
            platform="override_demo",
            override=_override_with(
                f["override"],
                "search.url.base",
                "https://people.override.example/" + "a" * MAX_BASE_URL_CHARS + "/",
            ),
        ),
        INVALID_PROFILE,
        MAX_BASE_URL_CHARS,
    ),
    ("profile search.url.fixed_params", "entries"): lambda f: (
        _compile_params(
            platform="override_demo",
            override=_override_with(
                f["override"],
                "search.url.fixed_params",
                [{"name": f"p{i}", "value": "v"} for i in range(MAX_FIXED_PARAMS + 1)],
            ),
        ),
        INVALID_PROFILE,
        MAX_FIXED_PARAMS,
    ),
    ("profile search.url.fixed_params[].value", "characters"): lambda f: (
        _compile_params(
            platform="override_demo",
            override=_override_with(
                f["override"],
                "search.url.fixed_params",
                [{"name": "mode", "value": "v" * (MAX_FIXED_PARAM_VALUE_CHARS + 1)}],
            ),
        ),
        INVALID_PROFILE,
        MAX_FIXED_PARAM_VALUE_CHARS,
    ),
    ("profile search.url.max_length", "value"): lambda f: (
        _compile_params(
            platform="override_demo",
            override=_override_with(
                f["override"],
                "search.url.max_length",
                MAX_SEARCH_URL_LENGTH_SETTING + 1,
            ),
        ),
        INVALID_PROFILE,
        MAX_SEARCH_URL_LENGTH_SETTING,
    ),
    ("profile text field", "characters"): lambda f: (
        _compile_params(
            platform="override_demo",
            override=_override_with(
                f["override"],
                "sources",
                [
                    {
                        **f["override"]["sources"][0],
                        "title": "t" * (MAX_PROFILE_TEXT_CHARS + 1),
                    }
                ],
            ),
        ),
        INVALID_PROFILE,
        MAX_PROFILE_TEXT_CHARS,
    ),
    ("profile list field", "entries"): lambda f: (
        _compile_params(
            platform="override_demo",
            override=_override_with(
                f["override"],
                "filters.company_size.values",
                [f"s{i}" for i in range(MAX_PROFILE_LIST_ENTRIES + 1)],
            ),
        ),
        INVALID_PROFILE,
        MAX_PROFILE_LIST_ENTRIES,
    ),
    ("profile integer field", "value"): lambda f: (
        _compile_params(
            platform="override_demo",
            override=_override_with(
                f["override"], "search.per_query_result_cap", MAX_PROFILE_INTEGER + 1
            ),
        ),
        INVALID_PROFILE,
        MAX_PROFILE_INTEGER,
    ),
}


def test_every_cap_has_a_case():
    assert set(SIZE_CAP_CASES) == {(field, unit) for field, unit, _, _ in CAP_TABLE}
    assert len(CAP_TABLE) == len({(field, unit) for field, unit, _, _ in CAP_TABLE})


@pytest.mark.parametrize(
    "field,unit",
    sorted(SIZE_CAP_CASES),
    ids=[f"{f}-{u}" for f, u in sorted(SIZE_CAP_CASES)],
)
def test_size_caps(skill, error_inputs, field, unit):
    params, code, limit = SIZE_CAP_CASES[(field, unit)](error_inputs)
    result = skill.execute(params)
    if code is None:
        assert result["status"] == "completed"
        assert "title_match" not in result["leads"][0]["reasons"]
        return
    detail = _assert_error(result, code)
    assert detail["limit"] == limit
    assert detail["actual"] > limit


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


def _parse_check_list(text, heading):
    checks = []
    for line in _section(text, heading).splitlines():
        match = re.match(r"^(\d+)\. `([A-Z_]+)` — (.*)$", line)
        if match:
            assert int(match.group(1)) == len(checks) + 1
            checks.append((match.group(2), match.group(3)))
    return checks


def test_check_lists_match_instructions():
    text = _instructions()
    assert _parse_check_list(text, "### compile_search") == list(COMPILE_SEARCH_CHECKS)
    assert _parse_check_list(text, "### ingest_leads") == list(INGEST_LEADS_CHECKS)


def test_scoring_reads_only_the_first_200_characters(skill, example_plan):
    inside = (
        "x" * (SCORING_TEXT_CHARS - len(" Head of Operations")) + " Head of Operations"
    )
    lead = _ingest_one(skill, example_plan, _row(title=inside))
    assert "title_match" in lead["reasons"]
    beyond = "x" * SCORING_TEXT_CHARS + " Head of Operations"
    lead = _ingest_one(skill, example_plan, _row(title=beyond))
    assert "title_match" not in lead["reasons"]


# --------------------------------------------------------------------------
# The issue's examples and the other compile_search fixtures
# --------------------------------------------------------------------------


def test_issue_example_compile_search(skill, compile_params):
    result = skill.execute(compile_params)
    assert result == _load_json(FIXTURES_DIR, "expected_compile.json")
    plan = result["plan"]
    profile = _load_profile("demo")
    assert plan["platform"] == "demo"
    assert plan["profile_version"] == "2026-09-29" == profile["profile_version"]
    assert plan["profile_origin"] == "bundled"
    assert re.fullmatch(r"plan_[0-9a-f]{12}", plan["plan_id"])
    assert [q["id"] for q in plan["queries"]] == ["q01", "q02", "q03"]
    assert [q["partition"] for q in plan["queries"]] == [
        {"geo": "DE"},
        {"geo": "NL"},
        {"geo": "BE"},
    ]
    for query, geo, url in zip(plan["queries"], ("DE", "NL", "BE"), ISSUE_SEARCH_URLS):
        assert query["keywords"] == ISSUE_Q01_KEYWORDS
        assert query["filters"] == {"geo": [geo], "company_size": ["51-200", "201-500"]}
        assert query["expected_cap"] == profile["search"]["per_query_result_cap"]
        assert query["search_url"] == url
    assert plan["pacing"] == {
        "per_run_max_queries": profile["pacing"]["per_run_max_queries"],
        "per_day_max_rows": profile["pacing"]["per_day_max_rows"],
        "source": "platform_profile",
    }
    assert plan["rubric"] == {
        "title_match": 40,
        "seniority": 20,
        "industry": 20,
        "geo": 10,
        "language": 10,
        "exclusion_hit": -100,
        "off_target_below": 50,
    }
    assert plan["partitioning"] == {
        "dimensions": ["geo"],
        "estimated_matches": None,
        "cap_risk": False,
    }
    assert plan["criteria"]["titles"] == ["Head of Operations", "Operations Director"]
    assert "network" not in plan["criteria"]


def test_issue_example_ingest_leads(skill, ingest_params):
    result = skill.execute(ingest_params)
    assert result == _load_json(FIXTURES_DIR, "expected_ingest.json")
    assert result["leads"] == [
        {
            "lead_id": "L0001",
            "profile_key": "demo:lead-0001",
            "score": 90,
            "reasons": ["title_match", "seniority", "industry", "geo"],
            "off_target": False,
            "stage": "NEW",
            "source_query": "q01",
        },
        {
            "lead_id": "L0002",
            "profile_key": "demo:lead-0002",
            "score": 0,
            "reasons": ["exclusion_hit:recruiter"],
            "off_target": True,
            "stage": "NEW",
            "source_query": "q02",
        },
    ]
    assert result["review_table"]["columns"] == list(REVIEW_COLUMNS)
    assert result["review_table"]["csv"] == ISSUE_CSV
    assert result["stats"] == {"rows_in": 2, "deduped": 0, "off_target": 1, "kept": 1}


def test_linkedin_profile_compile(skill):
    params = _load_json(FIXTURES_DIR, "linkedin_profile_params.json")
    result = skill.execute(params)
    assert result == _load_json(FIXTURES_DIR, "expected_linkedin_profile.json")
    profile = _load_profile("linkedin")
    plan = result["plan"]
    assert plan["platform"] == "linkedin"
    assert plan["profile_version"] == profile["profile_version"]
    assert plan["profile_origin"] == "bundled"
    assert (
        plan["pacing"]["per_run_max_queries"]
        == profile["pacing"]["per_run_max_queries"]
    )
    assert plan["pacing"]["per_day_max_rows"] == profile["pacing"]["per_day_max_rows"]
    assert len(plan["queries"]) == 1
    query = plan["queries"][0]
    assert query["expected_cap"] == profile["search"]["per_query_result_cap"]
    assert query["partition"] == {}
    assert query["keywords"] == '("Account Executive" OR "Sales Manager")'
    assert query["filters"] == {"geo": ["106693272"], "network": ["S"]}
    assert query["search_url"] == LINKEDIN_SEARCH_URL
    url = profile["search"]["url"]
    assert query["search_url"].startswith(
        url["base"] + "?" + url["keywords_param"] + "="
    )
    assert plan["partitioning"]["dimensions"] == []
    assert "network" not in plan["criteria"]


def test_multi_dimension_partition(skill):
    params = _load_json(FIXTURES_DIR, "multi_dimension_params.json")
    result = skill.execute(params)
    assert result == _load_json(FIXTURES_DIR, "expected_multi_dimension.json")
    plan = result["plan"]
    assert plan["partitioning"] == {
        "dimensions": ["geo", "company_size"],
        "estimated_matches": 3500,
        "cap_risk": False,
    }
    assert [q["partition"] for q in plan["queries"]] == [
        {"geo": "DE", "company_size": "51-200"},
        {"geo": "DE", "company_size": "201-500"},
        {"geo": "NL", "company_size": "51-200"},
        {"geo": "NL", "company_size": "201-500"},
    ]
    assert [q["id"] for q in plan["queries"]] == ["q01", "q02", "q03", "q04"]
    for query in plan["queries"]:
        assert query["filters"] == {
            "geo": [query["partition"]["geo"]],
            "company_size": [query["partition"]["company_size"]],
        }

    risk = skill.execute(
        _load_json(FIXTURES_DIR, "multi_dimension_cap_risk_params.json")
    )
    assert risk == _load_json(FIXTURES_DIR, "expected_multi_dimension_cap_risk.json")
    assert risk["plan"]["partitioning"] == {
        "dimensions": ["geo", "company_size"],
        "estimated_matches": 9000,
        "cap_risk": True,
    }
    assert len(risk["plan"]["queries"]) == 4

    # Without an estimate only the first candidate with two or more values expands.
    plan = _compile_plan(skill, geos=["DE", "NL"], company_size=["51-200", "201-500"])
    assert plan["partitioning"]["dimensions"] == ["geo"]
    assert [q["partition"] for q in plan["queries"]] == [{"geo": "DE"}, {"geo": "NL"}]
    assert plan["queries"][0]["filters"]["company_size"] == ["51-200", "201-500"]
    # A single value per dimension gives one query with no partition.
    plan = _compile_plan(skill, geos=["DE"], company_size=["51-200"])
    assert plan["queries"][0]["partition"] == {}
    assert plan["partitioning"]["dimensions"] == []
    # An estimate within one cap expands nothing; a zero estimate neither.
    for estimate in (0, 1000):
        plan = _compile_plan(skill, geos=["DE", "NL"], estimated_matches=estimate)
        assert plan["partitioning"] == {
            "dimensions": [],
            "estimated_matches": estimate,
            "cap_risk": False,
        }
        assert len(plan["queries"]) == 1
    # Candidates with one value are skipped, later ones still expand.
    plan = _compile_plan(
        skill, geos=["DE"], company_size=["51-200", "201-500"], estimated_matches=1500
    )
    assert plan["partitioning"]["dimensions"] == ["company_size"]
    assert len(plan["queries"]) == 2


def test_expected_cap_is_the_profile_cap(skill, override):
    for name in (
        "example_compile_params.json",
        "linkedin_profile_params.json",
        "multi_dimension_params.json",
    ):
        params = _load_json(FIXTURES_DIR, name)
        result = skill.execute(params)
        cap = _load_profile(params["platform"])["search"]["per_query_result_cap"]
        for query in result["plan"]["queries"]:
            assert query["expected_cap"] == cap
    plan = _compile_plan(skill, platform="override_demo", override=override)
    assert (
        plan["queries"][0]["expected_cap"] == override["search"]["per_query_result_cap"]
    )
    assert (
        plan["pacing"]["per_run_max_queries"]
        == override["pacing"]["per_run_max_queries"]
    )


def test_partition_rule_is_documented():
    section = _flat(_section(_instructions(), "## Partition rule"))
    for phrase in (
        "`partition_order`",
        "two or more values",
        "`estimated_matches`",
        "`per_query_result_cap`",
        "cartesian product",
        "varying slowest",
        "`cap_risk`",
        "cannot see result counts",
    ):
        assert phrase in section, phrase


@contextlib.contextmanager
def _deadline(seconds):
    """Interrupt a runaway call so that a failing test cannot exhaust memory."""
    if not hasattr(signal, "SIGALRM"):
        yield
        return

    def interrupt(signum, frame):
        raise TimeoutError(f"the call did not return within {seconds} seconds")

    previous = signal.signal(signal.SIGALRM, interrupt)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def test_query_count_is_capped_before_queries_are_built(skill, override):
    profile = copy.deepcopy(override)
    profile["search"]["per_query_result_cap"] = 1
    profile["search"]["max_query_length"] = MAX_PROFILE_INTEGER
    sizes = [f"s{i}" for i in range(LIST_CAPS["company_size"])]
    networks = [f"n{i}" for i in range(LIST_CAPS["network"])]
    profile["filters"]["company_size"]["values"] = sizes
    profile["filters"]["network"] = {"filter_id": "net", "values": networks}
    profile["partition_order"] = list(FILTER_DIMENSIONS)
    spec = {
        "titles": [f"Title {i}" for i in range(LIST_CAPS["titles"])],
        "keywords_include": [f"kw {i}" for i in range(LIST_CAPS["keywords_include"])],
        "keywords_exclude": [f"kx {i}" for i in range(LIST_CAPS["keywords_exclude"])],
        "industries": [f"ind {i}" for i in range(LIST_CAPS["industries"])],
        "seniority": list(SENIORITY_LEVELS),
        "geos": _geo_codes(LIST_CAPS["geos"]),
        "languages": [a + b for a in "abcde" for b in "abcd"],
        "company_size": sizes,
        "network": networks,
        "estimated_matches": MAX_ESTIMATED_MATCHES,
    }
    for field, values in spec.items():
        if field in LIST_CAPS:
            assert len(values) == LIST_CAPS[field], field
    expected = 1
    for dimension in FILTER_DIMENSIONS:
        expected *= len(spec[skill_module.SPEC_LIST_FOR_DIMENSION[dimension]])
    assert expected > MAX_QUERIES
    started = time.perf_counter()
    with _deadline(3.0):
        result = skill.execute(
            _compile_params(platform="override_demo", override=profile, **spec)
        )
    elapsed = time.perf_counter() - started
    detail = _assert_error(result, LIMIT_EXCEEDED, "size_cap")
    assert detail["path"] == "plan.queries"
    assert detail["limit"] == MAX_QUERIES
    assert detail["actual"] == expected
    assert elapsed < 2.0, elapsed


def test_network_filter_validation(skill):
    _assert_error(
        skill.execute(_compile_params(network=["S"])),
        INVALID_TARGET_SPEC,
        "dimension_not_in_profile",
    )
    _assert_error(
        skill.execute(_compile_params(platform="linkedin", network=["X"])),
        INVALID_TARGET_SPEC,
        "value_not_in_profile",
    )
    detail = _assert_error(
        skill.execute(
            _compile_params(
                platform="linkedin", network=["S"] * (LIST_CAPS["network"] + 1)
            )
        ),
        LIMIT_EXCEEDED,
        "size_cap",
    )
    assert detail["limit"] == LIST_CAPS["network"]
    _assert_error(
        skill.execute(_compile_params(company_size=["1-10"], platform="linkedin")),
        INVALID_TARGET_SPEC,
        "dimension_not_in_profile",
    )


def test_network_values_reach_query_filters(skill):
    plan = _compile_plan(skill, platform="linkedin", network=["S", "O"])
    assert plan["queries"][0]["filters"]["network"] == ["S", "O"]
    assert "network" not in plan["criteria"]
    profile = _load_profile("linkedin")
    profile["partition_order"] = ["network", "geo"]
    plan = _compile_plan(
        skill, platform="linkedin", override=profile, network=["S", "O"]
    )
    assert plan["partitioning"]["dimensions"] == ["network"]
    assert [q["filters"]["network"] for q in plan["queries"]] == [["S"], ["O"]]
    assert [q["partition"] for q in plan["queries"]] == [
        {"network": "S"},
        {"network": "O"},
    ]


def test_search_url_format(skill, compile_params):
    plan = skill.execute(compile_params)["plan"]
    assert plan["queries"][0]["search_url"] == ISSUE_SEARCH_URLS[0]
    parsed = urlsplit(plan["queries"][0]["search_url"])
    assert parsed.scheme == "https" and parsed.netloc == "demo.example"
    pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    assert [name for name, _ in pairs] == ["keywords", "geo", "company_size"]
    assert dict(pairs)["keywords"] == ISSUE_Q01_KEYWORDS
    assert dict(pairs)["geo"] == '["DE"]'
    assert dict(pairs)["company_size"] == '["51-200","201-500"]'

    linkedin = skill.execute(_load_json(FIXTURES_DIR, "linkedin_profile_params.json"))
    url = linkedin["plan"]["queries"][0]["search_url"]
    profile = _load_profile("linkedin")
    base = profile["search"]["url"]["base"]
    assert url.startswith(base + "?keywords=")
    pairs = parse_qsl(urlsplit(url).query, keep_blank_values=True, strict_parsing=True)
    assert [name for name, _ in pairs] == ["keywords", "origin", "geoUrn", "network"]
    assert dict(pairs)["origin"] == "FACETED_SEARCH"
    assert dict(pairs)["geoUrn"] == '["106693272"]'
    assert dict(pairs)["network"] == '["S"]'

    # The filter order is fixed, not the key order of the profile's filters.
    reordered = _load_profile("linkedin")
    reordered["filters"] = {
        "network": reordered["filters"]["network"],
        "geo": reordered["filters"]["geo"],
    }
    assert list(reordered["filters"]) == ["network", "geo"]
    params = _load_json(FIXTURES_DIR, "linkedin_profile_params.json")
    params["profile_override"] = reordered
    override_plan = skill.execute(params)["plan"]
    assert override_plan["queries"][0]["search_url"] == url
    assert list(override_plan["queries"][0]["filters"]) == ["geo", "network"]
    assert override_plan["profile_origin"] == "override"


@pytest.mark.parametrize("term", ["a&b=c#d?e%f", "x=1&geo=%5B%22ZZ%22%5D", "#frag?x=y"])
def test_search_url_cannot_break_out(skill, term):
    plan = _compile_plan(skill, titles=[term], geos=["DE"])
    query = plan["queries"][0]
    url = query["search_url"]
    assert url.count("?") == 1 and "#" not in url
    pairs = parse_qsl(urlsplit(url).query, keep_blank_values=True, strict_parsing=True)
    assert [name for name, _ in pairs] == ["keywords", "geo"]
    assert dict(pairs)["keywords"] == query["keywords"] == '"' + term + '"'
    assert dict(pairs)["geo"] == '["DE"]'
    encoded = url.split("keywords=", 1)[1].split("&geo=", 1)[0]
    assert re.fullmatch(r"(?:[A-Za-z0-9\-._~]|%[0-9A-F]{2})+", encoded)
    for char in "&=#?":
        assert char not in encoded
    assert unquote(encoded) == query["keywords"]


@pytest.mark.parametrize(
    "mutate,reason",
    [
        (
            lambda u: u.update(base="http://people.override.example/search/"),
            "scheme_not_https",
        ),
        (
            lambda u: u.update(base="https://evil.example/search/"),
            "host_outside_profile_key",
        ),
        (
            lambda u: u.update(base="https://user@people.override.example/search/"),
            "user_info_or_port",
        ),
        (
            lambda u: u.update(base="https://people.override.example:8443/search/"),
            "user_info_or_port",
        ),
        (
            lambda u: u.update(base="https://people.override.example/search/?x=1"),
            "invalid_url",
        ),
        (
            lambda u: u.update(base="https://people.override.example/search/#top"),
            "invalid_url",
        ),
        (
            lambda u: u.update(base="https://people.override.example/a b/"),
            "invalid_url",
        ),
        (lambda u: u.update(keywords_param="region"), "duplicate_parameter_name"),
        (
            lambda u: u.update(fixed_params=[{"name": "q", "value": "x"}]),
            "duplicate_parameter_name",
        ),
        (lambda u: u.update(filter_value_encoding="csv"), "unknown_encoding"),
        (lambda u: u.update(keywords_param="1q"), "invalid_parameter_name"),
        (lambda u: u.update(max_length=0), "out_of_range"),
    ],
)
def test_profile_rejects_bad_search_url(skill, override, mutate, reason):
    broken = copy.deepcopy(override)
    mutate(broken["search"]["url"])
    detail = _assert_error(
        skill.execute(_compile_params(platform="override_demo", override=broken)),
        INVALID_PROFILE,
        reason,
    )
    assert detail["origin"] == "override"
    assert detail["path"].startswith("search.url")


def test_base_host_matches_on_label_boundary(skill):
    profile = _load_profile("linkedin")
    for host in ("linkedin.com", "a.linkedin.com", "WWW.LinkedIn.com"):
        profile["search"]["url"]["base"] = f"https://{host}/search/results/people/"
        plan = _compile_plan(
            skill, platform="linkedin", override=profile, geos=["106693272"]
        )
        assert plan["queries"][0]["search_url"].startswith(f"https://{host}/")
    for host in (
        "evil-linkedin.com",
        "linkedin.com.evil.example",
        "linkedin.com.",
        "xlinkedin.com",
    ):
        profile["search"]["url"]["base"] = f"https://{host}/search/results/people/"
        detail = _assert_error(
            skill.execute(_compile_params(platform="linkedin", override=profile)),
            INVALID_PROFILE,
        )
        assert detail["path"] == "search.url.base"
    assert skill_module._host_matches("A.LinkedIn.COM", "linkedin.com")
    assert skill_module._host_matches("linkedin.com", "LINKEDIN.com")
    assert not skill_module._host_matches("evil-linkedin.com", "linkedin.com")
    assert not skill_module._host_matches("linkedin.com.evil.example", "linkedin.com")


# --------------------------------------------------------------------------
# Query grammar and escaping
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "term,rendered",
    [
        ('Head" OR "Operations', '"Head OR Operations"'),
        ("a) OR (b", '"a OR b"'),
        ("(Head) OR (Student)", '"Head OR Student"'),
        ("AND", '"AND"'),
        ("or", '"or"'),
        ("NOT", '"NOT"'),
        ("a" + chr(0) + "b", "ab"),
        ("tab" + chr(9) + "bed", '"tab bed"'),
        (LEFT_QUOTE + "smart" + RIGHT_QUOTE, "smart"),
        (BIDI_OVERRIDE + "plain" + ZERO_WIDTH_SPACE, "plain"),
        ("  spaced   out  ", '"spaced out"'),
        ('"""', None),
    ],
)
def test_terms_cannot_change_query_structure(skill, term, rendered):
    if rendered is None:
        _assert_error(
            skill.execute(_compile_params(titles=[term, "Zed"])),
            INVALID_TARGET_SPEC,
            "empty_term",
        )
        return
    plan = _compile_plan(skill, titles=[term, "Zed"], keywords_include=[term])
    keywords = plan["queries"][0]["keywords"]
    assert keywords == f"({rendered} OR Zed) AND {rendered}"
    assert keywords.count("(") == 1 and keywords.count(")") == 1
    assert keywords.count('"') % 2 == 0
    assert plan["criteria"]["titles"][0] == rendered.strip('"')


def test_query_grammar_reproduces_the_documented_forms(skill):
    plan = _compile_plan(skill, titles=["Single"])
    assert plan["queries"][0]["keywords"] == "Single"
    plan = _compile_plan(skill, titles=["Head of Sales"])
    assert plan["queries"][0]["keywords"] == '"Head of Sales"'
    plan = _compile_plan(
        skill,
        titles=["Alpha", "Beta"],
        keywords_include=["Gamma Delta", "Epsilon"],
        keywords_exclude=["Zeta"],
    )
    assert (
        plan["queries"][0]["keywords"]
        == '(Alpha OR Beta) AND "Gamma Delta" AND Epsilon NOT Zeta'
    )
    # De-duplication is case-insensitive and keeps the first occurrence.
    plan = _compile_plan(
        skill, titles=["Alpha", "alpha", "Beta", "ALPHA"], geos=["DE", "DE"]
    )
    assert plan["criteria"]["titles"] == ["Alpha", "Beta"]
    assert plan["criteria"]["geos"] == ["DE"]
    assert plan["queries"][0]["keywords"] == "(Alpha OR Beta)"


# --------------------------------------------------------------------------
# Plan ids, profile origin, edited plans
# --------------------------------------------------------------------------


def test_different_specs_give_different_plan_ids(skill):
    first = _compile_plan(skill, titles=["Alpha"])
    second = _compile_plan(skill, titles=["Beta"])
    again = _compile_plan(skill, titles=["Alpha"])
    assert first["plan_id"] != second["plan_id"]
    assert first["plan_id"] == again["plan_id"]
    body = {key: first[key] for key in PLAN_KEYS if key != "plan_id"}
    serialised = json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    assert (
        first["plan_id"]
        == "plan_" + hashlib.sha256(serialised.encode()).hexdigest()[:12]
    )


def test_profile_origin_is_reported_and_checked(skill, demo_override):
    bundled_plan = _compile_plan(skill)
    override_plan = _compile_plan(skill, override=demo_override)
    assert bundled_plan["profile_origin"] == "bundled"
    assert override_plan["profile_origin"] == "override"
    assert bundled_plan["profile_version"] == override_plan["profile_version"]
    assert bundled_plan["plan_id"] != override_plan["plan_id"]
    assert bundled_plan["queries"] == override_plan["queries"]
    _assert_error(
        skill.execute(_ingest_params(override_plan, [_row()])),
        PLAN_MISMATCH,
        "profile_origin_mismatch",
    )
    _assert_error(
        skill.execute(_ingest_params(bundled_plan, [_row()], override=demo_override)),
        PLAN_MISMATCH,
        "profile_origin_mismatch",
    )
    assert (
        skill.execute(_ingest_params(override_plan, [_row()], override=demo_override))[
            "status"
        ]
        == "completed"
    )


def test_edited_plan_is_rejected(skill, example_plan):
    edited = copy.deepcopy(example_plan)
    edited["criteria"]["keywords_exclude"] = []
    _assert_error(
        skill.execute(_ingest_params(edited, [_row()])),
        PLAN_MISMATCH,
        "plan_id_mismatch",
    )
    edited = copy.deepcopy(example_plan)
    edited["plan_id"] = "plan_000000000000"
    _assert_error(
        skill.execute(_ingest_params(edited, [_row()])),
        PLAN_MISMATCH,
        "plan_id_mismatch",
    )
    edited = copy.deepcopy(example_plan)
    edited["queries"][0]["filters"]["geo"] = ["FR"]
    _assert_error(
        skill.execute(_ingest_params(edited, [_row()])),
        PLAN_MISMATCH,
        "plan_id_mismatch",
    )
    edited = copy.deepcopy(example_plan)
    edited["rubric"]["title_match"] = 100
    _assert_error(
        skill.execute(_ingest_params(edited, [_row()])), INVALID_PLAN, "rubric_mismatch"
    )
    edited = copy.deepcopy(example_plan)
    edited["profile_version"] = "2020-01-01"
    _assert_error(
        skill.execute(_ingest_params(edited, [_row()])),
        PLAN_MISMATCH,
        "profile_version_mismatch",
    )
    linkedin_plan = skill.execute(
        _load_json(FIXTURES_DIR, "linkedin_profile_params.json")
    )["plan"]
    _assert_error(
        skill.execute(_ingest_params(linkedin_plan, [_row()])),
        PLAN_MISMATCH,
        "platform_mismatch",
    )
    # An unedited plan passes, whatever the key order of its serialisation.
    reordered = json.loads(json.dumps(example_plan, sort_keys=True))
    assert skill.execute(_ingest_params(reordered, [_row()]))["status"] == "completed"


# --------------------------------------------------------------------------
# Ingest: de-duplication, ids, source queries, components, counts
# --------------------------------------------------------------------------


def test_dedup_within_rows_and_against_existing(skill):
    params = _load_json(FIXTURES_DIR, "ingest_dedup_params.json")
    result = skill.execute(params)
    assert result == _load_json(FIXTURES_DIR, "expected_ingest_dedup.json")
    assert result["stats"] == {"rows_in": 4, "deduped": 2, "off_target": 0, "kept": 2}
    assert [lead["profile_key"] for lead in result["leads"]] == [
        "demo:lead-0001",
        "demo:lead-0003",
    ]
    profile = _load_profile("demo")
    for url in (
        "https://demo.example/in/lead-0001",
        "https://DEMO.example/in/Lead-0001/",
        "http://www.demo.example/in/lead-0001?ref=export#x",
        "https://demo.example/in/LEAD-0001/details",
    ):
        assert skill_module._profile_key(url, profile) == "demo:lead-0001"
    for url in (
        "https://evil-demo.example/in/lead-0001",
        "https://demo.example.evil.example/in/lead-0001",
        "https://demo.example/profile/lead-0001",
        "https://demo.example/in/",
        "https://demo.example/in/lead 0001",
        "ftp://demo.example/in/lead-0001",
        "demo.example/in/lead-0001",
        "https://demo.example/in/" + "a" * 201,
    ):
        assert skill_module._profile_key(url, profile) is None


def test_lead_ids_continue_after_existing_leads(skill, example_plan):
    existing = [
        {"profile_key": "demo:a", "lead_id": "L0007"},
        {"profile_key": "demo:b", "lead_id": "L12"},
        {"profile_key": "demo:c", "lead_id": "X99"},
        {"profile_key": "demo:d", "lead_id": "L00003"},
    ]
    result = skill.execute(
        _ingest_params(
            example_plan,
            [
                _row(url="https://demo.example/in/n1"),
                _row(url="https://demo.example/in/n2"),
            ],
            existing=existing,
        )
    )
    assert [lead["lead_id"] for lead in result["leads"]] == ["L0013", "L0014"]
    result = skill.execute(_ingest_params(example_plan, [_row()]))
    assert result["leads"][0]["lead_id"] == "L0001"
    big = [{"profile_key": "demo:z", "lead_id": "L99999"}]
    result = skill.execute(_ingest_params(example_plan, [_row()], existing=big))
    assert result["leads"][0]["lead_id"] == "L100000"


def test_lead_id_numbering_is_exact_for_any_length(skill, example_plan):
    def next_ids(existing, count=1):
        rows = [_row(url=f"https://demo.example/in/new-{i}") for i in range(count)]
        result = skill.execute(_ingest_params(example_plan, rows, existing=existing))
        assert result["status"] == "completed", result
        return [lead["lead_id"] for lead in result["leads"]]

    def lead(lead_id, key="demo:a"):
        return {"profile_key": key, "lead_id": lead_id}

    # Only the whole string `L` followed by ASCII digits counts.
    assert next_ids([lead("L0041\n")]) == ["L0001"]
    assert next_ids([lead("L0041 ")]) == ["L0001"]
    assert next_ids([lead(" L0041")]) == ["L0001"]
    assert next_ids([lead("L")]) == ["L0001"]
    assert next_ids([lead("l0041")]) == ["L0001"]
    assert next_ids([lead("L٤٢")]) == ["L0001"]
    assert next_ids([lead("L0041")]) == ["L0042"]
    # The comparison is exact at any length: 19, 25 and 5,000 digits.
    nineteen = "1" + "0" * 18
    assert next_ids([lead("L" + nineteen)]) == ["L" + nineteen[:-1] + "1"]
    digits = "1234567890123456789012345"
    assert next_ids([lead("L" + digits)]) == ["L" + str(int(digits) + 1)]
    huge = "9" * 5000
    assert next_ids([lead("L" + huge)], count=2) == [
        "L1" + "0" * 5000,
        "L1" + "0" * 4999 + "1",
    ]
    # The highest number wins whatever its digit count or leading zeros.
    assert next_ids([lead("L" + "0" * 30 + "7"), lead("L8", "demo:b")]) == ["L0009"]
    assert next_ids([lead("L" + "9" * 20), lead("L1" + "0" * 19, "demo:b")]) == [
        "L1" + "0" * 20
    ]


def test_stage_is_new(skill):
    for params_name, _ in REFERENCE_FIXTURES:
        for case in _cases(_load_json(FIXTURES_DIR, params_name)):
            if case["action"] != "ingest_leads":
                continue
            for lead in skill.execute(case)["leads"]:
                assert lead["stage"] == "NEW"


def test_ingest_accepts_search_url_as_source_query(skill, example_plan):
    url = example_plan["queries"][1]["search_url"]
    lead = _ingest_one(skill, example_plan, _row(query=url))
    assert lead["source_query"] == "q02"
    result = skill.execute(_ingest_params(example_plan, [_row(query=url)]))
    assert result["review_table"]["csv"].endswith(",q02")
    assert url not in json.dumps(result)


def test_ingest_rejects_altered_search_url(skill, example_plan):
    url = example_plan["queries"][1]["search_url"]
    for altered in (
        url[:-1],
        url + "x",
        url.replace("%22NL%22", "%22nl%22"),
        url.upper(),
        " " + url,
    ):
        detail = _assert_error(
            skill.execute(_ingest_params(example_plan, [_row(query=altered)])),
            UNKNOWN_SOURCE_QUERY,
            "unknown_source_query",
        )
        assert detail["row"] == 0


def test_unknown_source_query(skill, example_plan):
    marker = "zq-marker-text"
    detail = _assert_error(
        skill.execute(_ingest_params(example_plan, [_row(), _row(query=marker)])),
        UNKNOWN_SOURCE_QUERY,
        "unknown_source_query",
    )
    assert detail["row"] == 1
    assert marker not in json.dumps(detail)


def test_unknown_platform(skill, example_plan):
    assert BUNDLED_PLATFORMS == ("demo", "linkedin")
    for platform in ("nowhere", "demo2", "linkedin_eu"):
        _assert_error(
            skill.execute(_compile_params(platform=platform)), UNKNOWN_PLATFORM
        )
        _assert_error(
            skill.execute(_ingest_params(example_plan, [_row()], platform=platform)),
            UNKNOWN_PLATFORM,
        )
    for platform in ("Demo", "demo/../linkedin", "", "x" * 33, "1demo"):
        _assert_error(
            skill.execute(_compile_params(platform=platform)),
            INVALID_REQUEST,
            "invalid_platform",
        )


def test_counts_are_consistent_on_every_fixture(skill):
    seen = 0
    for params_name, _ in REFERENCE_FIXTURES:
        for case in _cases(_load_json(FIXTURES_DIR, params_name)):
            if case["action"] != "ingest_leads":
                continue
            result = skill.execute(case)
            stats = result["stats"]
            assert set(stats) == set(STATS_KEYS)
            assert stats["rows_in"] == len(case["rows"])
            assert (
                stats["rows_in"]
                == stats["deduped"] + stats["off_target"] + stats["kept"]
            )
            assert stats["off_target"] == sum(
                1 for lead in result["leads"] if lead["off_target"]
            )
            assert stats["kept"] == sum(
                1 for lead in result["leads"] if not lead["off_target"]
            )
            assert len(result["leads"]) == stats["off_target"] + stats["kept"]
            seen += 1
    assert seen >= 5


def test_ingest_components_fixture(skill):
    params = _load_json(FIXTURES_DIR, "ingest_components_params.json")
    assert _run(skill, params) == _load_json(
        FIXTURES_DIR, "expected_ingest_components.json"
    )


def test_component_title_match(skill, example_plan):
    lead = _ingest_one(
        skill, example_plan, _row(title="Operations Director", query="q02")
    )
    assert "title_match" in lead["reasons"]
    lead = _ingest_one(
        skill, example_plan, _row(title="Director of Operations", query="q02")
    )
    assert "title_match" not in lead["reasons"]
    lead = _ingest_one(
        skill, example_plan, _row(title="HEAD OF OPERATIONS (EMEA)", query="q02")
    )
    assert "title_match" in lead["reasons"]


def test_component_seniority_row(skill, example_plan):
    assert "seniority" not in example_plan["queries"][0]["filters"]
    lead = _ingest_one(skill, example_plan, _row(title="Director of Fun"))
    assert "seniority" in lead["reasons"]
    lead = _ingest_one(skill, example_plan, _row(title="VP Fun"))
    assert "seniority" in lead["reasons"]
    lead = _ingest_one(skill, example_plan, _row(title="Fun Coordinator"))
    assert "seniority" not in lead["reasons"]
    lead = _ingest_one(skill, example_plan, _row(title="Fun Manager"))
    assert "seniority" not in lead["reasons"]  # manager is not in the criteria
    no_levels = _compile_plan(skill, titles=["Operations Manager"])
    lead = _ingest_one(skill, no_levels, _row(title="Director of Fun"))
    assert "seniority" not in lead["reasons"]


def test_component_seniority_inherited(skill, override):
    plan = _compile_plan(
        skill, platform="override_demo", override=override, seniority=["manager"]
    )
    assert plan["queries"][0]["filters"]["seniority"] == ["manager"]
    row = {
        "url": "https://people.override.example/people/x",
        "headline": "Warehouse Coordinator",
        "source": "q01",
    }
    lead = _ingest_one(skill, plan, row, platform="override_demo", override=override)
    assert "seniority" in lead["reasons"]


def test_component_industry_row(skill, example_plan):
    assert "industry" not in example_plan["queries"][0]["filters"]
    lead = _ingest_one(skill, example_plan, _row(company="Acme Logistics"))
    assert "industry" in lead["reasons"]
    lead = _ingest_one(skill, example_plan, _row(company="Acme Foods"))
    assert "industry" not in lead["reasons"]
    lead = _ingest_one(skill, example_plan, _row(title="Logistics Manager"))
    assert (
        "industry" not in lead["reasons"]
    )  # industries match the company, not the title
    lead = _ingest_one(skill, example_plan, _row())
    assert "industry" not in lead["reasons"]


def test_component_industry_inherited(skill, override):
    plan = _compile_plan(
        skill, platform="override_demo", override=override, industries=["logistics"]
    )
    assert plan["queries"][0]["filters"]["industry"] == ["logistics"]
    row = {
        "url": "https://people.override.example/people/x",
        "headline": "Nobody",
        "org": "Acme Foods",
        "source": "q01",
    }
    lead = _ingest_one(skill, plan, row, platform="override_demo", override=override)
    assert "industry" in lead["reasons"]


def test_component_geo_inherited(skill, example_plan):
    lead = _ingest_one(skill, example_plan, _row(title="Nobody", location="Nowhere"))
    assert lead["reasons"] == ["geo"]
    plan = _compile_plan(skill, titles=["Operations Manager"])
    assert "geo" not in plan["queries"][0]["filters"]
    lead = _ingest_one(skill, plan, _row(title="Nobody", location="Hamburg, Germany"))
    assert "geo" not in lead["reasons"]


def test_component_language_inherited(skill, override):
    plan = _compile_plan(
        skill, platform="override_demo", override=override, languages=["en"]
    )
    assert plan["queries"][0]["filters"]["language"] == ["en"]
    row = {
        "url": "https://people.override.example/people/x",
        "headline": "Nobody",
        "source": "q01",
    }
    lead = _ingest_one(skill, plan, row, platform="override_demo", override=override)
    assert "language" in lead["reasons"]
    demo_plan = _compile_plan(skill, languages=["en"])
    assert "language" not in demo_plan["queries"][0]["filters"]
    lead = _ingest_one(skill, demo_plan, _row(title="Nobody"))
    assert "language" not in lead["reasons"]


def test_component_exclusion_hit(skill, example_plan):
    lead = _ingest_one(
        skill,
        example_plan,
        _row(title="Head of Operations and Senior Recruiter", company="Acme Logistics"),
    )
    assert lead == {
        "lead_id": "L0001",
        "profile_key": "demo:lead-0500",
        "score": 0,
        "reasons": ["exclusion_hit:recruiter"],
        "off_target": True,
        "stage": "NEW",
        "source_query": "q01",
    }
    lead = _ingest_one(skill, example_plan, _row(title="Student Recruiter"))
    assert lead["reasons"] == ["exclusion_hit:recruiter", "exclusion_hit:student"]
    lead = _ingest_one(skill, example_plan, _row(title="Recruiting Lead"))
    assert "exclusion_hit:recruiter" not in lead["reasons"]  # whole tokens only


def test_off_target_threshold_is_the_rubric_value(skill, example_plan):
    lead = _ingest_one(
        skill, example_plan, _row(title="Director of Fun", company="Acme Logistics")
    )
    assert lead["score"] == 50 and lead["off_target"] is False
    lead = _ingest_one(skill, example_plan, _row(title="Director of Fun"))
    assert lead["score"] == 30 and lead["off_target"] is True
    assert RUBRIC["off_target_below"] == 50


def test_scoring_rule_is_documented():
    section = _section(_instructions(), "## Scoring")
    for component, kind in (
        ("`title_match`", "row"),
        ("`seniority`", "inherited"),
        ("`industry`", "inherited"),
        ("`geo`", "inherited"),
        ("`language`", "inherited"),
        ("`exclusion_hit`", "row"),
    ):
        line = next(
            ln for ln in section.splitlines() if ln.startswith("| " + component)
        )
        assert kind in line.lower(), line
    assert "kb/seniority_terms.json" in section
    assert "first 200 characters" in _flat(section)


# --------------------------------------------------------------------------
# Output shape and data minimisation
# --------------------------------------------------------------------------


def test_output_key_sets_are_exact(skill, compile_params, ingest_params):
    compiled = skill.execute(compile_params)
    assert set(compiled) == {"status", "plan"}
    plan = compiled["plan"]
    assert tuple(plan) == PLAN_KEYS
    for query in plan["queries"]:
        assert tuple(query) == QUERY_KEYS
    assert tuple(plan["pacing"]) == PACING_KEYS
    assert tuple(plan["rubric"]) == RUBRIC_KEYS
    assert tuple(plan["criteria"]) == CRITERIA_FIELDS
    assert tuple(plan["partitioning"]) == PARTITIONING_KEYS
    ingested = skill.execute(ingest_params)
    assert set(ingested) == {"status", "leads", "review_table", "stats"}
    for lead in ingested["leads"]:
        assert tuple(lead) == LEAD_KEYS
    assert set(ingested["review_table"]) == {"columns", "csv"}
    assert ingested["review_table"]["columns"] == list(REVIEW_COLUMNS)
    assert tuple(ingested["stats"]) == STATS_KEYS


def test_full_name_and_profile_url_never_appear(skill):
    checked = 0
    for params_name, _ in REFERENCE_FIXTURES:
        for case in _cases(_load_json(FIXTURES_DIR, params_name)):
            if case["action"] != "ingest_leads":
                continue
            profile = case.get("profile_override") or _load_profile(case["platform"])
            url_column = profile["export_columns"]["profile_url"]
            text = json.dumps(skill.execute(case))
            for row in case["rows"]:
                for key, value in row.items():
                    if key in ("fullName", "name"):
                        assert value not in text
                    if key == url_column:
                        assert value not in text
                        assert urlsplit(value).hostname not in text
                checked += 1
            assert "https://" not in text and "http://" not in text
    assert checked >= 10


def test_error_details_never_echo_row_text(skill, example_plan):
    marker = "zq-marker-text"
    cases = [
        _ingest_params(
            example_plan, [{"profileUrl": marker, "title": marker, "query": "q01"}]
        ),
        _ingest_params(
            example_plan, [{"profileUrl": "https://demo.example/in/x", "query": marker}]
        ),
        _ingest_params(
            example_plan,
            [
                {
                    "profileUrl": "https://demo.example/in/x",
                    "title": marker,
                    "query": marker,
                }
            ],
        ),
        _ingest_params(example_plan, [{marker: "x"}]),
        _ingest_params(example_plan, [_row(**{marker: "v" * (MAX_CELL_CHARS + 1)})]),
        _ingest_params(
            example_plan, [_row()], existing=[{"profile_key": marker, marker: 1}]
        ),
        _compile_params(titles=[marker + " " * 2 + "x" * MAX_TERM_CHARS]),
        _compile_params(geos=[marker]),
        _compile_params(**{marker: 1}),
        {**_compile_params(), marker: 1},
        _compile_params(platform=marker),
        _compile_params(platform="override_demo", override={marker: 1}),
    ]
    for params in cases:
        result = skill.execute(params)
        assert result["status"] == "error", params
        assert marker not in json.dumps(result)


def _in_both_orders(build, items):
    """``build`` applied to ``items`` in the given order and in reverse."""
    return build(list(items)), build(list(reversed(items)))


def test_error_details_do_not_depend_on_key_order(skill, compile_params, example_plan):
    def request(extra):
        return skill.execute({**compile_params, **dict(extra)})

    def ingest(cells):
        return skill.execute(_ingest_params(example_plan, [{**_row(), **dict(cells)}]))

    # A request holding a key of the other action and an unknown key.
    first, second = _in_both_orders(
        request, [("plan", example_plan), ("unexpected", 1)]
    )
    assert first == second
    assert _assert_error(first, INVALID_REQUEST, "unknown_key")["path"] == "plan"
    # Two keys of the other action: the alphabetically first one is named.
    first, second = _in_both_orders(request, [("rows", []), ("plan", example_plan)])
    assert first == second
    assert first["error"]["detail"]["path"] == "plan"
    # A row holding two over-cap cells of different lengths.
    first, second = _in_both_orders(
        ingest,
        [("long", "x" * (MAX_CELL_CHARS + 1)), ("longer", "x" * (MAX_CELL_CHARS + 2))],
    )
    assert first == second
    assert _assert_error(first, LIMIT_EXCEEDED, "size_cap") == {
        "reason": "size_cap",
        "path": "rows[0]",
        "limit": MAX_CELL_CHARS,
        "actual": MAX_CELL_CHARS + 2,
    }


# --------------------------------------------------------------------------
# Review table and CSV
# --------------------------------------------------------------------------


def _hostile_result(skill):
    params = _load_json(FIXTURES_DIR, "ingest_hostile_params.json")
    result = skill.execute(params)
    assert result == _load_json(FIXTURES_DIR, "expected_ingest_hostile.json")
    return params, result


def test_csv_neutralises_formula_cells(skill):
    params, result = _hostile_result(skill)
    rows = list(csv.reader(io.StringIO(result["review_table"]["csv"])))
    cells = {(row[0], REVIEW_COLUMNS.index("title")): row[3] for row in rows[1:]}
    assert cells[("L0001", 3)] == "'=SUM(A1:A9)"
    assert cells[("L0002", 3)] == "'-Head of Operations"
    assert cells[("L0003", 3)] == "'=Operations Director"
    assert cells[("L0004", 3)] == "'" + FULL_WIDTH_EQUALS + "Director of Logistics"
    assert rows[1][4] == "'+Demo, Inc."
    assert rows[1][5] == "'@Hamburg"
    for row in rows[1:]:
        for cell in row[3:6]:
            assert not cell or cell[0] not in "=+-@" + FULL_WIDTH_EQUALS
    for text, expected in (
        ("=1+1", "'=1+1"),
        ("  +1", "'+1"),
        ("@cmd", "'@cmd"),
        ("-x", "'-x"),
        (chr(0xFF0B) + "x", "'" + chr(0xFF0B) + "x"),
        (chr(0xFF0D) + "x", "'" + chr(0xFF0D) + "x"),
        (chr(0xFF20) + "x", "'" + chr(0xFF20) + "x"),
        (chr(0x200B) + "=x", "'=x"),
        ("plain", "plain"),
        ("", ""),
    ):
        assert skill_module._csv_cell(text) == expected


def test_csv_neutralises_formula_segments_in_reasons(skill):
    plan = _compile_plan(skill, keywords_exclude=["x;=1+1"])
    result = skill.execute(_ingest_params(plan, [_row(title="x 1 1")]))
    assert result["status"] == "completed", result
    assert result["leads"][0]["reasons"] == ["exclusion_hit:x;=1+1"]
    text = result["review_table"]["csv"]
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[1][REVIEW_COLUMNS.index("reasons")] == "exclusion_hit:x;'=1+1"
    for fields in csv.reader(io.StringIO(text), delimiter=";"):
        for field in fields:
            assert not field or field[0] not in skill_module._FORMULA_LEADERS, field


def test_csv_quotes_and_strips_control_characters(skill):
    params, result = _hostile_result(skill)
    text = result["review_table"]["csv"]
    assert not text.endswith("\n")
    for char in text:
        assert char == "\n" or ord(char) >= 32, repr(char)
    assert (
        BIDI_OVERRIDE not in text
        and ZERO_WIDTH_SPACE not in text
        and chr(0xA0) not in text
    )
    assert '"Demo ""Quoted"" GmbH"' in text
    assert '"Berlin, Germany"' in text
    assert "DemoReversed BV" in text
    assert "Line one Line two Tabbed" in text
    assert "Demo Logistics," in text
    assert "Munich, Germany" in text
    rows = list(csv.reader(io.StringIO(text)))
    long_title = rows[5][3]
    assert len(long_title) == SCORING_TEXT_CHARS
    assert long_title.startswith("Operations ")


def test_line_breaks_become_spaces(skill, example_plan):
    breaks = ("\t", "\n", "\x0b", "\x0c", "\r", "\x85", " ", " ")
    for char in breaks:
        cell = "Line one" + char + "Line two"
        assert skill_module._csv_cell(cell) == "Line one Line two", repr(char)
        assert skill_module._sanitise_term(cell, set()) == "Line one Line two"
    # Other control and format characters are still removed, not spaced.
    assert skill_module._csv_cell("Line" + chr(1) + "one") == "Lineone"
    assert skill_module._sanitise_term("Line" + ZERO_WIDTH_SPACE + "one", set()) == (
        "Lineone"
    )
    result = skill.execute(
        _ingest_params(example_plan, [_row(location="Line one\nLine two")])
    )
    rows = list(csv.reader(io.StringIO(result["review_table"]["csv"])))
    assert rows[1][REVIEW_COLUMNS.index("location")] == "Line one Line two"
    plan = _compile_plan(
        skill, titles=["Zed"], keywords_include=["warehouse\nautomation"]
    )
    assert plan["queries"][0]["keywords"] == 'Zed AND "warehouse automation"'
    assert plan["criteria"]["keywords_include"] == ["warehouse automation"]
    for title in ("Head of\nOperations", "Head of\tOperations"):
        lead = _ingest_one(skill, example_plan, _row(title=title))
        assert "title_match" in lead["reasons"], repr(title)


def _documented_reasons_cell(reasons):
    """The reasons joined with ``;``, each formula-leading segment prefixed."""
    return ";".join(
        (
            "'" + segment
            if segment and segment[0] in skill_module._FORMULA_LEADERS
            else segment
        )
        for segment in ";".join(reasons).split(";")
    )


def test_csv_round_trips_through_the_csv_reader(skill):
    for params_name, _ in REFERENCE_FIXTURES:
        for case in _cases(_load_json(FIXTURES_DIR, params_name)):
            if case["action"] != "ingest_leads":
                continue
            result = skill.execute(case)
            rows = list(csv.reader(io.StringIO(result["review_table"]["csv"])))
            assert rows[0] == list(REVIEW_COLUMNS)
            assert len(rows) == len(result["leads"]) + 1
            for row, lead in zip(rows[1:], result["leads"]):
                assert len(row) == len(REVIEW_COLUMNS)
                assert row[0] == lead["lead_id"]
                assert row[1] == str(lead["score"])
                assert row[2] == ("true" if lead["off_target"] else "false")
                assert row[6] == _documented_reasons_cell(lead["reasons"])
                assert row[7] == lead["source_query"]


# --------------------------------------------------------------------------
# Platform profiles and overrides
# --------------------------------------------------------------------------


def _schema(name):
    return _load_json(SCHEMAS_DIR, name)


def test_bundled_profiles_validate(skill):
    schema = _schema("platform_profile.schema.json")
    checker = jsonschema.FormatChecker()
    for platform in BUNDLED_PLATFORMS:
        profile = _load_profile(platform)
        jsonschema.validate(profile, schema, format_checker=checker)
        assert skill_module._validate_profile(profile, platform) is profile
        assert profile["platform"] == platform
        assert profile["sources"]
        for source in profile["sources"]:
            assert set(source) == {"title", "reference", "retrieved_on"}
    override = _load_json(FIXTURES_DIR, "override_profile.json")
    jsonschema.validate(override, schema, format_checker=checker)
    assert skill_module._validate_profile(override, "override_demo") is override
    assert "override_demo" not in BUNDLED_PLATFORMS
    linkedin = _load_profile("linkedin")
    assert linkedin["filters"].keys() == {"geo", "network"}
    assert "company_size" not in linkedin["filters"]
    demo = _load_profile("demo")
    assert demo["filters"].keys() == {"geo", "company_size"}
    assert demo["partition_order"] == ["geo", "company_size"]
    assert demo["search"]["url"]["base"].startswith("https://demo.example/")
    assert demo["profile_key"]["host_suffixes"] == ["demo.example"]
    assert demo["profile_version"] == "2026-09-29"
    terms = skill_module._load_seniority_terms()
    assert set(terms) == set(skill_module.SENIORITY_LEVELS)
    assert all(isinstance(t, str) and t for level in terms.values() for t in level)


def test_fixtures_match_the_documentation_schemas(skill):
    checker = jsonschema.FormatChecker()
    target_spec = _schema("target_spec.schema.json")
    plan_schema = _schema("plan.schema.json")
    lead_schema = _schema("lead_record.schema.json")
    for params_name, expected_name in REFERENCE_FIXTURES:
        for case, expected in zip(
            _cases(_load_json(FIXTURES_DIR, params_name)),
            _cases(_load_json(FIXTURES_DIR, expected_name)),
        ):
            if case["action"] == "compile_search":
                jsonschema.validate(
                    case["target_spec"], target_spec, format_checker=checker
                )
                jsonschema.validate(
                    expected["plan"], plan_schema, format_checker=checker
                )
            else:
                jsonschema.validate(case["plan"], plan_schema, format_checker=checker)
                for lead in expected["leads"]:
                    jsonschema.validate(lead, lead_schema, format_checker=checker)


def test_override_profile_passes_the_same_validation(skill, override):
    plan = _compile_plan(
        skill, platform="override_demo", override=override, geos=["DE"]
    )
    assert plan["profile_origin"] == "override"
    assert plan["platform"] == "override_demo"
    assert plan["profile_version"] == override["profile_version"]
    row = {
        "url": "https://people.override.example/people/lead-0001",
        "headline": "Operations Manager",
        "source": "q01",
    }
    result = skill.execute(
        _ingest_params(plan, [row], platform="override_demo", override=override)
    )
    assert result["status"] == "completed"
    assert result["leads"][0]["profile_key"] == "override_demo:lead-0001"


@pytest.mark.parametrize(
    "mutate,reason,path",
    [
        (lambda p: p.pop("sources"), "missing_key", "profile.sources"),
        (lambda p: p.update(extra=1), "unknown_key", "profile"),
        (lambda p: p.update(schema_version=2), "schema_version", "schema_version"),
        (lambda p: p.update(platform="other"), "platform_mismatch", "platform"),
        (
            lambda p: p.update(profile_version="2026-13-01"),
            "not_a_date",
            "profile_version",
        ),
        (lambda p: p.update(sources=[]), "too_few_entries", "sources"),
        (
            lambda p: p["search"]["grammar"].update(forbidden_characters=["(", ")"]),
            "structural_character_not_forbidden",
            "search.grammar.quote",
        ),
        (
            lambda p: p["search"]["grammar"].update(reserved_words=["AND", "OR"]),
            "operator_not_reserved",
            "search.grammar.not",
        ),
        (
            lambda p: p["search"]["grammar"].update(quote='""'),
            "not_a_single_character",
            "search.grammar.quote",
        ),
        (
            lambda p: p["filters"].update(
                company_size={"filter_id": "size", "values": None}
            ),
            "values_required",
            "filters.company_size.values",
        ),
        (
            lambda p: p["filters"].update(
                company_size={"filter_id": "size", "values": []}
            ),
            "too_few_entries",
            "filters.company_size.values",
        ),
        (
            lambda p: p["filters"].update(bogus={"filter_id": "b", "values": None}),
            "unknown_key",
            "filters",
        ),
        (
            lambda p: p["filters"].update(region={"filter_id": "geo", "values": None}),
            "unknown_key",
            "filters",
        ),
        (
            lambda p: p["filters"]["geo"].update(filter_id="level"),
            "duplicate_parameter_name",
            "search.url",
        ),
        (
            lambda p: p.update(partition_order=["network"]),
            "dimension_without_filter",
            "partition_order[0]",
        ),
        (
            lambda p: p.update(partition_order=["geo", "geo"]),
            "duplicate_dimension",
            "partition_order[1]",
        ),
        (
            lambda p: p["pacing"].update(per_run_max_queries=0),
            "out_of_range",
            "pacing.per_run_max_queries",
        ),
        (
            lambda p: p["pacing"].update(per_day_max_rows=True),
            "not_an_integer",
            "pacing.per_day_max_rows",
        ),
        (
            lambda p: p["export_columns"].update(company="headline"),
            "duplicate_column",
            "export_columns",
        ),
        (
            lambda p: p["export_columns"].pop("title"),
            "missing_key",
            "export_columns.title",
        ),
        (
            lambda p: p["profile_key"].update(path_prefix="people"),
            "invalid_path_prefix",
            "profile_key.path_prefix",
        ),
        (
            lambda p: p["profile_key"].update(host_suffixes=["bad host"]),
            "invalid_host",
            "profile_key.host_suffixes[0]",
        ),
        (
            lambda p: p["profile_key"].update(host_suffixes=["other.example"]),
            "host_outside_profile_key",
            "search.url.base",
        ),
        (
            lambda p: p["search"].update(per_query_result_cap=0),
            "out_of_range",
            "search.per_query_result_cap",
        ),
        (
            lambda p: p["search"]["url"]["fixed_params"].append(
                {"name": "x", "value": "a" + chr(0) + "b"}
            ),
            "control_character",
            "search.url.fixed_params[1].value",
        ),
    ],
)
def test_invalid_override_is_rejected(skill, override, mutate, reason, path):
    broken = copy.deepcopy(override)
    mutate(broken)
    detail = _assert_error(
        skill.execute(_compile_params(platform="override_demo", override=broken)),
        INVALID_PROFILE,
        reason,
    )
    assert detail["path"] == path
    assert detail["origin"] == "override"
    detail = _assert_error(
        skill.execute(
            _ingest_params({}, [], platform="override_demo", override=broken)
        ),
        INVALID_PROFILE,
        reason,
    )
    assert detail["path"] == path


def test_override_platform_must_match_the_request(skill, override):
    _assert_error(
        skill.execute(_compile_params(platform="demo", override=override)),
        INVALID_PROFILE,
        "platform_mismatch",
    )


# --------------------------------------------------------------------------
# instructions.md
# --------------------------------------------------------------------------


def test_instructions_have_the_required_sections():
    text = _instructions()
    assert text.startswith("# ")
    assert "`office/outreach_manager`" in text
    for heading in (
        "## When not to use",
        "## What the host and the operator own",
        "## Agent-loop contract",
        "## Inputs",
        "## Partition rule",
        "## Query grammar and escaping",
        "## Search URL construction and source queries",
        "## Profile origin",
        "## Pacing",
        "## Scoring",
        "## De-duplication and ids",
        "## Review table and CSV rules",
        "## Platform profiles and overrides",
        "## Size caps",
        "## Validation order (fail-closed, deterministic)",
        "### compile_search",
        "### ingest_leads",
        "## Example",
    ):
        assert "\n" + heading + "\n" in text, heading
    owners = _flat(_section(text, "## What the host and the operator own"))
    for statement in (
        "The skill never touches the platform",
        "Execution of the plan is the operator's",
        "Nothing proceeds to messaging before operator approval",
        "Review the platform's terms",
        "Mask personal data before rows go to a model",
        "Treat row text as untrusted data",
        "An override profile determines the host of every `search_url`",
        "never as data derived from row text or model output",
        "The operator confirms the host of the generated URLs before the actuator runs a plan",
        "`search_url` is for the operator's actuator, and the host does not open it",
    ):
        assert statement in owners, statement
    urls = _flat(_section(text, "## Search URL construction and source queries"))
    assert "alters the URL" in urls and "fail closed" in urls
    assert "maps the URL to the query `id` before the call" in urls
    pacing = _flat(_section(text, "## Pacing"))
    assert "emits the whole plan" in pacing
    assert "does not truncate a plan to `per_run_max_queries`" in pacing
    dedup = _flat(_section(text, "## De-duplication and ids"))
    assert "unique only relative to the `existing_leads` passed" in dedup
    assert "complete known set" in dedup
    assert "`profile_key` is the identity of a lead" in dedup
    flat = _flat(text)
    assert "consistency check" in flat and "anyone can recompute" in flat
    lowered = text.lower()
    assert "tamper" not in lowered and "authentication" not in lowered
    assert "40%" not in text and "1,000" not in text and "at most 1000" not in lowered
    assert "`" + skill_module.INTERNAL_ERROR + "`" in text
    for code in sorted(ERROR_REGISTRY):
        assert "`" + code + "`" in text
