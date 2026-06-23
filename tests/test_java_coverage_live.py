"""Live integration test for measure_branch_coverage_java.

Marked @pytest.mark.slow so it is excluded from the normal fast suite.
The test copies the proven spike fixture (/tmp/jacoco-spike) into a
fresh tmp_path directory, runs Gradle with the JaCoCo init-script, and
asserts that partial branch coverage is correctly detected.

Run with:
  pytest tests/test_java_coverage_live.py -q -m slow
"""

import os
import shutil

import pytest

pytestmark = pytest.mark.slow


def test_live_java_branch_measure(tmp_path):
    if not shutil.which("java"):
        pytest.skip("no java")
    src = "/tmp/jacoco-spike"
    if not os.path.isdir(src):
        pytest.skip("spike fixture absent")

    proj = tmp_path / "p"
    shutil.copytree(src, proj)

    from servers import coverage_java as cj

    targets = [
        {
            "file_path": "src/main/java/demo/Classify.java",
            "name": "of",
            "line_start": 4,
            "line_end": 12,
        }
    ]
    res = cj.measure_branch_coverage_java(
        str(proj), ["ClassifyTest"], targets, test_filters=["demo.ClassifyTest"]
    )

    assert res["tool_status"] == "ok", f"unexpected status: {res['tool_status']!r} err={res.get('error')!r}"
    assert res["fully_covered"] is False
    assert len(res["per_target"]) == 1
    pt = res["per_target"][0]
    assert pt["n_covered"] >= 1, f"expected at least 1 covered branch, got {pt['n_covered']}"
