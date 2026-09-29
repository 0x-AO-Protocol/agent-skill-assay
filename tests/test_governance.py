# SPDX-License-Identifier: MIT
"""Guard the public v0.1 governance and support artifacts."""

from pathlib import Path

import json
import yaml

ROOT = Path(__file__).resolve().parents[1]
GOVERNANCE = ROOT / "governance"


def test_governance_documents_are_published():
    expected = {
        "ACCEPTANCE_CRITERIA.md",
        "CONTRIBUTOR_CONTRACT.md",
        "NORMS.md",
        "DEPRECATION.md",
        "REVIEW_SLA.md",
        "CHANGELOG.md",
    }
    assert expected <= {path.name for path in GOVERNANCE.iterdir()}
    for name in expected:
        content = (GOVERNANCE / name).read_text(encoding="utf-8")
        assert "Version:" in content or name == "CHANGELOG.md"
    assert "[stable]" in (GOVERNANCE / "CONTRIBUTOR_CONTRACT.md").read_text()
    assert "[provisional]" in (GOVERNANCE / "CONTRIBUTOR_CONTRACT.md").read_text()


def test_repository_governance_files_are_present():
    claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "Test discipline" in claude
    assert "Open boundary" in claude
    assert "Secrets" in claude
    codeowners = (ROOT / ".github" / "CODEOWNERS").read_text(encoding="utf-8")
    assert "* @mrmasa88" in codeowners


def test_ci_checks_map_only_stable_norms():
    config = yaml.safe_load((GOVERNANCE / "ci_checks.yaml").read_text(encoding="utf-8"))
    assert config["schema_version"] == "1.0"
    stable = {"N-1", "N-2", "N-3", "N-5"}
    for check in config["checks"]:
        assert check["workflow_job"]
        assert set(check["norms"]) <= stable
    jobs = {
        "build (3.10)",
        "build (3.11)",
        "build (3.12)",
        "wheel-smoke",
        "provenance-check",
        "secret-scan",
        "public-text-lint",
    }
    assert {check["workflow_job"] for check in config["checks"]} == jobs


def test_branch_protection_spec_matches_ci_checks():
    spec = json.loads(
        (GOVERNANCE / "branch_protection.json").read_text(encoding="utf-8")
    )
    contexts = set(spec["required_status_checks"]["contexts"])
    config = yaml.safe_load((GOVERNANCE / "ci_checks.yaml").read_text(encoding="utf-8"))
    jobs = {check["workflow_job"] for check in config["checks"]}
    assert contexts == jobs
    assert spec["enforce_admins"] is True
    assert spec["allow_force_pushes"] is False
    assert spec["allow_deletions"] is False


def test_support_policy_is_machine_readable():
    policy = json.loads((ROOT / "support_policy.json").read_text(encoding="utf-8"))
    assert policy["schema_version"] == "1.0"
    assert policy["project"] == "agent-skill-assay"
    assert policy["supported_releases"] == 2
    assert policy["min_supported"] == "0.1.0"
    assert (
        json.loads(
            (ROOT / "skill_assay" / "support_policy.json").read_text(encoding="utf-8")
        )
        == policy
    )


def test_roadmap_names_the_4_4b_skills():
    roadmap = (ROOT / "ROADMAP.md").read_text(encoding="utf-8")
    for skill in ("outreach_manager", "period_close", "upgrade_gate"):
        assert f"`{skill}`" in roadmap
    assert "not v0.1 catalog entries" in roadmap


def test_r19_evidence_is_public_and_redacted():
    evidence = (ROOT / "docs" / "security" / "firewall-r19-evidence.md").read_text(
        encoding="utf-8"
    )
    assert "MAX_DECODE_DEPTH" in evidence
    assert "MAX_DECODE_BYTES" in evidence
    assert "test_oversized_encoded_payload_fails_closed" in evidence
    assert "decoded attacker text" in evidence
