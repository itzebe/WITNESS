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


def test_tamper_demo_docstring_count_matches_the_cases():
    """The module docstring must state the same count as the cases below.

    It said "four distinct ways" while the script tampers five, and the summary
    line said "Six" while the script tampers five. Both are claims a judge can
    check in seconds, so both are pinned here.
    """
    source = SCRIPT.read_text()
    docstring = " ".join(source.split('"""')[1].split())
    assert "five distinct ways" in docstring, (
        "the docstring count no longer matches the five tamper cases"
    )


def test_tamper_demo_summary_count_matches_the_cases_shown():
    """The summary word must equal the number of classes actually detected.

    A hardcoded count drifts: the script once printed "Six tamper classes
    detected" over five cases. The count is now derived from the detected list,
    so this checks the printed word against the cases reported in the same run.
    """
    result = subprocess.run(
        [sys.executable, str(SCRIPT)], capture_output=True, text=True, timeout=60
    )
    out = result.stdout
    detected = out.count("chain INVALID")
    words = {1: "One", 2: "Two", 3: "Three", 4: "Four", 5: "Five", 6: "Six"}
    expected = f"{words[detected]} tamper classes detected"
    assert expected in out, (
        f"summary does not match the {detected} detected classes:\n{out}"
    )


def test_tamper_demo_states_the_limitation():
    """The demo must not oversell the ledger."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT)], capture_output=True, text=True, timeout=60
    )
    out = result.stdout
    assert "valid=True" in out, "the full-rewrite limitation must be demonstrated"
    assert "never 'immutable'" in out
