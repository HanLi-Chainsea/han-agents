"""Live integration test for JS coverage backend — Vitest against /tmp/vitest-spike.

SKIPPED BY DEFAULT.  Only runs when all three conditions are met:
  1. HAN_RUN_JS_LIVE=1 environment variable is set
  2. npx is resolvable on PATH
  3. /tmp/vitest-spike directory exists

This test intentionally runs npm/npx. Do NOT include in the default pytest suite.
"""
import os
import shutil

import pytest

_SPIKE_DIR = '/tmp/vitest-spike'

_LIVE = (
    os.environ.get('HAN_RUN_JS_LIVE')
    and shutil.which('npx')
    and os.path.isdir(_SPIKE_DIR)
)


@pytest.mark.skipif(
    not _LIVE,
    reason='Set HAN_RUN_JS_LIVE=1, ensure npx is on PATH, and /tmp/vitest-spike exists',
)
class TestMeasureBranchCoverageJsLive:
    def test_vitest_spike_partial_coverage(self):
        """Run vitest coverage against the spike fixture in place.

        We do NOT copytree the fixture: copying node_modules breaks the
        .bin symlinks, so `npx vitest` can't resolve. The backend writes
        coverage to a tempdir (non-invasive), so running in place is safe.
        """
        from servers.coverage_js import measure_branch_coverage_js

        proj = _SPIKE_DIR

        test_targets = ['test/classify.test.js']
        coverage_targets = [{
            'file_path': 'src/classify.js',
            'name': 'classify',
            'line_start': 1,
            'line_end': 5,
        }]

        res = measure_branch_coverage_js(
            proj,
            test_targets,
            coverage_targets,
            tool='vitest',
        )

        assert res['tool_status'] == 'ok', (
            f'Expected ok, got {res["tool_status"]}: {res.get("error")}'
        )
        assert res['fully_covered'] is False, (
            'classify in vitest-spike is only partially covered (pos path only)'
        )

        pt = res['per_target'][0]
        assert pt['n_covered'] >= 1, 'At least one branch arm must be covered'
        assert len(pt['missing_branches']) >= 1, (
            'neg and zero paths are not covered → at least one missing branch'
        )
