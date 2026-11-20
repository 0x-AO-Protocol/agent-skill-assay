## Skill context: compliance/scoped_risk_literature_scan

This skill screens the publications that your agent collected for one review cycle and checks the annotations your agent writes for the items it keeps. It makes no model call and opens nothing: it reads only the request, and identical requests return identical results.

### When to call it

- Once per review cycle, after the window has closed, when the scope (risk domains and jurisdictions) and the configured sources are known.
- `screen` first. Read the kept items yourself, write one annotation per kept item, then call `compile` with the same request plus `annotations` (and `attestation` when the practitioner attests time spent).

Do not call it to change a method, model or assumption, to advise on an engagement, to search beyond the configured sources, or without a defined window and scope.

### What you supply

- `cycle_start`, `cycle_end`: real dates `YYYY-MM-DD`; the window holds 1 to 92 days, both ends included.
- `risk_domains` (1 to 20) and `jurisdictions` (1 to 50): unique codes that bound the scan.
- `sources` (1 to 50): each with a unique `name`, a `url` (data only, never opened; at most 512 characters, and the whole value must match `https://[A-Za-z0-9./_-]*`) and a `kind`: `literature`, `regulatory`, `technology` or `economic`.
- `max_items`: 1 to 200.
- `client_exposure_tags` (optional, up to 50) and `previous_tallies.by_domain` (optional; scope domains to last cycle's counts, a missing count read as 0).
- `collection`: exactly one report per configured source, each with `source_name`, `status` (`ok` or `unreachable`) and `items`. An item has `title` (up to 300 characters), `published_date`, `jurisdiction`, `domains` (1 to 10), optional `exposure_tags` (up to 20) and optional `url`. At most 500 items per report and 2000 in all. Read the sources with your own tools; report an unreachable source as `unreachable`, and the run stops.
- `log_destination` (optional): echoed in the `compile` record; the skill writes nothing there.

Text values must not be blank and must hold no control character (U+0000 to U+001F, U+007F to U+009F, line feed included). Other characters, such as U+00A0, U+2028 and U+3000, are ordinary text.

### What the screen does

1. Each item is excluded for the first reason that applies: `out_of_window`, then `out_of_jurisdiction`, then `out_of_domain` (none of its domains is in scope).
2. The rest are ordered: relevant to clients first (an exposure tag is one of `client_exposure_tags`), then newest `published_date` first, then the source's position in `sources`, then the normalized title by code point, then the position in `collection`.
3. Walking that order, an item whose normalized title, date and jurisdiction equal an earlier item's is excluded as `duplicate`. The normalized title folds the ASCII letters `A`-`Z` to `a`-`z` and collapses each run of U+0020 spaces to one space; no other character changes, so `É` and `é`, or U+00A0 and a space, stay different.
4. Items after the first `max_items` are excluded as `over_cap`. Kept items get `item_ref` `R001`, `R002`, ... in that order.
5. A kept item relevant to clients has `follow_up_required: true` and `follow_up_due_by` = `cycle_end` plus `window_days`.
6. `tallies` count the in-scope items after duplicates and before the cap: per scope domain (an item counts once in each of its scope domains), per jurisdiction, per source kind, and `change_by_domain` (this cycle minus the previous one; `null` without `previous_tallies`).

### Writing the annotations

`screen` returns `annotation_slots`, `impact_areas` and `likelihoods`. For `compile`, send exactly one annotation per kept item: `item_ref`, `summary` (up to 2000 characters) and `impact` with `area` (`exposures`, `methods`, `both` or `none`), `likelihood` (`low`, `medium` or `high`) and `note` (up to 1000 characters).

The check covers presence, keys, enums and caps only. `summary` and `impact.note` are not checked for content: the skill does not read the publications, so it cannot tell whether a summary is accurate. You write them, and a practitioner reviews them; every record is returned with `review_status: pending_review`.

`competence_minutes_logged` is `attestation.minutes` only when `attestation.attested` is `true`; otherwise 0.

### Errors

An error returns `{"status": "error", "error": {"code", "detail"}}` with no record and no minutes. For a failed check, the detail carries `reason` and `path`, and where useful a `limit` or an `item_ref` the skill assigned; it never repeats your text. Correct the request rather than retry it unchanged.

The checks run in this order, and the first failure is returned:

1. the request is an object (`INVALID_REQUEST`, `parameters_not_object`);
2. `action` is `screen` or `compile` (`unknown_action`);
3. no unknown top-level key (`unknown_key`);
4. required fields present, then top-level types (`missing_field`, `invalid_type`);
5. `risk_domains` and `jurisdictions` not empty (`EMPTY_SCOPE`);
6. list-length caps (`size_cap`);
7. every field's shape, pattern, enum, range and text cap, in field order (`unknown_key`, `missing_field`, `invalid_type`, `invalid_value`, `out_of_range`, `size_cap`);
8. real calendar dates (`INVALID_DATE`);
9. the window: end not before start, at most 92 days (`WINDOW_INVALID`);
10. no repeated domain, jurisdiction, tag, source name or collection report (`DUPLICATE_KEY`);
11. `previous_tallies` names scope domains only (`INVALID_REQUEST`, `unknown_domain`);
12. reports: each names a configured source (`UNKNOWN_SOURCE`), each configured source has a report (`SOURCE_NOT_REPORTED`), each report is reachable (`SOURCE_UNREACHABLE`);
13. `attestation` and `annotations` only with `compile` (`field_not_allowed`);
14. `compile` only: each annotation names a kept item (`UNKNOWN_ITEM_REF`) once (`DUPLICATE_KEY`), and every kept item has an annotation (`ANNOTATION_MISSING`).

An unexpected internal fault returns `INTERNAL_ERROR` with the exception class name only.
