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
import re
import shutil
import subprocess
import tempfile
from typing import Dict, List, Optional

_TIMEOUT_SEC = 600

# Reuse the same ANSI sanitizer pattern used in servers/coverage.py
_ANSI_RE = re.compile(r'\x1b\[[0-9;?]*[ -/]*[@-~]')


def _sanitize(text: Optional[str], limit: int = 600) -> str:
    """Strip ANSI escape codes and control characters; truncate to limit chars.

    Mirrors servers.coverage._sanitize — JS runner stdout/stderr goes into
    reject/working-memory text, so it must be sanitized before use.
    """
    if not text:
        return ''
    text = _ANSI_RE.sub('', text)
    text = ''.join(c for c in text
                   if c in ('\n', '\t') or (ord(c) >= 32 and ord(c) != 127))
    return text.strip()[-limit:]


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

    Robustness: if loc or loc.start is not a dict (e.g. a string from a
    malformed coverage-final.json), return None rather than raising.
    The caller treats None as schema_error (fail-closed).
    """
    loc = branch_entry.get('loc')
    if isinstance(loc, dict):
        start = loc.get('start')
        if isinstance(start, dict):
            line = start.get('line')
            if isinstance(line, int) and not isinstance(line, bool) and line > 0:
                return line
    # fallback
    line = branch_entry.get('line')
    if isinstance(line, int) and not isinstance(line, bool) and line > 0:
        return line
    return None


def _match_file(coverage_keys: List[str], file_path: str, project_root: str) -> Optional[str]:
    """Return the unique coverage JSON key that corresponds to file_path, or None.

    J3 fix — three-phase lookup:
      1. Exact realpath match: resolve file_path against project_root and compare
         with os.path.realpath of each JSON key.  If exactly one key matches → use it.
      2. Suffix-unique match: among all JSON keys whose normalized path ends with
         '/file_path' (or equals it), if exactly one matches → use it.
      3. Zero matches → None (no_targets).
         >1 matches at any phase → None ('schema_error' signalled by caller).

    Returns the matched key string, or None when zero matches were found.
    The caller distinguishes zero-match vs ambiguous-match by checking separately.
    """
    fp = file_path.replace('\\', '/')

    # Phase 1: Exact realpath match
    if not os.path.isabs(fp):
        candidate = os.path.realpath(os.path.join(project_root, fp))
    else:
        candidate = os.path.realpath(fp)

    exact_matches = [k for k in coverage_keys if os.path.realpath(k) == candidate]
    if len(exact_matches) == 1:
        return exact_matches[0]
    if len(exact_matches) > 1:
        return None  # ambiguous — caller should signal schema_error

    # Phase 2: Suffix match — only accept when unique
    suffix_matches = [k for k in coverage_keys
                      if k.replace('\\', '/').endswith('/' + fp)
                      or k.replace('\\', '/') == fp]
    if len(suffix_matches) == 1:
        return suffix_matches[0]
    # 0 → no match; >1 → ambiguous; both return None
    return None


def _match_file_ambiguous(coverage_keys: List[str], file_path: str,
                          project_root: str) -> bool:
    """Return True when file_path maps to MORE THAN ONE JSON key (ambiguous).

    Used by parse_js_coverage to distinguish no_targets from schema_error.
    """
    fp = file_path.replace('\\', '/')
    if not os.path.isabs(fp):
        candidate = os.path.realpath(os.path.join(project_root, fp))
    else:
        candidate = os.path.realpath(fp)

    exact_matches = [k for k in coverage_keys if os.path.realpath(k) == candidate]
    if len(exact_matches) > 1:
        return True

    # If exact phase was unambiguous (0 or 1), check suffix-unique
    if len(exact_matches) == 0:
        suffix_matches = [k for k in coverage_keys
                          if k.replace('\\', '/').endswith('/' + fp)
                          or k.replace('\\', '/') == fp]
        if len(suffix_matches) > 1:
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
    - Target file ambiguous (>1 JSON keys match) → 'schema_error'
    - File present but no branchMap/b data → 'schema_error'
    - Missing/malformed b entries          → 'schema_error'
    - b[id] = [] (zero arms)              → 'schema_error'
    - orphan b id (in b but not branchMap) within range → 'schema_error'
    - branchMap entry with unresolvable line within range → 'schema_error'
    - branchMap entry whose loc or loc.start is not a dict → 'schema_error'
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
    coverage_keys = list(raw.keys())
    per_target = []

    for target in coverage_targets:
        file_path = target.get('file_path', '')
        name = target.get('name', '')
        line_start = target['line_start']
        line_end = target['line_end']

        # J3: Use exact/unique match; detect ambiguity
        matched_key = _match_file(coverage_keys, file_path, root_abs)

        if matched_key is None:
            # Distinguish: ambiguous vs absent
            if _match_file_ambiguous(coverage_keys, file_path, root_abs):
                return _result('schema_error',
                               f'Target file {file_path!r} matches multiple entries in '
                               f'coverage JSON (ambiguous in monorepo). '
                               f'Use a more specific path.')
            return _result('no_targets',
                           f'Target file not found in coverage: {file_path}')

        file_data = raw[matched_key]

        # Validate presence of branchMap and b
        branch_map = file_data.get('branchMap')
        b_data = file_data.get('b')

        if not isinstance(branch_map, dict) or not isinstance(b_data, dict):
            return _result('schema_error',
                           f'Target {file_path}: coverage data missing branchMap/b '
                           f'(file present but no branch schema)')

        # J1: Validate branchMap↔b correspondence GLOBALLY first, then per-range.
        # Check for orphan b ids (in b but not in branchMap) within target range.
        # We do range-aware checking below.

        # Collect branches in range — fail-closed on any structural anomaly
        covered_branches = []
        missing_branches = []

        # Track which branchMap ids are in range
        in_range_bids = set()

        for bid, bentry in branch_map.items():
            if not isinstance(bentry, dict):
                return _result('schema_error',
                               f'Target {file_path}: branchMap[{bid}] is not a dict')

            # Robustness: if loc is present but not a dict, treat as schema_error.
            # The _branch_line fallback can still find a valid line via the top-level
            # 'line' field, masking the malformed loc. We must explicitly reject this
            # to prevent silently accepting malformed coverage data (non-dict loc).
            _loc = bentry.get('loc')
            if _loc is not None and not isinstance(_loc, dict):
                return _result('schema_error',
                               f'Target {file_path}: branchMap[{bid}].loc is not a dict '
                               f'(got {type(_loc).__name__!r}); malformed coverage schema')
            _loc_start = _loc.get('start') if isinstance(_loc, dict) else None
            if _loc_start is not None and not isinstance(_loc_start, dict):
                return _result('schema_error',
                               f'Target {file_path}: branchMap[{bid}].loc.start is not a '
                               f'dict (got {type(_loc_start).__name__!r}); malformed schema')
            br_line = _branch_line(bentry)

            # J1: Unresolvable line within range → schema_error (not skip-and-pass)
            if br_line is None:
                # We don't know if this branch is in range or not.
                # Fail-closed: treat as schema_error since we cannot safely skip
                # a branch that might be in range.
                return _result('schema_error',
                               f'Target {file_path}: branchMap[{bid}] has no '
                               f'resolvable line number (loc.start.line and line '
                               f'field are both absent/invalid or loc/loc.start '
                               f'is not a dict)')

            if not (line_start <= br_line <= line_end):
                continue

            in_range_bids.add(bid)

            # J1: b[id] missing → schema_error
            if bid not in b_data:
                return _result('schema_error',
                               f'Target {file_path}: b[{bid}] missing (branchMap/b mismatch)')
            arm_hits = b_data[bid]
            if not isinstance(arm_hits, list):
                return _result('schema_error',
                               f'Target {file_path}: b[{bid}] is not a list')

            # J1: Empty arm list → schema_error (zero-arm branch is malformed)
            if len(arm_hits) == 0:
                return _result('schema_error',
                               f'Target {file_path}: b[{bid}] is empty (zero arms) '
                               f'at line {br_line}; malformed coverage data')

            for arm_idx, hit_count in enumerate(arm_hits):
                if not isinstance(hit_count, int) or isinstance(hit_count, bool):
                    return _result('schema_error',
                                   f'Target {file_path}: b[{bid}][{arm_idx}] is not int')
                entry = {'from': br_line, 'to': arm_idx}
                if hit_count > 0:
                    covered_branches.append(entry)
                else:
                    missing_branches.append(entry)

        # J1: Detect orphan b ids — in b_data but not in branchMap — within range.
        # We approximate "in range" for orphan ids by checking all b ids not in branchMap
        # at all (since we cannot determine their line). Any orphan → schema_error.
        for bid in b_data:
            if bid not in branch_map:
                return _result('schema_error',
                               f'Target {file_path}: b[{bid}] has no matching '
                               f'branchMap entry (orphan b id; branchMap/b mismatch)')

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
        tool: 'vitest' (default) or 'jest'. Other tools (mocha etc.) → 'unavailable'.

    Returns:
        Same contract as servers/coverage.py::measure_branch_coverage.
        tool_status values:
          'ok'             — ran cleanly; per_target populated
          'tests_failed'   — test run exited non-zero with test failures
          'test_run_error' — subprocess error / timeout / coverage json absent
          'no_targets'     — coverage json produced but target file absent
          'invalid_targets'— bad line_start/line_end
          'schema_error'   — json present but malformed
          'unavailable'    — npx not found / unsupported tool (infra; gate rejects)
    """
    from servers.coverage import _invalid_targets

    bad = _invalid_targets(coverage_targets)
    if bad:
        return _result('invalid_targets', bad)

    if not coverage_targets:
        return _result('no_targets', 'No coverage targets provided')

    # Major: mocha (and any non-vitest/jest) → fail-closed unavailable.
    # Do NOT silently run a different runner — that is a false green.
    tool_lower = (tool or 'vitest').lower().strip()
    if tool_lower not in ('vitest', 'jest'):
        return _result('unavailable',
                       f'JS coverage runner "{tool}" is not supported '
                       f'(only vitest and jest are supported); '
                       f'cannot verify branch coverage')

    # J2: JS unavailable → fail-closed (not proceed).
    if not _js_available():
        return _result('unavailable',
                       'JS coverage runner 不可用：請確認專案已安裝 vitest/jest；'
                       '無法驗證分支覆蓋,不予放行')

    project_path = os.path.realpath(project_path)

    with tempfile.TemporaryDirectory() as tmp:
        cov_dir = os.path.join(tmp, 'coverage')

        # Major: use --no-install so npx uses the project's locally installed
        # binary and fails (→ unavailable → gate rejects) rather than downloading
        # an unpinned runner from the registry.
        if tool_lower == 'jest':
            cmd = [
                'npx', '--no-install', 'jest',
                '--coverage',
                '--coverageReporters=json',
                f'--coverageDirectory={cov_dir}',
                '--',
                *test_targets,
            ]
        else:
            # vitest
            cmd = [
                'npx', '--no-install', 'vitest', 'run',
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
        # Major: sanitize subprocess output before it enters rejection context
        combined = _sanitize((run.stdout or '') + (run.stderr or ''))

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
