#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Generate and check the registry's shared files from the bundles under skills/.

  --write                 regenerate every generated block and file
  --check                 exit 1 on any difference or rule violation, naming the block
  --add-category NAME --purpose TEXT   register a category, then --write
  --release-check VERSION run by the release step: versions, changelog, no Unreleased

Generated blocks sit between <!-- registry-sync:begin NAME --> and
<!-- registry-sync:end NAME -->. The registered categories are the rows of the
CONTRIBUTING.md block "categories"; registry_sync also owns the "cat: " entries of
.github/labels.json, the generated extras block of pyproject.toml and each
category's empty __init__.py. Output is sorted and has no timestamps.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from skill_assay.core.discovery import list_registry_skill_ids  # noqa: E402
from skill_assay.core.extras import (  # noqa: E402
    GENERATED_BEGIN,
    GENERATED_END,
    collect_category_requirements,
    collect_skill_requirements,
    registry_id_to_extra,
    render_generated_block,
)

# The four bundles of the first release: the frozen "v0.1" table of ROADMAP.md.
FIRST_RELEASE_BUNDLES = frozenset(
    {
        "security/prompt_injection_firewall",
        "monitoring/kpi_gate",
        "wellness/mental_coach",
        "monitoring/business_diagnostic",
    }
)
# The catalog-index descriptions of the seven bundles shipped by v0.1.3 (the legacy
# set of scripts/determinism_guard.py), frozen verbatim. Every other bundle's
# description is the first sentence of its manifest short_description.
LEGACY_DESCRIPTIONS = {
    "security/prompt_injection_firewall": (
        "Detects prompt-injection patterns before model execution."
    ),
    "monitoring/business_diagnostic": "Computes deterministic diagnostic ledger outputs.",
    "monitoring/kpi_gate": "Evaluates a metrics snapshot against a declared policy.",
    "wellness/mental_coach": "Provides grounded, non-clinical wellness support.",
    "office/outreach_manager": (
        "Plans partitioned platform searches and scores exported lead rows."
    ),
    "finance/period_close": (
        "Closes a period from a journal into statements, checks and gap notes."
    ),
    "dev_tools/upgrade_gate": (
        "Assesses pinned dependencies against release snapshots: hold, upgrade or test."
    ),
}
RESERVED_EXTRAS = frozenset(
    {"all", "dev", "gemini", "claude", "openai", "bedrock", "agents"}
)
CATEGORY_NAME = re.compile(r"[a-z][a-z0-9_]*")
CATEGORY_NAME_MAX = 30
LABEL_PREFIX, LABEL_COLOR = "cat: ", "E6D9F5"
LABEL_NAME_MAX, LABEL_DESCRIPTION_MAX = 50, 100
PLANNED_SENTENCE = (
    "Standard skills are proposed as skill requests and are listed here only "
    "after their release."
)
PLACEHOLDER_IDS = frozenset({"category/skill_name"})
CATALOG_LINK = "](../skills/README.md)"
BLOCK_FILES = {
    "CONTRIBUTING.md": ("categories",),
    "docs/skills/README.md": ("catalog-index",),
    "ROADMAP.md": ("releases", "planned-skills"),
    "docs/usage/install_extras.md": ("category-extras", "skill-extras"),
}
RELEASE = re.compile(
    r"## \[(\d+\.\d+\.\d+)\] - \d{4}-\d{2}-\d{2}[ \t]*$", re.M | re.ASCII
)
ADDED_ID = re.compile(r"^- `([a-z][a-z0-9_]*/[a-z][a-z0-9_]*)`:", re.M)
TABLE_HEAD = "| :--- | :--- | :--- |\n"


class SyncError(Exception):
    pass


def _markers(name: str) -> tuple[str, str]:
    return (
        f"<!-- registry-sync:begin {name} -->\n",
        f"<!-- registry-sync:end {name} -->",
    )


def _block(text: str, name: str) -> str | None:
    begin, end = _markers(name)
    if (
        text.count(begin) != 1
        or text.count(end) != 1
        or text.index(begin) > text.index(end)
    ):
        return None
    return text.split(begin, 1)[1].split(end, 1)[0]


def _replace(text: str, name: str, body: str) -> str:
    begin, end = _markers(name)
    head, rest = text.split(begin, 1)
    return head + begin + body + end + rest.split(end, 1)[1]


def _read(root: Path, relative: str) -> str:
    return (root / relative).read_text(encoding="utf-8")


def registered_categories(root: Path = REPO_ROOT) -> dict[str, str]:
    """Category -> purpose, from the generated table of CONTRIBUTING.md."""
    body = _block(_read(root, "CONTRIBUTING.md"), "categories")
    if body is None:
        raise SyncError("CONTRIBUTING.md: missing block categories")
    categories = {}
    for line in body.splitlines()[2:]:
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 3 or not re.fullmatch(r"`[^`]+`", cells[0]):
            raise SyncError(f"CONTRIBUTING.md: malformed category row: {line}")
        categories[cells[0].strip("`")] = cells[1]
    return categories


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def changelog_releases(text: str) -> dict[str, str]:
    """Bundle id -> the earliest released version whose Added list names it."""
    releases: dict[str, str] = {}
    for section in re.split(r"^(?=## )", text, flags=re.M):
        match = RELEASE.match(section)
        added = re.search(r"^### Added[ \t]*$(.*?)(?=^### |\Z)", section, re.M | re.S)
        if not match or not added:
            continue
        for bundle_id in ADDED_ID.findall(added.group(1)):
            known = releases.get(bundle_id)
            if known is None or _version_key(match.group(1)) < _version_key(known):
                releases[bundle_id] = match.group(1)
    return releases


def _manifests(root: Path, problems: list[str]) -> dict[str, dict]:
    manifests = {}
    for bundle_id in list_registry_skill_ids(root / "skills"):
        try:
            data = yaml.safe_load(_read(root, f"skills/{bundle_id}/manifest.yaml"))
        except Exception as exc:
            problems.append(
                f"{bundle_id}: cannot read manifest.yaml ({type(exc).__name__})"
            )
            continue
        manifests[bundle_id] = data if isinstance(data, dict) else {}
    return manifests


def dwa_collisions(root: Path = REPO_ROOT) -> list[str]:
    """Detailed Work Activities served by more than one bundle of origin ao_standard."""
    served: dict[str, list[str]] = {}
    for bundle_id, manifest in _manifests(root, []).items():
        dwa = manifest.get("serves_dwa")
        if manifest.get("origin") != "ao_standard" or not isinstance(dwa, dict):
            continue
        for dwa_id in dwa.get("ids") or []:
            served.setdefault(str(dwa_id), []).append(bundle_id)
    return [
        f"{dwa_id}: served by {', '.join(sorted(ids))}"
        for dwa_id, ids in sorted(served.items())
        if len(ids) > 1
    ]


def agent_loops_failures(text: str, manifested) -> list[str]:
    """The agent-loops guide links the catalog index and names only registered ids."""
    failures = []
    if CATALOG_LINK not in text:
        failures.append("missing link to the catalog index (../skills/README.md)")
    named = set(re.findall(r"`([\w-]+/[\w-]+)`", text, re.ASCII)) - set(manifested)
    named -= PLACEHOLDER_IDS
    failures.extend(f"unknown skill id: {skill_id}" for skill_id in sorted(named))
    return failures


def _category_problems(name: str, existing_extras: set[str]) -> list[str]:
    problems = []
    shape = CATEGORY_NAME.fullmatch(name) and "__" not in name
    if not shape or len(name) > CATEGORY_NAME_MAX:
        problems.append(
            f"invalid category name: {name!r} ([a-z][a-z0-9_]*, no __, <= 30)"
        )
    label = len(LABEL_PREFIX + name) > LABEL_NAME_MAX
    if label or len(_label_description(name)) > LABEL_DESCRIPTION_MAX:
        problems.append(f"category {name}: label exceeds the GitHub limits (50 / 100)")
    if name in RESERVED_EXTRAS:
        problems.append(f"category {name}: reserved extras name")
    elif name in existing_extras:
        problems.append(f"extras name collision: {name}")
    return problems


def _label_description(category: str) -> str:
    return f"Registry skill category — {category} (`skills/{category}/`)."


def _title(category: str) -> str:
    return " ".join(part.capitalize() for part in category.split("_"))


def _first_sentence(text: str) -> str:
    text = " ".join(str(text).split())
    match = re.match(r"(.+?[.!?])(?=\s|$)", text, re.ASCII)
    return (match.group(1) if match else text).replace("|", "\\|")


def _packages(requirements) -> str:
    return ", ".join(f"`{req}`" for req in requirements) or "*(none today)*"


def _table(header: list[str], rows) -> str:
    head = f"| {' | '.join(header)} |\n" + "| :--- " * len(header) + "|\n"
    return head + "".join(f"| {' | '.join(row)} |\n" for row in rows)


def _page_title(page: Path) -> str | None:
    titles = (
        re.findall(r"^# (.+)$", page.read_text("utf-8"), re.M) if page.is_file() else []
    )
    return titles[0] if titles else None


def _render(
    root: Path, categories: dict[str, str], manifests, problems
) -> dict[str, str]:
    """Return the body of every generated block, keyed by block name."""
    by_category: dict[str, list[str]] = {}
    for bundle_id in sorted(manifests):
        by_category.setdefault(bundle_id.split("/")[0], []).append(bundle_id)
    for category in sorted(set(by_category) - set(categories)):
        problems.append(f"unregistered category: {category} (run --add-category)")
    short_names: dict[str, list[str]] = {}
    for bundle_id in manifests:
        short_names.setdefault(bundle_id.split("/")[1], []).append(bundle_id)
    for name, ids in sorted(short_names.items()):
        if len(ids) > 1:
            problems.append(f"short name collision: {name} ({', '.join(sorted(ids))})")
    extras = [*RESERVED_EXTRAS, *categories, *map(registry_id_to_extra, manifests)]
    for name in sorted({name for name in extras if extras.count(name) > 1}):
        problems.append(f"extras name collision: {name}")
    for category in categories:
        problems.extend(
            p for p in _category_problems(category, set()) if "collision" not in p
        )
    problems.extend(dwa_collisions(root))
    sections = []
    for category, ids in sorted(by_category.items()):
        rows = []
        for bundle_id in ids:
            manifest, name = manifests[bundle_id], bundle_id.split("/")[1]
            title = _page_title(root / "docs" / "skills" / f"{name}.md")
            if title is None:
                problems.append(
                    f"{bundle_id}: missing catalog page title docs/skills/{name}.md"
                )
            short = manifest.get("short_description")
            if not short:
                problems.append(f"{bundle_id}: missing short_description")
            issuer = (
                manifest["issuer"] if isinstance(manifest.get("issuer"), dict) else {}
            )
            org, version = issuer.get("org") or issuer.get("name"), manifest.get(
                "version"
            )
            rows.append(
                [f"[{title}]({name}.md)", f"`{bundle_id}`", f"`{version}`", str(org)]
            )
            if bundle_id in LEGACY_DESCRIPTIONS:
                rows[-1].append(LEGACY_DESCRIPTIONS[bundle_id])
            else:
                rows[-1].append(_first_sentence(short or ""))
        columns = ["Skill", "ID", "Version", "Issuer", "Description"]
        sections.append(f"## {_title(category)}\n\n" + _table(columns, rows))
    releases = changelog_releases(_read(root, "CHANGELOG.md"))
    later = [b for b in manifests if b not in FIRST_RELEASE_BUNDLES]
    later.sort(
        key=lambda b: (b not in releases, _version_key(releases.get(b, "0.0.0")), b)
    )
    skill_reqs = collect_skill_requirements(root / "skills")
    category_reqs = collect_category_requirements(skill_reqs)

    def count(c):
        n = len(by_category.get(c, []))
        return (
            f"[{n}](docs/skills/README.md#{_title(c).lower().replace(' ', '-')})"
            if n
            else "0"
        )

    return {
        "catalog-index": "\n".join(sections),
        "releases": _table(
            ["Skill", "Release", "Status"],
            [
                [
                    f"`{b}`",
                    f"v{releases[b]}" if b in releases else "Unreleased",
                    "Registered",
                ]
                for b in later
            ],
        ),
        "planned-skills": PLANNED_SENTENCE + "\n",
        "category-extras": _table(
            ["Extra", "Skills", "Packages"],
            [
                [
                    f"`{c}`",
                    ", ".join(f"`{b}`" for b in ids),
                    _packages(category_reqs.get(c, [])),
                ]
                for c, ids in sorted(by_category.items())
            ],
        ),
        "skill-extras": _table(
            ["Extra", "Registry ID", "Packages"],
            [
                [
                    f"`{registry_id_to_extra(b)}`",
                    f"`{b}`",
                    _packages(skill_reqs.get(b, [])),
                ]
                for b in sorted(manifests)
            ],
        ),
        "categories": _table(
            ["Category", "Purpose", "Skills"],
            [[f"`{c}`", categories[c], count(c)] for c in sorted(categories)],
        ),
    }


def plan(root: Path, extra_categories: dict[str, str] | None = None):
    """Return (expected content per relative path, problems). Nothing is written."""
    problems: list[str] = []
    try:
        categories = {**registered_categories(root), **(extra_categories or {})}
    except SyncError as exc:
        return {}, [str(exc)]
    manifests = _manifests(root, problems)
    bodies = _render(root, categories, manifests, problems)
    expected: dict[str, str] = {}
    for relative, names in BLOCK_FILES.items():
        text = _read(root, relative)
        for name in names:
            if _block(text, name) is None:
                problems.append(f"{relative}: missing block {name}")
            else:
                text = _replace(text, name, bodies[name])
        expected[relative] = text
    labels = json.loads(_read(root, ".github/labels.json"))
    labels = [entry for entry in labels if not entry["name"].startswith(LABEL_PREFIX)]
    for c in sorted(categories):
        labels.append(
            {
                "name": LABEL_PREFIX + c,
                "color": LABEL_COLOR,
                "description": _label_description(c),
            }
        )
    expected[".github/labels.json"] = (
        json.dumps(labels, indent=4, ensure_ascii=False) + "\n"
    )
    pyproject = _read(root, "pyproject.toml")
    if GENERATED_BEGIN not in pyproject or GENERATED_END not in pyproject:
        problems.append("pyproject.toml: missing the generated extras markers")
    else:
        before, rest = pyproject.split(GENERATED_BEGIN, 1)
        generated = render_generated_block(root / "skills")
        expected["pyproject.toml"] = (
            f"{before.rstrip()}\n\n{generated}" + rest.split(GENERATED_END, 1)[1]
        )
    for category in sorted({bundle_id.split("/")[0] for bundle_id in manifests}):
        expected[f"skills/{category}/__init__.py"] = None  # created empty when missing
    for category in sorted(extra_categories or {}):
        # A category registered before its first bundle gets its package marker too.
        expected.setdefault(f"skills/{category}/__init__.py", None)
    loops = root / "docs" / "usage" / "agent_loops.md"
    if loops.is_file():
        for failure in agent_loops_failures(loops.read_text("utf-8"), manifests):
            problems.append(f"docs/usage/agent_loops.md: {failure}")
    return expected, problems


def _stale(root: Path, expected: dict[str, str]) -> list[str]:
    stale = []
    for relative, content in expected.items():
        path = root / relative
        if content is None:
            if not path.is_file():
                stale.append(f"{relative}: missing")
            continue
        current = path.read_text(encoding="utf-8") if path.is_file() else ""
        if current == content:
            continue
        if relative in BLOCK_FILES:
            stale.extend(
                f"{relative}: block {name} is out of date"
                for name in BLOCK_FILES[relative]
                if _block(current, name) != _block(content, name)
            )
        else:
            what = (
                "category labels"
                if relative.endswith("labels.json")
                else "extras block"
            )
            stale.append(
                f"{relative}: {what} are out of date".replace("block are", "block is")
            )
    return stale


def _report(lines: list[str]) -> None:
    for line in lines:
        print(f"registry-sync: {line}", file=sys.stderr)


def check(root: Path) -> int:
    expected, problems = plan(root)
    stale = [] if problems else _stale(root, expected)
    if problems or stale:
        _report(problems + stale + ["run: python scripts/registry_sync.py --write"])
        return 1
    print("registry-sync: OK")
    return 0


def write(root: Path, extra_categories: dict[str, str] | None = None) -> int:
    expected, problems = plan(root, extra_categories)
    if problems:
        _report(problems + ["nothing was written"])
        return 1
    changed = []
    for relative, content in expected.items():
        path = root / relative
        if content is None:
            if not path.is_file():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"")
                changed.append(relative)
        elif not path.is_file() or path.read_text(encoding="utf-8") != content:
            path.write_text(content, encoding="utf-8", newline="\n")
            changed.append(relative)
    print(
        "\n".join(f"registry-sync: wrote {item}" for item in changed)
        or "registry-sync: up to date"
    )
    return 0


def add_category(root: Path, name: str, purpose: str) -> int:
    try:
        categories = registered_categories(root)
    except SyncError as exc:
        _report([str(exc)])
        return 1
    manifests = _manifests(root, [])
    existing = {*categories, *map(registry_id_to_extra, manifests)}
    problems = _category_problems(name, existing)
    if name in categories:
        problems = [f"category {name}: already registered"]
    if not purpose.strip() or "|" in purpose or "\n" in purpose:
        problems.append("purpose: one non-empty line without '|'")
    if problems:
        _report(problems)
        return 1
    return write(root, {name: " ".join(purpose.split())})


def release_check(root: Path, version: str) -> int:
    version = version[1:] if version.startswith("v") else version
    problems = []
    if not re.fullmatch(r"\d+\.\d+\.\d+", version, re.ASCII):
        problems.append(f"invalid version: {version!r}")
    project = _read(root, "pyproject.toml").split("[project]", 1)[-1]
    found = re.search(r'^version\s*=\s*"([^"]*)"', project, re.M)
    if not found or found.group(1) != version:
        problems.append(
            f"pyproject.toml: version is {found and found.group(1)!r}, expected {version}"
        )
    found = re.search(
        r'^version:\s*"?([^"\n]*?)"?\s*$', _read(root, "CITATION.cff"), re.M
    )
    if not found or found.group(1) != version:
        problems.append(
            f"CITATION.cff: version is {found and found.group(1)!r}, expected {version}"
        )
    changelog = _read(root, "CHANGELOG.md")
    if version not in RELEASE.findall(changelog):
        problems.append(f"CHANGELOG.md: no section ## [{version}] - YYYY-MM-DD")
    expected, sync_problems = plan(root)
    problems += sync_problems + ([] if sync_problems else _stale(root, expected))
    releases = changelog_releases(changelog)
    for bundle_id in list_registry_skill_ids(root / "skills"):
        if bundle_id not in FIRST_RELEASE_BUNDLES and bundle_id not in releases:
            problems.append(
                f"{bundle_id}: Unreleased (no released Added line in CHANGELOG.md)"
            )
    if problems:
        _report(problems)
        return 1
    print(f"registry-sync: release v{version} is complete")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--add-category", metavar="NAME")
    mode.add_argument("--release-check", metavar="VERSION")
    parser.add_argument(
        "--purpose", help="purpose of the category (with --add-category)"
    )
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if args.add_category is not None:
        if args.purpose is None:
            parser.error("--add-category needs --purpose")
        return add_category(root, args.add_category, args.purpose)
    if args.release_check is not None:
        return release_check(root, args.release_check)
    return write(root) if args.write else check(root)


if __name__ == "__main__":
    raise SystemExit(main())
