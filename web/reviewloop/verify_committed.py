"""Run the reviewloop suite from a Git archive and write re-runnable evidence."""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path


def run(command: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None, binary: bool = False):
    result = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=not binary,
        encoding=None if binary else "utf-8",
        errors=None if binary else "replace",
        env={**os.environ, **(env or {})},
        check=False,
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sha", default="HEAD")
    parser.add_argument(
        "--output",
        default="web/reviewloop/evidence/VERIFY-COMMITTED.json",
    )
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[2]
    resolved = run(["git", "-C", str(repository), "rev-parse", f"{args.sha}^{{commit}}"])
    if resolved.returncode != 0:
        print(resolved.stderr, file=sys.stderr)
        return 2
    code_sha = resolved.stdout.strip()
    archive = run(["git", "-C", str(repository), "archive", "--format=tar", code_sha], binary=True)
    if archive.returncode != 0:
        print(archive.stderr.decode("utf-8", errors="replace"), file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="reviewloop-archive-") as raw:
        export = Path(raw)
        with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode="r:") as bundle:
            for member in bundle.getmembers():
                target = (export / member.name).resolve()
                if export not in target.parents and target != export:
                    print(f"archive member escaped export: {member.name}", file=sys.stderr)
                    return 2
            bundle.extractall(export, filter="data")
        command = [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            str(export / "web" / "reviewloop" / "tests"),
            "-t",
            str(export),
            "-v",
        ]
        tests = run(
            command,
            cwd=export,
            env={"PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(export)},
        )
        combined = tests.stdout + tests.stderr
        match = re.search(r"Ran (\d+) tests?", combined)
        denominator = int(match.group(1)) if match else 0
        passed = denominator if tests.returncode == 0 else sum(1 for line in combined.splitlines() if line.endswith(" ... ok"))
        requested_output = Path(args.output)
        destination = requested_output if requested_output.is_absolute() else repository / requested_output
        report = {
            "measurement": {
                "code_sha": code_sha,
                "store": "Git archive plus isolated temporary Git fixture repositories",
                "invocation": (
                    f"PYTHONDONTWRITEBYTECODE=1 python {repository.as_posix()}"
                    "/web/reviewloop/verify_committed.py "
                    f"--sha {code_sha} --output {destination.as_posix()}"
                ),
                "denominator": denominator,
                "predicate": "every declared hermetic scene exits ok when run from the named Git archive",
            },
            "result": {
                "passed": passed,
                "failed": max(denominator - passed, 0),
                "exit_code": tests.returncode,
            },
            "scenes": [
                "structured operational, sensitive, and duplicate-member payloads refuse while authored controls remain admissible",
                "draft proposal is decidable and accepted change stops on a local branch",
                "existing answer suppresses a duplicate proposal and fires the brake",
                "existing migration refuses instead of resolving",
                "false-positive rate uses decided proposals as its denominator",
                "governed proposal needs a named human and concurrent edit becomes an exception row",
            ],
            "output": combined,
        }
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="")

    if denominator == 0:  # DENOMINATOR
        print("0 committed test scenes ran. A verdict over an empty set is not a pass.")
        return 2
    print(f"DENOMINATOR committed test scenes: {denominator}")
    print(f"RESULT passed {passed}, failed {max(denominator - passed, 0)}")
    print(f"CODE SHA: {code_sha}")
    print(f"EVIDENCE: {destination}")
    return tests.returncode


if __name__ == "__main__":
    raise SystemExit(main())
