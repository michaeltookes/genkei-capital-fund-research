"""Tests for the CI retry-with-backoff wrapper (B-125).

scripts/ci/retry.sh wraps the external-API collect step in each ingest
workflow so a transient flake is retried instead of dropping a day of data.
These exercise the load-bearing behavior offline (the YAML wiring validates in
CI): bounded attempts, correct exit-code propagation, and — critically — that a
caller capturing stdout still recovers the *successful* attempt's
``ingest_run_id=`` line.

base_delay is passed as 0 throughout so the backoff ``sleep`` is instant.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

RETRY = Path(__file__).resolve().parents[2] / "scripts" / "ci" / "retry.sh"

# A command that increments a counter file each call and succeeds only once the
# counter reaches ``threshold``. Used to simulate "flaky then recovers".
_FLAKY = 'n=$(cat "$1"); n=$((n+1)); echo "$n" > "$1"; [ "$n" -ge "$2" ]'
# Same, but also prints an ingest_run_id line only on the successful attempt.
_FLAKY_WITH_RUNID = (
    'n=$(cat "$1"); n=$((n+1)); echo "$n" > "$1"; '
    'if [ "$n" -ge "$2" ]; then echo "wrote ingest_run_id=$n"; exit 0; fi; '
    'echo "transient failure" >&2; exit 1'
)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(RETRY), *args],
        capture_output=True,
        text=True,
        timeout=30,
    )


class RetryScriptTests(unittest.TestCase):
    def _counter(self) -> Path:
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory)
        path = Path(directory) / "counter.cnt"
        path.write_text("0")
        return path

    def test_succeeds_first_attempt(self) -> None:
        counter = self._counter()
        proc = _run("3", "0", "--", "bash", "-c", _FLAKY, "_", str(counter), "1")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(counter.read_text().strip(), "1")  # ran exactly once

    def test_recovers_after_flakes(self) -> None:
        counter = self._counter()
        # Succeeds on the 3rd attempt.
        proc = _run("3", "0", "--", "bash", "-c", _FLAKY, "_", str(counter), "3")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(counter.read_text().strip(), "3")  # took all three attempts

    def test_exhausts_attempts_and_propagates_failure(self) -> None:
        counter = self._counter()
        # Needs 5 successes-worth but only 3 attempts allowed → fails.
        proc = _run("3", "0", "--", "bash", "-c", _FLAKY, "_", str(counter), "5")
        self.assertEqual(proc.returncode, 1)  # the command's real exit code
        self.assertEqual(counter.read_text().strip(), "3")  # stopped at max_attempts

    def test_runid_capture_picks_successful_attempt(self) -> None:
        counter = self._counter()
        proc = _run(
            "3", "0", "--", "bash", "-c", _FLAKY_WITH_RUNID, "_", str(counter), "3"
        )
        self.assertEqual(proc.returncode, 0)
        # Mirror the workflow's parse: grep the run-id line, take the last.
        run_id_lines = [
            line for line in proc.stdout.splitlines() if "ingest_run_id=" in line
        ]
        self.assertEqual(len(run_id_lines), 1)
        self.assertTrue(run_id_lines[-1].endswith("ingest_run_id=3"))
        # Retry diagnostics must stay on stderr, never polluting captured stdout.
        self.assertNotIn("retry.sh:", proc.stdout)

    def test_usage_error_without_separator(self) -> None:
        proc = _run("3", "0", "python3", "--version")
        self.assertEqual(proc.returncode, 2)

    def test_rejects_non_integer_attempts(self) -> None:
        proc = _run("abc", "0", "--", "true")
        self.assertEqual(proc.returncode, 2)


if __name__ == "__main__":
    unittest.main()
