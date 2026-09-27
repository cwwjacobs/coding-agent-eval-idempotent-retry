"""Visible tests. The agent may run these; they must keep passing unchanged."""

import unittest

from payclient import PaymentClient, Receipt


class RecordingTransport:
    def __init__(self):
        self.calls = []

    def post(self, path, body, headers):
        self.calls.append((path, dict(body), dict(headers)))
        return {"charge_id": f"ch_{len(self.calls)}", "amount_cents": body["amount_cents"]}


class BasicChargeTests(unittest.TestCase):
    def test_successful_charge_returns_receipt(self):
        transport = RecordingTransport()
        receipt = PaymentClient(transport).charge("order-1", 1299)
        self.assertEqual(receipt, Receipt("order-1", "ch_1", 1299))

    def test_charge_posts_order_and_amount(self):
        transport = RecordingTransport()
        PaymentClient(transport).charge("order-2", 500)
        path, body, _ = transport.calls[0]
        self.assertEqual(path, "/v1/charges")
        self.assertEqual(body, {"order_id": "order-2", "amount_cents": 500})

    def test_retry_safe_with_explicit_key(self):
        transport = RecordingTransport()
        PaymentClient(transport).charge("order-3", 100, idempotency_key="k-3")
        self.assertEqual(transport.calls[0][2], {"Idempotency-Key": "k-3"})


if __name__ == "__main__":
    unittest.main()
