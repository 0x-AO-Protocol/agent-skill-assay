"""
Local execute demo for dev_tools/upgrade_gate.

Runs the deterministic upgrade gate entirely offline against the in-bundle
fixtures: a digest of one watched repository's events, an assessment of two
pinned dependencies against a release snapshot, and two fail-closed contract
errors from the closed registry (an event dated after as_of, an unsupported
range). No API keys, no network; the host supplies every input.
"""

import json
from pathlib import Path

from skill_assay.core.loader import SkillLoader


def _bundle_dir(module) -> Path:
    return Path(module.__file__).resolve().parent


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _print_error(result: dict) -> None:
    print(f"  status: {result['status']}")
    print(f"  contract error: {result['error']['code']} (fail-closed)")
    print(f"  detail: {result['error']['detail']}")


def run_demo() -> None:
    print("Loading dev_tools/upgrade_gate...")
    bundle = SkillLoader.load_skill("dev_tools/upgrade_gate")
    skill = bundle["class"]()
    fixtures = _bundle_dir(bundle["module"]) / "fixtures"

    print("\nScenario 1: digest the events of a watched repository")
    digest = skill.execute(_load_json(fixtures / "example_digest_params.json"))
    print(f"  status: {digest['status']}")
    print(f"  window: {digest['window']}")
    for entry in digest["per_repo"]:
        print(f"  repo {entry['repo']} counts={entry['counts']}")
        for jump in entry["version_jumps"]:
            print(f"    jump: {jump['from']} -> {jump['to']} ({jump['kind']})")
        print(f"    breaking_hits: {entry['breaking_hits']}")
        print(f"    path_hits: {entry['path_hits']}")
        print(f"    notable: {entry['notable']}")
        print(
            f"    dropped_before_since: {entry['dropped_before_since']} "
            f"unparsed_versions: {entry['unparsed_versions']}"
        )
    print(f"  fired_any: {digest['fired_any']} insufficient: {digest['insufficient']}")

    print("\nScenario 2: assess the pinned dependencies against a release snapshot")
    assessed = skill.execute(_load_json(fixtures / "example_assess_params.json"))
    print(f"  status: {assessed['status']}")
    for result in assessed["results"]:
        print(
            f"  {result['name']} ({result['ecosystem']}) current={result['current']} "
            f"latest={result['latest']} latest_compatible={result['latest_compatible']}"
        )
        print(
            f"    verdict: {result['verdict']} target={result['target']} "
            f"reasons={result['reasons']}"
        )
        print(f"    drift={result['drift']} signals={result['signals']}")
    print(f"  summary: {assessed['summary']}")
    print(
        f"  fired_any: {assessed['fired_any']} insufficient: {assessed['insufficient']} "
        f"stale_snapshot: {assessed['stale_snapshot']}"
    )

    print("\nScenario 3: an event dated after as_of (fail-closed contract error)")
    _print_error(
        skill.execute(_load_json(fixtures / "example_future_event_params.json"))
    )

    print("\nScenario 4: an unsupported range form (fail-closed contract error)")
    params = _load_json(fixtures / "example_assess_params.json")
    params["dependencies"][0]["range"] = "~=2"
    _print_error(skill.execute(params))

    print("\nDemo complete.")


if __name__ == "__main__":
    run_demo()
