#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Small dependency-free secret scan for the current publication tree."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", ".venv", ".pytest_cache", "__pycache__", "build", "dist"}
SKIP_FILES = {
    "scripts/secret_scan.py",
    "scripts/public_text_lint.py",
    "governance/public_text_rules.yaml",
}

PATTERNS = (
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b")),
    ("github-pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("openai-style-key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
)


def iter_files():
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT).as_posix()
        if relative in SKIP_FILES or any(part in SKIP_DIRS for part in path.parts):
            continue
        yield path


def scan() -> list[tuple[str, int, str]]:
    findings = []
    for path in iter_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line_no, line in enumerate(text.splitlines(), 1):
            for name, pattern in PATTERNS:
                if pattern.search(line):
                    findings.append((path.relative_to(ROOT).as_posix(), line_no, name))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    findings = scan()
    if findings:
        for path, line, name in findings:
            print(f"{path}:{line}: {name}")
        return 1
    if not args.quiet:
        print("secret-scan: clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
