"""Validate .github/labels.json matches repository label policy."""

import json
import re
from pathlib import Path

LABELS_PATH = Path(__file__).resolve().parent.parent / ".github" / "labels.json"
HEX_COLOR = re.compile(r"^[0-9A-Fa-f]{6}$")
GITHUB_LABEL_DESCRIPTION_MAX = 100

CATEGORY_LABEL_PREFIX = "cat: "
CATEGORY_LABEL_COLOR = "E6D9F5"
REPO_ROOT = LABELS_PATH.parent.parent


def _registry_categories() -> tuple[str, ...]:
    """Registered categories: the generated table of CONTRIBUTING.md is the source."""
    from scripts import registry_sync

    return tuple(sorted(registry_sync.registered_categories(REPO_ROOT)))


def _bare_category_names_forbidden_as_repo_labels() -> set[str]:
    # Repo-wide `security` is intentional (vulnerabilities/trust model); the
    # category uses `cat: security`.
    return set(_registry_categories()) - {"security"}


REQUIRED_LABELS = {
    "bug",
    "documentation",
    "enhancement",
    "good first issue",
    "help wanted",
    "skill request",
    "skill upgrade",
    "core framework",
    "cli",
    "examples",
    "testing",
    "packaging",
    "ci",
    "security",
    "discussion",
    "question",
    "wontfix",
}


def _load_labels() -> list[dict]:
    return json.loads(LABELS_PATH.read_text(encoding="utf-8"))


def _label_map() -> dict[str, dict]:
    return {entry["name"]: entry for entry in _load_labels()}


def test_labels_json_exists_and_is_array():
    labels = _load_labels()
    assert isinstance(labels, list)
    assert len(labels) >= len(REQUIRED_LABELS)


def test_required_labels_present():
    names = set(_label_map())
    missing = REQUIRED_LABELS - names
    assert not missing, f"Missing labels in labels.json: {sorted(missing)}"


def test_label_entries_well_formed():
    names: set[str] = set()
    for entry in _load_labels():
        name = entry.get("name")
        color = entry.get("color", "")
        description = entry.get("description", "")
        assert name and isinstance(name, str)
        assert name not in names, f"Duplicate label: {name}"
        names.add(name)
        assert HEX_COLOR.match(color), f"Bad color for {name}: {color!r}"
        assert description and isinstance(description, str)
        assert len(description) <= GITHUB_LABEL_DESCRIPTION_MAX, (
            f"Label {name!r} description is {len(description)} chars; "
            f"GitHub maximum is {GITHUB_LABEL_DESCRIPTION_MAX}"
        )


def test_rfc_template_labels_exist():
    """Issue template 05_rfc.yml references discussion and core framework."""
    names = set(_label_map())
    assert "discussion" in names
    assert "core framework" in names


def test_registry_category_labels_present():
    """Every registry category has a matching cat: label."""
    names = set(_label_map())
    expected = {
        f"{CATEGORY_LABEL_PREFIX}{category}" for category in _registry_categories()
    }
    missing = expected - names
    assert not missing, f"Missing category labels in labels.json: {sorted(missing)}"


def test_category_labels_share_color_and_prefix():
    """Category labels use cat: prefix and one shared pastel color."""
    labels = _label_map()
    category_labels = [
        labels[f"{CATEGORY_LABEL_PREFIX}{category}"]
        for category in _registry_categories()
    ]
    colors = {entry["color"].upper() for entry in category_labels}
    assert colors == {
        CATEGORY_LABEL_COLOR
    }, f"All cat: labels must use #{CATEGORY_LABEL_COLOR}; got {sorted(colors)}"
    for category in _registry_categories():
        name = f"{CATEGORY_LABEL_PREFIX}{category}"
        assert name in labels
        assert labels[name]["name"].startswith(CATEGORY_LABEL_PREFIX)


def test_category_labels_do_not_collide_with_repo_labels():
    """Bare category folder names must not be repo-wide labels (except security)."""
    names = set(_label_map())
    collisions = _bare_category_names_forbidden_as_repo_labels() & names
    assert not collisions, (
        f"Registry category names must use cat: prefix, not repo-wide labels: "
        f"{sorted(collisions)}"
    )
    assert "security" in names
    assert f"{CATEGORY_LABEL_PREFIX}security" in names


def test_category_labels_are_exactly_the_registered_categories():
    """The cat: labels are generated from the registered categories, no more."""
    names = {name for name in _label_map() if name.startswith(CATEGORY_LABEL_PREFIX)}
    assert names == {
        f"{CATEGORY_LABEL_PREFIX}{category}" for category in _registry_categories()
    }


def test_every_category_directory_with_a_bundle_is_registered():
    skills_root = REPO_ROOT / "skills"
    with_bundles = {
        path.parent.parent.name for path in skills_root.glob("*/*/manifest.yaml")
    }
    assert with_bundles
    assert with_bundles <= set(_registry_categories())
