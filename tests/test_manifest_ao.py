# SPDX-License-Identifier: MIT
"""AO-native manifest fields and static provenance checks."""

import json
from pathlib import Path

import jsonschema
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
