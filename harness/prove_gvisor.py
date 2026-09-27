"""gVisor proof: run the complete validation matrix twice with the evaluation
containers under the runsc runtime, and record what happened.

    python3 harness/prove_gvisor.py

Exit codes: 0 proof passed; 1 proof failed; 3 blocked (runsc not usable).
Writes proof/gvisor/ (proof.json, PROOF.md, logs, both matrices).

Scope: this establishes host containment under gVisor while candidate code
executes during evaluation. It does not isolate the verifier from candidate
code inside the evaluation container, and it involves no Labyrinth
integration or Labyrinth sealing.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "proof" / "gvisor"
sys.path.insert(0, str(ROOT / "harness"))
import validate  # noqa: E402

# Full evidence SHA-256 of the standalone default-runtime (runc) baseline at 42f36a8.
STANDALONE_BASELINE_EVIDENCE_SHA256 = "bc1d2f0d8676d0538a7cdfaf46c6be36ebf93791a11ee2159b56469d22a9474f"


def run(cmd: list[str], log: Path | None = None, timeout: int = 1800) -> dict:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=ROOT)
    if log is not None:
        log.write_text(f"$ {' '.join(cmd)}\n{proc.stdout}{proc.stderr}exit={proc.returncode}\n")
    return {"command": " ".join(cmd), "exit_code": proc.returncode,
            "log": log.relative_to(ROOT).as_posix() if log else None,
            "stdout_tail": proc.stdout.strip().splitlines()[-3:]}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    runtimes = validate.docker_runtimes()
    if "runsc" not in runtimes["registered"]:
        print(f"BLOCKED: runsc is not registered with Docker (registered: {runtimes['registered']}).")
        print("Install gVisor, then confirm: docker run --rm --runtime=runsc hello-world")
        return 3
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    steps: dict[str, dict] = {}
    steps["canary"] = run(["docker", "run", "--rm", "--runtime=runsc", "hello-world"], OUT / "canary.log", 300)
    if steps["canary"]["exit_code"] != 0:
        print("BLOCKED: runsc is registered but the hello-world canary failed; see proof/gvisor/canary.log")
        (OUT / "proof.json").write_text(json.dumps({"status": "BLOCKED", "steps": steps}, indent=2) + "\n")
        return 3

    matrices = []
    for n in (1, 2):
        steps[f"matrix_run_{n}"] = run(["python3", "harness/validate.py", "--runtime", "runsc"],
                                       OUT / f"matrix-run-{n}.log")
        copy = OUT / f"matrix-run-{n}.json"
        shutil.copy2(ROOT / "results" / "matrix.json", copy)
        matrices.append(json.loads(copy.read_text()))

    both_validated = all(steps[f"matrix_run_{n}"]["exit_code"] == 0 for n in (1, 2)) and all(
        m["all_branches_match_expectation"] for m in matrices)
    identical = matrices[0]["evidence_sha256"] == matrices[1]["evidence_sha256"]
    runsc_both = all(m["container_runtime"]["requested"] == "runsc" for m in matrices)
    # The baseline is fixed; a mismatch fails the proof and is never re-baselined here.
    baseline_match = all(m["evidence_sha256"] == STANDALONE_BASELINE_EVIDENCE_SHA256 for m in matrices)
    passed = both_validated and identical and runsc_both and baseline_match

    proof = {
        "schema": "coding-agent-eval.gvisor-proof.v1",
        "status": "PASSED" if passed else "FAILED",
        "claim": "Candidate code executed under gVisor (runsc) during evaluation, "
                 "with the same verdicts and failed-check identities as the standalone baseline.",
        "not_claimed": [
            "Labyrinth integration or Labyrinth sealing",
            "isolation of the verifier from candidate code inside the evaluation container",
        ],
        "image": validate.IMAGE,
        "container_runtime": matrices[0]["container_runtime"],
        "fixture_sha256": matrices[0]["fixture_sha256"],
        "verifier_sha256": matrices[0]["verifier_sha256"],
        "evidence_sha256": {f"run_{n}": m["evidence_sha256"] for n, m in zip((1, 2), matrices)},
        "evidence_identical_across_runs": identical,
        "standalone_runc_baseline_evidence_sha256": STANDALONE_BASELINE_EVIDENCE_SHA256,
        "evidence_matches_standalone_runc_baseline": baseline_match,
        "matrix_sha256": {f"run_{n}": sha256_file(OUT / f"matrix-run-{n}.json") for n in (1, 2)},
        "verdicts": [{k: r[k] for k in ("branch", "expected_verdict", "observed_verdict",
                                         "observed_failed_checks", "matches_expectation")}
                     for r in matrices[0]["rows"]],
        "steps": steps,
    }
    (OUT / "proof.json").write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n")

    lines = [
        "# gVisor evaluation proof — coding-agent-eval-idempotent-retry", "",
        f"**Status: {proof['status']}**", "",
        f"Claim: {proof['claim']}", "",
        "Not claimed: " + "; ".join(proof["not_claimed"]) + ".", "",
        f"- Image: `{proof['image']}`",
        f"- Container runtime: `{proof['container_runtime']['requested']}` "
        f"({proof['container_runtime'].get('runsc_version')})",
        f"- Evidence SHA-256, run 1: `{proof['evidence_sha256']['run_1']}`",
        f"- Evidence SHA-256, run 2: `{proof['evidence_sha256']['run_2']}`",
        f"- Identical across runs: {identical}; identical to the standalone runc baseline: "
        f"{proof['evidence_matches_standalone_runc_baseline']}", "",
        "| Step | Command | Exit |", "|---|---|---|",
    ]
    lines += [f"| {name} | `{s['command']}` | {s['exit_code']} |" for name, s in steps.items()]
    lines += ["", "| Branch | Expected | Observed | Failed checks | Matches |", "|---|---|---|---|---|"]
    lines += [f"| `{v['branch']}` | {v['expected_verdict']} | {v['observed_verdict']} | "
              f"{', '.join(v['observed_failed_checks']) or '—'} | {'yes' if v['matches_expectation'] else 'NO'} |"
              for v in proof["verdicts"]]
    (OUT / "PROOF.md").write_text("\n".join(lines) + "\n")
    print(f"{proof['status']}: see proof/gvisor/PROOF.md")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
