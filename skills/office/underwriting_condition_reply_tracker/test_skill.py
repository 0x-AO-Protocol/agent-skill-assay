"""Bundle tests for office/underwriting_condition_reply_tracker.

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
REGISTRY_ID = "office/underwriting_condition_reply_tracker"
TR, SL = "track_nothing_at_risk", "scaffold_lead_window"
CT, CC = "compile_template", "compile_caller_text"
CASES = (TR, SL, CT, CC)
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
SERVED_ACTIVITIES = {"onet_release": "31.0", "ids": ["4.A.4.b.1.c.8"]}
PARAMETER_TYPES = {
    "action": "string",
    "as_of_date": "string",
    "closing_deadline": "string",
    "escalation_lead_days": "integer",
    "verified_file": "array",
    "condition_requests": "array",
    "drafts": "array",
}
IR, DUP, FIG = "INVALID_REQUEST", "DUPLICATE_ID", "UNSUPPORTED_FIGURE"
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
    ),
    "INVALID_DATE": ("invalid_date",),
    "EMPTY_VERIFIED_FILE": ("empty_verified_file",),
    "DEADLINE_BEFORE_AS_OF": ("deadline_before_as_of",),
    "REQUEST_AFTER_AS_OF": ("request_after_as_of",),
    DUP: ("duplicate_id",),
    "NO_OPEN_CONDITION": ("no_open_condition",),
    "UNKNOWN_CONDITION": ("unknown_condition",),
    "DRAFT_FOR_CLOSED_CONDITION": ("condition_not_open",),
    "DRAFT_MISSING": ("draft_missing",),
    "UNKNOWN_DOCUMENT": ("unknown_document",),
    "MALFORMED_CITATION": ("malformed_marker",),
    "CITATION_MISMATCH": ("marker_not_cited", "cited_without_marker"),
    FIG: (
        "non_ascii_digit",
        "unsupported_date",
        "unsupported_number",
        "unsupported_token",
        "unsupported_number_word",
    ),
}
FAULT_REASONS = {"INTERNAL_ERROR": ("internal_error",)}
FIELDS = ("issue_summary", "proposed_resolution", "reply_body")
CAPS = {"issue_summary": 600, "proposed_resolution": 600, "reply_body": 4000}


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
    from skills.office.underwriting_condition_reply_tracker import skill

    return skill


@pytest.fixture(scope="module")
def run(module):
    return module.UnderwritingConditionReplyTrackerSkill().execute


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


def document(doc_id, title="Supporting statement", summary=None):
    entry = {"document_id": doc_id, "title": title, "document_type": "other"}
    if summary is not None:
        entry["summary"] = summary
    return entry


def documents(count, id_length=6, title="Supporting statement"):
    return [document(("D%d" % n).ljust(id_length, "x"), title) for n in range(count)]


def condition(cond_id, status="open", description="Provide the statement."):
    return {
        "condition_id": cond_id,
        "requested_on": "2027-02-25",
        "description": description,
        "status": status,
    }


def citing(docs, cond_id="UC-7", base=CT):
    """A compile request whose first draft cites every one of ``docs``."""
    ids = [doc["document_id"] for doc in docs]
    changes = {"verified_file": docs, "drafts.0.cited_document_ids": ids}
    return make(base, {**changes, "drafts.1.cited_document_ids": ids[:1]})


def all_open_with_drafts(count):
    """Up to 100 open conditions and ``count`` drafts that cite DOC-A."""
    ids = ["C%d" % n for n in range(min(count, 100))]
    drafts = [
        {"condition_id": ids[n % len(ids)], "cited_document_ids": ["DOC-A"]}
        for n in range(count)
    ]
    return make(
        CC, {"condition_requests": [condition(i) for i in ids], "drafts": drafts}
    )


def error_of(output):
    assert output["status"] == "error", output
    assert set(output) == {"status", "error"}
    assert set(output["error"]) == {"code", "detail"}
    return output["error"]["code"], output["error"]["detail"]


def canonical(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def drafts_of(output):
    return {draft["condition_id"]: draft for draft in output["drafts"]}


DOC_A = PARAMS[TR]["verified_file"][0]
CC_DRAFTS = PARAMS[CC]["drafts"]
CT_DRAFTS = PARAMS[CT]["drafts"]
REPLY = PARAMS[CC]["drafts"][0]["reply_body"]
ALL_RESOLVED = {
    "condition_requests.0.status": "resolved",
    "condition_requests.1.status": "resolved",
}
EVIDENCE = {
    "verified_file.0.title": "Bank statement Q4, February",
    "verified_file.0.summary": "Balance 1500 on 2027-02-14 at 3x cover.",
}


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
    assert manifest["category"] == "office"
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
    assert set(manifest["outputs"]) >= {"status", "tracker", "drafts", "run_summary"}
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
    assert properties["action"]["enum"] == ["track", "scaffold", "compile"]


def test_manifest_matches_the_skill_constant(module):
    manifest = read_manifest()
    assert module.MANIFEST == {
        "name": manifest["name"],
        "version": manifest["version"],
        "parameters": manifest["parameters"],
    }


@pytest.mark.parametrize("name", CASES)
def test_every_params_fixture_passes_parameter_validation(module, name):
    skill = module.UnderwritingConditionReplyTrackerSkill()
    assert skill.validate_params(make(name)) is True


def test_card_issuer_and_fields_match():
    card = read_json(BUNDLE / "card.json")
    manifest = read_manifest()
    assert card["issuer"] == manifest["issuer"]
    assert card["name"] == "Underwriting Condition Reply Tracker"
    assert card["description"] == manifest["short_description"]
    sample = read_json(FIXTURES / "card_ui_samples.json")
    assert card["ui_schema"]["type"] == "card"
    for field in card["ui_schema"]["fields"]:
        assert field["key"] in sample and field["label"], field


def test_card_sample_equals_the_output_of_its_params_fixture(run):
    sample = read_json(FIXTURES / "card_ui_samples.json")
    assert canonical(sample) == canonical(run(make(TR)))


def test_actions_are_exactly_the_published_set(module):
    assert module.ACTIONS == ("track", "scaffold", "compile")
    enum = read_manifest()["parameters"]["properties"]["action"]["enum"]
    assert enum == list(module.ACTIONS)


def test_error_registry_is_closed_and_final(module):
    assert module.ERROR_REGISTRY == ERROR_REASONS
    assert module.FAULT_REGISTRY == FAULT_REASONS


# --------------------------------------------------------------------------
# The worked examples
# --------------------------------------------------------------------------


def test_track_with_no_condition_at_risk(run):
    output = run(make(TR))
    assert canonical(output) == canonical(EXPECTED[TR])
    assert output["escalations"] == []


def test_scaffold_inside_the_lead_window(run):
    output = run(make(SL))
    assert canonical(output) == canonical(EXPECTED[SL])
    assert [slot["condition_id"] for slot in output["slots"]] == ["UC-7", "UC-8"]


def test_compile_composes_template_drafts(run):
    output = run(make(CT))
    assert canonical(output) == canonical(EXPECTED[CT])
    assert {draft["composed_by"] for draft in output["drafts"]} == {"template"}


def test_compile_keeps_checked_caller_text(run):
    output = run(make(CC))
    assert canonical(output) == canonical(EXPECTED[CC])
    assert output["drafts"][0]["composed_by"] == "caller"


@pytest.mark.parametrize("name", CASES)
def test_reference_fixtures_match_expected_outputs(run, name):
    params = make(name)
    before = canonical(params)
    for _ in range(2):
        assert canonical(run(params)) == canonical(EXPECTED[name])
    assert canonical(params) == before


def _rejects(run, base, changes, code, reason, path):
    detail = {"reason": reason, "path": path}
    assert error_of(run(make(base, changes))) == (code, detail)


def test_deadline_before_run_date_is_rejected(run):
    changes = {"closing_deadline": "2027-02-27"}
    reason = "deadline_before_as_of"
    _rejects(run, TR, changes, "DEADLINE_BEFORE_AS_OF", reason, "closing_deadline")


def test_empty_verified_file_is_rejected(run):
    changes = {"verified_file": []}
    reason = "empty_verified_file"
    _rejects(run, TR, changes, "EMPTY_VERIFIED_FILE", reason, "verified_file")


def test_cited_document_outside_the_inventory_is_rejected(run):
    reply = "The February statement [doc:DOC-Z] shows the balance."
    changes = {"drafts.0.cited_document_ids": ["DOC-Z"], "drafts.0.reply_body": reply}
    path = "drafts[0].cited_document_ids[0]"
    _rejects(run, CC, changes, "UNKNOWN_DOCUMENT", "unknown_document", path)


def test_draft_for_a_waived_condition_is_rejected(run):
    changes = {"drafts.+": {"condition_id": "UC-9", "cited_document_ids": ["DOC-T"]}}
    code = "DRAFT_FOR_CLOSED_CONDITION"
    _rejects(run, CT, changes, code, "condition_not_open", "drafts[2].condition_id")


def test_lead_window_above_range_is_rejected(run):
    changes = {"escalation_lead_days": 400}
    _rejects(run, TR, changes, IR, "out_of_range", "escalation_lead_days")


def test_reply_body_without_markers_is_rejected(run):
    changes = {
        "drafts.0.reply_body": "The statement shows a balance above two payments."
    }
    reason = "cited_without_marker"
    _rejects(run, CC, changes, "CITATION_MISMATCH", reason, "drafts[0].reply_body")


def test_drafts_are_not_allowed_with_track(run):
    _rejects(run, TR, {"drafts": CC_DRAFTS}, IR, "field_not_allowed", "drafts")


def test_open_condition_without_a_draft_is_rejected(run):
    changes = {"drafts.1": DROP}
    _rejects(
        run, CT, changes, "DRAFT_MISSING", "draft_missing", "condition_requests[1]"
    )


# --------------------------------------------------------------------------
# Countdown, templates and the cut rule
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("closing", "lead", "flagged"),
    [
        pytest.param("2027-03-06", 5, ["UC-1", "UC-2"], id="days-equal-to-window"),
        pytest.param("2027-03-07", 5, [], id="one-day-more"),
        pytest.param("2027-03-01", None, ["UC-1", "UC-2"], id="same-day-closing"),
        pytest.param("2027-03-01", 0, ["UC-1", "UC-2"], id="window-zero-same-day"),
        pytest.param("2027-03-02", 0, [], id="window-zero-next-day"),
    ],
)
def test_lead_window_boundaries(run, closing, lead, flagged):
    changes = {"closing_deadline": closing, "condition_requests.1.status": "open"}
    if lead is not None:
        changes["escalation_lead_days"] = lead
    changes["condition_requests.+"] = condition("UC-3", "waived")
    output = run(make(TR, changes))
    assert [e["condition_id"] for e in output["escalations"]] == flagged
    assert [e["at_risk"] for e in output["tracker"]] == [bool(flagged)] * 2 + [False]
    assert output["lead_window_days"] == (7 if lead is None else lead)


SEVERAL_REPLY = (
    "Regarding condition UC-7, raised 2027-05-06.\n\n"
    "The verified file holds these documents in support:\n"
    "- Homeowner insurance declaration [doc:DOC-H]\n"
    "- Preliminary title report [doc:DOC-T]\n\n"
    "We ask that the condition be reviewed for clearance on the basis of these "
    "documents."
)
SEVERAL_RESOLUTION = (
    "Review the cited verified documents (DOC-H, DOC-T) and clear condition UC-7 "
    "if they satisfy it."
)


@pytest.mark.parametrize(
    ("cited", "resolution", "reply"),
    [
        pytest.param(["DOC-H"], None, None, id="one-document"),
        pytest.param(
            ["DOC-T", "DOC-H"], SEVERAL_RESOLUTION, SEVERAL_REPLY, id="two-documents"
        ),
    ],
)
def test_template_text_is_byte_exact(run, cited, resolution, reply):
    draft = drafts_of(run(make(CT, {"drafts.0.cited_document_ids": cited})))["UC-7"]
    expected = EXPECTED[CT]["drafts"][0]
    assert draft["issue_summary"] == expected["issue_summary"]
    assert draft["cited_document_ids"] == sorted(cited)
    assert draft["proposed_resolution"] == (
        resolution or expected["proposed_resolution"]
    )
    assert draft["reply_body"] == (reply or expected["reply_body"])


LONG_DESCRIPTION = ("Reserve 1,500 by 2027-02-14 for Q4 and two payments. " * 40)[:2000]
LONG_TITLE = ("Statement 2,500 for FY2026 and three months " * 5)[:200]


def _long_ids():
    return documents(20, id_length=64)


def _long_titles():
    return documents(20, title=LONG_TITLE)


def _full_resolution(ids):
    joined = ", ".join(ids)
    return f"Review the cited verified documents ({joined}) and clear condition UC-7 if they satisfy it."


def _full_reply(docs):
    lines = "".join(f"- {d['title']} [doc:{d['document_id']}]\n" for d in docs)
    return (
        "Regarding condition UC-7, raised 2027-05-06.\n\nThe verified file holds these "
        "documents in support:\n"
        + lines
        + "\nWe ask that the condition be reviewed for "
        "clearance on the basis of these documents."
    )


CUT_CASES = [
    pytest.param(
        lambda: make(CT, {"condition_requests.0.description": LONG_DESCRIPTION}),
        "issue_summary",
        lambda: "Condition UC-7, raised 2027-05-06: " + LONG_DESCRIPTION,
        id="issue-summary",
    ),
    pytest.param(
        lambda: citing(_long_ids()),
        "proposed_resolution",
        lambda: _full_resolution([d["document_id"] for d in _long_ids()]),
        id="proposed-resolution",
    ),
    pytest.param(
        lambda: citing(_long_titles()),
        "reply_body",
        lambda: _full_reply(_long_titles()),
        id="reply-body",
    ),
]


@pytest.mark.parametrize(("build", "field", "full"), CUT_CASES)
def test_template_values_are_cut_at_their_caps(run, build, field, full):
    text = drafts_of(run(build()))["UC-7"][field]
    whole = full()
    cap = CAPS[field]
    assert len(whole) > cap and len(text) <= cap and text.endswith("...")
    kept = len(text) - 3
    assert whole[:kept] == text[:-3] and whole[kept] == " "
    assert " " not in whole[kept + 1 : cap - 2]


@pytest.mark.parametrize(
    ("text", "cap", "expected"),
    [
        pytest.param("abc", 5, "abc", id="shorter-than-cap"),
        pytest.param("abcde", 5, "abcde", id="equal-to-cap"),
        pytest.param("ab cdef", 5, "ab...", id="cut-before-a-space"),
        pytest.param("a  bcdefg", 6, "a ...", id="prefix-ending-in-a-space"),
        pytest.param("abcdefgh", 6, "abc...", id="hard-cut"),
        pytest.param(
            "Days " * 130, 600, ("Days " * 118) + "Days...", id="run-summary-cap"
        ),
    ],
)
def test_cut_rule(module, text, cap, expected):
    assert module._cut(text, cap) == expected


def test_hard_cut_when_no_space_precedes_the_cap(module):
    value = "x" * 700 + " tail"
    assert module._cut(value, 600) == "x" * 597 + "..."
    assert len(module._cut(value, 600)) == 600


def test_long_composed_reply_body_is_cut_and_completes(run):
    output = run(citing(_long_titles()))
    draft = drafts_of(output)["UC-7"]
    assert output["status"] == "completed"
    assert len(draft["reply_body"]) <= 4000 and draft["reply_body"].endswith("...")
    assert len(draft["cited_document_ids"]) == 20
    missing = [
        i
        for i in draft["cited_document_ids"]
        if f"[doc:{i}]" not in draft["reply_body"]
    ]
    assert missing


def test_copied_marker_text_reaches_composed_drafts_unchanged(run):
    changes = {
        "condition_requests.1.description": "Clear the lien noted at [doc: and in [doc:X].",
        "verified_file.1.title": "Title report [doc:X]",
    }
    output = run(make(CT, changes))
    draft = drafts_of(output)["UC-8"]
    assert output["status"] == "completed"
    assert draft["issue_summary"].endswith(
        "Clear the lien noted at [doc: and in [doc:X]."
    )
    assert "- Title report [doc:X] [doc:DOC-T]\n" in draft["reply_body"]


# --------------------------------------------------------------------------
# Citations and the figure rule
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "marker",
    [
        pytest.param(" [doc:", id="unclosed-at-end"),
        pytest.param(" [doc:]", id="empty-id"),
        pytest.param(" [doc:A B]", id="space-in-id"),
        pytest.param(" [doc:" + "A" * 65 + "]", id="id-too-long"),
        pytest.param(" [doc:DOC-A extra", id="unclosed-before-text"),
    ],
)
def test_malformed_markers_are_rejected(run, marker):
    path = "drafts[0].reply_body"
    changes = {"drafts.0.reply_body": REPLY + marker}
    _rejects(run, CC, changes, "MALFORMED_CITATION", "malformed_marker", path)


UNSUPPORTED = [
    ("full-width-digit", "Reserves of ２ payments.", "non_ascii_digit"),
    ("arabic-indic-digit", "Reserves of ٣ payments.", "non_ascii_digit"),
    ("number", "Reserves of 9 payments.", "unsupported_number"),
    ("date", "Paid on 2027-02-14.", "unsupported_date"),
    ("token-5M", "A balance of 5M.", "unsupported_token"),
    ("token-3x", "Cover of 3x.", "unsupported_token"),
    ("token-units", "Holds 48200units.", "unsupported_token"),
    ("token-FY2027", "Taxes for FY2027.", "unsupported_token"),
    ("token-8e3", "About 8e3 left.", "unsupported_token"),
    ("number-word", "Reserves of three payments.", "unsupported_number_word"),
]


@pytest.mark.parametrize(
    ("field", "text", "reason"),
    [
        pytest.param(field, text, reason, id=f"{field}-{label}")
        for field in FIELDS
        for label, text, reason in UNSUPPORTED
    ],
)
def test_figure_rule_rejects_each_unsupported_form(run, field, text, reason):
    if field == "reply_body":
        text = text + " [doc:DOC-A]"
    _rejects(run, CC, {f"drafts.0.{field}": text}, FIG, reason, f"drafts[0].{field}")


ADMITTED = [
    ("description-number-word", "Reserves of TWO payments."),
    ("requested-on", "Raised on 2027-02-25."),
    ("run-date", "As of 2027-03-01."),
    ("closing-date", "Closing on 2027-03-13."),
    ("days-to-closing", "Closing in 12 days."),
    ("days-open", "Open for 4 days."),
    ("lead-window", "Window of 7 days."),
    ("prior-replies", "After 1 reply."),
    ("title-token", "Statement for Q4."),
    ("summary-number", "Balance of 1,500."),
    ("summary-date", "Balance on 2027-02-14."),
    ("summary-token", "Cover of 3x."),
    ("condition-id", "Unlike UC-2, this is open."),
    ("document-id", "See DOC-A."),
]


@pytest.mark.parametrize(
    ("field", "text"),
    [
        pytest.param(field, text, id=f"{field}-{label}")
        for field in FIELDS
        for label, text in ADMITTED
    ],
)
def test_figure_rule_accepts_each_admitted_source(run, field, text):
    if field == "reply_body":
        text = text + " [doc:DOC-A]"
    output = run(make(CC, {**EVIDENCE, f"drafts.0.{field}": text}))
    assert output["status"] == "completed", output.get("error")
    assert output["drafts"][0][field] == text


def test_figure_in_an_uncited_document_is_not_admitted(run):
    slip = document("DOC-B", "Pay slip 2,750")
    changes = {"verified_file.+": slip, "drafts.0.issue_summary": "Income of 2750."}
    _rejects(run, CC, changes, FIG, "unsupported_number", "drafts[0].issue_summary")
    changes["drafts.0.cited_document_ids.+"] = "DOC-B"
    changes["drafts.0.reply_body"] = REPLY + " [doc:DOC-B]"
    assert run(make(CC, changes))["status"] == "completed"


@pytest.mark.parametrize(
    ("figure", "supported"),
    [
        pytest.param("1,500", True, id="grouped"),
        pytest.param("1500.00", True, id="trailing-zeros"),
        pytest.param("01500", True, id="leading-zero"),
        pytest.param("-1500", True, id="sign-is-not-part"),
        pytest.param("1500.", True, id="point-without-digits"),
        pytest.param("1500.5", False, id="other-fraction"),
        pytest.param("15,00", False, id="bad-grouping"),
    ],
)
def test_equal_number_forms_match(run, figure, supported):
    output = run(
        make(CC, {**EVIDENCE, "drafts.0.issue_summary": f"Balance {figure} now."})
    )
    assert (output["status"] == "completed") is supported, output


def test_figure_rule_reads_drafts_fields_and_words_in_order(run):
    changes = {
        "drafts.0.reply_body": "Cover 9 [doc:DOC-H].",
        "drafts.1.issue_summary": "Lien 8.",
    }
    _rejects(run, CT, changes, FIG, "unsupported_number", "drafts[0].reply_body")
    changes = {
        "drafts.0.issue_summary": "Three items.",
        "drafts.0.reply_body": "Cover 9 [doc:DOC-H].",
    }
    _rejects(
        run, CT, changes, FIG, "unsupported_number_word", "drafts[0].issue_summary"
    )
    changes = {"drafts.0.issue_summary": "Cover 5M from 2026-01-01."}
    _rejects(run, CT, changes, FIG, "unsupported_token", "drafts[0].issue_summary")


def test_figure_check_runs_after_every_citation_check(run):
    changes = {
        "drafts.0.issue_summary": "Reserves of 9 payments.",
        "drafts.0.reply_body": "No marker.",
    }
    reason = "cited_without_marker"
    _rejects(run, CC, changes, "CITATION_MISMATCH", reason, "drafts[0].reply_body")


@pytest.mark.parametrize(
    ("word", "supported"),
    [
        pytest.param("TWO", True, id="upper-case"),
        pytest.param("twO", True, id="mixed-case"),
        pytest.param("ｔｗｏ", True, id="full-width-letters-are-no-word"),
        pytest.param("TWΟ", True, id="greek-letter-ends-the-word"),
        pytest.param("THREE", False, id="unsupported-word"),
    ],
)
def test_number_words_fold_ascii_letters_only(run, word, supported):
    output = run(make(CC, {"drafts.0.issue_summary": f"Reserves of {word} payments."}))
    assert (output["status"] == "completed") is supported, output


MARK = "ZQX-planted-text"


def test_document_summaries_never_reach_any_output(run):
    for base in (TR, SL, CT, CC):
        params = make(base)
        for doc in params["verified_file"]:
            doc["summary"] = MARK + " 1,250"
        output = run(params)
        assert output["status"] == "completed"
        assert MARK not in canonical(output)


def test_document_summary_supports_a_figure_of_a_citing_draft(run):
    changes = {
        "verified_file.1.summary": "Premium 1,200.",
        "drafts.1.issue_summary": "Premium 1200.",
    }
    assert run(make(CT, changes))["status"] == "completed"
    changes["drafts.0.issue_summary"] = "Premium 1200."
    _rejects(run, CT, changes, FIG, "unsupported_number", "drafts[0].issue_summary")


# A document summary that admits the parts of every run below, but no whole run.
PARTS = (
    "Parts 5, 0, 1, 3, 6, 12, 50 and 1,000 and the tokens v6, FY2027, 0000abc, "
    "Q4, x5, 5M and 5x appear here."
)
SPANS = ["5,0000", "1,0000", "6.0.0", "12.50.00"]
NUMBER_RUNS = (
    SPANS
    + [f"x-{span}" for span in SPANS]
    + [f"{span}-x" for span in SPANS]
    + ["approx.5,0000", "Rs.5,00,000", "No.1,0000", "ref.6.0.0", "ca.12.50.00"]
    + ["abc,5,0000", "5,0000.abc", "5,,000", "5.,000", "5-,000", "12.,50"]
    + ["1,000,,000", "x-.,-y", "5.-6", "5,-6"]
)
TOKEN_RUNS = ["Q4.5,0000", "v6.0.0", "FY2027,000", "FY2027.5", "5,0000abc", "Q4,5"]
TOKEN_RUNS += ["x5,0000", "1.5M", "3.5x", "Q4.5"]
SUMMARY_0, ISSUE_0 = "verified_file.0.summary", "drafts.0.issue_summary"
ISSUE_PATH = "drafts[0].issue_summary"


def quoted(text):
    return f"The figure {text} is quoted."


@pytest.mark.parametrize(
    ("text", "reason"),
    [pytest.param(text, "unsupported_number", id=text) for text in NUMBER_RUNS]
    + [pytest.param(text, "unsupported_token", id=text) for text in TOKEN_RUNS],
)
def test_a_run_is_never_read_as_its_admitted_parts(run, text, reason):
    """A run of digits, commas and points is one figure, also after a letter or
    a hyphen and with doubled separators; so is a run joined digit to digit to a
    token that holds a letter."""
    changes = {SUMMARY_0: PARTS, ISSUE_0: quoted(text)}
    _rejects(run, CC, changes, FIG, reason, ISSUE_PATH)


COPIES = ["1.2.0", "2025,2026", "approx.5,0000", "5,,000", "v6.0.0", "FY2027,000"]
COPIES += ["abc.v6.0.0", "x-.,-y", "5.-6", "5,-6", "1.5M", "3.5x", "Q4.5", "12345,678"]


@pytest.mark.parametrize("text", COPIES)
def test_a_run_copied_whole_from_an_admitted_text_passes(run, text):
    changes = {SUMMARY_0: f"The source holds {text} as written.", ISSUE_0: quoted(text)}
    output = run(make(CC, changes))
    assert output["status"] == "completed", output.get("error")
    assert output["drafts"][0]["issue_summary"] == quoted(text)


CHAIN = "Release v6.0.0 is planned."
TOTAL = "The total is 12345678."
# fmt: off
READINGS = [
    pytest.param("5.2.0", PARTS + " 15.2.0", "unsupported_number", id="inside-a-longer-run"),
    pytest.param("5,0000", PARTS + " 5,00000", "unsupported_number", id="inside-a-longer-grouped-run"),
    pytest.param("6.0.0", PARTS + " v6.0.0", "unsupported_number", id="inside-an-admitted-chain"),
    pytest.param(".5", PARTS, "unsupported_number", id="leading-point-is-not-5"),
    pytest.param(".5", "The rate was 0.5 before.", None, id="leading-point-is-0.5"),
    pytest.param("approx.5", PARTS, None, id="approx-5-reads-as-5"),
    pytest.param("approx.5", "No figure here.", "unsupported_number", id="approx-5-needs-5"),
    pytest.param("No.1,500", "The cap is 1,500.", None, id="no-1500-reads-as-1500"),
    pytest.param("No.1,500", "Parts 1 and 500.", "unsupported_number", id="no-1500-is-not-1-and-500"),
    pytest.param("1,500-2,000", "From 1,500 to 2,000.", None, id="range-reads-as-two-numbers"),
    pytest.param("1,500-2,000", "Parts 1, 500, 2 and 0.", "unsupported_number", id="range-is-not-its-groups"),
    pytest.param("Q1,Q2", "Quarters Q1 and Q2.", None, id="q1-q2-reads-as-two-tokens"),
    pytest.param("Q1,Q2", "Quarter Q1 only.", "unsupported_token", id="q1-q2-needs-q2"),
    pytest.param("v6", CHAIN, None, id="token-inside-an-admitted-chain"),
    pytest.param("0", CHAIN, "unsupported_number", id="no-number-from-an-admitted-chain"),
    pytest.param("6.0.0", CHAIN, "unsupported_number", id="no-run-from-an-admitted-chain"),
    pytest.param("12345678", TOTAL, None, id="the-value-itself"),
    pytest.param("12345,678", TOTAL, "unsupported_number", id="long-first-group-is-not-the-value"),
]
# fmt: on


@pytest.mark.parametrize(("text", "summary", "reason"), READINGS)
def test_runs_points_and_tokens_read_as_stated(run, text, summary, reason):
    output = run(make(CC, {SUMMARY_0: summary, ISSUE_0: quoted(text)}))
    if reason is None:
        assert output["status"] == "completed", output.get("error")
    else:
        assert error_of(output) == (FIG, {"reason": reason, "path": ISSUE_PATH})


RUNS_TEXT = (
    "Runs 1.2.0, 2025,2026, approx.5,0000, 5,,000, v6.0.0, abc.v6.0.0 and 5.-6 stand."
)


def _hyphen_ids():
    """Twenty documents whose 63-character ids are runs joined by single hyphens."""
    return [document("D%02d" % n + "-x1" * 20) for n in range(20)]


HYPHEN_IDS = {
    "condition_requests.0.condition_id": "UC-7-a_1",
    "drafts.0.condition_id": "UC-7-a_1",
    "verified_file.0.document_id": "7-7",
    "drafts.0.cited_document_ids": ["7-7"],
}


INVARIANT_CASES = [
    pytest.param(lambda: make(CT), id="template-example"),
    pytest.param(
        lambda: make(
            CC, {"drafts.0": {"condition_id": "UC-1", "cited_document_ids": ["DOC-A"]}}
        ),
        id="caller-example-without-text",
    ),
    pytest.param(
        lambda: make(CT, {"condition_requests.0.description": LONG_DESCRIPTION}),
        id="issue-summary-cap",
    ),
    pytest.param(lambda: citing(_long_ids()), id="proposed-resolution-cap"),
    pytest.param(lambda: citing(_long_titles()), id="reply-body-cap"),
    pytest.param(
        lambda: make(CT, {"drafts.0.issue_summary": "Cover is shown."}), id="mixed"
    ),
    pytest.param(
        lambda: make(CT, {"condition_requests.0.description": RUNS_TEXT}),
        id="description-with-runs",
    ),
    pytest.param(
        lambda: make(CT, {"verified_file.0.title": RUNS_TEXT}), id="title-with-runs"
    ),
    pytest.param(
        lambda: make(
            CT, {"condition_requests.0.description": ((RUNS_TEXT + " ") * 25)[:2000]}
        ),
        id="issue-summary-cap-with-runs",
    ),
    pytest.param(
        lambda: citing(documents(20, title=((RUNS_TEXT + " ") * 3)[:200])),
        id="reply-body-cap-with-runs",
    ),
    pytest.param(lambda: make(CT, HYPHEN_IDS), id="ids-joined-by-single-hyphens"),
    pytest.param(
        lambda: citing(_hyphen_ids()), id="proposed-resolution-cap-hyphen-ids"
    ),
]


@pytest.mark.parametrize("build", INVARIANT_CASES)
def test_every_composed_draft_passes_the_figure_rule(module, run, build):
    params = build()
    output = run(params)
    assert output["status"] == "completed", output.get("error")
    request = module._validate(params)
    composed = drafts_of(output)
    for draft in request.drafts:
        supplied = params["drafts"][draft.position]
        for field in FIELDS:
            text = composed[draft.condition_id][field]
            assert len(text) <= CAPS[field]
            if field not in supplied:
                assert module._figure_reason(request, draft, text) is None, (
                    field,
                    text,
                )


ID_FIELDS = [
    ("document-id", "verified_file.0.document_id", "verified_file[0].document_id"),
    (
        "condition-id",
        "condition_requests.0.condition_id",
        "condition_requests[0].condition_id",
    ),
]
BAD_IDS = [
    ("leading-hyphen", "-UC7"),
    ("trailing-hyphen", "UC7-"),
    ("double-hyphen", "UC--7"),
    ("trailing-line-feed", "UC-7\n"),
]


@pytest.mark.parametrize(
    ("location", "value", "path"),
    [
        pytest.param(location, value, path, id=f"{field}-{label}")
        for field, location, path in ID_FIELDS
        for label, value in BAD_IDS
    ],
)
def test_ids_are_whole_runs_joined_by_single_hyphens(run, location, value, path):
    _rejects(run, TR, {location: value}, IR, "invalid_value", path)


def test_an_id_form_fails_at_the_shape_check_before_the_dates(run):
    changes = {"condition_requests.0.condition_id": "UC--1", "as_of_date": "2027-02-30"}
    _rejects(
        run, TR, changes, IR, "invalid_value", "condition_requests[0].condition_id"
    )


@pytest.mark.parametrize(
    ("location", "text", "path"),
    [
        pytest.param(
            "condition_requests.0.description",
            "Pay ٣ months.",
            "condition_requests[0].description",
            id="description-arabic-indic",
        ),
        pytest.param(
            "condition_requests.0.description",
            "Pay १ month.",
            "condition_requests[0].description",
            id="description-devanagari",
        ),
        pytest.param(
            "verified_file.0.title",
            "Statement ２",
            "verified_file[0].title",
            id="title-full-width",
        ),
    ],
)
def test_copied_texts_reject_other_decimal_digits(run, location, text, path):
    _rejects(run, TR, {location: text}, IR, "invalid_value", path)


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("Bank statement", id="no-break-space"),
        pytest.param("Bank statement", id="line-separator"),
        pytest.param("Bank　statement", id="wide-space"),
    ],
)
def test_unicode_spaces_and_separators_are_ordinary_text(run, text):
    plain = drafts_of(run(make(CT)))["UC-7"]["reply_body"]
    output = run(make(CT, {"verified_file.0.title": text}))
    body = drafts_of(output)["UC-7"]["reply_body"]
    assert f"- {text} [doc:DOC-H]" in body
    assert len(body.split("\n")) == len(plain.split("\n"))


# --------------------------------------------------------------------------
# The contract errors and their order
# --------------------------------------------------------------------------


def case(name, base, changes, code, reason, path, **extra):
    return pytest.param(
        base, changes, code, {"reason": reason, "path": path, **extra}, id=name
    )


# fmt: off
ERROR_CASES = [
    case("request-not-an-object", lambda: [make(TR)], {}, IR, "parameters_not_object", ""),
    case("request-not-an-object-with-an-action", lambda: [{"action": "send"}], {}, IR, "parameters_not_object", ""),
    case("unknown-action", TR, {"action": "send"}, IR, "unknown_action", "action"),
    case("unknown-key", TR, {"extra": 1}, IR, "unknown_key", ""),
    case("missing-field", TR, {"as_of_date": DROP}, IR, "missing_field", "as_of_date"),
    case("invalid-type", TR, {"escalation_lead_days": "7"}, IR, "invalid_type", "escalation_lead_days"),
    case("invalid-value", TR, {"verified_file.0.document_id": "DOC A"}, IR, "invalid_value",
         "verified_file[0].document_id"),
    case("out-of-range", TR, {"escalation_lead_days": 400}, IR, "out_of_range", "escalation_lead_days"),
    case("size-cap", TR, {"verified_file": documents(201)}, IR, "size_cap", "verified_file", limit=200, actual=201),
    case("drafts-with-track", TR, {"drafts": CC_DRAFTS}, IR, "field_not_allowed", "drafts"),
    case("impossible-date", TR, {"as_of_date": "2027-02-30"}, "INVALID_DATE", "invalid_date", "as_of_date"),
    case("empty-verified-file", TR, {"verified_file": []}, "EMPTY_VERIFIED_FILE", "empty_verified_file",
         "verified_file"),
    case("deadline-before-run-date", TR, {"closing_deadline": "2027-02-27"}, "DEADLINE_BEFORE_AS_OF",
         "deadline_before_as_of", "closing_deadline"),
    case("request-after-run-date", TR, {"condition_requests.0.requested_on": "2027-03-02"}, "REQUEST_AFTER_AS_OF",
         "request_after_as_of", "condition_requests[0].requested_on"),
    case("repeated-document", TR, {"verified_file.+": DOC_A}, DUP, "duplicate_id", "verified_file[1].document_id"),
    case("repeated-condition", TR, {"condition_requests.1.condition_id": "UC-1"}, DUP, "duplicate_id",
         "condition_requests[1].condition_id"),
    case("repeated-draft", CT, {"drafts.1.condition_id": "UC-7"}, DUP, "duplicate_id", "drafts[1].condition_id"),
    case("repeated-cited-document", CC, {"drafts.0.cited_document_ids.+": "DOC-A"}, DUP, "duplicate_id",
         "drafts[0].cited_document_ids[1]"),
    case("no-open-condition", SL, ALL_RESOLVED, "NO_OPEN_CONDITION", "no_open_condition", "condition_requests"),
    case("unknown-condition", CT, {"drafts.0.condition_id": "UC-99"}, "UNKNOWN_CONDITION", "unknown_condition",
         "drafts[0].condition_id"),
    case("draft-for-waived-condition", CT, {"drafts.+": {"condition_id": "UC-9", "cited_document_ids": ["DOC-T"]}},
         "DRAFT_FOR_CLOSED_CONDITION", "condition_not_open", "drafts[2].condition_id"),
    case("draft-missing", CT, {"drafts.1": DROP}, "DRAFT_MISSING", "draft_missing", "condition_requests[1]"),
    case("compile-without-drafts", CT, {"drafts": DROP}, "DRAFT_MISSING", "draft_missing", "condition_requests[0]"),
    case("cited-document-unknown", CC, {"drafts.0.cited_document_ids": ["DOC-Z"]}, "UNKNOWN_DOCUMENT",
         "unknown_document", "drafts[0].cited_document_ids[0]"),
    case("marker-document-unknown", CC, {"drafts.0.issue_summary": "See [doc:DOC-Z]."}, "UNKNOWN_DOCUMENT",
         "unknown_document", "drafts[0].issue_summary"),
    case("marker-malformed", CC, {"drafts.0.reply_body": REPLY + " [doc:"}, "MALFORMED_CITATION",
         "malformed_marker", "drafts[0].reply_body"),
    case("marker-not-cited", CT, {"drafts.0.issue_summary": "Cover is shown [doc:DOC-T]."}, "CITATION_MISMATCH",
         "marker_not_cited", "drafts[0].issue_summary"),
    case("cited-without-marker", CC, {"drafts.0.reply_body": "The statement shows the balance."},
         "CITATION_MISMATCH", "cited_without_marker", "drafts[0].reply_body"),
    case("figure-non-ascii-digit", CC, {"drafts.0.issue_summary": "Reserves of ２ payments."}, FIG,
         "non_ascii_digit", "drafts[0].issue_summary"),
    case("figure-date", CC, {"drafts.0.issue_summary": "Paid on 2027-02-14."}, FIG, "unsupported_date",
         "drafts[0].issue_summary"),
    case("figure-number", CC, {"drafts.0.issue_summary": "Reserves of 9 payments."}, FIG, "unsupported_number",
         "drafts[0].issue_summary"),
    case("figure-token", CC, {"drafts.0.issue_summary": "A balance of 5M."}, FIG, "unsupported_token",
         "drafts[0].issue_summary"),
    case("figure-number-word", CC, {"drafts.0.issue_summary": "Reserves of three payments."}, FIG,
         "unsupported_number_word", "drafts[0].issue_summary"),
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


def conditions(count):
    return [condition("C%d" % n) for n in range(count)]


# Each entry violates two checks; the earlier check of the documented order
# wins. The steps: 1 object, 2 action, 3 unknown top-level keys, 4 required
# fields and top-level types, 5 empty verified file, 6 size caps, 7 item
# shapes, 8 real dates, 9 repeated document and condition ids, 10 date order,
# 11 drafts allowed, 12 an open condition, 13 each draft's condition,
# 14 a draft per open condition, 15 citations, 16 the figure rule.
# fmt: off
ORDER_CASES = [
    order("action-before-keys", (2, 3), TR, {"action": "send", "extra": 1}, IR, "unknown_action", "action"),
    order("keys-before-required", (3, 4), TR, {"extra": 1, "as_of_date": DROP}, IR, "unknown_key", ""),
    order("required-before-empty-file", (4, 5), TR, {"as_of_date": DROP, "verified_file": []}, IR,
          "missing_field", "as_of_date"),
    order("empty-file-before-caps", (5, 6), TR, {"verified_file": [], "condition_requests": conditions(101)},
          "EMPTY_VERIFIED_FILE", "empty_verified_file", "verified_file"),
    order("caps-before-shapes", (6, 7), TR, {"verified_file": documents(201), "escalation_lead_days": 400}, IR,
          "size_cap", "verified_file", limit=200, actual=201),
    order("shapes-before-dates", (7, 8), TR, {"escalation_lead_days": 400, "as_of_date": "2027-02-30"}, IR,
          "out_of_range", "escalation_lead_days"),
    order("dates-before-duplicates", (8, 9), TR, {"as_of_date": "2027-02-30", "verified_file.+": DOC_A},
          "INVALID_DATE", "invalid_date", "as_of_date"),
    order("duplicates-before-date-order", (9, 10), TR, {"verified_file.+": DOC_A, "closing_deadline": "2027-02-27"},
          DUP, "duplicate_id", "verified_file[1].document_id"),
    order("date-order-before-drafts-allowed", (10, 11), TR, {"closing_deadline": "2027-02-27", "drafts": CC_DRAFTS},
          "DEADLINE_BEFORE_AS_OF", "deadline_before_as_of", "closing_deadline"),
    order("drafts-allowed-before-open-condition", (11, 12), SL, {"drafts": CT_DRAFTS, **ALL_RESOLVED}, IR,
          "field_not_allowed", "drafts"),
    order("open-condition-before-draft-conditions", (12, 13), CT, {**ALL_RESOLVED, "drafts.0.condition_id": "UC-99"},
          "NO_OPEN_CONDITION", "no_open_condition", "condition_requests"),
    order("draft-conditions-before-missing-drafts", (13, 14), CT, {"drafts.0.condition_id": "UC-99"},
          "UNKNOWN_CONDITION", "unknown_condition", "drafts[0].condition_id"),
    order("missing-drafts-before-citations", (14, 15), CT, {"drafts.1": DROP, "drafts.0.cited_document_ids": ["DOC-Z"]},
          "DRAFT_MISSING", "draft_missing", "condition_requests[1]"),
    order("citations-before-figures", (15, 16), CC, {"drafts.0.issue_summary": "Reserves of 9 [doc:DOC-Z]."},
          "UNKNOWN_DOCUMENT", "unknown_document", "drafts[0].issue_summary"),
    order("deadline-before-request-dates", (10, 10), TR,
          {"closing_deadline": "2027-02-27", "condition_requests.0.requested_on": "2027-03-02"},
          "DEADLINE_BEFORE_AS_OF", "deadline_before_as_of", "closing_deadline"),
    order("closed-draft-before-later-unknown", (13, 13), CT,
          {"drafts.0.condition_id": "UC-9", "drafts.1.condition_id": "UC-99"},
          "DRAFT_FOR_CLOSED_CONDITION", "condition_not_open", "drafts[0].condition_id"),
    order("unknown-and-closed-before-repeat", (13, 13), CT,
          {"drafts.+": {"condition_id": "UC-7", "cited_document_ids": ["DOC-H"]}, "drafts.1.condition_id": "UC-9"},
          "DRAFT_FOR_CLOSED_CONDITION", "condition_not_open", "drafts[1].condition_id"),
    order("repeated-cited-before-unknown-cited", (15, 15), CT,
          {"drafts.0.cited_document_ids": ["DOC-Z", "DOC-H", "DOC-H"]},
          DUP, "duplicate_id", "drafts[0].cited_document_ids[2]"),
    order("malformed-pass-before-known-pass", (15, 15), CC,
          {"drafts.0.issue_summary": "See [doc:DOC-Z].", "drafts.0.reply_body": REPLY + " [doc:"},
          "MALFORMED_CITATION", "malformed_marker", "drafts[0].reply_body"),
    order("known-pass-before-cited-pass", (15, 15), CT,
          {"drafts.0.issue_summary": "See [doc:DOC-T].", "drafts.0.reply_body": "See [doc:DOC-H] and [doc:DOC-Z]."},
          "UNKNOWN_DOCUMENT", "unknown_document", "drafts[0].reply_body"),
    order("cited-pass-before-marked-pass", (15, 15), CT,
          {"drafts.0.issue_summary": "See [doc:DOC-T].", "drafts.0.reply_body": "No marker here."},
          "CITATION_MISMATCH", "marker_not_cited", "drafts[0].issue_summary"),
]
# fmt: on


# Adjacent checks that one request cannot fail together have no pair entry;
# each of the two checks has its own case in the error table.
UNPRODUCIBLE_PAIRS = {(1, 2): "a request that is not an object has no action to check"}


def test_pairwise_table_covers_every_adjacent_pair():
    pairs = {p.values[0] for p in ORDER_CASES if p.values[0][1] == p.values[0][0] + 1}
    every_pair = {(step, step + 1) for step in range(1, 16)}
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


PATH_SHAPE = re.compile("[a-z_]+(\\[[0-9]+\\])*(\\.[a-z_]+(\\[[0-9]+\\])*)*", re.ASCII)
# fmt: off
ECHO_CASES = [
    (TR, {MARK: 1}),
    (TR, {"verified_file.0." + MARK: 1}),
    (TR, {"verified_file.0.title": MARK + "\x00"}),
    (TR, {"verified_file.0.title": MARK * 20}),
    (TR, {"verified_file.0.summary": MARK, "closing_deadline": "2027-02-27"}),
    (TR, {"verified_file.0.document_type": MARK}),
    (TR, {"condition_requests.0.description": MARK + "\x01"}),
    (TR, {"condition_requests.0.status": MARK}),
    (CT, {"drafts.0.condition_id": MARK}),
    (CT, {"drafts.0.cited_document_ids": [MARK]}),
    (CC, {"drafts.0.issue_summary": MARK + " 9"}),
    (CC, {"drafts.0.reply_body": MARK + " [doc:" + MARK + "]"}),
    (CC, {"drafts.0.proposed_resolution": MARK + " [doc:"}),
]
# fmt: on


def test_error_details_never_echo_input_text(run):
    outputs = [run(make(*request_)) for request_ in ECHO_CASES]
    outputs += [run(make(*request_)) for request_ in ERROR_REQUESTS.values()]
    for output in outputs:
        _, detail = error_of(output)
        assert MARK not in canonical(output)
        assert list(detail)[:2] == ["reason", "path"]
        assert set(detail) <= {"reason", "path", "limit", "actual"}
        assert detail["path"] == "" or PATH_SHAPE.fullmatch(detail["path"])


def test_internal_fault_returns_only_the_fault_envelope(module, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("planted fault text")

    monkeypatch.setattr(module, "_tracker", broken)
    output = module.UnderwritingConditionReplyTrackerSkill().execute(make(TR))
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
    pytest.param(TR, {"extra": 1}, "", id="top-level"),
    pytest.param(TR, {"verified_file.0.extra": 1}, "verified_file[0]", id="document"),
    pytest.param(TR, {"condition_requests.0.extra": 1}, "condition_requests[0]", id="condition"),
    pytest.param(CC, {"drafts.0.extra": 1}, "drafts[0]", id="draft"),
]
TYPE_CASES = [
    pytest.param(TR, "escalation_lead_days", True, "escalation_lead_days", id="lead-boolean"),
    pytest.param(TR, "escalation_lead_days", 7.0, "escalation_lead_days", id="lead-fraction"),
    pytest.param(TR, "as_of_date", 20270301, "as_of_date", id="date-number"),
    pytest.param(TR, "verified_file", "DOC-A", "verified_file", id="file-string"),
    pytest.param(TR, "verified_file.0", "DOC-A", "verified_file[0]", id="document-string"),
    pytest.param(TR, "verified_file.0.document_id", 7, "verified_file[0].document_id", id="document-id-number"),
    pytest.param(TR, "verified_file.0.title", 7, "verified_file[0].title", id="title-number"),
    pytest.param(TR, "verified_file.0.document_type", 7, "verified_file[0].document_type", id="type-number"),
    pytest.param(TR, "verified_file.0.summary", 7, "verified_file[0].summary", id="summary-number"),
    pytest.param(TR, "condition_requests.0.requested_on", 20270225, "condition_requests[0].requested_on",
                 id="requested-on-number"),
    pytest.param(TR, "condition_requests.0.status", 7, "condition_requests[0].status", id="status-number"),
    pytest.param(TR, "condition_requests.0.prior_reply_count", True, "condition_requests[0].prior_reply_count",
                 id="reply-count-boolean"),
    pytest.param(CC, "drafts", {}, "drafts", id="drafts-object"),
    pytest.param(CC, "drafts.0", "UC-1", "drafts[0]", id="draft-string"),
    pytest.param(CC, "drafts.0.cited_document_ids", "DOC-A", "drafts[0].cited_document_ids", id="cited-string"),
    pytest.param(CC, "drafts.0.cited_document_ids.0", 7, "drafts[0].cited_document_ids[0]", id="cited-number"),
    pytest.param(CC, "drafts.0.reply_body", 7, "drafts[0].reply_body", id="reply-number"),
]
TEXT_FIELDS = [
    ("title", TR, "verified_file.0.title", "verified_file[0].title"),
    ("summary", TR, "verified_file.0.summary", "verified_file[0].summary"),
    ("description", TR, "condition_requests.0.description", "condition_requests[0].description"),
    ("draft-condition", CC, "drafts.0.condition_id", "drafts[0].condition_id"),
    ("cited-id", CC, "drafts.0.cited_document_ids.0", "drafts[0].cited_document_ids[0]"),
    ("issue-summary", CC, "drafts.0.issue_summary", "drafts[0].issue_summary"),
    ("proposed-resolution", CC, "drafts.0.proposed_resolution", "drafts[0].proposed_resolution"),
    ("reply-body", CC, "drafts.0.reply_body", "drafts[0].reply_body"),
]
BAD_TEXTS = [
    ("blank", "   "),
    ("spaces-and-line-feeds", " \n \n"),
    ("next-line-control", "Rate\u0085path"),
    ("null-control", "Rate\x00path"),
    ("tab-control", "Rate\tpath"),
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
    _rejects(run, base, changes, IR, "unknown_key", path)


@pytest.mark.parametrize(("base", "location", "value", "path"), TYPE_CASES)
def test_type_rules_are_strict(run, base, location, value, path):
    _rejects(run, base, {location: value}, IR, "invalid_type", path)


@pytest.mark.parametrize(("base", "location", "text", "path"), TEXT_CASES)
def test_text_field_rules(run, base, location, text, path):
    _rejects(run, base, {location: text}, IR, "invalid_value", path)


SURROGATE = "\ud800"
# fmt: off
SURROGATE_CASES = [
    pytest.param(TR, "action", "track", IR, "unknown_action", "action", id="action"),
    pytest.param(TR, "as_of_date", "2027-03-01", "INVALID_DATE", "invalid_date", "as_of_date", id="run-date"),
    pytest.param(TR, "closing_deadline", "2027-03-13", "INVALID_DATE", "invalid_date", "closing_deadline",
                 id="closing-date"),
    pytest.param(TR, "verified_file.0.document_id", "DOC-A", IR, "invalid_value", "verified_file[0].document_id",
                 id="document-id"),
    pytest.param(TR, "verified_file.0.title", "Bank", IR, "invalid_value", "verified_file[0].title", id="title"),
    pytest.param(TR, "verified_file.0.document_type", "other", IR, "invalid_value",
                 "verified_file[0].document_type", id="document-type"),
    pytest.param(TR, "verified_file.0.summary", "Balance", IR, "invalid_value", "verified_file[0].summary",
                 id="summary"),
    pytest.param(TR, "condition_requests.0.condition_id", "UC-1", IR, "invalid_value",
                 "condition_requests[0].condition_id", id="condition-id"),
    pytest.param(TR, "condition_requests.0.requested_on", "2027-02-25", "INVALID_DATE", "invalid_date",
                 "condition_requests[0].requested_on", id="requested-on"),
    pytest.param(TR, "condition_requests.0.description", "Show", IR, "invalid_value",
                 "condition_requests[0].description", id="description"),
    pytest.param(TR, "condition_requests.0.status", "open", IR, "invalid_value", "condition_requests[0].status",
                 id="status"),
    pytest.param(CC, "drafts.0.condition_id", "UC-1", IR, "invalid_value", "drafts[0].condition_id",
                 id="draft-condition"),
    pytest.param(CC, "drafts.0.cited_document_ids.0", "DOC-A", IR, "invalid_value",
                 "drafts[0].cited_document_ids[0]", id="cited-id"),
    pytest.param(CC, "drafts.0.issue_summary", "Reserves", IR, "invalid_value", "drafts[0].issue_summary",
                 id="issue-summary"),
    pytest.param(CC, "drafts.0.proposed_resolution", "Rely", IR, "invalid_value", "drafts[0].proposed_resolution",
                 id="proposed-resolution"),
    pytest.param(CC, "drafts.0.reply_body", "See [doc:DOC-A]", IR, "invalid_value", "drafts[0].reply_body",
                 id="reply-body"),
]
# fmt: on


@pytest.mark.parametrize(
    ("base", "location", "text", "code", "reason", "path"), SURROGATE_CASES
)
def test_lone_surrogates_fail_at_the_first_check_that_reads_the_field(
    run, base, location, text, code, reason, path
):
    """A date fails its date check; any other string fails its own field check."""
    _rejects(run, base, {location: text + SURROGATE}, code, reason, path)


def test_line_feeds_are_allowed_in_text_fields(run):
    changes = {
        "condition_requests.0.description": "Show reserves.\nTwo payments.",
        **EVIDENCE,
    }
    changes["drafts.0.reply_body"] = "Line one.\nBalance 1500 [doc:DOC-A]."
    output = run(make(CC, changes))
    assert output["status"] == "completed", output.get("error")
    assert output["drafts"][0]["reply_body"] == "Line one.\nBalance 1500 [doc:DOC-A]."


def _text(base, location, prefix=""):
    return lambda n: make(base, {location: prefix + "x" * (n - len(prefix))})


# fmt: off
CAP_CASES = [
    ("verified-file", lambda n: make(TR, {"verified_file": documents(n)}), 200, "verified_file"),
    ("conditions", lambda n: make(TR, {"condition_requests": conditions(n)}), 100, "condition_requests"),
    ("drafts", all_open_with_drafts, 100, "drafts"),
    ("cited-ids", lambda n: citing(documents(n)), 20, "drafts[0].cited_document_ids"),
    ("title", _text(TR, "verified_file.0.title"), 200, "verified_file[0].title"),
    ("summary", _text(TR, "verified_file.0.summary"), 1000, "verified_file[0].summary"),
    ("description", _text(TR, "condition_requests.0.description"), 2000, "condition_requests[0].description"),
    ("issue-summary", _text(CC, "drafts.0.issue_summary"), 600, "drafts[0].issue_summary"),
    ("proposed-resolution", _text(CC, "drafts.0.proposed_resolution"), 600, "drafts[0].proposed_resolution"),
    ("reply-body", _text(CC, "drafts.0.reply_body", "[doc:DOC-A] "), 4000, "drafts[0].reply_body"),
]
RANGE_CASES = [
    ("lead-window", lambda n: make(TR, {"escalation_lead_days": n}), 0, 365, "escalation_lead_days"),
    ("prior-replies", lambda n: make(TR, {"condition_requests.0.prior_reply_count": n}), 0, 50,
     "condition_requests[0].prior_reply_count"),
]
SIZE_CASES = (
    [pytest.param(build, cap, None, id=f"{name}-at-cap") for name, build, cap, _ in CAP_CASES]
    + [pytest.param(build, cap + 1, {"reason": "size_cap", "path": path, "limit": cap, "actual": cap + 1},
                    id=f"{name}-over-cap")
       for name, build, cap, path in CAP_CASES]
    + [pytest.param(build, value, None, id=f"{name}-{label}")
       for name, build, low, high, _ in RANGE_CASES for label, value in (("lowest", low), ("highest", high))]
    + [pytest.param(build, value, {"reason": "out_of_range", "path": path}, id=f"{name}-{label}")
       for name, build, low, high, path in RANGE_CASES for label, value in (("below", low - 1), ("above", high + 1))]
    + [pytest.param(lambda n: make(TR, {"condition_requests": []}), 0,
                    {"reason": "out_of_range", "path": "condition_requests"}, id="no-conditions"),
       pytest.param(lambda n: make(CC, {"drafts": []}), 0, {"reason": "out_of_range", "path": "drafts"},
                    id="no-drafts"),
       pytest.param(lambda n: make(CC, {"drafts.0.cited_document_ids": []}), 0,
                    {"reason": "out_of_range", "path": "drafts[0].cited_document_ids"}, id="no-cited-ids")]
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
    pytest.param("as_of_date", "2027-02-29", "as_of_date", id="not-a-leap-year"),
    pytest.param("as_of_date", "2027-3-1", "as_of_date", id="one-digit-month-and-day"),
    pytest.param("as_of_date", "2027-03-01T00:00", "as_of_date", id="with-a-time"),
    pytest.param("as_of_date", "２０２７-03-01", "as_of_date", id="full-width-digits"),
    pytest.param("as_of_date", "2027-03-01\n", "as_of_date", id="trailing-line-feed"),
    pytest.param("closing_deadline", "2027-13-01", "closing_deadline", id="month-thirteen"),
    pytest.param("condition_requests.0.requested_on", "0000-02-25", "condition_requests[0].requested_on",
                 id="year-zero"),
]
# fmt: on


@pytest.mark.parametrize(("location", "value", "path"), DATE_CASES)
def test_dates_are_real_calendar_dates(run, location, value, path):
    _rejects(run, TR, {location: value}, "INVALID_DATE", "invalid_date", path)
    leap = make(TR, {"as_of_date": "2028-02-29", "closing_deadline": "2028-03-01"})
    assert run(leap)["days_until_closing"] == 1


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
    ("t = s.isdigit()", "text-folding"),
    ("lines = s.splitlines()", "text-folding"),
    ("parts = s.split()", "argument-less-strip-or-split"),
    ("t = s.strip()", "argument-less-strip-or-split"),
    ("p = re.compile('[a-z]+')", "pattern-without-ascii"),
    ("m = re.fullmatch('[a-z]+', s)", "pattern-without-ascii"),
    ("p = re.compile('[a-z]+', re.IGNORECASE | re.ASCII)", "case-insensitive-pattern"),
    ("p = re.compile(r'\\w+', re.ASCII)", "unicode-class"),
]
CLEAN_CONTROLS = [
    "p = re.compile('[a-z]+', re.ASCII)",
    "t = s.strip(' \\n')",
    "parts = s.split(',')",
    "n = a // b",
    "d = date(2027, 4, 5).isoformat()",
    "ok = ch.isdecimal() and ch not in '0123456789'",
]


def test_runtime_modules_follow_the_text_number_and_date_rules():
    """No float, no thread decimal context, no fromisoformat and ASCII-only text.

    The one Unicode-dependent test the skill uses, str.isdecimal(), finds decimal
    digits other than 0 to 9 and is allowed.
    """
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
