## Skill context: office/underwriting_condition_reply_tracker

This skill tracks the underwriting conditions on one loan file against the closing date and checks the reply drafts your agent prepares for the open conditions. It makes no model call and reads only the request; on one Python version, identical requests return identical results. It sends nothing: every draft comes back held for operator review.

### When to call it

- `track`: the countdown only (days to closing, days open, lead-window flags and escalations).
- `scaffold`: the countdown plus one slot per open condition, with the documents that may be cited, the marker form and the caps.
- `compile`: the countdown plus one checked draft per open condition.

Do not call it to decide whether a condition is satisfied, to send a reply, or to update condition states; those stay with the operator.

### What you supply

- `as_of_date` (the run date) and `closing_deadline`, both real dates `YYYY-MM-DD`; the deadline is not before the run date.
- `escalation_lead_days` (optional, 0 to 365, default 7).
- `verified_file`: 1 to 200 documents with a unique `document_id`, a `title` (up to 200 characters), a `document_type` and an optional `summary` (up to 1000 characters). The whole `document_id` value must match both `[A-Za-z0-9_-]{1,64}` and `[A-Za-z0-9_]+(?:-[A-Za-z0-9_]+)*`: ASCII letters, digits and `_`, joined by single hyphens, 1 to 64 characters. The skill trusts the inventory as given. A summary is read only as evidence for the figure check and never appears in any output.
- `condition_requests`: 1 to 100 conditions with a unique `condition_id` (the whole value must match the same two patterns as a `document_id`), `requested_on` (not after the run date), a `description` (up to 2000 characters), a `status` (`open`, `resolved` or `waived`) and an optional `prior_reply_count` (0 to 50).
- For `compile` only, `drafts`: exactly one per open condition, each with `condition_id`, `cited_document_ids` (1 to 20 unique ids from the verified file) and, optionally, `issue_summary` and `proposed_resolution` (up to 600 characters each) and `reply_body` (up to 4000 characters).

Texts must not be blank and may hold no control character except line feed. Titles and descriptions may hold no decimal digit other than `0`-`9`, because the templates copy them into drafts.

### Writing a draft

- Cite a document in any draft text with the marker `[doc:<document_id>]`. Every `[doc:` must start a well-formed marker, every marker must name a document you list in `cited_document_ids`, and when you supply `reply_body` it must carry a marker for each cited document.
- Each text you supply must pass the figure rule. A figure is supported when it comes from the condition's `description`, the `title` or `summary` of a document the draft cites, the dates (`requested_on`, `as_of_date`, `closing_deadline`), the computed values (days to closing, days open, lead window, prior reply count), or is a `document_id` or `condition_id` of the request. The words of those texts are read by the same rules.
- A decimal digit other than `0`-`9` fails, and citation markers are set aside. The rest is read in words: runs of ASCII letters, digits and `_` joined by `-`, `.` and `,`; a run of `-`, `.` and `,` that no ASCII letter, digit or `_` follows ends the word (`8,000.` reads as `8,000`). First, a `.` that directly precedes a digit and directly follows no ASCII letter, digit or `_` is read as `0.` (`.5` is 0.5).
- Numbers: with commas, a first group of one to three digits, then groups of a comma and three digits; without commas, any digit run; either with at most one decimal part. `1,500`, `1500` and `1500.00` all read as 1500; a sign is not part of a number. Dates are `YYYY-MM-DD`.
- A word that is not an id, a date or a number is read in pieces split at `-` (`1,500-2,000` holds 1,500 and 2,000); a comma or point next to that `-` stays in its piece (`5.-6` holds `5.` and 6).
- A piece made only of digits, commas and points that is not a number of these forms (`5,0000`, `6.0.0`, `5,,000`) is supported only when an admitted text holds the same run whole, not inside a longer one; its parts never support it.
- A piece with an ASCII letter or `_` is split into tokens at each run of `.` and `,`; two neighbouring tokens are joined when a digit ends the first and a digit begins the second. Joined tokens of digits only are one run (`approx.5,0000` holds `5,0000`; `No.1,500` holds 1,500). Joined tokens that include a token with an ASCII letter or `_` are one figure, so a run joined to such a token is read with it (`v6.0.0`, `1.5M`, `FY2027,000`), supported only when it appears whole in an admitted text. Tokens that are not joined are read apart (`Q1,Q2` holds `Q1` and `Q2`).
- A lone token that joins a digit with an ASCII letter or `_` (such as `Q4`, `5M`, `FY2027`) must equal an id or a token of an admitted text, also one inside a longer figure (an admitted `v6.0.0` supports `v6`, but neither `0` nor `6.0.0`).
- The English number words `two` to `twenty`, the tens, `hundred`, `thousand`, `million`, `billion`, `trillion`, `double`, `twice`, `triple`, `doubled`, `tripled` and `halved`, folded on ASCII letters only, must be tokens of an admitted text.
- Superscript and circled digits, fractions, Roman numerals and CJK numerals are not read as figures.
- For each figure of these forms, the rule checks that it has a source in the request, not which document it came from and not that it is correct.
- Leave a text out and the skill composes it from a fixed template, cut at its cap before the last space (with `...`). A composed text repeats the condition's description and the cited titles as they are; the marker checks read only the texts you supply.

### Outputs

`days_until_closing`, `lead_window_days`, `tracker` (per condition, in input order), `escalations`, `operator_review_pending` (always true) and `run_summary`; `scaffold` adds `slots`; `compile` adds `drafts` in condition order, each with `cited_document_ids` in inventory order and `composed_by` (`caller`, `template` or `mixed`).

### Errors

An error returns `{"status": "error", "error": {"code", "detail"}}` and nothing else. For a failed check, the detail carries `reason`, `path` and, for a size cap, `limit` and `actual`; it never repeats your text.

The checks run in this order, and the first failure is returned:

1. the request is an object; 2. `action`; 3. no unknown top-level key; 4. required fields, then top-level types; 5. `verified_file` not empty (`EMPTY_VERIFIED_FILE`); 6. list-length caps; 7. every field's shape, pattern, enum, range and text cap; 8. real dates (`INVALID_DATE`); 9. no repeated `document_id` or `condition_id` (`DUPLICATE_ID`); 10. the deadline, then each `requested_on`, against the run date (`DEADLINE_BEFORE_AS_OF`, `REQUEST_AFTER_AS_OF`); 11. `drafts` only with `compile`; 12. an open condition for `scaffold` and `compile` (`NO_OPEN_CONDITION`); 13. each draft in order: a known condition (`UNKNOWN_CONDITION`), open (`DRAFT_FOR_CLOSED_CONDITION`), not repeated (`DUPLICATE_ID`); 14. a draft for each open condition (`DRAFT_MISSING`); 15. each draft in order: cited ids not repeated (`DUPLICATE_ID`) and known (`UNKNOWN_DOCUMENT`), then, each over the supplied texts in the order `issue_summary`, `proposed_resolution`, `reply_body`: markers well formed (`MALFORMED_CITATION`), markers known (`UNKNOWN_DOCUMENT`), markers cited, and every cited id marked in a supplied `reply_body` (`CITATION_MISMATCH`); 16. the figure rule, draft by draft and text by text (`UNSUPPORTED_FIGURE`).

An unexpected internal fault returns `INTERNAL_ERROR` with the exception class name only.
