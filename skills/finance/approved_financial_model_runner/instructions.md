# Approved Financial Model Runner

`finance/approved_financial_model_runner` runs one registered model version on
prepared tables and returns figures only: the requested quantities as rounded
decimal strings, and an archive record of the run for the caller to store.

One call does all of this:

- checks the request and the whole registry document passed with it;
- looks up the model, the version and the parameter revision in that
  document;
- requires every table to be marked `passed` upstream and to have the SHA-256
  declared for it;
- parses each table into columns of decimals;
- evaluates every quantity of the version, in declaration order, over the
  frozen parameter values and the table columns;
- for `run`, returns the requested quantities, rounded half-up to each
  quantity's declared decimals, with the archive record.

A second action, `verify`, rebuilds the same run and confirms that a stored
archive document is identical to the one the run produces now. It returns
that confirmation with the run id and the document's SHA-256, and no figures.

A model is data, never code: a quantity is a formula in the closed grammar
below, and nothing in a registry document can make the skill load or run
anything. `execute()` makes no model call and uses no network, clock, file or
environment. Identical requests return identical output.

## When not to use

- Preparing or cleaning data, writing or calibrating a model, or choosing
  between models.
- Narrative, recommendations or client text: the result holds figures only.
- Tuning a parameter for a single run: values come only from a recorded
  revision.

## What the caller owns

- Reading storage. The caller reads the registry document and each prepared
  table from its own storage and passes their exact text.
- Custody of the registry. The caller passes the operator's registry
  document, not one written for the run. The archive record carries the
  SHA-256 of the selected version entry, so a run can be checked against the
  operator's registry later.
- Storing the archive. The caller stores `archive_record.document` (and the
  table texts under their digests) before relying on the figures, and may
  call `verify` with the stored document later.
- Upstream validation. The caller's validation step sets
  `validation_status`; the skill refuses anything but `passed` and does not
  judge business content.

## Request

| Field | Required | Rule |
| :-- | :-- | :-- |
| `action` | yes | `run` or `verify` |
| `model_name` | yes | `[a-z][a-z0-9_]{0,63}` |
| `model_version` | yes | `MAJOR.MINOR.PATCH`, each part 0 or up to six digits without a leading zero |
| `parameter_revision` | yes | `rev-[1-9][0-9]{0,8}` |
| `input_files` | yes | 1 to 20 entries, below |
| `requested_quantities` | yes | 1 to 100 distinct names, each `[a-z][a-z0-9_]{0,63}` |
| `archive_label` | no | `[a-z0-9][a-z0-9_-]{0,63}` |
| `registry` | yes | an object: the registry document, below |
| `archive_document` | `verify` only | 1 to 2,000,000 characters, no control character (U+0000 to U+001F or U+007F to U+009F); not allowed with `run` |

Each `input_files` entry:

| Field | Rule |
| :-- | :-- |
| `file_ref` | 1 to 512 characters, no control character (U+0000 to U+001F or U+007F to U+009F); a relative path with no leading `/`, no `\` and no empty, `.` or `..` segment |
| `input_alias` | `[a-z][a-z0-9_]{0,31}`, not `param` or `q`, distinct within the request |
| `sha256` | `[a-f0-9]{64}`, the SHA-256 of `content` encoded as UTF-8 |
| `validation_status` | `passed` or `failed` |
| `content` | the table text, at most 2,000,000 characters; all contents together at most 8,000,000 |

Pass each table's bytes decoded as UTF-8, with no newline translation. Unknown
keys are rejected at every level, and no string may hold a lone surrogate.
Each quoted pattern is the one the code compiles, applied with ASCII rules,
and the whole value must match. `model_version` (and a registry entry's
`model_version`) matches `(?:0|[1-9][0-9]{0,5})\.(?:0|[1-9][0-9]{0,5})\.(?:0|[1-9][0-9]{0,5})`.

## Registry document

```
registry := { "registry_id": ID, "models": [model, ...] }                  1 to 100 models
model    := { "model_name": NAME, "versions": [version, ...] }              1 to 20 versions
version  := { "model_version": SEMVER,
              "inputs":     [ { "input_alias": ALIAS, "columns": [NAME, ...] } ],   1 to 20; 1 to 50 columns
              "parameters": [NAME, ...],                                     0 to 100
              "revisions":  [ { "label": REV, "values": { NAME: DECIMAL } } ],  1 to 50
              "quantities": [ { "name": NAME, "unit": UNIT?, "decimals": INT, "expression": FORMULA } ] }  1 to 100
```

- `registry_id` is `[a-z0-9][a-z0-9_-]{0,63}`. Model names, versions within
  a model, input aliases and revision labels within a version, columns within
  an input, parameters and quantity names are each distinct.
- Input aliases follow the request rule (not `param` or `q`).
- Each revision's `values` names exactly the declared parameters, each with a
  decimal string.
- `unit` is optional, `[A-Za-z0-9_%/.-]{1,32}`. `decimals` is an integer
  from 0 to 12. A quantity name may not equal a parameter name.

The whole document is checked, every model and version included, before any
lookup: a malformed entry fails the run even when another entry is selected.

## Formula grammar

```
expr     := term (("+" | "-") term)*
term     := unary (("*" | "/") unary)*
unary    := "-" unary | power
power    := atom ("^" exponent)?
atom     := NUMBER | "param." NAME | "q." NAME
          | AGG "(" ALIAS "." NAME ")"
          | "sumproduct" "(" ALIAS "." NAME "," ALIAS "." NAME ")"
          | "abs" "(" expr ")"
          | ("least" | "greatest") "(" expr ("," expr)* ")"
          | "round" "(" expr "," DIGITS ")"
          | "(" expr ")"
AGG      := "sum" | "mean" | "min" | "max" | "first" | "last" | "count"
```

- `NUMBER` is unsigned: `(?:0|[1-9][0-9]{0,17})(?:\.[0-9]{1,12})?`, and the
  whole token must match. A sign comes from unary minus.
- `exponent` is an optional `+` or `-` and a `NUMBER` with no point, at most
  64: an integer from -64 to 64 written with no leading zero.
  A power is computed by repeated multiplication, so `x ^ 0` is 1, also for
  `x` = 0.
- `DIGITS` in `round` is a `NUMBER` with no point, at most 12: an integer
  from 0 to 12, unsigned, written with no leading zero.
- `least` and `greatest` take 2 to 10 arguments; `abs` takes one.
- `sumproduct` takes two columns of the same input.
- `param.NAME` names a declared parameter; `ALIAS.NAME` a declared column of
  a declared input; `q.NAME` a quantity declared earlier in the same version,
  read at its rounded value (the value returned). A quantity cannot name
  itself or a later one.
- Spaces between tokens are ignored; the space (U+0020) is the only
  separator. A formula must not hold a control character (U+0000 to U+001F
  or U+007F to U+009F; tab, line feed and carriage return included). Any
  other character outside the grammar is rejected.
- Caps: 2,000 characters, 400 tokens, and 32 levels of open parentheses (a
  grouping or a function call's argument list each open one level; unary
  minus opens none).
- Unary minus binds looser than `^`: `-3 ^ 2` is -9. `+`, `-`, `*` and `/` are
  left-associative.

## Tables

- Lines end in a line feed; one carriage return before it is removed. The
  final line ending is optional. Empty lines are rejected. No other character
  ends a line.
- The first line is the header: the input's declared columns, each once, in
  any order, separated by `,`.
- Each further line has one cell per header column. A cell is a decimal string
  `-?(?:0|[1-9][0-9]{0,17})(?:\.[0-9]{1,12})?`: ASCII digits only, no `+`, no
  exponent, no separator, no space. Quoting is not supported; a `"` anywhere is
  rejected.
- 1 to 100,000 data rows.

Revision values follow the same decimal rule. JSON numbers are never read as
values.

## Evaluation and rounding

- Every quantity of the version is evaluated, in declaration order, whether it
  is requested or not; a failure in any of them fails the run. Only requested
  quantities are returned.
- Arithmetic runs in one private decimal context (precision 40, half-up),
  never in the caller's thread context.
- Every value the evaluation produces (each operation, function and aggregate
  result, and each rounded quantity) must stay below 10^24 in magnitude.
- A quantity's value is rounded half-up to its `decimals` (ties away from zero:
  -0.125 at two decimals is -0.13), shown in plain notation without an
  exponent; a negative zero is shown as zero. `round()` rounds the same way.

## Output of `run`

```json
{
  "status": "completed",
  "model_name": "capital_impact", "model_version": "1.2.0", "parameter_revision": "rev-3",
  "results": [
    {"quantity": "rwa_added", "value": "750000.00", "unit": "currency_units"},
    {"quantity": "capital_required", "value": "60000.00", "unit": "currency_units"}
  ],
  "archive_record": {
    "run_id": "run-0c2f871458836088",
    "input_digest": "267929b6e53433579c1b504856a8445adf65822447debb04d73108baa8c3a166",
    "registry_entry_sha256": "07e5cd3955ce6fd5292c0bdc065189e535e7cfeb65a95cd25babeba2d4a7fb6c",
    "model_reference": "registry/capital_impact/1.2.0",
    "inputs_reference": "archive/unlabelled/run-0c2f871458836088/inputs",
    "outputs_reference": "archive/unlabelled/run-0c2f871458836088/outputs/results.json",
    "document": "<canonical JSON text>",
    "document_sha256": "<SHA-256 of document>"
  }
}
```

Canonical JSON is
`json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`,
encoded as UTF-8 for hashing.

- `results` follows the order of `requested_quantities`; `unit` appears only
  when the registry declares one.
- `input_digest` is the SHA-256 of each file's `sha256` followed by a line
  feed, concatenated in request order.
- `registry_entry_sha256` is the SHA-256 of the canonical JSON of the selected
  version object, as supplied.
- `run_id` is `run-` plus the first 16 hex characters of the SHA-256 of the
  canonical JSON of `archive_label` (or null), `input_digest`, `model_name`,
  `model_version`, `parameter_revision`, `registry_entry_sha256` and
  `requested_quantities`. Identical requests give the same run id.
- The references use `archive_label`, or `unlabelled` when it is absent. They
  are suggested locations; the skill stores nothing.
- `document` is the canonical JSON of `schema`
  (`approved_financial_model_runner.archive/1`), `run_id`, `registry_id`,
  `model_name`, `model_version`, `parameter_revision`, `parameter_values` (the
  revision's values), `registry_entry_sha256`, `inputs` (each `input_alias`,
  `file_ref` and `sha256`), `input_digest`, `requested_quantities`, `results`
  and `archive_label` (or null). Table contents are referenced by digest and
  never copied. `document_sha256` is its SHA-256.

## Output of `verify`

```json
{"status": "completed", "verified": true, "run_id": "run-0c2f871458836088", "document_sha256": "<64 hex>"}
```

`verify` recomputes the whole run from the request, then compares the rebuilt
document with `archive_document` character for character. A stored document
that is valid JSON but differs in any character, spacing included, fails.

## Errors, in check order

```json
{"status": "error", "error": {"code": "<code>", "detail": {"reason": "<reason>", "field": "<path>"}}}
```

The checks run in this order; the first failure returns its code, and nothing
after it is computed.

| # | Code | Reasons |
| --: | :-- | :-- |
| 1 | `INVALID_REQUEST` | `not_an_object`, `unknown_key`, `missing_field`, `wrong_type`, `invalid_value`, `reserved_name`, `length_out_of_range`, `total_too_long`, `count_out_of_range`, `duplicate_entry`, `field_not_allowed` |
| 2 | `REGISTRY_INVALID` | `not_an_object`, `unknown_key`, `missing_field`, `wrong_type`, `invalid_value`, `reserved_name`, `count_out_of_range`, `duplicate_entry`, `values_mismatch`, `name_clash` |
| 3 | `MODEL_DEFINITION_INVALID` | `too_long`, `invalid_character`, `invalid_literal`, `too_many_tokens`, `syntax_error`, `too_deep`, `invalid_exponent`, `invalid_round_digits`, `argument_count`, `unknown_parameter`, `unknown_input`, `unknown_column`, `unknown_quantity`, `columns_of_different_inputs` |
| 4 | `MODEL_NOT_REGISTERED` | `model_not_found` |
| 5 | `VERSION_NOT_REGISTERED` | `version_not_found` |
| 6 | `REVISION_NOT_REGISTERED` | `revision_not_found` |
| 7 | `QUANTITY_NOT_DEFINED` | `quantity_not_found` |
| 8 | `INPUT_BINDING_MISMATCH` | `alias_not_declared`, `declared_input_missing` |
| 9 | `INPUT_NOT_VALIDATED` | `not_passed` |
| 10 | `DIGEST_MISMATCH` | `digest_differs` |
| 11 | `INPUT_PARSE_ERROR` | `quote`, `empty_line`, `duplicate_column`, `header_mismatch`, `no_rows`, `too_many_rows`, `ragged_row`, `invalid_cell` |
| 12 | `MODEL_EVALUATION_ERROR` | `division_by_zero`, `magnitude_limit`, `invalid_operation`, `empty_column`, `unequal_lengths` |
| 13 | `ARCHIVE_MISMATCH` | `document_differs` (`verify` only) |
| | `INTERNAL_ERROR` | `internal_error`, with `exception`, the class name of an unexpected fault |

- Within check 1 the fields are read in the order of the request table, each
  input file entry completely before the next. Within checks 2 and 3 the
  registry is read model by model and version by version; check 2 covers the
  whole document before check 3 starts. A registry that is not an object is
  check 1.
- A formula that holds a control character fails with `INVALID_REQUEST`,
  reason `invalid_value` and the formula's path, at the point where check 2
  reads that formula, so before any formula is parsed.
- A `file_ref` or an `archive_document` that holds a control character fails
  with `INVALID_REQUEST`, reason `invalid_value` and the field's path, right
  after the field's type check: a stored document with one never reaches the
  comparison of check 13.
- Checks 9 and 10 each cover every file before the next check starts; check
  11 parses the files in request order.
- `field`, when one applies, is a path of names and indexes, such as
  `input_files[0].sha256` or
  `registry.models[1].versions[0].quantities[2].expression`. For an unknown
  key it names the object that holds the key. A request that is not an
  object, or an unknown key at its top level, has no `field`. No detail
  repeats a table cell, a parameter value or any other input text.
