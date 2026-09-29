import importlib.util
import subprocess
import sys
import warnings
import zipfile
from pathlib import Path

import pytest

from skill_assay.core.loader import (
    SKILL_ASSAY_SKILL_PATH_ENV,
    SkillLoader,
    SkillAssayIdentityWarning,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_load_skill_not_found():
    with pytest.raises(FileNotFoundError):
        SkillLoader.load_skill("nonexistent_skill_path_12345")


def test_load_skill_registry_has_manifest():
    bundle = SkillLoader.load_skill("security/prompt_injection_firewall")
    assert bundle["manifest"].get("name") == "security/prompt_injection_firewall"
    assert bundle["registry_id"] == "security/prompt_injection_firewall"
    assert bundle["class"].__name__ == "PromptInjectionFirewallSkill"
    assert SkillLoader.get_skill_class(bundle) is bundle["class"]


def test_load_skill_inspect_mode_skips_module_exec():
    bundle = SkillLoader.load_skill(
        "security/prompt_injection_firewall",
        execute_module=False,
    )
    assert bundle["manifest"].get("name") == "security/prompt_injection_firewall"
    assert bundle["instructions"]
    assert bundle["module"] is None
    assert bundle["class"] is None
    with pytest.raises(KeyError, match="execute_module=True"):
        SkillLoader.get_skill_class(bundle)


def test_load_skill_module_import_error_after_preflight(tmp_path, monkeypatch):
    skill_dir = tmp_path / "skills" / "demo" / "hidden_import"
    skill_dir.mkdir(parents=True)
    (skill_dir / "manifest.yaml").write_text(
        "name: demo/hidden_import\nversion: 0.1.0\ndescription: test\n"
        "parameters:\n  type: object\n  properties: {}\n",
        encoding="utf-8",
    )
    (skill_dir / "skill.py").write_text(
        "import undeclared_hidden_pkg\n"
        "from skill_assay.core.base_skill import BaseSkill\n"
        "class HiddenImportSkill(BaseSkill):\n"
        "    def execute(self, **kwargs):\n"
        "        return {}\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ImportError, match="Manifest requirement pre-flight passed"):
        SkillLoader.load_skill("demo/hidden_import")


def test_load_skill_can_skip_requirement_check():
    """Packaging smoke installs base wheel only; optional extras may be absent."""
    bundle = SkillLoader.load_skill(
        "wellness/mental_coach",
        check_requirements=False,
    )
    assert bundle["manifest"].get("name") == "wellness/mental_coach"
    assert bundle["class"].__name__ == "MentalCoachSkill"


def test_load_skill_missing_requirements_suggests_skill_extra(monkeypatch):
    real_find_spec = importlib.util.find_spec

    def fake_find_spec(name, package=None):
        if name == "google.genai":
            return None
        return real_find_spec(name, package)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)

    with pytest.raises(
        ImportError, match=r"agent-skill-assay\[wellness_mental_coach\]"
    ):
        SkillLoader.load_skill("wellness/mental_coach")


def test_load_skill_unpinned_requirement_ignores_installed_version(
    tmp_path, monkeypatch
):
    """Unpinned manifest deps only require importability, not a version match."""
    skill_dir = tmp_path / "skills" / "demo" / "unpinned"
    skill_dir.mkdir(parents=True)
    (skill_dir / "manifest.yaml").write_text(
        "name: demo/unpinned\nversion: 0.1.0\ndescription: test\n"
        "parameters:\n  type: object\n  properties: {}\n"
        "requirements:\n  - demo_pkg\n",
        encoding="utf-8",
    )
    (skill_dir / "skill.py").write_text(
        "from skill_assay.core.base_skill import BaseSkill\n"
        "class UnpinnedSkill(BaseSkill):\n"
        "    def execute(self, **kwargs):\n"
        "        return {}\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        importlib.util, "find_spec", lambda name, package=None: object()
    )

    bundle = SkillLoader.load_skill("demo/unpinned")
    assert bundle["manifest"]["name"] == "demo/unpinned"


def test_load_skill_rejects_unsatisfied_version_specifier(tmp_path, monkeypatch):
    skill_dir = tmp_path / "skills" / "demo" / "pinned"
    skill_dir.mkdir(parents=True)
    (skill_dir / "manifest.yaml").write_text(
        "name: demo/pinned\nversion: 0.1.0\ndescription: test\n"
        "parameters:\n  type: object\n  properties: {}\n"
        "requirements:\n  - demo_pkg>=2.0.0\n",
        encoding="utf-8",
    )
    (skill_dir / "skill.py").write_text(
        "from skill_assay.core.base_skill import BaseSkill\n"
        "class PinnedSkill(BaseSkill):\n"
        "    def execute(self, **kwargs):\n"
        "        return {}\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        importlib.util, "find_spec", lambda name, package=None: object()
    )
    monkeypatch.setattr("skill_assay.core.extras.version", lambda _name: "1.0.0")

    with pytest.raises(ImportError, match="unsatisfied version requirements"):
        SkillLoader.load_skill("demo/pinned")


def test_load_skill_accepts_satisfied_version_specifier(tmp_path, monkeypatch):
    skill_dir = tmp_path / "skills" / "demo" / "pinned_ok"
    skill_dir.mkdir(parents=True)
    (skill_dir / "manifest.yaml").write_text(
        "name: demo/pinned_ok\nversion: 0.1.0\ndescription: test\n"
        "parameters:\n  type: object\n  properties: {}\n"
        "requirements:\n  - demo_pkg>=2.0.0\n",
        encoding="utf-8",
    )
    (skill_dir / "skill.py").write_text(
        "from skill_assay.core.base_skill import BaseSkill\n"
        "class PinnedOkSkill(BaseSkill):\n"
        "    def execute(self, **kwargs):\n"
        "        return {}\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        importlib.util, "find_spec", lambda name, package=None: object()
    )
    monkeypatch.setattr("skill_assay.core.extras.version", lambda _name: "2.1.0")

    bundle = SkillLoader.load_skill("demo/pinned_ok")
    assert bundle["class"].__name__ == "PinnedOkSkill"


def test_load_skill_class_is_instantiable():
    bundle = SkillLoader.load_skill("security/prompt_injection_firewall")
    skill = bundle["class"]()
    result = skill.execute({"source_text": "hello world"})
    assert "error" not in result


def test_discover_skill_class_requires_exactly_one_subclass(tmp_path, monkeypatch):
    skill_dir = tmp_path / "skills" / "broken" / "no_class"
    skill_dir.mkdir(parents=True)
    (skill_dir / "manifest.yaml").write_text(
        "name: broken/no_class\nversion: 0.1.0\ndescription: test\n"
        "parameters:\n  type: object\n  properties: {}\n",
        encoding="utf-8",
    )
    (skill_dir / "skill.py").write_text(
        "def helper():\n    return 1\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ImportError, match="found none"):
        SkillLoader.load_skill("broken/no_class")


def test_discover_skill_class_rejects_multiple_subclasses(tmp_path, monkeypatch):
    skill_dir = tmp_path / "skills" / "broken" / "two_classes"
    skill_dir.mkdir(parents=True)
    (skill_dir / "manifest.yaml").write_text(
        "name: broken/two_classes\nversion: 0.1.0\ndescription: test\n"
        "parameters:\n  type: object\n  properties: {}\n",
        encoding="utf-8",
    )
    (skill_dir / "skill.py").write_text(
        "from skill_assay.core.base_skill import BaseSkill\n"
        "class FirstSkill(BaseSkill):\n"
        "    @property\n"
        "    def manifest(self):\n"
        "        return {}\n"
        "    def execute(self, params):\n"
        "        return {}\n"
        "class SecondSkill(BaseSkill):\n"
        "    @property\n"
        "    def manifest(self):\n"
        "        return {}\n"
        "    def execute(self, params):\n"
        "        return {}\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ImportError, match="found: \\['FirstSkill', 'SecondSkill'\\]"):
        SkillLoader.load_skill("broken/two_classes")


def test_load_skill_registry_id_matches_bundled_skills():
    with warnings.catch_warnings():
        warnings.simplefilter("error", SkillAssayIdentityWarning)
        bundle = SkillLoader.load_skill("wellness/mental_coach")
    assert bundle["registry_id"] == "wellness/mental_coach"


def test_flat_skill_skips_identity_validation(tmp_path, monkeypatch):
    skill_dir = tmp_path / "skills" / "my_flat_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "manifest.yaml").write_text(
        "name: different_from_path\nversion: 0.1.0\ndescription: test\n"
        "parameters:\n  type: object\n  properties: {}\n",
        encoding="utf-8",
    )
    (skill_dir / "skill.py").write_text(
        "from skill_assay.core.base_skill import BaseSkill\n"
        "class FlatSkill(BaseSkill):\n"
        "    def execute(self, **kwargs):\n"
        "        return {}\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    with warnings.catch_warnings():
        warnings.simplefilter("error", SkillAssayIdentityWarning)
        bundle = SkillLoader.load_skill("my_flat_skill")
    assert bundle["registry_id"] is None


def test_registry_layout_mismatch_warns(tmp_path, monkeypatch):
    skill_dir = tmp_path / "skills" / "monitoring" / "bad_manifest"
    skill_dir.mkdir(parents=True)
    (skill_dir / "manifest.yaml").write_text(
        "name: monitoring/wrong_name\nversion: 0.1.0\ndescription: test\n"
        "parameters:\n  type: object\n  properties: {}\n",
        encoding="utf-8",
    )
    (skill_dir / "skill.py").write_text(
        "from skill_assay.core.base_skill import BaseSkill\n"
        "class BadManifestSkill(BaseSkill):\n"
        "    def execute(self, **kwargs):\n"
        "        return {}\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    with pytest.warns(SkillAssayIdentityWarning, match="does not match registry path"):
        bundle = SkillLoader.load_skill("monitoring/bad_manifest")
    assert bundle["registry_id"] == "monitoring/bad_manifest"


def test_registry_layout_missing_name_warns(tmp_path, monkeypatch):
    skill_dir = tmp_path / "skills" / "security" / "no_name"
    skill_dir.mkdir(parents=True)
    (skill_dir / "manifest.yaml").write_text(
        "version: 0.1.0\ndescription: test\n"
        "parameters:\n  type: object\n  properties: {}\n",
        encoding="utf-8",
    )
    (skill_dir / "skill.py").write_text(
        "from skill_assay.core.base_skill import BaseSkill\n"
        "class NoNameSkill(BaseSkill):\n"
        "    def execute(self, **kwargs):\n"
        "        return {}\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    with pytest.warns(SkillAssayIdentityWarning, match="missing 'name'"):
        bundle = SkillLoader.load_skill("security/no_name")
    assert bundle["registry_id"] == "security/no_name"


def test_env_root_registry_layout_validates(tmp_path, monkeypatch):
    root = tmp_path / "custom_skills"
    skill_dir = root / "monitoring" / "env_registry_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "manifest.yaml").write_text(
        "name: monitoring/env_registry_skill\nversion: 0.1.0\ndescription: test\n"
        "parameters:\n  type: object\n  properties: {}\n",
        encoding="utf-8",
    )
    (skill_dir / "skill.py").write_text(
        "from skill_assay.core.base_skill import BaseSkill\n"
        "class EnvRegistrySkill(BaseSkill):\n"
        "    def execute(self, **kwargs):\n"
        "        return {}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(SKILL_ASSAY_SKILL_PATH_ENV, str(root))
    monkeypatch.chdir(tmp_path)
    with warnings.catch_warnings():
        warnings.simplefilter("error", SkillAssayIdentityWarning)
        bundle = SkillLoader.load_skill("monitoring/env_registry_skill")
    assert bundle["registry_id"] == "monitoring/env_registry_skill"


def test_resolve_skill_from_project_skills_dir(tmp_path, monkeypatch):
    skill_dir = tmp_path / "skills" / "local_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "manifest.yaml").write_text(
        "name: local_skill\nversion: 0.1.0\ndescription: test\n"
        "parameters:\n  type: object\n  properties: {}\n",
        encoding="utf-8",
    )
    (skill_dir / "skill.py").write_text(
        "from skill_assay.core.base_skill import BaseSkill\n"
        "class LocalSkill(BaseSkill):\n"
        "    def execute(self, **kwargs):\n"
        "        return {}\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    bundle = SkillLoader.load_skill("local_skill")
    assert bundle["manifest"]["name"] == "local_skill"
    assert bundle["registry_id"] is None


def test_resolve_skill_from_env_path(tmp_path, monkeypatch):
    root = tmp_path / "custom_skills"
    skill_dir = root / "env_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "manifest.yaml").write_text(
        "name: env_skill\nversion: 0.1.0\ndescription: test\n"
        "parameters:\n  type: object\n  properties: {}\n",
        encoding="utf-8",
    )
    (skill_dir / "skill.py").write_text(
        "from skill_assay.core.base_skill import BaseSkill\n"
        "class EnvSkill(BaseSkill):\n"
        "    def execute(self, **kwargs):\n"
        "        return {}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(SKILL_ASSAY_SKILL_PATH_ENV, str(root))
    monkeypatch.chdir(tmp_path)
    bundle = SkillLoader.load_skill("env_skill")
    assert bundle["manifest"]["name"] == "env_skill"
    assert bundle["registry_id"] is None


def test_resolve_skill_prefers_env_over_cwd_legacy(tmp_path, monkeypatch):
    """Legacy mode (no YAML config): SKILL_ASSAY_SKILL_PATH wins over cwd ./skills/."""
    env_root = tmp_path / "env_root"
    env_skill = env_root / "shared_id"
    env_skill.mkdir(parents=True)
    (env_skill / "manifest.yaml").write_text(
        "name: from_env\nversion: 0.1.0\ndescription: test\n"
        "parameters:\n  type: object\n  properties: {}\n",
        encoding="utf-8",
    )
    (env_skill / "skill.py").write_text(
        "from skill_assay.core.base_skill import BaseSkill\n"
        "class EnvSkill(BaseSkill):\n"
        "    def execute(self, **kwargs):\n"
        "        return {'source': 'env'}\n",
        encoding="utf-8",
    )

    cwd_skill = tmp_path / "skills" / "shared_id"
    cwd_skill.mkdir(parents=True)
    (cwd_skill / "manifest.yaml").write_text(
        "name: from_cwd\nversion: 0.1.0\ndescription: test\n"
        "parameters:\n  type: object\n  properties: {}\n",
        encoding="utf-8",
    )
    (cwd_skill / "skill.py").write_text(
        "from skill_assay.core.base_skill import BaseSkill\n"
        "class CwdSkill(BaseSkill):\n"
        "    def execute(self, **kwargs):\n"
        "        return {'source': 'cwd'}\n",
        encoding="utf-8",
    )

    monkeypatch.setenv(SKILL_ASSAY_SKILL_PATH_ENV, str(env_root))
    monkeypatch.chdir(tmp_path)
    bundle = SkillLoader.load_skill("shared_id")
    assert bundle["manifest"]["name"] == "from_env"


def test_resolve_skill_prefers_cwd_over_env_in_configured_mode(tmp_path, monkeypatch):
    """Configured mode default order: project ./skills/ wins over env path."""
    from skill_assay.core.config import PROJECT_CONFIG_FILENAME, clear_config_cache

    env_root = tmp_path / "env_root"
    env_skill = env_root / "shared_id"
    env_skill.mkdir(parents=True)
    (env_skill / "manifest.yaml").write_text(
        "name: from_env\nversion: 0.1.0\ndescription: test\n"
        "parameters:\n  type: object\n  properties: {}\n",
        encoding="utf-8",
    )
    (env_skill / "skill.py").write_text(
        "from skill_assay.core.base_skill import BaseSkill\n"
        "class EnvSkill(BaseSkill):\n"
        "    def execute(self, **kwargs):\n"
        "        return {'source': 'env'}\n",
        encoding="utf-8",
    )

    project_dir = tmp_path / "repo"
    cwd_skill = project_dir / "skills" / "shared_id"
    cwd_skill.mkdir(parents=True)
    (cwd_skill / "manifest.yaml").write_text(
        "name: from_cwd\nversion: 0.1.0\ndescription: test\n"
        "parameters:\n  type: object\n  properties: {}\n",
        encoding="utf-8",
    )
    (cwd_skill / "skill.py").write_text(
        "from skill_assay.core.base_skill import BaseSkill\n"
        "class CwdSkill(BaseSkill):\n"
        "    def execute(self, **kwargs):\n"
        "        return {'source': 'cwd'}\n",
        encoding="utf-8",
    )
    (project_dir / PROJECT_CONFIG_FILENAME).write_text(
        "paths:\n  project: auto\n",
        encoding="utf-8",
    )

    monkeypatch.setenv(SKILL_ASSAY_SKILL_PATH_ENV, str(env_root))
    monkeypatch.chdir(project_dir)
    clear_config_cache()
    bundle = SkillLoader.load_skill("shared_id")
    assert bundle["manifest"]["name"] == "from_cwd"


def test_wheel_includes_skill_manifest(tmp_path):
    wheel_dir = tmp_path / "wheels"
    wheel_dir.mkdir()

    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            str(REPO_ROOT),
            "--no-deps",
            "--no-build-isolation",
            "-w",
            str(wheel_dir),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    wheels = list(wheel_dir.glob("agent_skill_assay-*.whl"))
    assert len(wheels) == 1
    wheel_path = wheels[0]

    with zipfile.ZipFile(wheel_path) as archive:
        names = archive.namelist()
        assert any(
            n.endswith("skills/wellness/mental_coach/manifest.yaml") for n in names
        )
        assert any(
            n.endswith("skills/security/prompt_injection_firewall/manifest.yaml")
            for n in names
        )


def test_to_ollama_prompt():
    sample_bundle = {
        "manifest": {
            "name": "test_ollama_skill",
            "description": "A very useful test skill.",
            "parameters": {
                "type": "object",
                "properties": {
                    "arg1": {"type": "string", "description": "The first arg"}
                },
                "required": ["arg1"],
            },
        }
    }

    prompt = SkillLoader.to_ollama_prompt(sample_bundle)
    assert "### Tool: `test_ollama_skill`" in prompt
    assert "**Description:** A very useful test skill." in prompt
    assert "- `arg1` (string): The first arg [Required]" in prompt


def test_to_gemini_tool():
    sample_bundle = {
        "manifest": {
            "name": "monitoring/kpi_gate",
            "parameters": {
                "type": "object",
                "properties": {"param1": {"type": "string"}},
            },
        }
    }
    tool = SkillLoader.to_gemini_tool(sample_bundle)
    decl = tool.function_declarations[0]
    assert decl.name == "monitoring_kpi_gate"
    assert type(tool).__name__ == "Tool"
    # Gemini requires UPPERCASE types for Protobufs
    assert decl.parameters.type.name == "OBJECT"
    assert decl.parameters.properties["param1"].type.name == "STRING"


def test_sanitize_gemini_tool_name():
    assert (
        SkillLoader._sanitize_gemini_tool_name("wellness/mental_coach")
        == "wellness_mental_coach"
    )
    assert SkillLoader._sanitize_gemini_tool_name("kpi_gate") == "kpi_gate"
    assert SkillLoader._sanitize_gemini_tool_name("") == "unknown_tool"
    assert SkillLoader._sanitize_gemini_tool_name("a" * 80).startswith("a")
    assert len(SkillLoader._sanitize_gemini_tool_name("a" * 80)) == 64


def test_to_claude_tool():
    sample_bundle = {
        "manifest": {
            "name": "test_claude_skill",
            "description": "desc",
            "parameters": {
                "type": "object",
                "properties": {"arg_claude": {"type": "string"}},
            },
        }
    }
    tool = SkillLoader.to_claude_tool(sample_bundle)
    assert tool["name"] == "test_claude_skill"
    assert tool["input_schema"]["type"] == "object"

    registry_bundle = {
        "manifest": {
            "name": "security/prompt_injection_firewall",
            "description": "desc",
            "parameters": {"type": "object", "properties": {}},
        }
    }
    assert (
        SkillLoader.to_claude_tool(registry_bundle)["name"]
        == "security_prompt_injection_firewall"
    )


def test_sanitize_openai_tool_name():
    assert (
        SkillLoader._sanitize_openai_tool_name("wellness/mental_coach")
        == "wellness_mental_coach"
    )
    assert SkillLoader._sanitize_openai_tool_name("kpi_gate") == "kpi_gate"
    assert SkillLoader._sanitize_openai_tool_name("") == "unknown_tool"
    assert SkillLoader._sanitize_openai_tool_name("a" * 80).startswith("a")
    assert len(SkillLoader._sanitize_openai_tool_name("a" * 80)) == 64


def test_to_openai_tool():
    sample_bundle = {
        "manifest": {
            "name": "wellness/mental_coach",
            "description": "Evaluate site policy.",
            "parameters": {
                "type": "object",
                "properties": {"target_url": {"type": "string", "description": "URL"}},
                "required": ["target_url"],
            },
        }
    }
    tool = SkillLoader.to_openai_tool(sample_bundle)
    assert tool["type"] == "function"
    assert tool["function"]["name"] == "wellness_mental_coach"
    assert tool["function"]["description"] == "Evaluate site policy."
    assert tool["function"]["parameters"]["type"] == "object"
    assert "target_url" in tool["function"]["parameters"]["properties"]


def test_sanitize_deepseek_tool_name():
    assert (
        SkillLoader._sanitize_deepseek_tool_name("wellness/mental_coach")
        == "wellness_mental_coach"
    )


def test_to_deepseek_tool():
    sample_bundle = {
        "manifest": {
            "name": "wellness/mental_coach",
            "description": "Evaluate site policy.",
            "parameters": {
                "type": "object",
                "properties": {"target_url": {"type": "string", "description": "URL"}},
                "required": ["target_url"],
            },
        }
    }
    tool = SkillLoader.to_deepseek_tool(sample_bundle)
    assert tool["type"] == "function"
    assert tool["function"]["name"] == "wellness_mental_coach"


def test_sanitize_bedrock_tool_name():
    assert (
        SkillLoader._sanitize_bedrock_tool_name("wellness/mental_coach")
        == "wellness_mental_coach"
    )
    assert SkillLoader._sanitize_bedrock_tool_name("") == "unknown_tool"


def test_to_bedrock_tool():
    sample_bundle = {
        "manifest": {
            "name": "wellness/mental_coach",
            "description": "Evaluate site policy.",
            "parameters": {
                "type": "object",
                "properties": {"target_url": {"type": "string", "description": "URL"}},
                "required": ["target_url"],
            },
        }
    }
    tool = SkillLoader.to_bedrock_tool(sample_bundle)
    spec = tool["toolSpec"]
    assert spec["name"] == "wellness_mental_coach"
    assert spec["description"] == "Evaluate site policy."
    assert spec["inputSchema"]["json"]["type"] == "object"
    assert "target_url" in spec["inputSchema"]["json"]["properties"]


def test_bedrock_tool_name_matches_openai_sanitization():
    sample_bundle = {
        "manifest": {
            "name": "monitoring/kpi_gate",
            "description": "Evaluate metrics.",
            "parameters": {"type": "object", "properties": {}},
        }
    }
    openai_name = SkillLoader.to_openai_tool(sample_bundle)["function"]["name"]
    bedrock_name = SkillLoader.to_bedrock_tool(sample_bundle)["toolSpec"]["name"]
    assert bedrock_name == openai_name == "monitoring_kpi_gate"
