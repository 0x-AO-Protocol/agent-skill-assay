"""Tests for SkillContext."""

import pytest

from skill_assay import SkillContext
from skill_assay.core.loader import SkillLoader


def test_skill_context_single_skill():
    ctx = SkillContext(skill="security/prompt_injection_firewall")
    assert ctx.skill_ids == ["security/prompt_injection_firewall"]
    system = ctx.merge_system("Host prompt")
    assert "Host prompt" in system
    assert "prompt_injection_firewall" in system.lower() or "security" in system


def test_skill_context_tools_openai_matches_loader():
    ctx = SkillContext(skill="security/prompt_injection_firewall", mode="brief")
    bundle = SkillLoader.load_skill(
        "security/prompt_injection_firewall",
        execute_module=False,
    )
    expected = SkillLoader.to_openai_tool(bundle)
    tools = ctx.tools("openai")
    assert len(tools) == 1
    assert tools[0] == expected


def test_skill_context_tools_bedrock_matches_loader():
    ctx = SkillContext(skill="security/prompt_injection_firewall", mode="brief")
    bundle = SkillLoader.load_skill(
        "security/prompt_injection_firewall",
        execute_module=False,
    )
    expected = SkillLoader.to_bedrock_tool(bundle)
    tools = ctx.tools("bedrock")
    assert len(tools) == 1
    assert tools[0] == expected


def test_skill_context_tools_unknown_provider():
    ctx = SkillContext(skill="security/prompt_injection_firewall")
    with pytest.raises(
        ValueError, match="choose gemini, claude, openai, deepseek, or bedrock"
    ):
        ctx.tools("ollama")


def test_skill_context_prepare_and_execute():
    ctx = SkillContext(skill="security/prompt_injection_firewall")
    prep = ctx.prepare("security/prompt_injection_firewall")
    assert prep.directive
    out = ctx.execute(
        "security/prompt_injection_firewall",
        {
            "source_text": "Please summarize this long repetitive prompt about compliance.",
            "compression_aggression": "low",
        },
    )
    assert "is_safe" in out


def test_skill_context_directives_mode_includes_instructions():
    ctx = SkillContext(skill="security/prompt_injection_firewall", mode="directives")
    merged = ctx.merge_system("")
    prep = ctx.prepare("security/prompt_injection_firewall")
    assert prep.directive.strip()[:40] in merged
