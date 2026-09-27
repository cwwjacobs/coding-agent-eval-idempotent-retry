"""Validation matrix: run every solution branch through the verifier and check
that each gets its expected verdict for the expected reasons.

    python3 harness/validate.py            # run the matrix
    python3 harness/validate.py --repeat   # run it twice and require identical evidence
    python3 harness/validate.py --lock     # regenerate verifier/fixture.lock.json

Needs Docker and Python 3.8+ on the host; the verifier itself runs inside a
container pinned by digest, with networking disabled.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "fixture"
VERIFIER = ROOT / "verifier"
SOLUTIONS = ROOT / "solutions"
RESULTS = ROOT / "results"
LOCK_PATH = VERIFIER / "fixture.lock.json"
IMAGE = "python@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f"  # python:3.12-slim
BRANCHES = ["reference", "noop", "swallow_timeout", "constant_key", "tamper_tests", "tamper_injection", "infra_error"]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def manifest(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): sha256_bytes(p.read_bytes())
            for p in sorted(root.rglob("*")) if p.is_file() and "__pycache__" not in p.parts}


def tree_hash(root: Path) -> str:
    return sha256_bytes(json.dumps(manifest(root), sort_keys=True).encode())


def write_lock() -> None:
    LOCK_PATH.write_text(json.dumps({"files": manifest(FIXTURE)}, indent=2, sort_keys=True) + "\n")
    print(f"wrote {LOCK_PATH.relative_to(ROOT)}")


def run_branch(branch: str, scratch: Path) -> dict:
    expected = json.loads((SOLUTIONS / branch / "expected.json").read_text())
    work = scratch / branch
    shutil.copytree(FIXTURE, work, ignore=shutil.ignore_patterns("__pycache__"))
    overlay = SOLUTIONS / expected.get("overlay_from", branch) / "files"
    if overlay.is_dir():
        shutil.copytree(overlay, work, dirs_exist_ok=True)
    cmd = ["docker", "run", "--rm", "--network", "none", "--read-only",
           "--tmpfs", "/tmp:rw,exec,size=64m", "--cap-drop", "ALL",
           "--security-opt", "no-new-privileges", "--pids-limit", "128", "--memory", "512m",
           "--user", f"{os.getuid()}:{os.getgid()}",
           "-v", f"{work}:/work:ro", "-v", f"{VERIFIER}:/verifier:ro"]
    if expected.get("fault_injection"):
        cmd += ["-e", f"EVAL_FAULT_INJECTION={expected['fault_injection']}"]
    cmd += [IMAGE, "python", "/verifier/verify.py", "/work"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    try:
        evidence = json.loads(proc.stdout)
    except json.JSONDecodeError:
        evidence = {"verdict": "ERROR", "error": f"harness: no verifier output (docker exit {proc.returncode}): "
                                                 f"{proc.stderr.strip()[-300:]}"}
    observed_failed = sorted(evidence.get("failed_checks", []))
    match = evidence["verdict"] == expected["verdict"] and observed_failed == sorted(expected["failed_checks"])
    return {"branch": branch, "about": expected["about"], "expected_verdict": expected["verdict"],
            "observed_verdict": evidence["verdict"], "expected_failed_checks": sorted(expected["failed_checks"]),
            "observed_failed_checks": observed_failed, "error": evidence.get("error"),
            "matches_expectation": match, "evidence": evidence}


def run_matrix() -> list[dict]:
    with tempfile.TemporaryDirectory(prefix="eval-matrix-") as tmp:
        return [run_branch(b, Path(tmp)) for b in BRANCHES]


def write_results(rows: list[dict]) -> None:
    (RESULTS / "evidence").mkdir(parents=True, exist_ok=True)
    for row in rows:
        (RESULTS / "evidence" / f"{row['branch']}.json").write_text(
            json.dumps(row["evidence"], indent=2, sort_keys=True) + "\n")
    matrix = {
        "schema": "coding-agent-eval.matrix.v1",
        "task": "idempotent-retry",
        "image": IMAGE,
        "fixture_sha256": tree_hash(FIXTURE),
        "verifier_sha256": tree_hash(VERIFIER),
        "all_branches_match_expectation": all(r["matches_expectation"] for r in rows),
        "rows": [{k: v for k, v in r.items() if k != "evidence"} for r in rows],
    }
    (RESULTS / "matrix.json").write_text(json.dumps(matrix, indent=2, sort_keys=True) + "\n")
    lines = ["# Validation matrix — idempotent-retry", "",
             f"Image `{IMAGE}`, network disabled. Fixture `{matrix['fixture_sha256'][:12]}`, "
             f"verifier `{matrix['verifier_sha256'][:12]}`.", "",
             "| Branch | Expected | Observed | Failed checks | Matches |", "|---|---|---|---|---|"]
    for r in rows:
        failed = ", ".join(c.replace("behavior.", "") for c in r["observed_failed_checks"]) or (r["error"] and "—") or "none"
        lines.append(f"| `{r['branch']}` | {r['expected_verdict']} | {r['observed_verdict']} | {failed} | "
                     f"{'yes' if r['matches_expectation'] else '**NO**'} |")
    lines += ["", "Each branch's full evidence is in `results/evidence/<branch>.json`.", ""]
    for r in rows:
        lines.append(f"- **{r['branch']}**: {r['about']}")
    (RESULTS / "SUMMARY.md").write_text("\n".join(lines) + "\n")


SHORT = {
    "behavior.no_fault_single_charge": "no_fault",
    "behavior.lost_response_single_charge": "lost_response",
    "behavior.request_lost_single_charge": "request_lost",
    "behavior.gateway_down_raises": "gateway_down",
    "behavior.distinct_orders_distinct_charges": "distinct_orders",
    "api.unchanged": "api",
    "tests.visible_pass": "visible_tests",
    "files.protected_unchanged": "protected_files",
    "files.no_unexpected_files": "unexpected_files",
}


def print_table(rows: list[dict]) -> None:
    print(f"\n{'BRANCH':<18}{'EXPECTED':<10}{'OBSERVED':<10}{'MATCH':<7}FAILED CHECKS (full ids in results/)")
    print("-" * 96)
    for r in rows:
        failed = ", ".join(SHORT.get(c, c) for c in r["observed_failed_checks"])
        if r["observed_verdict"] == "ERROR":
            failed = (r["error"] or "")[:60]
        print(f"{r['branch']:<18}{r['expected_verdict']:<10}{r['observed_verdict']:<10}"
              f"{'yes' if r['matches_expectation'] else 'NO':<7}{failed}")
    ok = all(r["matches_expectation"] for r in rows)
    print("-" * 96)
    print(f"{sum(r['matches_expectation'] for r in rows)}/{len(rows)} branches got the expected verdict "
          f"for the expected reasons -> {'VALIDATED' if ok else 'NOT VALIDATED'}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lock", action="store_true")
    ap.add_argument("--repeat", action="store_true")
    args = ap.parse_args()
    if args.lock:
        write_lock()
        return 0
    lock = json.loads(LOCK_PATH.read_text())["files"]
    if lock != manifest(FIXTURE):
        print("ERROR: fixture does not match verifier/fixture.lock.json (run --lock after intended changes)")
        return 2
    rows = run_matrix()
    write_results(rows)
    print_table(rows)
    if args.repeat:
        first = json.dumps([r["evidence"] for r in rows], sort_keys=True)
        second = json.dumps([r["evidence"] for r in run_matrix()], sort_keys=True)
        same = first == second
        print(f"repeat run: evidence {'byte-identical' if same else 'DIFFERS'} "
              f"(sha256 {sha256_bytes(first.encode())[:16]})")
        if not same:
            return 1
    return 0 if all(r["matches_expectation"] for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
