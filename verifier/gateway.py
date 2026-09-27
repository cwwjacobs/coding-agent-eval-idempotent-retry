"""Fake payment gateway, run as its own process.

It owns the ledger. Candidate code only reaches it through a request pipe, so
the ledger it reports is the authoritative record of what was charged.

argv: fault_plan_json req_fd resp_fd report_fd [crash]
Each request consumes the next fault from the plan (then "ok"):
  ok                     process the request and respond
  timeout_before_commit  drop the request; respond with a timeout
  timeout_after_commit   process the request; respond with a timeout
"""

from __future__ import annotations

import json
import os
import sys


def main() -> int:
    plan = json.loads(sys.argv[1])
    req = os.fdopen(int(sys.argv[2]), "r", encoding="utf-8")
    resp = os.fdopen(int(sys.argv[3]), "w", encoding="utf-8")
    report = os.fdopen(int(sys.argv[4]), "w", encoding="utf-8")
    if len(sys.argv) > 5 and sys.argv[5] == "crash":
        return 3  # fault injection: gateway dies before serving anything

    ledger: list[dict] = []
    by_key: dict[str, dict] = {}
    requests: list[dict] = []

    for line in req:
        msg = json.loads(line)
        fault = plan[len(requests)] if len(requests) < len(plan) else "ok"
        headers = {str(k).lower(): str(v) for k, v in (msg.get("headers") or {}).items()}
        key = headers.get("idempotency-key")
        body = msg.get("body") or {}
        requests.append({"path": msg.get("path"), "body": body, "idempotency_key": key, "fault": fault})

        if msg.get("path") != "/v1/charges":
            reply = {"error": "unknown path"}
        elif fault == "timeout_before_commit":
            reply = {"timeout": True}
        else:
            if key is not None and key in by_key:
                charge = by_key[key]
            else:
                charge = {
                    "charge_id": f"ch_{len(ledger) + 1}",
                    "order_id": body.get("order_id"),
                    "amount_cents": body.get("amount_cents"),
                }
                ledger.append(charge)
                if key is not None:
                    by_key[key] = charge
            reply = {"timeout": True} if fault == "timeout_after_commit" else dict(charge)
        resp.write(json.dumps(reply) + "\n")
        resp.flush()

    report.write(json.dumps({"ledger": ledger, "requests": requests}))
    report.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
