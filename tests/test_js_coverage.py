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
