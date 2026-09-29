#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Check the current tree for non-public project references."""

from __future__ import annotations

import argparse
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", ".venv", ".pytest_cache", "__pycache__", "build", "dist"}
SKIP_FILES = {
    "governance/public_text_rules.yaml",
    "governance/branch_protection.json",
}

HASH_RULES = {
    "old-project-name": {
        "babf61c1a0824b743d9d0f7659a78c27fe37ca70d05dae181d7d9ca4f83c7f3d",
    },
    "old-upstream-reference": {
        "0a89e62ded68c92a068b16cd433ae084c83a7ca314f9a8a2d9bf35f05e26989e",
        "1e4d0b2fb1ddc82de184198593e192294ae8424e7bf7888c3d8d2c470282ccd4",
        "e6702ce3fa6f30a984b52043af533ed8aa12f27f5950446e5497b01e21ea7a92",
        "c5713223917d194c5e34e3a45193e5d293e6f34778bf23a55ff054e1efba100b",
    },
    "pending-founder-language": {
        "89176252dd5b76abb1d9791f599cda05337b0f3c67989a15be599f40180feffe",
    },
    "closed-mechanism-reference": {
        "16469bfdf88e27910f32103b02c4e2a2ef0db8324c9b996c33b3c5978c9b47f5",
        "07a213073bc33f604dd816dca82e551a87a13d382518e446128d89abca8121d9",
        "9bf8e929ce3b3c251183b4e4bf1ebe04aae7bc56e2a2f5c397553803eb12a9e5",
        "d4587d464509377de94f7454909b0b466b44669532e1f8b4aed2692f36fd2bb9",
        "427a2ba33a1a4a634b27545ccd96ce87b9afd3bb63ccfb3479be72e723d74603",
        "763134d8c5ca698e11ca8e8d8cb00c54a308165467d43b089c2fb9ef9e6e131b",
        "944cd4c3078472d8282be1d39e55df2ce7a8b6bda3e76c9668bf824192a45dbb",
        "a3decf8592fd649144d5df013ff4f975badb42214695bcd1a6fa2a13c55ce2de",
        "4a1bac87e46e4e7efca0c1095dfc0ef745699221d58ba938f5e57858674af7ab",
        "6181a95091be12c5a31c1c0cda081a40720c15f3058df07338ebeb30a186397d",
        "add7e520cfb1d51b819fe5deb80942f4ffe354d1d79cb4d41e83e6a8fdfdc8c7",
        "956df85ea851d8a0489de4e15e0ce0260f9485e404a9feec499f92ad9cf566bd",
        "8f7733bc4ca89aa668ec8f85b6745db894c43f6e67cd564c9eee9ea5e951f9d6",
    },
}
HASH_WINDOW = 3

REGEX_RULES = (
    (
        "numbered-internal-issue-reference",
        re.compile(
            r"(?i)(?:\bissue\s*#\d+\b|"
            r"github\.com/[^\s/]+/[^\s/]+/issues/\d+|"
            r"\b(?:fixes|refs|closes|closed)\s+#\d+\b)"
        ),
    ),
)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str
    text: str


def iter_files() -> Iterable[Path]:
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT).as_posix()
        if (
            relative in SKIP_FILES
            or any(part in SKIP_DIRS for part in path.parts)
            or any(part.endswith(".egg-info") for part in path.parts)
        ):
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if b"\x00" in data:
            continue
        yield path


def _token_hashes(text: str) -> set[str]:
    tokens = list(re.finditer(r"[a-z0-9]+", text.casefold()))
    hashes: set[str] = set()
    for start in range(len(tokens)):
        for end in range(start + 1, min(len(tokens), start + HASH_WINDOW) + 1):
            window = tokens[start:end]
            words = [token.group() for token in window]
            spaced = " ".join(words)
            hashes.add(hashlib.sha256(spaced.encode("utf-8")).hexdigest())
            separators = [
                text[left.end() : right.start()]
                for left, right in zip(window, window[1:])
            ]
            if all(re.fullmatch(r"[\s_-]*", gap) for gap in separators) and not (
                len(words) == 2
                and words[1].isdigit()
                and any(len(gap) > 3 for gap in separators)
            ):
                compact = "".join(words)
                hashes.add(hashlib.sha256(compact.encode("utf-8")).hexdigest())
    return hashes


def scan() -> list[Finding]:
    findings: list[Finding] = []
    for path in iter_files():
        relative = path.relative_to(ROOT).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            line_hashes = _token_hashes(line)
            for rule, hashes in HASH_RULES.items():
                if relative == "THIRD_PARTY_NOTICES" and rule == "old-project-name":
                    continue
                if line_hashes.intersection(hashes):
                    findings.append(Finding(relative, number, rule, "<redacted>"))
            for rule, pattern in REGEX_RULES:
                match = pattern.search(line)
                if match:
                    findings.append(Finding(relative, number, rule, match.group(0)))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    findings = scan()
    if findings:
        for finding in findings:
            print(f"{finding.path}:{finding.line}: {finding.rule}: " f"{finding.text}")
        return 1
    if not args.quiet:
        print("public-text-lint: clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
