"""Bundle tests for dev_tools/upgrade_gate.

All tests are offline and deterministic: fixtures under fixtures/ and the term
list under kb/ exercise the published v0.1 interface, including the three
reference examples, every closed-registry error code in its documented order,
every size cap, the eight verdict rules and their order, both version
comparators and range grammars, the window rules, the path-pattern matcher,
the no-echo rule, parser time bounds and bit-identical repeat execution
across hash seeds. No network access is required or permitted.
"""

import ast
import contextlib
import copy
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time

import jsonschema
import pytest
import yaml

from skill_assay import SkillContext
from skill_assay.core.loader import SkillLoader

from . import skill as skill_module
from .skill import (
    ACTIONS,
    ASSESS_CHECKS,
    ASSESS_OUTPUT_KEYS,
    BUNDLE_READS,
    CAP_TABLE,
    DIGEST_CHECKS,
    DIGEST_OUTPUT_KEYS,
    DRIFT_KEYS,
    DUPLICATE_DEPENDENCY,
    EMPTY_DEPENDENCIES,
    ERROR_REGISTRY,
    EVALUATION_ORDER,
    EVALUATION_ORDER_LINE,
    FAULT_REGISTRY,
    FUTURE_EVENT,
    FUTURE_RELEASE,
    INTERNAL_ERROR,
    INVALID_POLICY,
    INVALID_RANGE,
    INVALID_REQUEST,
    INVALID_VERSION,
    INVALID_WINDOW,
    JUMP_KEYS,
    MAX_ADVISORIES,
    MAX_ADVISORIES_PER_DEPENDENCY,
    MAX_DEPENDENCIES,
    MAX_EVENTS_PER_REPOSITORY,
    MAX_NAME_CHARS,
    MAX_NOTES_CHARS,
    MAX_PATH_CHARS,
    MAX_PATH_PATTERN_PAIRS,
    MAX_PATHS_PER_EVENT,
    MAX_PATTERN_CHARS,
    MAX_PATTERN_WILDCARDS,
    MAX_PATTERNS,
    MAX_RANGE_CHARS,
    MAX_RANGE_CLAUSES,
    MAX_RELEASE_ENTRIES,
    MAX_REPOSITORIES,
    MAX_TITLE_CHARS,
    MAX_TOTAL_EVENTS,
    MAX_TOTAL_NOTES_CHARS,
    MAX_TOTAL_PATH_CHARS,
    MAX_TOTAL_PATHS,
    MAX_TOTAL_TITLE_CHARS,
    MAX_TOTAL_VERSIONS,
    MAX_VERSION_CHARS,
    MAX_VERSIONS_PER_ENTRY,
    MISSING_AS_OF,
    PER_REPO_KEYS,
    REASON_CODES,
    RESULT_KEYS,
    RULES,
    SIGNAL_KEYS,
    SUMMARY_KEYS,
    UNKNOWN_ECOSYSTEM,
    VERDICTS,
    UpgradeGateSkill,
)

BUNDLE_DIR = os.path.dirname(os.path.abspath(__file__))
FIXTURES_DIR = os.path.join(BUNDLE_DIR, "fixtures")
KB_DIR = os.path.join(BUNDLE_DIR, "kb")
SCHEMAS_DIR = os.path.join(BUNDLE_DIR, "schemas")
REPO_ROOT = os.path.abspath(os.path.join(BUNDLE_DIR, "..", "..", ".."))
REGISTRY_ID = "dev_tools/upgrade_gate"

# Reference fixtures: (params file, expected file), one request each.
REFERENCE_FIXTURES = (
    ("example_assess_params.json", "expected_assess.json"),
    ("example_digest_params.json", "expected_digest.json"),
    ("example_future_event_params.json", "expected_future_event.json"),
)
# Case files: a list of named cases, each with its input and expected result.
CASE_FIXTURES = ("rule_cases.json", "age_cases.json", "window_cases.json")
EXPECTED_FIXTURE_FILES = (
    {name for pair in REFERENCE_FIXTURES for name in pair}
    | set(CASE_FIXTURES)
    | {
        "version_order_semver.json",
        "version_order_pep440.json",
        "range_vectors.json",
        "unsupported_forms.json",
    }
)
EXPECTED_SCHEMA_FILES = {
    "assess_request.schema.json",
    "digest_request.schema.json",
    "output.schema.json",
}
EXPECTED_KB_FILES = {"breaking_terms.json"}
EXPECTED_BUNDLE_FILES = {
    "__init__.py",
    "manifest.yaml",
    "skill.py",
    "instructions.md",
    "test_skill.py",
    "card.json",
}
BREAKING_TERMS = (
    "BREAKING",
    "INCOMPATIBLE",
    "REMOVED",
    "RENAMED",
    "DROPPED",
    "MIGRATION",
)
TIME_BOUND_SECONDS = 30.0
LONE_SURROGATE = "\ud800"
MARKER = "zq-marker-text"


# --------------------------------------------------------------------------
# Helpers and fixtures
# --------------------------------------------------------------------------


def _load_json(directory, name):
    with open(os.path.join(directory, name), "r", encoding="utf-8") as handle:
        return json.load(handle)


def _requests_in(payload):
    """The requests a fixture file holds: one, or the inputs of its cases."""
    if isinstance(payload, dict) and "cases" in payload:
        return [case["params"] for case in payload["cases"]]
    return [payload]


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _assert_error(result, code, reason=None):
    assert set(result) == {"status", "error"}
    assert result["status"] == "error"
    assert set(result["error"]) == {"code", "detail"}
    assert result["error"]["code"] == code
    detail = result["error"]["detail"]
    assert isinstance(detail, dict)
    if code == FUTURE_EVENT:
        # The one code whose detail carries the published shape and no reason.
        assert set(detail) == {"event_id", "at"}
        return detail
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


def _dependency(
    name="demo_http", ecosystem="pypi", current="2.3.1", version_range=">=2.3,<3"
):
    return {
        "name": name,
        "ecosystem": ecosystem,
        "current": current,
        "range": version_range,
    }


def _version(version="2.3.4", published_at="2026-09-01T00:00:00Z", **more):
    item = {"version": version, "published_at": published_at}
    item.update(more)
    return item


def _entry(name="demo_http", ecosystem="pypi", versions=None):
    return {
        "name": name,
        "ecosystem": ecosystem,
        "versions": [_version()] if versions is None else versions,
    }


def _advisory(
    affected="<2.3.2", ident="DEMO-2026-0001", name="demo_http", ecosystem="pypi"
):
    return {"id": ident, "name": name, "ecosystem": ecosystem, "affected": affected}


def _policy(**overrides):
    policy = {
        "allow_major": False,
        "allow_prerelease": False,
        "min_age_days": 3,
        "max_snapshot_age_days": 7,
    }
    policy.update(overrides)
    return policy


def _assess_params(**overrides):
    params = {
        "action": "assess",
        "as_of": "2026-09-12",
        "snapshot_at": "2026-09-11T02:00:00Z",
        "dependencies": [_dependency()],
        "releases": [_entry()],
        "policy": _policy(),
    }
    params.update(overrides)
    return params


def _event(kind="commit", ident="c1", at="2026-09-09T08:00:00Z", **more):
    event = {"type": kind, "id": ident, "at": at}
    event.update(more)
    return event


def _repository(repo="demo/kit", events=None):
    return {"repo": repo, "events": [_event()] if events is None else events}


def _digest_params(**overrides):
    params = {
        "action": "digest",
        "as_of": "2026-09-12",
        "since": "2026-09-05",
        "watched": [_repository()],
        "policy": {"paths_of_interest": ["src/config*"]},
    }
    params.update(overrides)
    return params


def _without(params, *keys):
    copied = copy.deepcopy(params)
    for key in keys:
        copied.pop(key, None)
    return copied


def _result(skill, params):
    result = skill.execute(copy.deepcopy(params))
    assert result["status"] == "completed", result
    return result


def _first_result(skill, params):
    return _result(skill, params)["results"][0]


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


@pytest.fixture
def skill():
    return UpgradeGateSkill()


@pytest.fixture
def manifest():
    with open(os.path.join(BUNDLE_DIR, "manifest.yaml"), "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture
def assess_params():
    return _load_json(FIXTURES_DIR, "example_assess_params.json")


@pytest.fixture
def digest_params():
    return _load_json(FIXTURES_DIR, "example_digest_params.json")


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
    assert len(EXPECTED_FIXTURE_FILES) == 13
    assert "manifest.yaml" not in EXPECTED_FIXTURE_FILES
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
    assert manifest["category"] == "dev_tools"
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
        "results",
        "summary",
        "window",
        "per_repo",
        "fired_any",
        "insufficient",
        "stale_snapshot",
    }


def test_skill_manifest_consistency(skill, manifest, assess_params, digest_params):
    assert skill.manifest["name"] == manifest["name"] == REGISTRY_ID
    assert skill.manifest["version"] == manifest["version"]
    assert skill.manifest["parameters"] == manifest["parameters"]
    assert "output" not in manifest
    seen = set()
    for params in (assess_params, digest_params):
        result = skill.execute(params)
        assert result["status"] == "completed"
        seen.update(result)
    assert set(manifest["outputs"]) == seen


def test_manifest_identity_matches_folder(manifest):
    category = os.path.basename(os.path.dirname(BUNDLE_DIR))
    folder = os.path.basename(BUNDLE_DIR)
    assert manifest["name"] == f"{category}/{folder}"


def test_manifest_parameters_validate_fixtures(skill):
    checked = 0
    for name in [params for params, _ in REFERENCE_FIXTURES] + list(CASE_FIXTURES):
        for params in _requests_in(_load_json(FIXTURES_DIR, name)):
            assert skill.validate_params(params) is True
            checked += 1
    assert checked > 60


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
    assert set(parameters["properties"]) == {
        "action",
        "as_of",
        "snapshot_at",
        "since",
        "policy",
        "dependencies",
        "releases",
        "advisories",
        "watched",
    }
    assert skill_module.MANIFEST_PARAMETERS == parameters


def test_framework_path_reaches_the_skill_checks():
    context = SkillContext(skill=REGISTRY_ID)
    result = context.execute(REGISTRY_ID, _assess_params(dependencies=[MARKER]))
    detail = _assert_error(result, INVALID_REQUEST, "not_an_object")
    assert detail["path"] == "dependencies[0]"
    assert MARKER not in json.dumps(result)
    result = context.execute(REGISTRY_ID, _digest_params(watched=[MARKER]))
    detail = _assert_error(result, INVALID_REQUEST, "not_an_object")
    assert detail["path"] == "watched[0]"
    assert MARKER not in json.dumps(result)


def test_skill_loader_can_import():
    bundle = SkillLoader.load_skill(REGISTRY_ID)
    assert bundle["manifest"]["name"] == REGISTRY_ID
    assert hasattr(bundle["module"], "UpgradeGateSkill")
    assert bundle["class"] is bundle["module"].UpgradeGateSkill
    assert bundle["instructions"].strip()
    assert bundle["card"]["issuer"]["email"] == bundle["manifest"]["issuer"]["email"]


def test_actions_are_exactly_the_published_set(
    skill, manifest, assess_params, digest_params
):
    assert ACTIONS == ("digest", "assess")
    assert manifest["parameters"]["properties"]["action"]["enum"] == list(ACTIONS)
    assert skill.execute(assess_params)["status"] == "completed"
    assert skill.execute(digest_params)["status"] == "completed"


@pytest.mark.parametrize("action", ["bogus", "", None, 3, ["assess"], "Assess"])
def test_unknown_action_is_a_contract_error(skill, assess_params, action):
    params = dict(assess_params)
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
    first = skill.execute(copy.deepcopy(params))
    second = skill.execute(copy.deepcopy(params))
    assert first == expected
    assert _canonical(first) == _canonical(second) == _canonical(expected)


HASH_SEED_SCRIPT = """
import hashlib
import json
import sys

from skill_assay.core.loader import SkillLoader

bundle = SkillLoader.load_skill("dev_tools/upgrade_gate")
skill = bundle["class"]()
digests = []
for path in json.loads(sys.argv[1]):
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, dict) and "cases" in payload:
        requests = [case["params"] for case in payload["cases"]]
    else:
        requests = [payload]
    results = [skill.execute(params) for params in requests]
    text = json.dumps(results, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    digests.append(hashlib.sha256(text.encode("utf-8")).hexdigest())
print(json.dumps(digests))
"""


def test_output_is_identical_across_hash_seeds():
    names = [name for name, _ in REFERENCE_FIXTURES] + list(CASE_FIXTURES)
    paths = [os.path.join(FIXTURES_DIR, name) for name in names]
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
        results = [_load_json(FIXTURES_DIR, expected_name)]
        expected.append(hashlib.sha256(_canonical(results).encode("utf-8")).hexdigest())
    for name in CASE_FIXTURES:
        results = [case["expected"] for case in _load_json(FIXTURES_DIR, name)["cases"]]
        expected.append(hashlib.sha256(_canonical(results).encode("utf-8")).hexdigest())
    assert outputs[0] == expected


# --------------------------------------------------------------------------
# Source checks (offline, stateless, standard library only)
# --------------------------------------------------------------------------

ALLOWED_IMPORTS = {"json", "os", "re"}
ALLOWED_FROM_IMPORTS = {
    "datetime": {"date", "datetime"},
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
    # The allow-list proves itself: yaml, packaging, fnmatch, glob and every
    # network module are rejected; only the named helpers of datetime pass.
    assert _disallowed_imports("import yaml\n") == ["yaml"]
    assert _disallowed_imports("from packaging.version import Version\n") == [
        "packaging.version"
    ]
    assert _disallowed_imports("import packaging\n") == ["packaging"]
    assert _disallowed_imports("import fnmatch\n") == ["fnmatch"]
    assert _disallowed_imports("import glob\n") == ["glob"]
    assert _disallowed_imports("import datetime\n") == ["datetime"]
    assert _disallowed_imports("from datetime import timezone\n") == [
        "datetime.timezone"
    ]
    assert _disallowed_imports("from datetime import date, datetime\n") == []
    for module in (
        "socket",
        "http",
        "requests",
        "ssl",
        "ftplib",
        "subprocess",
        "urllib",
    ):
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
    assert open_calls == 1 == len(BUNDLE_READS)
    assert BUNDLE_READS == ("kb/breaking_terms.json",)
    for name in ("socket", "http", "urllib", "requests", "ssl", "yaml", "packaging"):
        assert not hasattr(skill_module, name)


def test_breaking_terms_file_is_closed():
    with open(os.path.join(KB_DIR, "breaking_terms.json"), "rb") as handle:
        raw = handle.read()
    assert raw.endswith(b"\n") and not raw.endswith(b"\n\n")
    assert all(byte < 128 for byte in raw)
    text = raw.decode("ascii")
    assert text == json.dumps(json.loads(text), indent=2) + "\n"
    data = json.loads(text)
    assert data == {
        "schema_version": 1,
        "description": (
            "Breaking-change terms for release notes and event titles, written "
            "for this bundle. A term matches a whole upper-cased token of ASCII "
            "letters and digits."
        ),
        "terms": list(BREAKING_TERMS),
    }
    assert len(data["terms"]) == 6
    assert skill_module._load_breaking_terms() == list(BREAKING_TERMS)


# --------------------------------------------------------------------------
# Error registry, per-code cases, documented order
# --------------------------------------------------------------------------


def test_error_registry_is_closed_and_final():
    assert ERROR_REGISTRY == frozenset(
        {
            "INVALID_REQUEST",
            "MISSING_AS_OF",
            "INVALID_WINDOW",
            "FUTURE_EVENT",
            "EMPTY_DEPENDENCIES",
            "DUPLICATE_DEPENDENCY",
            "UNKNOWN_ECOSYSTEM",
            "INVALID_VERSION",
            "INVALID_RANGE",
            "FUTURE_RELEASE",
            "INVALID_POLICY",
        }
    )
    assert len(ERROR_REGISTRY) == 11
    assert FAULT_REGISTRY == frozenset({"INTERNAL_ERROR"})
    assert INTERNAL_ERROR not in ERROR_REGISTRY
    assert "UNKNOWN_ACTION" not in ERROR_REGISTRY
    assert {code for code, _ in ASSESS_CHECKS} <= ERROR_REGISTRY
    assert {code for code, _ in DIGEST_CHECKS} <= ERROR_REGISTRY
    assert {code for code, _ in ASSESS_CHECKS} | {
        code for code, _ in DIGEST_CHECKS
    } == (ERROR_REGISTRY)
    assert len(ASSESS_CHECKS) == 26
    assert len(DIGEST_CHECKS) == 13


ERROR_CASES = {
    INVALID_REQUEST: lambda: _assess_params(action="bogus"),
    MISSING_AS_OF: lambda: _without(_assess_params(), "as_of"),
    INVALID_WINDOW: lambda: _digest_params(since="2026-09-13"),
    FUTURE_EVENT: lambda: _load_json(FIXTURES_DIR, "example_future_event_params.json"),
    EMPTY_DEPENDENCIES: lambda: _assess_params(dependencies=[]),
    DUPLICATE_DEPENDENCY: lambda: _assess_params(
        dependencies=[_dependency(), _dependency(name="Demo-HTTP")]
    ),
    UNKNOWN_ECOSYSTEM: lambda: _assess_params(
        dependencies=[_dependency(ecosystem="gem")]
    ),
    INVALID_VERSION: lambda: _assess_params(
        dependencies=[_dependency(current="v2.3.1")]
    ),
    INVALID_RANGE: lambda: _assess_params(
        dependencies=[_dependency(version_range="~=2")]
    ),
    FUTURE_RELEASE: lambda: _assess_params(
        releases=[_entry(versions=[_version(published_at="2026-09-13T00:00:00Z")])]
    ),
    INVALID_POLICY: lambda: _assess_params(policy=_policy(strict="yes")),
}


def test_error_cases_cover_registry():
    assert set(ERROR_CASES) == ERROR_REGISTRY


@pytest.mark.parametrize("code", sorted(ERROR_CASES))
def test_each_error_code(skill, code):
    _assert_error(skill.execute(ERROR_CASES[code]()), code)


def _unreachable(reason):
    return reason


def _many(builder, count, **kwargs):
    return [
        builder(**{key: value.format(i=i) for key, value in kwargs.items()})
        for i in range(count)
    ]


LONG_NAME = "demo" + "-" * 97 + "http"  # normalises to demo-http; 105 characters

# An exercised pair is (input builder, reason of check i) or, where check i
# has a fixed path, (input builder, reason, path); a string records why no
# single input can violate both checks of the pair.
ASSESS_PAIRS = {
    (1, 2): _unreachable("check 2 needs an object, which check 1 rejects"),
    (2, 3): (
        lambda: _assess_params(action="bogus", bogus_key=1),
        "unknown_action",
        "action",
    ),
    (3, 4): (lambda: _without(_assess_params(bogus_key=1), "as_of"), "unknown_key"),
    (4, 5): _unreachable(
        "check 5 needs a present as_of, whose absence check 4 reports"
    ),
    (5, 6): (
        lambda: _without(_assess_params(as_of="2026-13-01"), "snapshot_at"),
        "invalid_date",
        "as_of",
    ),
    (6, 7): (
        lambda: _without(_assess_params(snapshot_at="2026-09-11"), "policy"),
        "invalid_timestamp",
        "snapshot_at",
    ),
    (7, 8): (
        lambda: _assess_params(policy={}, dependencies="demo_http"),
        "missing_key",
        "policy.allow_major",
    ),
    (8, 9): (
        lambda: _assess_params(dependencies=[], releases={}),
        "not_a_list",
        "releases",
    ),
    (9, 10): (
        lambda: _assess_params(
            dependencies=[],
            releases=_many(_entry, MAX_RELEASE_ENTRIES + 1, name="demo_pkg_{i}"),
        ),
        "empty_dependencies",
        "dependencies",
    ),
    (10, 11): (
        lambda: _assess_params(
            dependencies=[{**_dependency(), "bogus": 1}]
            + _many(_dependency, MAX_DEPENDENCIES, name="demo_pkg_{i}")
        ),
        "size_cap",
        "dependencies",
    ),
    (11, 12): (
        lambda: _assess_params(
            dependencies=[{**_dependency(ecosystem="gem"), "bogus": 1}]
        ),
        "unknown_key",
        "dependencies[0]",
    ),
    (12, 13): (
        lambda: _assess_params(
            dependencies=[_dependency(name="-bad", ecosystem="gem")]
        ),
        "unknown_ecosystem",
        "dependencies[0].ecosystem",
    ),
    (13, 14): (
        lambda: _assess_params(
            dependencies=[_dependency(), _dependency(name=LONG_NAME)]
        ),
        "invalid_name",
        "dependencies[1].name",
    ),
    (14, 15): (
        lambda: _assess_params(
            dependencies=[_dependency(), _dependency(current="bad")]
        ),
        "duplicate_dependency",
        "dependencies[1]",
    ),
    (15, 16): (
        lambda: _assess_params(
            dependencies=[_dependency(current="bad", version_range="bad")]
        ),
        "invalid_version",
        "dependencies[0].current",
    ),
    (16, 17): (
        lambda: _assess_params(
            dependencies=[_dependency(version_range="bad")], releases=["x"]
        ),
        "invalid_range",
        "dependencies[0].range",
    ),
    (17, 18): (
        lambda: _assess_params(releases=[{**_entry(ecosystem="gem"), "bogus": 1}]),
        "unknown_key",
        "releases[0]",
    ),
    (18, 19): (
        lambda: _assess_params(releases=[_entry(name="-bad", ecosystem="gem")]),
        "unknown_ecosystem",
        "releases[0].ecosystem",
    ),
    (19, 20): (
        lambda: _assess_params(releases=[_entry(name="-bad", versions=["x"])]),
        "invalid_name",
        "releases[0].name",
    ),
    (20, 21): (
        lambda: _assess_params(
            releases=[_entry(versions=[{**_version("bad"), "bogus": 1}])]
        ),
        "unknown_key",
        "releases[0].versions[0]",
    ),
    (21, 22): (
        lambda: _assess_params(
            releases=[_entry(versions=[_version("bad", "2026-09-13T00:00:00Z")])]
        ),
        "invalid_version",
        "releases[0].versions[0].version",
    ),
    (22, 23): (
        lambda: _assess_params(
            releases=[_entry(versions=[_version(published_at="2026-09-13T00:00:00Z")])],
            advisories=["x"],
        ),
        "future_release",
        "releases[0].versions[0].published_at",
    ),
    (23, 24): (
        lambda: _assess_params(advisories=[{**_advisory(ecosystem="gem"), "bogus": 1}]),
        "unknown_key",
        "advisories[0]",
    ),
    (24, 25): (
        lambda: _assess_params(advisories=[_advisory(name="-bad", ecosystem="gem")]),
        "unknown_ecosystem",
        "advisories[0].ecosystem",
    ),
    (25, 26): (
        lambda: _assess_params(advisories=[_advisory(affected="bad", name="-bad")]),
        "invalid_name",
        "advisories[0].name",
    ),
}

DIGEST_PAIRS = {
    (1, 2): _unreachable("check 2 needs an object, which check 1 rejects"),
    (2, 3): (
        lambda: _digest_params(action="bogus", bogus_key=1),
        "unknown_action",
        "action",
    ),
    (3, 4): (lambda: _without(_digest_params(bogus_key=1), "as_of"), "unknown_key"),
    (4, 5): _unreachable(
        "check 5 needs a present as_of, whose absence check 4 reports"
    ),
    (5, 6): (
        lambda: _without(_digest_params(as_of="2026-02-30"), "since"),
        "invalid_date",
        "as_of",
    ),
    (6, 7): (
        lambda: _without(_digest_params(since="2026-09-05T00:00:00Z"), "watched"),
        "invalid_since",
        "since",
    ),
    (7, 8): (
        lambda: _digest_params(watched="demo/kit", policy={"bogus": 1}),
        "not_a_list",
        "watched",
    ),
    (8, 9): (
        lambda: _digest_params(
            policy={"bogus": 1},
            watched=_many(_repository, MAX_REPOSITORIES + 1, repo="demo/kit-{i}"),
        ),
        "unknown_key",
        "policy",
    ),
    (9, 10): (
        lambda: _digest_params(
            policy={"paths_of_interest": ["src/[a]"]},
            watched=_many(_repository, MAX_REPOSITORIES + 1, repo="demo/kit-{i}"),
        ),
        "size_cap",
        "watched",
    ),
    (10, 11): (
        lambda: _digest_params(
            policy={"paths_of_interest": ["src/[a]"]}, watched=["x"]
        ),
        "invalid_character",
        "policy.paths_of_interest[0]",
    ),
    (11, 12): (
        lambda: _digest_params(watched=[{**_repository(events=["x"]), "bogus": 1}]),
        "unknown_key",
        "watched[0]",
    ),
    (12, 13): (
        lambda: _digest_params(
            watched=[
                _repository(events=[{**_event(at="2026-09-13T00:00:00Z"), "bogus": 1}])
            ]
        ),
        "unknown_key",
        "watched[0].events[0]",
    ),
}


def _pair_cases():
    cases = []
    for action, table in (("assess", ASSESS_PAIRS), ("digest", DIGEST_PAIRS)):
        for (first, second), entry in sorted(table.items()):
            cases.append(
                pytest.param(
                    action, first, second, entry, id=f"{action}-{first}-{second}"
                )
            )
    return cases


def test_pairwise_table_covers_every_adjacent_pair():
    for table, checks in ((ASSESS_PAIRS, ASSESS_CHECKS), (DIGEST_PAIRS, DIGEST_CHECKS)):
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


@pytest.mark.parametrize("action,first,second,entry", _pair_cases())
def test_first_violation_wins_in_documented_order(skill, action, first, second, entry):
    checks = ASSESS_CHECKS if action == "assess" else DIGEST_CHECKS
    if isinstance(entry, str):
        assert entry  # recorded reason: no single input violates both checks
        return
    builder, reason, *path = entry
    detail = _assert_error(skill.execute(builder()), checks[first - 1][0], reason)
    if path:
        assert detail["path"] == path[0]


def test_internal_fault_returns_an_error_envelope(skill, monkeypatch, assess_params):
    def boom(params, terms):
        raise RuntimeError("synthetic fault")

    monkeypatch.setattr(skill_module, "_assess", boom)
    result = skill.execute(assess_params)
    assert result == {
        "status": "error",
        "error": {
            "code": INTERNAL_ERROR,
            "detail": {"reason": "internal_error", "exception": "RuntimeError"},
        },
    }

    def interrupt(params, terms):
        raise KeyboardInterrupt()

    monkeypatch.setattr(skill_module, "_digest", interrupt)
    with pytest.raises(KeyboardInterrupt):
        skill.execute({"action": "digest"})


def test_error_envelope_and_completed_run_never_mix(
    skill, assess_params, digest_params
):
    for params in (assess_params, digest_params):
        completed = skill.execute(params)
        assert completed["status"] == "completed"
        assert "error" not in completed
    errored = skill.execute({**assess_params, "policy": {}})
    assert set(errored) == {"status", "error"}
    assert errored["error"]["code"] in ERROR_REGISTRY


@pytest.mark.parametrize(
    "mutate,code,reason",
    [
        (lambda p: p["policy"].update(allow_major=1), INVALID_POLICY, "not_a_boolean"),
        (
            lambda p: p["policy"].update(allow_prerelease="no"),
            INVALID_POLICY,
            "not_a_boolean",
        ),
        (
            lambda p: p["policy"].update(min_age_days=True),
            INVALID_POLICY,
            "not_an_integer",
        ),
        (
            lambda p: p["policy"].update(min_age_days="3"),
            INVALID_POLICY,
            "not_an_integer",
        ),
        (
            lambda p: p["policy"].update(min_age_days=3.0),
            INVALID_POLICY,
            "not_an_integer",
        ),
        (lambda p: p["policy"].update(min_age_days=-1), INVALID_POLICY, "out_of_range"),
        (
            lambda p: p["policy"].update(max_snapshot_age_days=3651),
            INVALID_POLICY,
            "out_of_range",
        ),
        (
            lambda p: p["policy"].update(max_snapshot_age_days=None),
            INVALID_POLICY,
            "not_an_integer",
        ),
        (lambda p: p.update(as_of=20260912), INVALID_REQUEST, "invalid_date"),
        (lambda p: p.update(as_of=None), MISSING_AS_OF, "missing_as_of"),
        (lambda p: p.update(snapshot_at=None), INVALID_REQUEST, "invalid_timestamp"),
        (lambda p: p.update(dependencies=None), INVALID_REQUEST, "not_a_list"),
        (lambda p: p.update(releases=None), INVALID_REQUEST, "not_a_list"),
        (lambda p: p.update(advisories={}), INVALID_REQUEST, "not_a_list"),
        (
            lambda p: p["dependencies"][0].update(current=2),
            INVALID_REQUEST,
            "invalid_type",
        ),
        (
            lambda p: p["dependencies"][0].update(ecosystem=1),
            INVALID_REQUEST,
            "not_a_string",
        ),
        (
            lambda p: p["releases"][0]["versions"][0].update(yanked="yes"),
            INVALID_REQUEST,
            "not_a_boolean",
        ),
        (
            lambda p: p["releases"][0]["versions"][0].update(yanked=0),
            INVALID_REQUEST,
            "not_a_boolean",
        ),
        (
            lambda p: p["releases"][0]["versions"][0].update(notes=5),
            INVALID_REQUEST,
            "invalid_type",
        ),
        (
            lambda p: p["releases"][0]["versions"][0].update(published_at=5),
            INVALID_REQUEST,
            "not_a_string",
        ),
    ],
)
def test_type_rules_are_strict(skill, mutate, code, reason):
    params = _assess_params()
    mutate(params)
    _assert_error(skill.execute(params), code, reason)


@pytest.mark.parametrize(
    "mutate,code,reason",
    [
        (lambda p: p.update(since=None), INVALID_WINDOW, "invalid_since"),
        (lambda p: p.update(since=20260905), INVALID_WINDOW, "invalid_since"),
        (lambda p: p.update(watched={}), INVALID_REQUEST, "not_a_list"),
        (lambda p: p.update(policy=[]), INVALID_REQUEST, "not_an_object"),
        (
            lambda p: p["policy"].update(paths_of_interest="src/*"),
            INVALID_POLICY,
            "not_a_string_list",
        ),
        (
            lambda p: p["policy"].update(paths_of_interest=["src/*", 1]),
            INVALID_POLICY,
            "not_a_string_list",
        ),
        (
            lambda p: p["watched"][0]["events"][0].update(type=1),
            INVALID_REQUEST,
            "unknown_event_type",
        ),
        (
            lambda p: p["watched"][0]["events"][0].update(type="push"),
            INVALID_REQUEST,
            "unknown_event_type",
        ),
        (
            lambda p: p["watched"][0]["events"][0].update(id=7),
            INVALID_REQUEST,
            "invalid_id",
        ),
        (
            lambda p: p["watched"][0]["events"][0].update(at=5),
            INVALID_REQUEST,
            "invalid_timestamp",
        ),
        (
            lambda p: p["watched"][0]["events"][0].update(title=5),
            INVALID_REQUEST,
            "invalid_type",
        ),
        (
            lambda p: p["watched"][0]["events"][0].update(paths=["a", 1]),
            INVALID_REQUEST,
            "invalid_type",
        ),
        (
            lambda p: p["watched"][0]["events"][0].update(paths="a"),
            INVALID_REQUEST,
            "invalid_type",
        ),
        (lambda p: p["watched"][0].update(repo=1), INVALID_REQUEST, "invalid_repo"),
    ],
)
def test_digest_type_rules_are_strict(skill, mutate, code, reason):
    params = _digest_params()
    mutate(params)
    _assert_error(skill.execute(params), code, reason)


# --------------------------------------------------------------------------
# Whole-string patterns, lone surrogates, crafted inputs, key order
# --------------------------------------------------------------------------

LINE_FEED_CASES = {
    "as_of": lambda: (
        _assess_params(as_of="2026-09-12\n"),
        INVALID_REQUEST,
        "invalid_date",
    ),
    "snapshot_at": lambda: (
        _assess_params(snapshot_at="2026-09-11T02:00:00Z\n"),
        INVALID_REQUEST,
        "invalid_timestamp",
    ),
    "since": lambda: (
        _digest_params(since="2026-09-05\n"),
        INVALID_WINDOW,
        "invalid_since",
    ),
    "events[].at": lambda: (
        _digest_params(
            watched=[_repository(events=[_event(at="2026-09-09T08:00:00Z\n")])]
        ),
        INVALID_REQUEST,
        "invalid_timestamp",
    ),
    "dependencies[].name pypi": lambda: (
        _assess_params(dependencies=[_dependency(name="demo_http\n")]),
        INVALID_REQUEST,
        "invalid_name",
    ),
    "dependencies[].name npm": lambda: (
        _assess_params(
            dependencies=[
                _dependency(
                    name="demo-yaml\n",
                    ecosystem="npm",
                    current="1.0.0",
                    version_range="^1.0.0",
                )
            ]
        ),
        INVALID_REQUEST,
        "invalid_name",
    ),
    "dependencies[].current": lambda: (
        _assess_params(dependencies=[_dependency(current="2.3.1\n")]),
        INVALID_VERSION,
        "invalid_version",
    ),
    "dependencies[].range": lambda: (
        _assess_params(dependencies=[_dependency(version_range=">=2.3,<3\n")]),
        INVALID_RANGE,
        "invalid_range",
    ),
    "advisories[].id": lambda: (
        _assess_params(advisories=[_advisory(ident="DEMO-2026-0001\n")]),
        INVALID_REQUEST,
        "invalid_id",
    ),
    "events[].id": lambda: (
        _digest_params(watched=[_repository(events=[_event(ident="c1\n")])]),
        INVALID_REQUEST,
        "invalid_id",
    ),
    "watched[].repo": lambda: (
        _digest_params(watched=[_repository(repo="demo/kit\n")]),
        INVALID_REQUEST,
        "invalid_repo",
    ),
    "policy.paths_of_interest[]": lambda: (
        _digest_params(policy={"paths_of_interest": ["src/config*\n"]}),
        INVALID_POLICY,
        "invalid_character",
    ),
}


@pytest.mark.parametrize("field", sorted(LINE_FEED_CASES))
def test_patterns_reject_a_trailing_line_feed(skill, field):
    params, code, reason = LINE_FEED_CASES[field]()
    _assert_error(skill.execute(params), code, reason)


def _huge_version():
    return "1." + "9" * 4_999


def _nine_clauses():
    return ",".join(
        [">=1.0", "<9", "!=1.1", "!=1.2", "!=1.3", "!=1.4", "!=1.5", "!=1.6", "!=1.7"]
    )


CRAFTED_CASES = {
    "huge-min-age": lambda: _assess_params(policy=_policy(min_age_days=10**5000)),
    "huge-max-snapshot-age": lambda: _assess_params(
        policy=_policy(max_snapshot_age_days=10**5000)
    ),
    "negative-huge-min-age": lambda: _assess_params(
        policy=_policy(min_age_days=-(10**5000))
    ),
    "boolean-min-age": lambda: _assess_params(policy=_policy(min_age_days=True)),
    "boolean-max-snapshot-age": lambda: _assess_params(
        policy=_policy(max_snapshot_age_days=False)
    ),
    "huge-current": lambda: _assess_params(
        dependencies=[_dependency(current=_huge_version())]
    ),
    "huge-release-version": lambda: _assess_params(
        releases=[_entry(versions=[_version(_huge_version())])]
    ),
    "nine-clause-range": lambda: _assess_params(
        dependencies=[_dependency(version_range=_nine_clauses())]
    ),
    "nine-clause-affected": lambda: _assess_params(
        advisories=[_advisory(affected=_nine_clauses())]
    ),
    "nine-comparator-npm-range": lambda: _assess_params(
        dependencies=[
            _dependency(
                ecosystem="npm",
                current="1.0.0",
                version_range=" ".join([">=1.0.0"] * 9),
            )
        ]
    ),
    "surrogate-in-dependency-name": lambda: _assess_params(
        dependencies=[_dependency(name="demo" + LONE_SURROGATE)]
    ),
    "surrogate-in-entry-name": lambda: _assess_params(
        releases=[_entry(name="demo" + LONE_SURROGATE)]
    ),
    "surrogate-in-advisory-id": lambda: _assess_params(
        advisories=[_advisory(ident="DEMO" + LONE_SURROGATE)]
    ),
    "surrogate-in-advisory-name": lambda: _assess_params(
        advisories=[_advisory(name="demo" + LONE_SURROGATE)]
    ),
    "surrogate-in-event-id": lambda: _digest_params(
        watched=[_repository(events=[_event(ident="c" + LONE_SURROGATE)])]
    ),
    "surrogate-in-repo": lambda: _digest_params(
        watched=[_repository(repo="demo/kit" + LONE_SURROGATE)]
    ),
    "surrogate-in-title": lambda: _digest_params(
        watched=[_repository(events=[_event(title="BREAKING" + LONE_SURROGATE)])]
    ),
    "surrogate-in-notes": lambda: _assess_params(
        releases=[_entry(versions=[_version(notes="BREAKING" + LONE_SURROGATE)])]
    ),
    "surrogate-in-path": lambda: _digest_params(
        watched=[_repository(events=[_event(paths=["src/" + LONE_SURROGATE])])]
    ),
    "surrogate-in-pattern": lambda: _digest_params(
        policy={"paths_of_interest": ["src/" + LONE_SURROGATE]}
    ),
    "surrogate-in-version": lambda: _assess_params(
        dependencies=[_dependency(current="2.3.1" + LONE_SURROGATE)]
    ),
    "surrogate-in-range": lambda: _assess_params(
        dependencies=[_dependency(version_range=">=2" + LONE_SURROGATE)]
    ),
    "surrogate-in-as-of": lambda: _assess_params(as_of="2026-09-12" + LONE_SURROGATE),
    "surrogate-in-action": lambda: _assess_params(action="assess" + LONE_SURROGATE),
    "list-at-top-level": lambda: [_assess_params()],
    "string-at-top-level": lambda: "assess",
    "none-at-top-level": lambda: None,
    "list-for-dependency": lambda: _assess_params(dependencies=[["demo_http"]]),
    "list-for-release-entry": lambda: _assess_params(releases=[[]]),
    "list-for-version": lambda: _assess_params(releases=[_entry(versions=[["2.3.4"]])]),
    "list-for-advisory": lambda: _assess_params(advisories=[[]]),
    "list-for-policy": lambda: _assess_params(policy=[]),
    "list-for-digest-policy": lambda: _digest_params(policy=[]),
    "list-for-repository": lambda: _digest_params(watched=[["demo/kit"]]),
    "list-for-event": lambda: _digest_params(watched=[_repository(events=[["c1"]])]),
    "object-for-dependencies": lambda: _assess_params(
        dependencies={"name": "demo_http"}
    ),
    "object-for-releases": lambda: _assess_params(releases={}),
    "object-for-versions": lambda: _assess_params(releases=[_entry(versions={})]),
    "object-for-advisories": lambda: _assess_params(advisories={}),
    "object-for-watched": lambda: _digest_params(watched={}),
    "object-for-events": lambda: _digest_params(watched=[_repository(events={})]),
    "object-for-paths": lambda: _digest_params(
        watched=[_repository(events=[_event(paths={})])]
    ),
    "object-for-patterns": lambda: _digest_params(policy={"paths_of_interest": {}}),
    "null-for-version-fields": lambda: _assess_params(
        releases=[_entry(versions=[{"version": None, "published_at": None}])]
    ),
    "integer-for-as-of": lambda: _assess_params(as_of=20260912),
    "list-for-snapshot-at": lambda: _assess_params(snapshot_at=[]),
    "object-for-since": lambda: _digest_params(since={}),
    "float-for-policy-integer": lambda: _assess_params(
        policy=_policy(min_age_days=1.5)
    ),
    "nul-in-version": lambda: _assess_params(
        dependencies=[_dependency(current="2.3.1\x00")]
    ),
    "nul-in-pattern": lambda: _digest_params(
        policy={"paths_of_interest": ["src/\x00"]}
    ),
    "deeply-nested-range": lambda: _assess_params(
        dependencies=[_dependency(version_range="(" * 100 + ">=1" + ")" * 100)]
    ),
    "ranges-of-stars": lambda: _digest_params(
        policy={"paths_of_interest": ["*" * 100]}
    ),
    "long-pattern-of-wildcards": lambda: _digest_params(
        policy={"paths_of_interest": ["?" * 300]}
    ),
}


@pytest.mark.parametrize("case", sorted(CRAFTED_CASES))
def test_crafted_inputs_never_reach_internal_error(skill, case):
    result = skill.execute(CRAFTED_CASES[case]())
    assert result["status"] == "error", result
    assert result["error"]["code"] != INTERNAL_ERROR, result
    assert result["error"]["code"] in ERROR_REGISTRY
    text = json.dumps(result, ensure_ascii=False)
    assert LONE_SURROGATE not in text
    assert "9999" not in text and "\x00" not in text


def _in_both_orders(build, items):
    """``build`` applied to ``items`` in the given order and in reverse."""
    return build(list(items)), build(list(reversed(items)))


def test_error_details_do_not_depend_on_key_order(skill):
    def request(extra):
        return skill.execute({**_assess_params(), **dict(extra)})

    def dependency(fields):
        return skill.execute(_assess_params(dependencies=[dict(fields)]))

    def version(fields):
        return skill.execute(_assess_params(releases=[_entry(versions=[dict(fields)])]))

    def policy(fields):
        return skill.execute(_assess_params(policy=dict(fields)))

    # Two keys of the other action: the alphabetically first one is named.
    first, second = _in_both_orders(request, [("watched", []), ("since", "2026-09-05")])
    assert first == second
    assert _assert_error(first, INVALID_REQUEST, "unknown_key")["path"] == "since"
    # A key of the other action and an unknown key.
    first, second = _in_both_orders(request, [("unexpected", 1), ("watched", [])])
    assert first == second
    assert first["error"]["detail"]["path"] == "watched"
    # A dependency with two unknown keys, neither of which is echoed.
    first, second = _in_both_orders(
        dependency, list(_dependency().items()) + [("zq_one", 1), ("zq_two", 2)]
    )
    assert first == second
    assert _assert_error(first, INVALID_REQUEST, "unknown_key") == {
        "reason": "unknown_key",
        "path": "dependencies[0]",
    }
    assert "zq_" not in json.dumps(first)
    # A dependency with two faulty values: the field order decides.
    first, second = _in_both_orders(
        dependency,
        [("range", 1), ("current", 2), ("ecosystem", "pypi"), ("name", "demo_http")],
    )
    assert first == second
    assert first["error"]["detail"]["path"] == "dependencies[0].current"
    # A version with a bad yanked and a bad published_at, both faults of
    # check 20: the field order reports published_at.
    first, second = _in_both_orders(
        version, [("yanked", "yes"), ("published_at", 5), ("version", "2.3.4")]
    )
    assert first == second
    assert first["error"]["detail"]["path"] == "releases[0].versions[0].published_at"
    # A bad notes value is counted by check 10, before check 20 sees yanked.
    first, second = _in_both_orders(
        version,
        [
            ("notes", 5),
            ("yanked", "yes"),
            ("published_at", "2026-09-01T00:00:00Z"),
            ("version", "2.3.4"),
        ],
    )
    assert first == second
    assert _assert_error(first, INVALID_REQUEST, "invalid_type")["path"] == (
        "releases[0].versions[0].notes"
    )
    # A policy with two faulty values: allow_major is reported first.
    first, second = _in_both_orders(
        policy,
        [
            ("min_age_days", "x"),
            ("allow_major", 1),
            ("allow_prerelease", False),
            ("max_snapshot_age_days", 7),
        ],
    )
    assert first == second
    assert first["error"]["detail"]["path"] == "policy.allow_major"


# --------------------------------------------------------------------------
# Size caps
# --------------------------------------------------------------------------


def _versions(count, start=1):
    return [_version(f"1.0.{start + i}") for i in range(count)]


def _entries(count, versions_each):
    return [
        _entry(name=f"demo_pkg_{i}", versions=_versions(versions_each))
        for i in range(count)
    ]


def _events(count, **more):
    return [_event(ident=f"c{i}", **more) for i in range(count)]


def _repositories(count, events_each, **more):
    return [
        _repository(repo=f"demo/kit-{i}", events=_events(events_each, **more))
        for i in range(count)
    ]


# (field, unit) -> builder returning (params, code, limit[, monkeypatches]).
SIZE_CAP_CASES = {
    ("dependencies", "entries"): lambda: (
        _assess_params(
            dependencies=_many(_dependency, MAX_DEPENDENCIES + 1, name="demo_pkg_{i}")
        ),
        INVALID_REQUEST,
        MAX_DEPENDENCIES,
    ),
    ("releases", "entries"): lambda: (
        _assess_params(releases=_entries(MAX_RELEASE_ENTRIES + 1, 1)),
        INVALID_REQUEST,
        MAX_RELEASE_ENTRIES,
    ),
    ("releases[].versions", "entries"): lambda: (
        _assess_params(
            releases=[_entry(versions=_versions(MAX_VERSIONS_PER_ENTRY + 1))]
        ),
        INVALID_REQUEST,
        MAX_VERSIONS_PER_ENTRY,
    ),
    ("releases (all versions together)", "entries"): lambda: (
        _assess_params(
            releases=_entries(
                MAX_TOTAL_VERSIONS // MAX_VERSIONS_PER_ENTRY + 1, MAX_VERSIONS_PER_ENTRY
            )
        ),
        INVALID_REQUEST,
        MAX_TOTAL_VERSIONS,
    ),
    ("advisories", "entries"): lambda: (
        _assess_params(
            advisories=_many(
                _advisory, MAX_ADVISORIES + 1, ident="DEMO-{i}", name="demo_pkg_{i}"
            )
        ),
        INVALID_REQUEST,
        MAX_ADVISORIES,
    ),
    ("advisories (per dependency)", "entries"): lambda: (
        _assess_params(
            advisories=_many(
                _advisory, MAX_ADVISORIES_PER_DEPENDENCY + 1, ident="DEMO-{i}"
            )
        ),
        INVALID_REQUEST,
        MAX_ADVISORIES_PER_DEPENDENCY,
    ),
    ("name", "characters"): lambda: (
        _assess_params(dependencies=[_dependency(name="d" * (MAX_NAME_CHARS + 1))]),
        INVALID_REQUEST,
        MAX_NAME_CHARS,
    ),
    ("version", "characters"): lambda: (
        _assess_params(
            dependencies=[_dependency(current="1." + "9" * (MAX_VERSION_CHARS - 1))]
        ),
        INVALID_REQUEST,
        MAX_VERSION_CHARS,
    ),
    ("range", "characters"): lambda: (
        _assess_params(
            dependencies=[_dependency(version_range=">=" + "9" * (MAX_RANGE_CHARS - 1))]
        ),
        INVALID_REQUEST,
        MAX_RANGE_CHARS,
    ),
    ("range", "clauses"): lambda: (
        _assess_params(dependencies=[_dependency(version_range=_nine_clauses())]),
        INVALID_REQUEST,
        MAX_RANGE_CLAUSES,
    ),
    ("releases[].versions[].notes", "characters"): lambda: (
        _assess_params(
            releases=[_entry(versions=[_version(notes="n" * (MAX_NOTES_CHARS + 1))])]
        ),
        INVALID_REQUEST,
        MAX_NOTES_CHARS,
    ),
    ("releases (all notes together)", "characters"): lambda: (
        _assess_params(
            releases=[
                _entry(
                    versions=[
                        _version(f"1.0.{i}", notes="n" * MAX_NOTES_CHARS)
                        for i in range(MAX_TOTAL_NOTES_CHARS // MAX_NOTES_CHARS + 1)
                    ]
                )
            ]
        ),
        INVALID_REQUEST,
        MAX_TOTAL_NOTES_CHARS,
    ),
    ("watched", "entries"): lambda: (
        _digest_params(watched=_repositories(MAX_REPOSITORIES + 1, 1)),
        INVALID_REQUEST,
        MAX_REPOSITORIES,
    ),
    ("watched[].events", "entries"): lambda: (
        _digest_params(
            watched=[_repository(events=_events(MAX_EVENTS_PER_REPOSITORY + 1))]
        ),
        INVALID_REQUEST,
        MAX_EVENTS_PER_REPOSITORY,
    ),
    ("watched (all events together)", "entries"): lambda: (
        _digest_params(
            watched=_repositories(
                MAX_TOTAL_EVENTS // MAX_EVENTS_PER_REPOSITORY + 1,
                MAX_EVENTS_PER_REPOSITORY,
            )
        ),
        INVALID_REQUEST,
        MAX_TOTAL_EVENTS,
    ),
    ("watched[].events[].title", "characters"): lambda: (
        _digest_params(
            watched=[_repository(events=[_event(title="t" * (MAX_TITLE_CHARS + 1))])]
        ),
        INVALID_REQUEST,
        MAX_TITLE_CHARS,
    ),
    ("watched (all titles together)", "characters"): lambda: (
        _digest_params(
            watched=[
                _repository(
                    events=_events(
                        MAX_TOTAL_TITLE_CHARS // MAX_TITLE_CHARS + 1,
                        title="t" * MAX_TITLE_CHARS,
                    )
                )
            ]
        ),
        INVALID_REQUEST,
        MAX_TOTAL_TITLE_CHARS,
    ),
    ("watched[].events[].paths", "entries"): lambda: (
        _digest_params(
            watched=[
                _repository(events=[_event(paths=["p"] * (MAX_PATHS_PER_EVENT + 1))])
            ]
        ),
        INVALID_REQUEST,
        MAX_PATHS_PER_EVENT,
    ),
    ("watched[].events[].paths[]", "characters"): lambda: (
        _digest_params(
            watched=[_repository(events=[_event(paths=["p" * (MAX_PATH_CHARS + 1)])])]
        ),
        INVALID_REQUEST,
        MAX_PATH_CHARS,
    ),
    ("watched (all paths together)", "entries"): lambda: (
        _digest_params(
            watched=[
                _repository(
                    events=_events(
                        MAX_TOTAL_PATHS // MAX_PATHS_PER_EVENT + 1,
                        paths=["p"] * MAX_PATHS_PER_EVENT,
                    )
                )
            ]
        ),
        INVALID_REQUEST,
        MAX_TOTAL_PATHS,
    ),
    ("watched (all path characters together)", "characters"): lambda: (
        _digest_params(
            watched=[
                _repository(
                    events=[
                        _event(
                            paths=["p" * MAX_PATH_CHARS]
                            * (MAX_TOTAL_PATH_CHARS // MAX_PATH_CHARS + 1)
                        )
                    ]
                )
            ]
        ),
        INVALID_REQUEST,
        MAX_TOTAL_PATH_CHARS,
    ),
    ("policy.paths_of_interest", "entries"): lambda: (
        _digest_params(
            policy={"paths_of_interest": [f"src/{i}" for i in range(MAX_PATTERNS + 1)]}
        ),
        INVALID_REQUEST,
        MAX_PATTERNS,
    ),
    ("policy.paths_of_interest[]", "characters"): lambda: (
        _digest_params(policy={"paths_of_interest": ["p" * (MAX_PATTERN_CHARS + 1)]}),
        INVALID_REQUEST,
        MAX_PATTERN_CHARS,
    ),
    ("policy.paths_of_interest[]", "wildcards"): lambda: (
        _digest_params(policy={"paths_of_interest": ["a*b*c*d*e*"]}),
        INVALID_POLICY,
        MAX_PATTERN_WILDCARDS,
    ),
    # Within the other caps the pair bound cannot be exceeded (5,000 paths
    # times 20 patterns equals it), so the path cap is raised for this case
    # to show that the bound is checked as its own constant.
    ("path-pattern pairs (total paths times patterns)", "pairs"): lambda: (
        _digest_params(
            watched=[
                _repository(
                    events=_events(
                        (MAX_PATH_PATTERN_PAIRS // MAX_PATTERNS) // MAX_PATHS_PER_EVENT
                        + 1,
                        paths=["p"] * MAX_PATHS_PER_EVENT,
                    )
                )
            ],
            policy={"paths_of_interest": [f"src/{i}" for i in range(MAX_PATTERNS)]},
        ),
        INVALID_REQUEST,
        MAX_PATH_PATTERN_PAIRS,
        {"MAX_TOTAL_PATHS": MAX_PATH_PATTERN_PAIRS},
    ),
}


def test_every_cap_has_a_case():
    assert set(SIZE_CAP_CASES) == {(field, unit) for field, unit, _, _ in CAP_TABLE}
    assert len(CAP_TABLE) == len({(field, unit) for field, unit, _, _ in CAP_TABLE})
    for field, unit, limit, code in CAP_TABLE:
        assert isinstance(limit, int) and limit > 0
        assert code in ERROR_REGISTRY


@pytest.mark.parametrize(
    "field,unit",
    sorted(SIZE_CAP_CASES),
    ids=[f"{f}-{u}" for f, u in sorted(SIZE_CAP_CASES)],
)
def test_size_caps(skill, monkeypatch, field, unit):
    params, code, limit, *patches = SIZE_CAP_CASES[(field, unit)]()
    for name, value in (patches[0] if patches else {}).items():
        monkeypatch.setattr(skill_module, name, value)
    detail = _assert_error(skill.execute(params), code)
    assert detail["limit"] == limit
    assert detail["actual"] > limit
    assert detail["path"]


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
    assert _parse_check_list(text, "### assess") == list(ASSESS_CHECKS)
    assert _parse_check_list(text, "### digest") == list(DIGEST_CHECKS)


# --------------------------------------------------------------------------
# The issue's examples
# --------------------------------------------------------------------------


def test_issue_example_assess(skill, assess_params):
    result = skill.execute(assess_params)
    assert result == _load_json(FIXTURES_DIR, "expected_assess.json")
    assert result == {
        "status": "completed",
        "results": [
            {
                "name": "demo_http",
                "ecosystem": "pypi",
                "current": "2.3.1",
                "latest": "3.0.0",
                "latest_compatible": "2.3.4",
                "drift": {"major": 1, "minor": 0, "patch": 3},
                "verdict": "upgrade",
                "target": "2.3.4",
                "reasons": ["PATCH_AVAILABLE", "MIN_AGE_MET"],
                "signals": {
                    "breaking_terms": ["BREAKING"],
                    "yanked_skipped": 0,
                    "advisory": False,
                    "major_available": True,
                },
            },
            {
                "name": "demo_yaml",
                "ecosystem": "npm",
                "current": "1.0.0",
                "latest": None,
                "latest_compatible": None,
                "drift": None,
                "verdict": "unknown",
                "target": None,
                "reasons": ["NO_RELEASE_SNAPSHOT"],
                "signals": None,
            },
        ],
        "summary": {"hold": 0, "upgrade": 1, "test": 0, "unknown": 1},
        "fired_any": True,
        "insufficient": True,
        "stale_snapshot": False,
    }


def test_issue_example_digest(skill, digest_params):
    result = skill.execute(digest_params)
    assert result == _load_json(FIXTURES_DIR, "expected_digest.json")
    assert result == {
        "status": "completed",
        "window": {"since": "2026-09-05", "as_of": "2026-09-12"},
        "per_repo": [
            {
                "repo": "demo/kit",
                "counts": {"release": 2, "tag": 0, "commit": 1},
                "version_jumps": [
                    {"from": "1.4.0", "to": "1.5.0", "kind": "minor"},
                    {"from": "1.5.0", "to": "1.5.1", "kind": "patch"},
                ],
                "breaking_hits": [],
                "path_hits": [{"event_id": "c7", "pattern": "src/config*"}],
                "notable": ["r1", "c7"],
                "dropped_before_since": 1,
                "unparsed_versions": 0,
            }
        ],
        "fired_any": True,
        "insufficient": False,
    }


def test_issue_example_future_event(skill):
    params = _load_json(FIXTURES_DIR, "example_future_event_params.json")
    result = skill.execute(params)
    assert result == _load_json(FIXTURES_DIR, "expected_future_event.json")
    assert result == {
        "status": "error",
        "error": {
            "code": "FUTURE_EVENT",
            "detail": {"event_id": "c9", "at": "2026-09-13T01:00:00Z"},
        },
    }
    digest = _load_json(FIXTURES_DIR, "example_digest_params.json")
    assert params["watched"][0]["events"][:-1] == digest["watched"][0]["events"]


# --------------------------------------------------------------------------
# The eight rules: case files, order, reason codes, output shape
# --------------------------------------------------------------------------


def _named_cases(name):
    return [
        pytest.param(case, id=case["name"])
        for case in _load_json(FIXTURES_DIR, name)["cases"]
    ]


@pytest.mark.parametrize("case", _named_cases("rule_cases.json"))
def test_rule_cases(skill, case):
    assert skill.execute(copy.deepcopy(case["params"])) == case["expected"]


def test_rule_cases_cover_every_rule_and_the_competing_pairs():
    names = {
        case["name"] for case in _load_json(FIXTURES_DIR, "rule_cases.json")["cases"]
    }
    for required in (
        "rule_1_fix_in_range",
        "rule_1_no_fix_without_major",
        "rule_1_no_fix_with_major",
        "rule_1_over_2",
        "rule_1_over_4",
        "rule_2_up_to_date",
        "rule_3_age_met",
        "rule_3_age_not_met",
        "rule_3_with_major_outside",
        "rule_4_minor",
        "rule_4_with_major_outside",
        "rule_5_hold",
        "rule_5_test_allowed",
        "rule_6_after_3",
        "rule_6_after_3_strict",
        "rule_6_after_4",
        "rule_6_after_1",
        "rule_6_after_1_strict",
        "rule_6_after_5_allow_major",
        "rule_7_yanked_skipped",
        "rule_7_prerelease_skipped",
        "rule_7_prerelease_allowed",
        "rule_8_no_entry",
        "rule_8_all_yanked",
        "rule_8_with_advisory",
        "out_of_range_without_advisory",
        "out_of_range_with_advisory",
    ):
        assert required in names, required
    assert len(names) == len(_load_json(FIXTURES_DIR, "rule_cases.json")["cases"])


@pytest.mark.parametrize("case", _named_cases("age_cases.json"))
def test_age_cases(skill, case):
    assert skill.execute(copy.deepcopy(case["params"])) == case["expected"]


@pytest.mark.parametrize("case", _named_cases("window_cases.json"))
def test_window_cases(skill, case):
    assert skill.execute(copy.deepcopy(case["params"])) == case["expected"]


def test_reason_codes_are_closed():
    assert REASON_CODES == (
        "ADVISORY",
        "NO_FIX_IN_RANGE",
        "UP_TO_DATE",
        "PATCH_AVAILABLE",
        "MINOR_AVAILABLE",
        "MAJOR_AVAILABLE",
        "MAJOR_ALLOWED",
        "MIN_AGE_MET",
        "MIN_AGE_NOT_MET",
        "BREAKING_TERM",
        "YANKED_SKIPPED",
        "PRERELEASE_SKIPPED",
        "NO_RELEASE_SNAPSHOT",
        "CURRENT_OUT_OF_RANGE",
    )
    assert len(REASON_CODES) == 14
    assert VERDICTS == ("hold", "upgrade", "test", "unknown")
    seen = set()
    for name in ("rule_cases.json", "age_cases.json"):
        for case in _load_json(FIXTURES_DIR, name)["cases"]:
            for result in case["expected"]["results"]:
                assert result["verdict"] in VERDICTS
                assert all(reason in REASON_CODES for reason in result["reasons"])
                assert len(result["reasons"]) == len(set(result["reasons"]))
                seen.update(result["reasons"])
                if result["verdict"] in ("hold", "unknown"):
                    assert result["target"] is None
                else:
                    assert result["target"] is not None
    assert seen == set(REASON_CODES)
    section = _flat(_section(_instructions(), "## Reason codes"))
    for code in REASON_CODES:
        assert "`" + code + "`" in section


def test_rule_order_matches_instructions():
    assert [number for number, _, _ in RULES] == [1, 2, 3, 4, 5, 6, 7, 8]
    assert [kind for _, kind, _ in RULES] == [
        "decider",
        "decider",
        "decider",
        "decider",
        "decider",
        "modifier",
        "filter",
        "absence",
    ]
    assert EVALUATION_ORDER == (8, "current_out_of_range", 7, 1, 2, 3, 4, 5, 6)
    assert (
        EVALUATION_ORDER_LINE
        == "Evaluation order: 8, current_out_of_range, 7, 1, 2, 3, 4, 5, 6"
    )
    section = _section(_instructions(), "## Verdict rules (ordered)")
    documented = []
    for line in section.splitlines():
        match = re.match(r"^(\d+)\. \((decider|modifier|filter|absence)\) (.*)$", line)
        if match:
            documented.append((int(match.group(1)), match.group(2), match.group(3)))
    assert documented == list(RULES)
    assert EVALUATION_ORDER_LINE in section.splitlines()


def test_output_key_sets_are_exact(skill, assess_params, digest_params):
    assessed = skill.execute(assess_params)
    assert tuple(assessed) == ASSESS_OUTPUT_KEYS
    for result in assessed["results"]:
        assert tuple(result) == RESULT_KEYS
        if result["drift"] is not None:
            assert tuple(result["drift"]) == DRIFT_KEYS
        if result["signals"] is not None:
            assert tuple(result["signals"]) == SIGNAL_KEYS
    assert tuple(assessed["summary"]) == SUMMARY_KEYS
    digested = skill.execute(digest_params)
    assert tuple(digested) == DIGEST_OUTPUT_KEYS
    assert tuple(digested["window"]) == ("since", "as_of")
    for entry in digested["per_repo"]:
        assert tuple(entry) == PER_REPO_KEYS
        assert tuple(entry["counts"]) == ("release", "tag", "commit")
        for jump in entry["version_jumps"]:
            assert tuple(jump) == JUMP_KEYS
        for hit in entry["path_hits"]:
            assert tuple(hit) == ("event_id", "pattern")


def _schema(name):
    return _load_json(SCHEMAS_DIR, name)


def test_fixtures_match_the_documentation_schemas():
    checker = jsonschema.FormatChecker()
    requests = {
        "assess": _schema("assess_request.schema.json"),
        "digest": _schema("digest_request.schema.json"),
    }
    output = _schema("output.schema.json")
    checked = 0
    for params_name, expected_name in REFERENCE_FIXTURES:
        params = _load_json(FIXTURES_DIR, params_name)
        jsonschema.validate(params, requests[params["action"]], format_checker=checker)
        jsonschema.validate(
            _load_json(FIXTURES_DIR, expected_name), output, format_checker=checker
        )
        checked += 1
    for name in CASE_FIXTURES:
        for case in _load_json(FIXTURES_DIR, name)["cases"]:
            assert set(case) == {"name", "params", "expected"}
            jsonschema.validate(
                case["params"],
                requests[case["params"]["action"]],
                format_checker=checker,
            )
            jsonschema.validate(case["expected"], output, format_checker=checker)
            checked += 1
    assert checked > 60


def test_rule_8_no_snapshot_is_unknown(skill):
    result = _first_result(skill, _assess_params(releases=[]))
    assert result["verdict"] == "unknown"
    assert result["reasons"] == ["NO_RELEASE_SNAPSHOT"]
    assert (
        result["latest"] is None
        and result["target"] is None
        and result["signals"] is None
    )
    result = _first_result(
        skill, _assess_params(releases=[_entry(versions=[_version(yanked=True)])])
    )
    assert result["verdict"] == "unknown" and result["reasons"] == [
        "NO_RELEASE_SNAPSHOT"
    ]
    assert _result(skill, _assess_params(releases=[]))["insufficient"] is True


def test_current_out_of_range_is_unknown(skill):
    params = _assess_params(dependencies=[_dependency(version_range=">=2.4,<3")])
    result = _first_result(skill, params)
    assert result == {
        "name": "demo_http",
        "ecosystem": "pypi",
        "current": "2.3.1",
        "latest": "2.3.4",
        "latest_compatible": None,
        "drift": None,
        "verdict": "unknown",
        "target": None,
        "reasons": ["CURRENT_OUT_OF_RANGE"],
        "signals": None,
    }
    params["advisories"] = [_advisory("<2.3.2")]
    assert _first_result(skill, params)["reasons"] == [
        "CURRENT_OUT_OF_RANGE",
        "ADVISORY",
    ]
    flat = _flat(_instructions())
    assert (
        "`unknown` with `CURRENT_OUT_OF_RANGE` means the pin and the range disagree"
        in flat
    )


def test_unknown_ecosystem(skill):
    for ecosystem in ("gem", "PyPI", "NPM", "cargo", "", "pypi\n"):
        detail = _assert_error(
            skill.execute(
                _assess_params(dependencies=[_dependency(ecosystem=ecosystem)])
            ),
            UNKNOWN_ECOSYSTEM,
            "unknown_ecosystem",
        )
        assert detail["path"] == "dependencies[0].ecosystem"
        assert not ecosystem or ecosystem not in json.dumps(detail)
        detail = _assert_error(
            skill.execute(_assess_params(releases=[_entry(ecosystem=ecosystem)])),
            UNKNOWN_ECOSYSTEM,
        )
        assert detail["path"] == "releases[0].ecosystem"
        detail = _assert_error(
            skill.execute(_assess_params(advisories=[_advisory(ecosystem=ecosystem)])),
            UNKNOWN_ECOSYSTEM,
        )
        assert detail["path"] == "advisories[0].ecosystem"
    assert skill_module.ECOSYSTEMS == ("pypi", "npm")


def test_pypi_names_are_normalised_for_matching(skill):
    for spelling in (
        "demo_http",
        "Demo-HTTP",
        "demo.http",
        "demo__http",
        "DEMO_-.http",
    ):
        params = _assess_params(dependencies=[_dependency(name=spelling)])
        result = _first_result(skill, params)
        assert result["name"] == spelling
        assert result["verdict"] == "upgrade"
    _assert_error(
        skill.execute(
            _assess_params(dependencies=[_dependency(), _dependency(name="demo.http")])
        ),
        DUPLICATE_DEPENDENCY,
        "duplicate_dependency",
    )
    _assert_error(
        skill.execute(_assess_params(releases=[_entry(), _entry(name="DEMO-http")])),
        INVALID_REQUEST,
        "duplicate_release_entry",
    )
    # npm names are not normalised, and are case-sensitive by their pattern.
    npm = _assess_params(
        dependencies=[
            _dependency(
                name="demo-yaml",
                ecosystem="npm",
                current="1.0.0",
                version_range="^1.0.0",
            )
        ],
        releases=[
            _entry(name="demo_yaml", ecosystem="npm", versions=[_version("1.0.1")])
        ],
    )
    assert _first_result(skill, npm)["verdict"] == "unknown"
    npm["releases"][0]["name"] = "demo-yaml"
    assert _first_result(skill, npm)["verdict"] == "upgrade"
    for bad in ("Demo-yaml", "@/x", "@demo/", "demo/yaml", ".demo", "d" * 215):
        _assert_error(
            skill.execute(
                _assess_params(
                    dependencies=[
                        _dependency(
                            name=bad,
                            ecosystem="npm",
                            current="1.0.0",
                            version_range="^1.0.0",
                        )
                    ]
                )
            ),
            INVALID_REQUEST,
        )


def test_equal_versions_in_one_entry_count_once(skill):
    # pypi: 1.0 and 1.0.0 compare equal; the later one is yanked and ignored.
    pypi = _assess_params(
        dependencies=[_dependency(current="0.9.0", version_range=">=0.9,<2")],
        releases=[
            _entry(
                versions=[
                    _version("1.0", "2026-09-01T00:00:00Z", notes="BREAKING: new api"),
                    _version("1.0.0", "2026-09-10T00:00:00Z", yanked=True),
                ]
            )
        ],
        policy=_policy(allow_major=True),
    )
    result = _first_result(skill, pypi)
    assert result["latest"] == "1.0"
    assert result["verdict"] == "hold"
    assert result["reasons"] == ["MAJOR_AVAILABLE", "MAJOR_ALLOWED", "BREAKING_TERM"]
    assert result["signals"] == {
        "breaking_terms": ["BREAKING"],
        "yanked_skipped": 0,
        "advisory": False,
        "major_available": True,
    }
    reduced = copy.deepcopy(pypi)
    del reduced["releases"][0]["versions"][1]
    assert skill.execute(reduced) == skill.execute(pypi)
    # npm: 1.0.0+a and 1.0.0+b compare equal; the first carries the facts.
    npm = _assess_params(
        dependencies=[
            _dependency(
                name="demo-yaml",
                ecosystem="npm",
                current="1.0.0",
                version_range="^1.0.0",
            )
        ],
        releases=[
            _entry(
                name="demo-yaml",
                ecosystem="npm",
                versions=[
                    _version("1.0.0+a", "2026-09-01T00:00:00Z", notes="BREAKING"),
                    _version(
                        "1.0.0+b", "2026-09-10T00:00:00Z", yanked=True, notes="REMOVED"
                    ),
                ],
            )
        ],
    )
    result = _first_result(skill, npm)
    assert result["verdict"] == "hold" and result["reasons"] == ["UP_TO_DATE"]
    assert result["latest"] == "1.0.0+a" and result["latest_compatible"] == "1.0.0+a"
    assert result["signals"]["yanked_skipped"] == 0
    reduced = copy.deepcopy(npm)
    del reduced["releases"][0]["versions"][1]
    assert skill.execute(reduced) == skill.execute(npm)
    assert "count as one version" in _flat(_section(_instructions(), "## Inputs"))


# --------------------------------------------------------------------------
# Comparators and grammars
# --------------------------------------------------------------------------


def _ordering_groups(name):
    groups = _load_json(FIXTURES_DIR, name)["ascending"]
    assert len(groups) >= 20
    return groups


def _assert_ascending(ecosystem, groups):
    parsed = [
        [skill_module._parse_version(ecosystem, text) for text in group]
        for group in groups
    ]
    for group, versions in zip(groups, parsed):
        assert all(version is not None for version in versions), group
        assert len({version.key for version in versions}) == 1, group
    for lower, upper in zip(parsed, parsed[1:]):
        assert lower[0].key < upper[0].key, (lower[0].text, upper[0].text)
        assert not upper[0].key < lower[0].key


def test_semver_ordering_vectors():
    groups = _ordering_groups("version_order_semver.json")
    _assert_ascending("npm", groups)
    prerelease = skill_module._parse_version("npm", "1.0.0-alpha")
    assert (
        prerelease.prerelease
        and not skill_module._parse_version("npm", "1.0.0").prerelease
    )
    build = skill_module._parse_version("npm", "1.0.0+build.1")
    assert build.key == skill_module._parse_version("npm", "1.0.0").key
    assert (build.major, build.minor, build.patch) == (1, 0, 0)


def test_pep440_ordering_vectors():
    groups = _ordering_groups("version_order_pep440.json")
    _assert_ascending("pypi", groups)
    for text in ("1.0.dev1", "1.0a1", "1.0b2", "1.0rc1", "1.0a1.dev1"):
        assert skill_module._parse_version("pypi", text).prerelease, text
    for text in ("1.0", "1.0.post1", "1", "1.0.0.0"):
        assert not skill_module._parse_version("pypi", text).prerelease, text
    short = skill_module._parse_version("pypi", "2")
    assert (short.major, short.minor, short.patch) == (2, 0, 0)
    assert (
        skill_module._parse_version("pypi", "1.0").key
        == skill_module._parse_version("pypi", "1.0.0.0").key
    )


def _range_check(skill, ecosystem, version_range, version):
    """The verdict reasons of a dependency pinned at ``version`` with ``version_range``."""
    name = "demo_http" if ecosystem == "pypi" else "demo-yaml"
    params = _assess_params(
        dependencies=[
            _dependency(
                name=name,
                ecosystem=ecosystem,
                current=version,
                version_range=version_range,
            )
        ],
        releases=[
            _entry(
                name=name,
                ecosystem=ecosystem,
                versions=[_version(version, yanked=True)],
            )
        ],
        policy=_policy(allow_prerelease=True),
    )
    result = skill.execute(params)
    assert result["status"] == "completed", (ecosystem, version_range, version, result)
    return result["results"][0]["reasons"]


def test_range_vectors(skill):
    vectors = _load_json(FIXTURES_DIR, "range_vectors.json")
    checked = 0
    for ecosystem in ("pypi", "npm"):
        assert len(vectors[ecosystem]) >= 15
        for case in vectors[ecosystem]:
            parsed = skill_module._parse_range(ecosystem, case["range"])
            assert parsed is not None, case["range"]
            for version in case["satisfy"]:
                assert parsed.contains(
                    skill_module._parse_version(ecosystem, version)
                ), (case["range"], version)
                assert _range_check(skill, ecosystem, case["range"], version) == [
                    "NO_RELEASE_SNAPSHOT"
                ]
                checked += 1
            for version in case["reject"]:
                assert not parsed.contains(
                    skill_module._parse_version(ecosystem, version)
                ), (case["range"], version)
                assert _range_check(skill, ecosystem, case["range"], version) == [
                    "NO_RELEASE_SNAPSHOT"
                ] or (False)
                checked += 1
    assert checked > 100
    # The two- and three-segment compatible-release forms.
    tilde = skill_module._parse_range("pypi", "~=1.4")
    assert tilde.contains(skill_module._parse_version("pypi", "1.9"))
    assert not tilde.contains(skill_module._parse_version("pypi", "2.0"))
    tilde = skill_module._parse_range("pypi", "~=1.4.2")
    assert tilde.contains(skill_module._parse_version("pypi", "1.4.9"))
    assert not tilde.contains(skill_module._parse_version("pypi", "1.5.0"))


def test_range_vectors_reach_the_verdict_path(skill):
    """A rejected version is CURRENT_OUT_OF_RANGE through execute, a satisfying one is not."""
    vectors = _load_json(FIXTURES_DIR, "range_vectors.json")
    for ecosystem in ("pypi", "npm"):
        name = "demo_http" if ecosystem == "pypi" else "demo-yaml"
        for case in vectors[ecosystem]:
            for version, inside in [(v, True) for v in case["satisfy"]] + [
                (v, False) for v in case["reject"]
            ]:
                params = _assess_params(
                    dependencies=[
                        _dependency(
                            name=name,
                            ecosystem=ecosystem,
                            current=version,
                            version_range=case["range"],
                        )
                    ],
                    releases=[
                        _entry(
                            name=name, ecosystem=ecosystem, versions=[_version(version)]
                        )
                    ],
                    policy=_policy(allow_prerelease=True),
                )
                reasons = _first_result(skill, params)["reasons"]
                if inside:
                    assert "CURRENT_OUT_OF_RANGE" not in reasons, (
                        case["range"],
                        version,
                    )
                else:
                    assert reasons == ["CURRENT_OUT_OF_RANGE"], (case["range"], version)


def test_unsupported_forms_fail_closed(skill):
    forms = _load_json(FIXTURES_DIR, "unsupported_forms.json")
    for ecosystem in ("pypi", "npm"):
        name = "demo_http" if ecosystem == "pypi" else "demo-yaml"
        good_version = "2.3.1" if ecosystem == "pypi" else "1.0.0"
        good_range = ">=2.3,<3" if ecosystem == "pypi" else "^1.0.0"
        assert len(forms["versions"][ecosystem]) >= 20
        for version in forms["versions"][ecosystem]:
            assert skill_module._parse_version(ecosystem, version) is None, version
            detail = _assert_error(
                skill.execute(
                    _assess_params(
                        dependencies=[
                            _dependency(
                                name=name,
                                ecosystem=ecosystem,
                                current=version,
                                version_range=good_range,
                            )
                        ]
                    )
                ),
                INVALID_VERSION,
                "invalid_version",
            )
            assert detail == {
                "reason": "invalid_version",
                "path": "dependencies[0].current",
            }
            detail = _assert_error(
                skill.execute(
                    _assess_params(
                        dependencies=[
                            _dependency(
                                name=name,
                                ecosystem=ecosystem,
                                current=good_version,
                                version_range=good_range,
                            )
                        ],
                        releases=[
                            _entry(
                                name=name,
                                ecosystem=ecosystem,
                                versions=[_version(version)],
                            )
                        ],
                    )
                ),
                INVALID_VERSION,
                "invalid_version",
            )
            assert detail == {
                "reason": "invalid_version",
                "path": "releases[0].versions[0].version",
            }
        assert "~=1" in forms["ranges"]["pypi"]
        assert len(forms["ranges"][ecosystem]) >= 20
        for version_range in forms["ranges"][ecosystem]:
            assert (
                skill_module._parse_range(ecosystem, version_range) is None
            ), version_range
            detail = _assert_error(
                skill.execute(
                    _assess_params(
                        dependencies=[
                            _dependency(
                                name=name,
                                ecosystem=ecosystem,
                                current=good_version,
                                version_range=version_range,
                            )
                        ]
                    )
                ),
                INVALID_RANGE,
                "invalid_range",
            )
            assert detail == {
                "reason": "invalid_range",
                "path": "dependencies[0].range",
            }
            detail = _assert_error(
                skill.execute(
                    _assess_params(
                        dependencies=[
                            _dependency(
                                name=name,
                                ecosystem=ecosystem,
                                current=good_version,
                                version_range=good_range,
                            )
                        ],
                        advisories=[
                            _advisory(
                                affected=version_range, name=name, ecosystem=ecosystem
                            )
                        ],
                    )
                ),
                INVALID_RANGE,
                "invalid_range",
            )
            assert detail == {
                "reason": "invalid_range",
                "path": "advisories[0].affected",
            }
    assert len(forms["patterns"]["invalid"]) >= 10
    for pattern in forms["patterns"]["invalid"]:
        detail = _assert_error(
            skill.execute(
                _digest_params(policy={"paths_of_interest": ["src/*", pattern]})
            ),
            INVALID_POLICY,
        )
        assert detail["path"] == "policy.paths_of_interest[1]"
        assert pattern not in json.dumps(detail) or pattern in ("", "*")
    for pattern in forms["patterns"]["valid"]:
        assert (
            skill.execute(_digest_params(policy={"paths_of_interest": [pattern]}))[
                "status"
            ]
            == "completed"
        )


def test_range_whitespace(skill):
    accepted = {
        "pypi": [">=2.3, <3", ">= 2.3,<3", ">=2.3 ,<3", ">= 2.3 , < 3", ">=2.3,  <3"],
        "npm": [
            ">=1.2.3 <2.0.0",
            ">=1.2.3   <2.0.0",
            ">= 1.2.3",
            "^1.0.0 || ^2.0.0",
            "^1.0.0||^2.0.0",
            "^1.0.0  ||  ^2.0.0",
        ],
    }
    rejected = {
        "pypi": [
            ">=2.3,\t<3",
            ">=2.3,\n<3",
            " >=2.3,<3",
            ">=2.3,<3 ",
            ">=\n2.3",
            ">=2.3,<3\n",
            ">=2.3,< 3 \n",
        ],
        "npm": [
            ">=1.2.3\t<2.0.0",
            ">=1.2.3\n<2.0.0",
            " ^1.0.0",
            "^1.0.0 ",
            "^1.0.0\n",
            ">=1.2.3 <2.0.0",
            ">=1.2.3\r\n<2.0.0",
        ],
    }
    for ecosystem in ("pypi", "npm"):
        name = "demo_http" if ecosystem == "pypi" else "demo-yaml"
        current = "2.3.1" if ecosystem == "pypi" else "1.5.0"
        for version_range in accepted[ecosystem]:
            params = _assess_params(
                dependencies=[
                    _dependency(
                        name=name,
                        ecosystem=ecosystem,
                        current=current,
                        version_range=version_range,
                    )
                ],
                releases=[],
            )
            assert _first_result(skill, params)["reasons"] == [
                "NO_RELEASE_SNAPSHOT"
            ], version_range
        for version_range in rejected[ecosystem]:
            params = _assess_params(
                dependencies=[
                    _dependency(
                        name=name,
                        ecosystem=ecosystem,
                        current=current,
                        version_range=version_range,
                    )
                ]
            )
            _assert_error(skill.execute(params), INVALID_RANGE, "invalid_range")


def test_grammar_is_documented():
    section = _flat(_section(_instructions(), "## Version and range grammar"))
    for phrase in (
        "semver 2.0.0",
        "PEP 440 subset",
        "no epoch" if "no epoch" in section else "an epoch (`1!1.0`)",
        "`~=V` requires at least two release segments",
        "`~=1.4` is `>=1.4, ==1.*`",
        "`~=1.4.2` is `>=1.4.2, ==1.4.*`",
        "`<V` does not admit prereleases of `V`",
        "`>V` does not admit post-releases of `V`",
        "hyphen range",
        "`===`",
        "`||`",
        "caret and tilde with node-semver's zero-major behaviour",
        "x-ranges and partial versions",
        "compared by plain precedence",
        "`INVALID_VERSION`",
        "`INVALID_RANGE`",
        "the only whitespace it may hold is the ASCII space",
    ):
        assert phrase in section, phrase
    terms = _flat(_section(_instructions(), "## Breaking terms and path patterns"))
    assert "ASCII letters, digits, `_`, `.`, `-`, `/`, `*` and `?`" in terms
    assert "never translated to regular expressions" in terms
    assert "more than 4 wildcards" in terms


# --------------------------------------------------------------------------
# Rule 7 and ages
# --------------------------------------------------------------------------


def test_yanked_and_prerelease_are_skipped(skill):
    params = _assess_params(
        releases=[
            _entry(
                versions=[
                    _version("2.3.3"),
                    _version("2.3.4", yanked=True),
                    _version("2.4.0a1"),
                ]
            )
        ]
    )
    result = _first_result(skill, params)
    assert result["target"] == "2.3.3" and result["latest"] == "2.3.3"
    assert result["reasons"] == [
        "PATCH_AVAILABLE",
        "MIN_AGE_MET",
        "YANKED_SKIPPED",
        "PRERELEASE_SKIPPED",
    ]
    assert result["signals"]["yanked_skipped"] == 1
    npm = _assess_params(
        dependencies=[
            _dependency(
                name="demo-yaml",
                ecosystem="npm",
                current="1.0.0",
                version_range="^1.0.0",
            )
        ],
        releases=[
            _entry(
                name="demo-yaml",
                ecosystem="npm",
                versions=[
                    _version("1.0.1"),
                    _version("1.0.2", yanked=True),
                    _version("1.1.0-beta.1"),
                ],
            )
        ],
    )
    result = _first_result(skill, npm)
    assert result["target"] == "1.0.1"
    assert result["reasons"] == [
        "PATCH_AVAILABLE",
        "MIN_AGE_MET",
        "YANKED_SKIPPED",
        "PRERELEASE_SKIPPED",
    ]


def test_prerelease_allowed_by_policy(skill):
    params = _assess_params(
        releases=[_entry(versions=[_version("2.3.4"), _version("2.4.0a1")])],
        policy=_policy(allow_prerelease=True),
    )
    result = _first_result(skill, params)
    assert result["verdict"] == "test" and result["target"] == "2.4.0a1"
    assert result["reasons"] == ["MINOR_AVAILABLE"]
    assert result["latest"] == "2.4.0a1"
    # A yanked prerelease stays skipped whatever the policy says.
    params["releases"][0]["versions"][1]["yanked"] = True
    result = _first_result(skill, params)
    assert result["target"] == "2.3.4" and result["reasons"] == [
        "PATCH_AVAILABLE",
        "MIN_AGE_MET",
        "YANKED_SKIPPED",
    ]


def test_min_age_boundaries(skill):
    def reasons(published_at, min_age_days=3):
        params = _assess_params(
            releases=[_entry(versions=[_version(published_at=published_at)])],
            policy=_policy(min_age_days=min_age_days),
        )
        return _first_result(skill, params)["reasons"]

    assert reasons("2026-09-10T00:00:00Z") == ["PATCH_AVAILABLE", "MIN_AGE_NOT_MET"]
    assert reasons("2026-09-09T23:59:59Z") == ["PATCH_AVAILABLE", "MIN_AGE_MET"]
    assert reasons("2026-09-09T00:00:00Z") == ["PATCH_AVAILABLE", "MIN_AGE_MET"]
    assert reasons("2026-09-08T00:00:00Z") == ["PATCH_AVAILABLE", "MIN_AGE_MET"]
    assert reasons("2026-09-12T23:59:59Z", 0) == ["PATCH_AVAILABLE", "MIN_AGE_MET"]
    assert reasons("2026-09-12T23:59:59Z", 1) == ["PATCH_AVAILABLE", "MIN_AGE_NOT_MET"]
    assert reasons("2016-09-12T00:00:00Z", 3650) == ["PATCH_AVAILABLE", "MIN_AGE_MET"]
    # Rules 4 and 5 carry no age condition.
    fresh = "2026-09-12T23:59:59Z"
    minor = _first_result(
        skill, _assess_params(releases=[_entry(versions=[_version("2.4.0", fresh)])])
    )
    assert minor["verdict"] == "test" and minor["reasons"] == ["MINOR_AVAILABLE"]
    major = _first_result(
        skill,
        _assess_params(
            releases=[_entry(versions=[_version("3.0.0", fresh)])],
            policy=_policy(allow_major=True),
        ),
    )
    assert major["verdict"] == "test" and major["reasons"] == [
        "MAJOR_AVAILABLE",
        "MAJOR_ALLOWED",
    ]


def test_snapshot_age_boundaries(skill):
    def stale(snapshot_at, max_snapshot_age_days=7):
        params = _assess_params(
            snapshot_at=snapshot_at,
            policy=_policy(max_snapshot_age_days=max_snapshot_age_days),
        )
        return _result(skill, params)["stale_snapshot"]

    assert stale("2026-09-05T00:00:00Z") is False
    assert stale("2026-09-05T23:59:59Z") is False
    assert stale("2026-09-04T23:59:59Z") is True
    assert stale("2026-09-12T00:00:00Z", 0) is False
    assert stale("2026-09-11T23:59:59Z", 0) is True
    assert stale("2026-09-12T23:59:59Z") is False
    _assert_error(
        skill.execute(_assess_params(snapshot_at="2026-09-13T00:00:00Z")),
        INVALID_REQUEST,
        "snapshot_after_as_of",
    )
    # The verdicts are still computed on a stale snapshot.
    result = _result(skill, _assess_params(snapshot_at="2026-08-01T00:00:00Z"))
    assert result["stale_snapshot"] is True
    assert result["results"][0]["verdict"] == "upgrade"


# --------------------------------------------------------------------------
# digest: window, terms, paths
# --------------------------------------------------------------------------


def test_window_boundaries(skill):
    def digest(at):
        return skill.execute(
            _digest_params(
                watched=[
                    _repository(events=[_event("release", "r1", at, title="v1.5.0")])
                ]
            )
        )

    inside = digest("2026-09-05T00:00:00Z")["per_repo"][0]
    assert inside["counts"]["release"] == 1 and inside["dropped_before_since"] == 0
    dropped = digest("2026-09-04T23:59:59Z")["per_repo"][0]
    assert dropped["counts"]["release"] == 0 and dropped["dropped_before_since"] == 1
    last = digest("2026-09-12T23:59:59Z")["per_repo"][0]
    assert last["counts"]["release"] == 1 and last["dropped_before_since"] == 0
    future = digest("2026-09-13T00:00:00Z")
    assert future == {
        "status": "error",
        "error": {
            "code": "FUTURE_EVENT",
            "detail": {"event_id": "r1", "at": "2026-09-13T00:00:00Z"},
        },
    }
    _assert_error(
        skill.execute(_digest_params(since="2026-09-13")),
        INVALID_WINDOW,
        "since_after_as_of",
    )
    same_day = _result(
        skill,
        _digest_params(
            since="2026-09-12",
            watched=[_repository(events=[_event(at="2026-09-12T12:00:00Z")])],
        ),
    )
    assert same_day["per_repo"][0]["counts"]["commit"] == 1
    section = _flat(_section(_instructions(), "## Window rules"))
    assert (
        "`00:00:00Z` on `since` to `23:59:59Z` on `as_of`, both ends included"
        in section
    )


@pytest.mark.parametrize(
    "title,terms",
    [
        ("rename config keys", []),
        ("RENAMED config keys", ["RENAMED"]),
        ("Renamed_keys", ["RENAMED"]),
        ("breaking-change: new api", ["BREAKING"]),
        ("unbreaking change", []),
        ("removed2 things", []),
        ("Breaking and incompatible, breaking again", ["BREAKING", "INCOMPATIBLE"]),
        (
            "migration dropped removed renamed incompatible breaking",
            ["BREAKING", "INCOMPATIBLE", "REMOVED", "RENAMED", "DROPPED", "MIGRATION"],
        ),
        ("dropping support", []),
        ("", []),
        ("BREAKINGé", ["BREAKING"]),
        ("éBREAKING", ["BREAKING"]),
        ("x" * 500, []),
    ],
)
def test_breaking_terms_match_on_token_boundaries(skill, title, terms):
    terms_list = list(BREAKING_TERMS)
    assert skill_module._breaking_terms_in(title, terms_list) == terms
    result = _result(
        skill,
        _digest_params(watched=[_repository(events=[_event(title=title, paths=[])])]),
    )
    entry = result["per_repo"][0]
    assert entry["breaking_hits"] == [
        {"event_id": "c1", "term": term} for term in terms
    ]
    assert entry["notable"] == (["c1"] if terms else [])
    assert result["fired_any"] is bool(terms)
    # The same scan serves release notes.
    assess = _assess_params(releases=[_entry(versions=[_version(notes=title)])])
    signals = _first_result(skill, assess)["signals"]
    assert signals["breaking_terms"] == terms


@pytest.mark.parametrize(
    "pattern,path,matches",
    [
        ("src/config*", "src/config.py", True),
        ("src/config*", "src/configs/a.py", False),
        ("src/config*", "src/config", True),
        ("src/*.py", "src/a.py", True),
        ("src/*.py", "src/a/b.py", False),
        ("src/*.py", "src/.py", True),
        ("src/**", "src/a/b.py", True),
        ("src/**", "src/", True),
        ("src/**", "src", False),
        ("**/x.py", "a/b/x.py", True),
        ("**/x.py", "/x.py", True),
        ("**/x.py", "x.py", False),
        ("**", "", True),
        ("*", "", True),
        ("?", "", False),
        ("?", "/", False),
        ("?", "a", True),
        ("src/*", "src/a/", False),
        ("src/**/*.py", "src/a/b/c.py", True),
        ("src/**/*.py", "src/c.py", False),
        ("src/*/*.py", "src/a/c.py", True),
        ("src/*/*.py", "src/a/b/c.py", False),
        ("a*b*c", "abc", True),
        ("a*b*c", "aXbYcZ", False),
        ("a*b*c", "a/b/c", False),
        ("a**b**c", "a/b/c", True),
        ("*a?", "aXaab", True),
        ("*a?", "aXa", False),
        ("*a?", "aX", True),
        ("*a?b", "aXaab", True),
        ("config.py", "src/config.py", False),
        ("*config.py", "src/config.py", False),
        ("**config.py", "src/config.py", True),
        ("src/*.p?", "src/a.py", True),
        ("src/*.p?", "src/a.p", False),
    ],
)
def test_path_patterns_match_the_whole_path(skill, pattern, path, matches):
    elements = skill_module._compile_pattern(pattern)
    assert skill_module._match_pattern(elements, path) is matches
    result = _result(
        skill,
        _digest_params(
            watched=[_repository(events=[_event(paths=[path])])],
            policy={"paths_of_interest": [pattern]},
        ),
    )
    hits = result["per_repo"][0]["path_hits"]
    assert hits == ([{"event_id": "c1", "pattern": pattern}] if matches else [])
    assert path == pattern or json.dumps(path) not in json.dumps(result)


def test_path_hits_count_one_element_per_event_and_pattern(skill):
    params = _digest_params(
        watched=[
            _repository(
                events=[
                    _event(
                        ident="c2",
                        at="2026-09-09T08:00:00Z",
                        paths=["src/a.py", "src/b.py", "docs/x.md"],
                    ),
                    _event(ident="c1", at="2026-09-09T08:00:00Z", paths=["src/a.py"]),
                    _event(
                        "release",
                        "r1",
                        "2026-09-08T08:00:00Z",
                        title="v1.0.0",
                        paths=["src/a.py"],
                    ),
                    _event(ident="c0", at="2026-09-10T08:00:00Z", paths=[]),
                ]
            )
        ],
        policy={"paths_of_interest": ["docs/*", "src/*.py", "src/*.py", "lib/*"]},
    )
    entry = _result(skill, params)["per_repo"][0]
    assert entry["path_hits"] == [
        {"event_id": "c1", "pattern": "src/*.py"},
        {"event_id": "c2", "pattern": "docs/*"},
        {"event_id": "c2", "pattern": "src/*.py"},
    ]
    assert entry["notable"] == ["c1", "c2"]
    assert entry["unparsed_versions"] == 0


def test_version_jumps_follow_the_documented_rules(skill):
    events = [
        _event("tag", "t0", "2026-09-01T00:00:00Z", title="v1.4.0"),
        _event("release", "r0", "2026-09-02T00:00:00Z", title="1.4.0+rebuilt"),
        _event("release", "r1", "2026-09-06T00:00:00Z", title="v1.4.1"),
        _event("tag", "t1", "2026-09-06T00:00:00Z", title="1.4.1"),
        _event("release", "r2", "2026-09-07T00:00:00Z", title="v1.5.0"),
        _event("release", "r3", "2026-09-08T00:00:00Z", title="Release notes"),
        _event("tag", "t3", "2026-09-08T00:00:00Z"),
        _event("commit", "c1", "2026-09-09T00:00:00Z", title="v9.0.0"),
        _event("release", "r4", "2026-09-10T00:00:00Z", title="v2.0.0-rc.1"),
        _event("release", "r5", "2026-09-11T00:00:00Z", title="v2.0.0"),
        _event("release", "r6", "2026-09-12T00:00:00Z", title="v1.9.9"),
    ]
    entry = _result(skill, _digest_params(watched=[_repository(events=events)]))[
        "per_repo"
    ][0]
    assert entry["version_jumps"] == [
        {"from": "1.4.0", "to": "1.4.1", "kind": "patch"},
        {"from": "1.4.1", "to": "1.5.0", "kind": "minor"},
        {"from": "1.5.0", "to": "2.0.0-rc.1", "kind": "major"},
        {"from": "2.0.0-rc.1", "to": "2.0.0", "kind": "prerelease"},
        {"from": "2.0.0", "to": "1.9.9", "kind": "downgrade"},
    ]
    assert entry["unparsed_versions"] == 2
    assert entry["notable"] == ["r2", "r4", "r6"]
    assert entry["dropped_before_since"] == 2
    assert entry["counts"] == {"release": 6, "tag": 2, "commit": 1}
    result = _result(skill, _digest_params(watched=[_repository(events=events)]))
    assert result["insufficient"] is True and result["fired_any"] is True
    # Without a dropped version there is no baseline and no first jump.
    entry = _result(skill, _digest_params(watched=[_repository(events=events[2:3])]))[
        "per_repo"
    ][0]
    assert entry["version_jumps"] == [] and entry["notable"] == []
    assert "1.4.0" not in json.dumps(entry)


def test_free_text_is_never_echoed(skill):
    completed = [
        _assess_params(
            releases=[
                _entry(
                    versions=[
                        _version(notes=f"BREAKING {MARKER} removed"),
                        _version("3.0.0", notes=MARKER),
                    ]
                )
            ],
            advisories=[_advisory()],
        ),
        _digest_params(
            watched=[
                _repository(
                    events=[
                        _event("release", "r1", title=f"v1.5.0 {MARKER}"),
                        _event(
                            "commit",
                            "c1",
                            title=f"{MARKER} DROPPED",
                            paths=[f"{MARKER}/x.py", MARKER],
                        ),
                        _event("tag", "t1", title=MARKER),
                    ]
                )
            ],
            policy={"paths_of_interest": ["*/x.py", "**"]},
        ),
    ]
    for params in completed:
        result = skill.execute(params)
        assert result["status"] == "completed", result
        assert MARKER not in json.dumps(result)
    errors = [
        _assess_params(
            releases=[_entry(versions=[_version(notes="n" * MAX_NOTES_CHARS + MARKER)])]
        ),
        _assess_params(
            releases=[_entry(versions=[_version(notes=MARKER + LONE_SURROGATE)])]
        ),
        _assess_params(releases=[_entry(versions=[_version(MARKER, notes=MARKER)])]),
        _assess_params(dependencies=[_dependency(current=MARKER)]),
        _assess_params(dependencies=[_dependency(version_range=MARKER)]),
        _assess_params(dependencies=[_dependency(name=MARKER + "!")]),
        _assess_params(dependencies=[_dependency(ecosystem=MARKER)]),
        _assess_params(advisories=[_advisory(ident=MARKER + "!", affected=MARKER)]),
        _assess_params(**{MARKER: 1}),
        _assess_params(policy=_policy(**{MARKER: 1})),
        _digest_params(
            watched=[_repository(events=[_event(title="t" * MAX_TITLE_CHARS + MARKER)])]
        ),
        _digest_params(
            watched=[_repository(events=[_event(title=MARKER + LONE_SURROGATE)])]
        ),
        _digest_params(
            watched=[
                _repository(events=[_event(paths=["p" * MAX_PATH_CHARS + MARKER])])
            ]
        ),
        _digest_params(
            watched=[_repository(events=[_event(paths=[MARKER + LONE_SURROGATE])])]
        ),
        _digest_params(watched=[_repository(events=[_event(kind=MARKER)])]),
        _digest_params(watched=[_repository(events=[_event(ident=MARKER + "!")])]),
        _digest_params(watched=[_repository(events=[_event(at=MARKER)])]),
        _digest_params(watched=[_repository(repo=MARKER)]),
        _digest_params(policy={"paths_of_interest": [MARKER + " x"]}),
        _digest_params(policy={MARKER: 1}),
        _digest_params(watched=[{**_repository(), MARKER: 1}]),
        _digest_params(watched=[_repository(events=[{**_event(), MARKER: 1}])]),
    ]
    for params in errors:
        result = skill.execute(params)
        assert result["status"] == "error", params
        assert MARKER not in json.dumps(result, ensure_ascii=False)


# --------------------------------------------------------------------------
# Time bounds
# --------------------------------------------------------------------------


def _timed(label, bound, call):
    started = time.perf_counter()
    with _deadline(bound):
        result = call()
    elapsed = time.perf_counter() - started
    print(f"time-bound case {label}: {elapsed:.3f}s")
    assert elapsed < bound, (label, elapsed)
    return result


def _pattern_at_cap(index):
    """A 256-character pattern with four wildcards that matches a realistic path."""
    body = f"src/module{index:02d}/" + "component" * 40
    pattern = "**/" + body[: MAX_PATTERN_CHARS - 9] + "*/*.p?"
    assert len(pattern) == MAX_PATTERN_CHARS, len(pattern)
    return pattern


TIME_BOUND_CASES = {
    "adversarial-pypi-version": lambda: (
        _assess_params(dependencies=[_dependency(current="1" + ".1" * 31 + "a")]),
        "error",
    ),
    "adversarial-npm-version": lambda: (
        _assess_params(
            dependencies=[
                _dependency(
                    name="demo-yaml",
                    ecosystem="npm",
                    current="1.0.0-" + "a." * 29,
                    version_range="*",
                )
            ]
        ),
        "error",
    ),
    "longest-valid-pypi-version": lambda: (
        _assess_params(
            dependencies=[_dependency(current="1" + "9" * 47 + ".1rc1.post1.dev1")]
        ),
        "completed",
    ),
    "adversarial-pypi-range": lambda: (
        _assess_params(
            dependencies=[
                _dependency(version_range=",".join(">=" + "9" * 29 for _ in range(8)))
            ]
        ),
        "completed",
    ),
    "adversarial-npm-range": lambda: (
        _assess_params(
            dependencies=[
                _dependency(
                    name="demo-yaml",
                    ecosystem="npm",
                    current="1.0.0",
                    version_range=" || ".join("^1.0.0-" + "a" * 20 for _ in range(8)),
                )
            ]
        ),
        "completed",
    ),
    "unsupported-npm-range-of-maximum-length": lambda: (
        _assess_params(
            dependencies=[
                _dependency(
                    name="demo-yaml",
                    ecosystem="npm",
                    current="1.0.0",
                    version_range="-" * MAX_RANGE_CHARS,
                )
            ]
        ),
        "error",
    ),
    "adversarial-pattern-against-adversarial-path": lambda: (
        _digest_params(
            watched=[_repository(events=[_event(paths=["a" * MAX_PATH_CHARS] * 20)])],
            policy={
                "paths_of_interest": [
                    "*a*a*a*b",
                    "?a?a?a*b",
                    "**a**a**a**b",
                    "a" * 252 + "*a*b",
                ]
            },
        ),
        "completed",
    ),
    "all-caps-path-matcher": lambda: (
        _digest_params(
            watched=[
                _repository(
                    events=[
                        _event(
                            ident=f"c{i}",
                            paths=[
                                f"src/{j:03d}.py" for j in range(MAX_PATHS_PER_EVENT)
                            ],
                        )
                        for i in range(MAX_TOTAL_PATHS // MAX_PATHS_PER_EVENT)
                    ]
                )
            ],
            policy={
                "paths_of_interest": [_pattern_at_cap(i) for i in range(MAX_PATTERNS)]
            },
        ),
        "completed",
    ),
    "all-caps-token-scan": lambda: (
        _digest_params(
            watched=[
                _repository(
                    events=[
                        _event(
                            ident=f"c{i}",
                            title=("breaks nothing " * 40)[:MAX_TITLE_CHARS],
                        )
                        for i in range(MAX_TOTAL_TITLE_CHARS // MAX_TITLE_CHARS)
                    ]
                )
            ],
            policy={"paths_of_interest": []},
        ),
        "completed",
    ),
    "all-caps-notes-scan": lambda: (
        _assess_params(
            releases=[
                _entry(
                    versions=[
                        _version(
                            f"2.3.{i + 2}",
                            notes=("removal of nothing " * 2000)[:MAX_NOTES_CHARS],
                        )
                        for i in range(MAX_TOTAL_NOTES_CHARS // MAX_NOTES_CHARS)
                    ]
                )
            ]
        ),
        "completed",
    ),
    "all-caps-advisory-scan": lambda: (
        _assess_params(
            dependencies=[
                _dependency(name=f"demo_pkg_{d}", current="1.0.0", version_range=">=1")
                for d in range(25)
            ],
            releases=[
                _entry(
                    name=f"demo_pkg_{d}",
                    versions=[
                        _version(f"1.{v // 1000}.{v % 1000}")
                        for v in range(MAX_VERSIONS_PER_ENTRY)
                    ],
                )
                for d in range(25)
            ],
            advisories=[
                _advisory(
                    affected=">=1.0.0,<1.9.0,!=1.5.0,!=1.5.1,!=1.5.2,!=1.5.3,!=1.5.4,!=1.5.5",
                    ident=f"DEMO-{d}-{a}",
                    name=f"demo_pkg_{d}",
                )
                for d in range(25)
                for a in range(MAX_ADVISORIES_PER_DEPENDENCY)
            ],
        ),
        "completed",
    ),
}


@pytest.mark.parametrize("case", sorted(TIME_BOUND_CASES))
def test_parsers_stay_within_time_bound(skill, case):
    params, status = TIME_BOUND_CASES[case]()
    result = _timed(case, TIME_BOUND_SECONDS, lambda: skill.execute(params))
    assert result["status"] == status, result
    if status == "error":
        assert result["error"]["code"] != INTERNAL_ERROR


def test_path_matcher_at_the_per_item_caps_stays_within_time_bound():
    """The matcher itself at 5,000 paths of 1,024 characters against 20
    patterns of 256 characters with four wildcards each; this volume exceeds
    the request's total-path-characters cap, so it is measured directly."""
    paths = [
        ("src/" + f"dir{i % 7}/" + "segment/" * 130)[: MAX_PATH_CHARS - 9]
        + f"/x{i:04d}.py"
        for i in range(MAX_TOTAL_PATHS)
    ]
    assert all(len(path) == MAX_PATH_CHARS for path in paths)
    patterns = [
        skill_module._compile_pattern(_pattern_at_cap(i)) for i in range(MAX_PATTERNS)
    ]

    def run():
        return sum(
            1
            for elements in patterns
            for path in paths
            if skill_module._match_pattern(elements, path)
        )

    hits = _timed("path-matcher-per-item-caps", TIME_BOUND_SECONDS, run)
    assert hits == 0


# --------------------------------------------------------------------------
# instructions.md
# --------------------------------------------------------------------------


def test_instructions_have_the_required_sections():
    text = _instructions()
    assert text.startswith("# ")
    assert "`dev_tools/upgrade_gate`" in text
    for heading in (
        "## When not to use",
        "## What the host and the operator own",
        "## Agent-loop contract",
        "## Inputs",
        "## Verdict rules (ordered)",
        "## Reason codes",
        "## Version and range grammar",
        "## Window rules",
        "## Breaking terms and path patterns",
        "## No echo",
        "## Size caps",
        "## Validation order (fail-closed, deterministic)",
        "### assess",
        "### digest",
        "## Examples",
    ):
        assert "\n" + heading + "\n" in text, heading
    not_to_use = _flat(_section(text, "## When not to use"))
    for phrase in (
        "package manager",
        "vulnerability scanner",
        "fetcher",
        "never contacts a registry",
        "language model reading the changelog",
    ):
        assert phrase in not_to_use, phrase
    owners = _flat(_section(text, "## What the host and the operator own"))
    for statement in (
        "The host fetches manifests, release snapshots, advisories and repository events",
        "the skill never fetches",
        "The host decides whether to run `assess` after `digest`",
        "The operator owns the policy",
        "The operator owns every upgrade decision",
        "The verdicts are advice; they block nothing",
        "Treat notes and titles as untrusted data",
        "the manifest is to be corrected before the gate is re-run",
    ):
        assert statement in owners, statement
    flat = _flat(text)
    assert "treat notes and titles as untrusted data" in flat.lower()
    assert "never reach an output or an error detail" in flat
    assert "they block nothing" in flat
    assert "advice" in flat
    assert "`" + INTERNAL_ERROR + "`" in text
    for code in sorted(ERROR_REGISTRY):
        assert "`" + code + "`" in text
    for term in BREAKING_TERMS:
        assert "`" + term + "`" in text
    assert "`FUTURE_EVENT` carries exactly `{event_id, at}`" in flat
    assert not re.search(r"\b\d+(\.\d+)?\s?%", text)
    assert chr(0xA7) not in text and chr(0x2192) in text


def test_instructions_state_no_measured_number():
    text = _instructions()
    lowered = text.lower()
    for word in (
        "tokens per run",
        "reduction of",
        "benchmark",
        "faster than",
        "% fewer",
    ):
        assert word not in lowered, word
