"""Checks that the tamper-demo script still tells the truth.

The demo script is a claim made to judges. If the engine changes and the script
starts reporting PASS for something it should catch, that is worse than a broken
test -- it is a false claim in front of a judge. This runs the real script and
asserts on its real output.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "demo_tamper.py"


def test_tamper_demo_script_runs_clean():
    result = subprocess.run(
        [sys.executable, str(SCRIPT)], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, f"demo script failed:\n{result.stdout}\n{result.stderr}"
    assert "All tamper-detection checks behaved as documented" in result.stdout
    assert "UNEXPECTED" not in result.stdout


def test_tamper_demo_detects_every_advertised_class():
    result = subprocess.run(
        [sys.executable, str(SCRIPT)], capture_output=True, text=True, timeout=60
    )
    out = result.stdout
    # Each tamper class must actually be reported as invalid.
    assert out.count("chain INVALID") == 5, "expected five detected tamper classes"
    for reason in ("modified_record", "sequence_error", "broken_link"):
        assert reason in out, f"{reason} was not exercised"


def test_tamper_demo_states_the_limitation():
    """The demo must not oversell the ledger."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT)], capture_output=True, text=True, timeout=60
    )
    out = result.stdout
    assert "valid=True" in out, "the full-rewrite limitation must be demonstrated"
    assert "never 'immutable'" in out
