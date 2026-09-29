"""Machine-readable support policy and runtime support-status reporting."""

from __future__ import annotations

import json
import os
import sys
import warnings
from importlib import metadata
from importlib.resources import files
from typing import Optional

from packaging.version import Version

PACKAGE_NAME = "agent-skill-assay"
SUPPORT_POLICY_RESOURCE = "support_policy.json"


class SupportStatusWarning(UserWarning):
    """The installed runtime is outside its bundled support window."""


def load_support_policy() -> dict:
    """Load the policy shipped with the installed package."""
    try:
        raw = (
            files("skill_assay")
            .joinpath(SUPPORT_POLICY_RESOURCE)
            .read_text(encoding="utf-8")
        )
    except (FileNotFoundError, ModuleNotFoundError):
        raw = (files("skill_assay") / SUPPORT_POLICY_RESOURCE).read_text(
            encoding="utf-8"
        )
    policy = json.loads(raw)
    if policy.get("project") != PACKAGE_NAME:
        raise ValueError("support policy project does not match package name")
    return policy


def is_version_check_disabled() -> bool:
    return os.environ.get("SKILL_ASSAY_NO_VERSION_CHECK", "").strip() == "1"


def get_installed_version() -> Optional[Version]:
    """Return the installed package version, or None for dev/editable/unparseable."""
    try:
        raw = metadata.version(PACKAGE_NAME)
    except metadata.PackageNotFoundError:
        return None
    if not raw or raw == "dev":
        return None
    try:
        return Version(raw)
    except Exception:
        return None


def minimum_supported_version(policy: Optional[dict] = None) -> Version:
    policy = policy or load_support_policy()
    return Version(str(policy["min_supported"]))


def should_emit_unsupported_advisory(
    installed: Version, policy: Optional[dict] = None
) -> bool:
    """Compatibility alias for callers using the pre-R-14 function name."""
    return installed < minimum_supported_version(policy)


def format_unsupported_message(installed: Version) -> str:
    policy = load_support_policy()
    return (
        f"Agent Skill Assay {installed} is unsupported. "
        f"Upgrade to >={policy['min_supported']}: "
        "pip install -U agent-skill-assay"
    )


_warned_versions: set[str] = set()


def emit_support_status_warning() -> None:
    """Emit at most one support warning for an out-of-window installation."""
    if is_version_check_disabled():
        return

    installed = get_installed_version()
    if installed is None:
        return

    policy = load_support_policy()
    if not should_emit_unsupported_advisory(installed, policy):
        return
    key = str(installed)
    if key in _warned_versions:
        return
    _warned_versions.add(key)

    message = (
        f"Agent Skill Assay {installed} is unsupported and outside the "
        f"supported window "
        f"(minimum supported: {policy['min_supported']}; "
        f"security-fix floor: {policy['min_security_fix']}). "
        f"Upgrade to >={policy['min_supported']}."
    )
    warnings.warn(message, SupportStatusWarning, stacklevel=2)
    # Keep the CLI's existing human-readable stderr behavior while exposing
    # the machine-testable warning contract.
    print(message, file=sys.stderr)


def emit_upgrade_advisory() -> None:
    """Backward-compatible entry point used by the CLI."""
    emit_support_status_warning()
