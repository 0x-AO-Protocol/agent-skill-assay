# Prompt Injection Firewall R-19 Evidence

Version: `0.1.0` package release; R-19 hardening profile

This file records the v0.2 hardening checks for
`security/prompt_injection_firewall`.

## FW-1 — Resource caps fail closed

| Cap | Limit | Behavior | Evidence |
| :--- | :--- | :--- | :--- |
| Input bytes | `MAX_INPUT_BYTES = 65536` | Returns a critical `resource_limit` finding and `is_safe=false` | `test_input_size_cap_fails_closed` |
| Decode candidates | `MAX_DECODE_CANDIDATES = 64` | Records the cap finding, scans only the bounded prefix, and fails closed | `test_decode_candidate_cap_fails_closed` |
| Decoded bytes | `MAX_DECODE_BYTES = 8192` | Records a redacted critical cap finding and fails closed | `test_oversized_encoded_payload_fails_closed` |
| Nested depth | `MAX_DECODE_DEPTH = 3` | Records a depth-cap finding and fails closed | `test_decode_depth_cap_fails_closed` |

The cap findings are profile-independent. A lenient profile cannot convert a
partial scan into a clean result.

The scanner is local-only and has no network or model call surface. Decoded
attacker text is never copied into the result evidence or telemetry; findings
report the decoder layer and pattern identifier instead.

## FW-2 — Detector coverage

The detector IDs are declared in the manifest and repeated in the instructions
and catalog. `test_every_documented_detector_has_a_positive_fixture` asserts
that every documented ID has a positive fixture, while
`test_documented_detector_set_is_synced_across_public_surfaces` prevents
documentation drift. The underlying fixtures cover hidden markup, invisible
Unicode, confusable skeletons, encoded payloads, instruction lexicon,
context-mismatch, and resource limits.

## FW-3 — Stable `is_safe` semantics

The manifest defines `is_safe=true` once. The same `_verdict()` path is used
for `strict`, `balanced`, and `lenient`; table-driven tests assert that clean
input is safe and a critical exfiltration is unsafe at every profile.
Resource-limit failures are also unsafe at every profile.

## FW-4 — Decoded text is redacted

`test_decoded_attacker_text_is_not_echoed` checks serialized output, and
`test_decoded_attacker_text_is_absent_from_logs_and_telemetry` checks captured
logs plus the findings payload. Evidence contains only bounded metadata such
as a decoder layer and pattern identifier, never decoded attacker text.

## FW-5 — v0.1 regression corpus

`fixtures/v0_1_corpus.json` freezes the v0.1 unsafe corpus. The
`test_v01_fixture_corpus_remains_unsafe` test reruns every fixture against the
v0.2 implementation and requires the recorded unsafe verdict.

## FW-6 — Offline and dependency contract

The manifest keeps `requirements: []`. An AST test checks the scanner
implementation for network imports and permits only the non-network
`urllib.parse` helper used for URL decoding. The firewall has no network call,
model, or API-key surface.

Executable evidence:

```bash
python -m pytest skills/security/prompt_injection_firewall/test_skill.py -q
```
