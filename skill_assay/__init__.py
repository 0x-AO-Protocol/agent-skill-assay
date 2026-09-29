"""Agent Skill Assay — installable agent skills framework."""

from skill_assay.context import SkillContext
from skill_assay.version_policy import emit_support_status_warning

emit_support_status_warning()

__all__ = ["SkillContext"]
