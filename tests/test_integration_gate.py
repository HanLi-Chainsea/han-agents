"""Pure unit tests for servers/integration_gate.py — parse_junit_results.

TDD: these tests are written BEFORE the implementation.
They must fail until integration_gate.py exists and is correct.
"""

import os
import tempfile
import textwrap

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_xml(tmp_path, name, content):
    p = tmp_path / name
    p.write_text(textwrap.dedent(content))
    return str(p)


# ---------------------------------------------------------------------------
# parse_junit_results — fixture-driven unit tests
# ---------------------------------------------------------------------------

class TestParseJunitResults:
    """All tests call parse_junit_results(xml_paths) and inspect the dict."""

    def test_single_suite_with_failure(self, tmp_path):
        """testsuite tests=2 failures=1 → passed=False, total=2, failures=1."""
        p = _write_xml(tmp_path, "r.xml", """\
            <?xml version="1.0"?>
            <testsuite name="SomeTests" tests="2" failures="1" errors="0" skipped="0">
              <testcase classname="foo.Bar" name="test_a"/>
              <testcase classname="foo.Bar" name="test_b">
                <failure message="AssertionError">expected 1 got 2</failure>
              </testcase>
            </testsuite>
        """)

        from servers.integration_gate import parse_junit_results
        res = parse_junit_results([p])

        assert res["ran"] is True
        assert res["total"] == 2
        assert res["failures"] == 1
        assert res["errors"] == 0
        assert res["passed"] is False
        assert res["error"] is None

    def test_single_suite_all_pass(self, tmp_path):
        """testsuite tests=3 failures=0 errors=0 → passed=True."""
        p = _write_xml(tmp_path, "r.xml", """\
            <?xml version="1.0"?>
            <testsuite name="SomeTests" tests="3" failures="0" errors="0" skipped="0">
              <testcase classname="foo.Bar" name="test_a"/>
              <testcase classname="foo.Bar" name="test_b"/>
              <testcase classname="foo.Bar" name="test_c"/>
            </testsuite>
        """)

        from servers.integration_gate import parse_junit_results
        res = parse_junit_results([p])

        assert res["ran"] is True
        assert res["total"] == 3
        assert res["failures"] == 0
        assert res["errors"] == 0
        assert res["passed"] is True
        assert res["error"] is None

    def test_testsuites_wrapper_sums_two_suites(self, tmp_path):
        """<testsuites> containing two <testsuite> elements — counts are summed."""
        p = _write_xml(tmp_path, "r.xml", """\
            <?xml version="1.0"?>
            <testsuites>
              <testsuite name="Suite1" tests="2" failures="0" errors="0" skipped="0"/>
              <testsuite name="Suite2" tests="4" failures="1" errors="0" skipped="0"/>
            </testsuites>
        """)

        from servers.integration_gate import parse_junit_results
        res = parse_junit_results([p])

        assert res["ran"] is True
        assert res["total"] == 6       # 2 + 4
        assert res["failures"] == 1    # 0 + 1
        assert res["errors"] == 0
        assert res["passed"] is False  # has failure
        assert res["error"] is None

    def test_empty_file_returns_ran_false(self, tmp_path):
        """Empty/garbage file → ran=False, passed=False, error set."""
        p = _write_xml(tmp_path, "bad.xml", "this is not xml at all")

        from servers.integration_gate import parse_junit_results
        res = parse_junit_results([p])

        assert res["ran"] is False
        assert res["passed"] is False
        assert res["error"] is not None

    def test_no_xml_paths_returns_ran_false(self):
        """Empty list → ran=False, passed=False."""
        from servers.integration_gate import parse_junit_results
        res = parse_junit_results([])

        assert res["ran"] is False
        assert res["passed"] is False
        assert res["error"] is not None

    def test_multi_file_aggregation(self, tmp_path):
        """Two separate XML files are summed correctly."""
        p1 = _write_xml(tmp_path, "a.xml", """\
            <?xml version="1.0"?>
            <testsuite name="A" tests="2" failures="0" errors="0" skipped="0"/>
        """)
        p2 = _write_xml(tmp_path, "b.xml", """\
            <?xml version="1.0"?>
            <testsuite name="B" tests="3" failures="0" errors="0" skipped="0"/>
        """)

        from servers.integration_gate import parse_junit_results
        res = parse_junit_results([p1, p2])

        assert res["ran"] is True
        assert res["total"] == 5
        assert res["failures"] == 0
        assert res["errors"] == 0
        assert res["passed"] is True

    def test_errors_count_prevents_passed(self, tmp_path):
        """errors=1 (even with failures=0) → passed=False."""
        p = _write_xml(tmp_path, "r.xml", """\
            <?xml version="1.0"?>
            <testsuite name="T" tests="2" failures="0" errors="1" skipped="0"/>
        """)

        from servers.integration_gate import parse_junit_results
        res = parse_junit_results([p])

        assert res["ran"] is True
        assert res["errors"] == 1
        assert res["passed"] is False

    def test_zero_tests_prevents_passed(self, tmp_path):
        """tests=0 even with 0 failures → passed=False (no evidence tests ran)."""
        p = _write_xml(tmp_path, "r.xml", """\
            <?xml version="1.0"?>
            <testsuite name="T" tests="0" failures="0" errors="0" skipped="0"/>
        """)

        from servers.integration_gate import parse_junit_results
        res = parse_junit_results([p])

        assert res["ran"] is True   # we parsed the file, but…
        assert res["passed"] is False  # total==0 prevents passed
