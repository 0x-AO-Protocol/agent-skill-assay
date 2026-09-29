# SPDX-License-Identifier: MIT
"""R-14 support policy behavior at import, CLI, and wheel boundaries."""

import json
import os
import subprocess
import sys
from pathlib import Path

from packaging.version import Version

from skill_assay import version_policy

ROOT = Path(__file__).resolve().parents[1]


def _run_python(code: str, *, env: dict[str, str] | None = None):
    process_env = os.environ.copy()
    process_env["PYTHONPATH"] = str(ROOT)
    if env:
        process_env.update(env)
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=process_env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_root_and_packaged_policy_are_identical():
    root_policy = json.loads((ROOT / "support_policy.json").read_text(encoding="utf-8"))
    packaged_policy = json.loads(
        (ROOT / "skill_assay" / "support_policy.json").read_text(encoding="utf-8")
    )
    assert root_policy == packaged_policy
    assert root_policy["supported_releases"] == 2


def test_import_emits_warning_outside_support_window():
    result = _run_python("""
import importlib.metadata
import warnings
importlib.metadata.version = lambda _name: "0.0.9"
warnings.simplefilter("always")
import skill_assay
""")
    assert result.returncode == 0
    assert "SupportStatusWarning" in result.stderr
    assert "0.0.9" in result.stderr


def test_cli_emits_warning_outside_support_window():
    result = _run_python("""
import importlib.metadata
import sys
importlib.metadata.version = lambda _name: "0.0.9"
sys.argv = ["skill-assay", "--version"]
from skill_assay.cli import main
main()
""")
    assert result.returncode == 0
    assert "SupportStatusWarning" in result.stderr
    assert "0.0.9" in result.stderr
    assert "skill-assay" in result.stdout


def test_warning_is_silent_inside_support_window():
    result = _run_python("""
import importlib.metadata
import warnings
importlib.metadata.version = lambda _name: "0.1.0"
warnings.simplefilter("always")
import skill_assay
""")
    assert result.returncode == 0
    assert "SupportStatusWarning" not in result.stderr


def test_warning_can_be_suppressed():
    result = _run_python(
        """
import importlib.metadata
import warnings
importlib.metadata.version = lambda _name: "0.0.9"
warnings.simplefilter("always")
import skill_assay
""",
        env={"SKILL_ASSAY_NO_VERSION_CHECK": "1"},
    )
    assert result.returncode == 0
    assert "SupportStatusWarning" not in result.stderr


def test_policy_boundary_is_fail_closed():
    policy = version_policy.load_support_policy()
    assert (
        version_policy.should_emit_unsupported_advisory(
            Version(policy["min_supported"]), policy
        )
        is False
    )
    assert (
        version_policy.should_emit_unsupported_advisory(Version("0.0.9"), policy)
        is True
    )
