"""Bundle tests for finance/approved_financial_model_runner.

Every test is offline. The reference fixtures under fixtures/ are synthetic.
Digests, run ids and archive documents are rebuilt here from their published
definitions, not read from the skill. The tests read files inside this bundle
only.
"""

import ast
import copy
import decimal
import hashlib
import json
import os
import re

import jsonschema
import pytest
import yaml

from skill_assay import SkillContext
from skill_assay.core.loader import SkillLoader

BUNDLE_DIR = os.path.dirname(os.path.abspath(__file__))
FIXTURES_DIR = os.path.join(BUNDLE_DIR, "fixtures")
SCHEMAS_DIR = os.path.join(BUNDLE_DIR, "schemas")
REGISTRY_ID = "finance/approved_financial_model_runner"
CARD_TITLE = "Approved Financial Model Runner"
CLASS_NAME = "ApprovedFinancialModelRunnerSkill"
ARCHIVE_SCHEMA = "approved_financial_model_runner.archive/1"

# Expected manifest values, held here so that the test reads no other bundle.
NORM_VERSION = "norms-1.0.0"
SERVED_DWAS = {"onet_release": "31.0", "ids": ["4.A.2.a.4.k.3"]}
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
        "Run the skill's reference fixtures through (a) the baseline agent and (b) "
        "the same agent calling the skill; publish the median tokens per run for "
        "both and the reduction."
    ),
}
PARAMETER_NAMES = (
    "action",
    "model_name",
    "model_version",
    "parameter_revision",
    "input_files",
    "requested_quantities",
    "archive_label",
    "registry",
    "archive_document",
)
REQUIRED_PARAMETERS = [
    "action",
    "model_name",
    "model_version",
    "parameter_revision",
    "input_files",
    "requested_quantities",
    "registry",
]
OUTPUT_KEYS = (
    "status",
    "model_name",
    "model_version",
    "parameter_revision",
    "results",
    "archive_record",
    "verified",
    "run_id",
    "document_sha256",
)

REFERENCE_FIXTURES = (
    "capital_impact_run",
    "revenue_projection_labelled_run",
    "verify_stored_run",
    "failed_upstream_validation",
    "changed_content_old_digest",
    "unregistered_revision",
    "undefined_quantity",
    "verify_changed_document",
    "division_by_zero",
)
RUN_FIXTURES = ("capital_impact_run", "revenue_projection_labelled_run")
CARD_FIXTURE = "capital_impact_run"
# The published identifiers of the two example runs.
EXAMPLE_IDS = {
    "capital_impact_run": (
        "run-0c2f871458836088",
        "267929b6e53433579c1b504856a8445adf65822447debb04d73108baa8c3a166",
        "07e5cd3955ce6fd5292c0bdc065189e535e7cfeb65a95cd25babeba2d4a7fb6c",
    ),
    "revenue_projection_labelled_run": (
        "run-3d22ac6b56a03361",
        "adec3f533dc2f27a9924e43ad969a7b771d681e5730a1b2551fc93731e677b08",
        "0311551b68831f1eb086b99627edad7ca8e528270fdc25581ef0296a791782dd",
    ),
}
MARKER = "zqxmarker"
VER = ("registry", "models", 0, "versions", 0)
FILE = ("input_files", 0)


def _load_json(*parts):
    with open(os.path.join(*parts), "r", encoding="utf-8") as handle:
        return json.load(handle)


def _params(name):
    return _load_json(FIXTURES_DIR, f"{name}_params.json")


def _expected(name):
    return _load_json(FIXTURES_DIR, f"expected_{name}.json")


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


OUTPUT_VALIDATOR = jsonschema.Draft202012Validator(
    _load_json(SCHEMAS_DIR, "output.schema.json")
)
REQUEST_VALIDATOR = jsonschema.Draft202012Validator(
    _load_json(SCHEMAS_DIR, "request.schema.json")
)


@pytest.fixture(scope="module")
def skill_module():
    from . import skill

    return skill


@pytest.fixture(scope="module")
def skill(skill_module):
    return skill_module.ApprovedFinancialModelRunnerSkill()


@pytest.fixture(scope="module")
def manifest():
    with open(os.path.join(BUNDLE_DIR, "manifest.yaml"), "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def card():
    return _load_json(BUNDLE_DIR, "card.json")


def _run(skill, params):
    """Execute and check the output against the output schema."""
    result = skill.execute(params)
    OUTPUT_VALIDATOR.validate(result)
    return result


DROP = ("drop",)
SAME = ("the dotted path",)


def _changed(params, *changes):
    """A deep copy of params with each (path, value) applied; DROP deletes the key."""
    result = copy.deepcopy(params)
    for path, value in changes:
        if not path:
            result = copy.deepcopy(value)
            continue
        target = result
        for key in path[:-1]:
            target = target[key]
        if value is DROP:
            del target[path[-1]]
        elif isinstance(target, list) and path[-1] == len(target):
            target.append(copy.deepcopy(value))
        else:
            target[path[-1]] = copy.deepcopy(value)
    return result


def _dotted(path):
    text = ""
    for part in path:
        if isinstance(part, int):
            text += f"[{part}]"
        else:
            text += f".{part}" if text else part
    return text


def _error(result):
    assert result["status"] == "error", result
    detail = result["error"]["detail"]
    assert set(detail) <= {"reason", "field"}, detail
    return result["error"]["code"], detail["reason"], detail.get("field")


def _v1():
    return _params("capital_impact_run")


DEFAULT_TABLE = "x,y\n2,10\n-3,20\n5.5,30\n"


def _custom(quantities, table=DEFAULT_TABLE, values=None, requested=None):
    """A one-model request: input t with columns x and y, parameters from values,
    and quantities given as (name, decimals, expression)."""
    values = {"a": "2", "b": "0.5"} if values is None else values
    version = {
        "model_version": "1.0.0",
        "inputs": [{"input_alias": "t", "columns": ["x", "y"]}],
        "parameters": list(values),
        "revisions": [{"label": "rev-1", "values": dict(values)}],
        "quantities": [
            {"name": name, "decimals": places, "expression": expression}
            for name, places, expression in quantities
        ],
    }
    return {
        "action": "run",
        "model_name": "m",
        "model_version": "1.0.0",
        "parameter_revision": "rev-1",
        "input_files": [
            {
                "file_ref": "t.csv",
                "input_alias": "t",
                "sha256": _sha(table),
                "validation_status": "passed",
                "content": table,
            }
        ],
        "requested_quantities": requested or [quantities[-1][0]],
        "registry": {
            "registry_id": "r",
            "models": [{"model_name": "m", "versions": [version]}],
        },
    }


def _value(skill, expression, places=2, table=DEFAULT_TABLE, values=None):
    result = _run(skill, _custom([("v", places, expression)], table, values))
    assert result["status"] == "completed", result
    return result["results"][0]["value"]


def _expression_error(skill, expression, table=DEFAULT_TABLE):
    return _error(_run(skill, _custom([("v", 2, expression)], table)))


def _rebuild(params, results):
    """run_id, input_digest, registry entry digest and document, by definition."""
    model = next(
        m
        for m in params["registry"]["models"]
        if m["model_name"] == params["model_name"]
    )
    version = next(
        v for v in model["versions"] if v["model_version"] == params["model_version"]
    )
    revision = next(
        r for r in version["revisions"] if r["label"] == params["parameter_revision"]
    )
    entry = _sha(_canonical(version))
    inputs = _sha("".join(f["sha256"] + "\n" for f in params["input_files"]))
    label = params.get("archive_label")
    identity = {
        "archive_label": label,
        "input_digest": inputs,
        "model_name": params["model_name"],
        "model_version": params["model_version"],
        "parameter_revision": params["parameter_revision"],
        "registry_entry_sha256": entry,
        "requested_quantities": params["requested_quantities"],
    }
    run_id = "run-" + _sha(_canonical(identity))[:16]
    document = _canonical(
        {
            "schema": ARCHIVE_SCHEMA,
            "run_id": run_id,
            "registry_id": params["registry"]["registry_id"],
            "model_name": params["model_name"],
            "model_version": params["model_version"],
            "parameter_revision": params["parameter_revision"],
            "parameter_values": revision["values"],
            "registry_entry_sha256": entry,
            "inputs": [
                {key: f[key] for key in ("input_alias", "file_ref", "sha256")}
                for f in params["input_files"]
            ],
            "input_digest": inputs,
            "requested_quantities": params["requested_quantities"],
            "results": results,
            "archive_label": label,
        }
    )
    return run_id, inputs, entry, document


# --------------------------------------------------------------------------
# The published examples
# --------------------------------------------------------------------------


def _assert_example_run(skill, name, values):
    params = _params(name)
    result = _run(skill, params)
    assert result == _expected(name)
    assert [r["value"] for r in result["results"]] == values
    run_id, inputs, entry, document = _rebuild(params, result["results"])
    record = result["archive_record"]
    assert (run_id, inputs, entry) == EXAMPLE_IDS[name]
    assert (record["run_id"], record["input_digest"]) == (run_id, inputs)
    assert record["registry_entry_sha256"] == entry
    assert record["document"] == document
    assert record["document_sha256"] == _sha(document)
    return result


def test_example_capital_impact_run(skill):
    result = _assert_example_run(skill, "capital_impact_run", ["750000.00", "60000.00"])
    assert result["archive_record"]["inputs_reference"] == (
        "archive/unlabelled/run-0c2f871458836088/inputs"
    )


def test_example_labelled_revenue_projection_run(skill):
    result = _assert_example_run(
        skill, "revenue_projection_labelled_run", ["126.4", "131.4"]
    )
    # The unrequested intermediate quantity is evaluated but not returned.
    assert [r["quantity"] for r in result["results"]] == [
        "projected_y1",
        "projected_y2",
    ]
    assert "base_revenue" not in result["archive_record"]["document"]


def test_example_verify_accepts_the_stored_document(skill):
    params = _params("verify_stored_run")
    stored = _expected("capital_impact_run")["archive_record"]
    assert params["archive_document"] == stored["document"]
    result = _run(skill, params)
    assert result == _expected("verify_stored_run")
    assert result == {
        "status": "completed",
        "verified": True,
        "run_id": stored["run_id"],
        "document_sha256": stored["document_sha256"],
    }


# fmt: off
@pytest.mark.parametrize(
    "name,code,reason,field",
    [
        ("failed_upstream_validation", "INPUT_NOT_VALIDATED", "not_passed", "input_files[0].validation_status"),
        ("changed_content_old_digest", "DIGEST_MISMATCH", "digest_differs", "input_files[0].sha256"),
        ("unregistered_revision", "REVISION_NOT_REGISTERED", "revision_not_found", "parameter_revision"),
        ("undefined_quantity", "QUANTITY_NOT_DEFINED", "quantity_not_found", "requested_quantities[0]"),
        ("verify_changed_document", "ARCHIVE_MISMATCH", "document_differs", "archive_document"),
        ("division_by_zero", "MODEL_EVALUATION_ERROR", "division_by_zero",
         "registry.models[0].versions[0].quantities[1].expression"),
    ],
)
# fmt: on
def test_example_error_cases(skill, name, code, reason, field):
    result = _run(skill, _params(name))
    assert result == _expected(name)
    assert _error(result) == (code, reason, field)


def test_fixture_with_changed_content_keeps_the_old_digest():
    params = _params("changed_content_old_digest")
    entry = params["input_files"][0]
    assert entry["sha256"] == _v1()["input_files"][0]["sha256"]
    assert _sha(entry["content"]).startswith("066747ff")
    assert _sha(entry["content"]).endswith("90cfd")


def test_fixture_with_changed_document_differs_by_one_character():
    stored = _expected("capital_impact_run")["archive_record"]["document"]
    changed = _params("verify_changed_document")["archive_document"]
    assert len(changed) == len(stored)
    assert sum(a != b for a, b in zip(stored, changed)) == 1


# --------------------------------------------------------------------------
# Request and registry validation
# --------------------------------------------------------------------------


def _case(case_id, changes, reason, field=SAME):
    changes = changes if isinstance(changes, list) else [changes]
    if field is SAME:
        field = _dotted(changes[-1][0])
    return pytest.param(changes, reason, field, id=case_id)


def _entries(count):
    return [
        dict(_v1()["input_files"][0], input_alias=f"a{n}", file_ref=f"f{n}.csv")
        for n in range(count)
    ]


BIG = "x" * 2_000_000
# fmt: off
INVALID_REQUEST_CASES = [
    _case("params_is_a_list", ((), []), "not_an_object", None),
    _case("params_is_null", ((), None), "not_an_object", None),
    _case("unknown_top_level_key", (("note",), "x"), "unknown_key", None),
    _case("missing_action", (("action",), DROP), "missing_field"),
    _case("action_unknown", (("action",), "execute"), "invalid_value"),
    _case("action_is_a_number", (("action",), 1), "wrong_type"),
    _case("model_name_upper_case", (("model_name",), "Capital_impact"), "invalid_value"),
    _case("missing_model_name", (("model_name",), DROP), "missing_field"),
    _case("model_version_two_parts", (("model_version",), "1.2"), "invalid_value"),
    _case("model_version_leading_zero", (("model_version",), "01.2.0"), "invalid_value"),
    _case("revision_zero", (("parameter_revision",), "rev-0"), "invalid_value"),
    _case("revision_without_dash", (("parameter_revision",), "rev3"), "invalid_value"),
    _case("missing_input_files", (("input_files",), DROP), "missing_field"),
    _case("input_files_is_a_string", (("input_files",), "x"), "wrong_type"),
    _case("input_files_empty", (("input_files",), []), "count_out_of_range"),
    _case("input_files_21_entries", (("input_files",), _entries(21)), "count_out_of_range"),
    _case("file_entry_is_a_string", (FILE, "x"), "not_an_object"),
    _case("file_entry_unknown_key", (FILE + ("note",), "x"), "unknown_key", "input_files[0]"),
    _case("missing_content", (FILE + ("content",), DROP), "missing_field"),
    _case("file_ref_empty", (FILE + ("file_ref",), ""), "length_out_of_range"),
    _case("file_ref_513_characters", (FILE + ("file_ref",), "a" * 513), "length_out_of_range"),
    _case("file_ref_absolute", (FILE + ("file_ref",), "/prepared/x.csv"), "invalid_value"),
    _case("file_ref_backslash", (FILE + ("file_ref",), "prepared\\x.csv"), "invalid_value"),
    _case("file_ref_empty_segment", (FILE + ("file_ref",), "prepared//x.csv"), "invalid_value"),
    _case("file_ref_dot_segment", (FILE + ("file_ref",), "./x.csv"), "invalid_value"),
    _case("file_ref_dot_dot_segment", (FILE + ("file_ref",), "a/../x.csv"), "invalid_value"),
    _case("file_ref_trailing_slash", (FILE + ("file_ref",), "prepared/"), "invalid_value"),
    _case("alias_param", (FILE + ("input_alias",), "param"), "reserved_name"),
    _case("alias_q", (FILE + ("input_alias",), "q"), "reserved_name"),
    _case("alias_upper_case", (FILE + ("input_alias",), "Exposures"), "invalid_value"),
    _case("alias_33_characters", (FILE + ("input_alias",), "a" * 33), "invalid_value"),
    _case("alias_repeated", (("input_files",), _entries(1) * 2), "duplicate_entry",
          "input_files[1].input_alias"),
    _case("digest_upper_case", (FILE + ("sha256",), "A" * 64), "invalid_value"),
    _case("status_unknown", (FILE + ("validation_status",), "ok"), "invalid_value"),
    _case("content_is_a_number", (FILE + ("content",), 1), "wrong_type"),
    _case("content_too_long", (FILE + ("content",), BIG + "x"), "length_out_of_range"),
    _case("contents_together_too_long", (("input_files",), [dict(e, content=BIG) for e in _entries(5)]),
          "total_too_long", "input_files"),
    _case("requested_is_a_string", (("requested_quantities",), "rwa_added"), "wrong_type"),
    _case("requested_empty", (("requested_quantities",), []), "count_out_of_range"),
    _case("requested_101_entries", (("requested_quantities",), [f"q{n}" for n in range(101)]),
          "count_out_of_range"),
    _case("requested_repeated", (("requested_quantities",), ["rwa_added", "rwa_added"]), "duplicate_entry",
          "requested_quantities[1]"),
    _case("requested_upper_case", (("requested_quantities",), ["RWA"]), "invalid_value",
          "requested_quantities[0]"),
    _case("label_upper_case", (("archive_label",), "Weekly"), "invalid_value"),
    _case("label_leading_dash", (("archive_label",), "-weekly"), "invalid_value"),
    _case("missing_registry", (("registry",), DROP), "missing_field"),
    _case("registry_is_a_list", (("registry",), []), "not_an_object"),
    _case("document_with_run", (("archive_document",), "{}"), "field_not_allowed"),
    _case("verify_without_document", (("action",), "verify"), "missing_field", "archive_document"),
    _case("document_empty", [(("action",), "verify"), (("archive_document",), "")], "length_out_of_range"),
    _case("document_is_a_number", [(("action",), "verify"), (("archive_document",), 1)], "wrong_type"),
]

R0, Q1 = VER + ("revisions", 0), VER + ("quantities", 1)
REGISTRY_INVALID_CASES = [
    _case("unknown_registry_key", (("registry", "note"), "x"), "unknown_key", "registry"),
    _case("missing_registry_id", (("registry", "registry_id"), DROP), "missing_field"),
    _case("registry_id_upper_case", (("registry", "registry_id"), "Practice"), "invalid_value"),
    _case("models_empty", (("registry", "models"), []), "count_out_of_range"),
    _case("model_is_a_string", (("registry", "models", 0), "x"), "not_an_object"),
    _case("unknown_model_key", (("registry", "models", 0, "note"), "x"), "unknown_key", "registry.models[0]"),
    _case("model_name_upper_case", (("registry", "models", 0, "model_name"), "Capital"), "invalid_value"),
    _case("model_repeated", (("registry", "models", 1, "model_name"), "capital_impact"), "duplicate_entry"),
    _case("versions_empty", (("registry", "models", 0, "versions"), []), "count_out_of_range"),
    _case("unknown_version_key", (VER + ("note",), "x"), "unknown_key", _dotted(VER)),
    _case("version_two_parts", (VER + ("model_version",), "1.2"), "invalid_value"),
    _case("version_repeated", (("registry", "models", 0, "versions", 1), "copy"), "duplicate_entry",
          "registry.models[0].versions[1].model_version"),
    _case("inputs_empty", (VER + ("inputs",), []), "count_out_of_range"),
    _case("input_alias_reserved", (VER + ("inputs", 0, "input_alias"), "q"), "reserved_name"),
    _case("input_alias_upper_case", (VER + ("inputs", 0, "input_alias"), "Exposures"), "invalid_value"),
    _case("input_alias_repeated", (VER + ("inputs", 1), {"input_alias": "exposures", "columns": ["a"]}),
          "duplicate_entry", "registry.models[0].versions[0].inputs[1].input_alias"),
    _case("columns_empty", (VER + ("inputs", 0, "columns"), []), "count_out_of_range"),
    _case("column_repeated", (VER + ("inputs", 0, "columns"), ["exposure", "exposure"]), "duplicate_entry",
          "registry.models[0].versions[0].inputs[0].columns[1]"),
    _case("column_upper_case", (VER + ("inputs", 0, "columns", 0), "Exposure"), "invalid_value"),
    _case("parameters_is_a_string", (VER + ("parameters",), "capital_ratio"), "wrong_type"),
    _case("parameter_repeated", (VER + ("parameters",), ["capital_ratio", "capital_ratio"]),
          "duplicate_entry", "registry.models[0].versions[0].parameters[1]"),
    _case("revisions_empty", (VER + ("revisions",), []), "count_out_of_range"),
    _case("label_repeated", (VER + ("revisions", 1), {"label": "rev-3", "values": {"capital_ratio": "1"}}),
          "duplicate_entry", "registry.models[0].versions[0].revisions[1].label"),
    _case("label_without_dash", (R0 + ("label",), "rev3"), "invalid_value"),
    _case("value_missing", (R0 + ("values",), {}), "values_mismatch"),
    _case("value_undeclared", (R0 + ("values", "extra"), "1"), "values_mismatch", _dotted(R0 + ("values",))),
    _case("value_with_percent", (R0 + ("values", "capital_ratio"), "8%"), "invalid_value",
          _dotted(R0 + ("values",))),
    _case("value_is_a_json_number", (R0 + ("values", "capital_ratio"), 0.08), "wrong_type",
          _dotted(R0 + ("values",))),
    _case("quantities_empty", (VER + ("quantities",), []), "count_out_of_range"),
    _case("unknown_quantity_key", (Q1 + ("note",), "x"), "unknown_key", _dotted(Q1)),
    _case("quantity_repeated", (Q1 + ("name",), "rwa_added"), "duplicate_entry"),
    _case("quantity_named_like_a_parameter", (Q1 + ("name",), "capital_ratio"), "name_clash"),
    _case("unit_with_space", (Q1 + ("unit",), "currency units"), "invalid_value"),
    _case("decimals_thirteen", (Q1 + ("decimals",), 13), "invalid_value"),
    _case("decimals_negative", (Q1 + ("decimals",), -1), "invalid_value"),
    _case("decimals_is_a_string", (Q1 + ("decimals",), "2"), "wrong_type"),
    _case("decimals_is_a_boolean", (Q1 + ("decimals",), True), "wrong_type"),
    _case("expression_is_a_number", (Q1 + ("expression",), 1), "wrong_type"),
    _case("missing_expression", (Q1 + ("expression",), DROP), "missing_field"),
]
# fmt: on


@pytest.mark.parametrize("changes,reason,field", INVALID_REQUEST_CASES)
def test_invalid_request_cases(skill, changes, reason, field):
    result = _run(skill, _changed(_v1(), *changes))
    assert _error(result) == ("INVALID_REQUEST", reason, field)


@pytest.mark.parametrize("changes,reason,field", REGISTRY_INVALID_CASES)
def test_registry_invalid_cases(skill, changes, reason, field):
    params = _v1()
    if changes[0][1] == "copy":  # a second version equal to the first
        changes = [(changes[0][0], params["registry"]["models"][0]["versions"][0])]
    result = _run(skill, _changed(params, *changes))
    assert _error(result) == ("REGISTRY_INVALID", reason, field)


# fmt: off
DEFINITION_CASES = [
    ("trailing_operator", "1 +", "syntax_error"),
    ("unclosed_parenthesis", "(1", "syntax_error"),
    ("extra_parenthesis", "1 )", "syntax_error"),
    ("empty_expression", "", "syntax_error"),
    ("two_atoms", "1 2", "syntax_error"),
    ("unknown_function", "total(t.x)", "syntax_error"),
    ("bare_name", "param", "syntax_error"),
    ("power_chain", "2 ^ 2 ^ 2", "syntax_error"),
    ("aggregate_of_a_number", "sum(1)", "syntax_error"),
    ("invalid_character", "1 $ 2", "invalid_character"),
    ("upper_case_letter", "Param.a", "invalid_character"),
    ("superscript_digit", "2 ²", "invalid_character"),
    ("literal_nineteen_digits", "1234567890123456789", "invalid_literal"),
    ("literal_thirteen_decimals", "1.1234567890123", "invalid_literal"),
    ("literal_leading_zero", "01", "invalid_literal"),
    ("expression_2001_characters", "1" + " " * 2000, "too_long"),
    ("expression_401_tokens", "1" + "+1" * 200, "too_many_tokens"),
    ("nesting_33_levels", "(" * 33 + "1" + ")" * 33, "too_deep"),
    ("nested_calls_33_levels", "abs(" * 33 + "1" + ")" * 33, "too_deep"),
    ("unknown_parameter", "param.c", "unknown_parameter"),
    ("unknown_input", "sum(u.x)", "unknown_input"),
    ("unknown_column", "sum(t.z)", "unknown_column"),
    ("unknown_quantity", "q.w", "unknown_quantity"),
    ("self_reference", "q.v + 1", "unknown_quantity"),
    ("exponent_65", "2 ^ 65", "invalid_exponent"),
    ("exponent_minus_65", "2 ^ -65", "invalid_exponent"),
    ("exponent_with_decimals", "2 ^ 1.5", "invalid_exponent"),
    ("exponent_in_parentheses", "2 ^ (2)", "invalid_exponent"),
    ("exponent_from_a_parameter", "2 ^ param.a", "invalid_exponent"),
    ("round_to_13_places", "round(1, 13)", "invalid_round_digits"),
    ("round_to_negative_places", "round(1, -1)", "invalid_round_digits"),
    ("round_to_fractional_places", "round(1, 1.5)", "invalid_round_digits"),
    ("least_with_one_argument", "least(1)", "argument_count"),
    ("greatest_with_eleven_arguments", "greatest(" + ", ".join(["1"] * 11) + ")", "argument_count"),
    ("abs_with_two_arguments", "abs(1, 2)", "argument_count"),
]
# fmt: on


@pytest.mark.parametrize(
    "expression,reason",
    [pytest.param(e, r, id=i) for i, e, r in DEFINITION_CASES],
)
def test_model_definition_invalid_cases(skill, expression, reason):
    field = "registry.models[0].versions[0].quantities[0].expression"
    error = _expression_error(skill, expression)
    assert error == ("MODEL_DEFINITION_INVALID", reason, field)


def test_forward_reference_and_cross_input_sumproduct_are_rejected(skill):
    forward = _custom([("v", 2, "q.w"), ("w", 2, "1")])
    field = "registry.models[0].versions[0].quantities[0].expression"
    assert _error(_run(skill, forward)) == (
        "MODEL_DEFINITION_INVALID",
        "unknown_quantity",
        field,
    )
    two = _custom([("v", 2, "sumproduct(t.x, u.z)")])
    two["registry"]["models"][0]["versions"][0]["inputs"].append(
        {"input_alias": "u", "columns": ["z"]}
    )
    table = "z\n1\n2\n3\n"
    two["input_files"].append(
        dict(two["input_files"][0], input_alias="u", content=table, sha256=_sha(table))
    )
    assert _error(_run(skill, two)) == (
        "MODEL_DEFINITION_INVALID",
        "columns_of_different_inputs",
        field,
    )
    two["registry"]["models"][0]["versions"][0]["quantities"][0][
        "expression"
    ] = "sum(t.x) + sum(u.z)"
    assert _run(skill, two)["results"][0]["value"] == "10.50"


# fmt: off
LOOKUP_CASES = [
    _case("model_unknown", (("model_name",), "unknown_model"), "MODEL_NOT_REGISTERED/model_not_found"),
    _case("version_unknown", (("model_version",), "9.9.9"), "VERSION_NOT_REGISTERED/version_not_found"),
    _case("version_of_another_model", (("model_version",), "2.0.1"), "VERSION_NOT_REGISTERED/version_not_found"),
    _case("revision_of_another_model", (("parameter_revision",), "rev-1"),
          "REVISION_NOT_REGISTERED/revision_not_found"),
    _case("quantity_of_another_model", (("requested_quantities",), ["base_revenue"]),
          "QUANTITY_NOT_DEFINED/quantity_not_found", "requested_quantities[0]"),
    _case("second_quantity_unknown", (("requested_quantities",), ["rwa_added", "capital_buffer"]),
          "QUANTITY_NOT_DEFINED/quantity_not_found", "requested_quantities[1]"),
    _case("extra_alias", (("input_files", 1), dict(_v1()["input_files"][0], input_alias="extra")),
          "INPUT_BINDING_MISMATCH/alias_not_declared", "input_files[1].input_alias"),
    _case("renamed_alias", (FILE + ("input_alias",), "other"), "INPUT_BINDING_MISMATCH/alias_not_declared"),
    _case("missing_alias", (VER + ("inputs", 1), {"input_alias": "rates", "columns": ["r"]}),
          "INPUT_BINDING_MISMATCH/declared_input_missing", "registry.models[0].versions[0].inputs[1].input_alias"),
]
# fmt: on


@pytest.mark.parametrize("changes,code_reason,field", LOOKUP_CASES)
def test_lookup_error_cases(skill, changes, code_reason, field):
    code, reason = code_reason.split("/")
    result = _run(skill, _changed(_v1(), *changes))
    assert _error(result) == (code, reason, field)


# fmt: off
TABLE_CASES = [
    ("header_differs", "x,z\n1,2\n", "header_mismatch"),
    ("header_missing_a_column", "x\n1\n", "header_mismatch"),
    ("header_upper_case", "X,y\n1,2\n", "header_mismatch"),
    ("header_repeated_column", "x,x,y\n1,2,3\n", "duplicate_column"),
    ("row_too_short", "x,y\n1\n", "ragged_row"),
    ("row_too_long", "x,y\n1,2,3\n", "ragged_row"),
    ("letter_cell", "x,y\n1,a\n", "invalid_cell"),
    ("exponent_cell", "x,y\n1,1e3\n", "invalid_cell"),
    ("space_in_cell", "x,y\n1, 2\n", "invalid_cell"),
    ("plus_sign_cell", "x,y\n+1,2\n", "invalid_cell"),
    ("carriage_return_without_line_feed", "x,y\n1,2\r3\n", "invalid_cell"),
    ("two_carriage_returns", "x,y\r\n1,2\r\r\n", "invalid_cell"),
    ("quoted_cell", 'x,y\n"1",2\n', "quote"),
    ("empty_line_between_rows", "x,y\n1,2\n\n3,4\n", "empty_line"),
    ("empty_line_after_header", "x,y\n\n", "empty_line"),
    ("empty_text", "", "empty_line"),
    ("header_only", "x,y\n", "no_rows"),
    ("header_without_line_end", "x,y", "no_rows"),
    ("too_many_rows", "x,y\n" + "1,2\n" * 100_001, "too_many_rows"),
]
# fmt: on


@pytest.mark.parametrize(
    "table,reason", [pytest.param(t, r, id=i) for i, t, r in TABLE_CASES]
)
def test_input_parse_error_cases(skill, table, reason):
    result = _run(skill, _custom([("v", 2, "sum(t.x)")], table))
    assert _error(result) == ("INPUT_PARSE_ERROR", reason, "input_files[0].content")


def test_largest_row_count_is_accepted(skill):
    assert _value(skill, "count(t.x)", 0, "x,y\n" + "1,2\n" * 100_000) == "100000"


# fmt: off
EVALUATION_CASES = [
    ("division_by_zero", [("v", 2, "1 / 0")], "division_by_zero", 0),
    ("zero_divided_by_zero", [("v", 2, "0 / 0")], "division_by_zero", 0),
    ("zero_to_a_negative_power", [("v", 2, "0 ^ -1")], "division_by_zero", 0),
    ("result_at_the_limit", [("v", 2, "1000000000000 * 1000000000000")], "magnitude_limit", 0),
    ("intermediate_at_the_limit", [("v", 2, "1000000000000 * 1000000000000 / 10")], "magnitude_limit", 0),
    ("rounded_value_at_the_limit", [("v", 0, "999999999999999999 * 1000000 + 999999.5")], "magnitude_limit", 0),
    ("power_at_the_limit", [("v", 2, "10 ^ 24")], "magnitude_limit", 0),
    ("failure_in_an_unrequested_quantity", [("u", 2, "1 / 0"), ("v", 2, "1")], "division_by_zero", 0),
]
# fmt: on


@pytest.mark.parametrize(
    "quantities,reason,index",
    [pytest.param(q, r, n, id=i) for i, q, r, n in EVALUATION_CASES],
)
def test_model_evaluation_error_cases(skill, quantities, reason, index):
    result = _run(skill, _custom(quantities, requested=[quantities[-1][0]]))
    field = f"registry.models[0].versions[0].quantities[{index}].expression"
    assert _error(result) == ("MODEL_EVALUATION_ERROR", reason, field)


def test_values_just_below_the_limit_pass(skill):
    big = "999999999999999999 * 1000000 + 999999.5"
    assert _value(skill, big, 1) == "999999999999999999999999.5"
    assert _value(skill, "999999999999 * 1000000000000 + 999999999999", 0) == (
        "999999999999999999999999"
    )


# Each table makes a sumproduct row product, or its running total, reach 10^24.
# fmt: off
SUMPRODUCT_LIMIT_TABLES = [
    ("products_that_cancel", "x,y\n1000000000000,1000000000000\n1000000000000,-1000000000000\n"),
    ("running_total_at_the_limit",
     "x,y\n500000000000,1000000000000\n500000000000,1000000000000\n1,-999999999999\n"),
    ("products_near_10_to_the_36",
     "x,y\n999999999999999999.999999999999,999999999999999999.999999999999\n"
     "-999999999999999999.999999999998,999999999999999999.999999999999\n0.000000000001,0.5\n"),
]
# fmt: on


@pytest.mark.parametrize(
    "table", [pytest.param(t, id=i) for i, t in SUMPRODUCT_LIMIT_TABLES]
)
def test_sumproduct_products_and_running_totals_stay_below_the_limit(skill, table):
    result = _run(skill, _custom([("v", 2, "sumproduct(t.x, t.y)")], table))
    field = "registry.models[0].versions[0].quantities[0].expression"
    assert _error(result) == ("MODEL_EVALUATION_ERROR", "magnitude_limit", field)


def test_sumproduct_just_below_the_limit_passes(skill):
    table = "x,y\n999999999999,1000000000000\n999999999999,1\n"
    assert _value(skill, "sumproduct(t.x, t.y)", 0, table) == "999999999999999999999999"


# Each request breaks two adjacent checks and returns the code of the earlier.
# fmt: off
ORDER_PAIRS = [
    ("request_before_registry", [(("action",), "go"), (("registry", "registry_id"), "X")], "INVALID_REQUEST"),
    ("registry_before_definition", [(("registry", "models", 1, "model_name"), "Bad"),
                                    (Q1 + ("expression",), "1 +")], "REGISTRY_INVALID"),
    ("definition_before_model", [(Q1 + ("expression",), "1 +"), (("model_name",), "nope")],
     "MODEL_DEFINITION_INVALID"),
    ("model_before_version", [(("model_name",), "nope"), (("model_version",), "9.9.9")], "MODEL_NOT_REGISTERED"),
    ("version_before_revision", [(("model_version",), "9.9.9"), (("parameter_revision",), "rev-9")],
     "VERSION_NOT_REGISTERED"),
    ("revision_before_quantity", [(("parameter_revision",), "rev-9"), (("requested_quantities",), ["nope"])],
     "REVISION_NOT_REGISTERED"),
    ("quantity_before_binding", [(("requested_quantities",), ["nope"]), (FILE + ("input_alias",), "other")],
     "QUANTITY_NOT_DEFINED"),
    ("binding_before_validation", [(FILE + ("input_alias",), "other"), (FILE + ("validation_status",), "failed")],
     "INPUT_BINDING_MISMATCH"),
    ("validation_before_digest", [(FILE + ("validation_status",), "failed"), (FILE + ("sha256",), "0" * 64)],
     "INPUT_NOT_VALIDATED"),
    ("digest_before_parse", [(FILE + ("content",), "exposure\n1\n")], "DIGEST_MISMATCH"),
    ("parse_before_evaluation", [(Q1 + ("expression",), "1 / 0"), (FILE + ("content",), "exposure\n1\n"),
                                 (FILE + ("sha256",), _sha("exposure\n1\n"))], "INPUT_PARSE_ERROR"),
    ("evaluation_before_archive", [(("action",), "verify"), (("archive_document",), "{}"),
                                   (Q1 + ("expression",), "1 / 0")], "MODEL_EVALUATION_ERROR"),
]
# fmt: on


@pytest.mark.parametrize(
    "changes,code", [pytest.param(c, k, id=i) for i, c, k in ORDER_PAIRS]
)
def test_check_order_pairs(skill, changes, code):
    result = _run(skill, _changed(_v1(), *changes))
    assert result["status"] == "error"
    assert result["error"]["code"] == code


def test_registry_checks_cover_every_version_before_lookup(skill):
    other = ("registry", "models", 1, "versions", 0)
    broken = _changed(_v1(), (other + ("quantities", 2, "expression"), "q.nope"))
    assert _error(_run(skill, broken)) == (
        "MODEL_DEFINITION_INVALID",
        "unknown_quantity",
        "registry.models[1].versions[0].quantities[2].expression",
    )
    bad_value = (other + ("revisions", 0, "values", "growth_rate"), "3%")
    assert _error(_run(skill, _changed(_v1(), bad_value))) == (
        "REGISTRY_INVALID",
        "invalid_value",
        "registry.models[1].versions[0].revisions[0].values",
    )


# --------------------------------------------------------------------------
# The expression grammar
# --------------------------------------------------------------------------

# fmt: off
VALUE_CASES = [
    ("precedence", "2 + 3 * 4", 0, "14"),
    ("parentheses", "(2 + 3) * 4", 0, "20"),
    ("subtraction_is_left_associative", "10 - 4 - 3", 0, "3"),
    ("division_is_left_associative", "64 / 4 / 2", 0, "8"),
    ("mixed_left_associative", "8 / 2 * 4", 0, "16"),
    ("power_binds_tighter_than_product", "2 * 3 ^ 2", 0, "18"),
    ("unary_minus_binds_looser_than_power", "-3 ^ 2", 0, "-9"),
    ("parenthesised_negative_base", "(-3) ^ 2", 0, "9"),
    ("double_unary_minus", "- - 3 ^ 2", 0, "9"),
    ("unary_minus_then_sum", "-2 + 5", 0, "3"),
    ("exponent_64", "2 ^ 64", 0, "18446744073709551616"),
    ("exponent_minus_64", "2 ^ -64 * 2 ^ 64", 0, "1"),
    ("exponent_with_plus_sign", "2 ^ +3", 0, "8"),
    ("exponent_sign_with_space", "2 ^ - 3", 3, "0.125"),
    ("exponent_zero", "2 ^ 0", 0, "1"),
    ("zero_to_the_zero", "0 ^ 0", 0, "1"),
    ("sum", "sum(t.x)", 2, "4.50"),
    ("mean", "mean(t.x)", 2, "1.50"),
    ("min", "min(t.x)", 2, "-3.00"),
    ("max", "max(t.x)", 2, "5.50"),
    ("first", "first(t.x)", 2, "2.00"),
    ("last", "last(t.x)", 2, "5.50"),
    ("count", "count(t.x)", 0, "3"),
    ("sumproduct", "sumproduct(t.x, t.y)", 2, "125.00"),
    ("abs", "abs(-2.5)", 1, "2.5"),
    ("least", "least(3, 1, 2)", 0, "1"),
    ("greatest", "greatest(3, 1, 2)", 0, "3"),
    ("greatest_of_ten", "greatest(" + ", ".join(str(n) for n in range(10)) + ")", 0, "9"),
    ("round_half_up", "round(2.345, 2)", 3, "2.350"),
    ("round_negative_tie", "round(-2.345, 2)", 3, "-2.350"),
    ("round_to_whole", "round(2.5, 0)", 0, "3"),
    ("parameters", "param.a * param.b", 2, "1.00"),
    ("spaces_everywhere", "  1+2  *  3  ", 0, "7"),
    ("whitespace_inside_references", "sum ( t . x ) + param . a", 2, "6.50"),
    ("nesting_32_levels", "(" * 32 + "1" + ")" * 32, 0, "1"),
    ("unary_minus_chain_399", "-" * 399 + "1", 0, "-1"),
    ("unary_minus_chain_398", "-" * 398 + "1", 0, "1"),
]
# fmt: on


@pytest.mark.parametrize(
    "expression,places,value",
    [pytest.param(e, p, v, id=i) for i, e, p, v in VALUE_CASES],
)
def test_grammar_values(skill, expression, places, value):
    assert _value(skill, expression, places) == value


# A control character is a code point from U+0000 to U+001F or from U+007F to
# U+009F. Only the space U+0020 may stand between the tokens of a formula.
CONTROL_CHARACTERS = [
    pytest.param("\t", id="tab"),
    pytest.param("\r", id="carriage_return"),
    pytest.param("\n", id="line_feed"),
    pytest.param("\x0b", id="vertical_tab"),
    pytest.param("\x0c", id="form_feed"),
    pytest.param("\x00", id="null"),
    pytest.param("\x1f", id="unit_separator"),
    pytest.param("\x7f", id="delete"),
    pytest.param("\x85", id="next_line"),
    pytest.param("\x9f", id="last_c1_control"),
]
FORMULA_FIELD = "registry.models[0].versions[0].quantities[0].expression"


@pytest.mark.parametrize("character", CONTROL_CHARACTERS)
def test_control_character_in_a_formula_is_an_invalid_request(skill, character):
    assert _expression_error(skill, f"1 +{character}2") == (
        "INVALID_REQUEST",
        "invalid_value",
        FORMULA_FIELD,
    )


@pytest.mark.parametrize("character", CONTROL_CHARACTERS[:2])
def test_control_character_is_rejected_before_any_formula_is_parsed(skill, character):
    # It comes before the parser's character and length rules.
    for expression in (f"1 ${character}2", "1" + " " * 2000 + character):
        error = _expression_error(skill, expression)
        assert error == ("INVALID_REQUEST", "invalid_value", FORMULA_FIELD)
    # It also comes before a parse fault in a formula read earlier.
    other = ("registry", "models", 1, "versions", 0, "quantities", 2, "expression")
    params = _changed(_v1(), (Q1 + ("expression",), "1 +"), (other, f"1{character}+ 2"))
    assert _error(_run(skill, params)) == (
        "INVALID_REQUEST",
        "invalid_value",
        "registry.models[1].versions[0].quantities[2].expression",
    )


@pytest.mark.parametrize(
    "character",
    [
        pytest.param("\xa0", id="no_break_space"),
        pytest.param("\u2028", id="line_separator"),
        pytest.param("\u3000", id="ideographic_space"),
    ],
)
def test_other_spaces_in_a_formula_reach_the_parser(skill, character):
    assert _expression_error(skill, f"1 +{character}2") == (
        "MODEL_DEFINITION_INVALID",
        "invalid_character",
        FORMULA_FIELD,
    )


# A file reference and the archive document hold no control character. The
# check comes right after the type check of the field, before its length bound
# and before any comparison with the archive.
@pytest.mark.parametrize("action", ["run", "verify"])
@pytest.mark.parametrize("character", CONTROL_CHARACTERS)
def test_control_character_in_a_file_reference_is_an_invalid_request(
    skill, action, character
):
    base = _v1() if action == "run" else _params("verify_stored_run")
    for value in (f"prepared/exp{character}osures.csv", "a" * 513 + character):
        params = _changed(base, (FILE + ("file_ref",), value))
        assert _error(_run(skill, params)) == (
            "INVALID_REQUEST",
            "invalid_value",
            "input_files[0].file_ref",
        )


@pytest.mark.parametrize("character", CONTROL_CHARACTERS)
def test_control_character_in_the_archive_document_is_an_invalid_request(
    skill, character
):
    document = _params("verify_stored_run")["archive_document"]
    for text in (document + character, document[:5] + character + document[5:]):
        params = _changed(_params("verify_stored_run"), (("archive_document",), text))
        assert _error(_run(skill, params)) == (
            "INVALID_REQUEST",
            "invalid_value",
            "archive_document",
        )


@pytest.mark.parametrize(
    "character",
    [
        pytest.param("\xa0", id="no_break_space"),
        pytest.param(" ", id="line_separator"),
        pytest.param("　", id="ideographic_space"),
    ],
)
def test_other_spaces_in_a_file_reference_are_content(skill, character):
    file_ref = f"prepared/exp{character}osures.csv"
    params = _changed(_v1(), (FILE + ("file_ref",), file_ref))
    document = _run(skill, params)["archive_record"]["document"]
    assert json.loads(document)["inputs"][0]["file_ref"] == file_ref
    verify = _changed(
        params, (("action",), "verify"), (("archive_document",), document)
    )
    assert _run(skill, verify)["verified"] is True


def test_long_unary_minus_chain_is_parsed_without_recursion(skill):
    # 399 signs and one literal reach the 400-token cap; one more sign passes it.
    assert _value(skill, "-" * 399 + "1", 0) == "-1"
    error = _expression_error(skill, "-" * 400 + "1")
    assert error[:2] == ("MODEL_DEFINITION_INVALID", "too_many_tokens")


def test_later_quantity_reads_the_rounded_value(skill):
    params = _custom([("u", 2, "1 / 3"), ("w", 4, "q.u * 3")], requested=["u", "w"])
    result = _run(skill, params)
    assert [r["value"] for r in result["results"]] == ["0.33", "0.9900"]
    # unit appears only when the registry declares one
    assert all("unit" not in r for r in result["results"])


# --------------------------------------------------------------------------
# Tables
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "table",
    [
        pytest.param("y,x\n10,2\n20,-3\n30,5.5\n", id="columns_in_another_order"),
        pytest.param("x,y\r\n2,10\r\n-3,20\r\n5.5,30\r\n", id="crlf"),
        pytest.param("x,y\n2,10\n-3,20\n5.5,30", id="no_final_line_end"),
        pytest.param("x,y\r\n2,10\n-3,20\r\n5.5,30", id="mixed_line_ends"),
    ],
)
def test_table_layouts_give_the_same_figures(skill, table):
    assert _value(skill, "sumproduct(t.x, t.y)", 2, table) == "125.00"


@pytest.mark.parametrize(
    "separator",
    [
        pytest.param(" ", id="line_separator"),
        pytest.param(" ", id="paragraph_separator"),
        pytest.param("\u0085", id="next_line"),
        pytest.param("\x0b", id="vertical_tab"),
        pytest.param("\x0c", id="form_feed"),
        pytest.param("\x1c", id="file_separator"),
    ],
)
def test_table_lines_split_on_line_feed_only(skill, separator):
    table = f"x,y\n2,10{separator}-3\n"
    result = _run(skill, _custom([("v", 2, "sum(t.x)")], table))
    assert _error(result) == (
        "INPUT_PARSE_ERROR",
        "invalid_cell",
        "input_files[0].content",
    )


# --------------------------------------------------------------------------
# Rounding and rendering
# --------------------------------------------------------------------------


@pytest.mark.parametrize("places", range(13))
def test_half_up_rounding_at_every_decimals_value(skill, places):
    # 1.1...15 with one more decimal than kept rounds up to 1.1...2.
    digits = "1" + "1" * places + "5"
    expression = f"{digits} / 10 ^ {places + 1}"
    expected = "2" if places == 0 else "1." + "1" * (places - 1) + "2"
    assert _value(skill, expression, places) == expected


@pytest.mark.parametrize(
    "expression,places,value",
    [
        ("-0.125", 2, "-0.13"),
        ("-2.5", 0, "-3"),
        ("round(-0.125, 2)", 3, "-0.130"),
        ("0 - 1.005", 2, "-1.01"),
    ],
)
def test_negative_ties_round_away_from_zero(skill, expression, places, value):
    assert _value(skill, expression, places) == value


@pytest.mark.parametrize(
    "expression,places,value",
    [
        ("-0.001", 2, "0.00"),
        ("0 * -1", 0, "0"),
        ("-0", 3, "0.000"),
        ("round(-0.004, 2)", 2, "0.00"),
        ("-(0.4)", 0, "0"),
    ],
)
def test_negative_zero_renders_as_zero(skill, expression, places, value):
    assert _value(skill, expression, places) == value


@pytest.mark.parametrize(
    "expression,places,value",
    [
        ("10 ^ -12", 12, "0.000000000001"),
        ("0.000001 * 0.000001", 12, "0.000000000001"),
        ("10 ^ 20", 0, "100000000000000000000"),
        ("1000000000000 * 100000000000", 2, "100000000000000000000000.00"),
        ("1 / 3", 12, "0.333333333333"),
    ],
)
def test_rendered_values_have_no_exponent(skill, expression, places, value):
    rendered = _value(skill, expression, places)
    assert rendered == value
    assert "E" not in rendered and "e" not in rendered


BAD_DECIMALS = [
    pytest.param("١", id="arabic_indic_digit"),
    pytest.param("１", id="full_width_digit"),
    pytest.param("1_000", id="underscore"),
    pytest.param("1,000", id="comma"),
    pytest.param(" 1", id="leading_space"),
    pytest.param("1e3", id="exponent"),
    pytest.param("+1", id="plus_sign"),
    pytest.param("NaN", id="not_a_number"),
    pytest.param("Infinity", id="infinity"),
    pytest.param("1.", id="trailing_point"),
    pytest.param(".5", id="leading_point"),
    pytest.param("01", id="leading_zero"),
    pytest.param("-0.1234567890123", id="thirteen_decimals"),
]


@pytest.mark.parametrize("value", BAD_DECIMALS)
def test_decimal_strings_reject_non_ascii_digits_and_numbers(skill, value):
    table = f"y,x\n1,{value}\n"
    result = _run(skill, _custom([("v", 2, "sum(t.x)")], table))
    expected_reason = "ragged_row" if "," in value else "invalid_cell"
    assert _error(result) == (
        "INPUT_PARSE_ERROR",
        expected_reason,
        "input_files[0].content",
    )
    path = VER + ("revisions", 0, "values", "capital_ratio")
    result = _run(skill, _changed(_v1(), (path, value)))
    field = "registry.models[0].versions[0].revisions[0].values"
    assert _error(result) == ("REGISTRY_INVALID", "invalid_value", field)


@pytest.mark.parametrize("number", [0.08, 8, True], ids=["float", "integer", "boolean"])
def test_json_numbers_are_not_decimal_strings(skill, number):
    path = VER + ("revisions", 0, "values", "capital_ratio")
    result = _run(skill, _changed(_v1(), (path, number)))
    field = "registry.models[0].versions[0].revisions[0].values"
    assert _error(result) == ("REGISTRY_INVALID", "wrong_type", field)


def test_private_context_ignores_the_thread_context(skill):
    expected = {name: _expected(name) for name in RUN_FIXTURES}
    third = _value(skill, "1 / 3", 12)
    hostile = decimal.Context(
        prec=3,
        rounding=decimal.ROUND_DOWN,
        traps=[decimal.Inexact, decimal.Rounded, decimal.InvalidOperation],
    )
    with decimal.localcontext(hostile):
        for name in RUN_FIXTURES:
            assert _run(skill, _params(name)) == expected[name]
        assert _value(skill, "1 / 3", 12) == third == "0.333333333333"


# --------------------------------------------------------------------------
# Digests, run ids, the archive document and verify
# --------------------------------------------------------------------------


def test_digests_and_run_id_follow_their_definitions(skill):
    for name in RUN_FIXTURES:
        params = _params(name)
        record = _run(skill, params)["archive_record"]
        results = json.loads(record["document"])["results"]
        run_id, inputs, entry, document = _rebuild(params, results)
        assert record["run_id"] == run_id
        assert record["input_digest"] == inputs
        assert record["registry_entry_sha256"] == entry
        assert record["document"] == document
        assert record["document_sha256"] == _sha(document)
    two_files = _custom([("v", 2, "sum(t.x)")])
    extra = dict(two_files["input_files"][0], input_alias="u", file_ref="u.csv")
    two_files["input_files"].append(extra)
    two_files["registry"]["models"][0]["versions"][0]["inputs"].append(
        {"input_alias": "u", "columns": ["x", "y"]}
    )
    record = _run(skill, two_files)["archive_record"]
    digest = two_files["input_files"][0]["sha256"]
    assert record["input_digest"] == _sha(f"{digest}\n{digest}\n")


def test_identical_requests_give_the_same_run_id(skill):
    first = _run(skill, _v1())
    assert _run(skill, _v1()) == first
    reordered = _run(skill, json.loads(json.dumps(_v1(), sort_keys=True)))
    assert reordered == first
    labelled = _run(skill, _changed(_v1(), (("archive_label",), "weekly")))
    swapped = _run(
        skill,
        _changed(_v1(), (("requested_quantities",), ["capital_required", "rwa_added"])),
    )
    run_ids = {r["archive_record"]["run_id"] for r in (first, labelled, swapped)}
    assert len(run_ids) == 3
    order = [r["quantity"] for r in swapped["results"]]
    assert order == ["capital_required", "rwa_added"]


def test_archive_references_for_labelled_and_unlabelled_runs(skill):
    for name, label in (
        ("capital_impact_run", "unlabelled"),
        (RUN_FIXTURES[1], "weekly"),
    ):
        params = _params(name)
        record = _run(skill, params)["archive_record"]
        run_id = record["run_id"]
        assert record["model_reference"] == (
            f"registry/{params['model_name']}/{params['model_version']}"
        )
        assert record["inputs_reference"] == f"archive/{label}/{run_id}/inputs"
        assert record["outputs_reference"] == (
            f"archive/{label}/{run_id}/outputs/results.json"
        )
        assert json.loads(record["document"])["archive_label"] == params.get(
            "archive_label"
        )


def test_document_references_tables_by_digest_only(skill):
    params = _v1()
    document = json.loads(_run(skill, params)["archive_record"]["document"])
    assert document["inputs"] == [
        {
            "input_alias": "exposures",
            "file_ref": "prepared/exposures.csv",
            "sha256": params["input_files"][0]["sha256"],
        }
    ]
    assert document["parameter_values"] == {"capital_ratio": "0.08"}
    assert document["schema"] == ARCHIVE_SCHEMA
    text = _run(skill, params)["archive_record"]["document"]
    assert params["input_files"][0]["content"] not in text
    assert "1000000" not in text and "risk_weight" not in text


@pytest.mark.parametrize("position", [0, 1, 20, 200, -2, -1])
def test_verify_rejects_every_one_character_change(skill, position):
    document = _expected("capital_impact_run")["archive_record"]["document"]
    index = position % len(document)
    replacement = "x" if document[index] != "x" else "y"
    changed = document[:index] + replacement + document[index + 1 :]
    params = _changed(_params("verify_stored_run"), (("archive_document",), changed))
    assert _error(_run(skill, params)) == (
        "ARCHIVE_MISMATCH",
        "document_differs",
        "archive_document",
    )


def test_verify_recomputes_rather_than_trusting_the_document(skill):
    document = json.loads(_expected("capital_impact_run")["archive_record"]["document"])
    document["results"][1]["value"] = "1.00"
    for text in (
        _canonical(document),
        json.dumps(json.loads(_params("verify_stored_run")["archive_document"])),
        _params("verify_stored_run")["archive_document"] + " ",
    ):
        params = _changed(_params("verify_stored_run"), (("archive_document",), text))
        assert _error(_run(skill, params))[0] == "ARCHIVE_MISMATCH"


def _reversed_keys(value):
    if isinstance(value, dict):
        return {key: _reversed_keys(value[key]) for key in reversed(list(value))}
    if isinstance(value, list):
        return [_reversed_keys(item) for item in value]
    return value


def test_error_detail_independent_of_key_order(skill):
    faults = [
        [(("note",), "x"), (("model_name",), "Bad")],
        [(FILE + ("note",), "x"), (FILE + ("sha256",), "bad")],
        [(VER + ("note",), "x"), (VER + ("model_version",), "1")],
        [(R0 + ("values", "extra"), "1"), (R0 + ("label",), "r")],
        [(Q1 + ("expression",), "q.nope + param.nope")],
    ]
    for changes in faults:
        params = _changed(_v1(), *changes)
        assert _run(skill, _reversed_keys(params)) == _run(skill, params), changes


# --------------------------------------------------------------------------
# Lone surrogates: each field's own check rejects them
# --------------------------------------------------------------------------

S = "\ud800"
# fmt: off
SURROGATE_CASES = [
    ("action", [(("action",), "run" + S)], "INVALID_REQUEST", "invalid_value", None),
    ("model_name", [(("model_name",), "m" + S)], "INVALID_REQUEST", "invalid_value", None),
    ("model_version", [(("model_version",), "1.2.0" + S)], "INVALID_REQUEST", "invalid_value", None),
    ("parameter_revision", [(("parameter_revision",), "rev-3" + S)], "INVALID_REQUEST", "invalid_value", None),
    ("file_ref", [(FILE + ("file_ref",), "a" + S)], "INVALID_REQUEST", "invalid_value", None),
    ("input_alias", [(FILE + ("input_alias",), "a" + S)], "INVALID_REQUEST", "invalid_value", None),
    ("sha256", [(FILE + ("sha256",), "a" * 63 + S)], "INVALID_REQUEST", "invalid_value", None),
    ("validation_status", [(FILE + ("validation_status",), "passed" + S)], "INVALID_REQUEST", "invalid_value",
     None),
    ("content", [(FILE + ("content",), "exposure,risk_weight\n1," + S + "\n")], "INVALID_REQUEST",
     "invalid_value", None),
    ("requested_quantity", [(("requested_quantities", 0), "rwa" + S)], "INVALID_REQUEST", "invalid_value",
     None),
    ("archive_label", [(("archive_label",), "w" + S)], "INVALID_REQUEST", "invalid_value", None),
    ("archive_document", [(("action",), "verify"), (("archive_document",), "{" + S)], "INVALID_REQUEST",
     "invalid_value", None),
    ("registry_id", [(("registry", "registry_id"), "r" + S)], "REGISTRY_INVALID", "invalid_value", None),
    ("registry_model_name", [(("registry", "models", 0, "model_name"), "m" + S)], "REGISTRY_INVALID",
     "invalid_value", None),
    ("registry_model_version", [(VER + ("model_version",), "1.2.0" + S)], "REGISTRY_INVALID",
     "invalid_value", None),
    ("registry_input_alias", [(VER + ("inputs", 0, "input_alias"), "e" + S)], "REGISTRY_INVALID",
     "invalid_value", None),
    ("registry_column", [(VER + ("inputs", 0, "columns", 0), "e" + S)], "REGISTRY_INVALID", "invalid_value",
     None),
    ("registry_parameter", [(VER + ("parameters", 0), "c" + S)], "REGISTRY_INVALID", "invalid_value", None),
    ("revision_label", [(R0 + ("label",), "rev-3" + S)], "REGISTRY_INVALID", "invalid_value", None),
    ("revision_value", [(R0 + ("values", "capital_ratio"), "0.08" + S)], "REGISTRY_INVALID", "invalid_value",
     _dotted(R0 + ("values",))),
    ("revision_value_key", [(R0 + ("values",), {"capital_ratio" + S: "0.08"})], "REGISTRY_INVALID",
     "values_mismatch", None),
    ("quantity_name", [(Q1 + ("name",), "c" + S)], "REGISTRY_INVALID", "invalid_value", None),
    ("quantity_unit", [(Q1 + ("unit",), "u" + S)], "REGISTRY_INVALID", "invalid_value", None),
    ("expression", [(Q1 + ("expression",), "q.rwa_added" + S)], "MODEL_DEFINITION_INVALID",
     "invalid_character", None),
]
# fmt: on


@pytest.mark.parametrize(
    "changes,code,reason,field",
    [pytest.param(c, k, r, f, id=i) for i, c, k, r, f in SURROGATE_CASES],
)
def test_lone_surrogate_is_rejected_by_the_check_of_its_field(
    skill, changes, code, reason, field
):
    result = _run(skill, _changed(_v1(), *changes))
    expected_field = field or _dotted(changes[-1][0])
    assert _error(result) == (code, reason, expected_field)
    # Nothing of the value reaches the output: it encodes as UTF-8.
    _canonical(result).encode("utf-8")


# --------------------------------------------------------------------------
# Bundle files, manifest, card and loader
# --------------------------------------------------------------------------

EXPECTED_TOP_FILES = {
    "__init__.py",
    "skill.py",
    "test_skill.py",
    "manifest.yaml",
    "instructions.md",
    "card.json",
}
EXPECTED_SCHEMA_FILES = {"request.schema.json", "output.schema.json"}
EXPECTED_FIXTURE_FILES = (
    {f"{name}_params.json" for name in REFERENCE_FIXTURES}
    | {f"expected_{name}.json" for name in REFERENCE_FIXTURES}
    | {"card_ui_samples.json"}
)


def _listing(root):
    names = set()
    for folder, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if name != "__pycache__"]
        for name in files:
            if name != ".DS_Store":
                names.add(os.path.relpath(os.path.join(folder, name), root))
    return names


def test_bundle_holds_exactly_the_standard_files():
    everything = _listing(BUNDLE_DIR)
    assert everything == (
        EXPECTED_TOP_FILES
        | {os.path.join("schemas", name) for name in EXPECTED_SCHEMA_FILES}
        | {os.path.join("fixtures", name) for name in EXPECTED_FIXTURE_FILES}
    )
    python_files = {name for name in everything if name.endswith(".py")}
    assert python_files == {"__init__.py", "skill.py", "test_skill.py"}
    assert os.path.isfile(os.path.join(BUNDLE_DIR, "..", "__init__.py"))


def test_manifest_declares_the_standard_fields(manifest):
    assert manifest["name"] == REGISTRY_ID
    assert manifest["version"] == "0.1.0"
    assert manifest["category"] == "finance"
    assert manifest["trust_tier"] == "L0"
    assert manifest["issuer"] == ISSUER
    assert manifest["norm_version"] == NORM_VERSION
    assert manifest["economic_effect"] == ECONOMIC_EFFECT
    assert "env_vars" not in manifest
    assert "requirements" not in manifest
    assert "output" not in manifest
    assert tuple(manifest["outputs"]) == OUTPUT_KEYS
    provenance = manifest["provenance"]
    assert set(provenance) == {"anchor", "content_sha256", "authored_on"}
    assert provenance["anchor"] == "repository"
    assert re.fullmatch(r"[0-9a-f]{64}", provenance["content_sha256"], re.ASCII)
    assert re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", provenance["authored_on"])
    assert isinstance(manifest["description"], str) and manifest["description"]
    short = manifest["short_description"]
    # The catalog index shows the first sentence: it must end only at the end.
    assert short.endswith(".")
    assert re.search(r"[.!?]\s", short) is None


def test_manifest_constitution_lists_two_to_five_rules(manifest):
    lines = [line for line in manifest["constitution"].split("\n") if line]
    assert 2 <= len(lines) <= 5
    for number, line in enumerate(lines, 1):
        assert re.fullmatch(rf"{number}\. \S.*", line), line


def test_manifest_origin_is_ao_standard(manifest):
    assert manifest["origin"] == "ao_standard"
    assert manifest["issuer"]["org"] == "AO"


def test_manifest_serves_exactly_the_claimed_dwas(manifest):
    assert manifest["serves_dwa"] == SERVED_DWAS


def test_in_code_manifest_matches_manifest_yaml(skill, manifest):
    assert skill.manifest == {
        "name": manifest["name"],
        "version": manifest["version"],
        "parameters": manifest["parameters"],
    }


def test_manifest_parameters_take_the_flat_form(manifest):
    parameters = manifest["parameters"]
    assert parameters["type"] == "object"
    assert tuple(parameters["properties"]) == PARAMETER_NAMES
    assert parameters["required"] == REQUIRED_PARAMETERS
    assert parameters["additionalProperties"] is False
    for name, prop in parameters["properties"].items():
        expected = {"type", "description"} | ({"enum"} if name == "action" else set())
        assert set(prop) == expected, name
        assert isinstance(prop["type"], str) and prop["description"], name
    assert parameters["properties"]["action"]["enum"] == ["run", "verify"]


def test_every_params_fixture_passes_parameter_validation(skill):
    for name in REFERENCE_FIXTURES:
        assert skill.validate_params(_params(name)) is True, name
    context = SkillContext(skill=REGISTRY_ID)
    for name in REFERENCE_FIXTURES:
        assert context.execute(REGISTRY_ID, _params(name)) == _expected(name), name


def test_card_matches_manifest_and_fixture_outputs(card, manifest):
    assert card["name"] == CARD_TITLE
    assert card["description"] == manifest["short_description"]
    assert card["issuer"] == manifest["issuer"]
    ui_schema = card["ui_schema"]
    assert ui_schema["type"] == "card"
    samples = [_expected(name) for name in RUN_FIXTURES]
    for field in ui_schema["fields"]:
        assert field["label"], field
        for sample in samples:
            current = sample
            for part in field["key"].split("."):
                assert isinstance(current, dict) and part in current, field
                current = current[part]


def test_card_fixture_equals_the_output_of_its_params_fixture(skill):
    sample = _load_json(FIXTURES_DIR, "card_ui_samples.json")
    assert sample == _run(skill, _params(CARD_FIXTURE))
    assert sample == _expected(CARD_FIXTURE)


def test_skill_loader_loads_the_bundle():
    bundle = SkillLoader.load_skill(REGISTRY_ID)
    assert bundle["manifest"]["name"] == REGISTRY_ID
    assert bundle["class"].__name__ == CLASS_NAME
    assert REGISTRY_ID in bundle["instructions"]
    assert bundle["card"]["issuer"]["email"] == bundle["manifest"]["issuer"]["email"]
    result = bundle["class"]().execute(_params(CARD_FIXTURE))
    assert result == _expected(CARD_FIXTURE)


def test_every_fixture_matches_the_documentation_schemas():
    for name in REFERENCE_FIXTURES:
        OUTPUT_VALIDATOR.validate(_expected(name))
        REQUEST_VALIDATOR.validate(_params(name))


@pytest.mark.parametrize("name", REFERENCE_FIXTURES)
def test_fixtures_repeat_byte_identically(skill, name):
    params = _params(name)
    first = _canonical(_run(skill, params))
    second = _canonical(_run(skill, copy.deepcopy(params)))
    assert first == second == _canonical(_expected(name))


# --------------------------------------------------------------------------
# Source rules that the repository guard does not check
# --------------------------------------------------------------------------

RUNTIME_IMPORTS = {
    "hashlib",
    "json",
    "re",
    "decimal",
    "typing",
    "skill_assay.core.base_skill",
}
DECIMAL_CONTEXT_NAMES = {
    "getcontext",
    "setcontext",
    "localcontext",
    "DefaultContext",
    "BasicContext",
    "ExtendedContext",
}
UNICODE_TEXT_METHODS = {
    "lower",
    "upper",
    "casefold",
    "title",
    "swapcase",
    "capitalize",
    "isdigit",
    "isnumeric",
    "isalpha",
    "isalnum",
    "isspace",
    "splitlines",
}
WHITESPACE_METHODS = {"split", "strip", "lstrip", "rstrip"}
RE_FUNCTIONS = {
    "compile",
    "search",
    "match",
    "fullmatch",
    "split",
    "findall",
    "finditer",
    "sub",
    "subn",
}
FORBIDDEN_TEXT_NAMES = (
    DECIMAL_CONTEXT_NAMES | UNICODE_TEXT_METHODS | {"fromisoformat", "unicodedata"}
)


def _carries_re_ascii(node):
    return any(
        isinstance(item, ast.Attribute)
        and item.attr == "ASCII"
        and isinstance(item.value, ast.Name)
        and item.value.id == "re"
        for item in ast.walk(node)
    )


def _source_findings(tree):
    """Findings of the number and text rules in one parsed runtime module."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            found.append("float constant")
        elif isinstance(node, ast.Name):
            if node.id == "float" or node.id in FORBIDDEN_TEXT_NAMES:
                found.append(node.id)
        elif isinstance(node, ast.Attribute):
            if node.attr in FORBIDDEN_TEXT_NAMES:
                found.append(node.attr)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name not in RUNTIME_IMPORTS:
                    found.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.level or node.module not in RUNTIME_IMPORTS:
                found.append(f"import {node.module}")
            for alias in node.names:
                if node.module == "re" or alias.name in FORBIDDEN_TEXT_NAMES:
                    found.append(f"from {node.module} import {alias.name}")
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in ("hash", "id"):
                found.append(func.id)
            if not isinstance(func, ast.Attribute):
                continue
            if func.attr in WHITESPACE_METHODS and not node.args and not node.keywords:
                found.append(f"{func.attr}() without an argument")
            is_re = isinstance(func.value, ast.Name) and func.value.id == "re"
            arguments = [*node.args, *(keyword.value for keyword in node.keywords)]
            if is_re and func.attr in RE_FUNCTIONS:
                if not any(_carries_re_ascii(argument) for argument in arguments):
                    found.append(f"re.{func.attr} without re.ASCII")
    return found


FLAGGED_SNIPPETS = (
    "x = 0.5",
    "y = float(text)",
    "import unicodedata",
    "import os",
    "from decimal import getcontext",
    "decimal.localcontext()",
    "context = decimal.DefaultContext",
    "day = date.fromisoformat(text)",
    "re.compile('[a-z]')",
    "re.fullmatch('[a-z]', text)",
    "from re import fullmatch",
    "parts = text.split()",
    "text = text.strip()",
    "lines = text.splitlines()",
    "low = text.lower()",
    "ok = text.isdigit()",
    "ok = text.isspace()",
    "key = hash(text)",
)
ALLOWED_SNIPPETS = (
    "import hashlib",
    "from decimal import Context, Decimal",
    "pattern = re.compile('[a-z]', re.ASCII)",
    "ok = re.fullmatch('[a-z]', text, flags=re.ASCII)",
    "parts = text.split('\\n')",
    "blank = text.strip(' \\n') == ''",
    "value = _CTX.multiply(left, right)",
)


def test_runtime_source_follows_the_text_and_number_rules():
    runtime = sorted(
        name
        for name in _listing(BUNDLE_DIR)
        if name.endswith(".py") and name != "test_skill.py"
    )
    assert runtime == ["__init__.py", "skill.py"]
    for name in runtime:
        with open(os.path.join(BUNDLE_DIR, name), "r", encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), name)
        assert _source_findings(tree) == [], name
    for snippet in FLAGGED_SNIPPETS:
        assert _source_findings(ast.parse(snippet)) != [], snippet
    for snippet in ALLOWED_SNIPPETS:
        assert _source_findings(ast.parse(snippet)) == [], snippet


# --------------------------------------------------------------------------
# Error details and the fault registry
# --------------------------------------------------------------------------


def _marked():
    """The first example with the marker in the names the request carries."""
    params = _v1()
    text = json.dumps(params).replace("capital_impact", MARKER)
    text = text.replace("prepared/exposures.csv", f"{MARKER}/x.csv")
    return json.loads(text)


# fmt: off
ECHO_CASES = [
    ("unknown_key", [((MARKER,), 1)], "INVALID_REQUEST"),
    ("bad_file_ref", [(FILE + ("file_ref",), f"/{MARKER}")], "INVALID_REQUEST"),
    ("bad_alias", [(FILE + ("input_alias",), MARKER.upper())], "INVALID_REQUEST"),
    ("registry_unknown_key", [(VER + (MARKER,), 1)], "REGISTRY_INVALID"),
    ("registry_repeated_model", [(("registry", "models", 1, "model_name"), MARKER)], "REGISTRY_INVALID"),
    ("bad_expression", [(Q1 + ("expression",), f"q.{MARKER} + {MARKER}(1)")], "MODEL_DEFINITION_INVALID"),
    ("unknown_model", [(("model_name",), MARKER + "x")], "MODEL_NOT_REGISTERED"),
    ("unknown_version", [(("model_version",), "9.9.9")], "VERSION_NOT_REGISTERED"),
    ("unknown_revision", [(("parameter_revision",), "rev-9")], "REVISION_NOT_REGISTERED"),
    ("unknown_quantity", [(("requested_quantities",), [MARKER])], "QUANTITY_NOT_DEFINED"),
    ("unbound_alias", [(FILE + ("input_alias",), MARKER)], "INPUT_BINDING_MISMATCH"),
    ("not_validated", [(FILE + ("validation_status",), "failed")], "INPUT_NOT_VALIDATED"),
    ("digest", [(FILE + ("content",), f"{MARKER}\n")], "DIGEST_MISMATCH"),
    ("parse", [(FILE + ("content",), f"{MARKER}\n1\n"), (FILE + ("sha256",), _sha(f"{MARKER}\n1\n"))],
     "INPUT_PARSE_ERROR"),
    ("evaluation", [(Q1 + ("expression",), "q.rwa_added / 0")], "MODEL_EVALUATION_ERROR"),
    ("archive", [(("action",), "verify"), (("archive_document",), MARKER)], "ARCHIVE_MISMATCH"),
]
# fmt: on


@pytest.mark.parametrize(
    "changes,code", [pytest.param(c, k, id=i) for i, c, k in ECHO_CASES]
)
def test_error_details_never_echo_input_text(skill, changes, code):
    result = _run(skill, _changed(_marked(), *changes))
    assert result["status"] == "error"
    assert result["error"]["code"] == code
    text = _canonical(result).lower()
    assert MARKER not in text
    assert "exposure" not in text and "0.08" not in text


def test_unexpected_fault_returns_internal_error(skill, skill_module, monkeypatch):
    def fault(*args):
        raise RuntimeError("synthetic fault " + MARKER)

    monkeypatch.setattr(skill_module, "_archive_document", fault)
    assert _run(skill, _v1()) == {
        "status": "error",
        "error": {
            "code": "INTERNAL_ERROR",
            "detail": {"reason": "internal_error", "exception": "RuntimeError"},
        },
    }

    def interrupt(*args):
        raise KeyboardInterrupt()

    monkeypatch.setattr(skill_module, "_archive_document", interrupt)
    with pytest.raises(KeyboardInterrupt):
        skill.execute(_v1())


REGISTRY_QUANTITY_RE = re.compile(r'"quantities": \[ \{ (.*?) \} \]', re.ASCII)


def test_registry_grammar_marks_exactly_the_optional_quantity_keys(skill):
    """Each quantity key the documented grammar shows without ? is required."""
    with open(os.path.join(BUNDLE_DIR, "instructions.md"), encoding="utf-8") as f:
        shown = REGISTRY_QUANTITY_RE.search(f.read()).group(1)
    keys = re.findall(r'"([a-z_]+)": [A-Z]+(\??)', shown, re.ASCII)
    assert [key for key, _ in keys] == ["name", "unit", "decimals", "expression"]
    for key, optional in keys:
        result = _run(skill, _changed(_v1(), (VER + ("quantities", 0, key), DROP)))
        assert (result["status"] == "completed") == bool(optional), key


def test_manifest_description_states_the_field_path_form(skill, manifest):
    """The description gives a path of names and indexes, as the errors do."""
    description = " ".join(manifest["description"].split())
    assert "a path of names and indexes" in description
    assert "by index" not in description
    _, _, field = _error(_run(skill, _changed(_v1(), (("model_name",), DROP))))
    assert field == "model_name"
    _, _, field = _error(_run(skill, _changed(_v1(), (FILE + ("extra",), 1))))
    assert field == "input_files[0]"
