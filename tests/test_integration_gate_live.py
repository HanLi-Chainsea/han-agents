"""Live integration test for run_tests (Java / Gradle).

Skipped unless ALL three conditions hold:
  - HAN_RUN_JAVA_LIVE is set in the environment
  - 'java' binary is on PATH
  - /tmp/jacoco-spike fixture directory exists

Run with:
  HAN_RUN_JAVA_LIVE=1 pytest tests/test_integration_gate_live.py -q
"""

import os
import shutil


def test_live_java_run_tests(tmp_path):
    if not os.environ.get("HAN_RUN_JAVA_LIVE"):
        import pytest
        pytest.skip("set HAN_RUN_JAVA_LIVE=1 to run the Gradle live test")
    if not shutil.which("java"):
        import pytest
        pytest.skip("no java binary on PATH")
    src = "/tmp/jacoco-spike"
    if not os.path.isdir(src):
        import pytest
        pytest.skip("spike fixture absent: /tmp/jacoco-spike")

    proj = tmp_path / "p"
    shutil.copytree(src, proj)

    from servers.integration_gate import run_tests

    res = run_tests(
        str(proj),
        "java",
        test_filters=["demo.ClassifyTest"],
    )

    assert res["passed"] is True, (
        f"expected passed=True, got {res!r}"
    )
    assert res["ran"] is True
    assert res["total"] > 0
    assert res["failures"] == 0
    assert res["errors"] == 0
    assert "evidence" in res
