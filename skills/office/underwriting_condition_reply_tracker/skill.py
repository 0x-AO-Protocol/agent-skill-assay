"""Underwriting condition reply tracker: countdown, slots and checked reply drafts.

Three actions share one validated request:

  track    - computes the days to closing, the days each condition has been
             open and the lead-window flags, and lists the escalations.
  scaffold - adds one draft slot per open condition for the caller to fill.
  compile  - checks the caller's drafts (cited documents, citation markers,
             figures and caps), composes from fixed templates any draft text
             the caller leaves out, and returns every draft held for review.

The caller chooses the cited documents and may write the draft texts; the
skill sends nothing and decides nothing about the loan. It makes no model call
and uses no network, clock, file or environment. Every number is an integer.
A contract violation returns an error envelope with a code from the closed
registry below; an unexpected internal fault returns the one-code fault
registry entry INTERNAL_ERROR.
"""

import re
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from skill_assay.core.base_skill import BaseSkill

REGISTRY_ID = "office/underwriting_condition_reply_tracker"
VERSION = "0.1.0"

ACTION_TRACK = "track"
ACTION_SCAFFOLD = "scaffold"
ACTION_COMPILE = "compile"
ACTIONS = (ACTION_TRACK, ACTION_SCAFFOLD, ACTION_COMPILE)

# --------------------------------------------------------------------------
# Closed registries
# --------------------------------------------------------------------------

INVALID_REQUEST = "INVALID_REQUEST"
INVALID_DATE = "INVALID_DATE"
EMPTY_VERIFIED_FILE = "EMPTY_VERIFIED_FILE"
DEADLINE_BEFORE_AS_OF = "DEADLINE_BEFORE_AS_OF"
REQUEST_AFTER_AS_OF = "REQUEST_AFTER_AS_OF"
DUPLICATE_ID = "DUPLICATE_ID"
NO_OPEN_CONDITION = "NO_OPEN_CONDITION"
UNKNOWN_CONDITION = "UNKNOWN_CONDITION"
DRAFT_FOR_CLOSED_CONDITION = "DRAFT_FOR_CLOSED_CONDITION"
DRAFT_MISSING = "DRAFT_MISSING"
UNKNOWN_DOCUMENT = "UNKNOWN_DOCUMENT"
MALFORMED_CITATION = "MALFORMED_CITATION"
CITATION_MISMATCH = "CITATION_MISMATCH"
UNSUPPORTED_FIGURE = "UNSUPPORTED_FIGURE"
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
    ),
    INVALID_DATE: ("invalid_date",),
    EMPTY_VERIFIED_FILE: ("empty_verified_file",),
    DEADLINE_BEFORE_AS_OF: ("deadline_before_as_of",),
    REQUEST_AFTER_AS_OF: ("request_after_as_of",),
    DUPLICATE_ID: ("duplicate_id",),
    NO_OPEN_CONDITION: ("no_open_condition",),
    UNKNOWN_CONDITION: ("unknown_condition",),
    DRAFT_FOR_CLOSED_CONDITION: ("condition_not_open",),
    DRAFT_MISSING: ("draft_missing",),
    UNKNOWN_DOCUMENT: ("unknown_document",),
    MALFORMED_CITATION: ("malformed_marker",),
    CITATION_MISMATCH: ("marker_not_cited", "cited_without_marker"),
    UNSUPPORTED_FIGURE: (
        "non_ascii_digit",
        "unsupported_date",
        "unsupported_number",
        "unsupported_token",
        "unsupported_number_word",
    ),
}
FAULT_REGISTRY = {INTERNAL_ERROR: ("internal_error",)}

# --------------------------------------------------------------------------
# Enums, caps, templates and patterns
# --------------------------------------------------------------------------

DOCUMENT_TYPES = (
    "income_verification",
    "asset_verification",
    "identity_verification",
    "employment_verification",
    "property_valuation",
    "insurance_evidence",
    "title_evidence",
    "explanation_letter",
    "other",
)
CONDITION_STATUSES = ("open", "resolved", "waived")
DEFAULT_LEAD_DAYS = 7
LEAD_DAYS_RANGE = (0, 365)
PRIOR_REPLY_RANGE = (0, 50)
MAX_DOCUMENTS = 200
MAX_CONDITIONS = 100
MAX_DRAFTS = 100
MAX_CITED = 20
MAX_TITLE = 200
MAX_SUMMARY = 1000
MAX_DESCRIPTION = 2000
DRAFT_FIELDS = ("issue_summary", "proposed_resolution", "reply_body")
DRAFT_CAPS = {"issue_summary": 600, "proposed_resolution": 600, "reply_body": 4000}
RUN_SUMMARY_CAP = 600
REVIEW_STATE = "awaiting_operator_review"
CITATION_MARKER = "[doc:<document_id>]"

RUN_SUMMARY = (
    "Days to closing: %d. Lead window: %d. Open: %d. Resolved: %d. "
    "Waived: %d. Flagged: %d."
)
RUN_SUMMARY_DRAFTS = " Drafts awaiting operator review: %d."
ISSUE_SUMMARY = "Condition %s, raised %s: %s"
PROPOSED_RESOLUTION = (
    "Review the cited verified documents (%s) and clear condition %s if they "
    "satisfy it."
)
REPLY_OPENING = (
    "Regarding condition %s, raised %s.\n\n"
    "The verified file holds these documents in support:\n"
)
REPLY_LINE = "- %s [doc:%s]\n"
REPLY_CLOSING = (
    "\nWe ask that the condition be reviewed for clearance on the basis of these "
    "documents."
)

NUMBER_WORDS = frozenset(
    "two three four five six seven eight nine ten eleven twelve thirteen "
    "fourteen fifteen sixteen seventeen eighteen nineteen twenty thirty forty "
    "fifty sixty seventy eighty ninety hundred thousand million billion "
    "trillion double twice triple doubled tripled halved".split(" ")
)
ASCII_DIGITS = "0123456789"

_ID_RE = re.compile("[A-Za-z0-9_-]{1,64}", re.ASCII)
_ID_FORM_RE = re.compile("[A-Za-z0-9_]+(?:-[A-Za-z0-9_]+)*", re.ASCII)
_DATE_RE = re.compile("([0-9]{4})-([0-9]{2})-([0-9]{2})", re.ASCII)
_MARKER_RE = re.compile(r"\[doc:([A-Za-z0-9_-]{1,64})\]", re.ASCII)
_LEADING_POINT_RE = re.compile(r"(?<![A-Za-z0-9_])\.(?=[0-9])", re.ASCII)
_WORD_RE = re.compile("[A-Za-z0-9_]+(?:[-.,]+[A-Za-z0-9_]+)*", re.ASCII)
_DATE_WORD_RE = re.compile("[0-9]{4}-[0-9]{2}-[0-9]{2}", re.ASCII)
_GROUPED_NUMBER_RE = re.compile(r"[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?", re.ASCII)
_PLAIN_NUMBER_RE = re.compile(r"[0-9]+(?:\.[0-9]+)?", re.ASCII)
_RUN_RE = re.compile("[0-9.,]+", re.ASCII)
_TOKEN_RE = re.compile("[A-Za-z0-9_]+", re.ASCII)
_DIGITS_RE = re.compile("[0-9]+", re.ASCII)
_HAS_DIGIT_RE = re.compile("[0-9]", re.ASCII)
_HAS_LETTER_RE = re.compile("[A-Za-z_]", re.ASCII)
_CONTROL_RE = re.compile(r"[\x00-\x09\x0b-\x1f\x7f-\x9f]", re.ASCII)
_SURROGATE_RE = re.compile(r"[\ud800-\udfff]", re.ASCII)
_ASCII_FOLD = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")

# --------------------------------------------------------------------------
# The request shape
# --------------------------------------------------------------------------

TOP_LEVEL_TYPES = (
    ("action", "string"),
    ("as_of_date", "string"),
    ("closing_deadline", "string"),
    ("escalation_lead_days", "integer"),
    ("verified_file", "array"),
    ("condition_requests", "array"),
    ("drafts", "array"),
)
TOP_LEVEL_FIELDS = tuple(name for name, _ in TOP_LEVEL_TYPES)
ALWAYS_REQUIRED = (
    "as_of_date",
    "closing_deadline",
    "verified_file",
    "condition_requests",
)
DOCUMENT_FIELDS = (
    ("document_id", True),
    ("title", True),
    ("document_type", True),
    ("summary", False),
)
CONDITION_FIELDS = (
    ("condition_id", True),
    ("requested_on", True),
    ("description", True),
    ("status", True),
    ("prior_reply_count", False),
)
DRAFT_KEYS = (
    ("condition_id", True),
    ("cited_document_ids", True),
    ("issue_summary", False),
    ("proposed_resolution", False),
    ("reply_body", False),
)

PARAMETERS = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": list(ACTIONS),
            "description": (
                "track returns the countdown and the escalations; scaffold adds "
                "one draft slot per open condition; compile checks the caller's "
                "drafts, composes any text left out from fixed templates and "
                "returns every draft held for operator review."
            ),
        },
        "as_of_date": {
            "type": "string",
            "description": "Required. The run date as YYYY-MM-DD; no clock is read.",
        },
        "closing_deadline": {
            "type": "string",
            "description": (
                "Required. The closing date as YYYY-MM-DD, not before as_of_date."
            ),
        },
        "escalation_lead_days": {
            "type": "integer",
            "description": (
                "Optional. 0 to 365, default 7; an open condition is flagged when "
                "the days to closing are at most this window."
            ),
        },
        "verified_file": {
            "type": "array",
            "description": (
                "Required. 1 to 200 verified documents: document_id (unique), "
                "title (up to 200 characters), document_type and an optional "
                "summary (up to 1000 characters) that the figure check reads as "
                "evidence and no output repeats."
            ),
        },
        "condition_requests": {
            "type": "array",
            "description": (
                "Required. 1 to 100 conditions: condition_id (unique), "
                "requested_on (not after as_of_date), description (up to 2000 "
                "characters), status (open, resolved or waived) and an optional "
                "prior_reply_count (0 to 50)."
            ),
        },
        "drafts": {
            "type": "array",
            "description": (
                "compile only. One draft per open condition: "
                "condition_id, cited_document_ids (1 to 20 ids of the verified "
                "file) and optional issue_summary, proposed_resolution (up to 600 "
                "characters each) and reply_body (up to 4000 characters)."
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


def _fail(code: str, reason: str, path: str, **numbers: int) -> None:
    detail: Dict[str, Any] = {"reason": reason, "path": path}
    detail.update(numbers)
    raise _ContractError(code, detail)


def _invalid(reason: str, path: str, **numbers: int) -> None:
    _fail(INVALID_REQUEST, reason, path, **numbers)


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
    return _is_int(value)


def _has_other_digit(text: str) -> bool:
    """True when a decimal digit other than 0 to 9 occurs (str.isdecimal)."""
    return any(char.isdecimal() and char not in ASCII_DIGITS for char in text)


def _check_text(value: Any, path: str, cap: Optional[int], copied: bool = False):
    """A text: within its cap, no surrogate, no control but line feed, not blank."""
    if not isinstance(value, str):
        _invalid("invalid_type", path)
    if cap is not None and len(value) > cap:
        _invalid("size_cap", path, limit=cap, actual=len(value))
    if _SURROGATE_RE.search(value) or _CONTROL_RE.search(value):
        _invalid("invalid_value", path)
    if value.strip(" \n") == "":
        _invalid("invalid_value", path)
    if copied and _has_other_digit(value):
        _invalid("invalid_value", path)


def _check_id(value: Any, path: str) -> None:
    """The whole value matches both id patterns: 1 to 64 characters, and runs
    of ASCII letters, digits and "_" joined by single hyphens."""
    if not isinstance(value, str):
        _invalid("invalid_type", path)
    if not (_ID_RE.fullmatch(value) and _ID_FORM_RE.fullmatch(value)):
        _invalid("invalid_value", path)


def _check_enum(value: Any, allowed: Tuple[str, ...], path: str) -> None:
    if not isinstance(value, str):
        _invalid("invalid_type", path)
    if value not in allowed:
        _invalid("invalid_value", path)


def _check_int(value: Any, bounds: Tuple[int, int], path: str) -> None:
    if not _is_int(value):
        _invalid("invalid_type", path)
    if value < bounds[0] or value > bounds[1]:
        _invalid("out_of_range", path)


def _check_date_text(value: Any, path: str) -> None:
    """A date field is a string; the date check reads its grammar and calendar.

    Any other character, a lone surrogate included, fails that check.
    """
    if not isinstance(value, str):
        _invalid("invalid_type", path)


def _check_object(value: Any, fields: Tuple[Tuple[str, bool], ...], path: str) -> None:
    if not isinstance(value, dict):
        _invalid("invalid_type", path)
    allowed = [name for name, _ in fields]
    for name in value:
        if name not in allowed:
            _invalid("unknown_key", path)
    for name, required in fields:
        if required and name not in value:
            _invalid("missing_field", _key(path, name))


def _check_list(value: Any, path: str) -> List[Any]:
    if not isinstance(value, list):
        _invalid("invalid_type", path)
    if not value:
        _invalid("out_of_range", path)
    return value


def _cap(values: Any, limit: int, path: str) -> None:
    if isinstance(values, list) and len(values) > limit:
        _invalid("size_cap", path, limit=limit, actual=len(values))


# --------------------------------------------------------------------------
# Validation, in the documented check order
# --------------------------------------------------------------------------


def _check_top_level(params: Dict[str, Any], action: str) -> None:
    """Unknown top-level keys, then required fields, then top-level types."""
    for name in params:
        if name not in TOP_LEVEL_FIELDS:
            _invalid("unknown_key", "")
    required = ALWAYS_REQUIRED
    for name in TOP_LEVEL_FIELDS:
        if name in required and name not in params:
            _invalid("missing_field", name)
    for name, kind in TOP_LEVEL_TYPES:
        if name in params and not _has_type(params[name], kind):
            _invalid("invalid_type", name)


def _check_list_caps(params: Dict[str, Any]) -> None:
    _cap(params["verified_file"], MAX_DOCUMENTS, "verified_file")
    _cap(params["condition_requests"], MAX_CONDITIONS, "condition_requests")
    drafts = params.get("drafts", [])
    _cap(drafts, MAX_DRAFTS, "drafts")
    for position, draft in enumerate(drafts):
        if isinstance(draft, dict):
            path = _index("drafts", position) + ".cited_document_ids"
            _cap(draft.get("cited_document_ids"), MAX_CITED, path)


def _check_document(doc: Any, path: str) -> None:
    _check_object(doc, DOCUMENT_FIELDS, path)
    _check_id(doc["document_id"], path + ".document_id")
    _check_text(doc["title"], path + ".title", MAX_TITLE, copied=True)
    _check_enum(doc["document_type"], DOCUMENT_TYPES, path + ".document_type")
    if "summary" in doc:
        _check_text(doc["summary"], path + ".summary", MAX_SUMMARY)


def _check_condition(item: Any, path: str) -> None:
    _check_object(item, CONDITION_FIELDS, path)
    _check_id(item["condition_id"], path + ".condition_id")
    _check_date_text(item["requested_on"], path + ".requested_on")
    description = item["description"]
    _check_text(description, path + ".description", MAX_DESCRIPTION, copied=True)
    _check_enum(item["status"], CONDITION_STATUSES, path + ".status")
    if "prior_reply_count" in item:
        _check_int(
            item["prior_reply_count"], PRIOR_REPLY_RANGE, path + ".prior_reply_count"
        )


def _check_draft(draft: Any, path: str) -> None:
    _check_object(draft, DRAFT_KEYS, path)
    _check_text(draft["condition_id"], path + ".condition_id", None)
    cited = _check_list(draft["cited_document_ids"], path + ".cited_document_ids")
    for position, doc_id in enumerate(cited):
        _check_text(doc_id, _index(path + ".cited_document_ids", position), None)
    for field in DRAFT_FIELDS:
        if field in draft:
            _check_text(draft[field], path + "." + field, DRAFT_CAPS[field])


def _check_shapes(params: Dict[str, Any]) -> None:
    """Shapes, patterns, enums and ranges, field by field in input order."""
    _check_date_text(params["as_of_date"], "as_of_date")
    _check_date_text(params["closing_deadline"], "closing_deadline")
    if "escalation_lead_days" in params:
        _check_int(
            params["escalation_lead_days"], LEAD_DAYS_RANGE, "escalation_lead_days"
        )
    for position, doc in enumerate(params["verified_file"]):
        _check_document(doc, _index("verified_file", position))
    conditions = _check_list(params["condition_requests"], "condition_requests")
    for position, item in enumerate(conditions):
        _check_condition(item, _index("condition_requests", position))
    if "drafts" in params:
        for position, draft in enumerate(_check_list(params["drafts"], "drafts")):
            _check_draft(draft, _index("drafts", position))


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


def _first_repeat(values: List[str]) -> Optional[int]:
    seen = set()
    for position, value in enumerate(values):
        if value in seen:
            return position
        seen.add(value)
    return None


class _Condition:
    def __init__(self, raw: Dict[str, Any], position: int, requested: date):
        self.position = position
        self.condition_id = raw["condition_id"]
        self.requested = requested
        self.description = raw["description"]
        self.status = raw["status"]
        self.prior = raw.get("prior_reply_count", 0)
        self.days_open = 0


class _Evidence:
    """What the figure rule admits for one draft."""

    def __init__(self) -> None:
        self.dates = set()
        self.numbers = set()
        self.parts = set()
        self.tokens = set()
        self.folded = set()


class _Draft:
    def __init__(self, raw: Dict[str, Any], position: int):
        self.raw = raw
        self.position = position
        self.condition_id = raw["condition_id"]
        self.cited: List[str] = raw["cited_document_ids"]
        self.condition: Optional[_Condition] = None
        self.evidence: Optional[_Evidence] = None


class _Request:
    """The validated request."""

    def __init__(self, params: Dict[str, Any], action: str):
        self.action = action
        self.as_of = _parse_date(params["as_of_date"], "as_of_date")
        self.closing = _parse_date(params["closing_deadline"], "closing_deadline")
        self.lead = params.get("escalation_lead_days", DEFAULT_LEAD_DAYS)
        self.documents: List[Dict[str, Any]] = params["verified_file"]
        self.document_ids = [doc["document_id"] for doc in self.documents]
        self.doc_index = {doc_id: n for n, doc_id in enumerate(self.document_ids)}
        self.conditions: List[_Condition] = []
        for position, raw in enumerate(params["condition_requests"]):
            path = _index("condition_requests", position) + ".requested_on"
            requested = _parse_date(raw["requested_on"], path)
            self.conditions.append(_Condition(raw, position, requested))
        condition_ids = [item.condition_id for item in self.conditions]
        self.identifiers = frozenset(self.document_ids + condition_ids)
        self.drafts = [
            _Draft(raw, position)
            for position, raw in enumerate(params.get("drafts", []))
        ]
        self.days_until_closing = 0


def _check_order_of_dates(request: _Request) -> None:
    if request.closing < request.as_of:
        _fail(DEADLINE_BEFORE_AS_OF, "deadline_before_as_of", "closing_deadline")
    for item in request.conditions:
        if item.requested > request.as_of:
            path = _index("condition_requests", item.position) + ".requested_on"
            _fail(REQUEST_AFTER_AS_OF, "request_after_as_of", path)
    request.days_until_closing = (request.closing - request.as_of).days
    for item in request.conditions:
        item.days_open = (request.as_of - item.requested).days


def _check_draft_conditions(request: _Request) -> None:
    by_id = {item.condition_id: item for item in request.conditions}
    seen = set()
    for draft in request.drafts:
        path = _index("drafts", draft.position) + ".condition_id"
        item = by_id.get(draft.condition_id)
        if item is None:
            _fail(UNKNOWN_CONDITION, "unknown_condition", path)
        if item.status != "open":
            _fail(DRAFT_FOR_CLOSED_CONDITION, "condition_not_open", path)
        if draft.condition_id in seen:
            _fail(DUPLICATE_ID, "duplicate_id", path)
        seen.add(draft.condition_id)
        draft.condition = item


def _check_missing_drafts(request: _Request) -> None:
    drafted = [draft.condition_id for draft in request.drafts]
    for item in request.conditions:
        if item.status == "open" and item.condition_id not in drafted:
            path = _index("condition_requests", item.position)
            _fail(DRAFT_MISSING, "draft_missing", path)


def _malformed(text: str) -> bool:
    """True when an occurrence of "[doc:" does not start a well-formed marker."""
    start = text.find("[doc:")
    while start != -1:
        if _MARKER_RE.match(text, start) is None:
            return True
        start = text.find("[doc:", start + 1)
    return False


def _marker_ids(text: str) -> List[str]:
    return [match.group(1) for match in _MARKER_RE.finditer(text)]


def _check_citations(request: _Request, draft: _Draft) -> None:
    """Cited ids unique and known; then one pass per marker rule over the fields."""
    base = _index("drafts", draft.position)
    cited_path = base + ".cited_document_ids"
    repeat = _first_repeat(draft.cited)
    if repeat is not None:
        _fail(DUPLICATE_ID, "duplicate_id", _index(cited_path, repeat))
    for position, doc_id in enumerate(draft.cited):
        if doc_id not in request.doc_index:
            _fail(UNKNOWN_DOCUMENT, "unknown_document", _index(cited_path, position))
    supplied = [
        (field, draft.raw[field]) for field in DRAFT_FIELDS if field in draft.raw
    ]
    for field, text in supplied:
        if _malformed(text):
            _fail(MALFORMED_CITATION, "malformed_marker", base + "." + field)
    for field, text in supplied:
        if any(doc_id not in request.doc_index for doc_id in _marker_ids(text)):
            _fail(UNKNOWN_DOCUMENT, "unknown_document", base + "." + field)
    for field, text in supplied:
        if any(doc_id not in draft.cited for doc_id in _marker_ids(text)):
            _fail(CITATION_MISMATCH, "marker_not_cited", base + "." + field)
    if "reply_body" in draft.raw:
        marked = _marker_ids(draft.raw["reply_body"])
        if any(doc_id not in marked for doc_id in draft.cited):
            _fail(CITATION_MISMATCH, "cited_without_marker", base + ".reply_body")


def _check_figures(request: _Request) -> None:
    for draft in request.drafts:
        for field in DRAFT_FIELDS:
            if field in draft.raw:
                reason = _figure_reason(request, draft, draft.raw[field])
                if reason is not None:
                    path = _index("drafts", draft.position) + "." + field
                    _fail(UNSUPPORTED_FIGURE, reason, path)


def _validate(params: Dict[str, Any]) -> _Request:
    """Steps 3 to 16 of the check order; steps 1 and 2 run in _dispatch."""
    action = params["action"]
    _check_top_level(params, action)
    if not params["verified_file"]:
        _fail(EMPTY_VERIFIED_FILE, "empty_verified_file", "verified_file")
    _check_list_caps(params)
    _check_shapes(params)
    request = _Request(params, action)
    for path, values in (
        ("verified_file", request.document_ids),
        ("condition_requests", [item.condition_id for item in request.conditions]),
    ):
        repeat = _first_repeat(values)
        if repeat is not None:
            field = ".document_id" if path == "verified_file" else ".condition_id"
            _fail(DUPLICATE_ID, "duplicate_id", _index(path, repeat) + field)
    _check_order_of_dates(request)
    if "drafts" in params and action != ACTION_COMPILE:
        _invalid("field_not_allowed", "drafts")
    if action != ACTION_TRACK:
        if not any(item.status == "open" for item in request.conditions):
            _fail(NO_OPEN_CONDITION, "no_open_condition", "condition_requests")
    if action == ACTION_COMPILE:
        _check_draft_conditions(request)
        _check_missing_drafts(request)
        for draft in request.drafts:
            _check_citations(request, draft)
        _check_figures(request)
    return request


# --------------------------------------------------------------------------
# The figure rule
# --------------------------------------------------------------------------


def _is_number(word: str) -> bool:
    return bool(_GROUPED_NUMBER_RE.fullmatch(word) or _PLAIN_NUMBER_RE.fullmatch(word))


def _canonical(number: str) -> str:
    """Commas removed, leading zeros dropped (one kept), trailing fraction zeros dropped."""
    whole, _, fraction = number.replace(",", "").partition(".")
    whole = whole.lstrip("0") or "0"
    fraction = fraction.rstrip("0")
    return whole + "." + fraction if fraction else whole


def _read_run(run: str, read: List[Tuple[str, str]]) -> None:
    """A run made only of digits, commas and points is read whole."""
    if _is_number(run):
        read.append(("number", _canonical(run)))
    else:
        read.append(("run", run))


def _linked(left: str, right: str) -> bool:
    return left[-1] in ASCII_DIGITS and right[0] in ASCII_DIGITS


def _read_part(part: str, read: List[Tuple[str, str]]) -> None:
    """A part of a word: a run, or its tokens in chains of linked tokens.

    Two neighbouring tokens are linked when the first ends with a digit and the
    second begins with one. A chain of tokens of digits only is a run; any other
    chain of two or more tokens is read whole.
    """
    read.append(("part", part))
    if _RUN_RE.fullmatch(part):
        _read_run(part, read)
        return
    tokens = list(_TOKEN_RE.finditer(part))
    first = 0
    for position, token in enumerate(tokens):
        read.append(("token", token.group(0)))
        following = tokens[position + 1] if position + 1 < len(tokens) else None
        if following is not None and _linked(token.group(0), following.group(0)):
            continue
        chain = tokens[first : position + 1]
        text = part[chain[0].start() : token.end()]
        first = position + 1
        if all(_DIGITS_RE.fullmatch(match.group(0)) for match in chain):
            _read_run(text, read)
        elif len(chain) > 1:
            read.append(("chain", text))
        elif _HAS_DIGIT_RE.search(text) and _HAS_LETTER_RE.search(text):
            read.append(("joined", text))
        elif text.translate(_ASCII_FOLD) in NUMBER_WORDS:
            read.append(("word", text.translate(_ASCII_FOLD)))


def _figures(text: str, identifiers: frozenset) -> List[Tuple[str, str]]:
    """What the figure rule reads in ``text``, in order, as (kind, text) pairs.

    The kinds are date, number, part (each part of a word split at "-"), run (a
    run of digits, commas and points that is not a number), chain (linked
    tokens that hold a letter or "_"), token (each token of a part), joined (a
    lone token that joins a digit with a letter or "_") and word (a lone number
    word, folded). First, a point that directly precedes a digit and directly
    follows no ASCII letter, digit or "_" is read as "0.".
    """
    read: List[Tuple[str, str]] = []
    for match in _WORD_RE.finditer(_LEADING_POINT_RE.sub("0.", text)):
        word = match.group(0)
        if word in identifiers:
            continue
        if _DATE_WORD_RE.fullmatch(word):
            read.append(("date", word))
        elif _is_number(word):
            read.append(("number", _canonical(word)))
        else:
            for part in word.split("-"):
                if part:
                    _read_part(part, read)
    return read


def _read_admitted(text: str, identifiers: frozenset, evidence: _Evidence) -> None:
    """Collect the dates, numbers, parts and tokens of an admitted text."""
    for kind, figure in _figures(text, identifiers):
        if kind == "date":
            evidence.dates.add(figure)
        elif kind == "number":
            evidence.numbers.add(figure)
        elif kind == "token":
            evidence.tokens.add(figure)
            evidence.folded.add(figure.translate(_ASCII_FOLD))
        elif kind in ("part", "run", "chain"):
            evidence.parts.add(figure)


def _evidence(request: _Request, draft: _Draft) -> _Evidence:
    if draft.evidence is None:
        evidence = _Evidence()
        item = draft.condition
        evidence.dates.update(
            (
                item.requested.isoformat(),
                request.as_of.isoformat(),
                request.closing.isoformat(),
            )
        )
        values = (request.days_until_closing, item.days_open, request.lead, item.prior)
        evidence.numbers.update(str(value) for value in values)
        texts = [item.description]
        for doc_id in draft.cited:
            doc = request.documents[request.doc_index[doc_id]]
            texts.append(doc["title"])
            if "summary" in doc:
                texts.append(doc["summary"])
        for text in texts:
            _read_admitted(text, request.identifiers, evidence)
        draft.evidence = evidence
    return draft.evidence


def _figure_reason(request: _Request, draft: _Draft, text: str) -> Optional[str]:
    """The reason of the first unsupported figure of ``text``, or None."""
    if _has_other_digit(text):
        return "non_ascii_digit"
    evidence = _evidence(request, draft)
    for kind, figure in _figures(_MARKER_RE.sub(" ", text), request.identifiers):
        if kind == "date" and figure not in evidence.dates:
            return "unsupported_date"
        if kind == "number" and figure not in evidence.numbers:
            return "unsupported_number"
        if kind == "run" and figure not in evidence.parts:
            return "unsupported_number"
        if kind == "chain" and figure not in evidence.parts:
            return "unsupported_token"
        if kind == "joined" and not (
            figure in request.identifiers or figure in evidence.tokens
        ):
            return "unsupported_token"
        if kind == "word" and figure not in evidence.folded:
            return "unsupported_number_word"
    return None


# --------------------------------------------------------------------------
# Templates and the outputs
# --------------------------------------------------------------------------


def _cut(text: str, cap: int) -> str:
    """Return text as is, or cut to fit cap when it is longer.

    A cut keeps the longest prefix of at most cap - 3 characters that ends
    before a space, or the first cap - 3 characters when there is no such
    prefix, and adds "...".
    """
    if len(text) <= cap:
        return text
    space = text.rfind(" ", 0, cap - 2)
    if space == -1:
        return text[: cap - 3] + "..."
    return text[:space] + "..."


def _tracker(request: _Request) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    entries, escalations = [], []
    for item in request.conditions:
        at_risk = item.status == "open" and request.days_until_closing <= request.lead
        entries.append(
            {
                "condition_id": item.condition_id,
                "status": item.status,
                "days_until_closing": request.days_until_closing,
                "days_open": item.days_open,
                "prior_reply_count": item.prior,
                "at_risk": at_risk,
            }
        )
        if at_risk:
            escalations.append(
                {
                    "condition_id": item.condition_id,
                    "days_until_closing": request.days_until_closing,
                    "lead_window_days": request.lead,
                }
            )
    return entries, escalations


def _run_summary(request: _Request, flagged: int, drafts: int) -> str:
    counts = [
        sum(1 for item in request.conditions if item.status == status)
        for status in CONDITION_STATUSES
    ]
    numbers = (request.days_until_closing, request.lead, *counts, flagged)
    text = RUN_SUMMARY % numbers
    if request.action == ACTION_COMPILE:
        text += RUN_SUMMARY_DRAFTS % drafts
    return _cut(text, RUN_SUMMARY_CAP)


def _composed(field: str, request: _Request, item: _Condition, cited: List[str]) -> str:
    requested = item.requested.isoformat()
    if field == "issue_summary":
        text = ISSUE_SUMMARY % (item.condition_id, requested, item.description)
    elif field == "proposed_resolution":
        text = PROPOSED_RESOLUTION % (", ".join(cited), item.condition_id)
    else:
        lines = [
            REPLY_LINE % (request.documents[request.doc_index[doc_id]]["title"], doc_id)
            for doc_id in cited
        ]
        opening = REPLY_OPENING % (item.condition_id, requested)
        text = opening + "".join(lines) + REPLY_CLOSING
    return _cut(text, DRAFT_CAPS[field])


def _compiled_drafts(request: _Request) -> List[Dict[str, Any]]:
    by_condition = {draft.condition_id: draft for draft in request.drafts}
    compiled = []
    for item in request.conditions:
        if item.status != "open":
            continue
        draft = by_condition[item.condition_id]
        cited = [doc_id for doc_id in request.document_ids if doc_id in draft.cited]
        texts = {}
        for field in DRAFT_FIELDS:
            if field in draft.raw:
                texts[field] = draft.raw[field]
            else:
                texts[field] = _composed(field, request, item, cited)
        supplied = sum(1 for field in DRAFT_FIELDS if field in draft.raw)
        composed_by = "mixed"
        if supplied == len(DRAFT_FIELDS):
            composed_by = "caller"
        elif supplied == 0:
            composed_by = "template"
        compiled.append(
            {
                "condition_id": item.condition_id,
                "issue_summary": texts["issue_summary"],
                "cited_document_ids": cited,
                "proposed_resolution": texts["proposed_resolution"],
                "reply_body": texts["reply_body"],
                "composed_by": composed_by,
                "review_state": REVIEW_STATE,
            }
        )
    return compiled


def _slots(request: _Request) -> List[Dict[str, Any]]:
    caps = {"cited_document_ids": MAX_CITED, **DRAFT_CAPS}
    return [
        {
            "condition_id": item.condition_id,
            "description": item.description,
            "allowed_document_ids": list(request.document_ids),
            "citation_marker": CITATION_MARKER,
            "caps": dict(caps),
            "review_state": REVIEW_STATE,
        }
        for item in request.conditions
        if item.status == "open"
    ]


def _dispatch(params: Any) -> Dict[str, Any]:
    if not isinstance(params, dict):
        _invalid("parameters_not_object", "")
    action = params.get("action")
    if not isinstance(action, str) or action not in ACTIONS:
        _invalid("unknown_action", "action")
    request = _validate(params)
    entries, escalations = _tracker(request)
    drafts = _compiled_drafts(request) if action == ACTION_COMPILE else []
    output = {
        "status": "completed",
        "days_until_closing": request.days_until_closing,
        "lead_window_days": request.lead,
        "tracker": entries,
        "escalations": escalations,
        "operator_review_pending": True,
        "run_summary": _run_summary(request, len(escalations), len(drafts)),
    }
    if action == ACTION_SCAFFOLD:
        output["slots"] = _slots(request)
    elif action == ACTION_COMPILE:
        output["drafts"] = drafts
    return output


class UnderwritingConditionReplyTrackerSkill(BaseSkill):
    """Tracks underwriting conditions and checks the caller's reply drafts."""

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
