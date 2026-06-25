"""Pure unit tests for servers/coverage_js.py — no npm/npx invocations.

All tests use inline istanbul-format coverage-final.json fixtures written
to tmp_path.  The default pytest run must NEVER invoke npm/npx.
"""
import json
import os

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_istanbul_json(path, file_abs_path, branch_map, b_data, **extra):
    """Write a minimal istanbul coverage-final.json to path."""
    data = {
        file_abs_path: {
            'path': file_abs_path,
            'all': False,
            'statementMap': {},
            's': {},
            'branchMap': branch_map,
            'b': b_data,
            **extra,
        }
    }
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(data, fh)


def _make_target(file_path, name='fn', line_start=1, line_end=10):
    return {'file_path': file_path, 'name': name,
            'line_start': line_start, 'line_end': line_end}


# ---------------------------------------------------------------------------
# parse_js_coverage — happy path
# ---------------------------------------------------------------------------

class TestParseJsCoverageBasic:
    """One file, a function spanning lines, branchMap with 2 ids in range,
    b showing one arm covered one not → assert n_total/n_covered correct,
    fully_covered False, missing branch anchored at the right line.
    """

    def test_partial_coverage_n_total_n_covered(self, tmp_path):
        from servers.coverage_js import parse_js_coverage

        cov_json = tmp_path / 'coverage-final.json'
        src_abs = str(tmp_path / 'src' / 'classify.js')

        # branchMap: id 0 on line 2 (in range 1-5), id 1 on line 3 (in range 1-5)
        branch_map = {
            '0': {
                'type': 'if',
                'line': 2,
                'loc': {'start': {'line': 2, 'column': 2}, 'end': {'line': 4, 'column': 3}},
                'locations': [],
            },
            '1': {
                'type': 'if',
                'line': 3,
                'loc': {'start': {'line': 3, 'column': 2}, 'end': {'line': 5, 'column': 3}},
                'locations': [],
            },
        }
        # b: id 0 → arm 0 hit (1), arm 1 not hit (0); id 1 → arm 0 not hit (0)
        b_data = {'0': [1, 0], '1': [0]}

        _write_istanbul_json(str(cov_json), src_abs, branch_map, b_data)

        targets = [_make_target('src/classify.js', 'classify', 1, 5)]
        res = parse_js_coverage(str(cov_json), targets, str(tmp_path))

        assert res['tool_status'] == 'ok'
        assert res['fully_covered'] is False

        pt = res['per_target'][0]
        # 3 arms total: b[0]=[1,0] → 2 arms, b[1]=[0] → 1 arm
        assert pt['n_total'] == 3
        assert pt['n_covered'] == 1
        assert len(pt['missing_branches']) == 2
        assert len(pt['covered_branches']) == 1

    def test_missing_branch_anchored_at_correct_line(self, tmp_path):
        from servers.coverage_js import parse_js_coverage

        cov_json = tmp_path / 'coverage-final.json'
        src_abs = str(tmp_path / 'src' / 'classify.js')

        branch_map = {
            '0': {
                'type': 'if',
                'line': 2,
                'loc': {'start': {'line': 2, 'column': 2}, 'end': {'line': 3, 'column': 3}},
                'locations': [],
            },
        }
        # Both arms: arm 0 covered, arm 1 not covered
        b_data = {'0': [1, 0]}

        _write_istanbul_json(str(cov_json), src_abs, branch_map, b_data)

        targets = [_make_target('src/classify.js', 'classify', 1, 5)]
        res = parse_js_coverage(str(cov_json), targets, str(tmp_path))

        assert res['tool_status'] == 'ok'
        pt = res['per_target'][0]
        # Missing arm should be at line 2, arm index 1
        missing = pt['missing_branches']
        assert len(missing) == 1
        assert missing[0]['from'] == 2
        assert missing[0]['to'] == 1

    def test_fully_covered(self, tmp_path):
        from servers.coverage_js import parse_js_coverage

        cov_json = tmp_path / 'coverage-final.json'
        src_abs = str(tmp_path / 'src' / 'classify.js')

        branch_map = {
            '0': {
                'type': 'if',
                'line': 2,
                'loc': {'start': {'line': 2, 'column': 2}, 'end': {'line': 3, 'column': 3}},
                'locations': [],
            },
        }
        b_data = {'0': [3, 2]}  # both arms hit

        _write_istanbul_json(str(cov_json), src_abs, branch_map, b_data)

        targets = [_make_target('src/classify.js', 'classify', 1, 5)]
        res = parse_js_coverage(str(cov_json), targets, str(tmp_path))

        assert res['tool_status'] == 'ok'
        assert res['fully_covered'] is True
        pt = res['per_target'][0]
        assert pt['n_total'] == 2
        assert pt['n_covered'] == 2
        assert pt['missing_branches'] == []

    def test_branches_outside_range_not_counted(self, tmp_path):
        """Branches outside target line range must NOT contribute to n_total."""
        from servers.coverage_js import parse_js_coverage

        cov_json = tmp_path / 'coverage-final.json'
        src_abs = str(tmp_path / 'src' / 'classify.js')

        branch_map = {
            '0': {
                'type': 'if',
                'line': 2,
                'loc': {'start': {'line': 2, 'column': 0}, 'end': {'line': 3, 'column': 0}},
                'locations': [],
            },
            '1': {
                'type': 'if',
                'line': 20,   # outside range 1-5
                'loc': {'start': {'line': 20, 'column': 0}, 'end': {'line': 21, 'column': 0}},
                'locations': [],
            },
        }
        b_data = {'0': [1, 0], '1': [5, 5]}

        _write_istanbul_json(str(cov_json), src_abs, branch_map, b_data)

        targets = [_make_target('src/classify.js', 'classify', 1, 5)]
        res = parse_js_coverage(str(cov_json), targets, str(tmp_path))

        assert res['tool_status'] == 'ok'
        pt = res['per_target'][0]
        # Only id 0 (line 2) is in range; id 1 (line 20) excluded
        assert pt['n_total'] == 2
        assert pt['n_covered'] == 1

    def test_loc_start_line_preferred_over_line_field(self, tmp_path):
        """loc.start.line must be preferred over top-level line field."""
        from servers.coverage_js import parse_js_coverage

        cov_json = tmp_path / 'coverage-final.json'
        src_abs = str(tmp_path / 'src' / 'f.js')

        branch_map = {
            '0': {
                'type': 'if',
                'line': 50,   # top-level line → outside range 1-10
                'loc': {'start': {'line': 3, 'column': 0}, 'end': {'line': 4, 'column': 0}},
                'locations': [],
            },
        }
        b_data = {'0': [1, 0]}

        _write_istanbul_json(str(cov_json), src_abs, branch_map, b_data)

        targets = [_make_target('src/f.js', 'f', 1, 10)]
        res = parse_js_coverage(str(cov_json), targets, str(tmp_path))

        assert res['tool_status'] == 'ok'
        pt = res['per_target'][0]
        # loc.start.line=3 is in range, so branch IS counted
        assert pt['n_total'] == 2
        # The from-line in the branch entry should be 3 (from loc.start.line)
        all_branches = pt['covered_branches'] + pt['missing_branches']
        assert all(b['from'] == 3 for b in all_branches)


# ---------------------------------------------------------------------------
# parse_js_coverage — fail-closed cases
# ---------------------------------------------------------------------------

class TestParseJsCoverageFailClosed:
    def test_invalid_line_range_is_invalid_targets(self, tmp_path):
        from servers.coverage_js import parse_js_coverage

        cov_json = tmp_path / 'coverage-final.json'
        # Write minimal valid json (doesn't matter, target validation runs first)
        _write_istanbul_json(str(cov_json), '/abs/src/f.js', {}, {})

        targets = [{'file_path': 'src/f.js', 'name': 'f',
                    'line_start': 10, 'line_end': 5}]  # line_end < line_start
        res = parse_js_coverage(str(cov_json), targets, str(tmp_path))

        assert res['tool_status'] == 'invalid_targets'
        assert res['fully_covered'] is False

    def test_line_end_none_is_invalid_targets(self, tmp_path):
        from servers.coverage_js import parse_js_coverage

        cov_json = tmp_path / 'coverage-final.json'
        _write_istanbul_json(str(cov_json), '/abs/src/f.js', {}, {})

        targets = [{'file_path': 'src/f.js', 'name': 'f',
                    'line_start': 1, 'line_end': None}]
        res = parse_js_coverage(str(cov_json), targets, str(tmp_path))

        assert res['tool_status'] == 'invalid_targets'
        assert res['fully_covered'] is False

    def test_bool_line_start_is_invalid_targets(self, tmp_path):
        from servers.coverage_js import parse_js_coverage

        cov_json = tmp_path / 'coverage-final.json'
        _write_istanbul_json(str(cov_json), '/abs/src/f.js', {}, {})

        targets = [{'file_path': 'src/f.js', 'name': 'f',
                    'line_start': True, 'line_end': 5}]
        res = parse_js_coverage(str(cov_json), targets, str(tmp_path))

        assert res['tool_status'] == 'invalid_targets'
        assert res['fully_covered'] is False

    def test_target_file_absent_is_no_targets(self, tmp_path):
        """File not present in coverage JSON → 'no_targets', never fully_covered."""
        from servers.coverage_js import parse_js_coverage

        cov_json = tmp_path / 'coverage-final.json'
        other_abs = str(tmp_path / 'src' / 'other.js')
        _write_istanbul_json(str(cov_json), other_abs, {}, {})

        targets = [_make_target('src/classify.js', 'classify', 1, 5)]
        res = parse_js_coverage(str(cov_json), targets, str(tmp_path))

        assert res['tool_status'] == 'no_targets'
        assert res['fully_covered'] is False

    def test_file_present_no_branch_data_is_schema_error(self, tmp_path):
        """File present but branchMap has no entries in range → schema_error."""
        from servers.coverage_js import parse_js_coverage

        cov_json = tmp_path / 'coverage-final.json'
        src_abs = str(tmp_path / 'src' / 'classify.js')

        # branchMap exists but all branches are outside target range
        branch_map = {
            '0': {
                'type': 'if',
                'line': 50,
                'loc': {'start': {'line': 50, 'column': 0}, 'end': {'line': 51, 'column': 0}},
                'locations': [],
            },
        }
        b_data = {'0': [1, 0]}

        _write_istanbul_json(str(cov_json), src_abs, branch_map, b_data)

        # Target range 1-5 has no branches in it
        targets = [_make_target('src/classify.js', 'classify', 1, 5)]
        res = parse_js_coverage(str(cov_json), targets, str(tmp_path))

        # schema_error because file is present but no branch data for target range
        assert res['tool_status'] == 'schema_error'
        assert res['fully_covered'] is False

    def test_missing_branchmap_is_schema_error(self, tmp_path):
        """File present but branchMap key is absent → schema_error (not no_targets)."""
        from servers.coverage_js import parse_js_coverage

        cov_json = tmp_path / 'coverage-final.json'
        src_abs = str(tmp_path / 'src' / 'f.js')

        # Write entry without branchMap/b keys
        data = {src_abs: {'path': src_abs, 'all': False, 'statementMap': {}, 's': {}}}
        with open(str(cov_json), 'w') as fh:
            json.dump(data, fh)

        targets = [_make_target('src/f.js', 'f', 1, 5)]
        res = parse_js_coverage(str(cov_json), targets, str(tmp_path))

        assert res['tool_status'] == 'schema_error'
        assert res['fully_covered'] is False

    def test_b_entry_missing_for_branch_id_is_schema_error(self, tmp_path):
        """branchMap has id '0' but b dict is missing that key → schema_error."""
        from servers.coverage_js import parse_js_coverage

        cov_json = tmp_path / 'coverage-final.json'
        src_abs = str(tmp_path / 'src' / 'f.js')

        branch_map = {
            '0': {'type': 'if', 'line': 2,
                  'loc': {'start': {'line': 2, 'column': 0}, 'end': {'line': 3, 'column': 0}},
                  'locations': []},
        }
        b_data = {}  # missing '0'

        _write_istanbul_json(str(cov_json), src_abs, branch_map, b_data)

        targets = [_make_target('src/f.js', 'f', 1, 5)]
        res = parse_js_coverage(str(cov_json), targets, str(tmp_path))

        assert res['tool_status'] == 'schema_error'
        assert res['fully_covered'] is False

    def test_empty_targets_is_no_targets(self, tmp_path):
        from servers.coverage_js import parse_js_coverage

        cov_json = tmp_path / 'coverage-final.json'
        _write_istanbul_json(str(cov_json), '/abs/f.js', {}, {})

        res = parse_js_coverage(str(cov_json), [], str(tmp_path))
        assert res['tool_status'] == 'no_targets'


# ---------------------------------------------------------------------------
# parse_js_coverage — file matching (suffix / realpath)
# ---------------------------------------------------------------------------

class TestParseJsCoverageFileMatching:
    def test_suffix_match_finds_file(self, tmp_path):
        """Relative target path matches absolute JSON key by suffix."""
        from servers.coverage_js import parse_js_coverage

        cov_json = tmp_path / 'coverage-final.json'
        # Absolute key: /home/user/proj/src/classify.js
        src_abs = str(tmp_path / 'src' / 'classify.js')

        branch_map = {
            '0': {'type': 'if', 'line': 2,
                  'loc': {'start': {'line': 2, 'column': 0}, 'end': {'line': 3, 'column': 0}},
                  'locations': []},
        }
        b_data = {'0': [1, 0]}

        _write_istanbul_json(str(cov_json), src_abs, branch_map, b_data)

        # Target uses relative path
        targets = [_make_target('src/classify.js', 'classify', 1, 5)]
        res = parse_js_coverage(str(cov_json), targets, str(tmp_path))

        assert res['tool_status'] == 'ok'


# ---------------------------------------------------------------------------
# select_backend — JS routing
# ---------------------------------------------------------------------------

class TestSelectBackendJs:
    def test_vitest_maps_to_js(self):
        from servers.coverage_java import select_backend
        assert select_backend({'test_tool': 'vitest'}) == 'js'

    def test_jest_maps_to_js(self):
        from servers.coverage_java import select_backend
        assert select_backend({'test_tool': 'jest'}) == 'js'

    def test_mocha_maps_to_js(self):
        from servers.coverage_java import select_backend
        assert select_backend({'test_tool': 'mocha'}) == 'js'

    def test_vitest_mixed_case_maps_to_js(self):
        from servers.coverage_java import select_backend
        assert select_backend({'test_tool': 'Vitest'}) == 'js'

    def test_javascript_language_maps_to_js(self):
        from servers.coverage_java import select_backend
        assert select_backend({'primary_language': 'javascript'}) == 'js'

    def test_typescript_language_maps_to_js(self):
        from servers.coverage_java import select_backend
        assert select_backend({'primary_language': 'typescript'}) == 'js'

    def test_js_tool_takes_priority_over_language(self):
        # vitest test_tool with python language → js (test_tool wins)
        from servers.coverage_java import select_backend
        assert select_backend({'test_tool': 'vitest', 'primary_language': 'python'}) == 'js'

    def test_existing_java_mapping_unchanged(self):
        from servers.coverage_java import select_backend
        assert select_backend({'test_tool': 'gradle'}) == 'java'
        assert select_backend({'test_tool': 'maven'}) == 'java'
        assert select_backend({'test_tool': 'junit'}) == 'java'

    def test_existing_python_mapping_unchanged(self):
        from servers.coverage_java import select_backend
        assert select_backend({'test_tool': 'pytest'}) == 'python'
        assert select_backend({'test_tool': 'unittest'}) == 'python'

    def test_unknown_still_unknown(self):
        from servers.coverage_java import select_backend
        assert select_backend({'test_tool': 'cargo'}) == 'unknown'
        assert select_backend({}) == 'unknown'


# ---------------------------------------------------------------------------
# measure_branch_coverage_js — unavailable path (no subprocess)
# ---------------------------------------------------------------------------

class TestMeasureBranchCoverageJsUnavailable:
    def test_npx_not_found_returns_unavailable(self, tmp_path, monkeypatch):
        import servers.coverage_js as cov_js
        monkeypatch.setattr(cov_js, '_js_available', lambda: False)

        targets = [_make_target('src/f.js', 'f', 1, 5)]
        res = cov_js.measure_branch_coverage_js(
            str(tmp_path), ['test/f.test.js'], targets)

        assert res['tool_status'] == 'unavailable'
        assert res['fully_covered'] is False

    def test_invalid_targets_checked_before_subprocess(self, tmp_path, monkeypatch):
        """_invalid_targets must fire before any subprocess is started."""
        import servers.coverage_js as cov_js
        # Mark npx as available, but subprocess.run should never be called
        monkeypatch.setattr(cov_js, '_js_available', lambda: True)

        subprocess_called = []

        def boom(*a, **k):
            subprocess_called.append(1)
            raise AssertionError('subprocess.run must not be called for invalid targets')

        monkeypatch.setattr(cov_js.subprocess, 'run', boom)

        targets = [{'file_path': 'src/f.js', 'name': 'f',
                    'line_start': None, 'line_end': 5}]
        res = cov_js.measure_branch_coverage_js(
            str(tmp_path), ['test/f.test.js'], targets)

        assert res['tool_status'] == 'invalid_targets'
        assert subprocess_called == []


# ---------------------------------------------------------------------------
# measure_branch_coverage_js — subprocess error paths (monkeypatched)
# ---------------------------------------------------------------------------

class TestMeasureBranchCoverageJsSubprocess:
    def _valid_targets(self):
        return [_make_target('src/classify.js', 'classify', 1, 5)]

    def test_timeout_returns_test_run_error(self, tmp_path, monkeypatch):
        import subprocess as sp
        import servers.coverage_js as cov_js
        monkeypatch.setattr(cov_js, '_js_available', lambda: True)

        def boom(*a, **k):
            raise sp.TimeoutExpired(cmd='npx', timeout=600)

        monkeypatch.setattr(cov_js.subprocess, 'run', boom)
        res = cov_js.measure_branch_coverage_js(
            str(tmp_path), ['test.js'], self._valid_targets())

        assert res['tool_status'] == 'test_run_error'
        assert res['fully_covered'] is False

    def test_nonzero_exit_without_coverage_json_is_tests_failed(
            self, tmp_path, monkeypatch):
        import servers.coverage_js as cov_js
        monkeypatch.setattr(cov_js, '_js_available', lambda: True)

        class FakeRun:
            returncode = 1
            stdout = 'FAIL src/classify.test.js'
            stderr = ''

        monkeypatch.setattr(cov_js.subprocess, 'run', lambda *a, **k: FakeRun())
        res = cov_js.measure_branch_coverage_js(
            str(tmp_path), ['test.js'], self._valid_targets())

        assert res['tool_status'] == 'tests_failed'
        assert res['fully_covered'] is False

    def test_zero_exit_without_coverage_json_is_test_run_error(
            self, tmp_path, monkeypatch):
        import servers.coverage_js as cov_js
        monkeypatch.setattr(cov_js, '_js_available', lambda: True)

        class FakeRun:
            returncode = 0
            stdout = 'all good'
            stderr = ''

        monkeypatch.setattr(cov_js.subprocess, 'run', lambda *a, **k: FakeRun())
        res = cov_js.measure_branch_coverage_js(
            str(tmp_path), ['test.js'], self._valid_targets())

        assert res['tool_status'] == 'test_run_error'
        assert res['fully_covered'] is False


# ---------------------------------------------------------------------------
# J1 — parser must not silently skip unparseable branches
# ---------------------------------------------------------------------------

class TestParseJsCoverageJ1FailClosed:
    """J1: within a target's line range, structural anomalies → schema_error."""

    def _branch_entry(self, line):
        return {'type': 'if', 'line': line,
                'loc': {'start': {'line': line, 'column': 0}, 'end': {'line': line + 1, 'column': 0}},
                'locations': []}

    def test_empty_arm_list_in_range_is_schema_error(self, tmp_path):
        """b[id] = [] (zero arms) within target range → schema_error, not skip."""
        from servers.coverage_js import parse_js_coverage

        cov_json = tmp_path / 'coverage-final.json'
        src_abs = str(tmp_path / 'src' / 'f.js')

        branch_map = {
            '0': self._branch_entry(2),   # in range 1-5
            '1': self._branch_entry(3),   # in range 1-5
        }
        # id 0 has valid arms; id 1 has empty list (malformed)
        b_data = {'0': [1, 0], '1': []}

        _write_istanbul_json(str(cov_json), src_abs, branch_map, b_data)

        targets = [_make_target('src/f.js', 'f', 1, 5)]
        res = parse_js_coverage(str(cov_json), targets, str(tmp_path))

        assert res['tool_status'] == 'schema_error', (
            f'Expected schema_error for zero-arm branch, got {res["tool_status"]}')
        assert res['fully_covered'] is False

    def test_orphan_b_id_in_range_is_schema_error(self, tmp_path):
        """b has id not in branchMap → schema_error (orphan b id)."""
        from servers.coverage_js import parse_js_coverage

        cov_json = tmp_path / 'coverage-final.json'
        src_abs = str(tmp_path / 'src' / 'f.js')

        # branchMap only has id '0'
        branch_map = {'0': self._branch_entry(2)}
        # b has extra id '99' that doesn't exist in branchMap
        b_data = {'0': [1, 0], '99': [3, 0]}

        _write_istanbul_json(str(cov_json), src_abs, branch_map, b_data)

        targets = [_make_target('src/f.js', 'f', 1, 5)]
        res = parse_js_coverage(str(cov_json), targets, str(tmp_path))

        assert res['tool_status'] == 'schema_error', (
            f'Expected schema_error for orphan b id, got {res["tool_status"]}')
        assert res['fully_covered'] is False

    def test_unresolvable_line_in_range_is_schema_error(self, tmp_path):
        """branchMap entry with no resolvable line → schema_error (not skip)."""
        from servers.coverage_js import parse_js_coverage

        cov_json = tmp_path / 'coverage-final.json'
        src_abs = str(tmp_path / 'src' / 'f.js')

        branch_map = {
            '0': {
                'type': 'if',
                # neither loc.start.line nor line is present
                'loc': {'start': {'column': 0}, 'end': {'line': 3, 'column': 0}},
                'locations': [],
            },
        }
        b_data = {'0': [1, 0]}

        _write_istanbul_json(str(cov_json), src_abs, branch_map, b_data)

        targets = [_make_target('src/f.js', 'f', 1, 5)]
        res = parse_js_coverage(str(cov_json), targets, str(tmp_path))

        assert res['tool_status'] == 'schema_error', (
            f'Expected schema_error for unresolvable line, got {res["tool_status"]}')
        assert res['fully_covered'] is False

    def test_clean_partial_coverage_still_ok(self, tmp_path):
        """Guard against over-rejection: a clean partial-coverage fixture → ok."""
        from servers.coverage_js import parse_js_coverage

        cov_json = tmp_path / 'coverage-final.json'
        src_abs = str(tmp_path / 'src' / 'f.js')

        branch_map = {
            '0': self._branch_entry(2),
            '1': self._branch_entry(3),
        }
        # Both valid: id 0 has one covered arm, one missing; id 1 all covered
        b_data = {'0': [1, 0], '1': [2, 3]}

        _write_istanbul_json(str(cov_json), src_abs, branch_map, b_data)

        targets = [_make_target('src/f.js', 'f', 1, 5)]
        res = parse_js_coverage(str(cov_json), targets, str(tmp_path))

        assert res['tool_status'] == 'ok', (
            f'Expected ok for clean partial coverage, got {res["tool_status"]}')
        assert res['fully_covered'] is False
        pt = res['per_target'][0]
        # id0=[1,0]=2 arms, id1=[2,3]=2 arms → n_total=4
        # Actually: id0 has 2 arms, id1 has 2 arms → n_total=4
        assert pt['n_total'] == 4
        assert pt['n_covered'] == 3  # id0 arm0 + id1 arm0 + id1 arm1 = 3
        assert len(pt['missing_branches']) == 1   # id0 arm1

    def test_branchmap_missing_b_entry_in_range_is_schema_error(self, tmp_path):
        """branchMap id in range but absent from b → schema_error."""
        from servers.coverage_js import parse_js_coverage

        cov_json = tmp_path / 'coverage-final.json'
        src_abs = str(tmp_path / 'src' / 'f.js')

        branch_map = {'0': self._branch_entry(2)}
        b_data = {}  # missing id '0'

        _write_istanbul_json(str(cov_json), src_abs, branch_map, b_data)

        targets = [_make_target('src/f.js', 'f', 1, 5)]
        res = parse_js_coverage(str(cov_json), targets, str(tmp_path))

        assert res['tool_status'] == 'schema_error'
        assert res['fully_covered'] is False


# ---------------------------------------------------------------------------
# J2 — JS unavailable must NOT proceed in run_coverage_gate
# ---------------------------------------------------------------------------

class TestRunCoverageGateJ2JsUnavailable:
    """J2: JS runner absent → _gate_reject (verdict='rejected'), NOT proceed."""

    def _setup_js_task(self, mock_db_path_fixture, tmp_path, monkeypatch,
                       test_tool='vitest'):
        """Create a done task with JS coverage_targets and return (task_id, critic_id)."""
        import servers.project as _proj
        monkeypatch.setattr(_proj, 'ensure_project',
                            lambda *a, **k: {'tech_stack': {'test_tool': test_tool}})

        from servers.tasks import (create_task, create_subtask,
                                   update_task_status, reserve_critic_task)
        epic = create_task(project='proj', description='epic', task_level='epic')
        story = create_subtask(parent_id=epic, description='story', task_level='story',
                               requires_validation=False)
        targets = [{'file_path': 'src/f.js', 'name': 'fn',
                    'line_start': 1, 'line_end': 10}]
        task = create_subtask(parent_id=story, description='write js tests',
                              requires_validation=True,
                              metadata={'coverage_targets': targets})
        update_task_status(task, 'done', result='done\nTEST_TARGETS: test/f.test.js')
        critic = reserve_critic_task(task)
        return task, critic['id']

    def test_js_runner_unavailable_rejects_not_proceeds(
            self, mock_db_path, tmp_path, monkeypatch):
        """J2: npx not found → verdict='rejected', not 'proceed'."""
        import servers.coverage as cov
        import servers.coverage_js as cov_js
        import servers.facade as facade

        monkeypatch.setattr(cov, 'derive_test_targets', lambda *a, **k: ['test/f.test.js'])
        monkeypatch.setattr(cov_js, '_js_available', lambda: False)

        task, critic_id = self._setup_js_task(mock_db_path, tmp_path, monkeypatch)
        verdict = facade.run_coverage_gate(critic_id, task, 'proj', str(tmp_path))

        assert verdict['verdict'] == 'rejected', (
            f'JS unavailable must reject (fail-closed), got {verdict["verdict"]}')
        assert '不予放行' in ' '.join(verdict.get('issues', []))

    def test_js_runner_unavailable_reject_message_is_clear(
            self, mock_db_path, tmp_path, monkeypatch):
        """J2: rejection message mentions vitest/jest and explains why."""
        import servers.coverage as cov
        import servers.coverage_js as cov_js
        import servers.facade as facade

        monkeypatch.setattr(cov, 'derive_test_targets', lambda *a, **k: ['test/f.test.js'])
        monkeypatch.setattr(cov_js, '_js_available', lambda: False)

        task, critic_id = self._setup_js_task(mock_db_path, tmp_path, monkeypatch)
        verdict = facade.run_coverage_gate(critic_id, task, 'proj', str(tmp_path))

        issues_text = ' '.join(verdict.get('issues', []))
        assert 'vitest' in issues_text or 'jest' in issues_text or 'JS' in issues_text


# ---------------------------------------------------------------------------
# J3 — file attribution must be exact, not first-suffix-match
# ---------------------------------------------------------------------------

class TestParseJsCoverageJ3FileAttribution:
    """J3: monorepo with two same-named files must attribute to the correct one."""

    def _make_branch_map(self, line):
        return {
            '0': {'type': 'if', 'line': line,
                  'loc': {'start': {'line': line, 'column': 0},
                          'end': {'line': line + 1, 'column': 0}},
                  'locations': []},
        }

    def test_monorepo_exact_match_picks_correct_file(self, tmp_path):
        """Two keys pkgA/src/foo.js (0/2 covered) and pkgB/src/foo.js (2/2).
        Target pkgA/src/foo.js → must attribute to the 0/2 one (fully_covered False).
        """
        from servers.coverage_js import parse_js_coverage

        cov_json = tmp_path / 'coverage-final.json'

        pkg_a = str(tmp_path / 'pkgA' / 'src' / 'foo.js')
        pkg_b = str(tmp_path / 'pkgB' / 'src' / 'foo.js')

        # pkgA: branch at line 2, arm 0 hit (1), arm 1 NOT hit (0) → 1/2 covered
        data_a = {'branchMap': self._make_branch_map(2), 'b': {'0': [1, 0]},
                  'path': pkg_a, 'all': False, 'statementMap': {}, 's': {}}
        # pkgB: branch at line 2, both arms hit → 2/2 covered
        data_b = {'branchMap': self._make_branch_map(2), 'b': {'0': [3, 5]},
                  'path': pkg_b, 'all': False, 'statementMap': {}, 's': {}}

        with open(str(cov_json), 'w') as fh:
            json.dump({pkg_a: data_a, pkg_b: data_b}, fh)

        # Target explicitly names pkgA/src/foo.js → must NOT grab pkgB's 2/2
        targets = [_make_target('pkgA/src/foo.js', 'fn', 1, 5)]
        res = parse_js_coverage(str(cov_json), targets, str(tmp_path))

        assert res['tool_status'] == 'ok', (
            f'Expected ok for exact-matched file, got {res["tool_status"]}: {res.get("error")}')
        assert res['fully_covered'] is False, (
            'Must attribute to pkgA (0/2 covered), not pkgB (2/2)')
        pt = res['per_target'][0]
        assert len(pt['missing_branches']) >= 1

    def test_ambiguous_bare_name_is_schema_error(self, tmp_path):
        """A bare 'foo.js' target that matches both pkgA/src/foo.js and pkgB/src/foo.js
        → schema_error (not pick one arbitrarily).
        """
        from servers.coverage_js import parse_js_coverage

        cov_json = tmp_path / 'coverage-final.json'

        pkg_a = str(tmp_path / 'pkgA' / 'src' / 'foo.js')
        pkg_b = str(tmp_path / 'pkgB' / 'src' / 'foo.js')

        data_a = {'branchMap': self._make_branch_map(2), 'b': {'0': [1, 0]},
                  'path': pkg_a, 'all': False, 'statementMap': {}, 's': {}}
        data_b = {'branchMap': self._make_branch_map(2), 'b': {'0': [3, 5]},
                  'path': pkg_b, 'all': False, 'statementMap': {}, 's': {}}

        with open(str(cov_json), 'w') as fh:
            json.dump({pkg_a: data_a, pkg_b: data_b}, fh)

        # Ambiguous bare name — both keys end with /foo.js
        targets = [_make_target('foo.js', 'fn', 1, 5)]
        res = parse_js_coverage(str(cov_json), targets, str(tmp_path))

        assert res['tool_status'] == 'schema_error', (
            f'Ambiguous file match must be schema_error, got {res["tool_status"]}')
        assert res['fully_covered'] is False


# ---------------------------------------------------------------------------
# Major — npx --no-install must be present in the command list
# ---------------------------------------------------------------------------

class TestNpxNoInstall:
    """Verify that npx --no-install is used so registry downloads are prevented."""

    def _capture_cmd(self, tmp_path, monkeypatch, tool):
        import servers.coverage_js as cov_js
        monkeypatch.setattr(cov_js, '_js_available', lambda: True)

        captured = {}

        class FakeRun:
            returncode = 0
            stdout = ''
            stderr = ''

        def fake_run(cmd, **kwargs):
            captured['cmd'] = list(cmd)
            # Return zero exit but no coverage json → test_run_error (fine for cmd check)
            return FakeRun()

        monkeypatch.setattr(cov_js.subprocess, 'run', fake_run)

        targets = [_make_target('src/f.js', 'fn', 1, 5)]
        cov_js.measure_branch_coverage_js(str(tmp_path), ['test.js'], targets, tool=tool)
        return captured.get('cmd', [])

    def test_vitest_cmd_includes_no_install(self, tmp_path, monkeypatch):
        cmd = self._capture_cmd(tmp_path, monkeypatch, 'vitest')
        assert '--no-install' in cmd, (
            f'vitest cmd must contain --no-install to prevent registry downloads; got {cmd}')
        assert cmd[0] == 'npx'
        assert 'vitest' in cmd

    def test_jest_cmd_includes_no_install(self, tmp_path, monkeypatch):
        cmd = self._capture_cmd(tmp_path, monkeypatch, 'jest')
        assert '--no-install' in cmd, (
            f'jest cmd must contain --no-install to prevent registry downloads; got {cmd}')
        assert cmd[0] == 'npx'
        assert 'jest' in cmd


# ---------------------------------------------------------------------------
# Major — mocha must be unsupported (not silently run as vitest)
# ---------------------------------------------------------------------------

class TestMochaMustBeUnsupported:
    """Mocha (and any non-vitest/jest) must return unavailable, not run vitest."""

    def test_mocha_returns_unavailable_not_vitest(self, tmp_path, monkeypatch):
        import servers.coverage_js as cov_js
        monkeypatch.setattr(cov_js, '_js_available', lambda: True)

        subprocess_called = []

        def boom(*a, **k):
            subprocess_called.append(list(a[0]) if a else [])
            raise AssertionError('subprocess.run must NOT be called for mocha')

        monkeypatch.setattr(cov_js.subprocess, 'run', boom)

        targets = [_make_target('src/f.js', 'fn', 1, 5)]
        res = cov_js.measure_branch_coverage_js(
            str(tmp_path), ['test/f.test.js'], targets, tool='mocha')

        assert res['tool_status'] == 'unavailable', (
            f'mocha must return unavailable (not run vitest), got {res["tool_status"]}')
        assert res['fully_covered'] is False
        assert subprocess_called == [], 'subprocess.run must not be invoked for mocha'

    def test_unknown_tool_returns_unavailable(self, tmp_path, monkeypatch):
        import servers.coverage_js as cov_js
        monkeypatch.setattr(cov_js, '_js_available', lambda: True)

        targets = [_make_target('src/f.js', 'fn', 1, 5)]
        res = cov_js.measure_branch_coverage_js(
            str(tmp_path), ['test.js'], targets, tool='karma')

        assert res['tool_status'] == 'unavailable'
        assert res['fully_covered'] is False

    def test_mocha_gate_rejects_not_proceeds(self, mock_db_path, tmp_path, monkeypatch):
        """In the gate, mocha → measure returns unavailable → gate must reject (not proceed)."""
        import servers.coverage as cov
        import servers.coverage_js as cov_js
        import servers.project as _proj
        import servers.facade as facade

        monkeypatch.setattr(_proj, 'ensure_project',
                            lambda *a, **k: {'tech_stack': {'test_tool': 'mocha'}})
        monkeypatch.setattr(cov, 'derive_test_targets', lambda *a, **k: ['test/f.test.js'])
        monkeypatch.setattr(cov_js, '_js_available', lambda: True)
        # measure_branch_coverage_js will return unavailable (mocha unsupported)
        # without calling subprocess — no need to monkeypatch subprocess

        from servers.tasks import (create_task, create_subtask,
                                   update_task_status, reserve_critic_task)
        epic = create_task(project='proj', description='epic', task_level='epic')
        story = create_subtask(parent_id=epic, description='story', task_level='story',
                               requires_validation=False)
        targets = [{'file_path': 'src/f.js', 'name': 'fn',
                    'line_start': 1, 'line_end': 10}]
        task = create_subtask(parent_id=story, description='write mocha tests',
                              requires_validation=True,
                              metadata={'coverage_targets': targets})
        update_task_status(task, 'done', result='done\nTEST_TARGETS: test/f.test.js')
        critic = reserve_critic_task(task)
        critic_id = critic['id']

        verdict = facade.run_coverage_gate(critic_id, task, 'proj', str(tmp_path))

        assert verdict['verdict'] == 'rejected', (
            f'mocha should cause gate to reject (fail-closed), got {verdict["verdict"]}')


# ---------------------------------------------------------------------------
# Major — subprocess output sanitization
# ---------------------------------------------------------------------------

class TestSubprocessOutputSanitization:
    """ANSI/control chars in subprocess output must be stripped before use."""

    def test_ansi_codes_stripped_from_error_message(self, tmp_path, monkeypatch):
        """ANSI color codes in runner stdout/stderr must not appear in error message."""
        import servers.coverage_js as cov_js
        monkeypatch.setattr(cov_js, '_js_available', lambda: True)

        class FakeRun:
            returncode = 1
            stdout = '\x1b[31mFAIL\x1b[0m src/classify.test.js'
            stderr = '\x1b[33m⚠ Coverage not collected\x1b[0m'

        monkeypatch.setattr(cov_js.subprocess, 'run', lambda *a, **k: FakeRun())

        targets = [_make_target('src/f.js', 'fn', 1, 5)]
        res = cov_js.measure_branch_coverage_js(str(tmp_path), ['test.js'], targets)

        assert res['tool_status'] == 'tests_failed'
        error = res.get('error') or ''
        assert '\x1b' not in error, (
            f'ANSI escape codes must be stripped from error message; got: {error!r}')

    def test_control_chars_stripped_from_error_message(self, tmp_path, monkeypatch):
        """Control chars (e.g., BEL, BS) must be stripped from error messages."""
        import servers.coverage_js as cov_js
        monkeypatch.setattr(cov_js, '_js_available', lambda: True)

        class FakeRun:
            returncode = 1
            stdout = 'Error\x07\x08 something broke'  # BEL + BS
            stderr = ''

        monkeypatch.setattr(cov_js.subprocess, 'run', lambda *a, **k: FakeRun())

        targets = [_make_target('src/f.js', 'fn', 1, 5)]
        res = cov_js.measure_branch_coverage_js(str(tmp_path), ['test.js'], targets)

        error = res.get('error') or ''
        assert '\x07' not in error and '\x08' not in error, (
            f'Control chars must be stripped from error message; got: {error!r}')


# ---------------------------------------------------------------------------
# Robustness — non-dict loc in branchMap entry → schema_error (no crash)
# ---------------------------------------------------------------------------

class TestParseJsCoverageNonDictLoc:
    """Robustness: branchMap entry with loc that is not a dict (e.g. a string)
    must produce 'schema_error', not raise AttributeError.
    """

    def test_loc_is_string_returns_schema_error(self, tmp_path):
        """coverage-final.json where branchMap entry loc is a string → schema_error."""
        from servers.coverage_js import parse_js_coverage

        cov_json = tmp_path / 'coverage-final.json'
        src_abs = str(tmp_path / 'src' / 'f.js')

        # loc is a string (invalid schema — should not raise, should return schema_error)
        branch_map = {
            '0': {
                'type': 'if',
                'line': 2,
                'loc': 'invalid-string-not-a-dict',  # non-dict loc
                'locations': [],
            },
        }
        b_data = {'0': [1, 0]}

        data = {
            src_abs: {
                'path': src_abs,
                'all': False,
                'statementMap': {},
                's': {},
                'branchMap': branch_map,
                'b': b_data,
            }
        }
        with open(str(cov_json), 'w', encoding='utf-8') as fh:
            json.dump(data, fh)

        targets = [{'file_path': 'src/f.js', 'name': 'f',
                    'line_start': 1, 'line_end': 10}]
        # Must NOT raise; must return schema_error
        res = parse_js_coverage(str(cov_json), targets, str(tmp_path))

        assert res['tool_status'] == 'schema_error', (
            f'Non-dict loc must produce schema_error, got {res["tool_status"]}: {res.get("error")}')
        assert res['fully_covered'] is False

    def test_loc_start_is_string_returns_schema_error(self, tmp_path):
        """branchMap entry where loc.start is a string (not a dict) → schema_error."""
        from servers.coverage_js import parse_js_coverage

        cov_json = tmp_path / 'coverage-final.json'
        src_abs = str(tmp_path / 'src' / 'f.js')

        branch_map = {
            '0': {
                'type': 'if',
                'line': 2,
                'loc': {'start': 'also-a-string', 'end': {'line': 3, 'column': 0}},
                'locations': [],
            },
        }
        b_data = {'0': [1, 0]}

        data = {
            src_abs: {
                'path': src_abs, 'all': False, 'statementMap': {}, 's': {},
                'branchMap': branch_map, 'b': b_data,
            }
        }
        with open(str(cov_json), 'w', encoding='utf-8') as fh:
            json.dump(data, fh)

        targets = [{'file_path': 'src/f.js', 'name': 'f',
                    'line_start': 1, 'line_end': 10}]
        res = parse_js_coverage(str(cov_json), targets, str(tmp_path))

        # loc.start is non-dict → _branch_line returns None → J1 catches → schema_error
        assert res['tool_status'] == 'schema_error', (
            f'Non-dict loc.start must produce schema_error, got {res["tool_status"]}')
        assert res['fully_covered'] is False

    def test_valid_loc_after_non_dict_not_affected(self, tmp_path):
        """Sanity: a well-formed entry with loc.start.line still parses to 'ok'."""
        from servers.coverage_js import parse_js_coverage

        cov_json = tmp_path / 'coverage-final.json'
        src_abs = str(tmp_path / 'src' / 'f.js')

        branch_map = {
            '0': {
                'type': 'if',
                'line': 2,
                'loc': {'start': {'line': 2, 'column': 0}, 'end': {'line': 3, 'column': 0}},
                'locations': [],
            },
        }
        b_data = {'0': [1, 1]}  # both arms hit

        data = {
            src_abs: {
                'path': src_abs, 'all': False, 'statementMap': {}, 's': {},
                'branchMap': branch_map, 'b': b_data,
            }
        }
        with open(str(cov_json), 'w', encoding='utf-8') as fh:
            json.dump(data, fh)

        targets = [{'file_path': 'src/f.js', 'name': 'f',
                    'line_start': 1, 'line_end': 10}]
        res = parse_js_coverage(str(cov_json), targets, str(tmp_path))

        assert res['tool_status'] == 'ok', (
            f'Well-formed loc must still parse ok, got {res["tool_status"]}')
        assert res['fully_covered'] is True


# =============================================================================
# Hardening: JS coverage parser guard against non-dict file entries
# =============================================================================

class TestJSCoverageHardeningNonDictEntry:
    """When a file value in coverage-final.json is NOT a dict (e.g., a string),
    the parser must not crash — it should skip or signal schema_error instead.

    This is a robustness fix: malformed coverage JSON must not crash the gate.
    """

    def test_non_dict_file_entry_does_not_crash(self, tmp_path):
        """A file entry that is a string instead of dict → schema_error, not crash."""
        from servers.coverage_js import parse_js_coverage

        cov_json = tmp_path / 'coverage.json'
        src_abs = str(tmp_path / 'src/f.js')

        # Malformed: file value is a string, not a dict
        data = {
            src_abs: "this should be a dict, not a string"
        }
        with open(str(cov_json), 'w', encoding='utf-8') as fh:
            json.dump(data, fh)

        targets = [{'file_path': 'src/f.js', 'name': 'f',
                    'line_start': 1, 'line_end': 10}]

        # Must not raise; should handle gracefully
        res = parse_js_coverage(str(cov_json), targets, str(tmp_path))

        # Schema error is expected (file entry is malformed)
        assert res['tool_status'] == 'schema_error', (
            f'Non-dict file entry must signal schema_error, got {res["tool_status"]}')
        assert res['error'] is not None, (
            'schema_error must include an error message')
