# SPDX-License-Identifier: MIT
"""AO-native manifest fields and static provenance checks."""

import json
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest
import yaml

from scripts.provenance import content_sha256

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads(
    (ROOT / "skill_assay" / "schemas" / "manifest_ao.schema.json").read_text(
        encoding="utf-8"
    )
)

EXPECTED_SOURCES = {
    "security/prompt_injection_firewall": "406444f51930aabffd1da9a9fb3b5f58b85a3415",
    "monitoring/kpi_gate": "acdf991857535108ef05f2f96e0da1b6e9c1ebe7",
    "wellness/mental_coach": "de1c6b0880c90cd4cc6ed2b314b268187a1b4bd6",
    "monitoring/business_diagnostic": "1d5c4fecd7b8e24d2581a46873b95a3e2a96004b",
}

EXPECTED_ECONOMIC_EFFECT = {
    "claim": (
        "Performs the task in deterministic code instead of LLM generation, "
        "reducing LLM token consumption."
    ),
    "baseline_task": (
        "The same task performed by an LLM agent without this skill " "(prompt-only)."
    ),
    "metric": "LLM tokens (input + output) per task run.",
    "method": (
        "Run the skill's reference fixtures through (a) the baseline agent and "
        "(b) the same agent calling the skill; publish the median tokens per "
        "run for both and the reduction."
    ),
}


def test_four_bundles_have_ao_manifest_contract():
    for skill_id, source_sha in EXPECTED_SOURCES.items():
        bundle = ROOT / "skills" / skill_id
        manifest = yaml.safe_load(
            (bundle / "manifest.yaml").read_text(encoding="utf-8")
        )
        jsonschema.validate(manifest, SCHEMA)
        assert manifest["trust_tier"] == "L0"
        assert manifest["issuer"]["org"] == "AO"
        assert manifest["provenance"]["source"].startswith(
            f"agent-skill-assay-sources@{source_sha}:"
        )
        assert manifest["provenance"]["content_sha256"] == content_sha256(bundle)
        assert manifest["economic_effect"] == EXPECTED_ECONOMIC_EFFECT
        assert manifest["norm_version"] == "norms-1.0.0"


def test_template_declares_the_ao_fields():
    manifest = yaml.safe_load(
        (ROOT / "templates" / "python_skill" / "manifest.yaml").read_text(
            encoding="utf-8"
        )
    )
    for field in ("trust_tier", "provenance", "economic_effect", "norm_version"):
        assert field in manifest


def _minimal_manifest(provenance):
    return {
        "name": "category/skill_name",
        "version": "0.1.0",
        "issuer": {"org": "AO", "name": "Someone", "github": "someone"},
        "trust_tier": "L0",
        "provenance": provenance,
        "norm_version": "norms-1.0.0",
    }


def _archive_block():
    return {
        "source": "agent-skill-assay-sources@0000000:skills/category/skill_name",
        "content_sha256": "0" * 64,
        "ported_on": "2026-09-25",
    }


def _repository_block():
    return {
        "anchor": "repository",
        "content_sha256": "0" * 64,
        "authored_on": "2026-10-08",
    }


FORMAT_CHECKER = jsonschema.FormatChecker()

_PROVENANCE_FORMS = SCHEMA["properties"]["provenance"]["oneOf"]
REPOSITORY_FORM_INDEX = next(
    index
    for index, form in enumerate(_PROVENANCE_FORMS)
    if "anchor" in form["required"]
)
ARCHIVE_FORM_INDEX = next(
    index
    for index, form in enumerate(_PROVENANCE_FORMS)
    if "source" in form["required"]
)


def _is_valid(manifest) -> bool:
    try:
        jsonschema.validate(manifest, SCHEMA, format_checker=FORMAT_CHECKER)
    except jsonschema.ValidationError:
        return False
    return True


def _schema_failures(error):
    """Yield ``(field, message)`` pairs that name the field behind a schema error."""
    path = list(error.absolute_path)
    if error.validator == "oneOf" and path == ["provenance"]:
        chosen = (
            REPOSITORY_FORM_INDEX
            if isinstance(error.instance, dict) and "anchor" in error.instance
            else ARCHIVE_FORM_INDEX
        )
        for cause in error.context:
            if cause.relative_schema_path[0] == chosen:
                yield from _schema_failures(cause)
        return
    if error.validator == "required" and isinstance(error.instance, dict):
        for name in error.validator_value:
            if name not in error.instance:
                field = ".".join(str(part) for part in path + [name])
                yield field, f"{name!r} is a required property"
        return
    yield ".".join(str(part) for part in path) or "<root>", error.message


def ao_manifest_failures(skills_root: Path) -> list[str]:
    """Return one failure string per violated AO manifest rule under a skills root.

    Every failure names the bundle and the field it concerns.
    """
    validator = jsonschema.Draft202012Validator(SCHEMA, format_checker=FORMAT_CHECKER)
    failures: list[str] = []
    for manifest_path in sorted(skills_root.glob("*/*/manifest.yaml")):
        bundle = manifest_path.parent
        bundle_id = bundle.relative_to(skills_root).as_posix()
        try:
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            reason = getattr(exc, "problem", None) or str(exc).splitlines()[0]
            failures.append(f"{bundle_id}: <root>: invalid YAML: {reason}")
            continue
        if not isinstance(manifest, dict):
            failures.append(f"{bundle_id}: <root>: not a mapping")
            continue
        seen: set[tuple[str, str]] = set()
        for error in validator.iter_errors(manifest):
            for field, message in _schema_failures(error):
                if (field, message) in seen:
                    continue
                seen.add((field, message))
                failures.append(f"{bundle_id}: {field}: {message}")
        issuer = manifest.get("issuer")
        if not isinstance(issuer, dict):
            issuer = {}
        if issuer.get("org") != "AO":
            failures.append(f"{bundle_id}: issuer.org: {issuer.get('org')!r}")
        if manifest.get("norm_version") != "norms-1.0.0":
            failures.append(
                f"{bundle_id}: norm_version: {manifest.get('norm_version')!r}"
            )
        if manifest.get("economic_effect") != EXPECTED_ECONOMIC_EFFECT:
            failures.append(
                f"{bundle_id}: economic_effect: differs from the common text"
            )
        provenance = manifest.get("provenance")
        if not isinstance(provenance, dict):
            provenance = {}
        recorded = provenance.get("content_sha256")
        actual = content_sha256(bundle)
        if recorded != actual:
            failures.append(
                f"{bundle_id}: provenance.content_sha256: "
                f"recorded={recorded!r}, actual={actual}"
            )
    return failures


def _write_bundle(skills_root: Path, manifest) -> Path:
    bundle = skills_root / "category" / "skill_name"
    bundle.mkdir(parents=True)
    (bundle / "skill.py").write_text("VALUE = 1\n", encoding="utf-8")
    (bundle / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )
    return bundle


def _repository_anchored_manifest(skills_root: Path):
    """Write a bundle with a correct repository-anchored block; return its manifest."""
    manifest = _minimal_manifest(_repository_block())
    manifest["economic_effect"] = dict(EXPECTED_ECONOMIC_EFFECT)
    bundle = _write_bundle(skills_root, manifest)
    manifest["provenance"]["content_sha256"] = content_sha256(bundle)
    (bundle / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )
    return manifest


def _rewrite_manifest(skills_root: Path, manifest) -> None:
    (skills_root / "category" / "skill_name" / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )


def test_schema_accepts_the_archive_provenance_form():
    assert _is_valid(_minimal_manifest(_archive_block()))


def test_schema_accepts_the_repository_anchored_provenance_form():
    assert _is_valid(_minimal_manifest(_repository_block()))


def test_schema_rejects_repository_anchor_without_content_hash():
    block = _repository_block()
    del block["content_sha256"]
    assert not _is_valid(_minimal_manifest(block))


def test_schema_rejects_repository_anchor_with_an_archive_pointer():
    with_source = _repository_block()
    with_source["source"] = _archive_block()["source"]
    assert not _is_valid(_minimal_manifest(with_source))

    with_ported_on = _repository_block()
    with_ported_on["ported_on"] = "2026-09-25"
    assert not _is_valid(_minimal_manifest(with_ported_on))

    archive_with_anchor = _archive_block()
    archive_with_anchor["anchor"] = "repository"
    assert not _is_valid(_minimal_manifest(archive_with_anchor))


@pytest.mark.parametrize(
    "block",
    [
        pytest.param(
            {**_repository_block(), "content_sha256": "0" * 63},
            id="hash-wrong-length",
        ),
        pytest.param(
            {**_repository_block(), "content_sha256": "A" * 64},
            id="hash-upper-case",
        ),
        pytest.param(
            {**_repository_block(), "authored_on": "2026/10/08"},
            id="bad-authored-on",
        ),
        pytest.param({**_repository_block(), "anchor": "archive"}, id="bad-anchor"),
        pytest.param({"content_sha256": "0" * 64}, id="neither-form"),
        pytest.param(
            {**_repository_block(), "authored_on": "2026-02-31"},
            id="impossible-authored-on",
        ),
        pytest.param(
            {**_repository_block(), "authored_on": "\uff12\uff10\uff12\uff16-10-08"},
            id="non-ascii-digits-authored-on",
        ),
        pytest.param(
            {**_archive_block(), "ported_on": "yesterday"},
            id="archive-ported-on-not-a-date",
        ),
    ],
)
def test_schema_rejects_malformed_provenance(block):
    assert not _is_valid(_minimal_manifest(block))


def test_every_bundle_meets_the_ao_manifest_contract():
    assert ao_manifest_failures(ROOT / "skills") == []


def test_ao_contract_check_passes_a_valid_repository_anchored_bundle(tmp_path):
    skills_root = tmp_path / "skills"
    _repository_anchored_manifest(skills_root)
    assert ao_manifest_failures(skills_root) == []


def _set_tier_outside_enum(manifest):
    manifest["trust_tier"] = "L1"


def _set_wrong_issuer_org(manifest):
    manifest["issuer"]["org"] = "Someone Else"


def _set_wrong_norm_version(manifest):
    manifest["norm_version"] = "norms-0.9.0"


def _alter_economic_effect(manifest):
    manifest["economic_effect"]["claim"] = "A different claim."


def _null_economic_effect(manifest):
    manifest["economic_effect"] = None


def _drop_content_hash(manifest):
    del manifest["provenance"]["content_sha256"]


def _set_wrong_content_hash(manifest):
    manifest["provenance"]["content_sha256"] = "f" * 64


def _drop_trust_tier(manifest):
    del manifest["trust_tier"]


def _set_malformed_authored_on(manifest):
    manifest["provenance"]["authored_on"] = "2026/10/08"


def _set_impossible_authored_on(manifest):
    manifest["provenance"]["authored_on"] = "2026-02-31"


def _set_issuer_to_string(manifest):
    manifest["issuer"] = "AO"


def _set_provenance_to_string(manifest):
    manifest["provenance"] = "repository"


@pytest.mark.parametrize(
    ("change", "field"),
    [
        pytest.param(_set_tier_outside_enum, "trust_tier", id="tier-outside-enum"),
        pytest.param(_set_wrong_issuer_org, "issuer.org", id="issuer-org"),
        pytest.param(_set_wrong_norm_version, "norm_version", id="norm-version"),
        pytest.param(
            _alter_economic_effect, "economic_effect", id="economic-effect-altered"
        ),
        pytest.param(
            _null_economic_effect, "economic_effect", id="economic-effect-null"
        ),
        pytest.param(
            _drop_content_hash, "provenance.content_sha256", id="missing-hash"
        ),
        pytest.param(
            _set_wrong_content_hash, "provenance.content_sha256", id="wrong-hash"
        ),
        pytest.param(_drop_trust_tier, "trust_tier", id="missing-tier"),
        pytest.param(
            _set_malformed_authored_on,
            "provenance.authored_on",
            id="malformed-authored-on",
        ),
        pytest.param(
            _set_impossible_authored_on,
            "provenance.authored_on",
            id="impossible-authored-on",
        ),
        pytest.param(_set_issuer_to_string, "issuer", id="issuer-not-a-mapping"),
        pytest.param(
            _set_provenance_to_string, "provenance", id="provenance-not-a-mapping"
        ),
    ],
)
def test_ao_contract_check_flags_each_wrong_field(tmp_path, change, field):
    skills_root = tmp_path / "skills"
    manifest = _repository_anchored_manifest(skills_root)
    assert ao_manifest_failures(skills_root) == []

    change(manifest)
    _rewrite_manifest(skills_root, manifest)
    failures = ao_manifest_failures(skills_root)
    assert failures
    assert any(f": {field}: " in failure for failure in failures), failures


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("- a\n- b\n", id="list"),
        pytest.param("name: [unclosed\n", id="malformed-yaml"),
    ],
)
def test_ao_contract_check_reports_a_manifest_that_is_not_a_mapping(tmp_path, text):
    skills_root = tmp_path / "skills"
    _repository_anchored_manifest(skills_root)
    (skills_root / "category" / "skill_name" / "manifest.yaml").write_text(
        text, encoding="utf-8"
    )
    failures = ao_manifest_failures(skills_root)
    assert any(": <root>: " in failure for failure in failures), failures


def test_archive_form_is_used_only_by_the_pinned_bundles():
    skills_root = ROOT / "skills"
    archive_anchored = set()
    for manifest_path in skills_root.glob("*/*/manifest.yaml"):
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        if "source" in (manifest.get("provenance") or {}):
            archive_anchored.add(
                manifest_path.parent.relative_to(skills_root).as_posix()
            )
    assert archive_anchored == set(EXPECTED_SOURCES)


def test_template_shows_the_repository_anchor():
    manifest = yaml.safe_load(
        (ROOT / "templates" / "python_skill" / "manifest.yaml").read_text(
            encoding="utf-8"
        )
    )
    block = manifest["provenance"]
    assert block["anchor"] == "repository"
    assert "authored_on" in block
    assert "source" not in block
    assert "ported_on" not in block
    assert set(block) == {"anchor", "content_sha256", "authored_on"}


def test_provenance_hash_mode_prints_the_recorded_hash():
    script = ROOT / "scripts" / "provenance.py"
    for skill_id in EXPECTED_SOURCES:
        bundle = ROOT / "skills" / skill_id
        manifest = yaml.safe_load(
            (bundle / "manifest.yaml").read_text(encoding="utf-8")
        )
        result = subprocess.run(
            [sys.executable, str(script), "--hash", str(bundle)],
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout == manifest["provenance"]["content_sha256"] + "\n"


def test_provenance_hash_mode_hashes_the_current_bundle_content(tmp_path):
    skills_root = tmp_path / "skills"
    manifest = _repository_anchored_manifest(skills_root)
    bundle = skills_root / "category" / "skill_name"
    (bundle / "skill.py").write_text("VALUE = 2\n", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "provenance.py"),
            "--hash",
            str(bundle),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout == content_sha256(bundle) + "\n"
    assert result.stdout != manifest["provenance"]["content_sha256"] + "\n"
