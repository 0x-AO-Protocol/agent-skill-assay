"""Run a registered financial model version on prepared tables; figures only.

The caller passes the registry document and the text of each prepared table
with the request. The skill checks the request and the whole registry
document, looks up the model, its version and the parameter revision, checks
that every table was marked passed upstream and still has its declared
SHA-256, parses the tables, and evaluates every quantity of the version in
declaration order. A model is data, never code: each quantity is a formula in
a closed arithmetic grammar, parsed here by hand and evaluated in one private
decimal context. The run returns the requested quantities, rounded half-up to
their declared decimals, and a canonical archive document for the caller to
store. The verify action rebuilds that document and compares it with a stored
copy, character for character.

execute() makes no model call and uses no network, clock, file or
environment. A contract violation returns an error envelope with a code from
the closed registry below and a fixed reason; an unexpected internal fault
returns the one-code fault registry entry INTERNAL_ERROR.
"""

import hashlib
import json
import re
from decimal import (
    ROUND_HALF_UP,
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
)
from typing import Any, Dict, List, Optional, Tuple

from skill_assay.core.base_skill import BaseSkill

REGISTRY_ID = "finance/approved_financial_model_runner"
VERSION = "0.1.0"
ARCHIVE_SCHEMA = "approved_financial_model_runner.archive/1"

ACTION_RUN = "run"
ACTION_VERIFY = "verify"
ACTIONS = (ACTION_RUN, ACTION_VERIFY)

# --------------------------------------------------------------------------
# Closed registries
# --------------------------------------------------------------------------

INVALID_REQUEST = "INVALID_REQUEST"
REGISTRY_INVALID = "REGISTRY_INVALID"
MODEL_DEFINITION_INVALID = "MODEL_DEFINITION_INVALID"
MODEL_NOT_REGISTERED = "MODEL_NOT_REGISTERED"
VERSION_NOT_REGISTERED = "VERSION_NOT_REGISTERED"
REVISION_NOT_REGISTERED = "REVISION_NOT_REGISTERED"
QUANTITY_NOT_DEFINED = "QUANTITY_NOT_DEFINED"
INPUT_BINDING_MISMATCH = "INPUT_BINDING_MISMATCH"
INPUT_NOT_VALIDATED = "INPUT_NOT_VALIDATED"
DIGEST_MISMATCH = "DIGEST_MISMATCH"
INPUT_PARSE_ERROR = "INPUT_PARSE_ERROR"
MODEL_EVALUATION_ERROR = "MODEL_EVALUATION_ERROR"
ARCHIVE_MISMATCH = "ARCHIVE_MISMATCH"

# In check order: the first failing check returns its code.
ERROR_REGISTRY = (
    INVALID_REQUEST,
    REGISTRY_INVALID,
    MODEL_DEFINITION_INVALID,
    MODEL_NOT_REGISTERED,
    VERSION_NOT_REGISTERED,
    REVISION_NOT_REGISTERED,
    QUANTITY_NOT_DEFINED,
    INPUT_BINDING_MISMATCH,
    INPUT_NOT_VALIDATED,
    DIGEST_MISMATCH,
    INPUT_PARSE_ERROR,
    MODEL_EVALUATION_ERROR,
    ARCHIVE_MISMATCH,
)
INTERNAL_ERROR = "INTERNAL_ERROR"
FAULT_REGISTRY = (INTERNAL_ERROR,)

# --------------------------------------------------------------------------
# Shapes, caps and patterns
# --------------------------------------------------------------------------

REQUEST_KEYS = (
    "action",
    "model_name",
    "model_version",
    "parameter_revision",
    "input_files",
    "requested_quantities",
    "archive_label",
    "registry",
    "archive_document",
)
FILE_KEYS = ("file_ref", "input_alias", "sha256", "validation_status", "content")
REGISTRY_KEYS = ("registry_id", "models")
MODEL_KEYS = ("model_name", "versions")
VERSION_KEYS = ("model_version", "inputs", "parameters", "revisions", "quantities")
INPUT_KEYS = ("input_alias", "columns")
REVISION_KEYS = ("label", "values")
QUANTITY_KEYS = ("name", "unit", "decimals", "expression")
VALIDATION_STATUSES = ("passed", "failed")
RESERVED_ALIASES = ("param", "q")

MAX_INPUT_FILES = 20
MAX_FILE_REF = 512
MAX_CONTENT = 2_000_000
MAX_TOTAL_CONTENT = 8_000_000
MAX_REQUESTED = 100
MAX_DOCUMENT = 2_000_000
MAX_MODELS = 100
MAX_VERSIONS = 20
MAX_INPUTS = 20
MAX_COLUMNS = 50
MAX_PARAMETERS = 100
MAX_REVISIONS = 50
MAX_QUANTITIES = 100
MAX_DECIMALS = 12
MAX_EXPRESSION = 2000
MAX_TOKENS = 400
MAX_DEPTH = 32
MAX_EXPONENT = 64
MAX_ROWS = 100_000

AGGREGATES = ("sum", "mean", "min", "max", "first", "last", "count")
SCALARS = ("abs", "least", "greatest")

# Every pattern is ASCII-only and matched against the whole string.
_NAME_RE = re.compile(r"[a-z][a-z0-9_]{0,63}", re.ASCII)
_ALIAS_RE = re.compile(r"[a-z][a-z0-9_]{0,31}", re.ASCII)
_SEMVER_RE = re.compile(
    r"(?:0|[1-9][0-9]{0,5})\.(?:0|[1-9][0-9]{0,5})\.(?:0|[1-9][0-9]{0,5})", re.ASCII
)
_REVISION_RE = re.compile(r"rev-[1-9][0-9]{0,8}", re.ASCII)
_LABEL_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}", re.ASCII)
_SHA256_RE = re.compile(r"[a-f0-9]{64}", re.ASCII)
_UNIT_RE = re.compile(r"[A-Za-z0-9_%/.-]{1,32}", re.ASCII)
_DECIMAL_RE = re.compile(r"-?(?:0|[1-9][0-9]{0,17})(?:\.[0-9]{1,12})?", re.ASCII)
_LITERAL_RE = re.compile(r"(?:0|[1-9][0-9]{0,17})(?:\.[0-9]{1,12})?", re.ASCII)
_SURROGATE_RE = re.compile(r"[\ud800-\udfff]", re.ASCII)
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]", re.ASCII)
_TOKEN_RE = re.compile(
    r"(?P<space> +)|(?P<name>[a-z][a-z0-9_]*)"
    r"|(?P<number>[0-9]+(?:\.[0-9]+)?)|(?P<op>[-+*/^(),.])",
    re.ASCII,
)

# Every arithmetic operation goes through this private context, with every
# field set here, so neither the thread's context nor the default context is
# read.
_CTX = Context(
    prec=40,
    rounding=ROUND_HALF_UP,
    Emin=-999999,
    Emax=999999,
    capitals=1,
    clamp=0,
    flags=[],
    traps=[InvalidOperation, DivisionByZero, Overflow],
)
_ZERO = Decimal(0)
_ONE = Decimal(1)
_LIMIT = Decimal("1000000000000000000000000")
_QUANTA = (
    Decimal("1"),
    Decimal("0.1"),
    Decimal("0.01"),
    Decimal("0.001"),
    Decimal("0.0001"),
    Decimal("0.00001"),
    Decimal("0.000001"),
    Decimal("0.0000001"),
    Decimal("0.00000001"),
    Decimal("0.000000001"),
    Decimal("0.0000000001"),
    Decimal("0.00000000001"),
    Decimal("0.000000000001"),
)

PARAMETERS = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": list(ACTIONS),
            "description": (
                "run evaluates the model version and returns the figures and "
                "an archive record; verify rebuilds the same run and compares "
                "it with a stored archive document."
            ),
        },
        "model_name": {
            "type": "string",
            "description": "Required. The model to run, as named in the registry.",
        },
        "model_version": {
            "type": "string",
            "description": (
                "Required. The registered version, MAJOR.MINOR.PATCH: three "
                "parts joined by points, each 0 or 1 to 6 ASCII digits with no "
                "leading zero, and nothing else."
            ),
        },
        "parameter_revision": {
            "type": "string",
            "description": (
                "Required. The frozen parameter values to use: rev- and 1 to "
                "9 ASCII digits, the first of them 1 to 9, and nothing else. "
                "Values are never changed at run time."
            ),
        },
        "input_files": {
            "type": "array",
            "description": (
                "Required. 1 to 20 prepared tables, each with file_ref, "
                "input_alias, sha256, validation_status and content (the "
                "table text as stored)."
            ),
        },
        "requested_quantities": {
            "type": "array",
            "description": (
                "Required. 1 to 100 distinct quantity names to return, in the "
                "order wanted."
            ),
        },
        "archive_label": {
            "type": "string",
            "description": (
                "Optional. A label for the suggested archive location; "
                "unlabelled when absent."
            ),
        },
        "registry": {
            "type": "object",
            "description": (
                "Required. The registry document: models, versions, input "
                "columns, parameters, revisions and quantity formulas."
            ),
        },
        "archive_document": {
            "type": "string",
            "description": (
                "verify only: the archive document an earlier run returned "
                "and the caller stored."
            ),
        },
    },
    "required": [
        "action",
        "model_name",
        "model_version",
        "parameter_revision",
        "input_files",
        "requested_quantities",
        "registry",
    ],
    "additionalProperties": False,
}

MANIFEST = {"name": REGISTRY_ID, "version": VERSION, "parameters": PARAMETERS}


# --------------------------------------------------------------------------
# Contract errors and shape helpers
# --------------------------------------------------------------------------


class _ContractError(Exception):
    """Internal signal carrying a closed-registry code and its detail."""

    def __init__(self, code: str, detail: Dict[str, Any]):
        super().__init__(code)
        self.code = code
        self.detail = detail


def _fail(code: str, reason: str, field: Optional[str] = None) -> None:
    """Raise a contract error. The detail names fields and indexes only."""
    detail: Dict[str, Any] = {"reason": reason}
    if field is not None:
        detail["field"] = field
    raise _ContractError(code, detail)


def _object(value: Any, code: str, field: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        _fail(code, "not_an_object", field)
    return value


def _known_keys(
    value: Dict[str, Any], allowed: Tuple[str, ...], code: str, field: Optional[str]
) -> None:
    # The detail names the object, never the unknown key itself.
    for key in value:
        if key not in allowed:
            _fail(code, "unknown_key", field)


def _get(
    container: Dict[str, Any], key: str, code: str, prefix: str
) -> Tuple[Any, str]:
    path = f"{prefix}.{key}" if prefix else key
    if key not in container:
        _fail(code, "missing_field", path)
    return container[key], path


def _text(value: Any, pattern: Any, code: str, field: str) -> str:
    """A string that matches its ASCII pattern in full."""
    if not isinstance(value, str):
        _fail(code, "wrong_type", field)
    if pattern.fullmatch(value) is None:
        _fail(code, "invalid_value", field)
    return value


def _array(value: Any, code: str, field: str, low: int, high: int) -> List[Any]:
    if not isinstance(value, list):
        _fail(code, "wrong_type", field)
    if not low <= len(value) <= high:
        _fail(code, "count_out_of_range", field)
    return value


def _free_text(value: Any, field: str, low: int, high: int, table: bool = False) -> str:
    """A string with no grammar of its own: no lone surrogate, bounded length.

    Every such field but a table's content also holds no control character.
    """
    if not isinstance(value, str):
        _fail(INVALID_REQUEST, "wrong_type", field)
    if not table and _CONTROL_RE.search(value) is not None:
        _fail(INVALID_REQUEST, "invalid_value", field)
    if _SURROGATE_RE.search(value) is not None:
        _fail(INVALID_REQUEST, "invalid_value", field)
    if not low <= len(value) <= high:
        _fail(INVALID_REQUEST, "length_out_of_range", field)
    return value


# --------------------------------------------------------------------------
# Request validation (INVALID_REQUEST)
# --------------------------------------------------------------------------


class _File:
    __slots__ = ("file_ref", "alias", "sha256", "status", "content")

    def __init__(self) -> None:
        self.file_ref = ""
        self.alias = ""
        self.sha256 = ""
        self.status = ""
        self.content = ""


class _Request:
    __slots__ = (
        "action",
        "model_name",
        "model_version",
        "parameter_revision",
        "files",
        "requested",
        "label",
        "registry",
        "document",
    )

    def __init__(self) -> None:
        self.action = ""
        self.model_name = ""
        self.model_version = ""
        self.parameter_revision = ""
        self.files: List[_File] = []
        self.requested: List[str] = []
        self.label: Optional[str] = None
        self.registry: Dict[str, Any] = {}
        self.document: Optional[str] = None


def _check_file(entry: Any, index: int, aliases: List[str]) -> _File:
    code, prefix = INVALID_REQUEST, f"input_files[{index}]"
    entry = _object(entry, code, prefix)
    _known_keys(entry, FILE_KEYS, code, prefix)
    item = _File()
    value, path = _get(entry, "file_ref", code, prefix)
    item.file_ref = _free_text(value, path, 1, MAX_FILE_REF)
    segments = item.file_ref.split("/")
    if item.file_ref.startswith("/") or "\\" in item.file_ref:
        _fail(code, "invalid_value", path)
    if any(segment in ("", ".", "..") for segment in segments):
        _fail(code, "invalid_value", path)
    value, path = _get(entry, "input_alias", code, prefix)
    item.alias = _text(value, _ALIAS_RE, code, path)
    if item.alias in RESERVED_ALIASES:
        _fail(code, "reserved_name", path)
    if item.alias in aliases:
        _fail(code, "duplicate_entry", path)
    aliases.append(item.alias)
    value, path = _get(entry, "sha256", code, prefix)
    item.sha256 = _text(value, _SHA256_RE, code, path)
    value, path = _get(entry, "validation_status", code, prefix)
    if not isinstance(value, str):
        _fail(code, "wrong_type", path)
    if value not in VALIDATION_STATUSES:
        _fail(code, "invalid_value", path)
    item.status = value
    value, path = _get(entry, "content", code, prefix)
    item.content = _free_text(value, path, 0, MAX_CONTENT, table=True)
    return item


def _validate_request(params: Any) -> _Request:
    """Check the request field by field, in the order of the input table."""
    code = INVALID_REQUEST
    if not isinstance(params, dict):
        _fail(code, "not_an_object")
    _known_keys(params, REQUEST_KEYS, code, None)
    request = _Request()
    value, path = _get(params, "action", code, "")
    if not isinstance(value, str):
        _fail(code, "wrong_type", path)
    if value not in ACTIONS:
        _fail(code, "invalid_value", path)
    request.action = value
    value, path = _get(params, "model_name", code, "")
    request.model_name = _text(value, _NAME_RE, code, path)
    value, path = _get(params, "model_version", code, "")
    request.model_version = _text(value, _SEMVER_RE, code, path)
    value, path = _get(params, "parameter_revision", code, "")
    request.parameter_revision = _text(value, _REVISION_RE, code, path)
    value, path = _get(params, "input_files", code, "")
    entries = _array(value, code, path, 1, MAX_INPUT_FILES)
    aliases: List[str] = []
    for index, entry in enumerate(entries):
        request.files.append(_check_file(entry, index, aliases))
    if sum(len(item.content) for item in request.files) > MAX_TOTAL_CONTENT:
        _fail(code, "total_too_long", "input_files")
    value, path = _get(params, "requested_quantities", code, "")
    names = _array(value, code, path, 1, MAX_REQUESTED)
    for index, name in enumerate(names):
        item_path = f"requested_quantities[{index}]"
        name = _text(name, _NAME_RE, code, item_path)
        if name in request.requested:
            _fail(code, "duplicate_entry", item_path)
        request.requested.append(name)
    if "archive_label" in params:
        request.label = _text(params["archive_label"], _LABEL_RE, code, "archive_label")
    value, path = _get(params, "registry", code, "")
    request.registry = _object(value, code, path)
    if request.action == ACTION_RUN and "archive_document" in params:
        _fail(code, "field_not_allowed", "archive_document")
    if request.action == ACTION_VERIFY:
        value, path = _get(params, "archive_document", code, "")
        request.document = _free_text(value, path, 1, MAX_DOCUMENT)
    return request


# --------------------------------------------------------------------------
# Registry structure (REGISTRY_INVALID), over the whole document
# --------------------------------------------------------------------------


def _check_names(
    values: List[Any], path: str, pattern: Any, reserved: Tuple[str, ...] = ()
) -> List[str]:
    """Distinct names; a repeat fails at its own index."""
    seen: List[str] = []
    for index, value in enumerate(values):
        item_path = f"{path}[{index}]"
        name = _text(value, pattern, REGISTRY_INVALID, item_path)
        if name in reserved:
            _fail(REGISTRY_INVALID, "reserved_name", item_path)
        if name in seen:
            _fail(REGISTRY_INVALID, "duplicate_entry", item_path)
        seen.append(name)
    return seen


def _check_inputs(version: Dict[str, Any], prefix: str) -> None:
    code = REGISTRY_INVALID
    value, path = _get(version, "inputs", code, prefix)
    aliases: List[str] = []
    for index, entry in enumerate(_array(value, code, path, 1, MAX_INPUTS)):
        entry_path = f"{path}[{index}]"
        entry = _object(entry, code, entry_path)
        _known_keys(entry, INPUT_KEYS, code, entry_path)
        value, alias_path = _get(entry, "input_alias", code, entry_path)
        alias = _text(value, _ALIAS_RE, code, alias_path)
        if alias in RESERVED_ALIASES:
            _fail(code, "reserved_name", alias_path)
        if alias in aliases:
            _fail(code, "duplicate_entry", alias_path)
        aliases.append(alias)
        value, columns_path = _get(entry, "columns", code, entry_path)
        columns = _array(value, code, columns_path, 1, MAX_COLUMNS)
        _check_names(columns, columns_path, _NAME_RE)


def _check_revisions(
    version: Dict[str, Any], prefix: str, parameters: List[str]
) -> None:
    code = REGISTRY_INVALID
    value, path = _get(version, "revisions", code, prefix)
    labels: List[str] = []
    for index, entry in enumerate(_array(value, code, path, 1, MAX_REVISIONS)):
        entry_path = f"{path}[{index}]"
        entry = _object(entry, code, entry_path)
        _known_keys(entry, REVISION_KEYS, code, entry_path)
        value, label_path = _get(entry, "label", code, entry_path)
        label = _text(value, _REVISION_RE, code, label_path)
        if label in labels:
            _fail(code, "duplicate_entry", label_path)
        labels.append(label)
        value, values_path = _get(entry, "values", code, entry_path)
        values = _object(value, code, values_path)
        if any(key not in parameters for key in values):
            _fail(code, "values_mismatch", values_path)
        if any(name not in values for name in parameters):
            _fail(code, "values_mismatch", values_path)
        for name in parameters:
            _text(values[name], _DECIMAL_RE, code, values_path)


def _check_quantities(
    version: Dict[str, Any], prefix: str, parameters: List[str]
) -> None:
    code = REGISTRY_INVALID
    value, path = _get(version, "quantities", code, prefix)
    names: List[str] = []
    for index, entry in enumerate(_array(value, code, path, 1, MAX_QUANTITIES)):
        entry_path = f"{path}[{index}]"
        entry = _object(entry, code, entry_path)
        _known_keys(entry, QUANTITY_KEYS, code, entry_path)
        value, name_path = _get(entry, "name", code, entry_path)
        name = _text(value, _NAME_RE, code, name_path)
        if name in names:
            _fail(code, "duplicate_entry", name_path)
        if name in parameters:
            _fail(code, "name_clash", name_path)
        names.append(name)
        if "unit" in entry:
            _text(entry["unit"], _UNIT_RE, code, f"{entry_path}.unit")
        value, places_path = _get(entry, "decimals", code, entry_path)
        if not isinstance(value, int) or isinstance(value, bool):
            _fail(code, "wrong_type", places_path)
        if not 0 <= value <= MAX_DECIMALS:
            _fail(code, "invalid_value", places_path)
        value, expression_path = _get(entry, "expression", code, entry_path)
        if not isinstance(value, str):
            _fail(code, "wrong_type", expression_path)
        # Only the space separates tokens. A control character is a request
        # fault, found here before any formula is parsed.
        if _CONTROL_RE.search(value) is not None:
            _fail(INVALID_REQUEST, "invalid_value", expression_path)


def _check_version(version: Any, prefix: str, versions: List[str]) -> None:
    code = REGISTRY_INVALID
    version = _object(version, code, prefix)
    _known_keys(version, VERSION_KEYS, code, prefix)
    value, path = _get(version, "model_version", code, prefix)
    number = _text(value, _SEMVER_RE, code, path)
    if number in versions:
        _fail(code, "duplicate_entry", path)
    versions.append(number)
    _check_inputs(version, prefix)
    value, path = _get(version, "parameters", code, prefix)
    parameters = _check_names(
        _array(value, code, path, 0, MAX_PARAMETERS), path, _NAME_RE
    )
    _check_revisions(version, prefix, parameters)
    _check_quantities(version, prefix, parameters)


def _check_registry(registry: Dict[str, Any]) -> None:
    code = REGISTRY_INVALID
    _known_keys(registry, REGISTRY_KEYS, code, "registry")
    value, path = _get(registry, "registry_id", code, "registry")
    _text(value, _LABEL_RE, code, path)
    value, path = _get(registry, "models", code, "registry")
    names: List[str] = []
    for index, model in enumerate(_array(value, code, path, 1, MAX_MODELS)):
        prefix = f"registry.models[{index}]"
        model = _object(model, code, prefix)
        _known_keys(model, MODEL_KEYS, code, prefix)
        value, name_path = _get(model, "model_name", code, prefix)
        name = _text(value, _NAME_RE, code, name_path)
        if name in names:
            _fail(code, "duplicate_entry", name_path)
        names.append(name)
        value, versions_path = _get(model, "versions", code, prefix)
        versions: List[str] = []
        for number, version in enumerate(
            _array(value, code, versions_path, 1, MAX_VERSIONS)
        ):
            _check_version(version, f"{versions_path}[{number}]", versions)


# --------------------------------------------------------------------------
# Expressions (MODEL_DEFINITION_INVALID): tokenizer and parser
# --------------------------------------------------------------------------
#
# Nodes are tuples: ("num", value), ("param", name), ("q", name),
# ("chain", first, ((op, node), ...)) for + and -, ("product", first, ...)
# for * and /, ("neg", node), ("power", node, exponent),
# ("agg", function, alias, column), ("sumproduct", alias, column, column),
# ("round", node, places) and (scalar, (node, ...)) for abs, least, greatest.
# Operator chains and unary minus are read by loops, so recursion grows only
# with parentheses, which are capped at 32 levels.


def _tokens(text: str, field: str) -> List[Tuple[str, str]]:
    code = MODEL_DEFINITION_INVALID
    if len(text) > MAX_EXPRESSION:
        _fail(code, "too_long", field)
    tokens: List[Tuple[str, str]] = []
    position = 0
    while position < len(text):
        match = _TOKEN_RE.match(text, position)
        if match is None:
            _fail(code, "invalid_character", field)
        position = match.end()
        kind = match.lastgroup
        if kind == "space":
            continue
        if kind == "number" and _LITERAL_RE.fullmatch(match.group()) is None:
            _fail(code, "invalid_literal", field)
        tokens.append((kind, match.group()))
    if len(tokens) > MAX_TOKENS:
        _fail(code, "too_many_tokens", field)
    return tokens


class _Parser:
    __slots__ = ("tokens", "position", "depth", "field", "parameters", "inputs", "done")

    def __init__(
        self,
        tokens: List[Tuple[str, str]],
        field: str,
        parameters: List[str],
        inputs: Dict[str, List[str]],
        done: List[str],
    ) -> None:
        self.tokens = tokens
        self.position = 0
        self.depth = 0
        self.field = field
        self.parameters = parameters
        self.inputs = inputs
        self.done = done

    def fail(self, reason: str) -> None:
        _fail(MODEL_DEFINITION_INVALID, reason, self.field)

    def peek(self) -> Tuple[str, str]:
        if self.position < len(self.tokens):
            return self.tokens[self.position]
        return ("end", "")

    def take(self) -> Tuple[str, str]:
        token = self.peek()
        if token[0] == "end":
            self.fail("syntax_error")
        self.position += 1
        return token

    def expect(self, text: str) -> None:
        if self.take() != ("op", text):
            self.fail("syntax_error")

    def name(self) -> str:
        kind, text = self.take()
        if kind != "name":
            self.fail("syntax_error")
        return text

    def open(self) -> None:
        self.expect("(")
        self.depth += 1
        if self.depth > MAX_DEPTH:
            self.fail("too_deep")

    def close(self) -> None:
        self.expect(")")
        self.depth -= 1

    def whole(self) -> Tuple[Any, ...]:
        node = self.expr()
        if self.peek()[0] != "end":
            self.fail("syntax_error")
        return node

    def expr(self) -> Tuple[Any, ...]:
        first = self.term()
        rest = []
        while self.peek() in (("op", "+"), ("op", "-")):
            rest.append((self.take()[1], self.term()))
        return ("chain", first, tuple(rest)) if rest else first

    def term(self) -> Tuple[Any, ...]:
        first = self.unary()
        rest = []
        while self.peek() in (("op", "*"), ("op", "/")):
            rest.append((self.take()[1], self.unary()))
        return ("product", first, tuple(rest)) if rest else first

    def unary(self) -> Tuple[Any, ...]:
        signs = 0
        while self.peek() == ("op", "-"):
            self.take()
            signs += 1
        node = self.power()
        return ("neg", node) if signs % 2 else node

    def power(self) -> Tuple[Any, ...]:
        base = self.atom()
        if self.peek() != ("op", "^"):
            return base
        self.take()
        sign = 1
        if self.peek() in (("op", "+"), ("op", "-")):
            sign = -1 if self.take()[1] == "-" else 1
        kind, text = self.peek()
        if kind != "number" or "." in text or int(text) > MAX_EXPONENT:
            self.fail("invalid_exponent")
        self.take()
        return ("power", base, sign * int(text))

    def column(self) -> Tuple[str, str]:
        alias = self.name()
        self.expect(".")
        column = self.name()
        if alias not in self.inputs:
            self.fail("unknown_input")
        if column not in self.inputs[alias]:
            self.fail("unknown_column")
        return alias, column

    def atom(self) -> Tuple[Any, ...]:
        kind, text = self.take()
        if kind == "number":
            return ("num", Decimal(text))
        if (kind, text) == ("op", "("):
            self.position -= 1
            self.open()
            node = self.expr()
            self.close()
            return node
        if kind != "name":
            self.fail("syntax_error")
        if text in ("param", "q"):
            self.expect(".")
            name = self.name()
            if text == "param" and name not in self.parameters:
                self.fail("unknown_parameter")
            if text == "q" and name not in self.done:
                self.fail("unknown_quantity")
            return (text, name)
        self.open()
        node = self.call(text)
        self.close()
        return node

    def call(self, function: str) -> Tuple[Any, ...]:
        if function in AGGREGATES:
            alias, column = self.column()
            return ("agg", function, alias, column)
        if function == "sumproduct":
            alias, first = self.column()
            self.expect(",")
            other, second = self.column()
            if other != alias:
                self.fail("columns_of_different_inputs")
            return ("sumproduct", alias, first, second)
        if function == "round":
            node = self.expr()
            self.expect(",")
            kind, text = self.peek()
            if kind != "number" or "." in text or int(text) > MAX_DECIMALS:
                self.fail("invalid_round_digits")
            self.take()
            return ("round", node, int(text))
        if function not in SCALARS:
            self.fail("syntax_error")
        arguments = [self.expr()]
        while self.peek() == ("op", ","):
            self.take()
            arguments.append(self.expr())
        if function == "abs" and len(arguments) != 1:
            self.fail("argument_count")
        if function != "abs" and not 2 <= len(arguments) <= 10:
            self.fail("argument_count")
        return (function, tuple(arguments))


def _parse_version(version: Dict[str, Any], prefix: str) -> List[Tuple[Any, ...]]:
    """Parse every quantity of one version; earlier quantities only in scope."""
    inputs = {entry["input_alias"]: entry["columns"] for entry in version["inputs"]}
    done: List[str] = []
    nodes = []
    for index, quantity in enumerate(version["quantities"]):
        field = f"{prefix}.quantities[{index}].expression"
        tokens = _tokens(quantity["expression"], field)
        parser = _Parser(tokens, field, version["parameters"], inputs, done)
        nodes.append(parser.whole())
        done.append(quantity["name"])
    return nodes


def _parse_registry(registry: Dict[str, Any]) -> Dict[Tuple[int, int], List[Any]]:
    parsed = {}
    for i, model in enumerate(registry["models"]):
        for j, version in enumerate(model["versions"]):
            prefix = f"registry.models[{i}].versions[{j}]"
            parsed[(i, j)] = _parse_version(version, prefix)
    return parsed


# --------------------------------------------------------------------------
# Lookups, binding, upstream status, digests and tables (codes 4 to 11)
# --------------------------------------------------------------------------


def _lookup(request: _Request) -> Tuple[int, int, Dict[str, Any], Dict[str, str]]:
    models = request.registry["models"]
    names = [model["model_name"] for model in models]
    if request.model_name not in names:
        _fail(MODEL_NOT_REGISTERED, "model_not_found", "model_name")
    i = names.index(request.model_name)
    versions = models[i]["versions"]
    numbers = [version["model_version"] for version in versions]
    if request.model_version not in numbers:
        _fail(VERSION_NOT_REGISTERED, "version_not_found", "model_version")
    j = numbers.index(request.model_version)
    version = versions[j]
    labels = [revision["label"] for revision in version["revisions"]]
    if request.parameter_revision not in labels:
        _fail(REVISION_NOT_REGISTERED, "revision_not_found", "parameter_revision")
    values = version["revisions"][labels.index(request.parameter_revision)]["values"]
    defined = [quantity["name"] for quantity in version["quantities"]]
    for index, name in enumerate(request.requested):
        if name not in defined:
            field = f"requested_quantities[{index}]"
            _fail(QUANTITY_NOT_DEFINED, "quantity_not_found", field)
    return i, j, version, values


def _check_binding(request: _Request, version: Dict[str, Any], prefix: str) -> None:
    declared = [entry["input_alias"] for entry in version["inputs"]]
    supplied = [item.alias for item in request.files]
    for index, alias in enumerate(supplied):
        if alias not in declared:
            field = f"input_files[{index}].input_alias"
            _fail(INPUT_BINDING_MISMATCH, "alias_not_declared", field)
    for index, alias in enumerate(declared):
        if alias not in supplied:
            field = f"{prefix}.inputs[{index}].input_alias"
            _fail(INPUT_BINDING_MISMATCH, "declared_input_missing", field)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _check_files(request: _Request) -> None:
    for index, item in enumerate(request.files):
        if item.status != "passed":
            field = f"input_files[{index}].validation_status"
            _fail(INPUT_NOT_VALIDATED, "not_passed", field)
    for index, item in enumerate(request.files):
        if _sha256(item.content) != item.sha256:
            _fail(DIGEST_MISMATCH, "digest_differs", f"input_files[{index}].sha256")


def _parse_table(
    content: str, columns: List[str], field: str
) -> Dict[str, List[Decimal]]:
    """Read one table: header line, then rows of decimal cells; LF ends a line."""
    code = INPUT_PARSE_ERROR
    if '"' in content:
        _fail(code, "quote", field)
    lines = content.split("\n")
    if content.endswith("\n"):
        lines = lines[:-1]
        ended = len(lines)
    else:
        ended = len(lines) - 1
    for index, line in enumerate(lines):
        if index < ended and line.endswith("\r"):
            lines[index] = line[:-1]
        if lines[index] == "":
            _fail(code, "empty_line", field)
    header = lines[0].split(",")
    if len(set(header)) != len(header):
        _fail(code, "duplicate_column", field)
    if sorted(header) != sorted(columns):
        _fail(code, "header_mismatch", field)
    if len(lines) < 2:
        _fail(code, "no_rows", field)
    if len(lines) - 1 > MAX_ROWS:
        _fail(code, "too_many_rows", field)
    table: Dict[str, List[Decimal]] = {name: [] for name in header}
    for line in lines[1:]:
        cells = line.split(",")
        if len(cells) != len(header):
            _fail(code, "ragged_row", field)
        for name, cell in zip(header, cells):
            if _DECIMAL_RE.fullmatch(cell) is None:
                _fail(code, "invalid_cell", field)
            table[name].append(Decimal(cell))
    return table


# --------------------------------------------------------------------------
# Evaluation (MODEL_EVALUATION_ERROR)
# --------------------------------------------------------------------------


class _Scope:
    __slots__ = ("parameters", "tables", "quantities", "cache", "field")

    def __init__(
        self,
        parameters: Dict[str, Decimal],
        tables: Dict[str, Dict[str, List[Decimal]]],
    ) -> None:
        self.parameters = parameters
        self.tables = tables
        self.quantities: Dict[str, Decimal] = {}
        self.cache: Dict[Tuple[str, ...], Decimal] = {}
        self.field = ""


def _limited(value: Decimal, scope: _Scope) -> Decimal:
    """Every value the evaluator produces stays below 10^24 in magnitude."""
    if value.copy_abs() >= _LIMIT:
        _fail(MODEL_EVALUATION_ERROR, "magnitude_limit", scope.field)
    return value


def _divide(left: Decimal, right: Decimal, scope: _Scope) -> Decimal:
    if right.is_zero():
        _fail(MODEL_EVALUATION_ERROR, "division_by_zero", scope.field)
    return _CTX.divide(left, right)


def _total(values: List[Decimal], scope: _Scope) -> Decimal:
    """A sum whose running total stays below the limit at every step."""
    result = _ZERO
    for value in values:
        result = _limited(_CTX.add(result, value), scope)
    return result


def _aggregate(function: str, values: List[Decimal], scope: _Scope) -> Decimal:
    if function == "count":
        return Decimal(len(values))
    if not values:
        _fail(MODEL_EVALUATION_ERROR, "empty_column", scope.field)
    if function == "sum":
        return _total(values, scope)
    if function == "mean":
        return _divide(_total(values, scope), Decimal(len(values)), scope)
    if function == "first":
        return values[0]
    if function == "last":
        return values[-1]
    result = values[0]
    for value in values[1:]:
        if (function == "min" and value < result) or (
            function == "max" and value > result
        ):
            result = value
    return result


def _cached(key: Tuple[str, ...], scope: _Scope) -> Decimal:
    if key not in scope.cache:
        table = scope.tables[key[1]]
        if key[0] == "sumproduct":
            first, second = table[key[2]], table[key[3]]
            if len(first) != len(second):
                _fail(MODEL_EVALUATION_ERROR, "unequal_lengths", scope.field)
            products = [
                _limited(_CTX.multiply(a, b), scope) for a, b in zip(first, second)
            ]
            scope.cache[key] = _total(products, scope)
        else:
            scope.cache[key] = _aggregate(key[0], table[key[2]], scope)
    return scope.cache[key]


def _power(base: Decimal, exponent: int, scope: _Scope) -> Decimal:
    result = _ONE
    for _ in range(abs(exponent)):
        result = _limited(_CTX.multiply(result, base), scope)
    if exponent < 0:
        result = _divide(_ONE, result, scope)
    return result


def _evaluate(node: Tuple[Any, ...], scope: _Scope) -> Decimal:
    kind = node[0]
    if kind == "num":
        value = node[1]
    elif kind == "param":
        value = scope.parameters[node[1]]
    elif kind == "q":
        value = scope.quantities[node[1]]
    elif kind == "chain":
        value = _evaluate(node[1], scope)
        for op, item in node[2]:
            right = _evaluate(item, scope)
            value = _CTX.add(value, right) if op == "+" else _CTX.subtract(value, right)
            value = _limited(value, scope)
    elif kind == "product":
        value = _evaluate(node[1], scope)
        for op, item in node[2]:
            right = _evaluate(item, scope)
            if op == "*":
                value = _CTX.multiply(value, right)
            else:
                value = _divide(value, right, scope)
            value = _limited(value, scope)
    elif kind == "neg":
        value = _evaluate(node[1], scope).copy_negate()
    elif kind == "power":
        value = _power(_evaluate(node[1], scope), node[2], scope)
    elif kind in ("agg", "sumproduct"):
        value = _cached(tuple(node[1:]) if kind == "agg" else node, scope)
    elif kind == "round":
        value = _evaluate(node[1], scope).quantize(
            _QUANTA[node[2]], rounding=ROUND_HALF_UP, context=_CTX
        )
    elif kind == "abs":
        value = _evaluate(node[1][0], scope).copy_abs()
    else:
        values = [_evaluate(item, scope) for item in node[1]]
        value = values[0]
        for item in values[1:]:
            if (kind == "least" and item < value) or (
                kind == "greatest" and item > value
            ):
                value = item
    return _limited(value, scope)


def _rounded(value: Decimal, places: int, scope: _Scope) -> Decimal:
    """Half-up to the declared decimals; a negative zero becomes zero."""
    value = value.quantize(_QUANTA[places], rounding=ROUND_HALF_UP, context=_CTX)
    if value.is_zero():
        value = value.copy_abs()
    return _limited(value, scope)


def _evaluate_version(
    version: Dict[str, Any], prefix: str, nodes: List[Any], scope: _Scope
) -> None:
    """Every quantity, in declaration order, whether requested or not."""
    for index, (quantity, node) in enumerate(zip(version["quantities"], nodes)):
        scope.field = f"{prefix}.quantities[{index}].expression"
        try:
            value = _rounded(_evaluate(node, scope), quantity["decimals"], scope)
        except DivisionByZero:
            _fail(MODEL_EVALUATION_ERROR, "division_by_zero", scope.field)
        except Overflow:
            _fail(MODEL_EVALUATION_ERROR, "magnitude_limit", scope.field)
        except InvalidOperation:
            _fail(MODEL_EVALUATION_ERROR, "invalid_operation", scope.field)
        scope.quantities[quantity["name"]] = value


# --------------------------------------------------------------------------
# Results, the archive document and verify (ARCHIVE_MISMATCH)
# --------------------------------------------------------------------------


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _results(
    request: _Request, version: Dict[str, Any], scope: _Scope
) -> List[Dict[str, str]]:
    units = {}
    for quantity in version["quantities"]:
        if "unit" in quantity:
            units[quantity["name"]] = quantity["unit"]
    results = []
    for name in request.requested:
        item = {"quantity": name, "value": f"{scope.quantities[name]:f}"}
        if name in units:
            item["unit"] = units[name]
        results.append(item)
    return results


def _archive_document(
    request: _Request,
    values: Dict[str, str],
    identity: Dict[str, Any],
    results: List[Dict[str, str]],
) -> str:
    """The canonical record of the run; tables are referenced by digest only."""
    return _canonical(
        {
            "schema": ARCHIVE_SCHEMA,
            "run_id": identity["run_id"],
            "registry_id": request.registry["registry_id"],
            "model_name": request.model_name,
            "model_version": request.model_version,
            "parameter_revision": request.parameter_revision,
            "parameter_values": values,
            "registry_entry_sha256": identity["registry_entry_sha256"],
            "inputs": [
                {
                    "input_alias": item.alias,
                    "file_ref": item.file_ref,
                    "sha256": item.sha256,
                }
                for item in request.files
            ],
            "input_digest": identity["input_digest"],
            "requested_quantities": request.requested,
            "results": results,
            "archive_label": request.label,
        }
    )


def _identity(request: _Request, version: Dict[str, Any]) -> Dict[str, Any]:
    identity = {
        "archive_label": request.label,
        "input_digest": _sha256("".join(item.sha256 + "\n" for item in request.files)),
        "model_name": request.model_name,
        "model_version": request.model_version,
        "parameter_revision": request.parameter_revision,
        "registry_entry_sha256": _sha256(_canonical(version)),
        "requested_quantities": request.requested,
    }
    identity["run_id"] = "run-" + _sha256(_canonical(identity))[:16]
    return identity


def _run_model(params: Any) -> Dict[str, Any]:
    request = _validate_request(params)
    _check_registry(request.registry)
    parsed = _parse_registry(request.registry)
    i, j, version, values = _lookup(request)
    prefix = f"registry.models[{i}].versions[{j}]"
    _check_binding(request, version, prefix)
    _check_files(request)
    columns = {entry["input_alias"]: entry["columns"] for entry in version["inputs"]}
    tables = {}
    for index, item in enumerate(request.files):
        field = f"input_files[{index}].content"
        tables[item.alias] = _parse_table(item.content, columns[item.alias], field)
    parameters = {name: Decimal(text) for name, text in values.items()}
    scope = _Scope(parameters, tables)
    _evaluate_version(version, prefix, parsed[(i, j)], scope)
    results = _results(request, version, scope)
    identity = _identity(request, version)
    document = _archive_document(request, values, identity, results)
    document_sha256 = _sha256(document)
    run_id = identity["run_id"]
    if request.action == ACTION_VERIFY:
        if request.document != document:
            _fail(ARCHIVE_MISMATCH, "document_differs", "archive_document")
        return {
            "status": "completed",
            "verified": True,
            "run_id": run_id,
            "document_sha256": document_sha256,
        }
    location = f"archive/{request.label or 'unlabelled'}/{run_id}"
    return {
        "status": "completed",
        "model_name": request.model_name,
        "model_version": request.model_version,
        "parameter_revision": request.parameter_revision,
        "results": results,
        "archive_record": {
            "run_id": run_id,
            "input_digest": identity["input_digest"],
            "registry_entry_sha256": identity["registry_entry_sha256"],
            "model_reference": f"registry/{request.model_name}/{request.model_version}",
            "inputs_reference": f"{location}/inputs",
            "outputs_reference": f"{location}/outputs/results.json",
            "document": document,
            "document_sha256": document_sha256,
        },
    }


class ApprovedFinancialModelRunnerSkill(BaseSkill):
    """Run a registered financial model version on prepared tables; figures only."""

    @property
    def manifest(self) -> Dict[str, Any]:
        return MANIFEST

    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        try:
            return _run_model(params)
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
