"""Deterministic period close over a journal and a chart of accounts.

Two actions share one data model, the journal entry:

  validate - checks a period's journal against its chart of accounts and
             returns findings from a closed registry: blocking faults
             (unbalanced entries, unmapped accounts, entries outside the
             period or after as_of, duplicate ids, unbalanced opening
             balances), warnings (suspected duplicates, zero lines, negative
             cash, recurring items missing or out of line against prior
             periods) and one informational code (unused accounts).
  close    - runs validate first and fails closed on any blocking finding
             unless soft_close is set; otherwise returns a trial balance,
             income statement, balance sheet, direct-method cash flow,
             checks, gap notes, ratios and observations.

Single currency, single entity. Amounts are decimal strings; every figure is
computed with one private decimal context and rendered by one rounding rule,
so no binary floating-point value reaches the output. The skill reads no
file, makes no network call, reads no clock and writes nothing. Identical
input returns identical output. A contract violation returns an error
envelope with a code from the closed registry below; an unexpected internal
fault returns the one-code fault registry entry INTERNAL_ERROR.
"""

import re
from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Context, Decimal
from typing import Any, Dict, List, Optional, Tuple

from skill_assay.core.base_skill import BaseSkill

REGISTRY_ID = "finance/period_close"
VERSION = "0.1.0"

ACTION_VALIDATE = "validate"
ACTION_CLOSE = "close"
ACTIONS = (ACTION_VALIDATE, ACTION_CLOSE)

# --------------------------------------------------------------------------
# Closed registries
# --------------------------------------------------------------------------

INVALID_REQUEST = "INVALID_REQUEST"
MISSING_AS_OF = "MISSING_AS_OF"
INVALID_PERIOD = "INVALID_PERIOD"
CLOSE_BEFORE_PERIOD_END = "CLOSE_BEFORE_PERIOD_END"
EMPTY_JOURNAL = "EMPTY_JOURNAL"
INVALID_AMOUNT = "INVALID_AMOUNT"
UNKNOWN_ACCOUNT_CLASS = "UNKNOWN_ACCOUNT_CLASS"
UNKNOWN_SUBCLASS = "UNKNOWN_SUBCLASS"
CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
INVALID_DATE = "INVALID_DATE"
CLOSE_BLOCKED = "CLOSE_BLOCKED"

ERROR_REGISTRY = frozenset(
    {
        INVALID_REQUEST,
        MISSING_AS_OF,
        INVALID_PERIOD,
        CLOSE_BEFORE_PERIOD_END,
        EMPTY_JOURNAL,
        INVALID_AMOUNT,
        UNKNOWN_ACCOUNT_CLASS,
        UNKNOWN_SUBCLASS,
        CURRENCY_MISMATCH,
        INVALID_DATE,
        CLOSE_BLOCKED,
    }
)

INTERNAL_ERROR = "INTERNAL_ERROR"
FAULT_REGISTRY = frozenset({INTERNAL_ERROR})

SEVERITY_BLOCKING = "blocking"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"

UNBALANCED_ENTRY = "UNBALANCED_ENTRY"
UNMAPPED_ACCOUNT = "UNMAPPED_ACCOUNT"
ENTRY_OUT_OF_PERIOD = "ENTRY_OUT_OF_PERIOD"
ENTRY_AFTER_AS_OF = "ENTRY_AFTER_AS_OF"
DUPLICATE_ENTRY_ID = "DUPLICATE_ENTRY_ID"
OPENING_UNBALANCED = "OPENING_UNBALANCED"
SUSPECTED_DUPLICATE = "SUSPECTED_DUPLICATE"
ZERO_LINE = "ZERO_LINE"
NEGATIVE_CASH = "NEGATIVE_CASH"
EXPECTED_MISSING = "EXPECTED_MISSING"
AMOUNT_DEVIATION = "AMOUNT_DEVIATION"
UNUSED_ACCOUNT = "UNUSED_ACCOUNT"

# Reported in this order, one element per code that fired.
FINDINGS_REGISTRY = (
    (UNBALANCED_ENTRY, SEVERITY_BLOCKING),
    (UNMAPPED_ACCOUNT, SEVERITY_BLOCKING),
    (ENTRY_OUT_OF_PERIOD, SEVERITY_BLOCKING),
    (ENTRY_AFTER_AS_OF, SEVERITY_BLOCKING),
    (DUPLICATE_ENTRY_ID, SEVERITY_BLOCKING),
    (OPENING_UNBALANCED, SEVERITY_BLOCKING),
    (SUSPECTED_DUPLICATE, SEVERITY_WARNING),
    (ZERO_LINE, SEVERITY_WARNING),
    (NEGATIVE_CASH, SEVERITY_WARNING),
    (EXPECTED_MISSING, SEVERITY_WARNING),
    (AMOUNT_DEVIATION, SEVERITY_WARNING),
    (UNUSED_ACCOUNT, SEVERITY_INFO),
)
FINDING_CODES = tuple(code for code, _ in FINDINGS_REGISTRY)
FINDING_SEVERITY = dict(FINDINGS_REGISTRY)

# --------------------------------------------------------------------------
# Account classes, subclasses and the request shape
# --------------------------------------------------------------------------

CLASS_ASSET = "asset"
CLASS_LIABILITY = "liability"
CLASS_EQUITY = "equity"
CLASS_REVENUE = "revenue"
CLASS_EXPENSE = "expense"
CLASSES = (CLASS_ASSET, CLASS_LIABILITY, CLASS_EQUITY, CLASS_REVENUE, CLASS_EXPENSE)

SUBCLASS_CASH = "cash"
SUBCLASS_FIXED = "fixed"
SUBCLASS_LONG_TERM = "long_term"
SUBCLASS_OPERATING = "operating"
SUBCLASS_COGS = "cogs"
SUBCLASS_OTHER_CURRENT = "other_current"

# The closed subclass set per class; the default applies when omitted.
SUBCLASSES: Dict[str, Tuple[str, ...]] = {
    CLASS_ASSET: (
        SUBCLASS_CASH,
        "receivable",
        "inventory",
        SUBCLASS_OTHER_CURRENT,
        SUBCLASS_FIXED,
    ),
    CLASS_LIABILITY: ("payable", SUBCLASS_OTHER_CURRENT, SUBCLASS_LONG_TERM),
    CLASS_EQUITY: (),
    CLASS_REVENUE: (SUBCLASS_OPERATING, "other"),
    CLASS_EXPENSE: (SUBCLASS_COGS, SUBCLASS_OPERATING, "non_operating"),
}
DEFAULT_SUBCLASS: Dict[str, Optional[str]] = {
    CLASS_ASSET: SUBCLASS_OTHER_CURRENT,
    CLASS_LIABILITY: SUBCLASS_OTHER_CURRENT,
    CLASS_EQUITY: None,
    CLASS_REVENUE: SUBCLASS_OPERATING,
    CLASS_EXPENSE: SUBCLASS_OPERATING,
}
DEBIT_NORMAL_CLASSES = (CLASS_ASSET, CLASS_EXPENSE)
BALANCE_SHEET_CLASSES = (CLASS_ASSET, CLASS_LIABILITY, CLASS_EQUITY)
PROFIT_AND_LOSS_CLASSES = (CLASS_REVENUE, CLASS_EXPENSE)

REQUEST_KEYS = (
    "action",
    "as_of",
    "period",
    "currency",
    "chart_of_accounts",
    "opening_balances",
    "journal",
    "prior_journals",
    "options",
)
PERIOD_KEYS = ("start", "end")
OPTION_KEYS = ("soft_close", "recurrence_min_periods", "deviation_threshold")
CHART_ROW_KEYS = ("account", "class", "subclass")
LINE_KEYS = ("account", "debit", "credit")
ENTRY_KEYS = ("id", "date", "counterparty", "currency", "lines")
PRIOR_PERIOD_KEYS = ("period", "journal", "currency")

VALIDATE_OUTPUT_KEYS = ("status", "findings", "blocking")
CLOSE_OUTPUT_KEYS = (
    "status",
    "closed",
    "provisional",
    "trial_balance",
    "income_statement",
    "balance_sheet",
    "cash_flow",
    "checks",
    "notes",
    "ratios",
    "observations",
    "fired_any",
    "insufficient",
)
TRIAL_BALANCE_KEYS = ("account", "class", "opening", "debit", "credit", "closing")
INCOME_STATEMENT_KEYS = ("revenue", "cogs", "gross_profit", "expenses", "net_income")
BALANCE_SHEET_KEYS = (
    "assets",
    "liabilities",
    "equity_opening",
    "equity_movements",
    "net_income",
    "equity_closing",
    "balanced",
)
CASH_FLOW_KEYS = ("cash_opening", "inflows", "outflows", "cash_closing")
RATIO_KEYS = (
    "gross_margin",
    "operating_margin",
    "net_margin",
    "current_ratio",
    "runway_months",
    "expense_concentration_top1",
)
OBSERVATION_METRICS = ("revenue", "cash_closing")
EXPECTED_MISSING_NOTE_KEYS = (
    "code",
    "account",
    "counterparty",
    "cadence",
    "last_seen",
    "expected_amount",
    "detail",
)
AMOUNT_DEVIATION_NOTE_KEYS = (
    "code",
    "account",
    "counterparty",
    "amount",
    "median",
    "deviation",
)

DEFAULT_RECURRENCE_MIN_PERIODS = 2
MIN_RECURRENCE_MIN_PERIODS = 1
MAX_RECURRENCE_MIN_PERIODS = 24
DEFAULT_DEVIATION_THRESHOLD = "0.20"
PRIOR_PERIOD_LENGTH_TOLERANCE_DAYS = 3

CADENCE_WEEKLY = "weekly"
CADENCE_MONTHLY = "monthly"
CADENCE_QUARTERLY = "quarterly"
CADENCE_LONGER = "longer"
CADENCE_WEEKLY_MAX_DAYS = 10
CADENCE_MONTHLY_MAX_DAYS = 45
CADENCE_QUARTERLY_MAX_DAYS = 135

# --------------------------------------------------------------------------
# Size caps
# --------------------------------------------------------------------------

MAX_CHART_ROWS = 2_000
MAX_OPENING_LINES = 2_000
MAX_JOURNAL_ENTRIES = 20_000
MAX_LINES_PER_ENTRY = 200
MAX_PRIOR_PERIODS = 24
MAX_PRIOR_ENTRIES_PER_PERIOD = 20_000
MAX_TOTAL_LINES = 200_000

CAP_TABLE = (
    ("chart_of_accounts", "entries", MAX_CHART_ROWS, INVALID_REQUEST),
    ("opening_balances", "entries", MAX_OPENING_LINES, INVALID_REQUEST),
    ("journal", "entries", MAX_JOURNAL_ENTRIES, INVALID_REQUEST),
    ("journal[].lines", "entries", MAX_LINES_PER_ENTRY, INVALID_REQUEST),
    ("prior_journals", "entries", MAX_PRIOR_PERIODS, INVALID_REQUEST),
    (
        "prior_journals[].journal",
        "entries",
        MAX_PRIOR_ENTRIES_PER_PERIOD,
        INVALID_REQUEST,
    ),
    (
        "prior_journals[].journal[].lines",
        "entries",
        MAX_LINES_PER_ENTRY,
        INVALID_REQUEST,
    ),
    (
        "lines (journal and prior journals together)",
        "entries",
        MAX_TOTAL_LINES,
        INVALID_REQUEST,
    ),
)

# --------------------------------------------------------------------------
# Validation order: the first failing check's code is returned
# --------------------------------------------------------------------------

CHECKS = (
    (INVALID_REQUEST, "parameters are not an object."),
    (
        INVALID_REQUEST,
        "`action` absent, not a string, or not `validate` or `close`.",
    ),
    (INVALID_REQUEST, "an unknown top-level key."),
    (MISSING_AS_OF, "`as_of` absent or null."),
    (
        INVALID_DATE,
        "`as_of` not a string of the form `YYYY-MM-DD` naming a real date.",
    ),
    (
        INVALID_REQUEST,
        "`period` absent or not an object; `currency`, `chart_of_accounts` or "
        "`journal` absent; `chart_of_accounts` or `journal` not a list; "
        "`opening_balances` or `prior_journals` present and not a list; "
        "`options` present and not an object.",
    ),
    (INVALID_PERIOD, "`period` lacks `start` or `end`, or has another key."),
    (
        INVALID_DATE,
        "`period.start` or `period.end` malformed (`start` checked first).",
    ),
    (INVALID_PERIOD, "`period.start` after `period.end`."),
    (INVALID_REQUEST, "`currency` not a string matching `^[A-Z]{3}$`."),
    (
        INVALID_REQUEST,
        "`options` holds an unknown key, a value of the wrong type, or a value "
        "out of range.",
    ),
    (
        INVALID_REQUEST,
        "a size cap (chart rows; opening lines; journal entries; entries per "
        "prior period; lines per entry; prior periods; the one figure for all "
        "lines); counting treats a non-list where a list is expected as the "
        "same failure.",
    ),
    (
        INVALID_REQUEST,
        "per chart row: not an object; an unknown key; `account` absent, not a "
        "string, not matching its pattern, or already seen.",
    ),
    (
        UNKNOWN_ACCOUNT_CLASS,
        "per chart row: `class` absent or outside the closed set.",
    ),
    (
        UNKNOWN_SUBCLASS,
        "per chart row: `subclass` present and not allowed for the class.",
    ),
    (
        INVALID_REQUEST,
        "per opening line: not an object; an unknown key; `account` not a "
        "string; the account is a revenue or expense account; a second line "
        "for the same account.",
    ),
    (
        INVALID_AMOUNT,
        "per opening line: `debit` or `credit` fails the amount grammar.",
    ),
    (EMPTY_JOURNAL, "`journal` is an empty list."),
    (
        INVALID_REQUEST,
        "per entry: not an object; an unknown key; `id` absent, not a string, "
        "or not matching its pattern; `counterparty` present and not matching "
        "its pattern; `currency` present and not a string; `lines` absent or "
        "not a list; a line not an object, with an unknown key, or with "
        "`account` not a string.",
    ),
    (INVALID_DATE, "per entry: `date` malformed."),
    (
        CURRENCY_MISMATCH,
        "per entry: `currency` present and different from the request's.",
    ),
    (
        INVALID_AMOUNT,
        "per entry, per line: `debit` or `credit` fails the grammar.",
    ),
    (
        INVALID_REQUEST,
        "per prior period: not an object; an unknown key; `period` absent, not "
        "an object, or lacking `start` or `end`; `journal` absent or not a "
        "list; `currency` present and not a string.",
    ),
    (
        INVALID_DATE,
        "per prior period: `period.start` or `period.end` malformed.",
    ),
    (
        INVALID_PERIOD,
        "per prior period: start after end; it does not end before the current "
        "`period.start`; it overlaps an earlier-listed prior period; its "
        "length differs from the current period's by more than three days.",
    ),
    (
        CURRENCY_MISMATCH,
        "per prior period: `currency` present and different from the request's.",
    ),
    (
        INVALID_REQUEST,
        "per prior entry: the shape faults of check 19; a line's account that "
        "the chart does not hold.",
    ),
    (INVALID_DATE, "per prior entry: `date` malformed."),
    (INVALID_PERIOD, "per prior entry: `date` outside its period's dates."),
    (
        CURRENCY_MISMATCH,
        "per prior entry: `currency` present and different from the request's.",
    ),
    (
        INVALID_AMOUNT,
        "per prior entry, per line: `debit` or `credit` fails the grammar.",
    ),
    (
        CLOSE_BEFORE_PERIOD_END,
        "`as_of` is before `period.end`, with or without `soft_close`.",
    ),
    (
        CLOSE_BLOCKED,
        "`soft_close` false and at least one blocking finding. `detail.findings` "
        "lists each as `{code, refs}` in registry order, as in the issue's "
        "example.",
    ),
)
VALIDATE_CHECKS = CHECKS[:31]
CLOSE_CHECKS = CHECKS

# --------------------------------------------------------------------------
# Patterns, decimal context and the in-code manifest
# --------------------------------------------------------------------------

ACCOUNT_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"
ENTRY_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$"
COUNTERPARTY_PATTERN = r"^[a-z0-9][a-z0-9_.-]{0,63}$"
CURRENCY_PATTERN = r"^[A-Z]{3}$"
AMOUNT_PATTERN = r"^(0|[1-9][0-9]{0,14})(\.[0-9]{1,2})?$"
THRESHOLD_PATTERN = r"^(0|[1-9][0-9]{0,14})(\.[0-9]{1,4})?$"
DATE_PATTERN = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"

_ACCOUNT_RE = re.compile(ACCOUNT_PATTERN)
_ENTRY_ID_RE = re.compile(ENTRY_ID_PATTERN)
_COUNTERPARTY_RE = re.compile(COUNTERPARTY_PATTERN)
_CURRENCY_RE = re.compile(CURRENCY_PATTERN)
_AMOUNT_RE = re.compile(AMOUNT_PATTERN)
_THRESHOLD_RE = re.compile(THRESHOLD_PATTERN)
_DATE_RE = re.compile(DATE_PATTERN)
_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")

# Every arithmetic operation goes through this private context; the thread's
# decimal context is never read for arithmetic and never modified.
DECIMAL_PRECISION = 40
_CTX = Context(prec=DECIMAL_PRECISION, rounding=ROUND_HALF_UP)
_ZERO = Decimal(0)
_AMOUNT_QUANTUM = Decimal("0.01")
_RATIO_QUANTUM = Decimal("0.0001")
DAYS_PER_MONTH = Decimal("30.4375")

PARAMETERS = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": list(ACTIONS),
            "description": (
                "validate returns the findings of the journal against the "
                "chart of accounts; close runs validate first and returns the "
                "statements, checks, notes, ratios and observations."
            ),
        },
        "as_of": {
            "type": "string",
            "description": (
                "Required. The date of the run as YYYY-MM-DD; no clock is read. "
                "close needs as_of on or after period.end."
            ),
        },
        "period": {
            "type": "object",
            "description": (
                "Required. start and end as YYYY-MM-DD, start not after end; "
                "both ends belong to the period."
            ),
        },
        "currency": {
            "type": "string",
            "description": (
                "Required. Three upper-case letters; every entry and prior "
                "journal that names a currency must name this one."
            ),
        },
        "chart_of_accounts": {
            "type": "array",
            "description": (
                "Required. Rows of account (unique, ^[a-z][a-z0-9_]{0,63}$), "
                "class (asset, liability, equity, revenue, expense) and an "
                "optional subclass from the closed set of that class. Retired "
                "accounts stay in the chart."
            ),
        },
        "opening_balances": {
            "type": "array",
            "description": (
                "Optional. One line per balance-sheet account with account, "
                "debit and credit as decimal strings."
            ),
        },
        "journal": {
            "type": "array",
            "description": (
                "Required, non-empty. Entries of id, date, optional "
                "counterparty, optional currency and lines of account, debit "
                "and credit as decimal strings with at most two decimals."
            ),
        },
        "prior_journals": {
            "type": "array",
            "description": (
                "Optional. Earlier periods, oldest first, each with period, "
                "journal and optional currency; comparable in length to the "
                "current period within three days."
            ),
        },
        "options": {
            "type": "object",
            "description": (
                "Optional. soft_close (boolean, default false), "
                "recurrence_min_periods (integer 1 to 24, default 2) and "
                "deviation_threshold (decimal string greater than zero with at "
                "most four decimals, default 0.20)."
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


def _fail(code: str, reason: str, **extra: Any) -> None:
    detail: Dict[str, Any] = {"reason": reason}
    detail.update(extra)
    raise _ContractError(code, detail)


# --------------------------------------------------------------------------
# Type rules and small helpers
# --------------------------------------------------------------------------


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_string(value: Any) -> bool:
    """A str with no lone surrogate; every check that needs a string uses this."""
    return isinstance(value, str) and _SURROGATE_RE.search(value) is None


def _is_date(value: Any) -> bool:
    """The whole string matches the date pattern and names a real date."""
    if not _is_string(value) or not _DATE_RE.fullmatch(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _is_amount(value: Any) -> bool:
    return _is_string(value) and _AMOUNT_RE.fullmatch(value) is not None


def _days(start: date, end: date) -> int:
    """The length of a period in days, both ends included."""
    return (end - start).days + 1


def _add(left: Decimal, right: Decimal) -> Decimal:
    return _CTX.add(left, right)


def _subtract(left: Decimal, right: Decimal) -> Decimal:
    return _CTX.subtract(left, right)


def _multiply(left: Decimal, right: Decimal) -> Decimal:
    return _CTX.multiply(left, right)


def _divide(left: Decimal, right: Decimal) -> Decimal:
    return _CTX.divide(left, right)


def _total(values: List[Decimal]) -> Decimal:
    acc = _ZERO
    for value in values:
        acc = _CTX.add(acc, value)
    return acc


def _median(values: List[Decimal]) -> Decimal:
    """The median of a non-empty list; an even count takes the mean of the middle two."""
    ordered = sorted(values)
    count = len(ordered)
    middle = count // 2
    if count % 2 == 1:
        return ordered[middle]
    return _divide(_add(ordered[middle - 1], ordered[middle]), Decimal(2))


def _quantize_amount(value: Decimal) -> Decimal:
    return _CTX.quantize(value, _AMOUNT_QUANTUM)


def _render_amount(value: Decimal) -> str:
    """Two decimals, ROUND_HALF_UP; a value that quantises to zero renders as 0."""
    quantized = _quantize_amount(value)
    if quantized.is_zero():
        return "0"
    return str(quantized)


def _render_ratio(value: Decimal) -> str:
    """Four decimals, ROUND_HALF_UP; a value that quantises to zero renders unsigned."""
    quantized = _CTX.quantize(value, _RATIO_QUANTUM)
    if quantized.is_zero():
        return "0.0000"
    return str(quantized)


def _render_optional_amount(value: Optional[Decimal]) -> Optional[str]:
    return None if value is None else _render_amount(value)


def _render_optional_ratio(value: Optional[Decimal]) -> Optional[str]:
    return None if value is None else _render_ratio(value)


def _unknown_keys(value: Dict[str, Any], allowed: Tuple[str, ...]) -> bool:
    return any(key not in allowed for key in value)


# --------------------------------------------------------------------------
# Request validation (checks 3 to 31)
# --------------------------------------------------------------------------


def _check_request_shape(params: Dict[str, Any]) -> None:
    """Checks 3 to 6."""
    if _unknown_keys(params, REQUEST_KEYS):
        _fail(INVALID_REQUEST, "unknown_key")
    if "as_of" not in params or params["as_of"] is None:
        _fail(MISSING_AS_OF, "missing_as_of", path="as_of")
    if not _is_date(params["as_of"]):
        _fail(INVALID_DATE, "invalid_date", path="as_of")
    if "period" not in params:
        _fail(INVALID_REQUEST, "missing_field", path="period")
    if not isinstance(params["period"], dict):
        _fail(INVALID_REQUEST, "invalid_type", path="period")
    if "currency" not in params:
        _fail(INVALID_REQUEST, "missing_field", path="currency")
    if "chart_of_accounts" not in params:
        _fail(INVALID_REQUEST, "missing_field", path="chart_of_accounts")
    if not isinstance(params["chart_of_accounts"], list):
        _fail(INVALID_REQUEST, "invalid_type", path="chart_of_accounts")
    if "opening_balances" in params and not isinstance(
        params["opening_balances"], list
    ):
        _fail(INVALID_REQUEST, "invalid_type", path="opening_balances")
    if "journal" not in params:
        _fail(INVALID_REQUEST, "missing_field", path="journal")
    if not isinstance(params["journal"], list):
        _fail(INVALID_REQUEST, "invalid_type", path="journal")
    if "prior_journals" in params and not isinstance(params["prior_journals"], list):
        _fail(INVALID_REQUEST, "invalid_type", path="prior_journals")
    if "options" in params and not isinstance(params["options"], dict):
        _fail(INVALID_REQUEST, "invalid_type", path="options")


def _check_period(period: Dict[str, Any]) -> Tuple[date, date]:
    """Checks 7 to 9."""
    for key in PERIOD_KEYS:
        if key not in period:
            _fail(INVALID_PERIOD, "missing_field", path=f"period.{key}")
    if _unknown_keys(period, PERIOD_KEYS):
        _fail(INVALID_PERIOD, "unknown_key", path="period")
    for key in PERIOD_KEYS:
        if not _is_date(period[key]):
            _fail(INVALID_DATE, "invalid_date", path=f"period.{key}")
    start = date.fromisoformat(period["start"])
    end = date.fromisoformat(period["end"])
    if start > end:
        _fail(INVALID_PERIOD, "start_after_end", path="period")
    return start, end


def _check_currency(value: Any) -> str:
    """Check 10."""
    if not _is_string(value):
        _fail(INVALID_REQUEST, "invalid_type", path="currency")
    if not _CURRENCY_RE.fullmatch(value):
        _fail(INVALID_REQUEST, "currency_pattern", path="currency")
    return value


def _check_options(options: Dict[str, Any]) -> Tuple[bool, int, Decimal]:
    """Check 11."""
    if _unknown_keys(options, OPTION_KEYS):
        _fail(INVALID_REQUEST, "unknown_key", path="options")
    soft_close = options.get("soft_close", False)
    if not isinstance(soft_close, bool):
        _fail(INVALID_REQUEST, "invalid_type", path="options.soft_close")
    recurrence = options.get("recurrence_min_periods", DEFAULT_RECURRENCE_MIN_PERIODS)
    if not _is_int(recurrence):
        _fail(INVALID_REQUEST, "invalid_type", path="options.recurrence_min_periods")
    if not MIN_RECURRENCE_MIN_PERIODS <= recurrence <= MAX_RECURRENCE_MIN_PERIODS:
        _fail(INVALID_REQUEST, "out_of_range", path="options.recurrence_min_periods")
    threshold = options.get("deviation_threshold", DEFAULT_DEVIATION_THRESHOLD)
    if not _is_string(threshold):
        _fail(INVALID_REQUEST, "invalid_type", path="options.deviation_threshold")
    if not _THRESHOLD_RE.fullmatch(threshold):
        _fail(INVALID_REQUEST, "threshold_pattern", path="options.deviation_threshold")
    threshold_value = Decimal(threshold)
    if threshold_value <= _ZERO:
        _fail(INVALID_REQUEST, "out_of_range", path="options.deviation_threshold")
    return soft_close, recurrence, threshold_value


def _cap_fail(path: Optional[str], limit: int, actual: int) -> None:
    detail: Dict[str, Any] = {"reason": "size_cap"}
    if path is not None:
        detail["path"] = path
    detail["limit"] = limit
    detail["actual"] = actual
    raise _ContractError(INVALID_REQUEST, detail)


def _count_entry_lines(entry: Any, path: str) -> int:
    """The line count of one entry for the cap check; shapes are checked later."""
    if not isinstance(entry, dict) or "lines" not in entry:
        return 0
    lines = entry["lines"]
    if not isinstance(lines, list):
        _fail(INVALID_REQUEST, "invalid_type", path=f"{path}.lines")
    if len(lines) > MAX_LINES_PER_ENTRY:
        _cap_fail(f"{path}.lines", MAX_LINES_PER_ENTRY, len(lines))
    return len(lines)


def _check_size_caps(params: Dict[str, Any]) -> None:
    """Check 12: every cap is compared before any line is parsed."""
    chart = params["chart_of_accounts"]
    if len(chart) > MAX_CHART_ROWS:
        _cap_fail("chart_of_accounts", MAX_CHART_ROWS, len(chart))
    opening = params.get("opening_balances", [])
    if len(opening) > MAX_OPENING_LINES:
        _cap_fail("opening_balances", MAX_OPENING_LINES, len(opening))
    journal = params["journal"]
    if len(journal) > MAX_JOURNAL_ENTRIES:
        _cap_fail("journal", MAX_JOURNAL_ENTRIES, len(journal))
    total = 0
    for index, entry in enumerate(journal):
        total += _count_entry_lines(entry, f"journal[{index}]")
    priors = params.get("prior_journals", [])
    if len(priors) > MAX_PRIOR_PERIODS:
        _cap_fail("prior_journals", MAX_PRIOR_PERIODS, len(priors))
    for index, prior in enumerate(priors):
        if not isinstance(prior, dict) or "journal" not in prior:
            continue
        path = f"prior_journals[{index}].journal"
        entries = prior["journal"]
        if not isinstance(entries, list):
            _fail(INVALID_REQUEST, "invalid_type", path=path)
        if len(entries) > MAX_PRIOR_ENTRIES_PER_PERIOD:
            _cap_fail(path, MAX_PRIOR_ENTRIES_PER_PERIOD, len(entries))
        for position, entry in enumerate(entries):
            total += _count_entry_lines(entry, f"{path}[{position}]")
    if total > MAX_TOTAL_LINES:
        _cap_fail(None, MAX_TOTAL_LINES, total)


class _Account:
    __slots__ = ("name", "cls", "subclass")

    def __init__(self, name: str, cls: str, subclass: Optional[str]):
        self.name = name
        self.cls = cls
        self.subclass = subclass


def _check_chart(chart: List[Any]) -> Dict[str, _Account]:
    """Checks 13 to 15, row by row."""
    accounts: Dict[str, _Account] = {}
    for index, row in enumerate(chart):
        path = f"chart_of_accounts[{index}]"
        if not isinstance(row, dict):
            _fail(INVALID_REQUEST, "not_an_object", path=path)
        if _unknown_keys(row, CHART_ROW_KEYS):
            _fail(INVALID_REQUEST, "unknown_key", path=path)
        if "account" not in row:
            _fail(INVALID_REQUEST, "missing_field", path=f"{path}.account")
        name = row["account"]
        if not _is_string(name):
            _fail(INVALID_REQUEST, "invalid_type", path=f"{path}.account")
        if not _ACCOUNT_RE.fullmatch(name):
            _fail(INVALID_REQUEST, "account_pattern", path=f"{path}.account")
        if name in accounts:
            _fail(INVALID_REQUEST, "duplicate_account", path=f"{path}.account")
        if "class" not in row:
            _fail(UNKNOWN_ACCOUNT_CLASS, "missing_field", path=f"{path}.class")
        cls = row["class"]
        if not _is_string(cls) or cls not in CLASSES:
            _fail(UNKNOWN_ACCOUNT_CLASS, "unknown_class", path=f"{path}.class")
        subclass = DEFAULT_SUBCLASS[cls]
        if "subclass" in row:
            subclass = row["subclass"]
            if not _is_string(subclass) or subclass not in SUBCLASSES[cls]:
                _fail(UNKNOWN_SUBCLASS, "unknown_subclass", path=f"{path}.subclass")
        accounts[name] = _Account(name, cls, subclass)
    return accounts


def _check_amount(line: Dict[str, Any], path: str, key: str) -> Decimal:
    if key not in line:
        _fail(INVALID_AMOUNT, "missing_field", path=f"{path}.{key}")
    if not _is_amount(line[key]):
        _fail(INVALID_AMOUNT, "invalid_amount", path=f"{path}.{key}")
    return Decimal(line[key])


class _Line:
    __slots__ = ("account", "debit", "credit")

    def __init__(self, account: str, debit: Decimal, credit: Decimal):
        self.account = account
        self.debit = debit
        self.credit = credit


def _check_opening(opening: List[Any], accounts: Dict[str, _Account]) -> List[_Line]:
    """Checks 16 and 17, line by line."""
    lines: List[_Line] = []
    seen = set()
    for index, line in enumerate(opening):
        path = f"opening_balances[{index}]"
        if not isinstance(line, dict):
            _fail(INVALID_REQUEST, "not_an_object", path=path)
        if _unknown_keys(line, LINE_KEYS):
            _fail(INVALID_REQUEST, "unknown_key", path=path)
        if "account" not in line:
            _fail(INVALID_REQUEST, "missing_field", path=f"{path}.account")
        name = line["account"]
        if not _is_string(name):
            _fail(INVALID_REQUEST, "invalid_type", path=f"{path}.account")
        if not _ACCOUNT_RE.fullmatch(name):
            _fail(INVALID_REQUEST, "account_pattern", path=f"{path}.account")
        account = accounts.get(name)
        if account is not None and account.cls in PROFIT_AND_LOSS_CLASSES:
            _fail(
                INVALID_REQUEST, "not_a_balance_sheet_account", path=f"{path}.account"
            )
        if name in seen:
            _fail(INVALID_REQUEST, "duplicate_account", path=f"{path}.account")
        seen.add(name)
        debit = _check_amount(line, path, "debit")
        credit = _check_amount(line, path, "credit")
        lines.append(_Line(name, debit, credit))
    return lines


class _Entry:
    __slots__ = ("id", "date", "counterparty", "lines")

    def __init__(
        self, entry_id: str, when: date, counterparty: Optional[str], lines: List[_Line]
    ):
        self.id = entry_id
        self.date = when
        self.counterparty = counterparty
        self.lines = lines


def _check_entry_shape(
    entry: Any, path: str, accounts: Optional[Dict[str, _Account]]
) -> None:
    """Check 19, and check 27 when ``accounts`` is given (prior entries)."""
    if not isinstance(entry, dict):
        _fail(INVALID_REQUEST, "not_an_object", path=path)
    if _unknown_keys(entry, ENTRY_KEYS):
        _fail(INVALID_REQUEST, "unknown_key", path=path)
    if "id" not in entry:
        _fail(INVALID_REQUEST, "missing_field", path=f"{path}.id")
    if not _is_string(entry["id"]):
        _fail(INVALID_REQUEST, "invalid_type", path=f"{path}.id")
    if not _ENTRY_ID_RE.fullmatch(entry["id"]):
        _fail(INVALID_REQUEST, "id_pattern", path=f"{path}.id")
    if "counterparty" in entry:
        if not _is_string(entry["counterparty"]):
            _fail(INVALID_REQUEST, "invalid_type", path=f"{path}.counterparty")
        if not _COUNTERPARTY_RE.fullmatch(entry["counterparty"]):
            _fail(INVALID_REQUEST, "counterparty_pattern", path=f"{path}.counterparty")
    if "currency" in entry and not _is_string(entry["currency"]):
        _fail(INVALID_REQUEST, "invalid_type", path=f"{path}.currency")
    if "lines" not in entry:
        _fail(INVALID_REQUEST, "missing_field", path=f"{path}.lines")
    if not isinstance(entry["lines"], list):
        _fail(INVALID_REQUEST, "invalid_type", path=f"{path}.lines")
    for index, line in enumerate(entry["lines"]):
        line_path = f"{path}.lines[{index}]"
        if not isinstance(line, dict):
            _fail(INVALID_REQUEST, "not_an_object", path=line_path)
        if _unknown_keys(line, LINE_KEYS):
            _fail(INVALID_REQUEST, "unknown_key", path=line_path)
        if "account" not in line:
            _fail(INVALID_REQUEST, "missing_field", path=f"{line_path}.account")
        if not _is_string(line["account"]):
            _fail(INVALID_REQUEST, "invalid_type", path=f"{line_path}.account")
        if not _ACCOUNT_RE.fullmatch(line["account"]):
            _fail(INVALID_REQUEST, "account_pattern", path=f"{line_path}.account")
        if accounts is not None and line["account"] not in accounts:
            _fail(INVALID_REQUEST, "unmapped_account", path=f"{line_path}.account")


def _check_entry_date(entry: Dict[str, Any], path: str) -> date:
    if "date" not in entry:
        _fail(INVALID_DATE, "missing_field", path=f"{path}.date")
    if not _is_date(entry["date"]):
        _fail(INVALID_DATE, "invalid_date", path=f"{path}.date")
    return date.fromisoformat(entry["date"])


def _check_entry_currency(entry: Dict[str, Any], path: str, currency: str) -> None:
    if "currency" in entry and entry["currency"] != currency:
        _fail(CURRENCY_MISMATCH, "currency_mismatch", path=f"{path}.currency")


def _check_entry_lines(entry: Dict[str, Any], path: str) -> List[_Line]:
    lines: List[_Line] = []
    for index, line in enumerate(entry["lines"]):
        line_path = f"{path}.lines[{index}]"
        debit = _check_amount(line, line_path, "debit")
        credit = _check_amount(line, line_path, "credit")
        lines.append(_Line(line["account"], debit, credit))
    return lines


def _check_journal(journal: List[Any], currency: str) -> List[_Entry]:
    """Checks 18 to 22, entry by entry."""
    if not journal:
        _fail(EMPTY_JOURNAL, "empty_journal", path="journal")
    entries: List[_Entry] = []
    for index, entry in enumerate(journal):
        path = f"journal[{index}]"
        _check_entry_shape(entry, path, None)
        when = _check_entry_date(entry, path)
        _check_entry_currency(entry, path, currency)
        lines = _check_entry_lines(entry, path)
        entries.append(_Entry(entry["id"], when, entry.get("counterparty"), lines))
    return entries


class _PriorPeriod:
    __slots__ = ("start", "end", "entries")

    def __init__(self, start: date, end: date, entries: List[_Entry]):
        self.start = start
        self.end = end
        self.entries = entries

    @property
    def length(self) -> int:
        return _days(self.start, self.end)


def _check_prior_journals(
    priors: List[Any],
    currency: str,
    period_start: date,
    period_length: int,
    accounts: Dict[str, _Account],
) -> List[_PriorPeriod]:
    """Checks 23 to 31, period by period and then entry by entry."""
    result: List[_PriorPeriod] = []
    for index, prior in enumerate(priors):
        path = f"prior_journals[{index}]"
        if not isinstance(prior, dict):
            _fail(INVALID_REQUEST, "not_an_object", path=path)
        if _unknown_keys(prior, PRIOR_PERIOD_KEYS):
            _fail(INVALID_REQUEST, "unknown_key", path=path)
        if "period" not in prior:
            _fail(INVALID_REQUEST, "missing_field", path=f"{path}.period")
        period = prior["period"]
        if not isinstance(period, dict):
            _fail(INVALID_REQUEST, "invalid_type", path=f"{path}.period")
        for key in PERIOD_KEYS:
            if key not in period:
                _fail(INVALID_REQUEST, "missing_field", path=f"{path}.period.{key}")
        if _unknown_keys(period, PERIOD_KEYS):
            _fail(INVALID_REQUEST, "unknown_key", path=f"{path}.period")
        if "journal" not in prior:
            _fail(INVALID_REQUEST, "missing_field", path=f"{path}.journal")
        if not isinstance(prior["journal"], list):
            _fail(INVALID_REQUEST, "invalid_type", path=f"{path}.journal")
        if "currency" in prior and not _is_string(prior["currency"]):
            _fail(INVALID_REQUEST, "invalid_type", path=f"{path}.currency")
        for key in PERIOD_KEYS:
            if not _is_date(period[key]):
                _fail(INVALID_DATE, "invalid_date", path=f"{path}.period.{key}")
        start = date.fromisoformat(period["start"])
        end = date.fromisoformat(period["end"])
        if start > end:
            _fail(INVALID_PERIOD, "start_after_end", path=f"{path}.period")
        if end >= period_start:
            _fail(INVALID_PERIOD, "not_before_current_period", path=f"{path}.period")
        for earlier in result:
            if start <= earlier.end and earlier.start <= end:
                _fail(INVALID_PERIOD, "overlaps_prior_period", path=f"{path}.period")
        difference = abs(_days(start, end) - period_length)
        if difference > PRIOR_PERIOD_LENGTH_TOLERANCE_DAYS:
            _fail(
                INVALID_PERIOD,
                "length_differs",
                path=f"{path}.period",
                limit=PRIOR_PERIOD_LENGTH_TOLERANCE_DAYS,
                actual=difference,
            )
        if "currency" in prior and prior["currency"] != currency:
            _fail(CURRENCY_MISMATCH, "currency_mismatch", path=f"{path}.currency")
        entries: List[_Entry] = []
        for position, entry in enumerate(prior["journal"]):
            entry_path = f"{path}.journal[{position}]"
            _check_entry_shape(entry, entry_path, accounts)
            when = _check_entry_date(entry, entry_path)
            if when < start or when > end:
                _fail(INVALID_PERIOD, "entry_outside_period", path=f"{entry_path}.date")
            _check_entry_currency(entry, entry_path, currency)
            lines = _check_entry_lines(entry, entry_path)
            entries.append(_Entry(entry["id"], when, entry.get("counterparty"), lines))
        result.append(_PriorPeriod(start, end, entries))
    return result


class _Request:
    __slots__ = (
        "as_of",
        "start",
        "end",
        "currency",
        "chart",
        "accounts",
        "opening",
        "entries",
        "priors",
        "soft_close",
        "recurrence_min_periods",
        "deviation_threshold",
    )

    def __init__(self) -> None:
        self.chart: List[_Account] = []
        self.accounts: Dict[str, _Account] = {}
        self.opening: List[_Line] = []
        self.entries: List[_Entry] = []
        self.priors: List[_PriorPeriod] = []

    @property
    def length(self) -> int:
        return _days(self.start, self.end)


def _validate_request(params: Dict[str, Any]) -> _Request:
    request = _Request()
    _check_request_shape(params)
    request.as_of = date.fromisoformat(params["as_of"])
    request.start, request.end = _check_period(params["period"])
    request.currency = _check_currency(params["currency"])
    (
        request.soft_close,
        request.recurrence_min_periods,
        request.deviation_threshold,
    ) = _check_options(params.get("options", {}))
    _check_size_caps(params)
    request.accounts = _check_chart(params["chart_of_accounts"])
    request.chart = list(request.accounts.values())
    request.opening = _check_opening(
        params.get("opening_balances", []), request.accounts
    )
    request.entries = _check_journal(params["journal"], request.currency)
    request.priors = _check_prior_journals(
        params.get("prior_journals", []),
        request.currency,
        request.start,
        request.length,
        request.accounts,
    )
    return request


# --------------------------------------------------------------------------
# Books: balances over the entries dated within the period, chart lines only
# --------------------------------------------------------------------------


class _Balance:
    __slots__ = ("opening", "debit", "credit")

    def __init__(self) -> None:
        self.opening = _ZERO
        self.debit = _ZERO
        self.credit = _ZERO


class _Books:
    __slots__ = (
        "balances",
        "closing",
        "inflows",
        "outflows",
        "equity_movements",
        "in_period",
    )

    def __init__(self) -> None:
        self.balances: Dict[str, _Balance] = {}
        self.closing: Dict[str, Decimal] = {}
        self.inflows = _ZERO
        self.outflows = _ZERO
        self.equity_movements = _ZERO
        self.in_period: List[_Entry] = []


def _natural(account: _Account, debit: Decimal, credit: Decimal) -> Decimal:
    """A movement in the account's natural sign."""
    if account.cls in DEBIT_NORMAL_CLASSES:
        return _subtract(debit, credit)
    return _subtract(credit, debit)


def _compute_books(request: _Request) -> _Books:
    books = _Books()
    for account in request.chart:
        books.balances[account.name] = _Balance()
    for line in request.opening:
        account = request.accounts.get(line.account)
        if account is None:
            continue
        balance = books.balances[line.account]
        balance.opening = _add(
            balance.opening, _natural(account, line.debit, line.credit)
        )
    for entry in request.entries:
        if entry.date < request.start or entry.date > request.end:
            continue
        books.in_period.append(entry)
        cash_net = _ZERO
        for line in entry.lines:
            account = request.accounts.get(line.account)
            if account is None:
                continue
            balance = books.balances[line.account]
            balance.debit = _add(balance.debit, line.debit)
            balance.credit = _add(balance.credit, line.credit)
            if account.cls == CLASS_ASSET and account.subclass == SUBCLASS_CASH:
                cash_net = _add(cash_net, _subtract(line.debit, line.credit))
            if account.cls == CLASS_EQUITY:
                books.equity_movements = _add(
                    books.equity_movements, _subtract(line.credit, line.debit)
                )
        if cash_net > _ZERO:
            books.inflows = _add(books.inflows, cash_net)
        elif cash_net < _ZERO:
            books.outflows = _subtract(books.outflows, cash_net)
    for account in request.chart:
        balance = books.balances[account.name]
        books.closing[account.name] = _add(
            balance.opening, _natural(account, balance.debit, balance.credit)
        )
    return books


# --------------------------------------------------------------------------
# Findings and gap notes
# --------------------------------------------------------------------------


class _PairHistory:
    __slots__ = ("amounts", "counts", "lengths", "last_seen")

    def __init__(self) -> None:
        self.amounts: List[Decimal] = []
        self.counts: List[int] = []
        self.lengths: List[int] = []
        self.last_seen: Optional[date] = None


def _pair_amounts(
    entries: List[_Entry], accounts: Dict[str, _Account]
) -> Dict[Tuple[str, str], Tuple[Decimal, int, date]]:
    """Per (account, counterparty) pair: amount in natural sign, entry count, latest date."""
    result: Dict[Tuple[str, str], Tuple[Decimal, int, date]] = {}
    for entry in entries:
        if entry.counterparty is None:
            continue
        touched = set()
        for line in entry.lines:
            account = accounts.get(line.account)
            if account is None or account.cls not in PROFIT_AND_LOSS_CLASSES:
                continue
            pair = (line.account, entry.counterparty)
            amount, count, latest = result.get(pair, (_ZERO, 0, entry.date))
            result[pair] = (
                _add(amount, _natural(account, line.debit, line.credit)),
                count,
                latest,
            )
            touched.add(pair)
        for pair in touched:
            amount, count, latest = result[pair]
            result[pair] = (amount, count + 1, max(latest, entry.date))
    return result


def _cadence(history: _PairHistory) -> str:
    days = _divide(
        _median([Decimal(length) for length in history.lengths]),
        _median([Decimal(count) for count in history.counts]),
    )
    if days <= CADENCE_WEEKLY_MAX_DAYS:
        return CADENCE_WEEKLY
    if days <= CADENCE_MONTHLY_MAX_DAYS:
        return CADENCE_MONTHLY
    if days <= CADENCE_QUARTERLY_MAX_DAYS:
        return CADENCE_QUARTERLY
    return CADENCE_LONGER


def _gap_notes(request: _Request, books: _Books) -> List[Dict[str, Any]]:
    """EXPECTED_MISSING and AMOUNT_DEVIATION notes, in registry order."""
    histories: Dict[Tuple[str, str], _PairHistory] = {}
    for prior in request.priors:
        for pair, (amount, count, latest) in _pair_amounts(
            prior.entries, request.accounts
        ).items():
            history = histories.setdefault(pair, _PairHistory())
            history.amounts.append(amount)
            history.counts.append(count)
            history.lengths.append(prior.length)
            if history.last_seen is None or latest > history.last_seen:
                history.last_seen = latest
    current = _pair_amounts(books.in_period, request.accounts)
    missing: List[Dict[str, Any]] = []
    deviations: List[Dict[str, Any]] = []
    supplied = len(request.priors)
    for pair in sorted(histories, key=lambda item: f"{item[0]}/{item[1]}"):
        history = histories[pair]
        seen = len(history.amounts)
        if seen < request.recurrence_min_periods:
            continue
        median = _quantize_amount(_median(history.amounts))
        account, counterparty = pair
        if pair not in current:
            missing.append(
                {
                    "code": EXPECTED_MISSING,
                    "account": account,
                    "counterparty": counterparty,
                    "cadence": _cadence(history),
                    "last_seen": history.last_seen.isoformat(),
                    "expected_amount": _render_amount(median),
                    "detail": (
                        f"seen in {seen} of the last {supplied} periods, "
                        "absent in this one"
                    ),
                }
            )
            continue
        if median.is_zero():
            continue
        amount = current[pair][0]
        gap = _subtract(amount, median).copy_abs()
        if gap > _multiply(request.deviation_threshold, median.copy_abs()):
            deviations.append(
                {
                    "code": AMOUNT_DEVIATION,
                    "account": account,
                    "counterparty": counterparty,
                    "amount": _render_amount(amount),
                    "median": _render_amount(median),
                    "deviation": _render_ratio(_divide(gap, median.copy_abs())),
                }
            )
    return missing + deviations


class _Findings:
    __slots__ = ("refs", "opening_unbalanced", "notes")

    def __init__(self) -> None:
        self.refs: Dict[str, set] = {code: set() for code in FINDING_CODES}
        self.opening_unbalanced = False
        self.notes: List[Dict[str, Any]] = []

    def rendered(self) -> List[Dict[str, Any]]:
        """One element per code that fired, in registry order; refs unique and sorted."""
        result = []
        for code in FINDING_CODES:
            refs = self.refs[code]
            if code == OPENING_UNBALANCED:
                if self.opening_unbalanced:
                    result.append(
                        {
                            "code": code,
                            "severity": FINDING_SEVERITY[code],
                            "count": 1,
                            "refs": [],
                        }
                    )
                continue
            if not refs:
                continue
            ordered = sorted(refs)
            result.append(
                {
                    "code": code,
                    "severity": FINDING_SEVERITY[code],
                    "count": len(ordered),
                    "refs": ordered,
                }
            )
        return result

    def blocking(self) -> List[Dict[str, Any]]:
        return [
            {"code": item["code"], "refs": item["refs"]}
            for item in self.rendered()
            if item["severity"] == SEVERITY_BLOCKING
        ]

    def fired_any(self) -> bool:
        return any(item["severity"] != SEVERITY_INFO for item in self.rendered())


def _compute_findings(request: _Request, books: _Books) -> _Findings:
    findings = _Findings()
    refs = findings.refs
    used = set()
    id_counts: Dict[str, int] = defaultdict(int)
    groups: Dict[Tuple[Any, ...], List[str]] = defaultdict(list)
    for entry in request.entries:
        id_counts[entry.id] += 1
        debits = _total([line.debit for line in entry.lines])
        credits = _total([line.credit for line in entry.lines])
        if debits != credits:
            refs[UNBALANCED_ENTRY].add(entry.id)
        for line in entry.lines:
            used.add(line.account)
            if line.account not in request.accounts:
                refs[UNMAPPED_ACCOUNT].add(line.account)
            if line.debit.is_zero() and line.credit.is_zero():
                refs[ZERO_LINE].add(entry.id)
        if entry.date < request.start or entry.date > request.end:
            refs[ENTRY_OUT_OF_PERIOD].add(entry.id)
        if entry.date > request.as_of:
            refs[ENTRY_AFTER_AS_OF].add(entry.id)
        shape = tuple(
            sorted((line.account, line.debit, line.credit) for line in entry.lines)
        )
        groups[(entry.date, entry.counterparty, shape)].append(entry.id)
    for entry_id, count in id_counts.items():
        if count > 1:
            refs[DUPLICATE_ENTRY_ID].add(entry_id)
    for ids in groups.values():
        if len(set(ids)) >= 2:
            refs[SUSPECTED_DUPLICATE].update(ids)
    opening_debits = _total([line.debit for line in request.opening])
    opening_credits = _total([line.credit for line in request.opening])
    for line in request.opening:
        used.add(line.account)
        if line.account not in request.accounts:
            refs[UNMAPPED_ACCOUNT].add(line.account)
    findings.opening_unbalanced = opening_debits != opening_credits
    for account in request.chart:
        if account.cls == CLASS_ASSET and account.subclass == SUBCLASS_CASH:
            if books.closing[account.name] < _ZERO:
                refs[NEGATIVE_CASH].add(account.name)
    for prior in request.priors:
        for entry in prior.entries:
            for line in entry.lines:
                used.add(line.account)
    for account in request.chart:
        if account.name not in used:
            refs[UNUSED_ACCOUNT].add(account.name)
    findings.notes = _gap_notes(request, books)
    for note in findings.notes:
        refs[note["code"]].add(f"{note['account']}/{note['counterparty']}")
    return findings


# --------------------------------------------------------------------------
# Statements, ratios and the close
# --------------------------------------------------------------------------


def _accounts_of(request: _Request, cls: str, subclass: Optional[str] = None):
    return [
        account
        for account in request.chart
        if account.cls == cls and (subclass is None or account.subclass == subclass)
    ]


def _closing_total(books: _Books, accounts: List[_Account]) -> Decimal:
    return _total([books.closing[account.name] for account in accounts])


def _close(request: _Request, books: _Books, findings: _Findings) -> Dict[str, Any]:
    """Checks 32 and 33, then the statements."""
    if request.as_of < request.end:
        _fail(CLOSE_BEFORE_PERIOD_END, "close_before_period_end", path="as_of")
    blocking = findings.blocking()
    if blocking and not request.soft_close:
        raise _ContractError(CLOSE_BLOCKED, {"findings": blocking})

    trial_balance = []
    for account in request.chart:
        balance = books.balances[account.name]
        trial_balance.append(
            {
                "account": account.name,
                "class": account.cls,
                "opening": _render_amount(balance.opening),
                "debit": _render_amount(balance.debit),
                "credit": _render_amount(balance.credit),
                "closing": _render_amount(books.closing[account.name]),
            }
        )

    revenue_accounts = _accounts_of(request, CLASS_REVENUE)
    expense_accounts = _accounts_of(request, CLASS_EXPENSE)
    cogs_accounts = _accounts_of(request, CLASS_EXPENSE, SUBCLASS_COGS)
    other_expense_accounts = [
        account for account in expense_accounts if account.subclass != SUBCLASS_COGS
    ]
    revenue_sum = _closing_total(books, revenue_accounts)
    expense_sum = _closing_total(books, expense_accounts)
    revenue = revenue_sum if revenue_accounts else None
    cogs = _closing_total(books, cogs_accounts) if cogs_accounts else None
    expenses = (
        _closing_total(books, other_expense_accounts)
        if other_expense_accounts
        else None
    )
    gross_profit = None if cogs is None else _subtract(revenue_sum, cogs)
    net_income = _subtract(revenue_sum, expense_sum)

    assets = _closing_total(books, _accounts_of(request, CLASS_ASSET))
    liabilities = _closing_total(books, _accounts_of(request, CLASS_LIABILITY))
    equity_accounts = _accounts_of(request, CLASS_EQUITY)
    equity_opening = _total(
        [books.balances[account.name].opening for account in equity_accounts]
    )
    equity_closing = _add(_add(equity_opening, books.equity_movements), net_income)
    balanced = assets == _add(liabilities, equity_closing)

    cash_accounts = _accounts_of(request, CLASS_ASSET, SUBCLASS_CASH)
    if cash_accounts:
        cash_opening: Optional[Decimal] = _total(
            [books.balances[account.name].opening for account in cash_accounts]
        )
        inflows: Optional[Decimal] = books.inflows
        outflows: Optional[Decimal] = books.outflows
        cash_closing: Optional[Decimal] = _closing_total(books, cash_accounts)
    else:
        cash_opening = inflows = outflows = cash_closing = None

    revenue_usable = revenue is not None and not revenue.is_zero()
    gross_margin = (
        _divide(gross_profit, revenue)
        if gross_profit is not None and revenue_usable
        else None
    )
    operating_margin = None
    if revenue_usable:
        operating = _subtract(
            _subtract(
                _closing_total(
                    books, _accounts_of(request, CLASS_REVENUE, SUBCLASS_OPERATING)
                ),
                _closing_total(books, cogs_accounts),
            ),
            _closing_total(
                books, _accounts_of(request, CLASS_EXPENSE, SUBCLASS_OPERATING)
            ),
        )
        operating_margin = _divide(operating, revenue)
    net_margin = _divide(net_income, revenue) if revenue_usable else None
    current_assets = _closing_total(
        books,
        [
            account
            for account in _accounts_of(request, CLASS_ASSET)
            if account.subclass != SUBCLASS_FIXED
        ],
    )
    current_liabilities = _closing_total(
        books,
        [
            account
            for account in _accounts_of(request, CLASS_LIABILITY)
            if account.subclass != SUBCLASS_LONG_TERM
        ],
    )
    current_ratio = (
        None
        if current_liabilities.is_zero()
        else _divide(current_assets, current_liabilities)
    )
    runway_months = None
    if cash_accounts:
        # One division of exact operands, rounded once by the quantisation:
        # cash_closing times the length in days over net burn times 30.4375.
        net_burn = _subtract(outflows, inflows)
        if net_burn > _ZERO:
            runway_months = _divide(
                _multiply(cash_closing, Decimal(request.length)),
                _multiply(net_burn, DAYS_PER_MONTH),
            )
    concentration = None
    if expense_accounts:
        closings = [books.closing[account.name] for account in expense_accounts]
        concentration = (
            _ZERO if expense_sum.is_zero() else _divide(max(closings), expense_sum)
        )

    ratios = {
        "gross_margin": _render_optional_ratio(gross_margin),
        "operating_margin": _render_optional_ratio(operating_margin),
        "net_margin": _render_optional_ratio(net_margin),
        "current_ratio": _render_optional_ratio(current_ratio),
        "runway_months": _render_optional_ratio(runway_months),
        "expense_concentration_top1": _render_optional_ratio(concentration),
    }
    rendered_revenue = _render_optional_amount(revenue)
    rendered_cash_closing = _render_optional_amount(cash_closing)
    notes = blocking + findings.notes if request.soft_close else list(findings.notes)
    return {
        "status": "completed",
        "closed": not request.soft_close,
        "provisional": request.soft_close,
        "trial_balance": trial_balance,
        "income_statement": {
            "revenue": rendered_revenue,
            "cogs": _render_optional_amount(cogs),
            "gross_profit": _render_optional_amount(gross_profit),
            "expenses": _render_optional_amount(expenses),
            "net_income": _render_amount(net_income),
        },
        "balance_sheet": {
            "assets": _render_amount(assets),
            "liabilities": _render_amount(liabilities),
            "equity_opening": _render_amount(equity_opening),
            "equity_movements": _render_amount(books.equity_movements),
            "net_income": _render_amount(net_income),
            "equity_closing": _render_amount(equity_closing),
            "balanced": balanced,
        },
        "cash_flow": {
            "cash_opening": _render_optional_amount(cash_opening),
            "inflows": _render_optional_amount(inflows),
            "outflows": _render_optional_amount(outflows),
            "cash_closing": rendered_cash_closing,
        },
        "checks": findings.rendered(),
        "notes": notes,
        "ratios": ratios,
        "observations": [
            {
                "metric": "revenue",
                "value": rendered_revenue,
                "as_of": request.end.isoformat(),
            },
            {
                "metric": "cash_closing",
                "value": rendered_cash_closing,
                "as_of": request.end.isoformat(),
            },
        ],
        "fired_any": findings.fired_any(),
        "insufficient": any(value is None for value in ratios.values()),
    }


def _validate(request: _Request, findings: _Findings) -> Dict[str, Any]:
    return {
        "status": "completed",
        "findings": findings.rendered(),
        "blocking": bool(findings.blocking()),
    }


def _dispatch(params: Any) -> Dict[str, Any]:
    if not isinstance(params, dict):
        _fail(INVALID_REQUEST, "parameters_not_object")
    action = params.get("action")
    if not _is_string(action) or action not in ACTIONS:
        _fail(INVALID_REQUEST, "unknown_action", path="action")
    request = _validate_request(params)
    books = _compute_books(request)
    findings = _compute_findings(request, books)
    if action == ACTION_VALIDATE:
        return _validate(request, findings)
    return _close(request, books, findings)


class PeriodCloseSkill(BaseSkill):
    """Deterministic period close over a journal and a chart of accounts."""

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
