"""Regression of the controlled comparison, including the published CLI."""
import subprocess
import sys
from pathlib import Path

import pytest

from experiments.compare_gateway import ATTACKS, run_case


@pytest.mark.parametrize("attack", ATTACKS, ids=lambda attack: attack.name)
@pytest.mark.parametrize("protected", [False, True], ids=["baseline", "gateway"])
def test_fixed_comparison_case(protected, attack):
    # run_case also checks exact reasons, no disclosure, unchanged files, and
    # a successful allowed read before accepting a protected result.
    outcome = run_case(protected, attack)
    assert outcome.attack_success is (not protected)
    assert outcome.reason_code == (attack.expected_code if protected else None)


def test_comparison_cli():
    completed = subprocess.run(
        [sys.executable, "-m", "experiments.compare_gateway"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, check=True, timeout=30,
    )
    assert completed.stderr == ""
    assert completed.stdout.count("ATTACK SUCCESS") == 6
    assert completed.stdout.count("BLOCKED") == 6
    assert "Attack success rate: 6/6 (100%)" in completed.stdout
    assert "Attack success rate: 0/6 (0%)" in completed.stdout
