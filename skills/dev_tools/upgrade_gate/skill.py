"""Deterministic upgrade gate for pinned dependencies.

Two actions over host-supplied data:

  digest - summarises repository events (release, tag, commit) inside a
           window: counts by type, version jumps read from release and tag
           titles, breaking terms found in titles, and commits touching paths
           of interest. Events before the window are dropped and counted;
           an event after it is a contract violation.
  assess - gives every pinned dependency a verdict, hold, upgrade, test or
           unknown, with a target and reason codes, decided by eight ordered
           rules over a release snapshot and optional advisories.

The skill never fetches: the host supplies manifests, release snapshots,
advisories and events. It reads no file outside its bundle, makes no network
call, reads no clock and writes nothing; time enters only through the inputs.
Identical input returns identical output. The verdicts are advice to the
operator and the agent; they block nothing. A contract violation returns an
error envelope with a code from the closed registry below; an unexpected
internal fault returns the one-code fault registry entry INTERNAL_ERROR.
Release notes, event titles and paths are untrusted text. Notes and paths
never reach an output or an error detail; of a title, only a version read
from it (without its leading v, at most 64 characters, valid semver) reaches
an output, in version_jumps, and a host treats those version strings as
untrusted data too.
"""

import json
import os
import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from skill_assay.core.base_skill import BaseSkill

REGISTRY_ID = "dev_tools/upgrade_gate"
SKILL_VERSION = "0.1.0"

ACTION_DIGEST = "digest"
ACTION_ASSESS = "assess"
ACTIONS = (ACTION_DIGEST, ACTION_ASSESS)

# Closed registry of contract-violation codes: the eleven codes of the
# published interface. They are the skill's identity and never change.
INVALID_REQUEST = "INVALID_REQUEST"
MISSING_AS_OF = "MISSING_AS_OF"
INVALID_WINDOW = "INVALID_WINDOW"
FUTURE_EVENT = "FUTURE_EVENT"
EMPTY_DEPENDENCIES = "EMPTY_DEPENDENCIES"
DUPLICATE_DEPENDENCY = "DUPLICATE_DEPENDENCY"
UNKNOWN_ECOSYSTEM = "UNKNOWN_ECOSYSTEM"
INVALID_VERSION = "INVALID_VERSION"
INVALID_RANGE = "INVALID_RANGE"
FUTURE_RELEASE = "FUTURE_RELEASE"
INVALID_POLICY = "INVALID_POLICY"

ERROR_REGISTRY = frozenset(
    {
        INVALID_REQUEST,
        MISSING_AS_OF,
        INVALID_WINDOW,
        FUTURE_EVENT,
        EMPTY_DEPENDENCIES,
        DUPLICATE_DEPENDENCY,
        UNKNOWN_ECOSYSTEM,
        INVALID_VERSION,
        INVALID_RANGE,
        FUTURE_RELEASE,
        INVALID_POLICY,
    }
)

# One-code fault registry, returned only by the outermost handler of execute()
# when an unexpected exception escapes the contract checks.
INTERNAL_ERROR = "INTERNAL_ERROR"
FAULT_REGISTRY = frozenset({INTERNAL_ERROR})

ECOSYSTEM_PYPI = "pypi"
ECOSYSTEM_NPM = "npm"
ECOSYSTEMS = (ECOSYSTEM_PYPI, ECOSYSTEM_NPM)
EVENT_TYPES = ("release", "tag", "commit")

VERDICT_HOLD = "hold"
VERDICT_UPGRADE = "upgrade"
VERDICT_TEST = "test"
VERDICT_UNKNOWN = "unknown"
VERDICTS = (VERDICT_HOLD, VERDICT_UPGRADE, VERDICT_TEST, VERDICT_UNKNOWN)

# Closed reason vocabulary (14 codes).
ADVISORY = "ADVISORY"
NO_FIX_IN_RANGE = "NO_FIX_IN_RANGE"
UP_TO_DATE = "UP_TO_DATE"
PATCH_AVAILABLE = "PATCH_AVAILABLE"
MINOR_AVAILABLE = "MINOR_AVAILABLE"
MAJOR_AVAILABLE = "MAJOR_AVAILABLE"
MAJOR_ALLOWED = "MAJOR_ALLOWED"
MIN_AGE_MET = "MIN_AGE_MET"
MIN_AGE_NOT_MET = "MIN_AGE_NOT_MET"
BREAKING_TERM = "BREAKING_TERM"
YANKED_SKIPPED = "YANKED_SKIPPED"
PRERELEASE_SKIPPED = "PRERELEASE_SKIPPED"
NO_RELEASE_SNAPSHOT = "NO_RELEASE_SNAPSHOT"
CURRENT_OUT_OF_RANGE = "CURRENT_OUT_OF_RANGE"
REASON_CODES = (
    ADVISORY,
    NO_FIX_IN_RANGE,
    UP_TO_DATE,
    PATCH_AVAILABLE,
    MINOR_AVAILABLE,
    MAJOR_AVAILABLE,
    MAJOR_ALLOWED,
    MIN_AGE_MET,
    MIN_AGE_NOT_MET,
    BREAKING_TERM,
    YANKED_SKIPPED,
    PRERELEASE_SKIPPED,
    NO_RELEASE_SNAPSHOT,
    CURRENT_OUT_OF_RANGE,
)

# Every file the skill opens lives in this bundle; one open() call per entry.
BREAKING_TERMS_FILE = "breaking_terms.json"
BUNDLE_READS = ("kb/breaking_terms.json",)

# The eight verdict rules with the issue's numbers, order and wording, each
# with its kind. instructions.md carries this list and the evaluation order
# verbatim; a test compares both.
RULE_DECIDER = "decider"
RULE_MODIFIER = "modifier"
RULE_FILTER = "filter"
RULE_ABSENCE = "absence"
RULES = (
    (
        1,
        RULE_DECIDER,
        "An advisory affecting `current` → `upgrade` (target = lowest fixed "
        "version within range; outside the range → `hold` + `major_available`). "
        "Reading: an advisory's `affected` range contains `current`. The target "
        "is the lowest newer-in-range version that no advisory for this "
        "dependency contains. Found: `upgrade`, reason `ADVISORY`, regardless "
        "of age. Not found: `hold`, reasons `ADVISORY`, `NO_FIX_IN_RANGE`, then "
        "`MAJOR_AVAILABLE` when `major_available` is true.",
    ),
    (
        2,
        RULE_DECIDER,
        "`current` is the latest within range → `hold`. Reading: no "
        "newer-in-range version shares `current`'s major, and `major_available` "
        "is false: `hold`, reason `UP_TO_DATE`.",
    ),
    (
        3,
        RULE_DECIDER,
        "Patch-only drift and the target is older than `min_age_days` → "
        "`upgrade`. Reading: a newer-in-range version shares `current`'s major "
        "and `latest_compatible` has the same minor. Age met: `upgrade` to "
        "`latest_compatible`, reasons `PATCH_AVAILABLE`, `MIN_AGE_MET`. Not met: "
        "`hold`, reasons `PATCH_AVAILABLE`, `MIN_AGE_NOT_MET`.",
    ),
    (
        4,
        RULE_DECIDER,
        "Minor drift → `test`. Reading: as rule 3 but `latest_compatible` has a "
        "higher minor: `test` to `latest_compatible`, reason `MINOR_AVAILABLE`. "
        "No age condition.",
    ),
    (
        5,
        RULE_DECIDER,
        "A major outside the range → `hold` + `major_available` (`test` if "
        "`allow_major`). Reading: no newer-in-range version shares `current`'s "
        "major, and `major_available` is true. `allow_major` false: `hold`, "
        "reason `MAJOR_AVAILABLE`. True: `test`, target the highest eligible "
        "version with a higher major, reasons `MAJOR_AVAILABLE`, "
        "`MAJOR_ALLOWED`. No age condition.",
    ),
    (
        6,
        RULE_MODIFIER,
        "A breaking term in the notes between `current` and target lowers the "
        "verdict one step (`upgrade` → `test`; `hold` if `strict`). Reading: "
        "applied after the decider when the verdict is `upgrade` or `test`; if "
        "a breaking term occurs in the notes of any eligible version above "
        "`current` and up to the target, `upgrade` becomes `test` (or `hold` "
        "when `strict`), and `test` becomes `hold`. Reason `BREAKING_TERM` is "
        "appended.",
    ),
    (
        7,
        RULE_FILTER,
        "Yanked and prerelease versions are skipped as targets (prerelease "
        "allowed by policy). Reading: defines eligibility. `yanked_skipped` "
        "counts yanked versions greater than `current`; reason `YANKED_SKIPPED` "
        "is appended when it is not zero, `PRERELEASE_SKIPPED` when the highest "
        "version overall is an excluded prerelease.",
    ),
    (
        8,
        RULE_ABSENCE,
        "No release snapshot for the dependency → `unknown`. Reading: no "
        "release entry for the dependency, or no eligible version in it: "
        "`unknown`, reason `NO_RELEASE_SNAPSHOT` (followed by `ADVISORY` if an "
        "advisory contains `current`); `latest`, `latest_compatible`, `drift`, "
        "`target` and `signals` are `null`.",
    ),
)
# Rule 8 first; then the one fail-closed step outside the eight; then
# eligibility; then the deciders in order, the first match deciding; then the
# modifier.
EVALUATION_ORDER = (8, "current_out_of_range", 7, 1, 2, 3, 4, 5, 6)
EVALUATION_ORDER_LINE = "Evaluation order: " + ", ".join(
    str(step) for step in EVALUATION_ORDER
)

# Request key sets.
COMMON_REQUEST_KEYS = ("action", "as_of", "policy")
ASSESS_REQUEST_KEYS = COMMON_REQUEST_KEYS + (
    "snapshot_at",
    "dependencies",
    "releases",
    "advisories",
)
DIGEST_REQUEST_KEYS = COMMON_REQUEST_KEYS + ("since", "watched")
ALL_REQUEST_KEYS = frozenset(ASSESS_REQUEST_KEYS + DIGEST_REQUEST_KEYS)

DEPENDENCY_KEYS = ("name", "ecosystem", "current", "range")
RELEASE_ENTRY_KEYS = ("name", "ecosystem", "versions")
VERSION_REQUIRED_KEYS = ("version", "published_at")
VERSION_OPTIONAL_KEYS = ("yanked", "notes")
VERSION_KEYS = VERSION_REQUIRED_KEYS + VERSION_OPTIONAL_KEYS
ADVISORY_KEYS = ("id", "name", "ecosystem", "affected")
ASSESS_POLICY_REQUIRED_KEYS = (
    "allow_major",
    "allow_prerelease",
    "min_age_days",
    "max_snapshot_age_days",
)
ASSESS_POLICY_KEYS = ASSESS_POLICY_REQUIRED_KEYS + ("strict",)
POLICY_BOOLEAN_KEYS = ("allow_major", "allow_prerelease", "strict")
POLICY_INTEGER_KEYS = ("min_age_days", "max_snapshot_age_days")
DIGEST_POLICY_KEYS = ("paths_of_interest",)
WATCHED_KEYS = ("repo", "events")
EVENT_REQUIRED_KEYS = ("type", "id", "at")
EVENT_OPTIONAL_KEYS = ("title", "paths")
EVENT_KEYS = EVENT_REQUIRED_KEYS + EVENT_OPTIONAL_KEYS

RESULT_KEYS = (
    "name",
    "ecosystem",
    "current",
    "latest",
    "latest_compatible",
    "drift",
    "verdict",
    "target",
    "reasons",
    "signals",
)
DRIFT_KEYS = ("major", "minor", "patch")
SIGNAL_KEYS = ("breaking_terms", "yanked_skipped", "advisory", "major_available")
SUMMARY_KEYS = VERDICTS
ASSESS_OUTPUT_KEYS = (
    "status",
    "results",
    "summary",
    "fired_any",
    "insufficient",
    "stale_snapshot",
)
PER_REPO_KEYS = (
    "repo",
    "counts",
    "version_jumps",
    "breaking_hits",
    "path_hits",
    "notable",
    "dropped_before_since",
    "unparsed_versions",
)
JUMP_KEYS = ("from", "to", "kind")
JUMP_KINDS = ("major", "minor", "patch", "prerelease", "downgrade")
DIGEST_OUTPUT_KEYS = ("status", "window", "per_repo", "fired_any", "insufficient")

# Policy integer bounds (not caps: a value outside them is INVALID_POLICY).
MAX_POLICY_DAYS = 3_650

# Size caps. Every cap is a module constant, has one test case and one row in
# the table of instructions.md.
MAX_DEPENDENCIES = 2_000
MAX_RELEASE_ENTRIES = 2_000
MAX_VERSIONS_PER_ENTRY = 2_000
MAX_TOTAL_VERSIONS = 50_000
MAX_ADVISORIES = 2_000
MAX_ADVISORIES_PER_DEPENDENCY = 20
MAX_NAME_CHARS = 214
MAX_VERSION_CHARS = 64
MAX_RANGE_CHARS = 256
MAX_RANGE_CLAUSES = 8
MAX_NOTES_CHARS = 20_000
MAX_TOTAL_NOTES_CHARS = 2_000_000
MAX_REPOSITORIES = 200
MAX_EVENTS_PER_REPOSITORY = 5_000
MAX_TOTAL_EVENTS = 50_000
MAX_TITLE_CHARS = 500
MAX_TOTAL_TITLE_CHARS = 1_000_000
MAX_PATHS_PER_EVENT = 500
MAX_PATH_CHARS = 1_024
MAX_TOTAL_PATHS = 5_000
MAX_TOTAL_PATH_CHARS = 50_000
MAX_PATTERNS = 20
MAX_PATTERN_CHARS = 256
MAX_PATTERN_WILDCARDS = 4
MAX_PATH_PATTERN_PAIRS = 100_000

# (field, unit, limit, code) rows of the cap table in instructions.md.
CAP_TABLE = (
    ("dependencies", "entries", MAX_DEPENDENCIES, INVALID_REQUEST),
    ("releases", "entries", MAX_RELEASE_ENTRIES, INVALID_REQUEST),
    ("releases[].versions", "entries", MAX_VERSIONS_PER_ENTRY, INVALID_REQUEST),
    (
        "releases (all versions together)",
        "entries",
        MAX_TOTAL_VERSIONS,
        INVALID_REQUEST,
    ),
    ("advisories", "entries", MAX_ADVISORIES, INVALID_REQUEST),
    (
        "advisories (per dependency)",
        "entries",
        MAX_ADVISORIES_PER_DEPENDENCY,
        INVALID_REQUEST,
    ),
    ("name", "characters", MAX_NAME_CHARS, INVALID_REQUEST),
    ("version", "characters", MAX_VERSION_CHARS, INVALID_REQUEST),
    ("range", "characters", MAX_RANGE_CHARS, INVALID_REQUEST),
    ("range", "clauses", MAX_RANGE_CLAUSES, INVALID_REQUEST),
    ("releases[].versions[].notes", "characters", MAX_NOTES_CHARS, INVALID_REQUEST),
    (
        "releases (all notes together)",
        "characters",
        MAX_TOTAL_NOTES_CHARS,
        INVALID_REQUEST,
    ),
    ("watched", "entries", MAX_REPOSITORIES, INVALID_REQUEST),
    ("watched[].events", "entries", MAX_EVENTS_PER_REPOSITORY, INVALID_REQUEST),
    ("watched (all events together)", "entries", MAX_TOTAL_EVENTS, INVALID_REQUEST),
    ("watched[].events[].title", "characters", MAX_TITLE_CHARS, INVALID_REQUEST),
    (
        "watched (all titles together)",
        "characters",
        MAX_TOTAL_TITLE_CHARS,
        INVALID_REQUEST,
    ),
    ("watched[].events[].paths", "entries", MAX_PATHS_PER_EVENT, INVALID_REQUEST),
    ("watched[].events[].paths[]", "characters", MAX_PATH_CHARS, INVALID_REQUEST),
    ("watched (all paths together)", "entries", MAX_TOTAL_PATHS, INVALID_REQUEST),
    (
        "watched (all path characters together)",
        "characters",
        MAX_TOTAL_PATH_CHARS,
        INVALID_REQUEST,
    ),
    ("policy.paths_of_interest", "entries", MAX_PATTERNS, INVALID_REQUEST),
    ("policy.paths_of_interest[]", "characters", MAX_PATTERN_CHARS, INVALID_REQUEST),
    ("policy.paths_of_interest[]", "wildcards", MAX_PATTERN_WILDCARDS, INVALID_POLICY),
    (
        "path-pattern pairs (total paths times patterns)",
        "pairs",
        MAX_PATH_PATTERN_PAIRS,
        INVALID_REQUEST,
    ),
)

# The validation order of each action, one line per check. instructions.md
# carries both lists verbatim; the first failing check's code is returned.
ASSESS_CHECKS = (
    (INVALID_REQUEST, "parameters are not an object."),
    (
        INVALID_REQUEST,
        "`action` absent, not a string, or not `digest` or `assess`.",
    ),
    (
        INVALID_REQUEST,
        "an unknown top-level key, or a key of the other action (`since`, "
        "`watched`).",
    ),
    (MISSING_AS_OF, "`as_of` absent or null."),
    (
        INVALID_REQUEST,
        "`as_of` not a string of the form `YYYY-MM-DD` naming a real date.",
    ),
    (
        INVALID_REQUEST,
        "`snapshot_at` absent, not a string, not of the form "
        "`YYYY-MM-DDTHH:MM:SSZ`, or after the end of `as_of` (23:59:59Z).",
    ),
    (
        INVALID_POLICY,
        "`policy` absent or not an object; an unknown key; `allow_major`, "
        "`allow_prerelease` or `strict` not a boolean; `min_age_days` or "
        "`max_snapshot_age_days` absent, not an integer, or outside 0 to 3,650.",
    ),
    (
        INVALID_REQUEST,
        "`dependencies`, `releases` or `advisories` present and not a list.",
    ),
    (EMPTY_DEPENDENCIES, "`dependencies` absent or an empty list."),
    (
        INVALID_REQUEST,
        "a size cap (dependencies; release entries; versions per entry; total "
        "versions; advisories; advisories per dependency; the lengths of "
        "`name`, `current`, `range`, `affected` and `notes`; total notes "
        "characters; clauses per range); counting treats a non-list or "
        "non-string where a list or string is expected as the same failure.",
    ),
    (
        INVALID_REQUEST,
        "per dependency: not an object; a key set other than `name`, "
        "`ecosystem`, `current`, `range`; a value not a string.",
    ),
    (UNKNOWN_ECOSYSTEM, "per dependency: `ecosystem` not `pypi` or `npm`."),
    (
        INVALID_REQUEST,
        "per dependency: `name` not matching the ecosystem's pattern.",
    ),
    (
        DUPLICATE_DEPENDENCY,
        "per dependency: the normalised (`ecosystem`, `name`) already seen.",
    ),
    (
        INVALID_VERSION,
        "per dependency: `current` outside the ecosystem's grammar. `detail` "
        "gives the path, never the string.",
    ),
    (INVALID_RANGE, "per dependency: `range` outside the supported grammar."),
    (
        INVALID_REQUEST,
        "per release entry: not an object; a key set other than `name`, "
        "`ecosystem`, `versions`; `name` or `ecosystem` not a string; "
        "`versions` not a list.",
    ),
    (UNKNOWN_ECOSYSTEM, "per release entry."),
    (
        INVALID_REQUEST,
        "per release entry: `name` not matching the pattern; the normalised "
        "(`ecosystem`, `name`) already seen among release entries.",
    ),
    (
        INVALID_REQUEST,
        "per release entry, per version: not an object; an unknown key; "
        "`version` or `published_at` absent or not a string; `yanked` present "
        "and not a boolean; `notes` present and not a string; `published_at` "
        "malformed.",
    ),
    (
        INVALID_VERSION,
        "per version: `version` outside the grammar. Versions of one entry "
        "that compare equal are valid input and change no check; they count "
        "as one version, the first of them in the entry's list, with its own "
        "`published_at`, `yanked` and `notes`.",
    ),
    (FUTURE_RELEASE, "per version: `published_at` after the end of `as_of`."),
    (
        INVALID_REQUEST,
        "per advisory: not an object; a key set other than `id`, `name`, "
        "`ecosystem`, `affected`; a value not a string; `id` not matching its "
        "pattern, or already seen.",
    ),
    (UNKNOWN_ECOSYSTEM, "per advisory."),
    (INVALID_REQUEST, "per advisory: `name` not matching the pattern."),
    (INVALID_RANGE, "per advisory: `affected` outside the grammar."),
)

DIGEST_CHECKS = (
    (INVALID_REQUEST, "parameters are not an object."),
    (
        INVALID_REQUEST,
        "`action` absent, not a string, or not `digest` or `assess`.",
    ),
    (
        INVALID_REQUEST,
        "an unknown top-level key, or a key of the other action (`snapshot_at`, "
        "`dependencies`, `releases`, `advisories`).",
    ),
    (MISSING_AS_OF, "`as_of` absent or null."),
    (
        INVALID_REQUEST,
        "`as_of` not a string of the form `YYYY-MM-DD` naming a real date.",
    ),
    (
        INVALID_WINDOW,
        "`since` absent, not a string, malformed, or after `as_of`.",
    ),
    (
        INVALID_REQUEST,
        "`watched` absent or not a list; `policy` present and not an object.",
    ),
    (
        INVALID_POLICY,
        "`policy` holds an unknown key; `paths_of_interest` present and not a "
        "list of strings.",
    ),
    (
        INVALID_REQUEST,
        "a size cap (repositories; events per repository; total events; title "
        "length; total title characters; paths per event; path length; total "
        "paths; total path characters; patterns; pattern length; path-pattern "
        "pairs); counting treats a wrong container type as the same failure.",
    ),
    (
        INVALID_POLICY,
        "per pattern: empty; a character outside the closed set; more than two "
        "consecutive `*`; more than 4 wildcards.",
    ),
    (
        INVALID_REQUEST,
        "per repository: not an object; a key set other than `repo`, `events`; "
        "`repo` not a string matching its pattern, or already seen; `events` "
        "not a list.",
    ),
    (
        INVALID_REQUEST,
        "per repository, per event: not an object; an unknown key; `type` not "
        "one of `release`, `tag`, `commit`; `id` absent, not matching its "
        "pattern, or already seen in this repository; `at` absent or "
        "malformed; `title` present and not a string; `paths` present and not "
        "a list of strings.",
    ),
    (
        FUTURE_EVENT,
        "per event: `at` after the end of `as_of`. `detail` is `{event_id, at}` "
        "as in the published example.",
    ),
)

# Every pattern below is matched against the whole string (re.fullmatch), so
# a trailing line feed fails it.
_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_TIMESTAMP_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
_PYPI_NAME_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._-]{0,98}[A-Za-z0-9])?$")
_NPM_NAME_RE = re.compile(r"^(@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*$")
MAX_NPM_NAME_CHARS = 214
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
# A canonical number: no leading zero other than 0 itself.
_NUMBER_RE = re.compile(r"^(0|[1-9][0-9]*)$")
# The tail of a canonical pypi version after its release segments.
_PYPI_TAIL_RE = re.compile(
    r"^(?:(a|b|rc)([0-9]+))?(?:\.post([0-9]+))?(?:\.dev([0-9]+))?$"
)
# One semver prerelease or build identifier.
_IDENTIFIER_RE = re.compile(r"^[0-9A-Za-z-]+$")
# Runs of pypi name separators, collapsed to one hyphen.
_NAME_SEPARATORS_RE = re.compile(r"[-_.]+")
# Tokens for the breaking-term scan: maximal runs of ASCII letters and digits.
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
# A lone surrogate code point (Unicode category Cs) has no encoding, so a
# string that contains one is not a string for any check.
_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")

# The closed character set of path patterns.
PATTERN_CHARACTERS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-/*?"
)
_ELEMENT_LITERAL = "literal"
_ELEMENT_ONE = "?"
_ELEMENT_STAR = "*"
_ELEMENT_DOUBLE_STAR = "**"

_PYPI_OPERATORS = ("~=", "==", "!=", "<=", ">=", "<", ">")
_NPM_OPERATORS = (">=", "<=", ">", "<", "=", "^", "~")
_NPM_WILDCARDS = ("x", "X", "*")
_PRE_LETTER_ORDER = {"a": 0, "b": 1, "rc": 2}


class _ContractError(Exception):
    """Internal signal carrying a closed-registry code and a detail object."""

    def __init__(self, code: str, detail: Dict[str, Any]):
        super().__init__(code)
        self.code = code
        self.detail = detail


def _fail(code: str, reason: str, **extra: Any) -> None:
    detail: Dict[str, Any] = {"reason": reason}
    detail.update(extra)
    raise _ContractError(code, detail)


def _cap_fail(path: str, limit: int, actual: int) -> None:
    _fail(INVALID_REQUEST, "size_cap", path=path, limit=limit, actual=actual)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_string(value: Any) -> bool:
    """A str with no lone surrogate; every check that needs a string uses this."""
    return isinstance(value, str) and _SURROGATE_RE.search(value) is None


def _is_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(_is_string(item) for item in value)


def _parse_date(value: Any) -> Optional[date]:
    """The date a YYYY-MM-DD string names, or None when it is not one."""
    if not _is_string(value) or not _DATE_RE.fullmatch(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _parse_timestamp(value: Any) -> Optional[datetime]:
    """The UTC instant a YYYY-MM-DDTHH:MM:SSZ string names, or None."""
    if not _is_string(value) or not _TIMESTAMP_RE.fullmatch(value):
        return None
    day = _parse_date(value[:10])
    if day is None:
        return None
    hour, minute, second = int(value[11:13]), int(value[14:16]), int(value[17:19])
    if hour > 23 or minute > 59 or second > 59:
        return None
    return datetime(day.year, day.month, day.day, hour, minute, second)


def _end_of_day(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 23, 59, 59)


def _start_of_day(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 0, 0, 0)


# --------------------------------------------------------------------------
# Bundle reads
# --------------------------------------------------------------------------


def _bundle_path(*parts: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), *parts)


def _load_breaking_terms() -> List[str]:
    with open(_bundle_path("kb", BREAKING_TERMS_FILE), "r", encoding="utf-8") as handle:
        data = json.load(handle)
    return list(data["terms"])


# --------------------------------------------------------------------------
# Names and tokens
# --------------------------------------------------------------------------


def _normalise_name(ecosystem: str, name: str) -> str:
    """pypi: lower-cased, runs of -, _ and . collapsed to one -; npm: as it is."""
    if ecosystem == ECOSYSTEM_PYPI:
        return _NAME_SEPARATORS_RE.sub("-", name.lower())
    return name


def _name_matches(ecosystem: str, name: str) -> bool:
    if ecosystem == ECOSYSTEM_PYPI:
        return _PYPI_NAME_RE.fullmatch(name) is not None
    return len(name) <= MAX_NPM_NAME_CHARS and _NPM_NAME_RE.fullmatch(name) is not None


def _tokens(text: str) -> Set[str]:
    """The tokenising step: the upper-cased alphanumeric tokens of a text."""
    return {token.upper() for token in _TOKEN_RE.findall(text)}


def _breaking_terms_in(text: str, terms: List[str]) -> List[str]:
    """The terms whose upper-cased token occurs in text, in list order, each once."""
    tokens = _tokens(text)
    return [term for term in terms if term in tokens]


# --------------------------------------------------------------------------
# Versions
# --------------------------------------------------------------------------


class _Version:
    """A parsed version: its ordering key and the facts the rules read."""

    __slots__ = (
        "text",
        "key",
        "release",
        "major",
        "minor",
        "patch",
        "prerelease",
        "pre",
        "post",
        "dev",
    )

    def __init__(
        self,
        text: str,
        key: Tuple[Any, ...],
        release: Tuple[int, ...],
        padded: Tuple[int, int, int],
        prerelease: bool,
        post: Optional[int],
        pre: Optional[Tuple[str, int]] = None,
        dev: Optional[int] = None,
    ):
        self.text = text
        self.key = key
        self.release = release
        self.major, self.minor, self.patch = padded
        self.prerelease = prerelease
        # The pypi pre, post and dev segments; the exclusive comparisons read
        # them. An npm version has none.
        self.pre = pre
        self.post = post
        self.dev = dev


def _is_number(text: str) -> bool:
    return _NUMBER_RE.fullmatch(text) is not None


def _pypi_release_key(release: Tuple[int, ...]) -> Tuple[int, ...]:
    """The release with trailing zeros removed, so that 1.0 equals 1.0.0."""
    end = len(release)
    while end > 0 and release[end - 1] == 0:
        end -= 1
    return release[:end]


def _pypi_key(
    release: Tuple[int, ...],
    pre: Optional[Tuple[str, int]],
    post: Optional[int],
    dev: Optional[int],
) -> Tuple[Any, ...]:
    """Ordering as PEP 440: .devN before aN, bN, rcN, the final, then .postN."""
    if pre is None and post is None and dev is not None:
        pre_key: Tuple[int, int] = (-1, 0)
    elif pre is None:
        pre_key = (3, 0)
    else:
        pre_key = (_PRE_LETTER_ORDER[pre[0]], pre[1])
    post_key = -1 if post is None else post
    dev_key = (1, 0) if dev is None else (0, dev)
    return (_pypi_release_key(release), pre_key, post_key, dev_key)


def _parse_pypi_release(text: str) -> Optional[Tuple[int, ...]]:
    """One to four canonical numeric segments joined by dots, or None."""
    segments = text.split(".")
    if not 1 <= len(segments) <= 4 or not all(_is_number(s) for s in segments):
        return None
    return tuple(int(segment) for segment in segments)


def _parse_pypi(text: str) -> Optional[_Version]:
    """The canonical PEP 440 subset: release, optional aN/bN/rcN, .postN, .devN."""
    index = 0
    while index < len(text) and text[index] in "0123456789.":
        index += 1
    release_text, tail = text[:index], text[index:]
    if release_text.endswith("."):
        release_text, tail = release_text[:-1], "." + tail
    release = _parse_pypi_release(release_text)
    if release is None:
        return None
    match = _PYPI_TAIL_RE.fullmatch(tail)
    if match is None:
        return None
    letter, pre_number, post_number, dev_number = match.groups()
    for number in (pre_number, post_number, dev_number):
        if number is not None and not _is_number(number):
            return None
    pre = None if letter is None else (letter, int(pre_number))
    post = None if post_number is None else int(post_number)
    dev = None if dev_number is None else int(dev_number)
    padded = tuple(release[:3]) + (0,) * (3 - min(len(release), 3))
    return _Version(
        text,
        _pypi_key(release, pre, post, dev),
        release,
        (padded[0], padded[1], padded[2]),
        pre is not None or dev is not None,
        post,
        pre,
        dev,
    )


def _parse_identifiers(text: str, numeric_rule: bool) -> Optional[Tuple[Any, ...]]:
    """Dot-separated semver identifiers; numeric ones have no leading zero."""
    keys: List[Any] = []
    for identifier in text.split("."):
        if not _IDENTIFIER_RE.fullmatch(identifier):
            return None
        if identifier.isdigit():
            if numeric_rule and not _is_number(identifier):
                return None
            keys.append((0, int(identifier)))
        else:
            keys.append((1, identifier))
    return tuple(keys)


def _parse_npm_partial(
    text: str,
) -> Optional[Tuple[List[Optional[int]], Optional[Tuple[Any, ...]]]]:
    """A semver version or x-range partial: numeric parts (None for a wildcard),
    and the prerelease identifier keys when the version is complete."""
    core, build = (text.split("+", 1) + [None])[:2] if "+" in text else (text, None)
    core, pre = (core.split("-", 1) + [None])[:2] if "-" in core else (core, None)
    parts = core.split(".")
    if not 1 <= len(parts) <= 3:
        return None
    numbers: List[Optional[int]] = []
    for part in parts:
        if part in _NPM_WILDCARDS:
            numbers.append(None)
        elif _is_number(part):
            numbers.append(int(part))
        else:
            return None
    wildcard_seen = False
    for number in numbers:
        if number is None:
            wildcard_seen = True
        elif wildcard_seen:
            return None
    pre_keys: Optional[Tuple[Any, ...]] = None
    if pre is not None or build is not None:
        if len(numbers) != 3 or wildcard_seen:
            return None
        if pre is not None:
            pre_keys = _parse_identifiers(pre, True)
            if pre_keys is None:
                return None
        if build is not None and _parse_identifiers(build, False) is None:
            return None
    return numbers, pre_keys


def _npm_key(
    core: Tuple[int, int, int], pre: Optional[Tuple[Any, ...]]
) -> Tuple[Any, ...]:
    """Semver precedence: a prerelease sorts below its release; build is ignored."""
    if pre is None:
        return (core, 1, ())
    return (core, 0, pre)


def _parse_npm(text: str) -> Optional[_Version]:
    """Semver 2.0.0: three numeric components, optional prerelease and build."""
    parsed = _parse_npm_partial(text)
    if parsed is None:
        return None
    numbers, pre = parsed
    if len(numbers) != 3 or any(number is None for number in numbers):
        return None
    core = (numbers[0], numbers[1], numbers[2])
    return _Version(text, _npm_key(core, pre), core, core, pre is not None, None)


def _parse_version(ecosystem: str, text: str) -> Optional[_Version]:
    if ecosystem == ECOSYSTEM_PYPI:
        return _parse_pypi(text)
    return _parse_npm(text)


# --------------------------------------------------------------------------
# Ranges
# --------------------------------------------------------------------------


class _RangeError(Exception):
    """A range outside the supported grammar."""


def _check_range_whitespace(text: str) -> None:
    if (
        not text
        or text != text.strip()
        or any(ch.isspace() and ch != " " for ch in text)
    ):
        raise _RangeError()


def _count_pypi_clauses(text: str) -> int:
    return text.count(",") + 1


def _count_npm_clauses(text: str) -> int:
    """Comparators over all || sets, an operator and its version joined."""
    count = 0
    for comparator_set in text.split("||"):
        tokens = [token for token in comparator_set.split(" ") if token]
        pending_operator = False
        for token in tokens:
            if token in _NPM_OPERATORS:
                if not pending_operator:
                    count += 1
                pending_operator = True
                continue
            if not pending_operator:
                count += 1
            pending_operator = False
    return count


def _count_clauses(ecosystem: str, text: str) -> int:
    if ecosystem == ECOSYSTEM_PYPI:
        return _count_pypi_clauses(text)
    return _count_npm_clauses(text)


def _split_operator(clause: str, operators: Tuple[str, ...]) -> Tuple[str, str]:
    for operator in operators:
        if clause.startswith(operator):
            return operator, clause[len(operator) :]
    return "", clause


def _parse_pypi_range(text: str) -> List[Tuple[str, Any]]:
    """Comma-joined clauses; each is (operator, version) or a prefix clause."""
    _check_range_whitespace(text)
    clauses: List[Tuple[str, Any]] = []
    for raw in text.split(","):
        clause = raw.strip(" ")
        operator, rest = _split_operator(clause, _PYPI_OPERATORS)
        rest = rest.lstrip(" ")
        if not operator or not rest or " " in rest:
            raise _RangeError()
        if rest.endswith(".*"):
            if operator not in ("==", "!="):
                raise _RangeError()
            prefix = _parse_pypi_release(rest[:-2])
            if prefix is None:
                raise _RangeError()
            clauses.append((operator + ".*", prefix))
            continue
        version = _parse_pypi(rest)
        if version is None:
            raise _RangeError()
        if operator == "~=" and len(version.release) < 2:
            raise _RangeError()
        clauses.append((operator, version))
    return clauses


def _release_has_prefix(release: Tuple[int, ...], prefix: Tuple[int, ...]) -> bool:
    padded = release + (0,) * max(0, len(prefix) - len(release))
    return padded[: len(prefix)] == prefix


def _in_excluded_family(candidate: _Version, operator: str, version: _Version) -> bool:
    """Whether an exclusive comparison leaves the candidate out as a member of
    V's own family, as PEP 440 reads `<V` and `>V`.

    `<V`, when V is not a prerelease, leaves out a version of the same release
    that has a pre segment, when V has no post segment, or that has no pre
    segment, a dev segment and the same post segment as V. `>V`, when V has no
    post and no dev segment, leaves out a version of the same release with the
    same pre segment as V that has a post segment. The same release means
    equal release segments with trailing zeros ignored. The caller has
    already placed the candidate on the right side of V.
    """
    if _pypi_release_key(candidate.release) != _pypi_release_key(version.release):
        return False
    if operator == "<":
        if version.prerelease:
            return False
        if candidate.pre is not None:
            return version.post is None
        return candidate.dev is not None and candidate.post == version.post
    if version.post is not None or version.dev is not None:
        return False
    return candidate.post is not None and candidate.pre == version.pre


def _pypi_clause_holds(candidate: _Version, operator: str, operand: Any) -> bool:
    if operator == "==.*":
        return _release_has_prefix(candidate.release, operand)
    if operator == "!=.*":
        return not _release_has_prefix(candidate.release, operand)
    version: _Version = operand
    if operator == "==":
        return candidate.key == version.key
    if operator == "!=":
        return candidate.key != version.key
    if operator == ">=":
        return candidate.key >= version.key
    if operator == "<=":
        return candidate.key <= version.key
    if operator == ">":
        return candidate.key > version.key and not _in_excluded_family(
            candidate, operator, version
        )
    if operator == "<":
        return candidate.key < version.key and not _in_excluded_family(
            candidate, operator, version
        )
    # "~=V": at least V, equal on every release segment of V except the last.
    return candidate.key >= version.key and _release_has_prefix(
        candidate.release, version.release[:-1]
    )


def _npm_bound(numbers: List[int], prerelease_zero: bool = False) -> Tuple[Any, ...]:
    core = (numbers[0], numbers[1], numbers[2])
    return _npm_key(core, ((0, 0),) if prerelease_zero else None)


def _npm_comparators(operator: str, text: str) -> List[Tuple[str, Tuple[Any, ...]]]:
    """Desugar one comparator into plain comparisons on precedence keys.

    Partial versions, caret and tilde follow node-semver's desugaring, upper
    bounds included: the next line's lowest prerelease (`-0`) closes them.
    """
    parsed = _parse_npm_partial(text)
    if parsed is None:
        raise _RangeError()
    numbers, pre = parsed
    complete = [number for number in numbers if number is not None]
    count = len(complete)
    if count == 0:
        if operator in ("", "=", "^", "~"):
            return []
        raise _RangeError()
    major = complete[0]
    minor = complete[1] if count > 1 else 0
    patch = complete[2] if count > 2 else 0
    low = [major, minor, patch]
    next_major = [major + 1, 0, 0]
    next_minor = [major, minor + 1, 0]
    if count == 3:
        exact = _npm_key((major, minor, patch), pre)
        if operator in ("", "="):
            return [("=", exact)]
        if operator in (">", ">=", "<", "<="):
            return [(operator, exact)]
        if operator == "~":
            return [(">=", exact), ("<", _npm_bound(next_minor, True))]
        # Caret with node-semver's zero-major behaviour.
        if major > 0:
            upper = next_major
        elif minor > 0:
            upper = next_minor
        else:
            upper = [0, 0, patch + 1]
        return [(">=", exact), ("<", _npm_bound(upper, True))]
    if operator in ("", "=", "~"):
        upper = next_minor if count == 2 else next_major
        if operator == "~" and count == 1:
            upper = next_major
        return [(">=", _npm_bound(low)), ("<", _npm_bound(upper, True))]
    if operator == "^":
        if major > 0 or count == 1:
            upper = next_major
        else:
            upper = next_minor
        return [(">=", _npm_bound(low)), ("<", _npm_bound(upper, True))]
    bump = next_minor if count == 2 else next_major
    if operator == ">":
        return [(">=", _npm_bound(bump))]
    if operator == ">=":
        return [(">=", _npm_bound(low))]
    if operator == "<":
        return [("<", _npm_bound(low, True))]
    return [("<", _npm_bound(bump, True))]


def _parse_npm_range(text: str) -> List[List[Tuple[str, Tuple[Any, ...]]]]:
    """Sets separated by ||, any of which may hold; comparators within a set
    separated by spaces, all of which must hold."""
    _check_range_whitespace(text)
    sets: List[List[Tuple[str, Tuple[Any, ...]]]] = []
    for raw_set in text.split("||"):
        tokens = [token for token in raw_set.strip(" ").split(" ") if token]
        if not tokens:
            raise _RangeError()
        comparators: List[Tuple[str, Tuple[Any, ...]]] = []
        pending = ""
        for token in tokens:
            if token in _NPM_OPERATORS:
                if pending:
                    raise _RangeError()
                pending = token
                continue
            operator, rest = _split_operator(token, _NPM_OPERATORS)
            if pending:
                if operator:
                    raise _RangeError()
                operator, rest = pending, token
                pending = ""
            if not rest:
                raise _RangeError()
            comparators.extend(_npm_comparators(operator, rest))
        if pending:
            raise _RangeError()
        sets.append(comparators)
    return sets


def _npm_comparator_holds(
    key: Tuple[Any, ...], operator: str, bound: Tuple[Any, ...]
) -> bool:
    if operator == "=":
        return key == bound
    if operator == ">":
        return key > bound
    if operator == ">=":
        return key >= bound
    if operator == "<":
        return key < bound
    return key <= bound


class _Range:
    """A parsed range of either ecosystem with one `contains` test."""

    __slots__ = ("ecosystem", "clauses")

    def __init__(self, ecosystem: str, text: str):
        self.ecosystem = ecosystem
        if ecosystem == ECOSYSTEM_PYPI:
            self.clauses = _parse_pypi_range(text)
        else:
            self.clauses = _parse_npm_range(text)

    def contains(self, version: _Version) -> bool:
        if self.ecosystem == ECOSYSTEM_PYPI:
            return all(
                _pypi_clause_holds(version, operator, operand)
                for operator, operand in self.clauses
            )
        return any(
            all(
                _npm_comparator_holds(version.key, operator, bound)
                for operator, bound in comparator_set
            )
            for comparator_set in self.clauses
        )


def _parse_range(ecosystem: str, text: str) -> Optional[_Range]:
    try:
        return _Range(ecosystem, text)
    except _RangeError:
        return None


# --------------------------------------------------------------------------
# Path patterns
# --------------------------------------------------------------------------


def _compile_pattern(pattern: str) -> List[Tuple[str, str]]:
    """Elements of a pattern: literal runs, `?`, `*` and `**`.

    Raises _ContractError (INVALID_POLICY) for an empty pattern, a character
    outside the closed set, three consecutive `*` or too many wildcards; the
    caller supplies the path of the pattern in the request.
    """
    if not pattern:
        raise _ContractError(INVALID_POLICY, {"reason": "empty_pattern"})
    if any(ch not in PATTERN_CHARACTERS for ch in pattern):
        raise _ContractError(INVALID_POLICY, {"reason": "invalid_character"})
    if "***" in pattern:
        raise _ContractError(INVALID_POLICY, {"reason": "too_many_consecutive_stars"})
    elements: List[Tuple[str, str]] = []
    wildcards = 0
    index = 0
    while index < len(pattern):
        if pattern.startswith("**", index):
            elements.append((_ELEMENT_DOUBLE_STAR, ""))
            wildcards += 1
            index += 2
        elif pattern[index] == "*":
            elements.append((_ELEMENT_STAR, ""))
            wildcards += 1
            index += 1
        elif pattern[index] == "?":
            elements.append((_ELEMENT_ONE, ""))
            wildcards += 1
            index += 1
        else:
            end = index
            while end < len(pattern) and pattern[end] not in "*?":
                end += 1
            elements.append((_ELEMENT_LITERAL, pattern[index:end]))
            index = end
    if wildcards > MAX_PATTERN_WILDCARDS:
        raise _ContractError(
            INVALID_POLICY,
            {
                "reason": "too_many_wildcards",
                "limit": MAX_PATTERN_WILDCARDS,
                "actual": wildcards,
            },
        )
    return elements


def _match_pattern(elements: List[Tuple[str, str]], path: str) -> bool:
    """Whole-path match by an iterative table over (element, path position).

    `states` is the increasing list of path positions reachable after the
    elements consumed so far. A literal run is consumed by string search
    inside the window the states span; `?` advances each state over one
    character other than `/`; `*` extends each state to the next `/` or the
    end; `**` extends the lowest state to the end.
    """
    length = len(path)
    states: List[int] = [0]
    for kind, literal in elements:
        if not states:
            return False
        if kind == _ELEMENT_LITERAL:
            size = len(literal)
            allowed: Optional[Set[int]] = set(states) if len(states) > 1 else None
            window_end = states[-1] + size
            found: List[int] = []
            position = path.find(literal, states[0], window_end)
            while position != -1:
                if allowed is None or position in allowed:
                    found.append(position + size)
                position = path.find(literal, position + 1, window_end)
            states = found
        elif kind == _ELEMENT_ONE:
            states = [p + 1 for p in states if p < length and path[p] != "/"]
        elif kind == _ELEMENT_STAR:
            extended: List[int] = []
            reach = -1
            for p in states:
                if p <= reach:
                    continue
                end = path.find("/", p)
                if end == -1:
                    end = length
                extended.extend(range(p, end + 1))
                reach = end
            states = extended
        else:
            states = list(range(states[0], length + 1))
    return bool(states) and states[-1] == length


# --------------------------------------------------------------------------
# Request envelope (checks 1 to 5 of both actions)
# --------------------------------------------------------------------------


def _check_request_keys(params: Dict[str, Any], allowed: Tuple[str, ...]) -> None:
    """Check 3; the detail does not depend on the key order of the request.

    When a key of the other action is present, ``path`` names the
    alphabetically first such key; otherwise the detail has no path.
    """
    unknown = [key for key in params if key not in allowed]
    if not unknown:
        return
    foreign = sorted(key for key in unknown if key in ALL_REQUEST_KEYS)
    if foreign:
        _fail(INVALID_REQUEST, "unknown_key", path=foreign[0])
    _fail(INVALID_REQUEST, "unknown_key")


def _check_as_of(params: Dict[str, Any]) -> date:
    """Checks 4 and 5."""
    if params.get("as_of") is None:
        _fail(MISSING_AS_OF, "missing_as_of", path="as_of")
    as_of = _parse_date(params["as_of"])
    if as_of is None:
        _fail(INVALID_REQUEST, "invalid_date", path="as_of")
    return as_of


# --------------------------------------------------------------------------
# assess: validation (checks 6 to 26)
# --------------------------------------------------------------------------


def _check_snapshot_at(params: Dict[str, Any], as_of: date) -> datetime:
    """Check 6."""
    snapshot_at = _parse_timestamp(params.get("snapshot_at"))
    if snapshot_at is None:
        _fail(INVALID_REQUEST, "invalid_timestamp", path="snapshot_at")
    if snapshot_at > _end_of_day(as_of):
        _fail(INVALID_REQUEST, "snapshot_after_as_of", path="snapshot_at")
    return snapshot_at


def _check_assess_policy(params: Dict[str, Any]) -> Dict[str, Any]:
    """Check 7; returns the policy with `strict` defaulted."""
    policy = params.get("policy")
    if not isinstance(policy, dict):
        _fail(INVALID_POLICY, "not_an_object", path="policy")
    for key in policy:
        if key not in ASSESS_POLICY_KEYS:
            _fail(INVALID_POLICY, "unknown_key", path="policy")
    # Field by field in the documented order: absence, then type, then value.
    for key in ASSESS_POLICY_KEYS:
        if key not in policy:
            if key in ASSESS_POLICY_REQUIRED_KEYS:
                _fail(INVALID_POLICY, "missing_key", path=f"policy.{key}")
            continue
        if key in POLICY_BOOLEAN_KEYS:
            if not isinstance(policy[key], bool):
                _fail(INVALID_POLICY, "not_a_boolean", path=f"policy.{key}")
            continue
        if not _is_int(policy[key]):
            _fail(INVALID_POLICY, "not_an_integer", path=f"policy.{key}")
        if policy[key] < 0 or policy[key] > MAX_POLICY_DAYS:
            _fail(
                INVALID_POLICY,
                "out_of_range",
                path=f"policy.{key}",
                limit=MAX_POLICY_DAYS,
            )
    return {
        "allow_major": policy["allow_major"],
        "allow_prerelease": policy["allow_prerelease"],
        "min_age_days": policy["min_age_days"],
        "max_snapshot_age_days": policy["max_snapshot_age_days"],
        "strict": policy.get("strict", False),
    }


def _string_length(value: Any, path: str) -> int:
    """Check 10 helper: the length of a string, or the type failure."""
    if not _is_string(value):
        _fail(INVALID_REQUEST, "invalid_type", path=path)
    return len(value)


def _check_length(value: Any, path: str, limit: int) -> None:
    actual = _string_length(value, path)
    if actual > limit:
        _cap_fail(path, limit, actual)


def _check_range_caps(value: Any, path: str, ecosystem: Any) -> None:
    _check_length(value, path, MAX_RANGE_CHARS)
    if ecosystem in ECOSYSTEMS:
        clauses = _count_clauses(ecosystem, value)
        if clauses > MAX_RANGE_CLAUSES:
            _cap_fail(path, MAX_RANGE_CLAUSES, clauses)


def _counting_key(item: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """The normalised (ecosystem, name) of an object when both are strings."""
    ecosystem, name = item.get("ecosystem"), item.get("name")
    if not _is_string(ecosystem) or not _is_string(name):
        return None
    if ecosystem in ECOSYSTEMS:
        return ecosystem, _normalise_name(ecosystem, name)
    return ecosystem, name


def _check_assess_caps(
    dependencies: List[Any], releases: List[Any], advisories: List[Any]
) -> None:
    """Check 10, in the field order dependencies, releases, advisories."""
    if len(dependencies) > MAX_DEPENDENCIES:
        _cap_fail("dependencies", MAX_DEPENDENCIES, len(dependencies))
    for index, dependency in enumerate(dependencies):
        if not isinstance(dependency, dict):
            continue
        path = f"dependencies[{index}]"
        if "name" in dependency:
            _check_length(dependency["name"], f"{path}.name", MAX_NAME_CHARS)
        if "current" in dependency:
            _check_length(dependency["current"], f"{path}.current", MAX_VERSION_CHARS)
        if "range" in dependency:
            _check_range_caps(
                dependency["range"], f"{path}.range", dependency.get("ecosystem")
            )

    if len(releases) > MAX_RELEASE_ENTRIES:
        _cap_fail("releases", MAX_RELEASE_ENTRIES, len(releases))
    total_versions = 0
    total_notes = 0
    for index, entry in enumerate(releases):
        if not isinstance(entry, dict):
            continue
        path = f"releases[{index}]"
        if "name" in entry:
            _check_length(entry["name"], f"{path}.name", MAX_NAME_CHARS)
        if "versions" not in entry:
            continue
        versions = entry["versions"]
        if not isinstance(versions, list):
            _fail(INVALID_REQUEST, "invalid_type", path=f"{path}.versions")
        if len(versions) > MAX_VERSIONS_PER_ENTRY:
            _cap_fail(f"{path}.versions", MAX_VERSIONS_PER_ENTRY, len(versions))
        total_versions += len(versions)
        if total_versions > MAX_TOTAL_VERSIONS:
            _cap_fail("releases", MAX_TOTAL_VERSIONS, total_versions)
        for position, version in enumerate(versions):
            if not isinstance(version, dict):
                continue
            version_path = f"{path}.versions[{position}]"
            if "version" in version:
                _check_length(
                    version["version"], f"{version_path}.version", MAX_VERSION_CHARS
                )
            if "notes" in version:
                _check_length(
                    version["notes"], f"{version_path}.notes", MAX_NOTES_CHARS
                )
                total_notes += len(version["notes"])
                if total_notes > MAX_TOTAL_NOTES_CHARS:
                    _cap_fail("releases", MAX_TOTAL_NOTES_CHARS, total_notes)

    if len(advisories) > MAX_ADVISORIES:
        _cap_fail("advisories", MAX_ADVISORIES, len(advisories))
    per_dependency: Dict[Tuple[str, str], int] = {}
    for index, advisory in enumerate(advisories):
        if not isinstance(advisory, dict):
            continue
        path = f"advisories[{index}]"
        if "name" in advisory:
            _check_length(advisory["name"], f"{path}.name", MAX_NAME_CHARS)
        if "affected" in advisory:
            _check_range_caps(
                advisory["affected"], f"{path}.affected", advisory.get("ecosystem")
            )
        key = _counting_key(advisory)
        if key is None:
            continue
        per_dependency[key] = per_dependency.get(key, 0) + 1
        if per_dependency[key] > MAX_ADVISORIES_PER_DEPENDENCY:
            _cap_fail(path, MAX_ADVISORIES_PER_DEPENDENCY, per_dependency[key])


def _check_object(item: Any, path: str, allowed: Tuple[str, ...], code: str) -> None:
    """The first two faults of an object check: not an object, an unknown key.

    The caller then takes the fields in their documented order, each with
    its absence, type and value faults, so that the field order decides
    when several fields are at fault, never the key order of the input.
    """
    if not isinstance(item, dict):
        _fail(code, "not_an_object", path=path)
    for key in item:
        if key not in allowed:
            _fail(code, "unknown_key", path=path)


def _check_present(item: Dict[str, Any], path: str, key: str, code: str) -> None:
    if key not in item:
        _fail(code, "missing_key", path=f"{path}.{key}")


def _check_string_field(item: Dict[str, Any], path: str, key: str, code: str) -> None:
    """A required field: absent, then not a string."""
    _check_present(item, path, key, code)
    if not _is_string(item[key]):
        _fail(code, "not_a_string", path=f"{path}.{key}")


def _check_ecosystem(value: str, path: str) -> str:
    if value not in ECOSYSTEMS:
        _fail(UNKNOWN_ECOSYSTEM, "unknown_ecosystem", path=path)
    return value


def _check_dependencies(dependencies: List[Any]) -> List[Dict[str, Any]]:
    """Checks 11 to 16, dependency by dependency; returns parsed dependencies."""
    seen: Set[Tuple[str, str]] = set()
    parsed: List[Dict[str, Any]] = []
    for index, dependency in enumerate(dependencies):
        path = f"dependencies[{index}]"
        _check_object(dependency, path, DEPENDENCY_KEYS, INVALID_REQUEST)
        for key in DEPENDENCY_KEYS:
            _check_string_field(dependency, path, key, INVALID_REQUEST)
        ecosystem = _check_ecosystem(dependency["ecosystem"], f"{path}.ecosystem")
        name = dependency["name"]
        if not _name_matches(ecosystem, name):
            _fail(INVALID_REQUEST, "invalid_name", path=f"{path}.name")
        key = (ecosystem, _normalise_name(ecosystem, name))
        if key in seen:
            _fail(DUPLICATE_DEPENDENCY, "duplicate_dependency", path=path)
        seen.add(key)
        current = _parse_version(ecosystem, dependency["current"])
        if current is None:
            _fail(INVALID_VERSION, "invalid_version", path=f"{path}.current")
        version_range = _parse_range(ecosystem, dependency["range"])
        if version_range is None:
            _fail(INVALID_RANGE, "invalid_range", path=f"{path}.range")
        parsed.append(
            {
                "name": name,
                "ecosystem": ecosystem,
                "key": key,
                "current": current,
                "range": version_range,
            }
        )
    return parsed


def _check_releases(
    releases: List[Any], as_of: date
) -> Dict[Tuple[str, str], List[Dict[str, Any]]]:
    """Checks 17 to 22, entry by entry and version by version.

    Returns, per normalised (ecosystem, name), the entry's versions in list
    order with versions that compare equal reduced to the first of them.
    """
    end_of_as_of = _end_of_day(as_of)
    entries: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for index, entry in enumerate(releases):
        path = f"releases[{index}]"
        _check_object(entry, path, RELEASE_ENTRY_KEYS, INVALID_REQUEST)
        for key in ("name", "ecosystem"):
            _check_string_field(entry, path, key, INVALID_REQUEST)
        _check_present(entry, path, "versions", INVALID_REQUEST)
        if not isinstance(entry["versions"], list):
            _fail(INVALID_REQUEST, "not_a_list", path=f"{path}.versions")
        ecosystem = _check_ecosystem(entry["ecosystem"], f"{path}.ecosystem")
        if not _name_matches(ecosystem, entry["name"]):
            _fail(INVALID_REQUEST, "invalid_name", path=f"{path}.name")
        key = (ecosystem, _normalise_name(ecosystem, entry["name"]))
        if key in entries:
            _fail(INVALID_REQUEST, "duplicate_release_entry", path=path)
        versions: List[Dict[str, Any]] = []
        seen_keys: Set[Tuple[Any, ...]] = set()
        for position, item in enumerate(entry["versions"]):
            version_path = f"{path}.versions[{position}]"
            _check_object(item, version_path, VERSION_KEYS, INVALID_REQUEST)
            _check_string_field(item, version_path, "version", INVALID_REQUEST)
            _check_string_field(item, version_path, "published_at", INVALID_REQUEST)
            published_at = _parse_timestamp(item["published_at"])
            if published_at is None:
                _fail(
                    INVALID_REQUEST,
                    "invalid_timestamp",
                    path=f"{version_path}.published_at",
                )
            if "yanked" in item and not isinstance(item["yanked"], bool):
                _fail(INVALID_REQUEST, "not_a_boolean", path=f"{version_path}.yanked")
            if "notes" in item and not _is_string(item["notes"]):
                _fail(INVALID_REQUEST, "not_a_string", path=f"{version_path}.notes")
            version = _parse_version(ecosystem, item["version"])
            if version is None:
                _fail(
                    INVALID_VERSION, "invalid_version", path=f"{version_path}.version"
                )
            if published_at > end_of_as_of:
                _fail(
                    FUTURE_RELEASE,
                    "future_release",
                    path=f"{version_path}.published_at",
                )
            if version.key in seen_keys:
                continue
            seen_keys.add(version.key)
            versions.append(
                {
                    "version": version,
                    "published_on": published_at.date(),
                    "yanked": item.get("yanked", False),
                    "notes": item.get("notes", ""),
                }
            )
        entries[key] = versions
    return entries


def _check_advisories(advisories: List[Any]) -> Dict[Tuple[str, str], List[_Range]]:
    """Checks 23 to 26, advisory by advisory; returns ranges per dependency key."""
    seen_ids: Set[str] = set()
    by_dependency: Dict[Tuple[str, str], List[_Range]] = {}
    for index, advisory in enumerate(advisories):
        path = f"advisories[{index}]"
        _check_object(advisory, path, ADVISORY_KEYS, INVALID_REQUEST)
        _check_string_field(advisory, path, "id", INVALID_REQUEST)
        if not _ID_RE.fullmatch(advisory["id"]):
            _fail(INVALID_REQUEST, "invalid_id", path=f"{path}.id")
        if advisory["id"] in seen_ids:
            _fail(INVALID_REQUEST, "duplicate_id", path=f"{path}.id")
        seen_ids.add(advisory["id"])
        for key in ("name", "ecosystem", "affected"):
            _check_string_field(advisory, path, key, INVALID_REQUEST)
        ecosystem = _check_ecosystem(advisory["ecosystem"], f"{path}.ecosystem")
        if not _name_matches(ecosystem, advisory["name"]):
            _fail(INVALID_REQUEST, "invalid_name", path=f"{path}.name")
        affected = _parse_range(ecosystem, advisory["affected"])
        if affected is None:
            _fail(INVALID_RANGE, "invalid_range", path=f"{path}.affected")
        key = (ecosystem, _normalise_name(ecosystem, advisory["name"]))
        by_dependency.setdefault(key, []).append(affected)
    return by_dependency


# --------------------------------------------------------------------------
# assess: the eight rules
# --------------------------------------------------------------------------


def _highest(versions: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    best: Optional[Dict[str, Any]] = None
    for item in versions:
        if best is None or item["version"].key > best["version"].key:
            best = item
    return best


def _lowest(versions: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    best: Optional[Dict[str, Any]] = None
    for item in versions:
        if best is None or item["version"].key < best["version"].key:
            best = item
    return best


def _unknown_result(
    dependency: Dict[str, Any], latest: Optional[str], reasons: List[str]
) -> Dict[str, Any]:
    return {
        "name": dependency["name"],
        "ecosystem": dependency["ecosystem"],
        "current": dependency["current"].text,
        "latest": latest,
        "latest_compatible": None,
        "drift": None,
        "verdict": VERDICT_UNKNOWN,
        "target": None,
        "reasons": reasons,
        "signals": None,
    }


def _assess_dependency(
    dependency: Dict[str, Any],
    entry: List[Dict[str, Any]],
    advisories: List[_Range],
    policy: Dict[str, Any],
    as_of: date,
    terms: List[str],
) -> Dict[str, Any]:
    current: _Version = dependency["current"]
    version_range: _Range = dependency["range"]
    advisory_hit = any(affected.contains(current) for affected in advisories)
    advisory_reason = [ADVISORY] if advisory_hit else []

    # Rule 8: no release entry, or no eligible version in it.
    eligible = [
        item
        for item in entry
        if not item["yanked"]
        and (not item["version"].prerelease or policy["allow_prerelease"])
    ]
    if not eligible:
        return _unknown_result(
            dependency, None, [NO_RELEASE_SNAPSHOT] + advisory_reason
        )
    latest = _highest(eligible)["version"]

    # The one fail-closed step outside the eight rules.
    if not version_range.contains(current):
        return _unknown_result(
            dependency, latest.text, [CURRENT_OUT_OF_RANGE] + advisory_reason
        )

    # The notes of the eligible versions above current and up to latest are
    # scanned once; rule 6 and signals.breaking_terms both read this list.
    scanned = [
        (item, _breaking_terms_in(item["notes"], terms))
        for item in eligible
        if current.key < item["version"].key <= latest.key
    ]

    # Rule 7 facts: eligibility is applied above; the counts are reported.
    above_current = [item for item in entry if item["version"].key > current.key]
    yanked_skipped = sum(1 for item in above_current if item["yanked"])
    highest_overall = _highest(entry)["version"]
    prerelease_skipped = highest_overall.prerelease and not policy["allow_prerelease"]

    newer_in_range = [
        item
        for item in eligible
        if item["version"].key > current.key and version_range.contains(item["version"])
    ]
    same_major_in_range = [
        item
        for item in eligible
        if version_range.contains(item["version"])
        and item["version"].major == current.major
    ]
    latest_compatible_item = _highest(same_major_in_range)
    latest_compatible = (
        None if latest_compatible_item is None else latest_compatible_item["version"]
    )
    major_available = any(item["version"].major > current.major for item in eligible)
    newer_same_major = any(
        item["version"].major == current.major for item in newer_in_range
    )

    verdict = VERDICT_HOLD
    target: Optional[_Version] = None
    reasons: List[str] = []
    if advisory_hit:
        # Rule 1.
        fixes = [
            item
            for item in newer_in_range
            if not any(affected.contains(item["version"]) for affected in advisories)
        ]
        fix = _lowest(fixes)
        if fix is not None:
            verdict, target, reasons = VERDICT_UPGRADE, fix["version"], [ADVISORY]
        else:
            reasons = [ADVISORY, NO_FIX_IN_RANGE]
            if major_available:
                reasons.append(MAJOR_AVAILABLE)
    elif not newer_same_major and not major_available:
        # Rule 2.
        reasons = [UP_TO_DATE]
    elif newer_same_major and latest_compatible.minor == current.minor:
        # Rule 3.
        age = (as_of - latest_compatible_item["published_on"]).days
        if age >= policy["min_age_days"]:
            verdict, target = VERDICT_UPGRADE, latest_compatible
            reasons = [PATCH_AVAILABLE, MIN_AGE_MET]
        else:
            reasons = [PATCH_AVAILABLE, MIN_AGE_NOT_MET]
    elif newer_same_major:
        # Rule 4.
        verdict, target, reasons = VERDICT_TEST, latest_compatible, [MINOR_AVAILABLE]
    elif policy["allow_major"]:
        # Rule 5, major upgrades allowed.
        higher = [item for item in eligible if item["version"].major > current.major]
        verdict, target = VERDICT_TEST, _highest(higher)["version"]
        reasons = [MAJOR_AVAILABLE, MAJOR_ALLOWED]
    else:
        # Rule 5, major upgrades not allowed.
        reasons = [MAJOR_AVAILABLE]

    # Rule 6: a breaking term between current and the target lowers the verdict.
    if verdict in (VERDICT_UPGRADE, VERDICT_TEST):
        if any(found for item, found in scanned if item["version"].key <= target.key):
            if verdict == VERDICT_UPGRADE and not policy["strict"]:
                verdict = VERDICT_TEST
            else:
                verdict, target = VERDICT_HOLD, None
            reasons.append(BREAKING_TERM)
    if yanked_skipped:
        reasons.append(YANKED_SKIPPED)
    if prerelease_skipped:
        reasons.append(PRERELEASE_SKIPPED)

    breaking_terms: List[str] = []
    for _, found in scanned:
        for term in found:
            if term not in breaking_terms:
                breaking_terms.append(term)
    breaking_terms.sort(key=terms.index)

    drift = {"major": max(0, latest.major - current.major), "minor": 0, "patch": 0}
    if latest_compatible is not None:
        drift["minor"] = max(0, latest_compatible.minor - current.minor)
        if latest_compatible.minor == current.minor:
            drift["patch"] = max(0, latest_compatible.patch - current.patch)
    return {
        "name": dependency["name"],
        "ecosystem": dependency["ecosystem"],
        "current": current.text,
        "latest": latest.text,
        "latest_compatible": (
            None if latest_compatible is None else latest_compatible.text
        ),
        "drift": drift,
        "verdict": verdict,
        "target": None if target is None else target.text,
        "reasons": reasons,
        "signals": {
            "breaking_terms": breaking_terms,
            "yanked_skipped": yanked_skipped,
            "advisory": advisory_hit,
            "major_available": major_available,
        },
    }


def _assess(params: Dict[str, Any], terms: List[str]) -> Dict[str, Any]:
    _check_request_keys(params, ASSESS_REQUEST_KEYS)
    as_of = _check_as_of(params)
    snapshot_at = _check_snapshot_at(params, as_of)
    policy = _check_assess_policy(params)
    for key in ("dependencies", "releases", "advisories"):
        if key in params and not isinstance(params[key], list):
            _fail(INVALID_REQUEST, "not_a_list", path=key)
    dependencies = params.get("dependencies")
    if not dependencies:
        _fail(EMPTY_DEPENDENCIES, "empty_dependencies", path="dependencies")
    releases = params.get("releases", [])
    advisories = params.get("advisories", [])
    _check_assess_caps(dependencies, releases, advisories)
    parsed = _check_dependencies(dependencies)
    entries = _check_releases(releases, as_of)
    by_dependency = _check_advisories(advisories)

    results = [
        _assess_dependency(
            dependency,
            entries.get(dependency["key"], []),
            by_dependency.get(dependency["key"], []),
            policy,
            as_of,
            terms,
        )
        for dependency in parsed
    ]
    summary = {verdict: 0 for verdict in SUMMARY_KEYS}
    for result in results:
        summary[result["verdict"]] += 1
    snapshot_age = (as_of - snapshot_at.date()).days
    return {
        "status": "completed",
        "results": results,
        "summary": summary,
        "fired_any": summary[VERDICT_UPGRADE] + summary[VERDICT_TEST] > 0,
        "insufficient": summary[VERDICT_UNKNOWN] > 0,
        "stale_snapshot": snapshot_age > policy["max_snapshot_age_days"],
    }


# --------------------------------------------------------------------------
# digest: validation (checks 6 to 13)
# --------------------------------------------------------------------------


def _check_since(params: Dict[str, Any], as_of: date) -> date:
    """Check 6."""
    since = _parse_date(params.get("since"))
    if since is None:
        _fail(INVALID_WINDOW, "invalid_since", path="since")
    if since > as_of:
        _fail(INVALID_WINDOW, "since_after_as_of", path="since")
    return since


def _check_digest_policy(params: Dict[str, Any]) -> List[str]:
    """Checks 7 (the policy part) and 8; returns the raw pattern list."""
    policy = params.get("policy", {})
    if not isinstance(policy, dict):
        _fail(INVALID_REQUEST, "not_an_object", path="policy")
    for key in policy:
        if key not in DIGEST_POLICY_KEYS:
            _fail(INVALID_POLICY, "unknown_key", path="policy")
    patterns = policy.get("paths_of_interest", [])
    if not _is_string_list(patterns):
        _fail(INVALID_POLICY, "not_a_string_list", path="policy.paths_of_interest")
    return patterns


def _check_digest_caps(watched: List[Any], patterns: List[str]) -> None:
    """Check 9, in the field order watched, policy, then the pair bound."""
    if len(watched) > MAX_REPOSITORIES:
        _cap_fail("watched", MAX_REPOSITORIES, len(watched))
    total_events = 0
    total_titles = 0
    total_paths = 0
    total_path_chars = 0
    for index, repository in enumerate(watched):
        if not isinstance(repository, dict) or "events" not in repository:
            continue
        path = f"watched[{index}]"
        events = repository["events"]
        if not isinstance(events, list):
            _fail(INVALID_REQUEST, "invalid_type", path=f"{path}.events")
        if len(events) > MAX_EVENTS_PER_REPOSITORY:
            _cap_fail(f"{path}.events", MAX_EVENTS_PER_REPOSITORY, len(events))
        total_events += len(events)
        if total_events > MAX_TOTAL_EVENTS:
            _cap_fail("watched", MAX_TOTAL_EVENTS, total_events)
        for position, event in enumerate(events):
            if not isinstance(event, dict):
                continue
            event_path = f"{path}.events[{position}]"
            if "title" in event:
                _check_length(event["title"], f"{event_path}.title", MAX_TITLE_CHARS)
                total_titles += len(event["title"])
                if total_titles > MAX_TOTAL_TITLE_CHARS:
                    _cap_fail("watched", MAX_TOTAL_TITLE_CHARS, total_titles)
            if "paths" not in event:
                continue
            paths = event["paths"]
            if not isinstance(paths, list):
                _fail(INVALID_REQUEST, "invalid_type", path=f"{event_path}.paths")
            if len(paths) > MAX_PATHS_PER_EVENT:
                _cap_fail(f"{event_path}.paths", MAX_PATHS_PER_EVENT, len(paths))
            total_paths += len(paths)
            if total_paths > MAX_TOTAL_PATHS:
                _cap_fail("watched", MAX_TOTAL_PATHS, total_paths)
            for slot, item in enumerate(paths):
                _check_length(item, f"{event_path}.paths[{slot}]", MAX_PATH_CHARS)
                total_path_chars += len(item)
                if total_path_chars > MAX_TOTAL_PATH_CHARS:
                    _cap_fail("watched", MAX_TOTAL_PATH_CHARS, total_path_chars)
    if len(patterns) > MAX_PATTERNS:
        _cap_fail("policy.paths_of_interest", MAX_PATTERNS, len(patterns))
    for index, pattern in enumerate(patterns):
        _check_length(pattern, f"policy.paths_of_interest[{index}]", MAX_PATTERN_CHARS)
    pairs = total_paths * len(patterns)
    if pairs > MAX_PATH_PATTERN_PAIRS:
        _cap_fail("policy.paths_of_interest", MAX_PATH_PATTERN_PAIRS, pairs)


def _check_patterns(patterns: List[str]) -> List[Tuple[str, List[Tuple[str, str]]]]:
    """Check 10; returns (pattern, elements) pairs, a pattern listed twice once."""
    compiled: List[Tuple[str, List[Tuple[str, str]]]] = []
    seen: Set[str] = set()
    for index, pattern in enumerate(patterns):
        try:
            elements = _compile_pattern(pattern)
        except _ContractError as error:
            error.detail["path"] = f"policy.paths_of_interest[{index}]"
            raise
        if pattern not in seen:
            seen.add(pattern)
            compiled.append((pattern, elements))
    return compiled


def _check_watched(watched: List[Any], as_of: date) -> List[Dict[str, Any]]:
    """Checks 11 to 13, repository by repository and event by event."""
    end_of_as_of = _end_of_day(as_of)
    seen_repos: Set[str] = set()
    repositories: List[Dict[str, Any]] = []
    for index, repository in enumerate(watched):
        path = f"watched[{index}]"
        _check_object(repository, path, WATCHED_KEYS, INVALID_REQUEST)
        _check_present(repository, path, "repo", INVALID_REQUEST)
        repo = repository["repo"]
        if not _is_string(repo) or not _REPO_RE.fullmatch(repo):
            _fail(INVALID_REQUEST, "invalid_repo", path=f"{path}.repo")
        if repo in seen_repos:
            _fail(INVALID_REQUEST, "duplicate_repo", path=f"{path}.repo")
        seen_repos.add(repo)
        _check_present(repository, path, "events", INVALID_REQUEST)
        if not isinstance(repository["events"], list):
            _fail(INVALID_REQUEST, "not_a_list", path=f"{path}.events")
        seen_ids: Set[str] = set()
        events: List[Dict[str, Any]] = []
        for position, event in enumerate(repository["events"]):
            event_path = f"{path}.events[{position}]"
            _check_object(event, event_path, EVENT_KEYS, INVALID_REQUEST)
            _check_present(event, event_path, "type", INVALID_REQUEST)
            if not _is_string(event["type"]) or event["type"] not in EVENT_TYPES:
                _fail(INVALID_REQUEST, "unknown_event_type", path=f"{event_path}.type")
            _check_present(event, event_path, "id", INVALID_REQUEST)
            event_id = event["id"]
            if not _is_string(event_id) or not _ID_RE.fullmatch(event_id):
                _fail(INVALID_REQUEST, "invalid_id", path=f"{event_path}.id")
            if event_id in seen_ids:
                _fail(INVALID_REQUEST, "duplicate_id", path=f"{event_path}.id")
            seen_ids.add(event_id)
            _check_present(event, event_path, "at", INVALID_REQUEST)
            at = _parse_timestamp(event["at"])
            if at is None:
                _fail(INVALID_REQUEST, "invalid_timestamp", path=f"{event_path}.at")
            if "title" in event and not _is_string(event["title"]):
                _fail(INVALID_REQUEST, "not_a_string", path=f"{event_path}.title")
            if "paths" in event and not _is_string_list(event["paths"]):
                _fail(INVALID_REQUEST, "not_a_string_list", path=f"{event_path}.paths")
            if at > end_of_as_of:
                raise _ContractError(
                    FUTURE_EVENT, {"event_id": event_id, "at": event["at"]}
                )
            events.append(
                {
                    "type": event["type"],
                    "id": event_id,
                    "at": at,
                    "title": event.get("title"),
                    "paths": event.get("paths", []),
                }
            )
        repositories.append({"repo": repo, "events": events})
    return repositories


# --------------------------------------------------------------------------
# digest: aggregation
# --------------------------------------------------------------------------


def _jump_kind(previous: _Version, following: _Version) -> str:
    if following.key < previous.key:
        return "downgrade"
    if following.major != previous.major:
        return "major"
    if following.minor != previous.minor:
        return "minor"
    if following.patch != previous.patch:
        return "patch"
    return "prerelease"


def _title_version(event: Dict[str, Any]) -> Optional[_Version]:
    """A release or tag title read as a version after one leading `v`.

    What remains must be at most MAX_VERSION_CHARS characters, the cap of an
    assess version, and valid semver; a longer title does not parse.
    """
    title = event["title"]
    if event["type"] == "commit" or title is None:
        return None
    if title.startswith("v"):
        title = title[1:]
    if len(title) > MAX_VERSION_CHARS:
        return None
    return _parse_npm(title)


def _digest_repository(
    repository: Dict[str, Any],
    window_start: datetime,
    patterns: List[Tuple[str, List[Tuple[str, str]]]],
    terms: List[str],
) -> Dict[str, Any]:
    events = sorted(repository["events"], key=lambda event: (event["at"], event["id"]))
    dropped = [event for event in events if event["at"] < window_start]
    inside = [event for event in events if event["at"] >= window_start]
    counts = {kind: 0 for kind in EVENT_TYPES}
    for event in inside:
        counts[event["type"]] += 1

    # Version jumps: parsed titles in (at, id) order, de-duplicated by
    # precedence, the baseline being the latest dropped parsed version.
    unparsed = 0
    parsed: List[Tuple[Dict[str, Any], _Version]] = []
    seen_keys: Set[Tuple[Any, ...]] = set()
    for event in events:
        if event["type"] == "commit":
            continue
        version = _title_version(event)
        if version is None:
            if event["at"] >= window_start:
                unparsed += 1
            continue
        if version.key in seen_keys:
            continue
        seen_keys.add(version.key)
        parsed.append((event, version))
    previous: Optional[_Version] = None
    for event, version in parsed:
        if event["at"] < window_start:
            previous = version
    jumps: List[Dict[str, Any]] = []
    jump_events: Set[str] = set()
    for event, version in parsed:
        if event["at"] < window_start:
            continue
        if previous is not None:
            kind = _jump_kind(previous, version)
            jumps.append({"from": previous.text, "to": version.text, "kind": kind})
            if kind in ("major", "minor", "downgrade"):
                jump_events.add(event["id"])
        previous = version

    breaking_hits: List[Dict[str, str]] = []
    path_hits: List[Dict[str, str]] = []
    notable: List[str] = []
    for event in inside:
        hit = event["id"] in jump_events
        if event["title"] is not None:
            for term in _breaking_terms_in(event["title"], terms):
                breaking_hits.append({"event_id": event["id"], "term": term})
                hit = True
        if event["type"] == "commit":
            for pattern, elements in patterns:
                if any(_match_pattern(elements, path) for path in event["paths"]):
                    path_hits.append({"event_id": event["id"], "pattern": pattern})
                    hit = True
        if hit:
            notable.append(event["id"])
    return {
        "repo": repository["repo"],
        "counts": counts,
        "version_jumps": jumps,
        "breaking_hits": breaking_hits,
        "path_hits": path_hits,
        "notable": notable,
        "dropped_before_since": len(dropped),
        "unparsed_versions": unparsed,
    }


def _digest(params: Dict[str, Any], terms: List[str]) -> Dict[str, Any]:
    _check_request_keys(params, DIGEST_REQUEST_KEYS)
    as_of = _check_as_of(params)
    since = _check_since(params, as_of)
    if not isinstance(params.get("watched"), list):
        _fail(INVALID_REQUEST, "not_a_list", path="watched")
    raw_patterns = _check_digest_policy(params)
    watched = params["watched"]
    _check_digest_caps(watched, raw_patterns)
    patterns = _check_patterns(raw_patterns)
    repositories = _check_watched(watched, as_of)
    window_start = _start_of_day(since)
    per_repo = [
        _digest_repository(repository, window_start, patterns, terms)
        for repository in repositories
    ]
    return {
        "status": "completed",
        "window": {"since": params["since"], "as_of": params["as_of"]},
        "per_repo": per_repo,
        "fired_any": any(entry["notable"] for entry in per_repo),
        "insufficient": any(entry["unparsed_versions"] > 0 for entry in per_repo),
    }


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

# The manifest parameters, kept in code so that validate_params() works
# without a YAML reader; a bundle test pins this copy to manifest.yaml.
MANIFEST_PARAMETERS = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": list(ACTIONS),
            "description": (
                "digest summarises host-supplied repository events since a "
                "date; assess gives each pinned dependency a verdict against a "
                "release snapshot."
            ),
        },
        "as_of": {
            "type": "string",
            "description": (
                "Required by both actions: the evaluation date, YYYY-MM-DD. "
                "Time enters the skill only through the inputs."
            ),
        },
        "snapshot_at": {
            "type": "string",
            "description": (
                "assess input: when the release snapshot was taken, "
                "YYYY-MM-DDTHH:MM:SSZ, not after the end of as_of."
            ),
        },
        "since": {
            "type": "string",
            "description": (
                "digest input: the first day of the window, YYYY-MM-DD, not "
                "after as_of."
            ),
        },
        "policy": {
            "type": "object",
            "description": (
                "assess: allow_major, allow_prerelease (booleans), "
                "min_age_days, max_snapshot_age_days (integers 0 to 3650) and "
                "the optional boolean strict. digest: the optional list "
                "paths_of_interest of path patterns."
            ),
        },
        "dependencies": {
            "type": "array",
            "description": (
                "assess input: pinned dependencies, each an object with name, "
                "ecosystem (pypi or npm), current and range."
            ),
        },
        "releases": {
            "type": "array",
            "description": (
                "assess input: release snapshot entries, each with name, "
                "ecosystem and versions (version, published_at, optional "
                "yanked and notes)."
            ),
        },
        "advisories": {
            "type": "array",
            "description": (
                "assess input, optional: advisories, each with id, name, "
                "ecosystem and the affected range."
            ),
        },
        "watched": {
            "type": "array",
            "description": (
                "digest input: watched repositories, each with repo and its "
                "events (type, id, at, optional title and paths)."
            ),
        },
    },
    "required": ["action"],
    "additionalProperties": False,
}


def _dispatch(params: Any) -> Dict[str, Any]:
    if not isinstance(params, dict):
        _fail(INVALID_REQUEST, "parameters_not_object")
    action = params.get("action")
    if not _is_string(action) or action not in ACTIONS:
        _fail(INVALID_REQUEST, "unknown_action", path="action")
    terms = _load_breaking_terms()
    if action == ACTION_DIGEST:
        return _digest(params, terms)
    return _assess(params, terms)


class UpgradeGateSkill(BaseSkill):
    """Deterministic upgrade gate: digest of repository events and dependency verdicts."""

    @property
    def manifest(self) -> Dict[str, Any]:
        return {
            "name": REGISTRY_ID,
            "version": SKILL_VERSION,
            "parameters": MANIFEST_PARAMETERS,
        }

    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        try:
            return _dispatch(params)
        except _ContractError as error:
            return {
                "status": "error",
                "error": {"code": error.code, "detail": error.detail},
            }
        except Exception as error:
            return {
                "status": "error",
                "error": {
                    "code": INTERNAL_ERROR,
                    "detail": {
                        "reason": "internal_error",
                        "exception": type(error).__name__,
                    },
                },
            }
