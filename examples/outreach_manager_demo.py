"""
Local execute demo for office/outreach_manager.

Runs the deterministic outreach planner entirely offline against the
in-bundle fixtures on the synthetic demo profile: a target specification
compiled into a partitioned search plan with ready search URLs, two exported
rows ingested and scored against that plan, and two fail-closed contract
errors from the closed registry. No API keys, no network.
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
    print("Loading office/outreach_manager...")
    bundle = SkillLoader.load_skill("office/outreach_manager")
    skill = bundle["class"]()
    fixtures = _bundle_dir(bundle["module"]) / "fixtures"

    print("\nScenario 1: compile a partitioned search plan on the demo profile")
    compiled = skill.execute(_load_json(fixtures / "example_compile_params.json"))
    plan = compiled["plan"]
    print(f"  status: {compiled['status']}")
    print(
        f"  plan_id: {plan['plan_id']} (profile {plan['platform']} "
        f"{plan['profile_version']}, {plan['profile_origin']})"
    )
    for query in plan["queries"]:
        print(
            f"  {query['id']} partition={query['partition']} filters={query['filters']}"
        )
        print(f"    keywords: {query['keywords']}")
        print(f"    search_url: {query['search_url']}")
    print(f"  pacing: {plan['pacing']}")
    print(f"  partitioning: {plan['partitioning']}")

    print("\nScenario 2: ingest two exported rows against that plan")
    params = _load_json(fixtures / "example_ingest_params.json")
    params["plan"] = plan
    ingested = skill.execute(params)
    print(f"  status: {ingested['status']}")
    for lead in ingested["leads"]:
        print(
            f"  lead_id {lead['lead_id']} key={lead['profile_key']} "
            f"score={lead['score']} off_target={lead['off_target']} "
            f"reasons={lead['reasons']} source_query={lead['source_query']}"
        )
    print(f"  stats: {ingested['stats']}")
    print("  review table:")
    for line in ingested["review_table"]["csv"].splitlines():
        print(f"    {line}")

    print("\nScenario 3: unknown platform (fail-closed contract error)")
    _print_error(
        skill.execute(
            {
                "action": "compile_search",
                "platform": "nowhere",
                "target_spec": {"schema_version": 1, "titles": ["Head of Operations"]},
            }
        )
    )

    print("\nScenario 4: an edited plan (fail-closed contract error)")
    edited = json.loads(json.dumps(plan))
    edited["criteria"]["keywords_exclude"] = []
    params["plan"] = edited
    _print_error(skill.execute(params))

    print("\nDemo complete.")


if __name__ == "__main__":
    run_demo()
