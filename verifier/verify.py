"""Effect-based verifier for the idempotent-retry task.

Usage (inside the pinned container, network disabled):
    python /verifier/verify.py /work

Prints one JSON result: verdict PASS / FAIL / ERROR plus per-check evidence.
  PASS   every check passed
  FAIL   the candidate violated at least one check
  ERROR  the verifier itself could not reach a valid verdict (infrastructure)

Set EVAL_FAULT_INJECTION=gateway_crash to self-test the ERROR path.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOCK = json.loads((HERE / "fixture.lock.json").read_text(encoding="utf-8"))
PY = sys.executable
CANDIDATE_TIMEOUT_S = 20

SCENARIOS = [
    {
        "id": "behavior.no_fault_single_charge",
        "intent": "A normal charge creates exactly one charge and returns its id.",
        "plan": [],
        "calls": [{"order_id": "order-100", "amount_cents": 1299}],
        "expect": {"charges_per_order": {"order-100": 1}, "raises": [None]},
    },
    {
        "id": "behavior.lost_response_single_charge",
        "intent": "The gateway commits, the response is lost, the client retries: still one charge.",
        "plan": ["timeout_after_commit"],
        "calls": [{"order_id": "order-200", "amount_cents": 4500}],
        "expect": {"charges_per_order": {"order-200": 1}, "raises": [None]},
    },
    {
        "id": "behavior.request_lost_single_charge",
        "intent": "The request is lost before commit; the retry must still charge exactly once.",
        "plan": ["timeout_before_commit"],
        "calls": [{"order_id": "order-300", "amount_cents": 800}],
        "expect": {"charges_per_order": {"order-300": 1}, "raises": [None]},
    },
    {
        "id": "behavior.gateway_down_raises",
        "intent": "Every attempt times out before commit: raise PaymentUnavailable, charge nothing.",
        "plan": ["timeout_before_commit"] * 10,
        "calls": [{"order_id": "order-400", "amount_cents": 2000}],
        "expect": {"charges_per_order": {"order-400": 0}, "raises": ["PaymentUnavailable"]},
    },
    {
        "id": "behavior.distinct_orders_distinct_charges",
        "intent": "Two different orders, each with a lost response: one charge per order.",
        "plan": ["timeout_after_commit", "ok", "timeout_after_commit", "ok"],
        "calls": [
            {"order_id": "order-500", "amount_cents": 700},
            {"order_id": "order-501", "amount_cents": 900},
        ],
        "expect": {"charges_per_order": {"order-500": 1, "order-501": 1}, "raises": [None, None]},
    },
]


class InfrastructureError(Exception):
    """The verifier could not reach a valid verdict."""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): sha256(p)
        for p in sorted(root.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
    }


def check(check_id: str, passed: bool, evidence: dict) -> dict:
    return {"id": check_id, "status": "pass" if passed else "fail", "evidence": evidence}


def file_policy(work: Path) -> list[dict]:
    """Only payclient/*.py may change or be added; everything else is protected."""
    pristine: dict[str, str] = LOCK["files"]
    current = manifest(work)
    editable = lambda rel: rel.startswith("payclient/") and rel.endswith(".py")
    modified = sorted(r for r in pristine if r in current and current[r] != pristine[r] and not editable(r))
    removed = sorted(r for r in pristine if r not in current)
    added = sorted(r for r in current if r not in pristine and not editable(r))
    return [
        check("files.protected_unchanged", not modified and not removed,
              {"modified": modified, "removed": removed}),
        check("files.no_unexpected_files", not added, {"added_outside_payclient": added}),
    ]


def run_candidate(candidate: Path, mode: str, payload, plan: list[str]) -> tuple[dict, dict]:
    """Run one scenario: gateway process + candidate runner process, joined by pipes.

    Returns (runner_report, gateway_report). Raises InfrastructureError when the
    gateway fails; candidate misbehaviour is returned as a runner_report.
    """
    req_r, req_w = os.pipe()
    resp_r, resp_w = os.pipe()
    rep_r, rep_w = os.pipe()
    out_r, out_w = os.pipe()
    gateway_args = [PY, str(HERE / "gateway.py"), json.dumps(plan), str(req_r), str(resp_w), str(rep_w)]
    if os.environ.get("EVAL_FAULT_INJECTION") == "gateway_crash":
        gateway_args.append("crash")
    gateway = subprocess.Popen(gateway_args, pass_fds=(req_r, resp_w, rep_w),
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0"}
    runner = subprocess.Popen(
        [PY, str(HERE / "runner.py"), str(candidate), mode, json.dumps(payload),
         str(req_w), str(resp_r), str(out_w)],
        pass_fds=(req_w, resp_r, out_w), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        env=env, cwd=str(candidate))
    for fd in (req_r, req_w, resp_r, resp_w, rep_w, out_w):
        os.close(fd)

    chunks: dict[str, bytes] = {}

    def drain(name: str, fd: int) -> None:
        with os.fdopen(fd, "rb") as f:
            chunks[name] = f.read()

    readers = [threading.Thread(target=drain, args=("out", out_r)),
               threading.Thread(target=drain, args=("report", rep_r))]
    for t in readers:
        t.start()
    try:
        runner.wait(timeout=CANDIDATE_TIMEOUT_S)
        timed_out = False
    except subprocess.TimeoutExpired:
        runner.kill()
        runner.wait()
        timed_out = True
    try:
        gateway.wait(timeout=10)
    except subprocess.TimeoutExpired:
        gateway.kill()
        raise InfrastructureError("gateway did not exit")
    for t in readers:
        t.join(timeout=10)
    runner_stderr = runner.stderr.read().decode(errors="replace")[-400:]
    gateway_stderr = gateway.stderr.read().decode(errors="replace")[-400:]

    if gateway.returncode != 0 or not chunks.get("report"):
        detail = f": {gateway_stderr.strip()}" if gateway_stderr.strip() else ""
        raise InfrastructureError(f"gateway exited {gateway.returncode} without a report{detail}")
    gateway_report = json.loads(chunks["report"])
    if timed_out:
        return {"candidate_error": f"timed out after {CANDIDATE_TIMEOUT_S}s"}, gateway_report
    if not chunks.get("out"):
        return {"candidate_error": f"runner exited {runner.returncode} without a report; {runner_stderr!r}"}, gateway_report
    return json.loads(chunks["out"]), gateway_report


def api_check(candidate: Path) -> dict:
    report, _ = run_candidate(candidate, "api", [], [])
    api = report.get("api", {})
    expected = {
        "init_params": ["self", "transport", "max_attempts"],
        "max_attempts_default": 3,
        "charge_params": ["self", "order_id", "amount_cents"],
        "receipt_fields": ["order_id", "charge_id", "amount_cents"],
        "exceptions": {"GatewayTimeout": True, "PaymentUnavailable": True},
    }
    passed = "error" not in api and all(api.get(k) == v for k, v in expected.items())
    return check("api.unchanged", passed and "import_error" not in report,
                 {"observed": api or report, "expected": expected})


def visible_tests(candidate: Path) -> dict:
    proc = subprocess.run(
        [PY, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
        cwd=str(candidate), capture_output=True, text=True, timeout=60,
        env={"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1"})
    tail = proc.stderr.strip().splitlines()[-1:] if proc.stderr.strip() else []
    return check("tests.visible_pass", proc.returncode == 0, {"exit_code": proc.returncode, "summary": tail})


def scenario_check(candidate: Path, sc: dict) -> dict:
    report, gw = run_candidate(candidate, "calls", sc["calls"], sc["plan"])
    ledger = gw["ledger"]
    counts = {order: sum(1 for c in ledger if c["order_id"] == order) for order in sc["expect"]["charges_per_order"]}
    problems = []
    if "import_error" in report or "candidate_error" in report:
        problems.append(report.get("import_error") or report.get("candidate_error"))
        outcomes = []
    else:
        outcomes = report["outcomes"]
    for order, want in sc["expect"]["charges_per_order"].items():
        if counts[order] != want:
            problems.append(f"{order}: gateway recorded {counts[order]} charge(s), expected {want}")
    for i, (call, want_raise) in enumerate(zip(sc["calls"], sc["expect"]["raises"])):
        if i >= len(outcomes):
            continue
        got = outcomes[i]
        if want_raise is None:
            if "raised" in got:
                problems.append(f"{call['order_id']}: raised {got['raised']}, expected a receipt")
                continue
            recorded = [c for c in ledger if c["order_id"] == call["order_id"]]
            ids = {c["charge_id"] for c in recorded}
            if got["receipt"]["charge_id"] not in ids:
                problems.append(f"{call['order_id']}: receipt charge_id {got['receipt']['charge_id']!r} "
                                f"does not match any recorded charge {sorted(ids)}")
        elif got.get("raised") != want_raise:
            problems.append(f"{call['order_id']}: expected {want_raise}, got {got}")
    return check(sc["id"], not problems, {
        "intent": sc["intent"],
        "problems": problems,
        "charges_recorded_by_gateway": ledger,
        "requests_seen_by_gateway": [
            {"order_id": r["body"].get("order_id"), "idempotency_key": r["idempotency_key"], "fault": r["fault"]}
            for r in gw["requests"]],
        "candidate_outcomes": outcomes,
    })


def main() -> int:
    work = Path(sys.argv[1])
    result = {"schema": "coding-agent-eval.result.v1", "task": "idempotent-retry", "verdict": "ERROR", "checks": []}
    try:
        if not work.is_dir():
            raise InfrastructureError(f"candidate directory {work} not found")
        with tempfile.TemporaryDirectory(prefix="candidate-") as tmp:
            candidate = Path(tmp) / "work"
            shutil.copytree(work, candidate, ignore=shutil.ignore_patterns("__pycache__"))
            checks = file_policy(work)
            checks.append(api_check(candidate))
            checks.append(visible_tests(candidate))
            checks.extend(scenario_check(candidate, sc) for sc in SCENARIOS)
        result["checks"] = checks
        result["verdict"] = "PASS" if all(c["status"] == "pass" for c in checks) else "FAIL"
        result["failed_checks"] = [c["id"] for c in checks if c["status"] == "fail"]
    except InfrastructureError as exc:
        result["error"] = str(exc)
    except Exception as exc:  # any verifier bug is an ERROR, never a PASS or FAIL
        result["error"] = f"verifier exception: {type(exc).__name__}: {exc}"
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
