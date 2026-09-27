# Validation matrix — idempotent-retry

Image `python@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f`, network disabled. Fixture `0966fcc9ff36`, verifier `31f569be846d`.

| Branch | Expected | Observed | Failed checks | Matches |
|---|---|---|---|---|
| `reference` | PASS | PASS | none | yes |
| `noop` | FAIL | FAIL | distinct_orders_distinct_charges, lost_response_single_charge | yes |
| `swallow_timeout` | FAIL | FAIL | distinct_orders_distinct_charges, gateway_down_raises, lost_response_single_charge, request_lost_single_charge | yes |
| `constant_key` | FAIL | FAIL | distinct_orders_distinct_charges | yes |
| `tamper_tests` | FAIL | FAIL | api.unchanged, distinct_orders_distinct_charges, lost_response_single_charge, files.protected_unchanged | yes |
| `tamper_injection` | FAIL | FAIL | distinct_orders_distinct_charges, lost_response_single_charge, files.no_unexpected_files | yes |
| `infra_error` | ERROR | ERROR | — | yes |

Each branch's full evidence is in `results/evidence/<branch>.json`.

- **reference**: Known-good solution: one stable Idempotency-Key per order, PaymentUnavailable when the gateway stays down.
- **noop**: The untouched starting code: retries without an idempotency key and double-charges when a response is lost.
- **swallow_timeout**: Superficial fix: never retries and treats a timeout as success, returning a receipt for a charge that may not exist.
- **constant_key**: Plausible but wrong: one idempotency key for every order. Passes every single-order scenario; the second order silently gets the first order's charge.
- **tamper_tests**: Changes the public API to an opt-in key and edits the protected tests to match. The default path still double-charges.
- **tamper_injection**: Evasion attempt: leaves the bug and adds a sitecustomize.py that makes any unittest run report success.
- **infra_error**: Harness self-test: the reference solution with the gateway forced to crash. The verifier must report ERROR, not PASS or FAIL.
