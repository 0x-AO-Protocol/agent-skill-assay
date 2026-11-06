"""Synthetic bundle that meets the standard profile; used by the framework tests."""

import re
from typing import Any, Dict, List

from skill_assay.core.base_skill import BaseSkill

REGISTRY_ID = "sample/word_tally"
VERSION = "0.1.0"
MAX_TEXT_CHARS = 10000
WORD = re.compile(r"[a-z]+")

MANIFEST: Dict[str, Any] = {
    "name": REGISTRY_ID,
    "version": VERSION,
    "parameters": {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    },
}


def _error(code: str, path: str) -> Dict[str, Any]:
    return {"status": "error", "error": {"code": code, "path": path}}


def tally(text: str) -> List[List[Any]]:
    """Return [word, count] pairs, sorted by word."""
    counts: Dict[str, int] = {}
    for word in WORD.findall(text.lower()):
        counts[word] = counts.get(word, 0) + 1
    return [[word, counts[word]] for word in sorted(counts)]


class WordTallySkill(BaseSkill):
    """Counts the words of a text."""

    @property
    def manifest(self) -> Dict[str, Any]:
        return MANIFEST

    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(params, dict):
            return _error("INVALID_REQUEST", "")
        text = params.get("text")
        if not isinstance(text, str):
            return _error("INVALID_REQUEST", "text")
        if len(text) > MAX_TEXT_CHARS:
            return _error("TEXT_TOO_LONG", "text")
        words = tally(text)
        return {
            "status": "ok",
            "words": words,
            "total": sum(count for _, count in words),
        }
