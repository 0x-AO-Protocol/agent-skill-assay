# SPDX-License-Identifier: MIT
"""The checks of the standard profile, run on every bundle outside the legacy set.

The seven bundles shipped by v0.1.3 form a frozen legacy set: they stay
unchanged and outside the profile. Every other bundle under skills/, whatever
its origin, must pass the profile rules (standard files, manifest, maturity
label, static guard, dynamic guard), and no Detailed Work Activity may be
served by two bundles of origin ao_standard.

Today no bundle is outside the legacy set, so the rules are exercised on the
synthetic repository under tests/fixtures/standard_profile.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest
import yaml

from scripts.provenance import content_sha256

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_REPO = ROOT / "tests" / "fixtures" / "standard_profile"
BUNDLE_ID = "sample/word_tally"
SCHEMA = json.loads(
    (ROOT / "skill_assay" / "schemas" / "manifest_ao.schema.json").read_text(
        encoding="utf-8"
    )
)

SHIPPED_BY_V0_1_3 = frozenset(
    {
        "security/prompt_injection_firewall",
        "monitoring/kpi_gate",
        "wellness/mental_coach",
        "monitoring/business_diagnostic",
        "office/outreach_manager",
        "finance/period_close",
        "dev_tools/upgrade_gate",
    }
)


@pytest.fixture(scope="module")
def guard():
    from scripts import determinism_guard

    return determinism_guard


@pytest.fixture(scope="module")
def sync():
    from scripts import registry_sync

    return registry_sync


# --------------------------------------------------------------------------
# The profile checks
# --------------------------------------------------------------------------


STANDARD_FILES = ("manifest.yaml", "skill.py", "instructions.md", "card.json")
STANDARD_FILES += ("test_skill.py", "__init__.py")
COMMON_ECONOMIC_EFFECT = yaml.safe_load(
    (ROOT / "skills" / "finance" / "period_close" / "manifest.yaml").read_text("utf-8")
)["economic_effect"]
DWA_SCHEMA = SCHEMA["properties"]["serves_dwa"]
DWA_PATTERN = DWA_SCHEMA["properties"]["ids"]["items"]["pattern"]
VALIDATOR = jsonschema.Draft202012Validator(
    SCHEMA, format_checker=jsonschema.FormatChecker()
)


def layout_failures(repo_root: Path, bundle_id: str) -> list[str]:
    """Standard files: the bundle files, a params fixture and the catalog page."""
    bundle, name = repo_root / "skills" / bundle_id, bundle_id.split("/")[1]
    failures = [
        f"{bundle_id}: standard files: {item}: missing"
        for item in STANDARD_FILES
        if not (bundle / item).is_file()
    ]
    for item in ("schemas", "fixtures"):
        if not (bundle / item).is_dir():
            failures.append(f"{bundle_id}: standard files: {item}: missing directory")
    if not list((bundle / "schemas").glob("*.json")):
        failures.append(f"{bundle_id}: standard files: schemas: no JSON schema")
    if not list((bundle / "fixtures").glob("*_params.json")):
        failures.append(f"{bundle_id}: standard files: fixtures/*_params.json: none")
    if not (repo_root / "docs" / "skills" / f"{name}.md").is_file():
        failures.append(f"{bundle_id}: standard files: docs/skills/{name}.md: missing")
    return failures


ASCII_UPPER = str.maketrans("abcdefghijklmnopqrstuvwxyz", "ABCDEFGHIJKLMNOPQRSTUVWXYZ")


def _ascii_upper(text: str) -> str:
    """Upper-case A to Z only, so that no Unicode case rule changes the comparison."""
    return text.translate(ASCII_UPPER)


def manifest_failures(repo_root: Path, bundle_id: str) -> list[str]:
    """Manifest: the declarations, including origin and serves_dwa."""
    bundle = repo_root / "skills" / bundle_id
    failures: list[str] = []

    def fail(field, message):
        failures.append(f"{bundle_id}: manifest: {field}: {message}")

    try:
        manifest = yaml.safe_load((bundle / "manifest.yaml").read_text("utf-8"))
    except Exception as exc:
        return [f"{bundle_id}: manifest: manifest.yaml: {type(exc).__name__}"]
    if not isinstance(manifest, dict):
        return [f"{bundle_id}: manifest: manifest.yaml: not a mapping"]
    for error in VALIDATOR.iter_errors(manifest):
        fail(".".join(map(str, error.absolute_path)) or "<root>", error.message)
    if manifest.get("trust_tier") != "L0":
        fail("trust_tier", "must be L0")
    issuer = manifest.get("issuer") if isinstance(manifest.get("issuer"), dict) else {}
    for key in ("name", "email", "github", "org"):
        if not isinstance(issuer.get(key), str) or not issuer[key].strip():
            fail(f"issuer.{key}", "a non-empty string is required")
    provenance = manifest.get("provenance")
    provenance = provenance if isinstance(provenance, dict) else {}
    if provenance.get("anchor") != "repository":
        fail("provenance.anchor", "must be repository")
    if provenance.get("content_sha256") != content_sha256(bundle):
        fail("provenance.content_sha256", "differs from the bundle content")
    if manifest.get("economic_effect") != COMMON_ECONOMIC_EFFECT:
        fail("economic_effect", "differs from the common declaration")
    if manifest.get("env_vars"):
        fail("env_vars", "a standard bundle declares none")
    origin, org = manifest.get("origin"), issuer.get("org")
    if origin not in SCHEMA["properties"]["origin"]["enum"]:
        fail("origin", f"{origin!r}: one of ao_standard, ao_defined, third_party")
    elif origin == "third_party":
        parts = str(org or "").split(",")
        if not str(org or "").strip() or any(
            _ascii_upper(p.strip()) == "AO" for p in parts
        ):
            fail(
                "issuer.org", "a third-party bundle names an organisation other than AO"
            )
    elif org != "AO":
        fail("issuer.org", f"origin {origin} requires AO")
    dwa = manifest.get("serves_dwa")
    if origin == "ao_standard" and dwa is None:
        fail("serves_dwa", "required for origin ao_standard")
    elif dwa is not None:
        try:
            jsonschema.validate(dwa, DWA_SCHEMA)
        except jsonschema.ValidationError as exc:
            fail("serves_dwa", exc.message)
        else:
            for dwa_id in dwa["ids"]:
                if not re.fullmatch(DWA_PATTERN, dwa_id):
                    fail("serves_dwa.ids", f"{dwa_id!r} is not an exact match")
    return failures


def page_failures(repo_root: Path, bundle_id: str) -> list[str]:
    """Maturity label: the catalog page says the bundle is provisional."""
    page = repo_root / "docs" / "skills" / f"{bundle_id.split('/')[1]}.md"
    text = page.read_text("utf-8") if page.is_file() else ""
    if re.search(r"^\*\*Maturity:\*\* Provisional\b", text, re.M | re.ASCII):
        return []
    return [f'{bundle_id}: maturity label: no "**Maturity:** Provisional" line']


def guard_failures(guard, repo_root: Path, bundle_id: str) -> list[str]:
    """Static guard and dynamic guard: the two determinism guards."""
    bundle = repo_root / "skills" / bundle_id
    return [
        f"{bundle_id}: static guard: {item}" for item in guard.static_findings(bundle)
    ] + [f"{bundle_id}: dynamic guard: {item}" for item in guard.dynamic_check(bundle)]


def profile_failures(guard, repo_root: Path, bundle_id: str) -> list[str]:
    return (
        layout_failures(repo_root, bundle_id)
        + manifest_failures(repo_root, bundle_id)
        + page_failures(repo_root, bundle_id)
        + guard_failures(guard, repo_root, bundle_id)
    )


def bundles_outside_the_legacy_set(guard, repo_root: Path) -> list[str]:
    skills_root = repo_root / "skills"
    found = sorted(
        path.parent.relative_to(skills_root).as_posix()
        for path in skills_root.glob("*/*/manifest.yaml")
    )
    return [bundle_id for bundle_id in found if bundle_id not in guard.LEGACY_BUNDLES]


# --------------------------------------------------------------------------
# The real tree
# --------------------------------------------------------------------------


def test_legacy_bundles_are_exactly_the_seven_shipped_bundles(guard):
    assert isinstance(guard.LEGACY_BUNDLES, frozenset)
    assert guard.LEGACY_BUNDLES == SHIPPED_BY_V0_1_3


def test_every_legacy_bundle_is_in_the_tree(guard):
    for bundle_id in guard.LEGACY_BUNDLES:
        assert (ROOT / "skills" / bundle_id / "manifest.yaml").is_file(), bundle_id


def test_every_bundle_outside_the_legacy_set_meets_the_profile(guard):
    failures = []
    for bundle_id in bundles_outside_the_legacy_set(guard, ROOT):
        failures.extend(profile_failures(guard, ROOT, bundle_id))
    assert failures == []


def test_no_dwa_is_served_twice_by_ao_standard_bundles(sync):
    assert sync.dwa_collisions(ROOT) == []


# --------------------------------------------------------------------------
# Synthetic repositories
# --------------------------------------------------------------------------


def _copy_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    shutil.copytree(
        FIXTURE_REPO, repo, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
    )
    return repo


def _manifest_path(repo: Path, bundle_id: str = BUNDLE_ID) -> Path:
    return repo / "skills" / bundle_id / "manifest.yaml"


def _load(repo: Path, bundle_id: str = BUNDLE_ID) -> dict:
    return yaml.safe_load(_manifest_path(repo, bundle_id).read_text(encoding="utf-8"))


def _save(repo: Path, manifest: dict, bundle_id: str = BUNDLE_ID) -> None:
    _manifest_path(repo, bundle_id).write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )


def _rehash(repo: Path, bundle_id: str = BUNDLE_ID) -> None:
    manifest = _load(repo, bundle_id)
    manifest["provenance"]["content_sha256"] = content_sha256(
        repo / "skills" / bundle_id
    )
    _save(repo, manifest, bundle_id)


def _edit(repo: Path, change) -> None:
    manifest = _load(repo)
    change(manifest)
    _save(repo, manifest)


def _add_bundle(repo: Path, bundle_id: str, change) -> None:
    """Copy the fixture bundle to ``bundle_id`` and apply ``change`` to its manifest."""
    category, name = bundle_id.split("/")
    (repo / "skills" / category).mkdir(exist_ok=True)
    (repo / "skills" / category / "__init__.py").touch()
    shutil.copytree(repo / "skills" / BUNDLE_ID, repo / "skills" / bundle_id)
    manifest = _load(repo, bundle_id)
    manifest["name"] = bundle_id
    change(manifest)
    _save(repo, manifest, bundle_id)


def test_compliant_synthetic_bundle_meets_the_profile(guard):
    assert bundles_outside_the_legacy_set(guard, FIXTURE_REPO) == [BUNDLE_ID]
    assert profile_failures(guard, FIXTURE_REPO, BUNDLE_ID) == []
    assert layout_failures(FIXTURE_REPO, "sample/missing_bundle") != []


def test_legacy_ids_are_exempt_and_new_ids_are_not(guard, tmp_path):
    repo = _copy_repo(tmp_path)
    _add_bundle(repo, "finance/period_close", lambda manifest: None)
    assert bundles_outside_the_legacy_set(guard, repo) == [BUNDLE_ID]


# Standard files -----------------------------------------------------------


@pytest.mark.parametrize(
    "item",
    [
        "manifest.yaml",
        "skill.py",
        "instructions.md",
        "card.json",
        "test_skill.py",
        "__init__.py",
        "schemas",
        "fixtures",
    ],
)
def test_missing_standard_file_fails_the_standard_files_rule(tmp_path, item):
    repo = _copy_repo(tmp_path)
    path = repo / "skills" / BUNDLE_ID / item
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
    failures = layout_failures(repo, BUNDLE_ID)
    assert any(
        f.startswith(f"{BUNDLE_ID}: standard files: {item}:") for f in failures
    ), failures


def test_bundle_without_params_fixture_fails_the_standard_files_rule(tmp_path):
    repo = _copy_repo(tmp_path)
    fixtures = repo / "skills" / BUNDLE_ID / "fixtures"
    for path in fixtures.glob("*_params.json"):
        path.rename(path.with_name(path.name.replace("_params", "_input")))
    failures = layout_failures(repo, BUNDLE_ID)
    assert any(
        f.startswith(f"{BUNDLE_ID}: standard files: fixtures/*_params.json:")
        for f in failures
    ), failures


def test_bundle_without_catalog_page_fails_the_standard_files_rule(tmp_path):
    repo = _copy_repo(tmp_path)
    (repo / "docs" / "skills" / "word_tally.md").unlink()
    failures = layout_failures(repo, BUNDLE_ID)
    assert any(
        f.startswith(f"{BUNDLE_ID}: standard files: docs/skills/word_tally.md:")
        for f in failures
    ), failures


# Maturity label -----------------------------------------------------------


@pytest.mark.parametrize(
    "replacement",
    [
        pytest.param("", id="no-maturity-line"),
        pytest.param("**Maturity:** Stable.", id="other-maturity"),
        pytest.param("Maturity: Provisional", id="not-bold"),
    ],
)
def test_catalog_page_without_provisional_maturity_fails_the_maturity_label_rule(
    tmp_path, replacement
):
    repo = _copy_repo(tmp_path)
    page = repo / "docs" / "skills" / "word_tally.md"
    lines = [
        replacement if line.startswith("**Maturity:**") else line
        for line in page.read_text(encoding="utf-8").splitlines()
    ]
    page.write_text("\n".join(lines) + "\n", encoding="utf-8")
    failures = page_failures(repo, BUNDLE_ID)
    assert any(
        f.startswith(f"{BUNDLE_ID}: maturity label: ") for f in failures
    ), failures


# Manifest -----------------------------------------------------------------


def _set(path: str, value):
    def change(manifest):
        *parents, last = path.split(".")
        target = manifest
        for part in parents:
            target = target[part]
        target[last] = value

    return change


def _drop(path: str):
    def change(manifest):
        *parents, last = path.split(".")
        target = manifest
        for part in parents:
            target = target[part]
        del target[last]

    return change


def _third_party(org):
    def change(manifest):
        manifest["origin"] = "third_party"
        manifest["issuer"]["org"] = org
        del manifest["serves_dwa"]

    return change


MANIFEST_VIOLATIONS = [
    ("trust-tier", _set("trust_tier", "L2"), "trust_tier"),
    ("issuer-without-github", _drop("issuer.github"), "issuer.github"),
    ("issuer-without-email", _drop("issuer.email"), "issuer.email"),
    (
        "archive-provenance",
        _set(
            "provenance",
            {
                "source": "agent-skill-assay-sources@0000000:skills/sample/word_tally",
                "content_sha256": "0" * 64,
                "ported_on": "2026-09-25",
            },
        ),
        "provenance.anchor",
    ),
    (
        "stale-content-hash",
        _set("provenance.content_sha256", "f" * 64),
        "provenance.content_sha256",
    ),
    (
        "economic-effect-altered",
        _set("economic_effect.claim", "A different claim."),
        "economic_effect",
    ),
    (
        "env-vars",
        _set("env_vars", {"API_KEY": {"required": False}}),
        "env_vars",
    ),
    ("origin-missing", _drop("origin"), "origin"),
    ("origin-unknown", _set("origin", "not_an_origin"), "origin"),
    ("ao-standard-without-dwa", _drop("serves_dwa"), "serves_dwa"),
    ("ao-standard-other-org", _set("issuer.org", "Acme Labs"), "issuer.org"),
    (
        "ao-defined-other-org",
        lambda m: (_set("origin", "ao_defined")(m), _set("issuer.org", "Acme")(m)),
        "issuer.org",
    ),
    ("third-party-org-ao", _third_party("AO"), "issuer.org"),
    ("third-party-org-names-ao", _third_party("Acme, AO"), "issuer.org"),
    ("third-party-org-empty", _third_party(""), "issuer.org"),
    (
        "dwa-trailing-newline",
        _set("serves_dwa.ids", ["4.A.2.a.1.a.1\n"]),
        "serves_dwa.ids",
    ),
    (
        "dwa-five-ids",
        _set(
            "serves_dwa.ids",
            [f"4.A.2.a.1.a.{n}" for n in range(1, 6)],
        ),
        "serves_dwa",
    ),
    (
        "dwa-duplicate-id",
        _set("serves_dwa.ids", ["4.A.2.a.1.a.1", "4.A.2.a.1.a.1"]),
        "serves_dwa",
    ),
    ("dwa-release-float", _set("serves_dwa.onet_release", 31.0), "serves_dwa"),
    ("dwa-release-other", _set("serves_dwa.onet_release", "30.2"), "serves_dwa"),
]


@pytest.mark.parametrize(
    ("change", "field"),
    [(case[1], case[2]) for case in MANIFEST_VIOLATIONS],
    ids=[case[0] for case in MANIFEST_VIOLATIONS],
)
def test_manifest_violation_fails_the_manifest_rule(tmp_path, change, field):
    repo = _copy_repo(tmp_path)
    assert manifest_failures(repo, BUNDLE_ID) == []
    _edit(repo, change)
    failures = manifest_failures(repo, BUNDLE_ID)
    assert any(
        f.startswith(f"{BUNDLE_ID}: manifest: {field}:") for f in failures
    ), failures


@pytest.mark.parametrize(
    "change",
    [
        pytest.param(
            lambda m: (_set("origin", "ao_defined")(m), _drop("serves_dwa")(m)),
            id="ao-defined-without-dwa",
        ),
        pytest.param(_set("origin", "ao_defined"), id="ao-defined-with-dwa"),
        pytest.param(_third_party("Acme Labs"), id="third-party-without-dwa"),
    ],
)
def test_each_origin_is_accepted_with_its_rules(tmp_path, change):
    repo = _copy_repo(tmp_path)
    _edit(repo, change)
    assert manifest_failures(repo, BUNDLE_ID) == []
    assert _load(repo)["origin"] in SCHEMA["properties"]["origin"]["enum"]


def test_third_party_bundle_with_serves_dwa_is_validated(tmp_path):
    repo = _copy_repo(tmp_path)
    _edit(repo, _third_party("Acme Labs"))
    _edit(repo, _set("serves_dwa", {"onet_release": "31.0", "ids": ["4.A.1.a.1.a.1"]}))
    assert manifest_failures(repo, BUNDLE_ID) == []
    _edit(repo, _set("serves_dwa.ids", ["4.A.9.a.1.a.1"]))
    assert any(
        f.startswith(f"{BUNDLE_ID}: manifest: serves_dwa")
        for f in manifest_failures(repo, BUNDLE_ID)
    )


# Static guard and dynamic guard --------------------------------------------


def test_runtime_import_outside_the_allow_list_fails_the_static_guard(guard, tmp_path):
    repo = _copy_repo(tmp_path)
    skill = repo / "skills" / BUNDLE_ID / "skill.py"
    skill.write_text("import os\n" + skill.read_text(encoding="utf-8"), "utf-8")
    _rehash(repo)
    failures = guard_failures(guard, repo, BUNDLE_ID)
    assert any(
        f.startswith(f"{BUNDLE_ID}: static guard: skill.py:1: import:")
        for f in failures
    )


def test_hash_seed_dependent_output_fails_the_dynamic_guard(guard, tmp_path):
    repo = _copy_repo(tmp_path)
    skill = repo / "skills" / BUNDLE_ID / "skill.py"
    source = skill.read_text(encoding="utf-8").replace(
        '"total": sum(count for _, count in words),',
        '"total": sum(count for _, count in words),\n'
        '            "seen": list({word for word, _ in words}),',
    )
    assert '"seen"' in source
    skill.write_text(source, encoding="utf-8")
    _rehash(repo)
    failures = guard_failures(guard, repo, BUNDLE_ID)
    assert any(
        f.startswith(f"{BUNDLE_ID}: dynamic guard: ") for f in failures
    ), failures


def test_root_conftest_disables_sockets_for_standard_bundle_tests(tmp_path):
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy2(ROOT / "conftest.py", repo / "conftest.py")
    shutil.copy2(
        ROOT / "scripts" / "determinism_guard.py",
        repo / "scripts" / "determinism_guard.py",
    )
    (repo / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    tests = {
        "sample/new_one": (
            "import socket\n\nimport pytest\n\n\n"
            "def test_socket_is_blocked():\n"
            "    with pytest.raises(OSError):\n"
            "        socket.socket()\n"
            "    with pytest.raises(OSError):\n"
            "        socket.create_connection(('127.0.0.1', 9))\n"
        ),
        "finance/period_close": (
            "import socket\n\n\n"
            "def test_socket_is_not_blocked():\n"
            "    socket.socket().close()\n"
        ),
    }
    (repo / "skills").mkdir()
    (repo / "skills" / "__init__.py").touch()
    for bundle_id, source in tests.items():
        category, _ = bundle_id.split("/")
        (repo / "skills" / category).mkdir(exist_ok=True)
        (repo / "skills" / category / "__init__.py").touch()
        bundle = repo / "skills" / bundle_id
        bundle.mkdir()
        (bundle / "__init__.py").touch()
        (bundle / "test_skill.py").write_text(source, encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if key != "PYTEST_ADDOPTS"}
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", "skills"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout


# DWA uniqueness among ao_standard bundles ------------------------------------


def test_two_ao_standard_bundles_with_one_dwa_fail(sync, tmp_path):
    repo = _copy_repo(tmp_path)
    _add_bundle(repo, "sample/second_tally", lambda manifest: None)
    collisions = sync.dwa_collisions(repo)
    assert collisions == [
        "4.A.2.a.1.a.1: served by sample/second_tally, sample/word_tally"
    ]


@pytest.mark.parametrize(
    "change",
    [
        pytest.param(_set("origin", "ao_defined"), id="ao-defined"),
        pytest.param(
            lambda m: (_third_party("Acme")(m), _set("serves_dwa", _dwa())(m)),
            id="third-party",
        ),
    ],
)
def test_one_dwa_in_bundles_of_different_origins_passes(sync, tmp_path, change):
    repo = _copy_repo(tmp_path)
    _add_bundle(repo, "sample/second_tally", change)
    assert sync.dwa_collisions(repo) == []


def test_one_dwa_in_two_third_party_bundles_passes(sync, tmp_path):
    repo = _copy_repo(tmp_path)
    _edit(repo, lambda m: (_third_party("Acme")(m), _set("serves_dwa", _dwa())(m)))
    _add_bundle(
        repo,
        "sample/second_tally",
        lambda m: None,
    )
    assert sync.dwa_collisions(repo) == []


def _dwa():
    return {"onet_release": "31.0", "ids": ["4.A.2.a.1.a.1"]}
