# Cognition Instructions: Period Close

You have access to the `finance/period_close` tool.

This skill is a **deterministic, offline period close** over a journal and a
chart of accounts supplied by the host. It has two actions that share one
data model, the journal entry:

- `validate` checks the journal against the chart and returns **findings**
  from a closed registry: blocking faults, warnings and one informational
  code. It returns findings only.
- `close` runs `validate` first and fails closed on any blocking finding
  (`CLOSE_BLOCKED`) unless `soft_close` is set. It returns a **trial
  balance**, an **income statement**, a **balance sheet**, a direct-method
  **cash flow**, the **checks** (the same findings), **notes**, **ratios**,
  **observations** and two flags.

Given prior periods' journals, both actions name gaps: an account and
counterparty pair that recurred before and is absent now
(`EXPECTED_MISSING`), or an amount out of line with its past median
(`AMOUNT_DEVIATION`). A gap is named and never filled.

Single currency, single entity. `execute()` is a pure function: no network,
no clock, no file access; identical input returns identical output. Amounts
are decimal strings, every figure is computed with one private decimal
context, and no binary floating-point value reaches the output. A contract
violation returns `status: "error"` with a code from the closed registry; an
unexpected internal fault returns the one-code fault registry entry
`INTERNAL_ERROR`.

## When not to use

Do not use `period_close` for tax computation, jurisdiction-specific
presentation rules, payroll, multi-entity consolidation, or bank-feed
retrieval: those live in your bookkeeping system or with your accountant. It
does not fetch or sort anything: the host supplies the journal, the chart of
accounts and, optionally, prior periods. For a one-off look at a spreadsheet
of transactions, a language model reading the file is enough; this skill
exists for the repeated, unattended close whose numbers must be reproducible
and whose gaps must be named.

## What the host and the operator own

- The host supplies the journal, the chart of accounts and, optionally, the
  prior periods, and decides whether to call the model: the skill computes,
  and the model is called only when a finding or a note needs interpretation.
- The operator owns the books, the chart mapping and the decision to rely on
  a provisional close; a soft close is a working figure, not a closed period.
- The skill posts nothing, fetches nothing and sends nothing; it never
  changes the journal it is given.

## Agent-loop contract

- `CLOSE_BLOCKED` stops the loop: the operator corrects the journal and the
  host calls `close` again. Do not retry with a soft close in its place
  unless the operator asked for a provisional figure.
- Warnings are surfaced to the operator with the statements; they never
  change a figure.
- A gap note is a question for the operator, never a number to fill: the
  skill does not post a missing item, and neither should the model.
- An error envelope means the request did not meet the contract: correct the
  input, do not retry with guessed values.

## Inputs

Both actions take the same request.

- `as_of` (required): the date of the run as `YYYY-MM-DD`, a real calendar
  date; no clock is read. `close` needs `as_of` on or after `period.end`.
- `period` (required): `start` and `end` as dates, `start` not after `end`,
  with no other key. Both ends belong to the period.
- `currency` (required): three upper-case letters, `^[A-Z]{3}$`.
- `chart_of_accounts[]` (required): rows of `account`
  (`^[a-z][a-z0-9_]{0,63}$`, unique), `class` and an optional `subclass`
  from the closed sets below. Retired accounts must stay in the chart: an
  account used in a prior journal but absent from the chart is
  `INVALID_REQUEST`, and an account used in the current journal or the
  opening balances but absent from the chart is the blocking finding
  `UNMAPPED_ACCOUNT`.
- `opening_balances[]` (optional): lines of `account`, `debit`, `credit`,
  balance-sheet accounts only, one line per account.
- `journal[]` (required, non-empty): entries of `id`
  (`^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$`), `date`, optional `counterparty`
  (`^[a-z0-9][a-z0-9_.-]{0,63}$`, an identifier because it is echoed in
  `refs`), optional `currency` (must equal the request's), and `lines[]` of
  `account`, `debit`, `credit`. A line may carry both a debit and a credit;
  only a line whose two amounts are both zero is a `ZERO_LINE`.
- `prior_journals[]` (optional): earlier periods, oldest first, each with
  `period`, `journal[]` and optional `currency`. A prior period must end
  before `period.start`, must not overlap an earlier-listed prior period, and
  must be comparable in length to the current period within three days;
  every prior entry is dated within its period and every line's account is
  in the chart.
- `options` (optional): `soft_close` (boolean, default false),
  `recurrence_min_periods` (integer, not a boolean, from 1 to 24, default 2)
  and `deviation_threshold` (a decimal string greater than zero with at most
  four decimals, matching `^(0|[1-9][0-9]{0,14})(\.[0-9]{1,4})?$`, default
  `"0.20"`).

An amount matches `^(0|[1-9][0-9]{0,14})(\.[0-9]{1,2})?$`. A JSON number, a
sign, an exponent or a third decimal is `INVALID_AMOUNT`. A date field must
be a string of the form `YYYY-MM-DD` naming a real date; any other type and
any malformed value is `INVALID_DATE`. Every line's `account`, in
`opening_balances`, in `journal` and in a prior journal, must match the
chart's account pattern; otherwise the check that tests that line's
`account` returns `INVALID_REQUEST` with `detail.reason` naming the pattern.
Lengths in days include both ends: `end - start + 1`.

Reference JSON Schemas for the request and the output ship under `schemas/`
in this bundle; they are documentation, and the skill enforces the same
rules with explicit standard-library checks (`requirements: []` is
deliberate).

## Account classes and subclasses

The class and subclass sets are closed. A subclass is optional except that
an omitted asset or liability subclass is treated as `other_current` and an
omitted revenue or expense subclass as `operating`.

| Class | Subclasses | Default when omitted |
| :-- | :-- | :-- |
| `asset` | `cash`, `receivable`, `inventory`, `other_current`, `fixed` | `other_current` |
| `liability` | `payable`, `other_current`, `long_term` | `other_current` |
| `equity` | (none) | (none) |
| `revenue` | `operating`, `other` | `operating` |
| `expense` | `cogs`, `operating`, `non_operating` | `operating` |

Asset and expense accounts are debit-normal; liability, equity and revenue
accounts are credit-normal. A balance is shown in its natural sign.

## Findings registry

Findings are reported in this order, one element per code that fired, each
with `code`, `severity`, `count` and `refs`. `refs` are unique and sorted by
code point; `count` is their number, or 1 where `refs` is empty. The
entry-level findings look at every entry of `journal`, whatever its date;
for `UNUSED_ACCOUNT`, any line of `journal` or of a prior journal counts as
use.

| Code | Severity | Definition | `refs` |
| :-- | :-- | :-- | :-- |
| `UNBALANCED_ENTRY` | blocking | An entry's debits do not equal its credits. | entry ids |
| `UNMAPPED_ACCOUNT` | blocking | A journal or opening line names an account not in the chart. | account ids |
| `ENTRY_OUT_OF_PERIOD` | blocking | Entry date outside the period. | entry ids |
| `ENTRY_AFTER_AS_OF` | blocking | Entry date after `as_of`. | entry ids |
| `DUPLICATE_ENTRY_ID` | blocking | An id occurs more than once. | the ids |
| `OPENING_UNBALANCED` | blocking | Opening debits do not equal opening credits. | empty |
| `SUSPECTED_DUPLICATE` | warning | Two or more entries with different ids, the same date and counterparty, and the same multiset of lines, amounts compared by value. Entries without a counterparty are grouped with each other, never with an entry that has one. | all ids in each group |
| `ZERO_LINE` | warning | A line with zero debit and zero credit. | entry ids |
| `NEGATIVE_CASH` | warning | A cash-subclass account closes below zero. | account ids |
| `EXPECTED_MISSING` | warning | See the gap rules. | `account/counterparty` |
| `AMOUNT_DEVIATION` | warning | See the gap rules. | `account/counterparty` |
| `UNUSED_ACCOUNT` | info | A chart account with no opening line and no line in the current or any prior journal. | account ids |

`validate` returns `status`, `findings[]` and `blocking` (true when any
blocking finding exists). `close` returns the same findings as `checks[]`.

## Statement rules

Every figure uses the entries dated within the period and, of those, the
lines on chart accounts. The same rule applies to a soft close and a hard
close, so their figures agree when nothing blocks; in a soft close, entries
outside the period and lines on unmapped accounts are left out and
everything else is included as given, so `balanced` may be false.

- `trial_balance[]`: one row per chart account, in chart order, with
  `account`, `class`, `opening`, `debit`, `credit` and `closing`. `opening`
  is the opening balance in the account's natural sign (`0` for revenue and
  expense accounts); `debit` and `credit` are the period totals of the
  lines, both amounts of a line counted; `closing` is opening plus the
  movement in the natural sign (debit less credit for debit-normal accounts,
  credit less debit otherwise).
- `income_statement`: `revenue` is the total of revenue accounts; `cogs` the
  total of expense accounts with subclass `cogs`; `expenses` the total of
  all other expense accounts; `gross_profit` is revenue less `cogs`;
  `net_income` is revenue less all expense.
- `balance_sheet`: `assets` and `liabilities` are closing totals;
  `equity_opening` is the opening total of equity accounts;
  `equity_movements` their net change in the period; `equity_closing` is
  opening plus movements plus `net_income`; `balanced` is true when `assets`
  equals `liabilities` plus `equity_closing`.
- `cash_flow` is direct: for each entry take the net debit across
  cash-subclass accounts; a positive net adds to `inflows`, a negative net to
  `outflows`, so a transfer between two cash accounts counts as neither.
  `cash_opening` and `cash_closing` are the totals of the cash accounts.
- `closed` is true and `provisional` false for a hard close; a soft close
  returns `closed: false` and `provisional: true`, with the blocking findings
  repeated in `notes` as `{code, refs}` before the gap notes.
- `fired_any` is true when any blocking or warning finding exists;
  `insufficient` is true when any ratio is `null`.

Identities: total debits equal total credits in the trial balance; `assets`
equals `liabilities` plus `equity_closing`; `cash_closing` equals
`cash_opening` plus `inflows` less `outflows`; `net_income` is the same value
in both statements. They hold whenever no blocking finding exists.

## Gap rules

A pair is an account of class revenue or expense together with the entry's
counterparty; entries without a counterparty form no pair. A pair's amount
in a period is the sum of its lines in the account's natural sign; the
current-period amount uses the entries dated within the period and, of
those, the lines on chart accounts. The number of a pair's entries in a
period counts entries, not lines.

- `EXPECTED_MISSING`: the pair has at least one line in at least
  `recurrence_min_periods` of the supplied prior periods and none in the
  current period. Its note carries `code`, `account`, `counterparty`,
  `cadence`, `last_seen` (the latest prior entry date for the pair),
  `expected_amount` (the median of the pair's amounts over the prior periods
  where it was seen) and `detail`, exactly
  `seen in K of the last N periods, absent in this one`, with `K` the number
  of prior periods where the pair was seen and `N` the number of prior
  periods supplied.
- `AMOUNT_DEVIATION`: the pair is present now and was seen in at least
  `recurrence_min_periods` prior periods; with `m` the median as above,
  quantised to two decimals, and `m` not zero, it fires when the absolute
  difference between the current amount and `m` is greater than
  `deviation_threshold` times the absolute value of `m`, compared exactly.
  Its note carries exactly `code`, `account`, `counterparty`, `amount` (the
  current amount), `median` and `deviation` (the difference divided by the
  absolute median, four decimals).
- The median of an even count is the mean of the two middle values; the
  median is computed in code with the skill's decimal context.
- `cadence` is derived, not supplied: over the prior periods where the pair
  was seen, the median period length in days divided by the median number of
  the pair's entries per period; up to 10 days is `weekly`, up to 45
  `monthly`, up to 135 `quarterly`, above that `longer`.
- `notes` are ordered by the findings registry order of their codes and,
  within one code, by `account/counterparty`.
- A gap is named and never filled: no figure changes because of a note.

## Rounding and rendering

All arithmetic uses one private decimal context with a precision of 40
digits and `ROUND_HALF_UP`; the thread's decimal context is never read for
arithmetic and never modified. Input amounts have at most two decimals and
sums are exact. Derived amounts (medians) are quantised to two decimals,
ratios and deviations to four, both `ROUND_HALF_UP`. The zero rule applies
after quantisation: a value that quantises to zero renders unsigned, `0` for
amounts and `0.0000` for ratios and deviations, never `-0` or `-0.0000`. Any
other amount renders as a plain string with exactly two decimals and a
leading `-` if negative; ratios and deviations with exactly four decimals.

## Null rule and ratios

`null` means "the chart declares nothing this figure could come from".
`revenue` is `null` when the chart has no revenue account; `expenses` when it
has no expense account other than `cogs`; `cogs` and `gross_profit` when it
has no `cogs` account; all four cash-flow figures when it has no cash
account. Totals that take part in an identity (`assets`, `liabilities`, the
equity figures, `net_income`) are always numbers. Each ratio has its own
operands and its own `null` condition. A "line sum" is the total over the
named accounts' lines in the period, in the account's natural sign, and is
zero where the chart declares no such account; a "statement figure" is the
value shown in the income statement or balance sheet, `null` included.

| Ratio | Numerator | Denominator | `null` when |
| :-- | :-- | :-- | :-- |
| `gross_margin` | the statement figure `gross_profit` | the statement figure `revenue` | `gross_profit` or `revenue` is `null`, or `revenue` is zero |
| `operating_margin` | the line sum of operating revenue (revenue accounts with subclass `operating`), less the line sum of `cogs`, less the line sum of operating expense (expense accounts with subclass `operating`) | the statement figure `revenue` | `revenue` is `null` or zero |
| `net_margin` | the statement figure `net_income` | the statement figure `revenue` | `revenue` is `null` or zero |
| `current_ratio` | the closing total of current assets (every asset subclass except `fixed`) | the closing total of current liabilities (every liability subclass except `long_term`) | the denominator is zero, which includes a chart with no liability account |
| `runway_months` | the statement figure `cash_closing` | monthly net burn: (`outflows` less `inflows`) times 30.4375, divided by the period's length in days | the chart has no cash account, or net burn is not positive |
| `expense_concentration_top1` | the largest closing balance among expense accounts | the total of expense accounts | the chart has no expense account; `0.0000` when it has one and the total is zero |

## Observations

`observations[]` is a closed list of exactly `revenue` and `cash_closing`,
in that order, each with `metric`, `value` and `as_of`, where `as_of` equals
`period.end` and `value` is `null` when the figure is `null`. More metrics
later are additive through a Skill Upgrade issue. The list is shaped for a
planned upgrade of `monitoring/business_diagnostic`; no working chain exists
today.

## Size caps

Every cap is a module constant of the skill and has one test. The caps are
compared before any line is parsed, so an over-cap input is rejected from a
count alone; while counting, a non-list where a list is expected is the same
failure. Both `journal[].lines` and `prior_journals[].journal[].lines` carry
the lines-per-entry cap, and one figure bounds all lines across `journal`
and every prior journal together.

| Field | Unit | Limit | Code |
| :-- | :-- | :-- | :-- |
| `chart_of_accounts` | entries | 2000 | `INVALID_REQUEST` |
| `opening_balances` | entries | 2000 | `INVALID_REQUEST` |
| `journal` | entries | 20000 | `INVALID_REQUEST` |
| `journal[].lines` | entries | 200 | `INVALID_REQUEST` |
| `prior_journals` | entries | 24 | `INVALID_REQUEST` |
| `prior_journals[].journal` | entries | 20000 | `INVALID_REQUEST` |
| `prior_journals[].journal[].lines` | entries | 200 | `INVALID_REQUEST` |
| `lines (journal and prior journals together)` | entries | 200000 | `INVALID_REQUEST` |

## Validation order (fail-closed, deterministic)

The checks run in the numbered order and the first failing check's code is
returned; `detail.reason` names the check. Where a check runs per element
(per chart row, per opening line, per entry, per prior period), all checks of
one element run before the next element, in input order, so the earliest
failing element decides; within an entry, the shape of every line is checked
before the entry's date, currency and amounts. Where one check names several
fields, they are examined in the order of the Inputs section (`period`,
`currency`, `chart_of_accounts`, `opening_balances`, `journal`,
`prior_journals`, `options`; within `options`, `soft_close`,
`recurrence_min_periods`, `deviation_threshold`), never in the key order of
the input. The size caps are compared in that same field order, with the one
figure for all lines last.

Type rules (all checks): a JSON boolean is not an integer and an integer is
not a boolean; a JSON number is never accepted where a string is required,
and a string never where a number is; `null` counts as absent only where a
check says "absent or null". A string that contains a lone surrogate code
point (Unicode category `Cs`) is not a string and fails the check that tests
its field's type. Every pattern is matched against the whole string, so a
trailing line feed fails it.

`detail` is always an object with at least `reason` and, where it applies,
`path`, `limit` and `actual`, with one exception: `CLOSE_BLOCKED` carries
exactly `{"findings": [{"code", "refs"}, ...]}`, the blocking findings in
registry order and no other key. `path` is built from field names and list
indices only, for example `journal[2].lines[0].debit`; an unknown key is
never echoed. `detail` never contains input text other than validated ids
and dates.

### Both actions

1. `INVALID_REQUEST` — parameters are not an object.
2. `INVALID_REQUEST` — `action` absent, not a string, or not `validate` or `close`.
3. `INVALID_REQUEST` — an unknown top-level key.
4. `MISSING_AS_OF` — `as_of` absent or null.
5. `INVALID_DATE` — `as_of` not a string of the form `YYYY-MM-DD` naming a real date.
6. `INVALID_REQUEST` — `period` absent or not an object; `currency`, `chart_of_accounts` or `journal` absent; `chart_of_accounts` or `journal` not a list; `opening_balances` or `prior_journals` present and not a list; `options` present and not an object.
7. `INVALID_PERIOD` — `period` lacks `start` or `end`, or has another key.
8. `INVALID_DATE` — `period.start` or `period.end` malformed (`start` checked first).
9. `INVALID_PERIOD` — `period.start` after `period.end`.
10. `INVALID_REQUEST` — `currency` not a string matching `^[A-Z]{3}$`.
11. `INVALID_REQUEST` — `options` holds an unknown key, a value of the wrong type, or a value out of range.
12. `INVALID_REQUEST` — a size cap (chart rows; opening lines; journal entries; entries per prior period; lines per entry; prior periods; the one figure for all lines); counting treats a non-list where a list is expected as the same failure.
13. `INVALID_REQUEST` — per chart row: not an object; an unknown key; `account` absent, not a string, not matching its pattern, or already seen.
14. `UNKNOWN_ACCOUNT_CLASS` — per chart row: `class` absent or outside the closed set.
15. `UNKNOWN_SUBCLASS` — per chart row: `subclass` present and not allowed for the class.
16. `INVALID_REQUEST` — per opening line: not an object; an unknown key; `account` not a string; the account is a revenue or expense account; a second line for the same account.
17. `INVALID_AMOUNT` — per opening line: `debit` or `credit` fails the amount grammar.
18. `EMPTY_JOURNAL` — `journal` is an empty list.
19. `INVALID_REQUEST` — per entry: not an object; an unknown key; `id` absent, not a string, or not matching its pattern; `counterparty` present and not matching its pattern; `currency` present and not a string; `lines` absent or not a list; a line not an object, with an unknown key, or with `account` not a string.
20. `INVALID_DATE` — per entry: `date` malformed.
21. `CURRENCY_MISMATCH` — per entry: `currency` present and different from the request's.
22. `INVALID_AMOUNT` — per entry, per line: `debit` or `credit` fails the grammar.
23. `INVALID_REQUEST` — per prior period: not an object; an unknown key; `period` absent, not an object, or lacking `start` or `end`; `journal` absent or not a list; `currency` present and not a string.
24. `INVALID_DATE` — per prior period: `period.start` or `period.end` malformed.
25. `INVALID_PERIOD` — per prior period: start after end; it does not end before the current `period.start`; it overlaps an earlier-listed prior period; its length differs from the current period's by more than three days.
26. `CURRENCY_MISMATCH` — per prior period: `currency` present and different from the request's.
27. `INVALID_REQUEST` — per prior entry: the shape faults of check 19; a line's account that the chart does not hold.
28. `INVALID_DATE` — per prior entry: `date` malformed.
29. `INVALID_PERIOD` — per prior entry: `date` outside its period's dates.
30. `CURRENCY_MISMATCH` — per prior entry: `currency` present and different from the request's.
31. `INVALID_AMOUNT` — per prior entry, per line: `debit` or `credit` fails the grammar.

### close only

After the findings are computed:

32. `CLOSE_BEFORE_PERIOD_END` — `as_of` is before `period.end`, with or without `soft_close`.
33. `CLOSE_BLOCKED` — `soft_close` false and at least one blocking finding. `detail.findings` lists each as `{code, refs}` in registry order, as in the issue's example.

Duplicate entry ids and unmapped accounts in `opening_balances` or
`journal` are findings, not errors. `INTERNAL_ERROR` has no number: it is
returned only by the outermost handler of `execute()`, with `detail`
`{"reason": "internal_error", "exception": "<class name>"}`, when an
unexpected exception escapes the checks above.

## Example

A quarter with one sale, an opening cash balance funded by owner equity, and
three prior quarters in which `demo_landlord` was paid rent every month:

```json
{
  "action": "close",
  "as_of": "2026-07-05",
  "period": {"start": "2026-04-01", "end": "2026-06-30"},
  "currency": "USD",
  "chart_of_accounts": [
    {"account": "cash", "class": "asset", "subclass": "cash"},
    {"account": "sales", "class": "revenue"},
    {"account": "rent", "class": "expense"},
    {"account": "owner_equity", "class": "equity"}
  ],
  "opening_balances": [
    {"account": "cash", "debit": "4000.00", "credit": "0"},
    {"account": "owner_equity", "debit": "0", "credit": "4000.00"}
  ],
  "journal": [
    {"id": "j1", "date": "2026-04-03", "counterparty": "demo_customer",
     "lines": [{"account": "cash", "debit": "9800.00", "credit": "0"},
               {"account": "sales", "debit": "0", "credit": "9800.00"}]}
  ],
  "prior_journals": [{"period": {"start": "2026-01-01", "end": "2026-03-31"}, "journal": ["..."]}],
  "options": {"soft_close": false, "recurrence_min_periods": 2, "deviation_threshold": "0.20"}
}
```

returns `status: "completed"`, `closed: true`, a four-row trial balance
(`cash` closing `13800.00`), `revenue` `9800.00`, `net_income` `9800.00`,
`balanced: true`, `cash_closing` `13800.00`, one check and one note:
`EXPECTED_MISSING` for `rent/demo_landlord`, `cadence` `monthly`,
`expected_amount` `1500.00`, `detail`
`seen in 3 of the last 3 periods, absent in this one`. The rent row stays at
zero: the gap is named, not filled. With a second entry `j2` that has a
debit line only, `close` returns

```json
{"status": "error", "error": {"code": "CLOSE_BLOCKED", "detail": {"findings": [{"code": "UNBALANCED_ENTRY", "refs": ["j2"]}]}}}
```

and with `soft_close: true` the same input returns the statements with
`provisional: true`, `balanced: false` and the blocking finding first in
`notes`. The bundle's `fixtures/` folder holds the complete reference inputs
and outputs.
