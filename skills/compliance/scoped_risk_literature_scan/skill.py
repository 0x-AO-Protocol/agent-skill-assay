"""Scoped risk literature scan: screen a collection report, check its annotations.

Two actions share one validated request:

  screen  - screens every item the caller collected against the cycle
            window, the jurisdictions and the risk domains, drops repeats,
            orders and caps the rest, flags client relevance, tallies the
            in-scope items and returns one annotation slot per kept item.
  compile - runs the same screen, checks that the caller annotated exactly
            the kept items within the caps, credits attested minutes and
            returns the review record for the caller to store.

The caller collects the items and writes every summary and impact note; the
skill reads only the request and never judges what an annotation says. It
makes no model call and uses no network, clock, file or environment. Every
number is an integer. A contract violation returns an error envelope with a
code from the closed registry below; an unexpected internal fault returns the
one-code fault registry entry INTERNAL_ERROR.
"""

import re
from datetime import date
from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple

from skill_assay.core.base_skill import BaseSkill

REGISTRY_ID = "compliance/scoped_risk_literature_scan"
VERSION = "0.1.0"

ACTION_SCREEN = "screen"
ACTION_COMPILE = "compile"
ACTIONS = (ACTION_SCREEN, ACTION_COMPILE)

# --------------------------------------------------------------------------
# Closed registries
# --------------------------------------------------------------------------

INVALID_REQUEST = "INVALID_REQUEST"
INVALID_DATE = "INVALID_DATE"
EMPTY_SCOPE = "EMPTY_SCOPE"
WINDOW_INVALID = "WINDOW_INVALID"
DUPLICATE_KEY = "DUPLICATE_KEY"
UNKNOWN_SOURCE = "UNKNOWN_SOURCE"
SOURCE_NOT_REPORTED = "SOURCE_NOT_REPORTED"
SOURCE_UNREACHABLE = "SOURCE_UNREACHABLE"
UNKNOWN_ITEM_REF = "UNKNOWN_ITEM_REF"
ANNOTATION_MISSING = "ANNOTATION_MISSING"
INTERNAL_ERROR = "INTERNAL_ERROR"

ERROR_REGISTRY = {
    INVALID_REQUEST: (
        "parameters_not_object",
        "unknown_action",
        "unknown_key",
        "missing_field",
        "invalid_type",
        "invalid_value",
        "out_of_range",
        "size_cap",
        "field_not_allowed",
        "unknown_domain",
    ),
    INVALID_DATE: ("invalid_date",),
    EMPTY_SCOPE: ("empty_scope",),
    WINDOW_INVALID: ("end_before_start", "window_too_long"),
    DUPLICATE_KEY: ("duplicate_key",),
    UNKNOWN_SOURCE: ("unknown_source",),
    SOURCE_NOT_REPORTED: ("source_not_reported",),
    SOURCE_UNREACHABLE: ("source_unreachable",),
    UNKNOWN_ITEM_REF: ("unknown_item_ref",),
    ANNOTATION_MISSING: ("annotation_missing",),
}
FAULT_REGISTRY = {INTERNAL_ERROR: ("internal_error",)}

# --------------------------------------------------------------------------
# Enums, caps and patterns
# --------------------------------------------------------------------------

SOURCE_KINDS = ("literature", "regulatory", "technology", "economic")
REPORT_STATUSES = ("ok", "unreachable")
IMPACT_AREAS = ("exposures", "methods", "both", "none")
LIKELIHOODS = ("low", "medium", "high")
EXCLUSION_REASONS = (
    "out_of_window",
    "out_of_jurisdiction",
    "out_of_domain",
    "duplicate",
    "over_cap",
)

MAX_WINDOW_DAYS = 92
MAX_ITEMS_RANGE = (1, 200)
MINUTES_RANGE = (0, 1440)
PREVIOUS_COUNT_RANGE = (0, 100000)
MAX_RISK_DOMAINS = 20
MAX_JURISDICTIONS = 50
MAX_SOURCES = 50
MAX_CLIENT_TAGS = 50
MAX_ITEMS_PER_REPORT = 500
MAX_ITEMS_IN_ALL = 2000
MAX_ITEM_DOMAINS = 10
MAX_ITEM_TAGS = 20
MAX_ANNOTATIONS = 200
MAX_TITLE = 300
MAX_SUMMARY = 2000
MAX_IMPACT_NOTE = 1000
MAX_LINK = 512
MAX_LOG_DESTINATION = 512

_DOMAIN_RE = re.compile("[a-z][a-z0-9_]{1,63}", re.ASCII)
_JURISDICTION_RE = re.compile("[A-Z][A-Z0-9-]{1,7}", re.ASCII)
_SOURCE_NAME_RE = re.compile("[A-Za-z0-9_-]{1,64}", re.ASCII)
_LINK_RE = re.compile("https://[A-Za-z0-9./_-]*", re.ASCII)
_DATE_RE = re.compile("([0-9]{4})-([0-9]{2})-([0-9]{2})", re.ASCII)
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]", re.ASCII)
_SURROGATE_RE = re.compile(r"[\ud800-\udfff]", re.ASCII)
_SPACE_RUN_RE = re.compile(" +", re.ASCII)
_ASCII_FOLD = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")

# --------------------------------------------------------------------------
# The request shape
# --------------------------------------------------------------------------

TOP_LEVEL_TYPES = (
    ("action", "string"),
    ("cycle_start", "string"),
    ("cycle_end", "string"),
    ("risk_domains", "array"),
    ("jurisdictions", "array"),
    ("sources", "array"),
    ("max_items", "integer"),
    ("client_exposure_tags", "array"),
    ("previous_tallies", "object"),
    ("collection", "array"),
    ("log_destination", "string"),
    ("attestation", "object"),
    ("annotations", "array"),
)
TOP_LEVEL_FIELDS = tuple(name for name, _ in TOP_LEVEL_TYPES)
ALWAYS_REQUIRED = (
    "cycle_start",
    "cycle_end",
    "risk_domains",
    "jurisdictions",
    "sources",
    "max_items",
    "collection",
)
COMPILE_ONLY = ("attestation", "annotations")

PARAMETERS = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": list(ACTIONS),
            "description": (
                "screen returns the screened items, the counts, the tallies and "
                "one annotation slot per kept item; compile runs the same screen, "
                "checks the caller's annotations and returns the review record."
            ),
        },
        "cycle_start": {
            "type": "string",
            "description": (
                "Required. First day of the review cycle as YYYY-MM-DD; no clock "
                "is read."
            ),
        },
        "cycle_end": {
            "type": "string",
            "description": (
                "Required. Last day of the review cycle as YYYY-MM-DD, not before "
                "cycle_start; the window holds 1 to 92 days, both ends included."
            ),
        },
        "risk_domains": {
            "type": "array",
            "description": (
                "Required. 1 to 20 unique risk domains that bound the scan; the "
                "whole of each must match [a-z][a-z0-9_]{1,63}."
            ),
        },
        "jurisdictions": {
            "type": "array",
            "description": (
                "Required. 1 to 50 unique jurisdiction codes that bound the scan; "
                "the whole of each must match [A-Z][A-Z0-9-]{1,7}."
            ),
        },
        "sources": {
            "type": "array",
            "description": (
                "Required. 1 to 50 configured sources, each with a unique name, a "
                "link that is never opened (at most 512 characters; the whole link "
                "must match https://[A-Za-z0-9./_-]*) and a kind: literature, "
                "regulatory, technology or economic."
            ),
        },
        "max_items": {
            "type": "integer",
            "description": (
                "Required. The item cap, 1 to 200; in-scope items after the cap "
                "are counted as over_cap."
            ),
        },
        "client_exposure_tags": {
            "type": "array",
            "description": (
                "Optional. Up to 50 unique tags; an item whose exposure tags "
                "include one of them is relevant to clients and gets a follow-up "
                "date."
            ),
        },
        "previous_tallies": {
            "type": "object",
            "description": (
                "Optional. by_domain maps scope domains to the previous cycle's "
                "counts (0 to 100000); a missing count is 0."
            ),
        },
        "collection": {
            "type": "array",
            "description": (
                "Required. Exactly one report per configured source: "
                "source_name, status (ok or unreachable) and items, each with "
                "title, published_date, jurisdiction, domains and optional "
                "exposure_tags and url; at most 500 items per report and 2000 in "
                "all."
            ),
        },
        "log_destination": {
            "type": "string",
            "description": (
                "Optional. Echoed in the compile record; never opened or written."
            ),
        },
        "attestation": {
            "type": "object",
            "description": (
                "compile only, optional. attested (boolean) and minutes (0 to "
                "1440); the minutes are credited only when attested is true."
            ),
        },
        "annotations": {
            "type": "array",
            "description": (
                "compile only, and required there. One annotation per kept item: "
                "item_ref, summary (up to 2000 characters) and impact with area, "
                "likelihood and note (up to 1000 characters)."
            ),
        },
    },
    "required": ["action"],
    "additionalProperties": False,
}

MANIFEST = {"name": REGISTRY_ID, "version": VERSION, "parameters": PARAMETERS}


class _ContractError(Exception):
    """Internal signal carrying a closed-registry code and a detail object."""

    def __init__(self, code: str, detail: Dict[str, Any]):
        super().__init__(code)
        self.code = code
        self.detail = detail


def _fail(code: str, reason: str, path: str, **extra: Any) -> None:
    detail: Dict[str, Any] = {"reason": reason, "path": path}
    detail.update(extra)
    raise _ContractError(code, detail)


def _invalid(reason: str, path: str, **extra: Any) -> None:
    _fail(INVALID_REQUEST, reason, path, **extra)


def _key(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def _index(path: str, position: int) -> str:
    return f"{path}[{position}]"


# --------------------------------------------------------------------------
# Field rules
# --------------------------------------------------------------------------


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _has_type(value: Any, kind: str) -> bool:
    if kind == "string":
        return isinstance(value, str)
    if kind == "array":
        return isinstance(value, list)
    if kind == "object":
        return isinstance(value, dict)
    return _is_int(value)


def _check_text(value: Any, path: str, cap: Optional[int] = None) -> str:
    """A text: a string within its cap, no surrogate, no control, not blank."""
    if not isinstance(value, str):
        _invalid("invalid_type", path)
    if cap is not None and len(value) > cap:
        _invalid("size_cap", path, limit=cap)
    if _SURROGATE_RE.search(value) or _CONTROL_RE.search(value):
        _invalid("invalid_value", path)
    if value.strip(" \n") == "":
        _invalid("invalid_value", path)
    return value


def _check_pattern(value: Any, pattern: Any, path: str) -> str:
    if not isinstance(value, str):
        _invalid("invalid_type", path)
    if not pattern.fullmatch(value):
        _invalid("invalid_value", path)
    return value


def _check_link(value: Any, path: str) -> str:
    if not isinstance(value, str):
        _invalid("invalid_type", path)
    if len(value) > MAX_LINK:
        _invalid("size_cap", path, limit=MAX_LINK)
    return _check_pattern(value, _LINK_RE, path)


def _check_enum(value: Any, allowed: Tuple[str, ...], path: str) -> str:
    if not isinstance(value, str):
        _invalid("invalid_type", path)
    if value not in allowed:
        _invalid("invalid_value", path)
    return value


def _check_int(value: Any, bounds: Tuple[int, int], path: str) -> int:
    if not _is_int(value):
        _invalid("invalid_type", path)
    if value < bounds[0] or value > bounds[1]:
        _invalid("out_of_range", path)
    return value


def _check_object(value: Any, fields: Tuple[Tuple[str, bool], ...], path: str):
    """An object with only the named keys and every required one present."""
    if not isinstance(value, dict):
        _invalid("invalid_type", path)
    allowed = [name for name, _ in fields]
    for name in value:
        if name not in allowed:
            _invalid("unknown_key", path)
    for name, required in fields:
        if required and name not in value:
            _invalid("missing_field", _key(path, name))
    return value


def _check_list(value: Any, path: str, minimum: int = 0) -> List[Any]:
    if not isinstance(value, list):
        _invalid("invalid_type", path)
    if len(value) < minimum:
        _invalid("out_of_range", path)
    return value


def _check_codes(values: Any, pattern: Any, path: str, minimum: int = 0) -> None:
    for position, value in enumerate(_check_list(values, path, minimum)):
        _check_pattern(value, pattern, _index(path, position))


def _check_date_text(value: Any, path: str) -> None:
    """A date field is a string; the date check reads its grammar and calendar.

    Any other character, a lone surrogate included, fails that check.
    """
    if not isinstance(value, str):
        _invalid("invalid_type", path)


# --------------------------------------------------------------------------
# Validation, in the documented check order
# --------------------------------------------------------------------------

SOURCE_FIELDS = (("name", True), ("url", True), ("kind", True))
REPORT_FIELDS = (("source_name", True), ("status", True), ("items", True))
ITEM_FIELDS = (
    ("title", True),
    ("published_date", True),
    ("jurisdiction", True),
    ("domains", True),
    ("exposure_tags", False),
    ("url", False),
)
ATTESTATION_FIELDS = (("attested", True), ("minutes", True))
ANNOTATION_FIELDS = (("item_ref", True), ("summary", True), ("impact", True))
IMPACT_FIELDS = (("area", True), ("likelihood", True), ("note", True))
PREVIOUS_FIELDS = (("by_domain", True),)


def _check_top_level(params: Dict[str, Any], action: str) -> None:
    """Unknown top-level keys, then required fields, then top-level types."""
    for name in params:
        if name not in TOP_LEVEL_FIELDS:
            _invalid("unknown_key", "")
    required = ALWAYS_REQUIRED + (("annotations",) if action == ACTION_COMPILE else ())
    for name in TOP_LEVEL_FIELDS:
        if name in required and name not in params:
            _invalid("missing_field", name)
    for name, kind in TOP_LEVEL_TYPES:
        if name in params and not _has_type(params[name], kind):
            _invalid("invalid_type", name)


def _check_empty_scope(params: Dict[str, Any]) -> None:
    for name in ("risk_domains", "jurisdictions"):
        if not params[name]:
            _fail(EMPTY_SCOPE, "empty_scope", name)


def _cap(values: Any, limit: int, path: str) -> None:
    if isinstance(values, list) and len(values) > limit:
        _invalid("size_cap", path, limit=limit)


def _report_items(report: Any) -> List[Any]:
    items = report.get("items") if isinstance(report, dict) else None
    return items if isinstance(items, list) else []


def _check_list_caps(params: Dict[str, Any]) -> None:
    """Every list-length cap, before any item is read."""
    _cap(params["risk_domains"], MAX_RISK_DOMAINS, "risk_domains")
    _cap(params["jurisdictions"], MAX_JURISDICTIONS, "jurisdictions")
    _cap(params["sources"], MAX_SOURCES, "sources")
    _cap(params.get("client_exposure_tags"), MAX_CLIENT_TAGS, "client_exposure_tags")
    reports = params["collection"]
    for position, report in enumerate(reports):
        path = _index("collection", position) + ".items"
        _cap(_report_items(report), MAX_ITEMS_PER_REPORT, path)
    if sum(len(_report_items(report)) for report in reports) > MAX_ITEMS_IN_ALL:
        _invalid("size_cap", "collection", limit=MAX_ITEMS_IN_ALL)
    for position, report in enumerate(reports):
        for number, entry in enumerate(_report_items(report)):
            if isinstance(entry, dict):
                path = _index(_index("collection", position) + ".items", number)
                _cap(entry.get("domains"), MAX_ITEM_DOMAINS, path + ".domains")
                _cap(entry.get("exposure_tags"), MAX_ITEM_TAGS, path + ".exposure_tags")
    _cap(params.get("annotations"), MAX_ANNOTATIONS, "annotations")


def _check_source(source: Any, path: str) -> None:
    _check_object(source, SOURCE_FIELDS, path)
    _check_pattern(source["name"], _SOURCE_NAME_RE, path + ".name")
    _check_link(source["url"], path + ".url")
    _check_enum(source["kind"], SOURCE_KINDS, path + ".kind")


def _check_previous(previous: Dict[str, Any]) -> None:
    _check_object(previous, PREVIOUS_FIELDS, "previous_tallies")
    path = "previous_tallies.by_domain"
    counts = previous["by_domain"]
    if not isinstance(counts, dict):
        _invalid("invalid_type", path)
    for count in counts.values():
        _check_int(count, PREVIOUS_COUNT_RANGE, path)


def _check_item(entry: Any, path: str) -> None:
    _check_object(entry, ITEM_FIELDS, path)
    _check_text(entry["title"], path + ".title", MAX_TITLE)
    _check_date_text(entry["published_date"], path + ".published_date")
    _check_pattern(entry["jurisdiction"], _JURISDICTION_RE, path + ".jurisdiction")
    _check_codes(entry["domains"], _DOMAIN_RE, path + ".domains", minimum=1)
    if "exposure_tags" in entry:
        _check_codes(entry["exposure_tags"], _DOMAIN_RE, path + ".exposure_tags")
    if "url" in entry:
        _check_link(entry["url"], path + ".url")


def _check_report(report: Any, path: str) -> None:
    _check_object(report, REPORT_FIELDS, path)
    _check_text(report["source_name"], path + ".source_name")
    _check_enum(report["status"], REPORT_STATUSES, path + ".status")
    items = _check_list(report["items"], path + ".items")
    for number, entry in enumerate(items):
        _check_item(entry, _index(path + ".items", number))


def _check_annotation(note: Any, path: str) -> None:
    _check_object(note, ANNOTATION_FIELDS, path)
    _check_text(note["item_ref"], path + ".item_ref")
    _check_text(note["summary"], path + ".summary", MAX_SUMMARY)
    impact = _check_object(note["impact"], IMPACT_FIELDS, path + ".impact")
    _check_enum(impact["area"], IMPACT_AREAS, path + ".impact.area")
    _check_enum(impact["likelihood"], LIKELIHOODS, path + ".impact.likelihood")
    _check_text(impact["note"], path + ".impact.note", MAX_IMPACT_NOTE)


def _check_item_shapes(params: Dict[str, Any]) -> None:
    """Shapes, patterns, enums and ranges, field by field in input order."""
    _check_date_text(params["cycle_start"], "cycle_start")
    _check_date_text(params["cycle_end"], "cycle_end")
    _check_codes(params["risk_domains"], _DOMAIN_RE, "risk_domains")
    _check_codes(params["jurisdictions"], _JURISDICTION_RE, "jurisdictions")
    sources = _check_list(params["sources"], "sources", minimum=1)
    for position, source in enumerate(sources):
        _check_source(source, _index("sources", position))
    _check_int(params["max_items"], MAX_ITEMS_RANGE, "max_items")
    if "client_exposure_tags" in params:
        tags = params["client_exposure_tags"]
        _check_codes(tags, _DOMAIN_RE, "client_exposure_tags")
    if "previous_tallies" in params:
        _check_previous(params["previous_tallies"])
    for position, report in enumerate(params["collection"]):
        _check_report(report, _index("collection", position))
    if "log_destination" in params:
        destination = params["log_destination"]
        _check_text(destination, "log_destination", MAX_LOG_DESTINATION)
    if "attestation" in params:
        attestation = params["attestation"]
        _check_object(attestation, ATTESTATION_FIELDS, "attestation")
        if not isinstance(attestation["attested"], bool):
            _invalid("invalid_type", "attestation.attested")
        _check_int(attestation["minutes"], MINUTES_RANGE, "attestation.minutes")
    for position, note in enumerate(params.get("annotations", [])):
        _check_annotation(note, _index("annotations", position))


def _parse_date(value: str, path: str) -> date:
    """A whole-string YYYY-MM-DD in ASCII digits that names a real date."""
    match = _DATE_RE.fullmatch(value)
    day = None
    if match is not None:
        try:
            day = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            day = None
    if day is None:
        _fail(INVALID_DATE, "invalid_date", path)
    return day


def _first_repeat(values: List[Any]) -> Optional[int]:
    seen = set()
    for position, value in enumerate(values):
        if value in seen:
            return position
        seen.add(value)
    return None


def _check_repeats(values: List[Any], path: str, suffix: str = "") -> None:
    position = _first_repeat(values)
    if position is not None:
        _fail(DUPLICATE_KEY, "duplicate_key", _index(path, position) + suffix)


class _Entry:
    """One collected item with its parsed date and its collection position."""

    def __init__(self, raw: Dict[str, Any], published: date, source: str, order: int):
        self.title_text = raw["title"]
        self.published = published
        self.jurisdiction = raw["jurisdiction"]
        self.domains = raw["domains"]
        self.tags = raw.get("exposure_tags", [])
        self.url = raw.get("url")
        self.source = source
        self.position = order


class _Request:
    """The validated request."""

    def __init__(self, params: Dict[str, Any], action: str):
        self.action = action
        self.params = params
        self.start = date(1, 1, 1)
        self.end = date(1, 1, 1)
        self.window_days = 0
        self.entries: List[_Entry] = []
        self.domains: List[str] = params["risk_domains"]
        self.jurisdictions: List[str] = params["jurisdictions"]
        self.source_position = {
            source["name"]: position
            for position, source in enumerate(params["sources"])
        }
        self.source_kind = {
            source["name"]: source["kind"] for source in params["sources"]
        }
        self.client_tags = frozenset(params.get("client_exposure_tags", []))
        previous = params.get("previous_tallies")
        self.previous = None if previous is None else previous["by_domain"]
        self.max_items: int = params["max_items"]


def _read_dates(request: _Request) -> None:
    params = request.params
    request.start = _parse_date(params["cycle_start"], "cycle_start")
    request.end = _parse_date(params["cycle_end"], "cycle_end")
    order = 0
    for position, report in enumerate(params["collection"]):
        path = _index("collection", position) + ".items"
        for number, raw in enumerate(report["items"]):
            day = _parse_date(
                raw["published_date"], _index(path, number) + ".published_date"
            )
            request.entries.append(_Entry(raw, day, report["source_name"], order))
            order += 1


def _check_window(request: _Request) -> None:
    if request.end < request.start:
        _fail(WINDOW_INVALID, "end_before_start", "cycle_end")
    request.window_days = (request.end - request.start).days + 1
    if request.window_days > MAX_WINDOW_DAYS:
        _fail(WINDOW_INVALID, "window_too_long", "cycle_end", limit=MAX_WINDOW_DAYS)


def _check_duplicates(params: Dict[str, Any]) -> None:
    _check_repeats(params["risk_domains"], "risk_domains")
    _check_repeats(params["jurisdictions"], "jurisdictions")
    names = [source["name"] for source in params["sources"]]
    _check_repeats(names, "sources", ".name")
    _check_repeats(params.get("client_exposure_tags", []), "client_exposure_tags")
    reported = [report["source_name"] for report in params["collection"]]
    _check_repeats(reported, "collection", ".source_name")
    for position, report in enumerate(params["collection"]):
        path = _index("collection", position) + ".items"
        for number, raw in enumerate(report["items"]):
            _check_repeats(raw["domains"], _index(path, number) + ".domains")
            tags = raw.get("exposure_tags", [])
            _check_repeats(tags, _index(path, number) + ".exposure_tags")


def _check_previous_domains(request: _Request) -> None:
    if request.previous is not None:
        for domain in request.previous:
            if domain not in request.domains:
                _invalid("unknown_domain", "previous_tallies.by_domain")


def _check_reports(params: Dict[str, Any]) -> None:
    configured = [source["name"] for source in params["sources"]]
    reported = [report["source_name"] for report in params["collection"]]
    for position, name in enumerate(reported):
        if name not in configured:
            path = _index("collection", position) + ".source_name"
            _fail(UNKNOWN_SOURCE, "unknown_source", path)
    for position, name in enumerate(configured):
        if name not in reported:
            _fail(
                SOURCE_NOT_REPORTED, "source_not_reported", _index("sources", position)
            )
    for position, report in enumerate(params["collection"]):
        if report["status"] != "ok":
            path = _index("collection", position) + ".status"
            _fail(SOURCE_UNREACHABLE, "source_unreachable", path)


def _validate(params: Dict[str, Any], action: str) -> _Request:
    _check_top_level(params, action)
    _check_empty_scope(params)
    _check_list_caps(params)
    _check_item_shapes(params)
    request = _Request(params, action)
    _read_dates(request)
    _check_window(request)
    _check_duplicates(params)
    _check_previous_domains(request)
    _check_reports(params)
    if action == ACTION_SCREEN:
        for name in COMPILE_ONLY:
            if name in params:
                _invalid("field_not_allowed", name)
    return request


# --------------------------------------------------------------------------
# Screening, ordering, the cap and the tallies
# --------------------------------------------------------------------------


def _normalized_title(text: str) -> str:
    """Fold A to Z to a to z and collapse runs of U+0020; nothing else changes."""
    return _SPACE_RUN_RE.sub(" ", text.translate(_ASCII_FOLD))


def _exclusion(request: _Request, entry: _Entry) -> Optional[str]:
    if entry.published < request.start or entry.published > request.end:
        return "out_of_window"
    if entry.jurisdiction not in request.jurisdictions:
        return "out_of_jurisdiction"
    if not any(domain in request.domains for domain in entry.domains):
        return "out_of_domain"
    return None


def _relevant(request: _Request, entry: _Entry) -> bool:
    return any(tag in request.client_tags for tag in entry.tags)


def _order_key(request: _Request, entry: _Entry) -> Tuple[int, int, int, str, int]:
    return (
        0 if _relevant(request, entry) else 1,
        -entry.published.toordinal(),
        request.source_position[entry.source],
        _normalized_title(entry.title_text),
        entry.position,
    )


class _Screened:
    """The kept items in order, the exclusion counts and the tallies."""

    def __init__(self, kept: List[_Entry], excluded: Dict[str, int], tallies):
        self.kept = kept
        self.excluded = excluded
        self.tallies = tallies


def _tallies(request: _Request, unique: List[_Entry]) -> Dict[str, Any]:
    by_domain = {
        domain: sum(1 for entry in unique if domain in entry.domains)
        for domain in request.domains
    }
    by_jurisdiction = {
        place: sum(1 for entry in unique if entry.jurisdiction == place)
        for place in request.jurisdictions
    }
    by_kind = {
        kind: sum(1 for entry in unique if request.source_kind[entry.source] == kind)
        for kind in SOURCE_KINDS
    }
    change = None
    if request.previous is not None:
        change = {
            domain: by_domain[domain] - request.previous.get(domain, 0)
            for domain in request.domains
        }
    return {
        "in_scope_count": len(unique),
        "by_domain": by_domain,
        "by_jurisdiction": by_jurisdiction,
        "by_kind": by_kind,
        "change_by_domain": change,
    }


def _screen(request: _Request) -> _Screened:
    excluded = dict.fromkeys(EXCLUSION_REASONS, 0)
    in_scope = []
    for entry in request.entries:
        reason = _exclusion(request, entry)
        if reason is None:
            in_scope.append(entry)
        else:
            excluded[reason] += 1
    in_scope.sort(key=lambda entry: _order_key(request, entry))
    seen = set()
    unique = []
    for entry in in_scope:
        key = (_normalized_title(entry.title_text), entry.published, entry.jurisdiction)
        if key in seen:
            excluded["duplicate"] += 1
        else:
            seen.add(key)
            unique.append(entry)
    kept = unique[: request.max_items]
    excluded["over_cap"] = len(unique) - len(kept)
    return _Screened(kept, excluded, _tallies(request, unique))


# --------------------------------------------------------------------------
# Annotations and the outputs
# --------------------------------------------------------------------------


def _item_ref(number: int) -> str:
    return "R%03d" % number


def _check_annotations(request: _Request, screened: _Screened) -> Dict[str, Any]:
    kept_refs = [_item_ref(number) for number in range(1, len(screened.kept) + 1)]
    by_ref: Dict[str, Any] = {}
    for position, note in enumerate(request.params["annotations"]):
        path = _index("annotations", position) + ".item_ref"
        ref = note["item_ref"]
        if ref not in kept_refs:
            _fail(UNKNOWN_ITEM_REF, "unknown_item_ref", path)
        if ref in by_ref:
            _fail(DUPLICATE_KEY, "duplicate_key", path)
        by_ref[ref] = note
    for ref in kept_refs:
        if ref not in by_ref:
            _fail(ANNOTATION_MISSING, "annotation_missing", "annotations", item_ref=ref)
    return by_ref


def _render_item(request: _Request, entry: _Entry, number: int) -> Dict[str, Any]:
    relevant = _relevant(request, entry)
    due = None
    if relevant:
        due = (request.end + timedelta(days=request.window_days)).isoformat()
    return {
        "item_ref": _item_ref(number),
        "title": entry.title_text,
        "source_name": entry.source,
        "kind": request.source_kind[entry.source],
        "published_date": entry.published.isoformat(),
        "jurisdiction": entry.jurisdiction,
        "matched_domains": [d for d in request.domains if d in entry.domains],
        "url": entry.url,
        "client_relevant": relevant,
        "follow_up_required": relevant,
        "follow_up_due_by": due,
    }


def _common(request: _Request, screened: _Screened, items) -> Dict[str, Any]:
    excluded = screened.excluded
    return {
        "status": "completed",
        "cycle_start": request.start.isoformat(),
        "cycle_end": request.end.isoformat(),
        "window_days": request.window_days,
        "scope": {
            "risk_domains": list(request.domains),
            "jurisdictions": list(request.jurisdictions),
        },
        "items": items,
        "excluded_count": len(request.entries) - len(screened.kept),
        "excluded_by_reason": {
            reason: excluded[reason] for reason in EXCLUSION_REASONS
        },
        "flagged_count": sum(1 for item in items if item["follow_up_required"]),
        "tallies": screened.tallies,
    }


def _screen_output(request: _Request, screened: _Screened) -> Dict[str, Any]:
    items = [
        _render_item(request, entry, number)
        for number, entry in enumerate(screened.kept, 1)
    ]
    output = _common(request, screened, items)
    caps = {"summary": MAX_SUMMARY, "impact_note": MAX_IMPACT_NOTE}
    output["annotation_slots"] = [
        {"item_ref": item["item_ref"], "caps": dict(caps)} for item in items
    ]
    output["impact_areas"] = list(IMPACT_AREAS)
    output["likelihoods"] = list(LIKELIHOODS)
    output["review_status"] = "not_annotated"
    return output


def _compile_output(
    request: _Request, screened: _Screened, by_ref: Dict[str, Any]
) -> Dict[str, Any]:
    items = []
    for number, entry in enumerate(screened.kept, 1):
        item = _render_item(request, entry, number)
        note = by_ref[item["item_ref"]]
        impact = note["impact"]
        item["summary"] = note["summary"]
        item["impact"] = {
            "area": impact["area"],
            "likelihood": impact["likelihood"],
            "note": impact["note"],
        }
        items.append(item)
    output = _common(request, screened, items)
    attestation = request.params.get("attestation")
    minutes = 0
    if attestation is not None and attestation["attested"]:
        minutes = attestation["minutes"]
    output["competence_minutes_logged"] = minutes
    output["review_status"] = "pending_review"
    output["log_status"] = "returned_to_caller"
    output["log_destination"] = request.params.get("log_destination")
    return output


def _dispatch(params: Any) -> Dict[str, Any]:
    if not isinstance(params, dict):
        _invalid("parameters_not_object", "")
    action = params.get("action")
    if not isinstance(action, str) or action not in ACTIONS:
        _invalid("unknown_action", "action")
    request = _validate(params, action)
    screened = _screen(request)
    if action == ACTION_SCREEN:
        return _screen_output(request, screened)
    by_ref = _check_annotations(request, screened)
    return _compile_output(request, screened, by_ref)


class ScopedRiskLiteratureScanSkill(BaseSkill):
    """Screens a caller's collection report and checks the caller's annotations."""

    @property
    def manifest(self) -> Dict[str, Any]:
        return MANIFEST

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
