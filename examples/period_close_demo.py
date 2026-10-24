"""
Local execute demo for finance/period_close.

Runs the deterministic period close entirely offline against the in-bundle
fixtures: the issue's example closed into statements, checks and gap notes;
the same journal validated with an unbalanced entry; the fail-closed
CLOSE_BLOCKED; the soft close that returns provisional statements; and a
close attempted before the period end. No API keys, no network.
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


def _print_findings(items: list) -> None:
    if not items:
        print("    (none)")
    for item in items:
        print(
            f"    {item['code']} [{item['severity']}] count={item['count']} "
            f"refs={item['refs']}"
        )


def run_demo() -> None:
    print("Loading finance/period_close...")
    bundle = SkillLoader.load_skill("finance/period_close")
    skill = bundle["class"]()
    fixtures = _bundle_dir(bundle["module"]) / "fixtures"

    print("\nScenario 1: close the quarter from the journal (the issue's example)")
    closed = skill.execute(_load_json(fixtures / "example_close_params.json"))
    print(f"  status: {closed['status']}")
    print(f"  closed: {closed['closed']} provisional: {closed['provisional']}")
    print("  trial balance:")
    for row in closed["trial_balance"]:
        print(
            f"    {row['account']:<13} {row['class']:<8} opening={row['opening']} "
            f"debit={row['debit']} credit={row['credit']} closing={row['closing']}"
        )
    print(f"  income statement: {closed['income_statement']}")
    print(f"  balance sheet: {closed['balance_sheet']}")
    print(f"  cash flow: {closed['cash_flow']}")
    print("  checks:")
    _print_findings(closed["checks"])
    for note in closed["notes"]:
        print(f"  note: {note}")
    print(f"  ratios: {closed['ratios']}")
    print(f"  observations: {closed['observations']}")
    print(f"  fired_any: {closed['fired_any']} insufficient: {closed['insufficient']}")

    print("\nScenario 2: validate the same journal with an entry missing its credit")
    blocked = _load_json(fixtures / "example_blocked_params.json")
    validated = skill.execute({**blocked, "action": "validate"})
    print(f"  status: {validated['status']}")
    print("  findings:")
    _print_findings(validated["findings"])
    print(f"  blocking: {validated['blocking']}")

    print("\nScenario 3: close with that blocking finding (fail-closed contract error)")
    _print_error(skill.execute(blocked))

    print("\nScenario 4: soft close of the same journal (provisional statements)")
    soft = skill.execute(_load_json(fixtures / "soft_close_params.json"))
    print(f"  status: {soft['status']}")
    print(f"  closed: {soft['closed']} provisional: {soft['provisional']}")
    print(f"  balanced: {soft['balance_sheet']['balanced']}")
    for note in soft["notes"]:
        print(f"  note: {note}")

    print("\nScenario 5: close before the period end (fail-closed contract error)")
    early = _load_json(fixtures / "example_close_params.json")
    early["as_of"] = "2026-06-15"
    _print_error(skill.execute(early))

    print("\nDemo complete.")


if __name__ == "__main__":
    run_demo()
