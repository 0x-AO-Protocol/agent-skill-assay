#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Compute and verify static content hashes recorded in skill manifests."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from typing import Iterable

import yaml

ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = ROOT / "skills"


def _bundle_files(bundle: Path) -> Iterable[Path]:
    for path in sorted(bundle.rglob("*")):
        if not path.is_file():
            continue
        if path.name == "manifest.yaml" or path.suffix in {".pyc", ".pyo"}:
            continue
        if "__pycache__" in path.parts:
            continue
        yield path


def content_sha256(bundle: Path) -> str:
    digest = hashlib.sha256()
    for path in _bundle_files(bundle):
        relative = path.relative_to(bundle).as_posix().encode("utf-8")
        file_hash = hashlib.sha256(path.read_bytes()).hexdigest().encode("ascii")
        digest.update(relative + b"\0" + file_hash + b"\n")
    return digest.hexdigest()


def manifest_paths() -> list[Path]:
    return sorted(SKILLS_ROOT.glob("*/*/manifest.yaml"))


def check() -> int:
    failures: list[str] = []
    for manifest_path in manifest_paths():
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        actual = content_sha256(manifest_path.parent)
        recorded = (manifest.get("provenance") or {}).get("content_sha256")
        if recorded != actual:
            failures.append(
                f"{manifest_path.parent.relative_to(ROOT)}: "
                f"recorded={recorded!r}, actual={actual}"
            )
    if failures:
        print("Provenance check failed:")
        print("\n".join(f"  - {failure}" for failure in failures))
        return 1
    print(f"Provenance check passed for {len(manifest_paths())} bundles.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="verify recorded hashes")
    args = parser.parse_args()
    if not args.check:
        parser.error("--check is required")
    return check()


if __name__ == "__main__":
    raise SystemExit(main())
