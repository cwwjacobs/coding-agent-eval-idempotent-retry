"""Runs candidate code against the gateway pipe and reports what it returned.

argv: candidate_dir mode payload_json req_fd resp_fd out_fd
mode "calls": payload is a list of {"order_id", "amount_cents"}
mode "api":   payload is ignored; reports the public API shape

What this process reports is only used alongside the gateway's ledger: a
candidate could forge its own report, but not the gateway's record.
"""

from __future__ import annotations

import dataclasses
import inspect
import json
import os
import sys


def main() -> int:
    candidate_dir, mode, payload = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
    req = os.fdopen(int(sys.argv[4]), "w", encoding="utf-8")
    resp = os.fdopen(int(sys.argv[5]), "r", encoding="utf-8")
    out = os.fdopen(int(sys.argv[6]), "w", encoding="utf-8")

    def report(obj: dict) -> None:
        out.write(json.dumps(obj))
        out.close()

    sys.path.insert(0, candidate_dir)
    try:
        import payclient
    except BaseException as exc:  # candidate code failed to import
        report({"import_error": f"{type(exc).__name__}: {exc}"})
        return 0

    if mode == "api":
        info: dict = {}
        try:
            info["init_params"] = list(inspect.signature(payclient.PaymentClient.__init__).parameters)
            info["max_attempts_default"] = inspect.signature(
                payclient.PaymentClient.__init__).parameters["max_attempts"].default
            info["charge_params"] = list(inspect.signature(payclient.PaymentClient.charge).parameters)
            info["receipt_fields"] = [f.name for f in dataclasses.fields(payclient.Receipt)]
            info["exceptions"] = {
                name: issubclass(getattr(payclient, name), Exception)
                for name in ("GatewayTimeout", "PaymentUnavailable")
            }
        except BaseException as exc:
            info["error"] = f"{type(exc).__name__}: {exc}"
        report({"api": info})
        return 0

    class PipeTransport:
        def post(self, path, body, headers):
            req.write(json.dumps({"path": path, "body": body, "headers": headers}) + "\n")
            req.flush()
            reply = json.loads(resp.readline())
            if reply.get("timeout"):
                raise payclient.GatewayTimeout("simulated: no response from gateway")
            return reply

    outcomes = []
    for call in payload:
        try:
            client = payclient.PaymentClient(PipeTransport())
            receipt = client.charge(call["order_id"], call["amount_cents"])
            outcomes.append({"receipt": {
                "order_id": receipt.order_id,
                "charge_id": receipt.charge_id,
                "amount_cents": receipt.amount_cents,
            }})
        except BaseException as exc:
            outcomes.append({"raised": type(exc).__name__})
    report({"outcomes": outcomes})
    return 0


if __name__ == "__main__":
    sys.exit(main())
