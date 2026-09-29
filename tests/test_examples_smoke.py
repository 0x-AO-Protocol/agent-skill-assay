"""CI smoke tests for the currently ported AO local examples."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLES_DIR = REPO_ROOT / "examples"

LOCAL_EXECUTE_SMOKE_SCRIPTS: List[Tuple[str, List[str]]] = [
    (
        "mental_coach_demo.py",
        ["wellness/mental_coach", "Coaching", "Crisis escalation", "policy_status:"],
    ),
    (
        "prompt_injection_firewall_demo.py",
        ["security/prompt_injection_firewall", "Hidden HTML override", "is_safe:"],
    ),
    (
        "kpi_gate_demo.py",
        [
            "monitoring/kpi_gate",
            "NO_BOOKING",
            "insufficient_data:",
            "fail-closed",
            "Demo complete.",
        ],
    ),
    (
        "business_diagnostic_demo.py",
        [
            "monitoring/business_diagnostic",
            "insufficient_data:",
            "fail-closed",
            "Demo complete.",
        ],
    ),
]


@pytest.mark.parametrize("script_name,expected_markers", LOCAL_EXECUTE_SMOKE_SCRIPTS)
def test_local_execute_example_smoke(
    script_name: str, expected_markers: List[str]
) -> None:
    """Execute local demo scripts and verify clean exit and output markers."""
    script_path = EXAMPLES_DIR / script_name
    assert script_path.is_file(), f"Example script not found: {script_path}"

    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)
    env.setdefault("SKILL_ASSAY_CONFIG_DIR", str(REPO_ROOT / "tests" / "fixtures"))

    proc = subprocess.run(
        [sys.executable, str(script_path)],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
    )

    assert proc.returncode == 0, (
        f"Example {script_name} failed with return code {proc.returncode}.\n"
        f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    )

    for marker in expected_markers:
        assert marker in proc.stdout, (
            f"Expected output marker {marker!r} missing from {script_name} output.\n"
            f"STDOUT:\n{proc.stdout}"
        )


def test_every_example_script_has_smoke_classification() -> None:
    """Every remaining example must be covered by the smoke list."""
    smoke_names = {item[0] for item in LOCAL_EXECUTE_SMOKE_SCRIPTS}
    all_example_scripts = {path.name for path in EXAMPLES_DIR.glob("*.py")}
    assert all_example_scripts == smoke_names
