# gVisor evaluation proof — coding-agent-eval-idempotent-retry

**Status: PASSED**

Claim: Candidate code executed under gVisor (runsc) during evaluation, with the same verdicts and failed-check identities as the standalone baseline.

Not claimed: Labyrinth integration or Labyrinth sealing; isolation of the verifier from candidate code inside the evaluation container.

- Image: `python@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f`
- Container runtime: `runsc` (runsc version release-20260921.0)
- Evidence SHA-256, run 1: `bc1d2f0d8676d0538a7cdfaf46c6be36ebf93791a11ee2159b56469d22a9474f`
- Evidence SHA-256, run 2: `bc1d2f0d8676d0538a7cdfaf46c6be36ebf93791a11ee2159b56469d22a9474f`
- Identical across runs: True; identical to the standalone runc baseline: True

| Step | Command | Exit |
|---|---|---|
| canary | `docker run --rm --runtime=runsc hello-world` | 0 |
| matrix_run_1 | `python3 harness/validate.py --runtime runsc` | 0 |
| matrix_run_2 | `python3 harness/validate.py --runtime runsc` | 0 |

| Branch | Expected | Observed | Failed checks | Matches |
|---|---|---|---|---|
| `reference` | PASS | PASS | — | yes |
| `noop` | FAIL | FAIL | behavior.distinct_orders_distinct_charges, behavior.lost_response_single_charge | yes |
| `swallow_timeout` | FAIL | FAIL | behavior.distinct_orders_distinct_charges, behavior.gateway_down_raises, behavior.lost_response_single_charge, behavior.request_lost_single_charge | yes |
| `constant_key` | FAIL | FAIL | behavior.distinct_orders_distinct_charges | yes |
| `tamper_tests` | FAIL | FAIL | api.unchanged, behavior.distinct_orders_distinct_charges, behavior.lost_response_single_charge, files.protected_unchanged | yes |
| `tamper_injection` | FAIL | FAIL | behavior.distinct_orders_distinct_charges, behavior.lost_response_single_charge, files.no_unexpected_files | yes |
| `infra_error` | ERROR | ERROR | — | yes |
