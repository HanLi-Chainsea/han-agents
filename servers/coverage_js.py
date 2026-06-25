"""JavaScript/TypeScript branch-coverage backend.

Supports Vitest and Jest test runners via istanbul-format coverage-final.json.
Non-invasive: CLI flags only, never edits package.json / vitest.config / jest.config.

Istanbul coverage-final.json schema (v8 / babel providers):
  {
    "<abs_file_path>": {
      "branchMap": {
        "<id>": {
          "type": "branch" | "if" | ...,
          "line": <int>,           # fallback line for the branch
          "loc": {"start": {"line": <int>, ...}, ...},
          "locations": [...]
        }
      },
      "b": {
        "<id>": [<arm0_hits>, <arm1_hits>, ...]
      }
    }
  }

Branch line resolution (in order):
  1. branchMap[id]["loc"]["start"]["line"]  — precise start line of branch expression
  2. branchMap[id]["line"]                  — fallback top-level "line" field
"""

import json
import os
import shutil
import subprocess
import tempfile
from typing import Dict, List, Optional

_TIMEOUT_SEC = 600


def _result(status: str, error: Optional[str] = None,
            per_target: Optional[List[Dict]] = None,
            fully_covered: bool = False) -> Dict:
    """Match the contract from servers/coverage.py and coverage_java.py."""
    return {'tool_status': status, 'fully_covered': fully_covered,
            'per_target': per_target or [], 'error': error}


def _branch_line(branch_entry: dict) -> Optional[int]:
    """Extract the canonical line number for a branchMap entry.

    Prefer loc.start.line (precise); fall back to top-level "line".
    Returns None if neither is a valid int.
    """
    loc = branch_entry.get('loc') or {}
    start = loc.get('start') or {}
    line = start.get('line')
    if isinstance(line, int) and not isinstance(line, bool) and line > 0:
        return line
    # fallback
    line = branch_entry.get('line')
    if isinstance(line, int) and not isinstance(line, bool) and line > 0:
        return line
    return None


def _match_file(json_key: str, file_path: str, project_root: str) -> bool:
    """Return True when the coverage JSON key corresponds to file_path.

    Strategy:
    1. Exact suffix match (json_key ends with file_path component)
    2. Realpath match using project_root as base
    """
    # Normalize slashes
    jk = json_key.replace('\\', '/')
    fp = file_path.replace('\\', '/')

    # Suffix match: json key (absolute path) must end with /fp or equal fp
    if jk.endswith('/' + fp) or jk == fp:
        return True

    # Realpath match
    if not os.path.isabs(fp):
        candidate = os.path.realpath(os.path.join(project_root, fp))
    else:
        candidate = os.path.realpath(fp)
    if os.path.realpath(jk) == candidate:
        return True

    return False


def parse_js_coverage(coverage_json_path: str,
                      coverage_targets: List[Dict],
                      project_root: str) -> Dict:
    """Parse an istanbul-format coverage-final.json to per-target branch coverage.

    Args:
        coverage_json_path: Path to coverage-final.json
        coverage_targets: List of {'file_path', 'name', 'line_start', 'line_end'}
        project_root: Project root directory (for relative path resolution)

    Returns:
        Same contract as servers/coverage.py::measure_branch_coverage and
        coverage_java.parse_jacoco_xml:
        {
            'tool_status': 'ok'|'no_targets'|'schema_error'|'invalid_targets'|...,
            'fully_covered': bool,
            'per_target': [{
                'file_path', 'name', 'line_start', 'line_end',
                'missing_branches': [{'from': <line>, 'to': <arm_index>}],
                'covered_branches': [{'from': <line>, 'to': <arm_index>}],
                'n_total', 'n_covered'
            }],
            'error': str|None
        }

    Fail-closed invariants (no 假綠):
    - Invalid line ranges  → 'invalid_targets'
    - Target file absent   → 'no_targets'
    - File present but no branchMap/b data → 'schema_error'
    - Missing/malformed b entries          → 'schema_error'
    - NEVER defaults missing data to fully-covered.
    """
    # Validate targets — reuse the same guard as python/java backends (DRY, fail-closed)
    from servers.coverage import _invalid_targets
    bad = _invalid_targets(coverage_targets)
    if bad:
        return _result('invalid_targets', bad)

    if not coverage_targets:
        return _result('no_targets', 'No coverage targets provided')

    # Load the coverage JSON
    try:
        with open(coverage_json_path, encoding='utf-8') as fh:
            raw = json.load(fh)
    except (OSError, ValueError) as e:
        return _result('test_run_error', f'Failed to load coverage JSON: {e}')

    if not isinstance(raw, dict):
        return _result('schema_error', 'coverage-final.json: root is not a dict')

    root_abs = os.path.realpath(project_root)
    per_target = []

    for target in coverage_targets:
        file_path = target.get('file_path', '')
        name = target.get('name', '')
        line_start = target['line_start']
        line_end = target['line_end']

        # Find the matching key in the coverage JSON
        file_data = None
        for key, val in raw.items():
            if _match_file(key, file_path, root_abs):
                file_data = val
                break

        if file_data is None:
            return _result('no_targets',
                           f'Target file not found in coverage: {file_path}')

        # Validate presence of branchMap and b
        branch_map = file_data.get('branchMap')
        b_data = file_data.get('b')

        if not isinstance(branch_map, dict) or not isinstance(b_data, dict):
            return _result('schema_error',
                           f'Target {file_path}: coverage data missing branchMap/b '
                           f'(file present but no branch schema)')

        # Collect branches in range
        covered_branches = []
        missing_branches = []

        for bid, bentry in branch_map.items():
            if not isinstance(bentry, dict):
                return _result('schema_error',
                               f'Target {file_path}: branchMap[{bid}] is not a dict')

            br_line = _branch_line(bentry)
            if br_line is None:
                # Cannot resolve line — skip (defensive; not a hard error)
                continue

            if not (line_start <= br_line <= line_end):
                continue

            # Get arm hit-counts
            if bid not in b_data:
                return _result('schema_error',
                               f'Target {file_path}: b[{bid}] missing (branchMap/b mismatch)')
            arm_hits = b_data[bid]
            if not isinstance(arm_hits, list):
                return _result('schema_error',
                               f'Target {file_path}: b[{bid}] is not a list')

            for arm_idx, hit_count in enumerate(arm_hits):
                if not isinstance(hit_count, int) or isinstance(hit_count, bool):
                    return _result('schema_error',
                                   f'Target {file_path}: b[{bid}][{arm_idx}] is not int')
                entry = {'from': br_line, 'to': arm_idx}
                if hit_count > 0:
                    covered_branches.append(entry)
                else:
                    missing_branches.append(entry)

        # File present + branchMap/b present, but no branch slots in range → schema_error
        # (not no_targets — the file IS there; we just got no data for this target range)
        if not covered_branches and not missing_branches:
            return _result('schema_error',
                           f'Target {name} ({file_path}): no branch data in line range '
                           f'{line_start}-{line_end}')

        n_total = len(covered_branches) + len(missing_branches)
        n_covered = len(covered_branches)

        per_target.append({
            'file_path': file_path,
            'name': name,
            'line_start': line_start,
            'line_end': line_end,
            'covered_branches': covered_branches,
            'missing_branches': missing_branches,
            'n_total': n_total,
            'n_covered': n_covered,
        })

    fully_covered = all(not pt['missing_branches'] for pt in per_target)
    return _result('ok', None, per_target=per_target, fully_covered=fully_covered)


def _js_available() -> bool:
    """Return True when npx (and therefore node) is resolvable."""
    return shutil.which('npx') is not None


def measure_branch_coverage_js(
    project_path: str,
    test_targets: List[str],
    coverage_targets: List[Dict],
    *,
    tool: str = 'vitest',
) -> Dict:
    """Run Vitest or Jest non-invasively and return per-target branch coverage.

    Non-invasive: CLI flags only. Never edits package.json / vitest.config /
    jest.config.  Coverage reports are written to a temporary directory and
    cleaned up automatically.

    Args:
        project_path: Absolute path to the JS/TS project root.
        test_targets: Test file paths (relative to project_path or absolute).
        coverage_targets: List of {'file_path', 'name', 'line_start', 'line_end'}.
        tool: 'vitest' (default) or 'jest'.

    Returns:
        Same contract as servers/coverage.py::measure_branch_coverage.
        tool_status values:
          'ok'             — ran cleanly; per_target populated
          'tests_failed'   — test run exited non-zero with test failures
          'test_run_error' — subprocess error / timeout / coverage json absent
          'no_targets'     — coverage json produced but target file absent
          'invalid_targets'— bad line_start/line_end
          'schema_error'   — json present but malformed
          'unavailable'    — npx not found (infra; caller may fail-open)
    """
    from servers.coverage import _invalid_targets

    bad = _invalid_targets(coverage_targets)
    if bad:
        return _result('invalid_targets', bad)

    if not coverage_targets:
        return _result('no_targets', 'No coverage targets provided')

    if not _js_available():
        return _result('unavailable', 'npx not found; JS coverage unavailable')

    project_path = os.path.realpath(project_path)

    with tempfile.TemporaryDirectory() as tmp:
        cov_dir = os.path.join(tmp, 'coverage')

        tool_lower = (tool or 'vitest').lower().strip()

        if tool_lower == 'jest':
            cmd = [
                'npx', 'jest',
                '--coverage',
                '--coverageReporters=json',
                f'--coverageDirectory={cov_dir}',
                '--',
                *test_targets,
            ]
        else:
            # Default to vitest
            cmd = [
                'npx', 'vitest', 'run',
                '--coverage',
                '--coverage.provider=v8',
                '--coverage.reporter=json',
                f'--coverage.reportsDirectory={cov_dir}',
                '--',
                *test_targets,
            ]

        try:
            run = subprocess.run(
                cmd,
                cwd=project_path,
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_SEC,
            )
        except subprocess.TimeoutExpired:
            return _result('test_run_error',
                           f'{tool} timed out (>{_TIMEOUT_SEC}s)')
        except Exception as e:
            return _result('test_run_error', f'Failed to launch {tool}: {e}')

        rc = run.returncode
        combined = ((run.stdout or '') + (run.stderr or ''))[-600:]

        coverage_json = os.path.join(cov_dir, 'coverage-final.json')

        if rc != 0:
            # Non-zero exit: distinguish test failures from tool errors.
            # If coverage-final.json was produced, tests ran but some failed.
            if os.path.isfile(coverage_json):
                return _result('tests_failed',
                               f'{tool} tests failed (rc={rc}): {combined}')
            else:
                # Could be test failure before coverage written, or tool error
                return _result('tests_failed',
                               f'{tool} exited non-zero (rc={rc}): {combined}')

        if not os.path.isfile(coverage_json):
            return _result('test_run_error',
                           f'{tool} ran successfully but coverage-final.json not found '
                           f'(expected at {coverage_json})')

        return parse_js_coverage(coverage_json, coverage_targets, project_path)
