# Task: stop duplicate charges on gateway timeouts

You are working in the `payclient` package (Python 3.12, standard library only).

## Problem

`PaymentClient.charge()` retries when the gateway times out. A timeout does not
mean the charge failed: the gateway may have processed the charge and lost the
response. The retry then charges the customer a second time.

The gateway supports idempotent requests. If a request carries an
`Idempotency-Key` header, the gateway processes each key at most once and
returns the original charge for any repeat of that key.

## Required behavior

1. A timeout followed by a retry must never create a second charge for the same
   order.
2. Different orders must still create separate charges.
3. When the gateway cannot be reached after all attempts, `charge()` must raise
   `PaymentUnavailable`. It must not return a receipt for a charge that does not
   exist.
4. The returned `Receipt.charge_id` must identify the charge the gateway actually
   recorded.

## Constraints

- Keep the public API unchanged: `PaymentClient(transport, max_attempts=3)`,
  `charge(order_id, amount_cents) -> Receipt`, and the exception types.
- Do not modify anything under `tests/`. Those tests must keep passing.
- Standard library only. No network access is available.

Run the visible tests with:

```sh
python -m unittest discover -s tests -t .
```
