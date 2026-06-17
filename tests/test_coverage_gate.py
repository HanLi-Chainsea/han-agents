"""分支覆蓋率硬關：資料層、量測、gate 測試"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestCoverageGapHasLineEnd:
    def test_gap_dict_includes_line_end(self, monkeypatch):
        import servers.drift as drift

        fake_nodes = [{
            'id': 'n1', 'kind': 'function', 'name': 'foo',
            'file_path': 'servers/x.py', 'line_start': 10, 'line_end': 25,
            'visibility': 'public',
        }]
        monkeypatch.setattr(drift, '_fetch_all',
                            lambda fn, page_size=500: fake_nodes if 'nodes' in fn.__qualname__ or True else [])
        # 兩種 edge 查詢都回空 → 無覆蓋 → 一定成為 gap
        import servers.code_graph as cg
        monkeypatch.setattr(cg, 'get_code_nodes', lambda *a, **k: [])
        monkeypatch.setattr(cg, 'get_code_edges', lambda *a, **k: [])

        # _fetch_all 第一次（nodes）回 fake_nodes，之後（edges）回 []
        calls = {'n': 0}
        def fake_fetch(fn, page_size=500):
            calls['n'] += 1
            return fake_nodes if calls['n'] == 1 else []
        monkeypatch.setattr(drift, '_fetch_all', fake_fetch)

        gaps = drift.detect_coverage_gaps('proj')
        assert len(gaps) == 1
        assert gaps[0]['line_start'] == 10
        assert gaps[0]['line_end'] == 25


class TestCreateSubtaskMetadata:
    def test_metadata_persisted_and_readable(self, mock_db_path):
        # mock_db_path（tests/conftest.py）把 servers.BRAIN_DB 指向隔離測試 DB，
        # 並已執行 tasks.metadata 欄遷移，故不污染真實 brain.db。
        from servers.tasks import create_task, create_subtask, get_task
        epic = create_task(project='proj', description='epic', task_level='epic')
        cov = [{'file_path': 'servers/x.py', 'name': 'foo',
                'line_start': 10, 'line_end': 25}]
        tid = create_subtask(parent_id=epic, description='write tests',
                             metadata={'coverage_targets': cov})
        task = get_task(tid)
        assert task['metadata']['coverage_targets'] == cov


class TestRecipePersistsCoverageTargets:
    def test_task_metadata_has_coverage_targets(self):
        # 純函式單元：直接測「把 file_gaps 轉成 coverage_targets」的形狀
        from servers.recipes import _gaps_to_coverage_targets
        gaps = [
            {'name': 'foo', 'file_path': 'servers/x.py', 'line_start': 10, 'line_end': 25},
            {'name': 'bar', 'file_path': 'servers/x.py', 'line_start': 30, 'line_end': 40},
        ]
        targets = _gaps_to_coverage_targets(gaps)
        assert targets == [
            {'file_path': 'servers/x.py', 'name': 'foo', 'line_start': 10, 'line_end': 25},
            {'file_path': 'servers/x.py', 'name': 'bar', 'line_start': 30, 'line_end': 40},
        ]

    def test_recipe_actually_passes_metadata_to_created_task(self, mock_db_path, monkeypatch):
        # 整合測試（防假綠）：純函式對了不代表 recipe 真的把 metadata= 傳進 create_subtask。
        import servers.drift as drift
        import servers.project as project
        fake_gaps = [
            {'name': 'foo', 'file_path': 'servers/x.py', 'line_start': 10, 'line_end': 25, 'has_test': False},
            {'name': 'bar', 'file_path': 'servers/x.py', 'line_start': 30, 'line_end': 40, 'has_test': False},
            {'name': 'baz', 'file_path': 'servers/x.py', 'line_start': 45, 'line_end': 60, 'has_test': False},
        ]
        monkeypatch.setattr(drift, 'detect_coverage_gaps', lambda *a, **k: list(fake_gaps))
        monkeypatch.setattr(project, 'ensure_project',
                            lambda *a, **k: {'tech_stack': {'test_tool': 'pytest'}})

        from servers.recipes import recipe_unit_tests
        from servers.tasks import get_task
        res = recipe_unit_tests('proj', '/tmp/proj', max_tasks=1)
        task_id = res['stories'][0]['task_ids'][0]
        meta = get_task(task_id)['metadata']
        assert meta['coverage_targets'] == [
            {'file_path': 'servers/x.py', 'name': 'foo', 'line_start': 10, 'line_end': 25},
            {'file_path': 'servers/x.py', 'name': 'bar', 'line_start': 30, 'line_end': 40},
            {'file_path': 'servers/x.py', 'name': 'baz', 'line_start': 45, 'line_end': 60},
        ]


import textwrap

import pytest


def _coverage_importable():
    try:
        import importlib.util
        return importlib.util.find_spec('coverage') is not None
    except Exception:
        return False


def _write_fixture(root):
    (root / 'sample.py').write_text(textwrap.dedent('''\
        def classify(n):
            if n > 0:
                return "pos"
            if n < 0:
                return "neg"
            return "zero"

        def guarded(x):
            if x is None:        # pragma: no cover
                return "none"
            return "val"
    '''))
    (root / 'test_sample.py').write_text(textwrap.dedent('''\
        from sample import classify, guarded
        def test_pos():
            assert classify(5) == "pos"
        def test_val():
            assert guarded(1) == "val"
    '''))


@pytest.mark.skipif(not _coverage_importable(), reason="coverage not installed")
class TestMeasureBranchCoverage:
    def test_detects_missing_branches_in_target_range(self, tmp_path):
        from servers.coverage import measure_branch_coverage
        _write_fixture(tmp_path)
        targets = [{'file_path': 'sample.py', 'name': 'classify',
                    'line_start': 1, 'line_end': 6}]
        res = measure_branch_coverage(str(tmp_path), ['test_sample.py'], targets)
        assert res['tool_status'] == 'ok'
        assert res['fully_covered'] is False
        pt = res['per_target'][0]
        assert pt['name'] == 'classify'
        assert len(pt['missing_branches']) >= 1
        assert pt['n_total'] >= pt['n_covered'] + 1

    def test_out_of_range_branches_not_attributed(self, tmp_path):
        from servers.coverage import measure_branch_coverage
        _write_fixture(tmp_path)
        targets = [{'file_path': 'sample.py', 'name': 'guarded',
                    'line_start': 8, 'line_end': 11}]
        res = measure_branch_coverage(str(tmp_path), ['test_sample.py'], targets)
        assert res['tool_status'] == 'ok'
        pt = res['per_target'][0]
        for arc in pt['missing_branches']:
            assert 8 <= arc['from'] <= 11
        assert pt['missing_branches'] == []

    def test_pytest_failure_is_tests_failed(self, tmp_path):
        from servers.coverage import measure_branch_coverage
        _write_fixture(tmp_path)
        (tmp_path / 'test_broken.py').write_text('def test_x():\n    assert False\n')
        targets = [{'file_path': 'sample.py', 'name': 'classify',
                    'line_start': 1, 'line_end': 6}]
        res = measure_branch_coverage(str(tmp_path), ['test_broken.py'], targets)
        assert res['tool_status'] == 'tests_failed'
        assert res['error']

    def test_target_not_exercised_is_no_targets(self, tmp_path):
        from servers.coverage import measure_branch_coverage
        _write_fixture(tmp_path)
        (tmp_path / 'test_unrelated.py').write_text(
            'def test_math():\n    assert 1 + 1 == 2\n')
        targets = [{'file_path': 'sample.py', 'name': 'classify',
                    'line_start': 1, 'line_end': 6}]
        res = measure_branch_coverage(str(tmp_path), ['test_unrelated.py'], targets)
        assert res['tool_status'] == 'no_targets'
        assert res['error']

    def test_no_tests_collected_rc5_is_no_targets(self, tmp_path):
        from servers.coverage import measure_branch_coverage
        _write_fixture(tmp_path)
        (tmp_path / 'test_empty.py').write_text('# no tests here\nVALUE = 1\n')
        targets = [{'file_path': 'sample.py', 'name': 'classify',
                    'line_start': 1, 'line_end': 6}]
        res = measure_branch_coverage(str(tmp_path), ['test_empty.py'], targets)
        assert res['tool_status'] == 'no_targets'
        assert res['error']

    def test_no_coverage_file_left_behind(self, tmp_path):
        from servers.coverage import measure_branch_coverage
        _write_fixture(tmp_path)
        targets = [{'file_path': 'sample.py', 'name': 'classify',
                    'line_start': 1, 'line_end': 6}]
        measure_branch_coverage(str(tmp_path), ['test_sample.py'], targets)
        leftovers = [p for p in os.listdir(tmp_path) if p.startswith('.coverage')]
        assert leftovers == []


class TestMeasureUnavailableWhenNotInstalled:
    def test_missing_coverage_module_is_unavailable(self, tmp_path, monkeypatch):
        import servers.coverage as cov
        monkeypatch.setattr(cov, '_coverage_available', lambda: False)
        res = cov.measure_branch_coverage(str(tmp_path), ['test_x.py'],
                                          [{'file_path': 'x.py', 'name': 'f',
                                            'line_start': 1, 'line_end': 3}])
        assert res['tool_status'] == 'unavailable'
        assert 'coverage' in (res['error'] or '').lower()


class TestDeriveTestTargets:
    def test_parses_marker_from_executor_result(self, tmp_path):
        from servers.coverage import derive_test_targets
        (tmp_path / 'test_a.py').write_text('def test_a(): pass\n')
        (tmp_path / 'test_b.py').write_text('def test_b(): pass\n')
        result = "做完了。\nTEST_TARGETS: test_a.py, test_b.py\n其他說明。"
        got = derive_test_targets(str(tmp_path), result, [])
        assert got == ['test_a.py', 'test_b.py']

    def test_marker_paths_must_exist_and_be_tests(self, tmp_path):
        from servers.coverage import derive_test_targets
        (tmp_path / 'test_a.py').write_text('def test_a(): pass\n')
        result = "TEST_TARGETS: test_a.py, not_a_test.py, missing_test.py"
        got = derive_test_targets(str(tmp_path), result, [])
        # not_a_test.py 非測試命名；missing_test.py 不存在 → 都剔除
        assert got == ['test_a.py']

    def test_heuristic_fallback_by_stem(self, tmp_path):
        from servers.coverage import derive_test_targets
        srcdir = tmp_path / 'servers'
        srcdir.mkdir()
        (srcdir / 'memory.py').write_text('def f(): pass\n')
        tdir = tmp_path / 'tests'
        tdir.mkdir()
        (tdir / 'test_memory.py').write_text('def test_f(): pass\n')
        # 無 marker → 用 coverage_targets 的檔名 stem 找 test_memory.py
        got = derive_test_targets(str(tmp_path), "沒有 marker",
                                  [{'file_path': 'servers/memory.py', 'name': 'f',
                                    'line_start': 1, 'line_end': 1}])
        assert any(p.endswith('test_memory.py') for p in got)


class TestFormatMissingIssues:
    def test_human_readable_issue_lines(self):
        from servers.coverage import format_missing_issues
        per_target = [{
            'file_path': 'servers/x.py', 'name': 'classify',
            'line_start': 1, 'line_end': 6,
            'missing_branches': [{'from': 2, 'to': 4}, {'from': 4, 'to': 5}],
            'n_total': 4, 'n_covered': 2,
        }]
        issues = format_missing_issues(per_target)
        assert len(issues) == 1
        assert 'servers/x.py' in issues[0]
        assert 'classify' in issues[0]
        assert '2→4' in issues[0] and '4→5' in issues[0]


class TestCriticDispatchHasOriginalTaskId:
    def test_critic_dispatch_exposes_original_task_id(self, mock_db_path, tmp_path):
        # mock_db_path：隔離 DB；tmp_path：當 project_path 傳給 get_next_dispatch。
        from servers.tasks import create_task, create_subtask, update_task_status
        from servers.facade import get_next_dispatch

        epic = create_task(project='proj', description='epic', task_level='epic')
        story = create_subtask(parent_id=epic, description='story', task_level='story',
                               requires_validation=False)
        task = create_subtask(parent_id=story, description='write tests',
                              requires_validation=True)
        update_task_status(task, 'done', result='done\nTEST_TARGETS: tests/test_x.py')

        inst = get_next_dispatch(epic, 'proj', str(tmp_path))
        assert inst['subagent_type'] == 'critic'
        assert inst['original_task_id'] == task


class TestRunCoverageGate:
    def _setup_done_task(self, cov_targets, result):
        # 依賴測試方法已掛 mock_db_path fixture（隔離 DB）。
        from servers.tasks import (create_task, create_subtask,
                                   update_task_status, reserve_critic_task)
        epic = create_task(project='proj', description='epic', task_level='epic')
        story = create_subtask(parent_id=epic, description='story', task_level='story',
                               requires_validation=False)
        task = create_subtask(parent_id=story, description='write tests',
                              requires_validation=True,
                              metadata={'coverage_targets': cov_targets})
        update_task_status(task, 'done', result=result)
        critic = reserve_critic_task(task)
        return task, critic['id']

    def test_uncovered_rejects_and_writes_rejection_context(
            self, mock_db_path, tmp_path, monkeypatch):
        import servers.coverage as cov
        import servers.facade as facade
        from servers.memory import get_working_memory
        from servers.tasks import get_task
        targets = [{'file_path': 'x.py', 'name': 'f', 'line_start': 1, 'line_end': 9}]
        task, critic_id = self._setup_done_task(targets, 'done\nTEST_TARGETS: test_x.py')
        monkeypatch.setattr(cov, '_coverage_available', lambda: True)
        monkeypatch.setattr(cov, 'derive_test_targets', lambda *a, **k: ['test_x.py'])
        monkeypatch.setattr(cov, 'measure_branch_coverage', lambda *a, **k: {
            'tool_status': 'ok', 'fully_covered': False, 'error': None,
            'per_target': [{'file_path': 'x.py', 'name': 'f', 'line_start': 1, 'line_end': 9,
                            'missing_branches': [{'from': 2, 'to': 4}],
                            'n_total': 2, 'n_covered': 1}]})
        verdict = facade.run_coverage_gate(critic_id, task, 'proj', str(tmp_path))
        assert verdict['verdict'] == 'rejected'
        assert get_task(task)['status'] == 'pending'
        wm = get_working_memory(task, 'critic_suggestions')
        assert wm and '2→4' in wm

    def test_tests_failed_is_deterministic_reject(
            self, mock_db_path, tmp_path, monkeypatch):
        import servers.coverage as cov
        import servers.facade as facade
        from servers.tasks import get_task
        targets = [{'file_path': 'x.py', 'name': 'f', 'line_start': 1, 'line_end': 9}]
        task, critic_id = self._setup_done_task(targets, 'done\nTEST_TARGETS: test_x.py')
        monkeypatch.setattr(cov, '_coverage_available', lambda: True)
        monkeypatch.setattr(cov, 'derive_test_targets', lambda *a, **k: ['test_x.py'])
        monkeypatch.setattr(cov, 'measure_branch_coverage', lambda *a, **k: {
            'tool_status': 'tests_failed', 'fully_covered': False,
            'per_target': [], 'error': '測試未通過 (rc=1): assert False'})
        verdict = facade.run_coverage_gate(critic_id, task, 'proj', str(tmp_path))
        assert verdict['verdict'] == 'rejected'
        assert get_task(task)['status'] == 'pending'

    def test_no_test_targets_is_deterministic_reject(
            self, mock_db_path, tmp_path, monkeypatch):
        import servers.coverage as cov
        import servers.facade as facade
        targets = [{'file_path': 'x.py', 'name': 'f', 'line_start': 1, 'line_end': 9}]
        task, critic_id = self._setup_done_task(targets, 'done（沒有 marker）')
        monkeypatch.setattr(cov, '_coverage_available', lambda: True)
        monkeypatch.setattr(cov, 'derive_test_targets', lambda *a, **k: [])
        verdict = facade.run_coverage_gate(critic_id, task, 'proj', str(tmp_path))
        assert verdict['verdict'] == 'rejected'
        assert any('TEST_TARGETS' in i for i in verdict['issues'])

    def test_fully_covered_proceeds_to_critic(self, mock_db_path, tmp_path, monkeypatch):
        import servers.coverage as cov
        import servers.facade as facade
        targets = [{'file_path': 'x.py', 'name': 'f', 'line_start': 1, 'line_end': 9}]
        task, critic_id = self._setup_done_task(targets, 'done\nTEST_TARGETS: test_x.py')
        monkeypatch.setattr(cov, '_coverage_available', lambda: True)
        monkeypatch.setattr(cov, 'derive_test_targets', lambda *a, **k: ['test_x.py'])
        monkeypatch.setattr(cov, 'measure_branch_coverage', lambda *a, **k: {
            'tool_status': 'ok', 'fully_covered': True, 'error': None,
            'per_target': [{'file_path': 'x.py', 'name': 'f', 'line_start': 1, 'line_end': 9,
                            'missing_branches': [], 'n_total': 2, 'n_covered': 2}]})
        verdict = facade.run_coverage_gate(critic_id, task, 'proj', str(tmp_path))
        assert verdict['verdict'] == 'proceed'
        assert verdict.get('warn') in (None, '')

    def test_unavailable_proceeds_with_warning(self, mock_db_path, tmp_path, monkeypatch):
        import servers.coverage as cov
        import servers.facade as facade
        targets = [{'file_path': 'x.py', 'name': 'f', 'line_start': 1, 'line_end': 9}]
        task, critic_id = self._setup_done_task(targets, 'done\nTEST_TARGETS: test_x.py')
        monkeypatch.setattr(cov, '_coverage_available', lambda: False)
        verdict = facade.run_coverage_gate(critic_id, task, 'proj', str(tmp_path))
        assert verdict['verdict'] == 'proceed'
        assert '⚠️' in verdict['warn']

    def test_uncovered_at_retry_limit_returns_blocked(
            self, mock_db_path, tmp_path, monkeypatch):
        import servers.coverage as cov
        import servers.facade as facade
        from servers.tasks import get_task, update_task
        targets = [{'file_path': 'x.py', 'name': 'f', 'line_start': 1, 'line_end': 9}]
        task, critic_id = self._setup_done_task(targets, 'done\nTEST_TARGETS: test_x.py')
        update_task(task, rejection_count=facade.MAX_RETRIES - 1)
        monkeypatch.setattr(cov, '_coverage_available', lambda: True)
        monkeypatch.setattr(cov, 'derive_test_targets', lambda *a, **k: ['test_x.py'])
        monkeypatch.setattr(cov, 'measure_branch_coverage', lambda *a, **k: {
            'tool_status': 'ok', 'fully_covered': False, 'error': None,
            'per_target': [{'file_path': 'x.py', 'name': 'f', 'line_start': 1, 'line_end': 9,
                            'missing_branches': [{'from': 2, 'to': 4}],
                            'n_total': 2, 'n_covered': 1}]})
        verdict = facade.run_coverage_gate(critic_id, task, 'proj', str(tmp_path))
        assert verdict['verdict'] == 'blocked'
        assert get_task(task)['status'] == 'blocked'


class TestGetNextDispatchGated:
    def test_gated_rejects_then_executor_prompt_carries_missing_arc(
            self, mock_db_path, tmp_path, monkeypatch):
        import servers.coverage as cov
        import servers.facade as facade
        from servers.tasks import create_task, create_subtask, update_task_status
        epic = create_task(project='proj', description='epic', task_level='epic')
        story = create_subtask(parent_id=epic, description='story', task_level='story',
                               requires_validation=False)
        task = create_subtask(parent_id=story, description='write tests',
                              requires_validation=True,
                              metadata={'coverage_targets':
                                        [{'file_path': 'x.py', 'name': 'f',
                                          'line_start': 1, 'line_end': 9}]})
        update_task_status(task, 'done', result='done\nTEST_TARGETS: test_x.py')

        monkeypatch.setattr(cov, '_coverage_available', lambda: True)
        monkeypatch.setattr(cov, 'derive_test_targets', lambda *a, **k: ['test_x.py'])
        monkeypatch.setattr(cov, 'measure_branch_coverage', lambda *a, **k: {
            'tool_status': 'ok', 'fully_covered': False, 'error': None,
            'per_target': [{'file_path': 'x.py', 'name': 'f', 'line_start': 1, 'line_end': 9,
                            'missing_branches': [{'from': 2, 'to': 4}],
                            'n_total': 2, 'n_covered': 1}]})

        inst = facade.get_next_dispatch_gated(epic, 'proj', str(tmp_path))
        assert inst['subagent_type'] == 'executor'
        assert inst['task_id'] == task
        assert '2→4' in inst['prompt']
        assert 'x.py' in inst['prompt']

    def test_gated_returns_blocked_at_retry_limit_not_waiting(
            self, mock_db_path, tmp_path, monkeypatch):
        import servers.coverage as cov
        import servers.facade as facade
        from servers.tasks import create_task, create_subtask, update_task_status, update_task
        epic = create_task(project='proj', description='epic', task_level='epic')
        story = create_subtask(parent_id=epic, description='story', task_level='story',
                               requires_validation=False)
        task = create_subtask(parent_id=story, description='write tests',
                              requires_validation=True,
                              metadata={'coverage_targets':
                                        [{'file_path': 'x.py', 'name': 'f',
                                          'line_start': 1, 'line_end': 9}]})
        update_task_status(task, 'done', result='done\nTEST_TARGETS: test_x.py')
        update_task(task, rejection_count=facade.MAX_RETRIES - 1)
        monkeypatch.setattr(cov, '_coverage_available', lambda: True)
        monkeypatch.setattr(cov, 'derive_test_targets', lambda *a, **k: ['test_x.py'])
        monkeypatch.setattr(cov, 'measure_branch_coverage', lambda *a, **k: {
            'tool_status': 'ok', 'fully_covered': False, 'error': None,
            'per_target': [{'file_path': 'x.py', 'name': 'f', 'line_start': 1, 'line_end': 9,
                            'missing_branches': [{'from': 2, 'to': 4}],
                            'n_total': 2, 'n_covered': 1}]})
        inst = facade.get_next_dispatch_gated(epic, 'proj', str(tmp_path))
        assert inst['action'] == 'blocked'
        assert inst.get('subagent_type') != 'critic'
