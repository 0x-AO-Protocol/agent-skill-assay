"""Bundle tests for compliance/scoped_risk_literature_scan.

The tests build synthetic requests from the fixtures of this bundle and run
the skill in this process. They read only files inside the bundle and write
nothing.
"""

import ast
import json
import re
from copy import deepcopy
from pathlib import Path

import jsonschema
import pytest
import yaml

BUNDLE = Path(__file__).resolve().parent
FIXTURES = BUNDLE / "fixtures"
REGISTRY_ID = "compliance/scoped_risk_literature_scan"
SW, CW, DUP = "screen_week", "compile_week", "screen_duplicate_cap"
CASES = (SW, CW, DUP)
BUNDLE_FILES = frozenset(
    {
        "__init__.py",
        "card.json",
        "instructions.md",
        "manifest.yaml",
        "skill.py",
        "test_skill.py",
        "schemas/request.schema.json",
        "schemas/output.schema.json",
        "fixtures/card_ui_samples.json",
    }
    | {f"fixtures/{name}_params.json" for name in CASES}
    | {f"fixtures/expected_{name}.json" for name in CASES}
)

ISSUER = {
    "name": "Masa",
    "email": "masa88keith@gmail.com",
    "github": "mrmasa88",
    "org": "AO",
}
ECONOMIC_EFFECT = {
    "claim": (
        "Performs the task in deterministic code instead of LLM generation, "
        "reducing LLM token consumption."
    ),
    "baseline_task": (
        "The same task performed by an LLM agent without this skill (prompt-only)."
    ),
    "metric": "LLM tokens (input + output) per task run.",
    "method": (
        "Run the skill's reference fixtures through (a) the baseline agent and "
        "(b) the same agent calling the skill; publish the median tokens per run "
        "for both and the reduction."
    ),
}
SERVED_ACTIVITIES = {"onet_release": "31.0", "ids": ["4.A.2.a.4.b.2", "4.A.2.b.3.a.21"]}
PARAMETER_TYPES = {
    "action": "string",
    "cycle_start": "string",
    "cycle_end": "string",
    "risk_domains": "array",
    "jurisdictions": "array",
    "sources": "array",
    "max_items": "integer",
    "client_exposure_tags": "array",
    "previous_tallies": "object",
    "collection": "array",
    "log_destination": "string",
    "attestation": "object",
    "annotations": "array",
}
IR, DK = "INVALID_REQUEST", "DUPLICATE_KEY"
ERROR_REASONS = {
    IR: (
        "parameters_not_object",
        "unknown_action",
        "unknown_key",
        "missing_field",
        "invalid_type",
        "invalid_value",
        "out_of_range",
        "size_cap",
        "field_not_allowed",
        "unknown_domain",
    ),
    "INVALID_DATE": ("invalid_date",),
    "EMPTY_SCOPE": ("empty_scope",),
    "WINDOW_INVALID": ("end_before_start", "window_too_long"),
    DK: ("duplicate_key",),
    "UNKNOWN_SOURCE": ("unknown_source",),
    "SOURCE_NOT_REPORTED": ("source_not_reported",),
    "SOURCE_UNREACHABLE": ("source_unreachable",),
    "UNKNOWN_ITEM_REF": ("unknown_item_ref",),
    "ANNOTATION_MISSING": ("annotation_missing",),
}
FAULT_REASONS = {"INTERNAL_ERROR": ("internal_error",)}


def read_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def read_manifest():
    with open(BUNDLE / "manifest.yaml", "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


PARAMS = {name: read_json(FIXTURES / f"{name}_params.json") for name in CASES}
EXPECTED = {name: read_json(FIXTURES / f"expected_{name}.json") for name in CASES}


@pytest.fixture(scope="module")
def module():
    from skills.compliance.scoped_risk_literature_scan import skill

    return skill


@pytest.fixture(scope="module")
def run(module):
    return module.ScopedRiskLiteratureScanSkill().execute


# --------------------------------------------------------------------------
# Request builders
# --------------------------------------------------------------------------

DROP = object()


def make(base, changes=None):
    """A copy of a request with each change applied.

    ``base`` names a fixture request or is a function that returns a request.
    A change maps a dotted path (list positions as numbers) to a value; DROP
    removes the entry, and a path ending in ".+" appends the value to a list.
    """
    params = base() if callable(base) else deepcopy(PARAMS[base])
    for dotted, value in (changes or {}).items():
        keys = [int(part) if part.isdigit() else part for part in dotted.split(".")]
        target = params
        for key in keys[:-1]:
            target = target[key]
        if keys[-1] == "+":
            target.append(deepcopy(value))
        elif value is DROP:
            del target[keys[-1]]
        else:
            target[keys[-1]] = deepcopy(value)
    return params


def item(title, day, jurisdiction="EU", domains=("credit_risk",), tags=None):
    entry = {
        "title": title,
        "published_date": day,
        "jurisdiction": jurisdiction,
        "domains": list(domains),
    }
    if tags is not None:
        entry["exposure_tags"] = list(tags)
    return entry


def collected(first=(), second=()):
    """A screen request over the weekly window and scope with these items.

    The first report comes from the regulatory source and the second from the
    literature source; the client exposure tags are shipping and real_estate.
    """
    return make(
        SW, {"collection.0.items": list(first), "collection.1.items": list(second)}
    )


def many_items(count, prefix="Item"):
    return [item(f"{prefix} {number}", "2027-04-07") for number in range(count)]


def spread(*sizes):
    """A screen request with one economic source per size, each reporting that many."""
    names = [f"source_{k}" for k in range(len(sizes))]
    sources = [
        {"name": name, "url": "https://example.org/s", "kind": "economic"}
        for name in names
    ]
    reports = [
        {"source_name": name, "status": "ok", "items": many_items(size, name)}
        for name, size in zip(names, sizes)
    ]
    return make(SW, {"sources": sources, "collection": reports})


def annotation(ref, summary="A short summary.", note="A short note."):
    impact = {"area": "none", "likelihood": "low", "note": note}
    return {"item_ref": ref, "summary": summary, "impact": impact}


def annotated(count, extra=0):
    """A compile request whose ``count`` kept items carry one annotation each."""
    refs = ["R%03d" % number for number in range(1, count + extra + 1)]
    changes = {"action": "compile", "max_items": 200}
    changes["annotations"] = [annotation(ref) for ref in refs]
    return make(lambda: spread(count), changes)


def domain_list(count):
    return ["credit_risk", "climate_risk"] + [
        f"domain_{n:02d}" for n in range(count - 2)
    ]


def item_domains(count):
    return ["climate_risk"] + [f"domain_{n:02d}" for n in range(count - 1)]


def jurisdiction_list(count):
    return ["EU", "UK"] + [f"J{n:02d}" for n in range(count - 2)]


def tag_list(count):
    return [f"tag_{n:02d}" for n in range(count)]


def with_sources(count):
    params = make(SW)
    for number in range(count - 2):
        name = f"extra_source_{number:02d}"
        source = {"name": name, "url": "https://example.org/x", "kind": "technology"}
        params["sources"].append(source)
        params["collection"].append({"source_name": name, "status": "ok", "items": []})
    return params


def error_of(output):
    assert output["status"] == "error", output
    assert set(output) == {"status", "error"}
    assert set(output["error"]) == {"code", "detail"}
    return output["error"]["code"], output["error"]["detail"]


def titles(output):
    return [entry["title"] for entry in output["items"]]


def canonical(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


ANNOTATIONS = PARAMS[CW]["annotations"]
BLOG_ROLL = {"source_name": "blog_roll", "status": "ok", "items": []}
OUTSIDE = {"by_domain": {"market_risk": 1}}
UNREACHABLE = {"collection.1.status": "unreachable"}
ATTESTED = {"attested": True, "minutes": 5}


# --------------------------------------------------------------------------
# Bundle, manifest and card
# --------------------------------------------------------------------------


def test_bundle_file_set_is_exact():
    found = {
        path.relative_to(BUNDLE).as_posix()
        for path in BUNDLE.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }
    assert found == BUNDLE_FILES


def test_manifest_carries_the_standard_profile_fields():
    manifest = read_manifest()
    assert manifest["name"] == REGISTRY_ID
    assert manifest["version"] == "0.1.0"
    assert manifest["category"] == "compliance"
    assert manifest["trust_tier"] == "L0"
    assert manifest["issuer"] == ISSUER
    assert manifest["economic_effect"] == ECONOMIC_EFFECT
    assert manifest["norm_version"] == "norms-1.0.0"
    provenance = manifest["provenance"]
    assert list(provenance) == ["anchor", "content_sha256", "authored_on"]
    assert provenance["anchor"] == "repository"
    assert re.fullmatch("[0-9a-f]{64}", provenance["content_sha256"], re.ASCII)
    day = provenance["authored_on"]
    assert re.fullmatch("[0-9]{4}-[0-9]{2}-[0-9]{2}", day, re.ASCII)
    assert "env_vars" not in manifest
    assert "requirements" not in manifest
    assert "output" not in manifest
    assert set(manifest["outputs"]) >= {"status", "items", "review_status"}
    first = re.match("(.+?[.!?])(?= |$)", manifest["short_description"], re.ASCII)
    assert sum(first.group(1).count(mark) for mark in ".!?") == 1


def test_manifest_origin_is_ao_standard():
    assert read_manifest()["origin"] == "ao_standard"


def test_manifest_lists_the_served_activities():
    assert read_manifest()["serves_dwa"] == SERVED_ACTIVITIES


def test_manifest_constitution_has_two_to_five_numbered_lines():
    lines = [line for line in read_manifest()["constitution"].split("\n") if line]
    assert 2 <= len(lines) <= 5
    for number, line in enumerate(lines, 1):
        assert line.startswith(f"{number}. ") and len(line) > 10


def test_manifest_parameters_are_flat():
    parameters = read_manifest()["parameters"]
    assert parameters["type"] == "object"
    assert parameters["required"] == ["action"]
    assert parameters["additionalProperties"] is False
    properties = parameters["properties"]
    assert list(properties) == list(PARAMETER_TYPES)
    for name, schema in properties.items():
        keys = {"type", "description"} | ({"enum"} if name == "action" else set())
        assert set(schema) == keys, name
        assert schema["type"] == PARAMETER_TYPES[name]
        assert isinstance(schema["description"], str) and schema["description"]
    assert properties["action"]["enum"] == ["screen", "compile"]


def test_manifest_matches_the_skill_constant(module):
    manifest = read_manifest()
    assert module.MANIFEST == {
        "name": manifest["name"],
        "version": manifest["version"],
        "parameters": manifest["parameters"],
    }


@pytest.mark.parametrize("name", CASES)
def test_every_params_fixture_passes_parameter_validation(module, name):
    skill = module.ScopedRiskLiteratureScanSkill()
    assert skill.validate_params(make(name)) is True


def test_card_issuer_and_fields_match():
    card = read_json(BUNDLE / "card.json")
    manifest = read_manifest()
    assert card["issuer"] == manifest["issuer"]
    assert card["name"] == "Scoped Risk Literature Scan"
    assert card["description"] == manifest["short_description"]
    sample = read_json(FIXTURES / "card_ui_samples.json")
    assert card["ui_schema"]["type"] == "card"
    for field in card["ui_schema"]["fields"]:
        assert field["key"] in sample and field["label"], field


def test_card_sample_equals_the_output_of_its_params_fixture(run):
    sample = read_json(FIXTURES / "card_ui_samples.json")
    assert canonical(sample) == canonical(run(make(SW)))


def test_actions_are_exactly_the_published_set(module):
    assert module.ACTIONS == ("screen", "compile")
    enum = read_manifest()["parameters"]["properties"]["action"]["enum"]
    assert enum == list(module.ACTIONS)


def test_error_registry_is_closed_and_final(module):
    assert module.ERROR_REGISTRY == ERROR_REASONS
    assert module.FAULT_REGISTRY == FAULT_REASONS


# --------------------------------------------------------------------------
# The worked examples
# --------------------------------------------------------------------------


def test_screen_keeps_ordered_in_scope_items(run):
    output = run(make(SW))
    assert canonical(output) == canonical(EXPECTED[SW])
    assert [entry["item_ref"] for entry in output["items"]] == ["R001", "R002"]
    assert output["items"][0]["client_relevant"] is True


def test_compile_returns_the_review_record(run):
    output = run(make(CW))
    assert canonical(output) == canonical(EXPECTED[CW])
    assert output["review_status"] == "pending_review"
    assert output["log_status"] == "returned_to_caller"


def test_screen_drops_duplicates_and_applies_the_cap(run):
    output = run(make(DUP))
    assert canonical(output) == canonical(EXPECTED[DUP])
    assert output["excluded_by_reason"]["duplicate"] == 1
    assert output["excluded_by_reason"]["over_cap"] == 1


@pytest.mark.parametrize("name", CASES)
def test_reference_fixtures_match_expected_outputs(run, name):
    params = make(name)
    before = canonical(params)
    for _ in range(2):
        assert canonical(run(params)) == canonical(EXPECTED[name])
    assert canonical(params) == before


def test_end_before_start_is_rejected(run):
    detail = {"reason": "end_before_start", "path": "cycle_end"}
    assert error_of(run(make(SW, {"cycle_end": "2027-04-01"}))) == (
        "WINDOW_INVALID",
        detail,
    )


def test_unreachable_source_is_rejected(run):
    detail = {"reason": "source_unreachable", "path": "collection[1].status"}
    assert error_of(run(make(SW, UNREACHABLE))) == ("SOURCE_UNREACHABLE", detail)


def test_kept_item_without_annotation_is_rejected(run):
    detail = {"reason": "annotation_missing", "path": "annotations", "item_ref": "R002"}
    output = run(make(CW, {"annotations.1": DROP}))
    assert error_of(output) == ("ANNOTATION_MISSING", detail)


def test_negative_minutes_are_rejected(run):
    detail = {"reason": "out_of_range", "path": "attestation.minutes"}
    output = run(make(CW, {"attestation.minutes": -5}))
    assert error_of(output) == (IR, detail)


def test_unconfigured_source_report_is_rejected(run):
    detail = {"reason": "unknown_source", "path": "collection[2].source_name"}
    output = run(make(SW, {"collection.+": BLOG_ROLL}))
    assert error_of(output) == ("UNKNOWN_SOURCE", detail)


def test_empty_scope_is_rejected(run):
    detail = {"reason": "empty_scope", "path": "risk_domains"}
    assert error_of(run(make(SW, {"risk_domains": []}))) == ("EMPTY_SCOPE", detail)


def test_configured_source_without_report_is_rejected(run):
    detail = {"reason": "source_not_reported", "path": "sources[1]"}
    output = run(make(SW, {"collection.1": DROP}))
    assert error_of(output) == ("SOURCE_NOT_REPORTED", detail)


# --------------------------------------------------------------------------
# Screening, ordering, de-duplication and the cap
# --------------------------------------------------------------------------

# fmt: off
EXCLUSION_CASES = [
    pytest.param("2027-04-04", "EU", "credit_risk", "out_of_window", id="window"),
    pytest.param("2027-04-07", "US", "credit_risk", "out_of_jurisdiction", id="jurisdiction"),
    pytest.param("2027-04-07", "EU", "market_risk", "out_of_domain", id="domain"),
    pytest.param("2027-04-12", "US", "credit_risk", "out_of_window", id="window-before-jurisdiction"),
    pytest.param("2027-04-07", "US", "market_risk", "out_of_jurisdiction", id="jurisdiction-before-domain"),
    pytest.param("2027-03-01", "US", "market_risk", "out_of_window", id="all-three"),
]
# fmt: on


@pytest.mark.parametrize(("day", "place", "domain", "reason"), EXCLUSION_CASES)
def test_each_exclusion_reason_and_its_precedence(run, day, place, domain, reason):
    output = run(collected([item("Only item", day, place, [domain])]))
    reasons = ["out_of_window", "out_of_jurisdiction", "out_of_domain"]
    expected = dict.fromkeys(reasons + ["duplicate", "over_cap"], 0)
    expected[reason] = 1
    assert output["excluded_by_reason"] == expected
    assert output["excluded_count"] == 1
    assert output["items"] == []
    assert output["tallies"]["in_scope_count"] == 0


@pytest.mark.parametrize(
    ("day", "kept"),
    [
        pytest.param("2027-04-05", True, id="first-day"),
        pytest.param("2027-04-11", True, id="last-day"),
        pytest.param("2027-04-04", False, id="day-before"),
        pytest.param("2027-04-12", False, id="day-after"),
    ],
)
def test_window_ends_are_included(run, day, kept):
    output = run(collected([item("Edge item", day)]))
    assert titles(output) == (["Edge item"] if kept else [])
    assert output["excluded_by_reason"]["out_of_window"] == (0 if kept else 1)


def test_duplicates_across_sources(run):
    shared = [item("Shared title", "2027-04-07")]
    params = collected(shared, shared)
    for _ in ("as given", "reports reversed"):
        output = run(deepcopy(params))
        assert titles(output) == ["Shared title"]
        assert output["items"][0]["source_name"] == "supervisor_feed"
        assert output["excluded_by_reason"]["duplicate"] == 1
        assert output["tallies"]["in_scope_count"] == 1
        params["collection"].reverse()


@pytest.mark.parametrize(
    ("max_items", "kept", "over_cap"),
    [
        pytest.param(3, 3, 0, id="equal-to-the-count"),
        pytest.param(2, 2, 1, id="one-below"),
        pytest.param(4, 3, 0, id="one-above"),
    ],
)
def test_cap_boundary(run, max_items, kept, over_cap):
    output = run(make(lambda: collected(many_items(3)), {"max_items": max_items}))
    assert len(output["items"]) == kept
    assert output["excluded_by_reason"]["over_cap"] == over_cap
    assert output["tallies"]["in_scope_count"] == 3
    refs = [slot["item_ref"] for slot in output["annotation_slots"]]
    assert refs == ["R%03d" % number for number in range(1, kept + 1)]


def _reversed_reports(first, second):
    params = collected(first, second)
    params["collection"].reverse()
    return params


# fmt: off
ORDERING_CASES = [
    pytest.param(
        lambda: collected([item("Newer plain", "2027-04-10"), item("Older relevant", "2027-04-05", tags=["shipping"])]),
        [("Older relevant", "EU"), ("Newer plain", "EU")], id="client-relevant-first"),
    pytest.param(
        lambda: collected([item("Older", "2027-04-05"), item("Newer", "2027-04-09")]),
        [("Newer", "EU"), ("Older", "EU")], id="newest-first"),
    pytest.param(
        lambda: _reversed_reports([item("Regulator note", "2027-04-07")], [item("Journal note", "2027-04-07")]),
        [("Regulator note", "EU"), ("Journal note", "EU")], id="source-position"),
    pytest.param(
        lambda: collected([item("Beta note", "2027-04-07"), item("alpha note", "2027-04-07")]),
        [("alpha note", "EU"), ("Beta note", "EU")], id="normalized-title"),
    pytest.param(
        lambda: collected([item("Same title", "2027-04-07", "UK"), item("Same title", "2027-04-07")]),
        [("Same title", "UK"), ("Same title", "EU")], id="collection-position"),
]
# fmt: on


@pytest.mark.parametrize(("build", "expected"), ORDERING_CASES)
def test_ordering_tie_breaks(run, build, expected):
    output = run(build())
    found = [(entry["title"], entry["jurisdiction"]) for entry in output["items"]]
    assert found == expected


@pytest.mark.parametrize(
    ("first", "second"),
    [
        pytest.param("Étude des taux", "étude des taux", id="accented-case"),
        pytest.param("Rate path outlook", "Rate path outlook", id="no-break-space"),
        pytest.param("Rate　path outlook", "Rate path outlook", id="wide-space"),
    ],
)
def test_titles_differing_outside_ascii_are_not_duplicates(run, first, second):
    output = run(collected([item(first, "2027-04-07"), item(second, "2027-04-07")]))
    assert output["excluded_by_reason"]["duplicate"] == 0
    assert sorted(titles(output)) == sorted([first, second])


def test_title_normalization_folds_ascii_and_collapses_spaces(run):
    items = [
        item("Rate  path OUTLOOK", "2027-04-07"),
        item("Rate path outlook", "2027-04-07"),
        item("Rate path outlook", "2027-04-07", "UK"),
        item("Rate path outlook", "2027-04-08"),
    ]
    output = run(collected(items))
    assert output["excluded_by_reason"]["duplicate"] == 1
    found = [
        (e["title"], e["published_date"], e["jurisdiction"]) for e in output["items"]
    ]
    assert found == [
        ("Rate path outlook", "2027-04-08", "EU"),
        ("Rate  path OUTLOOK", "2027-04-07", "EU"),
        ("Rate path outlook", "2027-04-07", "UK"),
    ]


def test_item_refs_agree_between_screen_and_compile(run):
    screened, compiled = run(make(SW)), run(make(CW))
    pairs = [
        [(e["item_ref"], e["title"]) for e in out["items"]]
        for out in (screened, compiled)
    ]
    assert pairs[0] == pairs[1]
    slots = [slot["item_ref"] for slot in screened["annotation_slots"]]
    assert slots == [ref for ref, _ in pairs[0]]


def test_equal_ordering_keys_keep_collection_order(run):
    for first, second in (("EU", "UK"), ("UK", "EU")):
        twins = [item("Twin", "2027-04-07", first), item("Twin", "2027-04-07", second)]
        output = run(collected(twins))
        found = [(e["item_ref"], e["jurisdiction"]) for e in output["items"]]
        assert found == [("R001", first), ("R002", second)]


# --------------------------------------------------------------------------
# Minutes, tallies, window, follow-up dates and the log record
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("attestation", "minutes"),
    [
        pytest.param({"attested": True, "minutes": 50}, 50, id="attested"),
        pytest.param({"attested": False, "minutes": 50}, 0, id="not-attested"),
        pytest.param(DROP, 0, id="absent"),
        pytest.param({"attested": True, "minutes": 0}, 0, id="zero"),
        pytest.param({"attested": True, "minutes": 1440}, 1440, id="full-day"),
    ],
)
def test_competence_minutes(run, attestation, minutes):
    output = run(make(CW, {"attestation": attestation}))
    assert output["competence_minutes_logged"] == minutes
    assert output["review_status"] == "pending_review"


def _tally_request(previous):
    alpha = item("Alpha", "2027-04-07", "EU", ["credit_risk", "climate_risk"])
    beta = item("Beta", "2027-04-08", "UK")
    gamma = item("Gamma", "2027-04-06", "EU", ["market_risk", "liquidity_risk"])
    changes = {"risk_domains": ["credit_risk", "climate_risk", "market_risk"]}
    changes.update({"jurisdictions": ["EU", "UK", "DE"], "max_items": 1})
    if previous is not None:
        changes["previous_tallies"] = previous
    return make(lambda: collected([alpha], [beta, beta, gamma]), changes)


@pytest.mark.parametrize(
    ("previous", "change"),
    [
        pytest.param(None, None, id="no-previous-tallies"),
        pytest.param(
            {"by_domain": {}},
            {"credit_risk": 2, "climate_risk": 1, "market_risk": 1},
            id="empty-previous",
        ),
        pytest.param(
            {"by_domain": {"credit_risk": 3}},
            {"credit_risk": -1, "climate_risk": 1, "market_risk": 1},
            id="partial-previous",
        ),
        pytest.param(
            {"by_domain": {"market_risk": 1, "credit_risk": 2, "climate_risk": 0}},
            {"credit_risk": 0, "climate_risk": 1, "market_risk": 0},
            id="full-previous-in-another-order",
        ),
    ],
)
def test_tallies_and_change_by_domain(run, previous, change):
    output = run(_tally_request(previous))
    kinds = {"literature": 2, "regulatory": 1, "technology": 0, "economic": 0}
    expected = {
        "in_scope_count": 3,
        "by_domain": {"credit_risk": 2, "climate_risk": 1, "market_risk": 1},
        "by_jurisdiction": {"EU": 2, "UK": 1, "DE": 0},
        "by_kind": kinds,
        "change_by_domain": change,
    }
    assert canonical(output["tallies"]) == canonical(expected)
    assert output["excluded_by_reason"]["duplicate"] == 1
    assert output["excluded_by_reason"]["over_cap"] == 2
    assert output["items"][0]["matched_domains"] == ["credit_risk"]


@pytest.mark.parametrize(
    ("start", "end", "days"),
    [
        pytest.param("2027-04-07", "2027-04-07", 1, id="one-day"),
        pytest.param("2027-04-01", "2027-07-01", 92, id="ninety-two-days"),
        pytest.param("2027-04-01", "2027-07-02", None, id="ninety-three-days"),
    ],
)
def test_window_length_bounds(run, start, end, days):
    output = run(make(SW, {"cycle_start": start, "cycle_end": end}))
    if days is None:
        detail = {"reason": "window_too_long", "path": "cycle_end", "limit": 92}
        assert error_of(output) == ("WINDOW_INVALID", detail)
    else:
        assert output["window_days"] == days


def test_follow_up_after_the_last_calendar_day_is_a_fault(run):
    """A known limit of this version: no date after 9999-12-31 can be computed."""
    relevant = collected([item("Relevant", "9999-12-30", tags=["real_estate"])])
    changes = {"cycle_start": "9999-12-25", "cycle_end": "9999-12-31"}
    detail = {"reason": "internal_error", "exception": "OverflowError"}
    output = run(make(lambda: relevant, changes))
    assert output == {
        "status": "error",
        "error": {"code": "INTERNAL_ERROR", "detail": detail},
    }


def test_follow_up_due_dates(run):
    output = run(make(SW))
    assert [e["follow_up_due_by"] for e in output["items"]] == ["2027-04-18", None]
    for start, end, day, due in (
        ("2027-12-25", "2027-12-31", "2027-12-30", "2028-01-07"),
        ("2028-02-23", "2028-02-29", "2028-02-29", "2028-03-07"),
    ):
        relevant = collected([item("Relevant", day, tags=["real_estate"])])
        entry = run(make(lambda: relevant, {"cycle_start": start, "cycle_end": end}))
        assert entry["items"][0]["follow_up_required"] is True
        assert entry["items"][0]["follow_up_due_by"] == due


def test_log_destination_is_echoed_in_the_record(run):
    output = run(make(CW, {"log_destination": "reviews/week-14"}))
    assert output["log_destination"] == "reviews/week-14"
    assert output["log_status"] == "returned_to_caller"
    screened = run(make(SW, {"log_destination": "reviews/week-14"}))
    assert "log_destination" not in screened


@pytest.mark.parametrize(
    ("ref", "code", "reason"),
    [
        pytest.param(7, IR, "invalid_type", id="number"),
        pytest.param("   ", IR, "invalid_value", id="blank"),
        pytest.param("R\x07", IR, "invalid_value", id="control"),
        pytest.param("x", "UNKNOWN_ITEM_REF", "unknown_item_ref", id="letter"),
        pytest.param("R1", "UNKNOWN_ITEM_REF", "unknown_item_ref", id="short"),
        pytest.param("R999", "UNKNOWN_ITEM_REF", "unknown_item_ref", id="not-kept"),
    ],
)
def test_annotation_reference_shape(run, ref, code, reason):
    output = run(make(CW, {"annotations.0.item_ref": ref}))
    assert error_of(output) == (
        code,
        {"reason": reason, "path": "annotations[0].item_ref"},
    )


@pytest.mark.parametrize(
    ("base", "changes", "path"),
    [
        pytest.param(
            CW, {"attestation.minutes": DROP}, "attestation.minutes", id="no-minutes"
        ),
        pytest.param(
            CW, {"attestation.attested": DROP}, "attestation.attested", id="no-attested"
        ),
        pytest.param(
            SW,
            {"previous_tallies": {}},
            "previous_tallies.by_domain",
            id="no-by-domain",
        ),
    ],
)
def test_optional_objects_require_their_keys(run, base, changes, path):
    detail = {"reason": "missing_field", "path": path}
    assert error_of(run(make(base, changes))) == (IR, detail)


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("Rate path outlook", id="no-break-space"),
        pytest.param("Rate path outlook", id="line-separator"),
        pytest.param("Rate path outlook", id="paragraph-separator"),
        pytest.param("  Rate path outlook  ", id="surrounding-spaces"),
    ],
)
def test_unicode_spaces_and_separators_are_ordinary_text(run, text):
    assert titles(run(collected([item(text, "2027-04-07")]))) == [text]
    output = run(make(CW, {"annotations.0.summary": text}))
    assert output["items"][0]["summary"] == text


# --------------------------------------------------------------------------
# The contract errors and their order
# --------------------------------------------------------------------------


def case(name, base, changes, code, reason, path, **extra):
    return pytest.param(
        base, changes, code, {"reason": reason, "path": path, **extra}, id=name
    )


# fmt: off
ERROR_CASES = [
    case("request-not-an-object", lambda: [make(SW)], {}, IR, "parameters_not_object", ""),
    case("request-not-an-object-with-an-action", lambda: [{"action": "fetch"}], {}, IR, "parameters_not_object", ""),
    case("annotations-with-screen-naming-no-kept-item", SW, {"annotations": [annotation("R009")]}, IR,
         "field_not_allowed", "annotations"),
    case("unknown-action", SW, {"action": "fetch"}, IR, "unknown_action", "action"),
    case("unknown-key", SW, {"extra": 1}, IR, "unknown_key", ""),
    case("missing-field", SW, {"max_items": DROP}, IR, "missing_field", "max_items"),
    case("invalid-type", SW, {"max_items": "10"}, IR, "invalid_type", "max_items"),
    case("invalid-value", SW, {"sources.0.url": "http://example.org/feed"}, IR, "invalid_value", "sources[0].url"),
    case("out-of-range", SW, {"max_items": 0}, IR, "out_of_range", "max_items"),
    case("size-cap", SW, {"collection.0.items": many_items(501)}, IR, "size_cap", "collection[0].items", limit=500),
    case("compile-field-with-screen", SW, {"annotations": ANNOTATIONS}, IR, "field_not_allowed", "annotations"),
    case("previous-domain-outside-scope", SW, {"previous_tallies": OUTSIDE}, IR, "unknown_domain",
         "previous_tallies.by_domain"),
    case("impossible-date", SW, {"cycle_start": "2027-04-31"}, "INVALID_DATE", "invalid_date", "cycle_start"),
    case("empty-jurisdictions", SW, {"jurisdictions": []}, "EMPTY_SCOPE", "empty_scope", "jurisdictions"),
    case("end-before-start", SW, {"cycle_end": "2027-04-01"}, "WINDOW_INVALID", "end_before_start", "cycle_end"),
    case("window-too-long", SW, {"cycle_start": "2027-01-01", "cycle_end": "2027-04-03"}, "WINDOW_INVALID",
         "window_too_long", "cycle_end", limit=92),
    case("repeated-domain", SW, {"risk_domains.+": "credit_risk"}, DK, "duplicate_key", "risk_domains[2]"),
    case("repeated-jurisdiction", SW, {"jurisdictions.+": "EU"}, DK, "duplicate_key", "jurisdictions[2]"),
    case("repeated-client-tag", SW, {"client_exposure_tags.+": "shipping"}, DK, "duplicate_key",
         "client_exposure_tags[2]"),
    case("repeated-source-name", SW, {"sources.+": PARAMS[SW]["sources"][0]}, DK, "duplicate_key", "sources[2].name"),
    case("repeated-report", SW, {"collection.+": PARAMS[SW]["collection"][0]}, DK, "duplicate_key",
         "collection[2].source_name"),
    case("repeated-item-domain", SW, {"collection.0.items.0.domains.+": "climate_risk"}, DK, "duplicate_key",
         "collection[0].items[0].domains[1]"),
    case("repeated-item-tag", SW, {"collection.0.items.0.exposure_tags.+": "real_estate"}, DK, "duplicate_key",
         "collection[0].items[0].exposure_tags[1]"),
    case("repeated-annotation", CW, {"annotations.1.item_ref": "R001"}, DK, "duplicate_key",
         "annotations[1].item_ref"),
    case("unconfigured-source", SW, {"collection.+": BLOG_ROLL}, "UNKNOWN_SOURCE", "unknown_source",
         "collection[2].source_name"),
    case("unreported-source", SW, {"collection.1": DROP}, "SOURCE_NOT_REPORTED", "source_not_reported", "sources[1]"),
    case("unreachable-source", SW, UNREACHABLE, "SOURCE_UNREACHABLE", "source_unreachable", "collection[1].status"),
    case("unknown-item-reference", CW, {"annotations.0.item_ref": "R009"}, "UNKNOWN_ITEM_REF", "unknown_item_ref",
         "annotations[0].item_ref"),
    case("annotation-missing", CW, {"annotations.1": DROP}, "ANNOTATION_MISSING", "annotation_missing", "annotations",
         item_ref="R002"),
]
# fmt: on
ERROR_REQUESTS = {param.id: param.values[:2] for param in ERROR_CASES}


def test_every_code_and_reason_has_a_case():
    covered = {(param.values[2], param.values[3]["reason"]) for param in ERROR_CASES}
    registry = ERROR_REASONS.items()
    assert covered == {
        (code, reason) for code, reasons in registry for reason in reasons
    }


@pytest.mark.parametrize(("base", "changes", "code", "detail"), ERROR_CASES)
def test_each_error_case(run, base, changes, code, detail):
    output = run(make(base, changes))
    assert output == {"status": "error", "error": {"code": code, "detail": detail}}


def order(name, steps, base, changes, code, reason, path, **extra):
    detail = {"reason": reason, "path": path, **extra}
    return pytest.param(steps, base, changes, code, detail, id=name)


# Each entry violates two checks; the earlier check of the documented order
# wins. The steps: 1 object, 2 action, 3 unknown top-level keys, 4 required
# fields and top-level types, 5 empty scope, 6 size caps, 7 item shapes,
# 8 real dates, 9 window, 10 duplicates, 11 previous tallies domains,
# 12 collection reports, 13 compile-only fields, 14 annotations.
# fmt: off
ORDER_CASES = [
    order("action-before-keys", (2, 3), SW, {"action": "fetch", "extra": 1}, IR, "unknown_action", "action"),
    order("keys-before-required", (3, 4), SW, {"extra": 1, "max_items": DROP}, IR, "unknown_key", ""),
    order("required-before-scope", (4, 5), SW, {"max_items": DROP, "risk_domains": []}, IR, "missing_field",
          "max_items"),
    order("scope-before-caps", (5, 6), SW, {"risk_domains": [], "jurisdictions": jurisdiction_list(51)},
          "EMPTY_SCOPE", "empty_scope", "risk_domains"),
    order("caps-before-shapes", (6, 7), SW, {"client_exposure_tags": tag_list(51), "max_items": 0}, IR, "size_cap",
          "client_exposure_tags", limit=50),
    order("shapes-before-dates", (7, 8), SW, {"max_items": 0, "cycle_start": "2027-04-31"}, IR, "out_of_range",
          "max_items"),
    order("dates-before-window", (8, 9), SW,
          {"collection.0.items.0.published_date": "2027-02-30", "cycle_end": "2027-04-01"},
          "INVALID_DATE", "invalid_date", "collection[0].items[0].published_date"),
    order("window-before-duplicates", (9, 10), SW, {"cycle_end": "2027-04-01", "jurisdictions.+": "EU"},
          "WINDOW_INVALID", "end_before_start", "cycle_end"),
    order("duplicates-before-previous", (10, 11), SW, {"jurisdictions.+": "EU", "previous_tallies": OUTSIDE}, DK,
          "duplicate_key", "jurisdictions[2]"),
    order("previous-before-reports", (11, 12), SW, {"previous_tallies": OUTSIDE, "collection.+": BLOG_ROLL}, IR,
          "unknown_domain", "previous_tallies.by_domain"),
    order("reports-before-compile-fields", (12, 13), SW, {**UNREACHABLE, "attestation": ATTESTED},
          "SOURCE_UNREACHABLE", "source_unreachable", "collection[1].status"),
    order("unknown-source-before-unreported", (12, 12), SW, {"collection.1": BLOG_ROLL}, "UNKNOWN_SOURCE",
          "unknown_source", "collection[1].source_name"),
    order("unreported-before-unreachable", (12, 12), SW, {"collection.1": DROP, "collection.0.status": "unreachable"},
          "SOURCE_NOT_REPORTED", "source_not_reported", "sources[1]"),
    order("unknown-reference-before-missing", (14, 14), CW, {"annotations": [annotation("R009")]},
          "UNKNOWN_ITEM_REF", "unknown_item_ref", "annotations[0].item_ref"),
    order("repeated-reference-before-missing", (14, 14), CW, {"annotations": [annotation("R001")] * 2}, DK,
          "duplicate_key", "annotations[1].item_ref"),
]
# fmt: on


# Adjacent checks that one request cannot fail together have no pair entry;
# each of the two checks has its own case in the error table.
UNPRODUCIBLE_PAIRS = {
    (1, 2): "a request that is not an object has no action to check",
    (
        13,
        14,
    ): "compile-only fields fail with screen; annotations are checked with compile",
}


def test_pairwise_table_covers_every_adjacent_pair():
    pairs = {p.values[0] for p in ORDER_CASES if p.values[0][1] == p.values[0][0] + 1}
    every_pair = {(step, step + 1) for step in range(1, 14)}
    assert pairs == every_pair - set(UNPRODUCIBLE_PAIRS)
    assert not pairs & set(UNPRODUCIBLE_PAIRS)


@pytest.mark.parametrize(("steps", "base", "changes", "code", "detail"), ORDER_CASES)
def test_first_violation_wins_in_documented_order(
    run, steps, base, changes, code, detail
):
    output = run(make(base, changes))
    assert output == {"status": "error", "error": {"code": code, "detail": detail}}


# --------------------------------------------------------------------------
# Schemas, error details and faults
# --------------------------------------------------------------------------

SCHEMA_CASES = [pytest.param((name, None), id=name) for name in CASES]
SCHEMA_CASES += [
    pytest.param(values, id=name) for name, values in ERROR_REQUESTS.items()
]


@pytest.mark.parametrize("request_", SCHEMA_CASES)
def test_outputs_conform_to_the_output_schema(run, request_):
    schema = read_json(BUNDLE / "schemas" / "output.schema.json")
    jsonschema.validate(instance=run(make(*request_)), schema=schema)


@pytest.mark.parametrize("name", CASES)
def test_fixture_requests_conform_to_the_request_schema(name):
    schema = read_json(BUNDLE / "schemas" / "request.schema.json")
    jsonschema.validate(instance=PARAMS[name], schema=schema)


MARK = "ZQX-planted-text"
PATH_SHAPE = re.compile("[a-z_]+(\\[[0-9]+\\])*(\\.[a-z_]+(\\[[0-9]+\\])*)*", re.ASCII)
# fmt: off
ECHO_CASES = [
    (SW, {MARK: 1}),
    (SW, {"collection.0.items.0." + MARK: 1}),
    (SW, {"collection.0.items.0.title": MARK + "\x00"}),
    (SW, {"collection.0.items.0.title": MARK * 30}),
    (SW, {"collection.1.source_name": MARK}),
    (SW, {"log_destination": MARK + "\x01"}),
    (SW, {"previous_tallies": {"by_domain": {MARK: 1}}}),
    (SW, {"risk_domains.0": MARK}),
    (CW, {"annotations.0.item_ref": MARK}),
    (CW, {"annotations.0.summary": MARK * 200}),
    (CW, {"annotations.1.impact.note": MARK + "\n"}),
    (CW, {"annotations.1.impact.area": MARK}),
]
# fmt: on


def test_error_details_never_echo_input_text(run):
    outputs = [run(make(*request_)) for request_ in ECHO_CASES]
    outputs += [run(make(*request_)) for request_ in ERROR_REQUESTS.values()]
    for output in outputs:
        _, detail = error_of(output)
        assert MARK not in canonical(output)
        assert set(detail) <= {"reason", "path", "limit", "item_ref"}
        assert detail["path"] == "" or PATH_SHAPE.fullmatch(detail["path"])
        if "item_ref" in detail:
            assert re.fullmatch("R[0-9]{3}", detail["item_ref"], re.ASCII)


def test_internal_fault_returns_only_the_fault_envelope(module, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("planted fault text")

    monkeypatch.setattr(module, "_screen", broken)
    output = module.ScopedRiskLiteratureScanSkill().execute(make(SW))
    detail = {"reason": "internal_error", "exception": "RuntimeError"}
    assert output == {
        "status": "error",
        "error": {"code": "INTERNAL_ERROR", "detail": detail},
    }
    schema = read_json(BUNDLE / "schemas" / "output.schema.json")
    jsonschema.validate(instance=output, schema=schema)


def _floats(value):
    if isinstance(value, float):
        yield value
    elif isinstance(value, dict):
        for inner in value.values():
            yield from _floats(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from _floats(inner)


def test_no_float_in_any_output(run):
    outputs = [run(make(name)) for name in CASES]
    outputs += [run(make(*request_)) for request_ in ERROR_REQUESTS.values()]
    for output in outputs:
        assert list(_floats(output)) == []


# --------------------------------------------------------------------------
# Field rules
# --------------------------------------------------------------------------

# fmt: off
UNKNOWN_KEY_CASES = [
    pytest.param(SW, {"extra": 1}, "", id="top-level"),
    pytest.param(SW, {"sources.0.extra": 1}, "sources[0]", id="source"),
    pytest.param(SW, {"previous_tallies": {"by_domain": {}, "extra": 1}}, "previous_tallies", id="previous-tallies"),
    pytest.param(SW, {"collection.0.extra": 1}, "collection[0]", id="report"),
    pytest.param(SW, {"collection.0.items.0.extra": 1}, "collection[0].items[0]", id="item"),
    pytest.param(CW, {"attestation.extra": 1}, "attestation", id="attestation"),
    pytest.param(CW, {"annotations.0.extra": 1}, "annotations[0]", id="annotation"),
    pytest.param(CW, {"annotations.0.impact.extra": 1}, "annotations[0].impact", id="impact"),
]
TYPE_CASES = [
    pytest.param(SW, "max_items", True, "max_items", id="max-items-boolean"),
    pytest.param(SW, "max_items", "10", "max_items", id="max-items-string"),
    pytest.param(SW, "max_items", 10.0, "max_items", id="max-items-fraction"),
    pytest.param(SW, "cycle_start", 20270405, "cycle_start", id="date-number"),
    pytest.param(SW, "risk_domains", "credit_risk", "risk_domains", id="domains-string"),
    pytest.param(SW, "risk_domains.1", 7, "risk_domains[1]", id="domain-number"),
    pytest.param(SW, "sources.0", "supervisor_feed", "sources[0]", id="source-string"),
    pytest.param(SW, "collection.0.status", 1, "collection[0].status", id="status-number"),
    pytest.param(SW, "collection.0.items", {}, "collection[0].items", id="items-object"),
    pytest.param(SW, "collection.0.items.0.title", 7, "collection[0].items[0].title", id="title-number"),
    pytest.param(SW, "collection.0.items.0.published_date", 20270407, "collection[0].items[0].published_date",
                 id="published-date-number"),
    pytest.param(SW, "previous_tallies", {"by_domain": {"credit_risk": True}}, "previous_tallies.by_domain",
                 id="previous-count-boolean"),
    pytest.param(SW, "previous_tallies", {"by_domain": {"credit_risk": "5"}}, "previous_tallies.by_domain",
                 id="previous-count-string"),
    pytest.param(CW, "attestation.attested", 1, "attestation.attested", id="attested-number"),
    pytest.param(CW, "attestation.minutes", True, "attestation.minutes", id="minutes-boolean"),
    pytest.param(CW, "attestation", True, "attestation", id="attestation-boolean"),
    pytest.param(CW, "annotations", {}, "annotations", id="annotations-object"),
    pytest.param(CW, "annotations.0.impact", "high", "annotations[0].impact", id="impact-string"),
]
TEXT_FIELDS = [
    ("title", SW, "collection.0.items.0.title", "collection[0].items[0].title"),
    ("report-source", SW, "collection.0.source_name", "collection[0].source_name"),
    ("log-destination", SW, "log_destination", "log_destination"),
    ("annotation-reference", CW, "annotations.0.item_ref", "annotations[0].item_ref"),
    ("summary", CW, "annotations.0.summary", "annotations[0].summary"),
    ("impact-note", CW, "annotations.0.impact.note", "annotations[0].impact.note"),
]
BAD_TEXTS = [
    ("blank", "   "),
    ("spaces-and-line-feeds", " \n \n"),
    ("next-line-control", "Rate\u0085path"),
    ("null-control", "Rate\x00path"),
    ("line-feed", "Rate\npath"),
    ("delete-control", "Rate\x7fpath"),
    ("lone-surrogate", "Rate\ud800path"),
]
TEXT_CASES = [
    pytest.param(base, location, text, path, id=f"{field}-{label}")
    for field, base, location, path in TEXT_FIELDS
    for label, text in BAD_TEXTS
]
# fmt: on


@pytest.mark.parametrize(("base", "changes", "path"), UNKNOWN_KEY_CASES)
def test_unknown_keys_are_rejected_at_every_level(run, base, changes, path):
    detail = {"reason": "unknown_key", "path": path}
    assert error_of(run(make(base, changes))) == (IR, detail)


@pytest.mark.parametrize(("base", "location", "value", "path"), TYPE_CASES)
def test_type_rules_are_strict(run, base, location, value, path):
    detail = {"reason": "invalid_type", "path": path}
    assert error_of(run(make(base, {location: value}))) == (IR, detail)


@pytest.mark.parametrize(("base", "location", "text", "path"), TEXT_CASES)
def test_text_field_rules(run, base, location, text, path):
    detail = {"reason": "invalid_value", "path": path}
    assert error_of(run(make(base, {location: text}))) == (IR, detail)


SURROGATE = "\ud800"
BY_DOMAIN = "previous_tallies"
# fmt: off
SURROGATE_CASES = [
    pytest.param(SW, "action", "screen", IR, "unknown_action", "action", id="action"),
    pytest.param(SW, "cycle_start", "2027-04-05", "INVALID_DATE", "invalid_date", "cycle_start", id="cycle-start"),
    pytest.param(SW, "cycle_end", "2027-04-11", "INVALID_DATE", "invalid_date", "cycle_end", id="cycle-end"),
    pytest.param(SW, "risk_domains.0", "credit_risk", IR, "invalid_value", "risk_domains[0]", id="risk-domain"),
    pytest.param(SW, "jurisdictions.0", "EU", IR, "invalid_value", "jurisdictions[0]", id="jurisdiction"),
    pytest.param(SW, "sources.0.name", "supervisor_feed", IR, "invalid_value", "sources[0].name", id="source-name"),
    pytest.param(SW, "sources.0.url", "https://example.org/feed", IR, "invalid_value", "sources[0].url",
                 id="source-link"),
    pytest.param(SW, "sources.0.kind", "regulatory", IR, "invalid_value", "sources[0].kind", id="source-kind"),
    pytest.param(SW, "client_exposure_tags.0", "shipping", IR, "invalid_value", "client_exposure_tags[0]",
                 id="client-tag"),
    pytest.param(SW, "collection.0.source_name", "supervisor_feed", IR, "invalid_value",
                 "collection[0].source_name", id="report-source"),
    pytest.param(SW, "collection.0.status", "ok", IR, "invalid_value", "collection[0].status", id="report-status"),
    pytest.param(SW, "collection.0.items.0.title", "Consultation", IR, "invalid_value",
                 "collection[0].items[0].title", id="title"),
    pytest.param(SW, "collection.0.items.0.published_date", "2027-04-07", "INVALID_DATE", "invalid_date",
                 "collection[0].items[0].published_date", id="published-date"),
    pytest.param(SW, "collection.0.items.0.jurisdiction", "EU", IR, "invalid_value",
                 "collection[0].items[0].jurisdiction", id="item-jurisdiction"),
    pytest.param(SW, "collection.0.items.0.domains.0", "climate_risk", IR, "invalid_value",
                 "collection[0].items[0].domains[0]", id="item-domain"),
    pytest.param(SW, "collection.0.items.0.exposure_tags.0", "real_estate", IR, "invalid_value",
                 "collection[0].items[0].exposure_tags[0]", id="item-tag"),
    pytest.param(SW, "collection.0.items.0.url", "https://example.org/a", IR, "invalid_value",
                 "collection[0].items[0].url", id="item-link"),
    pytest.param(SW, "log_destination", "reviews", IR, "invalid_value", "log_destination", id="log-destination"),
    pytest.param(CW, "annotations.0.item_ref", "R001", IR, "invalid_value", "annotations[0].item_ref",
                 id="item-ref"),
    pytest.param(CW, "annotations.0.summary", "The supervisor", IR, "invalid_value", "annotations[0].summary",
                 id="summary"),
    pytest.param(CW, "annotations.0.impact.area", "exposures", IR, "invalid_value", "annotations[0].impact.area",
                 id="impact-area"),
    pytest.param(CW, "annotations.0.impact.likelihood", "medium", IR, "invalid_value",
                 "annotations[0].impact.likelihood", id="impact-likelihood"),
    pytest.param(CW, "annotations.0.impact.note", "Property", IR, "invalid_value", "annotations[0].impact.note",
                 id="impact-note"),
]
# fmt: on


@pytest.mark.parametrize(
    ("base", "location", "text", "code", "reason", "path"), SURROGATE_CASES
)
def test_lone_surrogates_fail_at_the_first_check_that_reads_the_field(
    run, base, location, text, code, reason, path
):
    """A date fails its date check; any other string fails its own field check."""
    detail = {"reason": reason, "path": path}
    assert error_of(run(make(base, {location: text + SURROGATE}))) == (code, detail)


def test_lone_surrogate_in_a_previous_tallies_domain_fails_the_scope_check(run):
    previous = {"by_domain": {"credit_risk" + SURROGATE: 1}}
    detail = {"reason": "unknown_domain", "path": "previous_tallies.by_domain"}
    assert error_of(run(make(SW, {BY_DOMAIN: previous}))) == (IR, detail)


def _text(base, location):
    return lambda n: make(base, {location: "x" * n})


def _link(location):
    return lambda n: make(SW, {location: "https://example.org/" + "a" * (n - 20)})


# fmt: off
CAP_CASES = [
    ("risk-domains", lambda n: make(SW, {"risk_domains": domain_list(n)}), 20, "risk_domains"),
    ("jurisdictions", lambda n: make(SW, {"jurisdictions": jurisdiction_list(n)}), 50, "jurisdictions"),
    ("sources", with_sources, 50, "sources"),
    ("client-tags", lambda n: make(SW, {"client_exposure_tags": tag_list(n)}), 50, "client_exposure_tags"),
    ("report-items", spread, 500, "collection[0].items"),
    ("all-items", lambda n: spread(500, 500, 500, 500, n - 2000), 2000, "collection"),
    ("annotations", lambda n: annotated(200, extra=n - 200), 200, "annotations"),
    ("item-domains", lambda n: make(SW, {"collection.0.items.0.domains": item_domains(n)}), 10,
     "collection[0].items[0].domains"),
    ("item-tags", lambda n: make(SW, {"collection.0.items.0.exposure_tags": tag_list(n)}), 20,
     "collection[0].items[0].exposure_tags"),
    ("title", _text(SW, "collection.0.items.0.title"), 300, "collection[0].items[0].title"),
    ("summary", _text(CW, "annotations.0.summary"), 2000, "annotations[0].summary"),
    ("impact-note", _text(CW, "annotations.0.impact.note"), 1000, "annotations[0].impact.note"),
    ("log-destination", _text(SW, "log_destination"), 512, "log_destination"),
    ("source-link", _link("sources.0.url"), 512, "sources[0].url"),
    ("item-link", _link("collection.0.items.0.url"), 512, "collection[0].items[0].url"),
]
RANGE_CASES = [
    ("max-items", lambda n: make(SW, {"max_items": n}), 1, 200, "max_items"),
    ("minutes", lambda n: make(CW, {"attestation.minutes": n}), 0, 1440, "attestation.minutes"),
    ("previous-count", lambda n: make(SW, {"previous_tallies": {"by_domain": {"credit_risk": n}}}), 0, 100000,
     "previous_tallies.by_domain"),
]
SIZE_CASES = (
    [pytest.param(build, cap, None, id=f"{name}-at-cap") for name, build, cap, _ in CAP_CASES]
    + [pytest.param(build, cap + 1, {"reason": "size_cap", "path": path, "limit": cap}, id=f"{name}-over-cap")
       for name, build, cap, path in CAP_CASES]
    + [pytest.param(build, value, None, id=f"{name}-{label}")
       for name, build, low, high, _ in RANGE_CASES for label, value in (("lowest", low), ("highest", high))]
    + [pytest.param(build, value, {"reason": "out_of_range", "path": path}, id=f"{name}-{label}")
       for name, build, low, high, path in RANGE_CASES for label, value in (("below", low - 1), ("above", high + 1))]
    + [pytest.param(lambda n: make(SW, {"sources": []}), 0, {"reason": "out_of_range", "path": "sources"},
                    id="no-sources"),
       pytest.param(lambda n: make(SW, {"collection.0.items.0.domains": []}), 0,
                    {"reason": "out_of_range", "path": "collection[0].items[0].domains"}, id="item-without-domains")]
)
# fmt: on


@pytest.mark.parametrize(("build", "value", "detail"), SIZE_CASES)
def test_size_caps(run, build, value, detail):
    output = run(build(value))
    if detail is None:
        assert output["status"] == "completed", output.get("error")
    else:
        assert error_of(output) == (IR, detail)


# fmt: off
DATE_CASES = [
    pytest.param("cycle_start", "2027-02-29", "cycle_start", id="not-a-leap-year"),
    pytest.param("cycle_start", "2027-4-5", "cycle_start", id="one-digit-month-and-day"),
    pytest.param("cycle_start", "2027-04-05T00:00", "cycle_start", id="with-a-time"),
    pytest.param("cycle_start", "２０２７-04-05", "cycle_start", id="full-width-digits"),
    pytest.param("cycle_start", "2027-04-05\n", "cycle_start", id="trailing-line-feed"),
    pytest.param("cycle_start", "2027-04-00", "cycle_start", id="day-zero"),
    pytest.param("cycle_end", "0000-04-11", "cycle_end", id="year-zero"),
    pytest.param("collection.0.items.0.published_date", "2027-02-30", "collection[0].items[0].published_date",
                 id="published-date"),
]
# fmt: on


@pytest.mark.parametrize(("location", "value", "path"), DATE_CASES)
def test_dates_are_real_calendar_dates(run, location, value, path):
    detail = {"reason": "invalid_date", "path": path}
    assert error_of(run(make(SW, {location: value}))) == ("INVALID_DATE", detail)
    leap = make(SW, {"cycle_start": "2028-02-29", "cycle_end": "2028-03-01"})
    assert run(leap)["window_days"] == 2


# --------------------------------------------------------------------------
# Source rules the shared guard does not check
# --------------------------------------------------------------------------

TEXT_FOLDING = frozenset(
    "lower upper casefold title swapcase capitalize isdigit isnumeric isalpha "
    "isalnum isspace splitlines".split(" ")
)
DECIMAL_CONTEXT = frozenset(
    "getcontext setcontext localcontext DefaultContext BasicContext "
    "ExtendedContext".split(" ")
)
NEEDS_ARGUMENT = frozenset({"split", "strip", "lstrip", "rstrip"})
PATTERN_CALLS = frozenset(
    "compile search match fullmatch sub subn split findall finditer".split(" ")
)
UNICODE_CLASS = re.compile("\\\\[dDwWsSbB]", re.ASCII)


def _is_re_attribute(node, name):
    return (
        isinstance(node, ast.Attribute)
        and node.attr == name
        and isinstance(node.value, ast.Name)
        and node.value.id == "re"
    )


def _name_rules(node, name):
    found = []
    if name == "float":
        found.append("float")
    if name in DECIMAL_CONTEXT:
        found.append("decimal-context")
    if name == "fromisoformat":
        found.append("date-parsing")
    if name == "unicodedata":
        found.append("unicode-database")
    if isinstance(node, ast.Attribute) and name in TEXT_FOLDING:
        found.append("text-folding")
    if isinstance(node, ast.Attribute) and name in ("IGNORECASE", "I"):
        found.append("case-insensitive-pattern")
    return found


def _call_rules(node):
    found = []
    if node.func.attr in NEEDS_ARGUMENT and not node.args and not node.keywords:
        found.append("argument-less-strip-or-split")
    if _is_re_attribute(node.func, node.func.attr) and node.func.attr in PATTERN_CALLS:
        parts = list(node.args) + [keyword.value for keyword in node.keywords]
        inner = [sub for part in parts for sub in ast.walk(part)]
        if not any(_is_re_attribute(sub, "ASCII") for sub in inner):
            found.append("pattern-without-ascii")
        first = node.args[0] if node.args else None
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            if UNICODE_CLASS.search(first.value):
                found.append("unicode-class")
    return found


def rule_findings(source):
    """Return one rule name per place where ``source`` breaks a source rule."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name):
            found += _name_rules(node, node.id)
        elif isinstance(node, ast.Attribute):
            found += _name_rules(node, node.attr)
        elif isinstance(node, ast.alias):
            found += _name_rules(node, node.name)
        elif isinstance(node, ast.Constant) and isinstance(node.value, float):
            found.append("float")
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            found.append("float")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            found += _call_rules(node)
    return found


RULE_CONTROLS = [
    ("x = 0.5", "float"),
    ("y = float(z)", "float"),
    ("q = a / b", "float"),
    ("from decimal import getcontext", "decimal-context"),
    ("c = decimal.localcontext()", "decimal-context"),
    ("d = date.fromisoformat(s)", "date-parsing"),
    ("import unicodedata", "unicode-database"),
    ("t = s.lower()", "text-folding"),
    ("t = s.casefold()", "text-folding"),
    ("t = s.isspace()", "text-folding"),
    ("lines = s.splitlines()", "text-folding"),
    ("parts = s.split()", "argument-less-strip-or-split"),
    ("t = s.strip()", "argument-less-strip-or-split"),
    ("p = re.compile('[a-z]+')", "pattern-without-ascii"),
    ("m = re.fullmatch('[a-z]+', s)", "pattern-without-ascii"),
    ("p = re.compile('[a-z]+', re.IGNORECASE | re.ASCII)", "case-insensitive-pattern"),
    ("p = re.compile(r'\\d+', re.ASCII)", "unicode-class"),
]
CLEAN_CONTROLS = [
    "p = re.compile('[a-z]+', re.ASCII)",
    "m = re.fullmatch('[a-z]+', s, flags=re.ASCII)",
    "t = s.strip(' ')",
    "parts = s.split(',')",
    "n = a // b",
    "d = date(2027, 4, 5).isoformat()",
]


def test_runtime_modules_follow_the_text_number_and_date_rules():
    """No float, no thread decimal context, no fromisoformat and ASCII-only text."""
    modules = sorted(
        path
        for path in BUNDLE.rglob("*.py")
        if path.name != "test_skill.py" and "__pycache__" not in path.parts
    )
    assert [path.name for path in modules] == ["__init__.py", "skill.py"]
    for path in modules:
        assert rule_findings(path.read_text(encoding="utf-8")) == [], path.name
    for source, rule in RULE_CONTROLS:
        assert rule in rule_findings(source), source
    for source in CLEAN_CONTROLS:
        assert rule_findings(source) == [], source
