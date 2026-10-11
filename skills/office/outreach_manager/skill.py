"""Deterministic search planning and lead ingestion for search-and-message platforms.

Two actions share one data model, the lead record:

  compile_search - turns a structured target specification and a platform
                   profile into a search plan: queries partitioned so that the
                   profile's per-query result cap is respected, each with a
                   ready search URL, plus pacing taken from the profile and an
                   explicit fit rubric.
  ingest_leads   - takes exported rows, normalises them through the profile's
                   column mapping, de-duplicates them on profile_key, scores
                   every row against the plan's rubric and criteria, flags
                   off-target rows and returns lead records (stage NEW) with a
                   review table.

The skill never touches a platform. It reads no file outside its bundle, makes
no network call, reads no clock and writes nothing. Identical input returns
identical output. A contract violation returns an error envelope with a code
from the closed registry below; an unexpected internal fault returns the
one-code fault registry entry INTERNAL_ERROR.
"""

import csv
import hashlib
import io
import json
import os
import re
import unicodedata
from datetime import date
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import quote, urlsplit

import yaml

from skill_assay.core.base_skill import BaseSkill

REGISTRY_ID = "office/outreach_manager"

ACTION_COMPILE_SEARCH = "compile_search"
ACTION_INGEST_LEADS = "ingest_leads"
ACTIONS = (ACTION_COMPILE_SEARCH, ACTION_INGEST_LEADS)

# Closed registry of contract-violation codes. These codes are the skill's
# identity and never change per operator.
INVALID_REQUEST = "INVALID_REQUEST"
LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
UNKNOWN_PLATFORM = "UNKNOWN_PLATFORM"
INVALID_PROFILE = "INVALID_PROFILE"
INVALID_TARGET_SPEC = "INVALID_TARGET_SPEC"
INVALID_PLAN = "INVALID_PLAN"
PLAN_MISMATCH = "PLAN_MISMATCH"
INVALID_EXISTING_LEADS = "INVALID_EXISTING_LEADS"
INVALID_ROWS = "INVALID_ROWS"
UNKNOWN_SOURCE_QUERY = "UNKNOWN_SOURCE_QUERY"

ERROR_REGISTRY = frozenset(
    {
        INVALID_REQUEST,
        LIMIT_EXCEEDED,
        UNKNOWN_PLATFORM,
        INVALID_PROFILE,
        INVALID_TARGET_SPEC,
        INVALID_PLAN,
        PLAN_MISMATCH,
        INVALID_EXISTING_LEADS,
        INVALID_ROWS,
        UNKNOWN_SOURCE_QUERY,
    }
)

# One-code fault registry, returned only by the outermost handler of execute()
# when an unexpected exception escapes the contract checks.
INTERNAL_ERROR = "INTERNAL_ERROR"
FAULT_REGISTRY = frozenset({INTERNAL_ERROR})

# Bundled platform profiles. The request's platform value selects an entry of
# this mapping; the request value itself never builds a path.
BUNDLED_PROFILE_FILES = {"demo": "demo.json", "linkedin": "linkedin.json"}
BUNDLED_PLATFORMS = tuple(sorted(BUNDLED_PROFILE_FILES))
SENIORITY_TERMS_FILE = "seniority_terms.json"

# Every file the skill opens lives in this bundle; one open() call per entry.
BUNDLE_READS = (
    "manifest.yaml",
    "kb/platforms/<platform>.json and kb/seniority_terms.json (one JSON reader)",
)

# The fit rubric: constants of the skill, carried in every plan.
RUBRIC = {
    "title_match": 40,
    "seniority": 20,
    "industry": 20,
    "geo": 10,
    "language": 10,
    "exclusion_hit": -100,
    "off_target_below": 50,
}
RUBRIC_KEYS = tuple(RUBRIC)
SCORED_COMPONENTS = ("title_match", "seniority", "industry", "geo", "language")
EXCLUSION_REASON_PREFIX = "exclusion_hit:"
STAGE_NEW = "NEW"
PACING_SOURCE = "platform_profile"

PROFILE_ORIGIN_BUNDLED = "bundled"
PROFILE_ORIGIN_OVERRIDE = "override"
PROFILE_ORIGINS = (PROFILE_ORIGIN_BUNDLED, PROFILE_ORIGIN_OVERRIDE)

# Filter dimensions in the fixed order used for query filters and search URLs.
FILTER_DIMENSIONS = (
    "geo",
    "company_size",
    "seniority",
    "industry",
    "language",
    "network",
)
SPEC_LIST_FOR_DIMENSION = {
    "geo": "geos",
    "company_size": "company_size",
    "seniority": "seniority",
    "industry": "industries",
    "language": "languages",
    "network": "network",
}
DIMENSION_FOR_SPEC_LIST = {name: dim for dim, name in SPEC_LIST_FOR_DIMENSION.items()}
# Dimensions whose values are platform-defined: they need a profile filter.
PLATFORM_DEFINED_DIMENSIONS = ("company_size", "network")
SENIORITY_LEVELS = ("entry", "senior", "manager", "director", "vp", "cxo", "owner")

# target_spec fields, in the documented input order.
TERM_LISTS = ("titles", "keywords_include", "keywords_exclude", "industries")
SPEC_LIST_FIELDS = (
    "titles",
    "keywords_include",
    "keywords_exclude",
    "industries",
    "seniority",
    "geos",
    "languages",
    "company_size",
    "network",
)
SPEC_KEYS = ("schema_version",) + SPEC_LIST_FIELDS + ("estimated_matches",)
CRITERIA_FIELDS = tuple(field for field in SPEC_LIST_FIELDS if field != "network")

# Request, plan and output key sets.
COMMON_REQUEST_KEYS = ("action", "platform", "profile_override")
COMPILE_REQUEST_KEYS = COMMON_REQUEST_KEYS + ("target_spec",)
INGEST_REQUEST_KEYS = COMMON_REQUEST_KEYS + ("plan", "rows", "existing_leads")
ALL_REQUEST_KEYS = frozenset(COMPILE_REQUEST_KEYS + INGEST_REQUEST_KEYS)

PLAN_KEYS = (
    "plan_id",
    "platform",
    "profile_version",
    "profile_origin",
    "queries",
    "pacing",
    "rubric",
    "criteria",
    "partitioning",
)
QUERY_KEYS = ("id", "partition", "keywords", "filters", "expected_cap", "search_url")
PACING_KEYS = ("per_run_max_queries", "per_day_max_rows", "source")
PARTITIONING_KEYS = ("dimensions", "estimated_matches", "cap_risk")
LEAD_KEYS = (
    "lead_id",
    "profile_key",
    "score",
    "reasons",
    "off_target",
    "stage",
    "source_query",
)
REVIEW_COLUMNS = (
    "lead_id",
    "score",
    "off_target",
    "title",
    "company",
    "location",
    "reasons",
    "source_query",
)
EXISTING_LEAD_KEYS = ("profile_key", "lead_id")
STATS_KEYS = ("rows_in", "deduped", "off_target", "kept")

# Platform profile key sets (bundled files and overrides share one schema).
PROFILE_KEYS = (
    "schema_version",
    "platform",
    "profile_version",
    "verified_on",
    "sources",
    "search",
    "filters",
    "partition_order",
    "pacing",
    "export_columns",
    "profile_key",
)
SOURCE_KEYS = ("title", "reference", "retrieved_on")
SEARCH_KEYS = ("per_query_result_cap", "max_query_length", "grammar", "url")
GRAMMAR_KEYS = (
    "and",
    "or",
    "not",
    "quote",
    "group_open",
    "group_close",
    "forbidden_characters",
    "reserved_words",
)
URL_KEYS = (
    "base",
    "keywords_param",
    "fixed_params",
    "filter_value_encoding",
    "max_length",
)
FIXED_PARAM_KEYS = ("name", "value")
FILTER_KEYS = ("filter_id", "values")
PROFILE_PACING_KEYS = ("per_run_max_queries", "per_day_max_rows")
EXPORT_COLUMNS_REQUIRED = ("profile_url", "title", "source_query")
EXPORT_COLUMNS_OPTIONAL = ("company", "location")
PROFILE_KEY_KEYS = ("host_suffixes", "path_prefix")
FILTER_VALUE_ENCODING = "json_string_array"

# Size caps. Every cap is listed in instructions.md and has one test case.
LIST_CAPS = {
    "titles": 20,
    "keywords_include": 20,
    "keywords_exclude": 20,
    "industries": 20,
    "seniority": 7,
    "geos": 50,
    "languages": 20,
    "company_size": 20,
    "network": 10,
}
MAX_TERM_CHARS = 100
MAX_TERM_TOKENS = 10
MAX_ESTIMATED_MATCHES = 10_000_000
MAX_QUERIES = 200
MAX_ROWS = 5_000
MAX_ROW_KEYS = 64
MAX_CELL_CHARS = 2_000
MAX_TOTAL_CELL_CHARS = 2_000_000
MAX_EXISTING_LEADS = 50_000
SCORING_TEXT_CHARS = 200
MAX_PROFILE_SOURCES = 20
MAX_BASE_URL_CHARS = 200
MAX_FIXED_PARAMS = 10
MAX_FIXED_PARAM_VALUE_CHARS = 100
MAX_SEARCH_URL_LENGTH_SETTING = 2_000
MAX_PROFILE_TEXT_CHARS = 500
MAX_PROFILE_LIST_ENTRIES = 500
MAX_PROFILE_INTEGER = 10_000_000

# (field, unit, limit, code) rows of the cap table in instructions.md.
CAP_TABLE = (
    ("target_spec.titles", "entries", LIST_CAPS["titles"], LIMIT_EXCEEDED),
    (
        "target_spec.keywords_include",
        "entries",
        LIST_CAPS["keywords_include"],
        LIMIT_EXCEEDED,
    ),
    (
        "target_spec.keywords_exclude",
        "entries",
        LIST_CAPS["keywords_exclude"],
        LIMIT_EXCEEDED,
    ),
    ("target_spec.industries", "entries", LIST_CAPS["industries"], LIMIT_EXCEEDED),
    ("target_spec.seniority", "entries", LIST_CAPS["seniority"], LIMIT_EXCEEDED),
    ("target_spec.geos", "entries", LIST_CAPS["geos"], LIMIT_EXCEEDED),
    ("target_spec.languages", "entries", LIST_CAPS["languages"], LIMIT_EXCEEDED),
    (
        "target_spec.company_size",
        "entries",
        LIST_CAPS["company_size"],
        LIMIT_EXCEEDED,
    ),
    ("target_spec.network", "entries", LIST_CAPS["network"], LIMIT_EXCEEDED),
    ("term", "characters", MAX_TERM_CHARS, LIMIT_EXCEEDED),
    ("term", "tokens", MAX_TERM_TOKENS, LIMIT_EXCEEDED),
    (
        "target_spec.estimated_matches",
        "value",
        MAX_ESTIMATED_MATCHES,
        LIMIT_EXCEEDED,
    ),
    ("plan.queries", "entries", MAX_QUERIES, LIMIT_EXCEEDED),
    (
        "plan.queries[].keywords",
        "characters",
        "profile search.max_query_length",
        LIMIT_EXCEEDED,
    ),
    (
        "plan.queries[].search_url",
        "characters",
        "profile search.url.max_length",
        LIMIT_EXCEEDED,
    ),
    ("rows", "entries", MAX_ROWS, LIMIT_EXCEEDED),
    ("rows[]", "keys", MAX_ROW_KEYS, LIMIT_EXCEEDED),
    ("rows[] cell", "characters", MAX_CELL_CHARS, LIMIT_EXCEEDED),
    ("rows (all cells together)", "characters", MAX_TOTAL_CELL_CHARS, LIMIT_EXCEEDED),
    ("existing_leads", "entries", MAX_EXISTING_LEADS, LIMIT_EXCEEDED),
    (
        "scored text cell",
        "characters read",
        SCORING_TEXT_CHARS,
        "truncated, not an error",
    ),
    ("profile sources", "entries", MAX_PROFILE_SOURCES, INVALID_PROFILE),
    ("profile search.url.base", "characters", MAX_BASE_URL_CHARS, INVALID_PROFILE),
    (
        "profile search.url.fixed_params",
        "entries",
        MAX_FIXED_PARAMS,
        INVALID_PROFILE,
    ),
    (
        "profile search.url.fixed_params[].value",
        "characters",
        MAX_FIXED_PARAM_VALUE_CHARS,
        INVALID_PROFILE,
    ),
    (
        "profile search.url.max_length",
        "value",
        MAX_SEARCH_URL_LENGTH_SETTING,
        INVALID_PROFILE,
    ),
    ("profile text field", "characters", MAX_PROFILE_TEXT_CHARS, INVALID_PROFILE),
    ("profile list field", "entries", MAX_PROFILE_LIST_ENTRIES, INVALID_PROFILE),
    ("profile integer field", "value", MAX_PROFILE_INTEGER, INVALID_PROFILE),
)

# The validation order of each action, one line per check. instructions.md
# carries both lists verbatim; the first failing check's code is returned.
COMPILE_SEARCH_CHECKS = (
    (INVALID_REQUEST, "parameters are not an object."),
    (
        INVALID_REQUEST,
        "`action` absent, not a string, or not one of the two action names.",
    ),
    (
        INVALID_REQUEST,
        "an unknown top-level key, or a key of the other action "
        "(`plan`, `rows`, `existing_leads`).",
    ),
    (
        INVALID_REQUEST,
        "`platform` absent, not a string, or not matching `^[a-z][a-z0-9_]{0,31}$`.",
    ),
    (
        INVALID_REQUEST,
        "`target_spec` absent or not an object; `profile_override` present "
        "and not an object.",
    ),
    (
        INVALID_PROFILE,
        "an override is present and fails the profile checks, or its "
        "`platform` differs from the request's.",
    ),
    (
        UNKNOWN_PLATFORM,
        "no override, and `platform` is not one of the bundled ids (a fixed "
        "tuple in code; the request value never builds a path).",
    ),
    (INVALID_PROFILE, "the bundled profile file fails the profile checks."),
    (
        INVALID_TARGET_SPEC,
        "`schema_version` is not the integer 1; an unknown key; `titles` "
        "absent; a list field that is not a list of strings; "
        "`estimated_matches` present and not an integer.",
    ),
    (
        LIMIT_EXCEEDED,
        "a list longer than its cap; a term longer than 100 characters or "
        "with more than 10 tokens; `estimated_matches` above 10,000,000.",
    ),
    (
        INVALID_TARGET_SPEC,
        "a term empty before or after sanitisation; a `seniority` value "
        "outside the closed set; a `geos` value failing `^[A-Z]{2}$`, or "
        "absent from the profile's `geo` value list when the profile gives "
        "one; a `languages` value failing `^[a-z]{2}$`; `company_size` given "
        "when the profile has no `company_size` filter, or a value absent "
        "from that filter's list; `network` given when the profile has no "
        "`network` filter, or a value absent from that filter's list.",
    ),
    (
        LIMIT_EXCEEDED,
        "more than 200 generated queries, or a query string longer than the "
        "profile's `max_query_length`, or a `search_url` longer than the "
        "profile's `search.url.max_length`.",
    ),
)

INGEST_LEADS_CHECKS = (
    (INVALID_REQUEST, "parameters are not an object."),
    (
        INVALID_REQUEST,
        "`action` absent, not a string, or not one of the two action names.",
    ),
    (
        INVALID_REQUEST,
        "an unknown top-level key, or the other action's key `target_spec`.",
    ),
    (
        INVALID_REQUEST,
        "`platform` absent, not a string, or not matching its pattern.",
    ),
    (
        INVALID_REQUEST,
        "`plan` absent or not an object; `rows` absent or not a list; "
        "`existing_leads` present and not a list; `profile_override` present "
        "and not an object.",
    ),
    (
        INVALID_PROFILE,
        "an override is present and fails the profile checks, or its "
        "`platform` differs from the request's.",
    ),
    (UNKNOWN_PLATFORM, "no override, and `platform` is not a bundled id."),
    (INVALID_PROFILE, "the bundled profile file fails the profile checks."),
    (
        INVALID_PLAN,
        "the plan fails its shape check: the plan key set (`profile_origin` "
        "included), each query's key set and types (`search_url` included), "
        "`rubric` the seven integers, `criteria` and `partitioning` present.",
    ),
    (LIMIT_EXCEEDED, "more than 200 queries in the plan."),
    (
        PLAN_MISMATCH,
        "`plan.platform` or `plan.profile_version` differs from the resolved "
        "profile, or `plan.profile_origin` differs from how the profile was "
        "resolved in this call, or `plan_id` is not the digest recomputed "
        "from the plan body. An edited plan is rejected.",
    ),
    (LIMIT_EXCEEDED, "more than 50,000 `existing_leads`."),
    (
        INVALID_EXISTING_LEADS,
        "an element is not an object with string `profile_key` and `lead_id` "
        "and no other key.",
    ),
    (LIMIT_EXCEEDED, "more than 5,000 rows."),
    (INVALID_ROWS, "a row is not an object. `detail` gives the row index."),
    (
        LIMIT_EXCEEDED,
        "a row with more than 64 keys; a cell longer than 2,000 characters; "
        "total characters across all row cells above 2,000,000.",
    ),
    (
        INVALID_ROWS,
        "a mapped required column (profile URL, title, source query) absent "
        "or not a string; no profile key can be derived from the URL. "
        "`detail` gives the row index.",
    ),
    (
        UNKNOWN_SOURCE_QUERY,
        "the row's source-query cell equals neither the `id` nor the "
        "`search_url` of a query in `plan.queries` (exact string equality, "
        "no normalisation).",
    ),
)

_PLATFORM_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_PARAM_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,31}$")
_GEO_RE = re.compile(r"^[A-Z]{2}$")
_LANGUAGE_RE = re.compile(r"^[a-z]{2}$")
_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._%-]{0,199}$")
_LEAD_ID_RE = re.compile(r"^L([0-9]{1,18})$")
_HOST_LABEL_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_BASE_URL_CHARS_RE = re.compile(r"^[A-Za-z0-9\-._~:/%]+$")
_TOKEN_RE = re.compile(r"[^\W_]+")

# Unicode categories removed from terms and text cells: control, format,
# line separator and paragraph separator.
_INVISIBLE_CATEGORIES = ("Cc", "Cf", "Zl", "Zp")
# Characters that make a spreadsheet treat a cell as a formula, with their
# full-width forms.
_FORMULA_LEADERS = "=+-@" + chr(0xFF1D) + chr(0xFF0B) + chr(0xFF0D) + chr(0xFF20)


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


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _is_date(value: Any) -> bool:
    if not isinstance(value, str) or not _DATE_RE.match(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


# --------------------------------------------------------------------------
# Text rules
# --------------------------------------------------------------------------


def _strip_invisible(text: str) -> str:
    return "".join(
        ch for ch in text if unicodedata.category(ch) not in _INVISIBLE_CATEGORIES
    )


def _collapse_whitespace(text: str) -> str:
    return " ".join(text.split())


def _sanitise_term(term: str, forbidden: Set[str]) -> str:
    """Remove invisible and forbidden characters, then collapse whitespace."""
    kept = [
        ch
        for ch in term
        if unicodedata.category(ch) not in _INVISIBLE_CATEGORIES and ch not in forbidden
    ]
    return _collapse_whitespace("".join(kept))


def _clean_text(text: str) -> str:
    """Sanitise a row text cell and keep its first SCORING_TEXT_CHARS."""
    return _collapse_whitespace(_strip_invisible(text))[:SCORING_TEXT_CHARS]


def _csv_cell(text: str) -> str:
    cleaned = _clean_text(text)
    if cleaned and cleaned[0] in _FORMULA_LEADERS:
        return "'" + cleaned
    return cleaned


def _tokens(text: str) -> List[str]:
    """Case-folded maximal runs of letters and digits."""
    return _TOKEN_RE.findall(text.casefold())


def _ngrams(tokens: List[str]) -> Set[Tuple[str, ...]]:
    grams: Set[Tuple[str, ...]] = set()
    for start in range(len(tokens)):
        last = min(len(tokens), start + MAX_TERM_TOKENS)
        for end in range(start + 1, last + 1):
            grams.add(tuple(tokens[start:end]))
    return grams


def _phrase_in(grams: Set[Tuple[str, ...]], phrase: str) -> bool:
    tokens = tuple(_tokens(phrase))
    return 0 < len(tokens) <= MAX_TERM_TOKENS and tokens in grams


def _host_matches(host: str, suffix: str) -> bool:
    """Equality or a suffix match at a label boundary, both sides lower-cased."""
    host = host.lower()
    suffix = suffix.lower()
    return host == suffix or host.endswith("." + suffix)


def _host_in_suffixes(host: str, suffixes: List[str]) -> bool:
    return any(_host_matches(host, suffix) for suffix in suffixes)


def _is_hostname(host: str) -> bool:
    if not host or len(host) > 253:
        return False
    return all(_HOST_LABEL_RE.match(label) for label in host.lower().split("."))


# --------------------------------------------------------------------------
# Bundle reads
# --------------------------------------------------------------------------


def _bundle_path(*parts: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), *parts)


def _read_bundle_json(*parts: str) -> Any:
    with open(_bundle_path(*parts), "r", encoding="utf-8") as handle:
        return json.load(handle)


def _load_bundled_profile(platform: str) -> Any:
    return _read_bundle_json("kb", "platforms", BUNDLED_PROFILE_FILES[platform])


def _load_seniority_terms() -> Dict[str, List[str]]:
    data = _read_bundle_json("kb", SENIORITY_TERMS_FILE)
    return {level: list(data["levels"][level]) for level in SENIORITY_LEVELS}


# --------------------------------------------------------------------------
# Platform profile checks (bundled files and overrides share these)
# --------------------------------------------------------------------------


def _profile_fail(reason: str, path: str, **extra: Any) -> None:
    _fail(INVALID_PROFILE, reason, path=path, **extra)


def _expect_object(value: Any, path: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        _profile_fail("not_an_object", path)
    return value


def _expect_keys(
    obj: Dict[str, Any], required: Tuple[str, ...], optional: Tuple[str, ...], path: str
) -> None:
    for key in required:
        if key not in obj:
            _profile_fail("missing_key", f"{path}.{key}")
    allowed = set(required) | set(optional)
    for key in obj:
        if key not in allowed:
            _profile_fail("unknown_key", path)


def _expect_text(value: Any, path: str, max_chars: int = MAX_PROFILE_TEXT_CHARS) -> str:
    if not isinstance(value, str):
        _profile_fail("not_a_string", path)
    if not value:
        _profile_fail("empty_string", path)
    if len(value) > max_chars:
        _profile_fail("limit", path, limit=max_chars, actual=len(value))
    return value


def _expect_integer(value: Any, path: str, low: int, high: int) -> int:
    if not _is_int(value):
        _profile_fail("not_an_integer", path)
    if value < low or value > high:
        _profile_fail("out_of_range", path, limit=high, actual=value)
    return value


def _expect_date(value: Any, path: str) -> str:
    if not _is_date(value):
        _profile_fail("not_a_date", path)
    return value


def _expect_text_list(
    value: Any,
    path: str,
    min_entries: int,
    max_entries: int = MAX_PROFILE_LIST_ENTRIES,
    max_chars: int = MAX_PROFILE_TEXT_CHARS,
) -> List[str]:
    if not isinstance(value, list):
        _profile_fail("not_a_list", path)
    if len(value) < min_entries:
        _profile_fail("too_few_entries", path, limit=min_entries, actual=len(value))
    if len(value) > max_entries:
        _profile_fail("limit", path, limit=max_entries, actual=len(value))
    for index, item in enumerate(value):
        _expect_text(item, f"{path}[{index}]", max_chars)
    return value


def _check_base_url(base: Any, suffixes: List[str]) -> None:
    path = "search.url.base"
    _expect_text(base, path, MAX_BASE_URL_CHARS)
    try:
        parsed = urlsplit(base)
    except ValueError:
        _profile_fail("invalid_url", path)
    if parsed.scheme != "https":
        _profile_fail("scheme_not_https", path)
    if "@" in parsed.netloc or ":" in parsed.netloc:
        _profile_fail("user_info_or_port", path)
    if parsed.query or parsed.fragment or "?" in base or "#" in base:
        _profile_fail("invalid_url", path)
    if not _BASE_URL_CHARS_RE.match(base):
        _profile_fail("invalid_url", path)
    host = parsed.hostname or ""
    if not _is_hostname(host):
        _profile_fail("invalid_host", path)
    if not _host_in_suffixes(host, suffixes):
        _profile_fail("host_outside_profile_key", path)


def _check_grammar(grammar: Any) -> None:
    path = "search.grammar"
    _expect_object(grammar, path)
    _expect_keys(grammar, GRAMMAR_KEYS, (), path)
    for key in ("and", "or", "not"):
        _expect_text(grammar[key], f"{path}.{key}")
    for key in ("quote", "group_open", "group_close"):
        _expect_text(grammar[key], f"{path}.{key}")
        if len(grammar[key]) != 1:
            _profile_fail("not_a_single_character", f"{path}.{key}")
    forbidden = _expect_text_list(
        grammar["forbidden_characters"], f"{path}.forbidden_characters", 0
    )
    for index, item in enumerate(forbidden):
        if len(item) != 1:
            _profile_fail(
                "not_a_single_character", f"{path}.forbidden_characters[{index}]"
            )
    reserved = _expect_text_list(grammar["reserved_words"], f"{path}.reserved_words", 0)
    for key in ("quote", "group_open", "group_close"):
        if grammar[key] not in forbidden:
            _profile_fail("structural_character_not_forbidden", f"{path}.{key}")
    for key in ("and", "or", "not"):
        if grammar[key] not in reserved:
            _profile_fail("operator_not_reserved", f"{path}.{key}")


def _check_url_block(url: Any, suffixes: List[str]) -> List[str]:
    """Validate search.url and return every parameter name it declares."""
    path = "search.url"
    _expect_object(url, path)
    _expect_keys(url, URL_KEYS, (), path)
    _check_base_url(url["base"], suffixes)
    names = [_expect_text(url["keywords_param"], f"{path}.keywords_param")]
    if not _PARAM_NAME_RE.match(url["keywords_param"]):
        _profile_fail("invalid_parameter_name", f"{path}.keywords_param")
    fixed = url["fixed_params"]
    if not isinstance(fixed, list):
        _profile_fail("not_a_list", f"{path}.fixed_params")
    if len(fixed) > MAX_FIXED_PARAMS:
        _profile_fail(
            "limit", f"{path}.fixed_params", limit=MAX_FIXED_PARAMS, actual=len(fixed)
        )
    for index, entry in enumerate(fixed):
        entry_path = f"{path}.fixed_params[{index}]"
        _expect_object(entry, entry_path)
        _expect_keys(entry, FIXED_PARAM_KEYS, (), entry_path)
        _expect_text(entry["name"], f"{entry_path}.name")
        if not _PARAM_NAME_RE.match(entry["name"]):
            _profile_fail("invalid_parameter_name", f"{entry_path}.name")
        value = _expect_text(
            entry["value"], f"{entry_path}.value", MAX_FIXED_PARAM_VALUE_CHARS
        )
        if any(unicodedata.category(ch) in _INVISIBLE_CATEGORIES for ch in value):
            _profile_fail("control_character", f"{entry_path}.value")
        names.append(entry["name"])
    if url["filter_value_encoding"] != FILTER_VALUE_ENCODING:
        _profile_fail("unknown_encoding", f"{path}.filter_value_encoding")
    _expect_integer(
        url["max_length"], f"{path}.max_length", 1, MAX_SEARCH_URL_LENGTH_SETTING
    )
    return names


def _check_filters(filters: Any) -> List[str]:
    """Validate filters and return every filter_id, in fixed dimension order."""
    path = "filters"
    _expect_object(filters, path)
    for key in filters:
        if key not in FILTER_DIMENSIONS:
            _profile_fail("unknown_key", path)
    names: List[str] = []
    for dimension in FILTER_DIMENSIONS:
        if dimension not in filters:
            continue
        entry = filters[dimension]
        entry_path = f"{path}.{dimension}"
        _expect_object(entry, entry_path)
        _expect_keys(entry, FILTER_KEYS, (), entry_path)
        _expect_text(entry["filter_id"], f"{entry_path}.filter_id")
        if not _PARAM_NAME_RE.match(entry["filter_id"]):
            _profile_fail("invalid_parameter_name", f"{entry_path}.filter_id")
        values = entry["values"]
        if values is None:
            if dimension in PLATFORM_DEFINED_DIMENSIONS:
                _profile_fail("values_required", f"{entry_path}.values")
        else:
            minimum = 1 if dimension in PLATFORM_DEFINED_DIMENSIONS else 0
            _expect_text_list(values, f"{entry_path}.values", minimum)
        names.append(entry["filter_id"])
    return names


def _check_profile_key(block: Any) -> List[str]:
    path = "profile_key"
    _expect_object(block, path)
    _expect_keys(block, PROFILE_KEY_KEYS, (), path)
    suffixes = _expect_text_list(block["host_suffixes"], f"{path}.host_suffixes", 1)
    for index, suffix in enumerate(suffixes):
        if not _is_hostname(suffix):
            _profile_fail("invalid_host", f"{path}.host_suffixes[{index}]")
    prefix = _expect_text(block["path_prefix"], f"{path}.path_prefix")
    if not prefix.startswith("/") or not prefix.endswith("/"):
        _profile_fail("invalid_path_prefix", f"{path}.path_prefix")
    return suffixes


def _validate_profile(profile: Any, platform: str) -> Dict[str, Any]:
    """Run every profile check; the first failing rule is INVALID_PROFILE."""
    _expect_object(profile, "profile")
    _expect_keys(profile, PROFILE_KEYS, (), "profile")
    if not _is_int(profile["schema_version"]) or profile["schema_version"] != 1:
        _profile_fail("schema_version", "schema_version")
    _expect_text(profile["platform"], "platform")
    if not _PLATFORM_RE.match(profile["platform"]):
        _profile_fail("invalid_platform", "platform")
    _expect_date(profile["profile_version"], "profile_version")
    _expect_date(profile["verified_on"], "verified_on")

    sources = profile["sources"]
    if not isinstance(sources, list):
        _profile_fail("not_a_list", "sources")
    if not sources:
        _profile_fail("too_few_entries", "sources", limit=1, actual=0)
    if len(sources) > MAX_PROFILE_SOURCES:
        _profile_fail(
            "limit", "sources", limit=MAX_PROFILE_SOURCES, actual=len(sources)
        )
    for index, source in enumerate(sources):
        source_path = f"sources[{index}]"
        _expect_object(source, source_path)
        _expect_keys(source, SOURCE_KEYS, (), source_path)
        _expect_text(source["title"], f"{source_path}.title")
        _expect_text(source["reference"], f"{source_path}.reference")
        _expect_date(source["retrieved_on"], f"{source_path}.retrieved_on")

    # profile_key is checked before search.url so that the base host can be
    # matched against the suffixes.
    suffixes = _check_profile_key(profile["profile_key"])

    search = profile["search"]
    _expect_object(search, "search")
    _expect_keys(search, SEARCH_KEYS, (), "search")
    _expect_integer(
        search["per_query_result_cap"],
        "search.per_query_result_cap",
        1,
        MAX_PROFILE_INTEGER,
    )
    _expect_integer(
        search["max_query_length"], "search.max_query_length", 1, MAX_PROFILE_INTEGER
    )
    _check_grammar(search["grammar"])
    parameter_names = _check_url_block(search["url"], suffixes)
    parameter_names.extend(_check_filters(profile["filters"]))
    if len(set(parameter_names)) != len(parameter_names):
        _profile_fail("duplicate_parameter_name", "search.url")

    order = profile["partition_order"]
    if not isinstance(order, list):
        _profile_fail("not_a_list", "partition_order")
    for index, dimension in enumerate(order):
        item_path = f"partition_order[{index}]"
        if not isinstance(dimension, str) or dimension not in FILTER_DIMENSIONS:
            _profile_fail("unknown_dimension", item_path)
        if dimension not in profile["filters"]:
            _profile_fail("dimension_without_filter", item_path)
        if dimension in order[:index]:
            _profile_fail("duplicate_dimension", item_path)

    pacing = profile["pacing"]
    _expect_object(pacing, "pacing")
    _expect_keys(pacing, PROFILE_PACING_KEYS, (), "pacing")
    for key in PROFILE_PACING_KEYS:
        _expect_integer(pacing[key], f"pacing.{key}", 1, MAX_PROFILE_INTEGER)

    columns = profile["export_columns"]
    _expect_object(columns, "export_columns")
    _expect_keys(
        columns, EXPORT_COLUMNS_REQUIRED, EXPORT_COLUMNS_OPTIONAL, "export_columns"
    )
    names = []
    for role in EXPORT_COLUMNS_REQUIRED + EXPORT_COLUMNS_OPTIONAL:
        if role in columns:
            names.append(_expect_text(columns[role], f"export_columns.{role}"))
    if len(set(names)) != len(names):
        _profile_fail("duplicate_column", "export_columns")

    if profile["platform"] != platform:
        _profile_fail("platform_mismatch", "platform")
    return profile


def _resolve_profile(
    params: Dict[str, Any], platform: str
) -> Tuple[Dict[str, Any], str]:
    """Checks 6 to 8 of both actions: override, bundled id, bundled file."""
    if "profile_override" in params:
        try:
            profile = _validate_profile(params["profile_override"], platform)
        except _ContractError as error:
            error.detail["origin"] = PROFILE_ORIGIN_OVERRIDE
            raise
        return profile, PROFILE_ORIGIN_OVERRIDE
    if platform not in BUNDLED_PROFILE_FILES:
        _fail(UNKNOWN_PLATFORM, "unknown_platform", path="platform")
    try:
        profile = _validate_profile(_load_bundled_profile(platform), platform)
    except _ContractError as error:
        error.detail["origin"] = PROFILE_ORIGIN_BUNDLED
        raise
    return profile, PROFILE_ORIGIN_BUNDLED


# --------------------------------------------------------------------------
# Request envelope (checks 1 to 5)
# --------------------------------------------------------------------------


def _check_request_keys(params: Dict[str, Any], allowed: Tuple[str, ...]) -> None:
    for key in params:
        if key in allowed:
            continue
        if key in ALL_REQUEST_KEYS:
            _fail(INVALID_REQUEST, "unknown_key", path=key)
        _fail(INVALID_REQUEST, "unknown_key")


def _check_platform(params: Dict[str, Any]) -> str:
    platform = params.get("platform")
    if not isinstance(platform, str) or not _PLATFORM_RE.match(platform):
        _fail(INVALID_REQUEST, "invalid_platform", path="platform")
    return platform


def _check_override_type(params: Dict[str, Any]) -> None:
    if "profile_override" in params and not isinstance(
        params["profile_override"], dict
    ):
        _fail(INVALID_REQUEST, "invalid_type", path="profile_override")


# --------------------------------------------------------------------------
# compile_search
# --------------------------------------------------------------------------


def _spec_fail(reason: str, path: str, **extra: Any) -> None:
    _fail(INVALID_TARGET_SPEC, reason, path=path, **extra)


def _cap_fail(path: str, limit: int, actual: int) -> None:
    _fail(LIMIT_EXCEEDED, "size_cap", path=path, limit=limit, actual=actual)


def _dedupe(values: List[str]) -> List[str]:
    seen: Set[str] = set()
    kept: List[str] = []
    for value in values:
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        kept.append(value)
    return kept


def _validate_target_spec(
    spec: Dict[str, Any], profile: Dict[str, Any]
) -> Dict[str, Any]:
    """Checks 9 to 11; returns the normalised spec (sanitised, de-duplicated)."""
    # Check 9: shape.
    for key in spec:
        if key not in SPEC_KEYS:
            _spec_fail("unknown_key", "target_spec")
    if not _is_int(spec.get("schema_version")) or spec["schema_version"] != 1:
        _spec_fail("schema_version", "target_spec.schema_version")
    if "titles" not in spec:
        _spec_fail("missing_titles", "target_spec.titles")
    for field in SPEC_LIST_FIELDS:
        if field in spec and not _is_string_list(spec[field]):
            _spec_fail("not_a_string_list", f"target_spec.{field}")
    if "estimated_matches" in spec and not _is_int(spec["estimated_matches"]):
        _spec_fail("not_an_integer", "target_spec.estimated_matches")

    # Checks 10 and 11, field by field in the documented order; within a list
    # every rule of one item runs before the next item.
    forbidden = set(profile["search"]["grammar"]["forbidden_characters"])
    filters = profile["filters"]
    normalised: Dict[str, Any] = {}
    for field in SPEC_LIST_FIELDS:
        values = spec.get(field, [])
        path = f"target_spec.{field}"
        if len(values) > LIST_CAPS[field]:
            _cap_fail(path, LIST_CAPS[field], len(values))
        dimension = DIMENSION_FOR_SPEC_LIST.get(field)
        allowed_values: Optional[List[str]] = None
        if dimension in PLATFORM_DEFINED_DIMENSIONS and values:
            if dimension not in filters:
                _spec_fail("dimension_not_in_profile", path)
            allowed_values = filters[dimension]["values"]
        if dimension == "geo" and dimension in filters:
            allowed_values = filters[dimension]["values"]
        cleaned: List[str] = []
        for index, value in enumerate(values):
            item_path = f"{path}[{index}]"
            if field in TERM_LISTS:
                if len(value) > MAX_TERM_CHARS:
                    _cap_fail(item_path, MAX_TERM_CHARS, len(value))
                token_count = len(_tokens(value))
                if token_count > MAX_TERM_TOKENS:
                    _cap_fail(item_path, MAX_TERM_TOKENS, token_count)
                term = _sanitise_term(value, forbidden)
                if not value or not term:
                    _spec_fail("empty_term", item_path)
                cleaned.append(term)
                continue
            if field == "seniority":
                if value not in SENIORITY_LEVELS:
                    _spec_fail("unknown_seniority", item_path)
            elif field == "geos":
                if allowed_values is not None:
                    if value not in allowed_values:
                        _spec_fail("geo_not_in_profile", item_path)
                elif not _GEO_RE.match(value):
                    _spec_fail("invalid_geo", item_path)
            elif field == "languages":
                if not _LANGUAGE_RE.match(value):
                    _spec_fail("invalid_language", item_path)
            elif allowed_values is not None and value not in allowed_values:
                _spec_fail("value_not_in_profile", item_path)
            cleaned.append(value)
        normalised[field] = _dedupe(cleaned)
    estimated = spec.get("estimated_matches")
    if estimated is not None:
        if estimated < 0:
            _spec_fail("negative_estimated_matches", "target_spec.estimated_matches")
        if estimated > MAX_ESTIMATED_MATCHES:
            _cap_fail("target_spec.estimated_matches", MAX_ESTIMATED_MATCHES, estimated)
    normalised["estimated_matches"] = estimated
    return normalised


def _render_term(term: str, grammar: Dict[str, Any], reserved: Set[str]) -> str:
    if term.isalnum() and term.casefold() not in reserved:
        return term
    return grammar["quote"] + term + grammar["quote"]


def _keywords_string(spec: Dict[str, Any], grammar: Dict[str, Any]) -> str:
    reserved = {word.casefold() for word in grammar["reserved_words"]}
    titles = [_render_term(term, grammar, reserved) for term in spec["titles"]]
    joined = (" " + grammar["or"] + " ").join(titles)
    if len(titles) >= 2:
        joined = grammar["group_open"] + joined + grammar["group_close"]
    parts = [joined]
    for term in spec["keywords_include"]:
        parts.append(grammar["and"] + " " + _render_term(term, grammar, reserved))
    for term in spec["keywords_exclude"]:
        parts.append(grammar["not"] + " " + _render_term(term, grammar, reserved))
    return " ".join(parts)


def _cartesian(value_lists: List[List[str]]) -> List[Tuple[str, ...]]:
    """Cartesian product with the first list varying slowest."""
    combos: List[Tuple[str, ...]] = [()]
    for values in value_lists:
        combos = [combo + (value,) for combo in combos for value in values]
    return combos


def _partition(
    spec: Dict[str, Any], profile: Dict[str, Any]
) -> Tuple[List[Dict[str, Any]], List[str], bool]:
    """Return (queries without ids and URLs, expanded dimensions, cap_risk)."""
    cap = profile["search"]["per_query_result_cap"]
    candidates = [
        dimension
        for dimension in profile["partition_order"]
        if spec[SPEC_LIST_FOR_DIMENSION[dimension]]
    ]
    estimated = spec["estimated_matches"]
    expanded: List[str] = []
    cap_risk = False
    if estimated is None:
        for dimension in candidates:
            if len(spec[SPEC_LIST_FOR_DIMENSION[dimension]]) >= 2:
                expanded.append(dimension)
                break
    else:
        needed = max(1, -(-estimated // cap))
        product = 1
        for dimension in candidates:
            if product >= needed:
                break
            count = len(spec[SPEC_LIST_FOR_DIMENSION[dimension]])
            if count >= 2:
                expanded.append(dimension)
                product *= count
        cap_risk = product < needed

    combos = _cartesian([spec[SPEC_LIST_FOR_DIMENSION[d]] for d in expanded])
    queries: List[Dict[str, Any]] = []
    for combo in combos:
        partition = dict(zip(expanded, combo))
        filters: Dict[str, List[str]] = {}
        for dimension in FILTER_DIMENSIONS:
            values = spec[SPEC_LIST_FOR_DIMENSION[dimension]]
            if dimension not in profile["filters"] or not values:
                continue
            if dimension in partition:
                filters[dimension] = [partition[dimension]]
            else:
                filters[dimension] = list(values)
        queries.append({"partition": partition, "filters": filters})
    return queries, expanded, cap_risk


def _search_url(
    keywords: str, filters: Dict[str, List[str]], profile: Dict[str, Any]
) -> str:
    url = profile["search"]["url"]
    parts = [quote(url["keywords_param"], safe="") + "=" + quote(keywords, safe="")]
    for entry in url["fixed_params"]:
        parts.append(
            quote(entry["name"], safe="") + "=" + quote(entry["value"], safe="")
        )
    for dimension in FILTER_DIMENSIONS:
        if dimension not in filters:
            continue
        name = profile["filters"][dimension]["filter_id"]
        encoded = json.dumps(
            filters[dimension], separators=(",", ":"), ensure_ascii=True
        )
        parts.append(quote(name, safe="") + "=" + quote(encoded, safe=""))
    return url["base"] + "?" + "&".join(parts)


def _plan_id(body: Dict[str, Any]) -> str:
    serialised = json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return "plan_" + hashlib.sha256(serialised.encode("utf-8")).hexdigest()[:12]


def _build_plan(
    spec: Dict[str, Any], profile: Dict[str, Any], origin: str
) -> Dict[str, Any]:
    grammar = profile["search"]["grammar"]
    search = profile["search"]
    keywords = _keywords_string(spec, grammar)
    partial, expanded, cap_risk = _partition(spec, profile)
    # Check 12.
    if len(partial) > MAX_QUERIES:
        _cap_fail("plan.queries", MAX_QUERIES, len(partial))
    if len(keywords) > search["max_query_length"]:
        _cap_fail("plan.queries[].keywords", search["max_query_length"], len(keywords))
    queries: List[Dict[str, Any]] = []
    for index, entry in enumerate(partial):
        url = _search_url(keywords, entry["filters"], profile)
        if len(url) > search["url"]["max_length"]:
            _cap_fail(
                f"plan.queries[{index}].search_url",
                search["url"]["max_length"],
                len(url),
            )
        queries.append(
            {
                "id": "q" + str(index + 1).zfill(2),
                "partition": entry["partition"],
                "keywords": keywords,
                "filters": entry["filters"],
                "expected_cap": search["per_query_result_cap"],
                "search_url": url,
            }
        )
    body = {
        "platform": profile["platform"],
        "profile_version": profile["profile_version"],
        "profile_origin": origin,
        "queries": queries,
        "pacing": {
            "per_run_max_queries": profile["pacing"]["per_run_max_queries"],
            "per_day_max_rows": profile["pacing"]["per_day_max_rows"],
            "source": PACING_SOURCE,
        },
        "rubric": dict(RUBRIC),
        "criteria": {field: list(spec[field]) for field in CRITERIA_FIELDS},
        "partitioning": {
            "dimensions": list(expanded),
            "estimated_matches": spec["estimated_matches"],
            "cap_risk": cap_risk,
        },
    }
    plan = {"plan_id": _plan_id(body)}
    plan.update(body)
    return plan


def _compile_search(params: Dict[str, Any]) -> Dict[str, Any]:
    _check_request_keys(params, COMPILE_REQUEST_KEYS)
    platform = _check_platform(params)
    if not isinstance(params.get("target_spec"), dict):
        _fail(INVALID_REQUEST, "invalid_type", path="target_spec")
    _check_override_type(params)
    profile, origin = _resolve_profile(params, platform)
    spec = _validate_target_spec(params["target_spec"], profile)
    return {"status": "completed", "plan": _build_plan(spec, profile, origin)}


# --------------------------------------------------------------------------
# ingest_leads
# --------------------------------------------------------------------------


def _plan_fail(reason: str, path: str) -> None:
    _fail(INVALID_PLAN, reason, path=path)


def _check_exact_keys(
    obj: Any, keys: Tuple[str, ...], path: str, reason_prefix: str
) -> None:
    if not isinstance(obj, dict):
        _plan_fail(reason_prefix + "not_an_object", path)
    for key in keys:
        if key not in obj:
            _plan_fail(reason_prefix + "missing_key", f"{path}.{key}")
    for key in obj:
        if key not in keys:
            _plan_fail(reason_prefix + "unknown_key", path)


def _validate_plan_shape(plan: Dict[str, Any]) -> None:
    """Check 9: the key sets and types of the plan compile_search returned."""
    _check_exact_keys(plan, PLAN_KEYS, "plan", "")
    for key in ("plan_id", "platform", "profile_version"):
        if not isinstance(plan[key], str):
            _plan_fail("not_a_string", f"plan.{key}")
    if plan["profile_origin"] not in PROFILE_ORIGINS:
        _plan_fail("unknown_profile_origin", "plan.profile_origin")
    if not isinstance(plan["queries"], list):
        _plan_fail("not_a_list", "plan.queries")
    for index, query in enumerate(plan["queries"]):
        path = f"plan.queries[{index}]"
        _check_exact_keys(query, QUERY_KEYS, path, "")
        for key in ("id", "keywords", "search_url"):
            if not isinstance(query[key], str):
                _plan_fail("not_a_string", f"{path}.{key}")
        if not _is_int(query["expected_cap"]):
            _plan_fail("not_an_integer", f"{path}.expected_cap")
        partition = query["partition"]
        if not isinstance(partition, dict):
            _plan_fail("not_an_object", f"{path}.partition")
        for key, value in partition.items():
            if key not in FILTER_DIMENSIONS or not isinstance(value, str):
                _plan_fail("invalid_partition", f"{path}.partition")
        filters = query["filters"]
        if not isinstance(filters, dict):
            _plan_fail("not_an_object", f"{path}.filters")
        for key, value in filters.items():
            if key not in FILTER_DIMENSIONS or not _is_string_list(value):
                _plan_fail("invalid_filters", f"{path}.filters")
    _check_exact_keys(plan["pacing"], PACING_KEYS, "plan.pacing", "")
    for key in PROFILE_PACING_KEYS:
        if not _is_int(plan["pacing"][key]):
            _plan_fail("not_an_integer", f"plan.pacing.{key}")
    if plan["pacing"]["source"] != PACING_SOURCE:
        _plan_fail("unknown_pacing_source", "plan.pacing.source")
    _check_exact_keys(plan["rubric"], RUBRIC_KEYS, "plan.rubric", "")
    for key in RUBRIC_KEYS:
        if not _is_int(plan["rubric"][key]) or plan["rubric"][key] != RUBRIC[key]:
            _plan_fail("rubric_mismatch", f"plan.rubric.{key}")
    _check_exact_keys(plan["criteria"], CRITERIA_FIELDS, "plan.criteria", "")
    for field in CRITERIA_FIELDS:
        if not _is_string_list(plan["criteria"][field]):
            _plan_fail("not_a_string_list", f"plan.criteria.{field}")
    partitioning = plan["partitioning"]
    _check_exact_keys(partitioning, PARTITIONING_KEYS, "plan.partitioning", "")
    if not _is_string_list(partitioning["dimensions"]):
        _plan_fail("not_a_string_list", "plan.partitioning.dimensions")
    estimated = partitioning["estimated_matches"]
    if estimated is not None and not _is_int(estimated):
        _plan_fail("not_an_integer", "plan.partitioning.estimated_matches")
    if not isinstance(partitioning["cap_risk"], bool):
        _plan_fail("not_a_boolean", "plan.partitioning.cap_risk")


def _check_plan_matches(
    plan: Dict[str, Any], profile: Dict[str, Any], origin: str
) -> None:
    """Check 11: the plan is consistent with the resolved profile and its id."""
    if plan["platform"] != profile["platform"]:
        _fail(PLAN_MISMATCH, "platform_mismatch", path="plan.platform")
    if plan["profile_version"] != profile["profile_version"]:
        _fail(PLAN_MISMATCH, "profile_version_mismatch", path="plan.profile_version")
    if plan["profile_origin"] != origin:
        _fail(PLAN_MISMATCH, "profile_origin_mismatch", path="plan.profile_origin")
    body = {key: plan[key] for key in PLAN_KEYS if key != "plan_id"}
    if _plan_id(body) != plan["plan_id"]:
        _fail(PLAN_MISMATCH, "plan_id_mismatch", path="plan.plan_id")


def _validate_existing_leads(existing: List[Any]) -> int:
    """Check 13; returns the highest numbered lead id of the form L<digits>."""
    highest = 0
    for index, lead in enumerate(existing):
        path = f"existing_leads[{index}]"
        if not isinstance(lead, dict):
            _fail(INVALID_EXISTING_LEADS, "not_an_object", path=path)
        for key in EXISTING_LEAD_KEYS:
            if not isinstance(lead.get(key), str):
                _fail(
                    INVALID_EXISTING_LEADS,
                    "missing_or_not_a_string",
                    path=f"{path}.{key}",
                )
        for key in lead:
            if key not in EXISTING_LEAD_KEYS:
                _fail(INVALID_EXISTING_LEADS, "unknown_key", path=path)
        match = _LEAD_ID_RE.match(lead["lead_id"])
        if match:
            highest = max(highest, int(match.group(1)))
    return highest


def _profile_key(url: str, profile: Dict[str, Any]) -> Optional[str]:
    """The platform id plus the lower-cased slug, or None when it cannot be derived."""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    try:
        host = parsed.hostname or ""
    except ValueError:
        return None
    if not host or not _host_in_suffixes(host, profile["profile_key"]["host_suffixes"]):
        return None
    prefix = profile["profile_key"]["path_prefix"]
    if not parsed.path.startswith(prefix):
        return None
    slug = parsed.path[len(prefix) :].split("/", 1)[0].lower()
    if not _SLUG_RE.match(slug):
        return None
    return profile["platform"] + ":" + slug


def _validate_rows(
    rows: List[Any], profile: Dict[str, Any], plan: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Checks 15 to 18, row by row; returns the normalised rows."""
    columns = profile["export_columns"]
    lookup: Dict[str, Dict[str, Any]] = {}
    for query in plan["queries"]:
        lookup.setdefault(query["id"], query)
        lookup.setdefault(query["search_url"], query)
    normalised: List[Dict[str, Any]] = []
    total_chars = 0
    for index, row in enumerate(rows):
        path = f"rows[{index}]"
        if not isinstance(row, dict):
            _fail(INVALID_ROWS, "row_not_object", path=path, row=index)
        if len(row) > MAX_ROW_KEYS:
            _cap_fail(path, MAX_ROW_KEYS, len(row))
        for value in row.values():
            if not isinstance(value, str):
                continue
            if len(value) > MAX_CELL_CHARS:
                _cap_fail(path, MAX_CELL_CHARS, len(value))
            total_chars += len(value)
            if total_chars > MAX_TOTAL_CELL_CHARS:
                _cap_fail("rows", MAX_TOTAL_CELL_CHARS, total_chars)
        cells: Dict[str, str] = {}
        for role in EXPORT_COLUMNS_REQUIRED:
            column = columns[role]
            if column not in row:
                _fail(INVALID_ROWS, "missing_column", path=path, row=index, column=role)
            if not isinstance(row[column], str):
                _fail(
                    INVALID_ROWS, "column_not_string", path=path, row=index, column=role
                )
            cells[role] = row[column]
        for role in EXPORT_COLUMNS_OPTIONAL:
            column = columns.get(role)
            if column is None or column not in row:
                cells[role] = ""
                continue
            if not isinstance(row[column], str):
                _fail(
                    INVALID_ROWS, "column_not_string", path=path, row=index, column=role
                )
            cells[role] = row[column]
        key = _profile_key(cells["profile_url"], profile)
        if key is None:
            _fail(INVALID_ROWS, "invalid_profile_url", path=path, row=index)
        query = lookup.get(cells["source_query"])
        if query is None:
            _fail(UNKNOWN_SOURCE_QUERY, "unknown_source_query", path=path, row=index)
        normalised.append(
            {
                "profile_key": key,
                "title": cells["title"],
                "company": cells["company"],
                "location": cells["location"],
                "query": query,
            }
        )
    return normalised


def _score_row(
    row: Dict[str, Any],
    criteria: Dict[str, List[str]],
    seniority_terms: Dict[str, List[str]],
) -> Tuple[int, List[str], bool]:
    title_grams = _ngrams(_tokens(_clean_text(row["title"])))
    company_grams = _ngrams(_tokens(_clean_text(row["company"])))
    filters = row["query"]["filters"]

    hits = [
        term for term in criteria["keywords_exclude"] if _phrase_in(title_grams, term)
    ]
    if hits:
        return 0, [EXCLUSION_REASON_PREFIX + term for term in hits], True

    awarded: Dict[str, bool] = {}
    awarded["title_match"] = any(_phrase_in(title_grams, t) for t in criteria["titles"])
    if "seniority" in filters:
        awarded["seniority"] = True
    else:
        awarded["seniority"] = any(
            _phrase_in(title_grams, term)
            for level in criteria["seniority"]
            for term in seniority_terms[level]
        )
    if "industry" in filters:
        awarded["industry"] = True
    else:
        awarded["industry"] = any(
            _phrase_in(company_grams, t) for t in criteria["industries"]
        )
    awarded["geo"] = "geo" in filters
    awarded["language"] = "language" in filters

    reasons = [name for name in SCORED_COMPONENTS if awarded[name]]
    score = sum(RUBRIC[name] for name in reasons)
    return score, reasons, score < RUBRIC["off_target_below"]


def _render_review_table(rows: List[List[Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
    writer.writerow(REVIEW_COLUMNS)
    for row in rows:
        writer.writerow(row)
    text = buffer.getvalue()
    if text.endswith("\n"):
        text = text[:-1]
    return text


def _ingest_leads(params: Dict[str, Any]) -> Dict[str, Any]:
    _check_request_keys(params, INGEST_REQUEST_KEYS)
    platform = _check_platform(params)
    if not isinstance(params.get("plan"), dict):
        _fail(INVALID_REQUEST, "invalid_type", path="plan")
    if not isinstance(params.get("rows"), list):
        _fail(INVALID_REQUEST, "invalid_type", path="rows")
    existing = params.get("existing_leads", [])
    if not isinstance(existing, list):
        _fail(INVALID_REQUEST, "invalid_type", path="existing_leads")
    _check_override_type(params)
    profile, origin = _resolve_profile(params, platform)
    plan = params["plan"]
    _validate_plan_shape(plan)
    if len(plan["queries"]) > MAX_QUERIES:
        _cap_fail("plan.queries", MAX_QUERIES, len(plan["queries"]))
    _check_plan_matches(plan, profile, origin)
    if len(existing) > MAX_EXISTING_LEADS:
        _cap_fail("existing_leads", MAX_EXISTING_LEADS, len(existing))
    highest = _validate_existing_leads(existing)
    rows = params["rows"]
    if len(rows) > MAX_ROWS:
        _cap_fail("rows", MAX_ROWS, len(rows))
    normalised = _validate_rows(rows, profile, plan)

    seniority_terms = _load_seniority_terms()
    known: Set[str] = {lead["profile_key"] for lead in existing}
    leads: List[Dict[str, Any]] = []
    table: List[List[Any]] = []
    deduped = 0
    off_target_count = 0
    for row in normalised:
        key = row["profile_key"]
        if key in known:
            deduped += 1
            continue
        known.add(key)
        score, reasons, off_target = _score_row(row, plan["criteria"], seniority_terms)
        lead_id = "L" + str(highest + len(leads) + 1).zfill(4)
        query_id = row["query"]["id"]
        leads.append(
            {
                "lead_id": lead_id,
                "profile_key": key,
                "score": score,
                "reasons": reasons,
                "off_target": off_target,
                "stage": STAGE_NEW,
                "source_query": query_id,
            }
        )
        table.append(
            [
                lead_id,
                score,
                "true" if off_target else "false",
                _csv_cell(row["title"]),
                _csv_cell(row["company"]),
                _csv_cell(row["location"]),
                ";".join(reasons),
                query_id,
            ]
        )
        if off_target:
            off_target_count += 1
    return {
        "status": "completed",
        "leads": leads,
        "review_table": {
            "columns": list(REVIEW_COLUMNS),
            "csv": _render_review_table(table),
        },
        "stats": {
            "rows_in": len(rows),
            "deduped": deduped,
            "off_target": off_target_count,
            "kept": len(leads) - off_target_count,
        },
    }


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def _dispatch(params: Any) -> Dict[str, Any]:
    if not isinstance(params, dict):
        _fail(INVALID_REQUEST, "parameters_not_object")
    action = params.get("action")
    if not isinstance(action, str) or action not in ACTIONS:
        _fail(INVALID_REQUEST, "unknown_action", path="action")
    if action == ACTION_COMPILE_SEARCH:
        return _compile_search(params)
    return _ingest_leads(params)


class OutreachManagerSkill(BaseSkill):
    """Deterministic search planning and lead ingestion over operator inputs."""

    @property
    def manifest(self) -> Dict[str, Any]:
        manifest_path = _bundle_path("manifest.yaml")
        if os.path.exists(manifest_path):
            with open(manifest_path, "r", encoding="utf-8") as handle:
                return yaml.safe_load(handle)
        return {"name": REGISTRY_ID, "version": "0.1.0"}

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
