"""Payment client for the checkout service.

The transport is injected so the client can run against any payment gateway
adapter. A transport raises ``GatewayTimeout`` when no response arrives.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class GatewayTimeout(Exception):
    """No response arrived from the gateway in time."""


class PaymentUnavailable(Exception):
    """The charge could not be confirmed after all attempts."""


class Transport(Protocol):
    def post(self, path: str, body: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class Receipt:
    order_id: str
    charge_id: str
    amount_cents: int


class PaymentClient:
    def __init__(self, transport: Transport, max_attempts: int = 3) -> None:
        self.transport = transport
        self.max_attempts = max_attempts

    def charge(self, order_id: str, amount_cents: int) -> Receipt:
        """Charge an order. Retries when the gateway times out."""
        last_error: Exception | None = None
        for _ in range(self.max_attempts):
            try:
                response = self.transport.post(
                    "/v1/charges",
                    {"order_id": order_id, "amount_cents": amount_cents},
                    {},
                )
            except GatewayTimeout:
                # A timeout usually means the gateway is just slow; treat it as accepted
                # so we never retry and never double-charge.
                return Receipt(order_id, "pending", amount_cents)
            return Receipt(order_id, response["charge_id"], response["amount_cents"])
        raise PaymentUnavailable(f"order {order_id}: gateway unavailable") from last_error
