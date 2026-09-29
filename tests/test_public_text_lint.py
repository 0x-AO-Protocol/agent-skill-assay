# SPDX-License-Identifier: MIT
"""Public-text and publication-tree hygiene checks."""

import hashlib
from pathlib import Path

import yaml

from scripts import public_text_lint

ROOT = Path(__file__).resolve().parents[1]


def test_public_text_is_clean():
    findings = public_text_lint.scan()
    assert not findings, "\n".join(
        f"{item.path}:{item.line}: {item.rule}: {item.text}" for item in findings
    )


def test_roadmap_contains_only_planned_four_point_four_b_entries():
    text = (ROOT / "ROADMAP.md").read_text(encoding="utf-8")
    manifest = yaml.safe_load(
        (ROOT / "skills/monitoring/kpi_gate/manifest.yaml").read_text(encoding="utf-8")
    )

    for skill in ("outreach_manager", "period_close", "upgrade_gate"):
        row = next(
            line for line in text.splitlines() if line.startswith(f"| `{skill}` |")
        )
        for value in manifest["economic_effect"].values():
            assert value in row
    assert "## Planned skills" in text


def test_hashed_rules_detect_joined_and_separated_tokens(tmp_path, monkeypatch):
    digest = hashlib.sha256(b"alphabeta").hexdigest()
    monkeypatch.setattr(public_text_lint, "ROOT", tmp_path)
    monkeypatch.setattr(public_text_lint, "HASH_RULES", {"synthetic": {digest}})
    sample = tmp_path / "sample.txt"
    sample.write_text(
        "alpha-beta\nalpha_beta\nalpha beta\nalphabeta\n" "notalphabeta\nalpha | beta\n"
    )

    findings = public_text_lint.scan()
    assert [(item.line, item.rule, item.text) for item in findings] == [
        (1, "synthetic", "<redacted>"),
        (2, "synthetic", "<redacted>"),
        (3, "synthetic", "<redacted>"),
        (4, "synthetic", "<redacted>"),
    ]


def test_hashed_rules_detect_compact_numbered_tokens():
    digest = hashlib.sha256(b"alpha2").hexdigest()
    assert digest in public_text_lint._token_hashes("alpha 2")
    assert digest in public_text_lint._token_hashes("alpha2")
    assert digest not in public_text_lint._token_hashes("alpha | 2")
    assert digest not in public_text_lint._token_hashes("alpha" + " " * 20 + "2")
