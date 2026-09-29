from skill_assay.cli import (
    _discover_skills,
    _resolve_pytest_targets,
    _parse_examples_index,
    _load_examples_index,
    _example_counts_by_skill,
    _example_github_url,
    _parse_nav,
    _NAV_EXIT,
    _NAV_BACK,
    cmd_list,
    cmd_examples,
    cmd_interactive,
    cmd_test,
    _short_description,
    cmd_help,
    cmd_paths,
    cmd_paths_submenu,
    cmd_doctor,
    cmd_theme_picker,
    cmd_theme,
)

import importlib.util

import pytest

from skill_assay.core.config import clear_config_cache


@pytest.fixture
def isolated_theme_environment(tmp_path, monkeypatch):
    """Keep theme reads and writes away from the user's real configuration."""
    repo = tmp_path / "repo"
    repo.mkdir()
    config_dir = tmp_path / "global-config"
    monkeypatch.chdir(repo)
    monkeypatch.setenv("SKILL_ASSAY_CONFIG_DIR", str(config_dir))
    clear_config_cache()
    yield repo, config_dir
    clear_config_cache()


def test_discover_skills_returns_skills(tmp_path):
    # Create a fake skill directory structure
    skill_dir = tmp_path / "security" / "prompt_injection_firewall"
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.py").touch()

    manifest = skill_dir / "manifest.yaml"
    manifest.write_text(
        "name: security/prompt_injection_firewall\n"
        "version: 0.1.0\n"
        "description: Fills PDF forms.\n"
        "requirements:\n"
        "  - pymupdf\n"
    )

    skills = _discover_skills(tmp_path)

    assert len(skills) == 1
    assert skills[0]["id"] == "security/prompt_injection_firewall"
    assert skills[0]["version"] == "0.1.0"


def test_discover_skills_empty_directory(tmp_path):
    # No skills created, directory is empty
    skills = _discover_skills(tmp_path)

    assert skills == []


def test_discover_skills_nonexistent_override_falls_back(tmp_path, monkeypatch):
    # An override path that does not exist should be ignored
    # and fall back to other roots without crashing
    monkeypatch.chdir(tmp_path)
    fake_path = tmp_path / "nonexistent"

    # Should not raise, just return empty list since no roots have skills
    skills = _discover_skills(fake_path)
    assert skills == []


def test_discover_skills_missing_optional_fields(tmp_path):
    # Manifest with only required fields, no version, description or requirements
    skill_dir = tmp_path / "security" / "minimal_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.py").touch()

    manifest = skill_dir / "manifest.yaml"
    manifest.write_text("name: minimal_skill\n")

    skills = _discover_skills(tmp_path)

    assert len(skills) == 1
    assert skills[0]["version"] == "?"
    assert skills[0]["description"] == ""
    assert skills[0]["requirements"] == ""


def test_discover_skills_ignores_deeply_nested_manifest(tmp_path):
    # manifest.yaml three levels deep should not be picked up
    skill_dir = tmp_path / "security" / "prompt_injection_firewall" / "extra"
    skill_dir.mkdir(parents=True)

    manifest = skill_dir / "manifest.yaml"
    manifest.write_text("name: should_not_appear\nversion: 0.1.0\n")

    skills = _discover_skills(tmp_path)

    assert skills == []


def test_discover_skills_prefers_issuer_org(tmp_path):
    # Manifest with issuer org and GitHub handle
    skill_dir = tmp_path / "security" / "prompt_injection_firewall"
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.py").touch()

    manifest = skill_dir / "manifest.yaml"
    manifest.write_text(
        "name: security/prompt_injection_firewall\n"
        "version: 0.1.0\n"
        "description: Fills PDF forms.\n"
        "issuer:\n"
        "  name: AO Tester\n"
        "  github: ao-tester\n"
        "  org: AO\n"
    )

    skills = _discover_skills(tmp_path)

    assert skills[0]["issuer"] == "AO"
    assert skills[0]["issuer_aliases"] == {"AO", "AO Tester", "ao-tester"}


def test_discover_skills_issuer_falls_back_to_name(tmp_path):
    # Manifest with issuer name but no github handle
    skill_dir = tmp_path / "security" / "prompt_injection_firewall"
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.py").touch()

    manifest = skill_dir / "manifest.yaml"
    manifest.write_text(
        "name: security/prompt_injection_firewall\n"
        "version: 0.1.0\n"
        "description: Fills PDF forms.\n"
        "issuer:\n"
        "  name: AO Tester\n"
    )

    skills = _discover_skills(tmp_path)

    assert skills[0]["issuer"] == "AO Tester"


def test_cmd_list_filter_by_category(tmp_path):
    # Only skills matching the category should appear
    import io
    from rich.console import Console

    for category, name in [
        ("security", "prompt_injection_firewall"),
        ("monitoring", "kpi_gate"),
    ]:
        skill_dir = tmp_path / category / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "skill.py").touch()
        (skill_dir / "manifest.yaml").write_text(
            f"name: {category}/{name}\nversion: 0.1.0\ndescription: Test.\n"
        )

    buf = io.StringIO()
    cmd_list(
        skills_root_override=tmp_path,
        category_filter="security",
        console=Console(file=buf, force_terminal=False),
    )

    output = buf.getvalue()
    assert "security" in output
    assert "monitoring" not in output


def test_cmd_list_filter_by_org_or_issuer_handle(tmp_path):
    import io
    from rich.console import Console

    skill_dir = tmp_path / "monitoring" / "kpi_gate"
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.py").touch()
    (skill_dir / "manifest.yaml").write_text(
        "name: monitoring/kpi_gate\nversion: 0.1.0\ndescription: KPI checks.\n"
        "issuer:\n  name: Tester\n  github: tester-handle\n  org: AO\n",
        encoding="utf-8",
    )

    for issuer in ("AO", "tester-handle"):
        buf = io.StringIO()
        cmd_list(
            skills_root_override=tmp_path,
            issuer_filter=issuer,
            console=Console(file=buf, force_terminal=False, width=120),
        )
        assert "monitoring/kpi_gate" in buf.getvalue()
        assert "AO" in buf.getvalue()


def test_short_description_uses_short_description_field():
    """short_description field takes priority over description."""
    data = {
        "short_description": "Short one.",
        "description": "This is a much longer description that should not appear.",
    }
    assert _short_description(data) == "Short one."


def test_short_description_truncates_at_80_chars():
    """short_description longer than 80 chars should be truncated with …"""
    data = {"short_description": "A" * 90}
    result = _short_description(data)
    assert len(result) == 81  # 80 + "…"
    assert result.endswith("…")


def test_short_description_falls_back_to_first_sentence():
    """Without short_description, use first sentence of description."""
    data = {"description": "First sentence. Second sentence follows."}
    assert _short_description(data) == "First sentence."


def test_short_description_empty_manifest():
    """Empty manifest should return empty string."""
    assert _short_description({}) == ""


def test_cmd_interactive_exits_on_q(monkeypatch):
    """Entering q should exit cleanly."""
    import io
    from rich.console import Console

    monkeypatch.setattr("builtins.input", lambda _: "q")
    buf = io.StringIO()
    cmd_interactive(console=Console(file=buf, force_terminal=False))
    assert "Bye" in buf.getvalue()


def test_cmd_interactive_unknown_command(monkeypatch):
    """Unknown command should print error then exit on q."""
    import io
    from rich.console import Console

    responses = iter(["unknown_cmd", "q"])
    monkeypatch.setattr("builtins.input", lambda _: next(responses))
    buf = io.StringIO()
    cmd_interactive(console=Console(file=buf, force_terminal=False))
    assert "Unknown command" in buf.getvalue()


def test_cmd_interactive_list_dispatch(tmp_path, monkeypatch):
    """Entering 1 or list should dispatch to cmd_list."""
    import io
    from rich.console import Console

    skill_dir = tmp_path / "security" / "test_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.py").touch()
    (skill_dir / "manifest.yaml").write_text(
        "name: test_skill\nversion: 0.1.0\ndescription: Test.\n"
        "short_description: Test skill.\n"
    )

    responses = iter(["1", "q"])
    monkeypatch.setattr("builtins.input", lambda _: next(responses))
    monkeypatch.chdir(tmp_path)

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)
    cmd_interactive(console=console)

    output = buf.getvalue()
    assert "test_skill" in output


def test_main_module_invocation():
    """python -m skill-assay should be importable and callable."""
    import skill_assay.__main__  # noqa: F401 — just verify it imports cleanly
    from skill_assay.__main__ import main

    assert callable(main)


def test_cmd_help_includes_list_examples(capsys):
    """cmd_help should include category, test, and issuer examples."""
    import io
    from rich.console import Console

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)
    cmd_help(console=console)

    output = buf.getvalue()
    assert "Topics" in output
    assert "skills" in output
    assert "--category" in output
    assert "skill-assay test" in output or "test" in output
    assert "skill-assay examples" in output or "examples" in output


def test_interactive_help_dispatches_to_cmd_help(monkeypatch):
    """Interactive menu option 6 / help opens topic submenu."""
    import io
    from rich.console import Console

    responses = iter(["6", "1", "", "b", "0"])
    monkeypatch.setattr("builtins.input", lambda _: next(responses))

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)
    cmd_interactive(console=console)

    output = buf.getvalue()
    assert "Skills" in output
    assert "--category" in output
    assert "--issuer" in output


def test_builtin_theme_palettes_match_intended_roles():
    import skill_assay.cli as cli

    assert set(cli.THEMES) == {"pastel", "ocean", "mono"}

    pastel = cli.THEMES["pastel"]
    assert pastel.heading_style == "bold #C7CEEA"
    assert pastel.menu_style == "#FFDAC1"
    assert pastel.gradient_mid == (0x79, 0xB6, 0xD8)

    ocean = cli.THEMES["ocean"]
    for red, green, blue in (
        ocean.gradient_start,
        ocean.gradient_mid,
        ocean.gradient_end,
    ):
        assert blue > red
        assert blue >= green

    mono = cli.THEMES["mono"]
    for red, green, blue in (
        mono.gradient_start,
        mono.gradient_mid,
        mono.gradient_end,
    ):
        assert red == green == blue


@pytest.mark.parametrize("theme_name", ["pastel", "ocean", "mono"])
def test_builtin_theme_smoke_render(theme_name, isolated_theme_environment):
    import io

    from rich.console import Console

    import skill_assay.cli as cli
    from skill_assay.core.config import save_global_presentation_theme

    save_global_presentation_theme(theme_name)
    buf = io.StringIO()
    cmd_help(console=Console(file=buf, force_terminal=False))

    palette = cli.THEMES[theme_name]
    assert cli._active_theme() == palette
    assert cli.TABLE_STYLE == palette.heading_style
    assert cli.MENU_STYLE == palette.menu_style
    assert cli.ERROR_STYLE == f"bold {palette.error_color}"
    assert cli._gradient_splash_text(("TEST",)).plain == "TEST\n"
    assert "Topics" in buf.getvalue()


@pytest.mark.parametrize("theme_name", ["pastel", "ocean", "mono"])
def test_mail_submenu_uses_active_theme(theme_name, isolated_theme_environment):
    import io

    from rich.console import Console

    import skill_assay.cli_mail as cli_mail
    from skill_assay.cli_theme import THEMES
    from skill_assay.core.config import save_global_presentation_theme

    save_global_presentation_theme(theme_name)
    buf = io.StringIO()

    assert (
        cli_mail.cmd_mail_submenu(
            console=Console(file=buf, force_terminal=False),
            input_fn=lambda _prompt: "b",
        )
        is None
    )

    palette = THEMES[theme_name]
    assert cli_mail.TABLE_STYLE == palette.heading_style
    assert cli_mail.ID_STYLE == palette.id_style
    assert cli_mail.MENU_STYLE == palette.menu_style
    assert cli_mail.ERROR_STYLE == f"bold {palette.error_color}"
    assert "Mail settings" in buf.getvalue()


def test_theme_picker_persists_and_preserves_global_config(
    isolated_theme_environment,
):
    import io

    import yaml
    from rich.console import Console

    import skill_assay.cli as cli

    _repo, config_dir = isolated_theme_environment
    config_path = config_dir / "config.yaml"
    config_dir.mkdir()
    config_path.write_text(
        "paths:\n  project: auto\nchains:\n  default: []\n",
        encoding="utf-8",
    )
    responses = iter(["unknown", "2"])
    buf = io.StringIO()

    assert (
        cmd_theme_picker(
            console=Console(file=buf, force_terminal=False),
            input_fn=lambda _prompt: next(responses),
        )
        is None
    )

    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert saved["presentation"]["theme"] == "ocean"
    assert saved["paths"] == {"project": "auto"}
    assert saved["chains"] == {"default": []}
    assert cli.TABLE_STYLE == cli.THEMES["ocean"].heading_style
    assert "Unknown theme: 'unknown'" in buf.getvalue()
    assert "Saved global theme 'ocean'" in buf.getvalue()


def test_interactive_theme_dispatches_and_refreshes(
    isolated_theme_environment, monkeypatch
):
    import io

    import yaml
    from rich.console import Console

    import skill_assay.cli as cli

    _repo, config_dir = isolated_theme_environment
    responses = iter(["8", "3", "q"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(responses))
    buf = io.StringIO()

    cmd_interactive(console=Console(file=buf, force_terminal=False))

    saved = yaml.safe_load((config_dir / "config.yaml").read_text(encoding="utf-8"))
    assert saved["presentation"]["theme"] == "mono"
    assert cli.TABLE_STYLE == cli.THEMES["mono"].heading_style
    assert "Current: pastel" in buf.getvalue()
    assert "Saved global theme 'mono'" in buf.getvalue()


def test_theme_picker_reports_project_override(isolated_theme_environment):
    import io

    import yaml
    from rich.console import Console

    from skill_assay.core.config import load_merged_config

    repo, config_dir = isolated_theme_environment
    (repo / ".skill-assay.yaml").write_text(
        "presentation:\n  theme: ocean\n",
        encoding="utf-8",
    )
    clear_config_cache()
    buf = io.StringIO()

    cmd_theme_picker(
        console=Console(file=buf, force_terminal=False),
        input_fn=lambda _prompt: "3",
    )

    global_config = yaml.safe_load(
        (config_dir / "config.yaml").read_text(encoding="utf-8")
    )
    assert global_config["presentation"]["theme"] == "mono"
    assert load_merged_config().presentation.theme == "ocean"
    assert "Project config keeps 'ocean' active" in buf.getvalue()


def test_version_flag(capsys):
    """skill-assay --version should print the installed version and exit."""
    import sys
    from skill_assay.cli import main

    monkeypatch_argv = sys.argv
    sys.argv = ["skill-assay", "--version"]
    try:
        with pytest.raises(SystemExit):
            main()
    finally:
        sys.argv = monkeypatch_argv

    captured = capsys.readouterr()
    assert "skill-assay" in captured.out.lower()


def _make_bundle(tmp_path, category, name, with_test=True):
    skill_dir = tmp_path / category / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.py").touch()
    (skill_dir / "manifest.yaml").write_text(
        f"name: {category}/{name}\nversion: 0.1.0\ndescription: Test.\n"
    )
    if with_test:
        (skill_dir / "test_skill.py").touch()
    return skill_dir


def test_resolve_pytest_targets_skill_id(tmp_path):
    _make_bundle(tmp_path, "security", "prompt_injection_firewall")
    targets, error = _resolve_pytest_targets(
        skills_root_override=tmp_path,
        skill_id="security/prompt_injection_firewall",
    )
    assert error is None
    assert targets == [
        tmp_path / "security" / "prompt_injection_firewall" / "test_skill.py"
    ]


def test_resolve_pytest_targets_category(tmp_path):
    _make_bundle(tmp_path, "security", "prompt_injection_firewall")
    _make_bundle(tmp_path, "monitoring", "kpi_gate")
    targets, error = _resolve_pytest_targets(
        skills_root_override=tmp_path,
        category="security",
    )
    assert error is None
    assert targets == [tmp_path / "security"]


def test_resolve_pytest_targets_all_roots(tmp_path):
    _make_bundle(tmp_path, "security", "prompt_injection_firewall")
    targets, error = _resolve_pytest_targets(skills_root_override=tmp_path)
    assert error is None
    assert targets == [tmp_path]


def test_resolve_pytest_targets_missing_skill(tmp_path):
    targets, error = _resolve_pytest_targets(
        skills_root_override=tmp_path,
        skill_id="security/missing",
    )
    assert targets == []
    assert "No bundle test found" in error


def test_resolve_pytest_targets_skill_id_and_category_conflict():
    targets, error = _resolve_pytest_targets(
        skill_id="security/prompt_injection_firewall",
        category="security",
    )
    assert targets == []
    assert "not both" in error


def test_cmd_test_invokes_pytest(tmp_path, monkeypatch):
    import sys

    _make_bundle(tmp_path, "security", "prompt_injection_firewall")
    captured = {}

    def fake_run(cmd, check=False):
        captured["cmd"] = cmd

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr("skill_assay.cli.subprocess.run", fake_run)

    rc = cmd_test(
        skills_root_override=tmp_path,
        skill_id="security/prompt_injection_firewall",
    )
    assert rc == 0
    assert captured["cmd"][0] == sys.executable
    assert captured["cmd"][1:3] == ["-m", "pytest"]
    assert (
        str(tmp_path / "security" / "prompt_injection_firewall" / "test_skill.py")
        in captured["cmd"]
    )


def test_cmd_test_verbose_flag(tmp_path, monkeypatch):
    _make_bundle(tmp_path, "security", "prompt_injection_firewall")
    captured = {}

    def fake_run(cmd, check=False):
        captured["cmd"] = cmd

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr("skill_assay.cli.subprocess.run", fake_run)

    cmd_test(
        skills_root_override=tmp_path,
        skill_id="security/prompt_injection_firewall",
        verbose=True,
        no_header=True,
    )
    assert "-v" in captured["cmd"]
    assert "--no-header" in captured["cmd"]


def test_cmd_test_missing_bundle_returns_nonzero(tmp_path):
    rc = cmd_test(
        skills_root_override=tmp_path,
        skill_id="security/missing",
    )
    assert rc == 1


def test_main_test_subcommand_exits_with_cmd_test_code(monkeypatch):
    import sys
    from skill_assay.cli import main

    monkeypatch.setattr("skill_assay.cli.cmd_test", lambda **kwargs: 0)

    argv = sys.argv
    sys.argv = ["skill-assay", "test", "security/prompt_injection_firewall"]
    try:
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0
    finally:
        sys.argv = argv


def test_interactive_test_dispatch(tmp_path, monkeypatch):
    """Entering 3 or test should dispatch to cmd_test."""
    import io
    from rich.console import Console

    _make_bundle(tmp_path, "security", "test_skill")
    captured = {}

    def fake_test(**kwargs):
        captured["called"] = True
        return 0

    monkeypatch.setattr("skill_assay.cli.cmd_test", fake_test)

    responses = iter(["test", "q"])
    monkeypatch.setattr("builtins.input", lambda _: next(responses))

    buf = io.StringIO()
    cmd_interactive(console=Console(file=buf, force_terminal=False))

    assert captured.get("called") is True


def test_interactive_examples_dispatch(examples_readme, monkeypatch):
    """Entering examples should prompt and dispatch to cmd_examples."""
    import io
    from rich.console import Console

    captured = {}

    def fake_examples(**kwargs):
        captured["skill_id"] = kwargs.get("skill_id")
        return 0

    monkeypatch.setattr("skill_assay.cli.cmd_examples", fake_examples)

    responses = iter(["examples", "", "0"])
    monkeypatch.setattr("builtins.input", lambda _: next(responses))

    buf = io.StringIO()
    cmd_interactive(console=Console(file=buf, force_terminal=False))

    assert captured.get("skill_id") is None


SAMPLE_EXAMPLES_README = (
    "# Examples\n\n"
    "## Runnable Scripts\n\n"
    "| Script | Skill ID | Provider | Required extra | Required env vars | Description |\n"
    "| :--- | :--- | :--- | :--- | :--- | :--- |\n"
    "| `mental_coach_demo.py` | `wellness/mental_coach` | Gemini | "
    "`[wellness_mental_coach]`, `[gemini]` | `GOOGLE_API_KEY` | Demo. |\n"
    "| `kpi_gate_demo.py` | `monitoring/kpi_gate`, "
    "`security/prompt_injection_firewall` | Ollama | `[monitoring_kpi_gate]`, "
    "`[security_prompt_injection_firewall]` | None | Multi. |\n"
)


@pytest.fixture
def examples_readme(tmp_path, monkeypatch):
    readme = tmp_path / "examples" / "README.md"
    readme.parent.mkdir(parents=True)
    readme.write_text(SAMPLE_EXAMPLES_README, encoding="utf-8")
    monkeypatch.setattr("skill_assay.cli._examples_readme_path", lambda: readme)
    return readme


def test_parse_examples_index_handles_multi_skill_ids(examples_readme):
    rows = _parse_examples_index(examples_readme)
    assert len(rows) == 2
    assert rows[1]["skill_ids"] == [
        "monitoring/kpi_gate",
        "security/prompt_injection_firewall",
    ]


def test_example_counts_by_skill_includes_multi_skill_rows(examples_readme):
    rows = _parse_examples_index(examples_readme)
    counts = _example_counts_by_skill(rows)
    assert counts["wellness/mental_coach"] == 1
    assert counts["monitoring/kpi_gate"] == 1
    assert counts["security/prompt_injection_firewall"] == 1


def test_cmd_list_examples_column(tmp_path, examples_readme):
    import io
    from rich.console import Console

    _make_bundle(tmp_path, "wellness", "mental_coach")
    _make_bundle(tmp_path, "monitoring", "kpi_gate")
    _make_bundle(tmp_path, "security", "prompt_injection_firewall")
    _make_bundle(tmp_path, "monitoring", "business_diagnostic")

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=200)
    cmd_list(
        skills_root_override=tmp_path,
        show_examples=True,
        console=console,
    )

    output = buf.getvalue()
    assert "EXAMPLES" in output
    assert "wellness" in output
    assert "monitoring" in output


def test_cmd_examples_lists_all_scripts(examples_readme):
    import io
    from rich.console import Console

    buf = io.StringIO()
    rc = cmd_examples(console=Console(file=buf, force_terminal=False, width=200))
    assert rc == 0
    output = buf.getvalue()
    assert "mental_coach_demo.py" in output
    assert "kpi_gate_demo.py" in output
    assert "Full notes:" in output
    assert "examples/README.md" in output


def test_cmd_examples_filters_by_skill_id(examples_readme):
    import io
    from rich.console import Console

    buf = io.StringIO()
    rc = cmd_examples(
        skill_id="wellness/mental_coach",
        console=Console(file=buf, force_terminal=False, width=200),
    )
    assert rc == 0
    output = buf.getvalue()
    assert "mental_coach_demo.py" in output
    assert "kpi_gate_demo.py" not in output


def test_example_github_url():
    url = _example_github_url("build_dataset_demo.py")
    assert url == (
        "https://github.com/0x-AO-Protocol/agent-skill-assay/blob/main/examples/build_dataset_demo.py"
    )


def test_cmd_examples_includes_github_links(examples_readme):
    import io
    from rich.console import Console

    buf = io.StringIO()
    cmd_examples(
        skill_id="wellness/mental_coach",
        console=Console(file=buf, force_terminal=False, width=220),
    )
    output = buf.getvalue()
    assert "mental_coach_demo.py" in output
    assert "gemini" in output.lower()


def test_cmd_examples_unknown_skill_returns_nonzero(examples_readme):
    rc = cmd_examples(skill_id="wellness/missing")
    assert rc == 1


def test_load_examples_index_prefers_local_readme(examples_readme):
    rows, source = _load_examples_index()
    assert len(rows) == 2
    assert source == examples_readme


def test_load_examples_index_github_fallback(monkeypatch):
    monkeypatch.setattr("skill_assay.cli._examples_readme_path", lambda: None)

    class FakeResponse:
        text = SAMPLE_EXAMPLES_README

        def raise_for_status(self):
            return None

    monkeypatch.setattr(
        "skill_assay.cli.requests.get",
        lambda url, timeout: FakeResponse(),
    )

    rows, source = _load_examples_index()
    assert len(rows) == 2
    assert source == (
        "https://github.com/0x-AO-Protocol/agent-skill-assay/blob/main/examples/README.md"
    )


def test_load_examples_index_github_failure(monkeypatch):
    monkeypatch.setattr("skill_assay.cli._examples_readme_path", lambda: None)

    def raise_error(url, timeout):
        raise OSError("offline")

    monkeypatch.setattr("skill_assay.cli.requests.get", raise_error)

    rows, source = _load_examples_index()
    assert rows == []
    assert source is None


def test_main_examples_subcommand_exits_with_cmd_examples_code(monkeypatch):
    import sys
    from skill_assay.cli import main

    monkeypatch.setattr("skill_assay.cli.cmd_examples", lambda **kwargs: 0)

    argv = sys.argv
    sys.argv = ["skill-assay", "examples", "wellness/mental_coach"]
    try:
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0
    finally:
        sys.argv = argv


def test_cmd_paths_shows_roots_and_tiers(tmp_path, monkeypatch):
    import io
    from rich.console import Console

    root = tmp_path / "skills"
    root.mkdir()
    skill_dir = root / "security" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.py").touch()
    (skill_dir / "manifest.yaml").write_text(
        "name: security/demo\nversion: 0.1.0\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    assert cmd_paths(console=console) == 0

    output = buf.getvalue()
    assert "Skill path resolution" in output
    assert "project" in output
    assert "bundled" in output
    assert str(root) in output or "skills" in output.lower()
    assert " 1 " in output or output.rstrip().endswith("1")


def test_cmd_help_includes_grouped_sections():
    import io
    from rich.console import Console

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)
    cmd_help(console=console)
    output = buf.getvalue()
    assert "Topics" in output
    assert "skills" in output
    assert "paths" in output
    assert "config" in output
    assert "context" in output
    assert "chains" in output
    assert "theme" in output
    assert "CLI usage examples" in output
    assert "skill-assay config show" in output
    assert "skill-assay context show" in output
    assert "skill-assay chain list" in output
    assert "skill-assay theme ocean" in output


def test_cmd_help_includes_paths_command():
    import io
    from rich.console import Console

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)
    cmd_help(console=console)
    output = buf.getvalue()
    assert "skill-assay paths" in output
    assert "skill-assay doctor" in output
    assert "coming soon" not in output.lower()


def test_main_paths_subcommand(monkeypatch):
    import sys
    from skill_assay.cli import main

    monkeypatch.setattr("skill_assay.cli.cmd_paths", lambda **kwargs: 0)

    argv = sys.argv
    sys.argv = ["skill-assay", "paths"]
    try:
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0
    finally:
        sys.argv = argv


def test_interactive_paths_dispatches(monkeypatch):
    import io
    from rich.console import Console

    responses = iter(["4", "1", "b", "0"])
    monkeypatch.setattr("builtins.input", lambda _: next(responses))

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    cmd_interactive(console=console)

    output = buf.getvalue()
    assert "Paths" in output
    assert "Skill path resolution" in output
    assert "not yet implemented" not in output.lower()


def test_parse_nav_exit_and_back():
    assert _parse_nav("0") == (None, _NAV_EXIT)
    assert _parse_nav("q") == (None, _NAV_EXIT)
    assert _parse_nav("quit") == (None, _NAV_EXIT)
    assert _parse_nav("b") == (None, _NAV_BACK)
    assert _parse_nav("back") == (None, _NAV_BACK)
    assert _parse_nav("list") == ("list", None)
    assert _parse_nav(None) == (None, _NAV_BACK)
    assert _parse_nav("") == ("", None)


def test_cmd_paths_submenu_edit_project(tmp_path, monkeypatch):
    import io
    from rich.console import Console

    project_skills = tmp_path / "my-skills"
    project_skills.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    monkeypatch.setenv("SKILL_ASSAY_CONFIG_DIR", str(tmp_path / "no-global"))

    responses = iter(["3", str(project_skills), "b"])
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    cmd_paths_submenu(console=console, input_fn=lambda _: next(responses))

    config_path = repo / ".skill-assay.yaml"
    assert config_path.is_file()
    assert str(project_skills.resolve()) in config_path.read_text(encoding="utf-8")


def test_cmd_paths_submenu_view_bundled(tmp_path, monkeypatch):
    import io
    from rich.console import Console

    monkeypatch.chdir(tmp_path)
    responses = iter(["2", "b"])
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    cmd_paths_submenu(console=console, input_fn=lambda _: next(responses))
    output = buf.getvalue()
    assert "Bundled registry (read-only)" in output


def test_cmd_paths_submenu_edit_external(tmp_path, monkeypatch):
    import io
    from rich.console import Console

    external = tmp_path / "external-skills"
    external.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    monkeypatch.setenv("SKILL_ASSAY_CONFIG_DIR", str(tmp_path / "no-global"))

    responses = iter(["4", "a", str(external), "", "b"])
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    cmd_paths_submenu(console=console, input_fn=lambda _: next(responses))

    config_path = repo / ".skill-assay.yaml"
    assert config_path.is_file()
    assert str(external.resolve()) in config_path.read_text(encoding="utf-8")


def test_interactive_doctor_dispatches(monkeypatch):
    import io
    from rich.console import Console

    responses = iter(["5", "q"])
    monkeypatch.setattr("builtins.input", lambda _: next(responses))

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    cmd_interactive(console=console)

    output = buf.getvalue()
    assert "DEPS" in output
    assert "LOAD" in output
    assert "ENVS" in output
    assert "manifest requirements" in output.lower()


def test_cmd_doctor_reports_ok_skill(tmp_path, monkeypatch):
    import io
    from rich.console import Console

    skill_dir = tmp_path / "skills" / "security" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "manifest.yaml").write_text(
        "name: security/demo\nversion: 0.1.0\ndescription: test\n"
        "parameters:\n  type: object\n  properties: {}\n",
        encoding="utf-8",
    )
    (skill_dir / "skill.py").write_text(
        "from skill_assay.core.base_skill import BaseSkill\n"
        "class DemoSkill(BaseSkill):\n"
        "    def execute(self, **kwargs):\n"
        "        return {}\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    assert cmd_doctor(skill_id="security/demo", console=console) == 0

    output = buf.getvalue()
    assert "security/demo" in output
    assert " ok " in output or "ok" in output


def test_cmd_doctor_reports_missing_env_vars(tmp_path, monkeypatch):
    import io
    from rich.console import Console

    skill_dir = tmp_path / "skills" / "monitoring" / "needs_key"
    skill_dir.mkdir(parents=True)
    (skill_dir / "manifest.yaml").write_text(
        "name: monitoring/needs_key\nversion: 0.1.0\ndescription: test\n"
        "parameters:\n  type: object\n  properties: {}\n"
        "env_vars:\n  ETHERSCAN_API_KEY:\n    required: true\n",
        encoding="utf-8",
    )
    (skill_dir / "skill.py").write_text(
        "from skill_assay.core.base_skill import BaseSkill\n"
        "class NeedsKeySkill(BaseSkill):\n"
        "    def execute(self, **kwargs):\n"
        "        return {}\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ETHERSCAN_API_KEY", raising=False)

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    assert cmd_doctor(skill_id="monitoring/needs_key", console=console) == 1

    output = buf.getvalue()
    assert "monitoring/needs_key" in output
    assert "ETHERSCAN_API_KEY" in output


def test_cmd_doctor_reports_missing_deps(tmp_path, monkeypatch):
    import io
    from rich.console import Console

    skill_dir = tmp_path / "skills" / "demo" / "needs_pkg"
    skill_dir.mkdir(parents=True)
    (skill_dir / "manifest.yaml").write_text(
        "name: demo/needs_pkg\nversion: 0.1.0\ndescription: test\n"
        "parameters:\n  type: object\n  properties: {}\n"
        "requirements:\n  - totally_missing_pkg_xyz\n",
        encoding="utf-8",
    )
    (skill_dir / "skill.py").write_text(
        "from skill_assay.core.base_skill import BaseSkill\n"
        "class NeedsPkgSkill(BaseSkill):\n"
        "    def execute(self, **kwargs):\n"
        "        return {}\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, package=None: None)

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    assert cmd_doctor(skill_id="demo/needs_pkg", console=console) == 1

    output = buf.getvalue()
    assert "fail" in output
    assert "demo/needs_pkg" in output


def test_main_doctor_subcommand(monkeypatch):
    import sys
    from skill_assay.cli import main

    monkeypatch.setattr("skill_assay.cli.cmd_doctor", lambda **kwargs: 0)

    argv = sys.argv
    sys.argv = ["skill-assay", "doctor", "security/demo"]
    try:
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0
    finally:
        sys.argv = argv


def test_main_config_subcommand(monkeypatch):
    import sys
    from skill_assay.cli import main

    monkeypatch.setattr("skill_assay.cli.cmd_config_show", lambda **kwargs: 0)

    argv = sys.argv
    sys.argv = ["skill-assay", "config", "show"]
    try:
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0
    finally:
        sys.argv = argv


def test_cmd_help_includes_mail_command():
    import io
    from rich.console import Console

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)
    cmd_help(console=console)
    output = buf.getvalue()
    assert "skill-assay mail" in output


def test_main_mail_subcommand(monkeypatch):
    import sys
    from skill_assay.cli import main

    monkeypatch.setattr("skill_assay.cli.cmd_mail", lambda *a, **k: 0)

    argv = sys.argv
    sys.argv = ["skill-assay", "mail", "addressbook", "show"]
    try:
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0
    finally:
        sys.argv = argv


def test_cmd_mail_addressbook_add(tmp_path, monkeypatch):
    import io
    from rich.console import Console

    from skill_assay.cli_mail import cmd_mail_addressbook_add

    monkeypatch.setenv("SKILL_ASSAY_CONFIG_DIR", str(tmp_path / "cfg"))
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=100)
    rc = cmd_mail_addressbook_add(
        console=console,
        display_name="Test User",
        email="test@example.com",
    )
    assert rc == 0
    ab = tmp_path / "cfg" / "addressbook.yaml"
    assert ab.is_file()


def test_interactive_mail_dispatches(monkeypatch):
    import io
    from rich.console import Console

    from skill_assay.cli import cmd_interactive

    responses = iter(["7", "1", "q"])
    monkeypatch.setattr("builtins.input", lambda _: next(responses))

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    cmd_interactive(console=console)

    output = buf.getvalue()
    assert "mail>" in output.lower() or "Address book" in output


def test_cmd_theme_sets_name_directly(isolated_theme_environment):
    import io
    import yaml
    from rich.console import Console

    _repo, config_dir = isolated_theme_environment
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    rc = cmd_theme("ocean", console=console)
    assert rc == 0

    saved = yaml.safe_load((config_dir / "config.yaml").read_text(encoding="utf-8"))
    assert saved["presentation"]["theme"] == "ocean"
    assert "Saved global theme 'ocean'" in buf.getvalue()


def test_cmd_theme_rejects_unknown_name(isolated_theme_environment):
    import io
    from rich.console import Console

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    rc = cmd_theme("unknown", console=console)
    assert rc == 1
    assert "Unknown theme" in buf.getvalue()


def test_main_theme_subcommand(monkeypatch, isolated_theme_environment):
    import sys
    import yaml
    from skill_assay.cli import main

    _repo, config_dir = isolated_theme_environment
    argv = sys.argv
    sys.argv = ["skill-assay", "theme", "mono"]
    try:
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0
    finally:
        sys.argv = argv

    saved = yaml.safe_load((config_dir / "config.yaml").read_text(encoding="utf-8"))
    assert saved["presentation"]["theme"] == "mono"


def test_main_theme_interactive_subcommand(monkeypatch, isolated_theme_environment):
    import sys
    from skill_assay.cli import main

    monkeypatch.setattr("builtins.input", lambda _: "2")
    argv = sys.argv
    sys.argv = ["skill-assay", "theme"]
    try:
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0
    finally:
        sys.argv = argv
