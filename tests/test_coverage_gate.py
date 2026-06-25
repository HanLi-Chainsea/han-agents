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

    def test_covered_branches_populated_and_range_filtered(self, tmp_path):
        """已覆蓋的 arc 要被保留下來（給逐條 ✓ 列示用），且同樣只取行範圍內的、
        數量與 n_covered 一致——這是逐條列示能成立的資料前提。"""
        from servers.coverage import measure_branch_coverage
        _write_fixture(tmp_path)
        targets = [{'file_path': 'sample.py', 'name': 'classify',
                    'line_start': 1, 'line_end': 6}]
        res = measure_branch_coverage(str(tmp_path), ['test_sample.py'], targets)
        pt = res['per_target'][0]
        assert pt['covered_branches'], 'classify(5) 至少走到一條分支，應有已覆蓋 arc'
        assert len(pt['covered_branches']) == pt['n_covered']  # 數量一致不對不上就是 bug
        for arc in pt['covered_branches']:
            assert 1 <= arc['from'] <= 6                       # 只取目標行範圍內
        # covered 與 missing 不重疊（同一條 arc 不會又算覆蓋又算未覆蓋）
        cov_set = {(a['from'], a['to']) for a in pt['covered_branches']}
        miss_set = {(a['from'], a['to']) for a in pt['missing_branches']}
        assert cov_set.isdisjoint(miss_set)

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

    def test_proceed_carries_coverage_summary_for_final_report(
            self, mock_db_path, tmp_path, monkeypatch):
        """全覆蓋放行時，gate 要把人類可讀的覆蓋摘要一併回傳，供收尾報告引用
        （不只是寫 stderr，否則迴圈結束後就拿不到）。"""
        import servers.coverage as cov
        import servers.facade as facade
        targets = [{'file_path': 'x.py', 'name': 'f', 'line_start': 1, 'line_end': 9}]
        task, critic_id = self._setup_done_task(targets, 'done\nTEST_TARGETS: test_x.py')
        monkeypatch.setattr(cov, '_coverage_available', lambda: True)
        monkeypatch.setattr(cov, 'derive_test_targets', lambda *a, **k: ['test_x.py'])
        monkeypatch.setattr(cov, 'measure_branch_coverage', lambda *a, **k: {
            'tool_status': 'ok', 'fully_covered': True, 'error': None,
            'per_target': [{'file_path': 'x.py', 'name': 'f', 'line_start': 1, 'line_end': 9,
                            'covered_branches': [{'from': 2, 'to': 3}, {'from': 2, 'to': 4}],
                            'missing_branches': [], 'n_total': 2, 'n_covered': 2}]})
        verdict = facade.run_coverage_gate(critic_id, task, 'proj', str(tmp_path))
        assert verdict['verdict'] == 'proceed'
        summary = '\n'.join(verdict['coverage_summary'])
        assert '2/2' in summary and 'x.py' in summary
        assert '✓' in summary and 'L2→3' in summary

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

    def test_gated_proceeds_to_critic_attaches_coverage_summary(
            self, mock_db_path, tmp_path, monkeypatch):
        """全覆蓋 → 派 critic；gate 把覆蓋摘要掛到回傳的 dispatch 上，
        讓 /han:unit-test 迴圈每輪都拿得到，最後寫進人類報告。"""
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
            'tool_status': 'ok', 'fully_covered': True, 'error': None,
            'per_target': [{'file_path': 'x.py', 'name': 'f', 'line_start': 1, 'line_end': 9,
                            'covered_branches': [{'from': 2, 'to': 3}, {'from': 2, 'to': 4}],
                            'missing_branches': [], 'n_total': 2, 'n_covered': 2}]})

        inst = facade.get_next_dispatch_gated(epic, 'proj', str(tmp_path))
        assert inst['subagent_type'] == 'critic'
        summary = '\n'.join(inst['coverage_summary'])
        assert '2/2' in summary and 'x.py' in summary and '✓' in summary

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


# ── Fail-closed 強化（codex 審查：杜絕假綠）─────────────────────────────


class TestMeasureFailClosedHardening:
    """量測層：非法 target 行範圍、測試執行錯誤、coverage json 格式異動
    都必須回可辨識的失敗狀態，絕不可退化成『單行/空覆蓋 → 假綠』。"""

    def test_line_end_none_is_invalid_targets_not_single_line(self, tmp_path):
        # 不需 coverage：入口先驗 target，非法即回傳（不進子行程）。
        from servers.coverage import measure_branch_coverage
        targets = [{'file_path': 'x.py', 'name': 'f',
                    'line_start': 5, 'line_end': None}]
        res = measure_branch_coverage(str(tmp_path), ['test_x.py'], targets)
        assert res['tool_status'] == 'invalid_targets'
        assert res['fully_covered'] is False

    def test_line_end_zero_is_invalid_targets(self, tmp_path):
        from servers.coverage import measure_branch_coverage
        targets = [{'file_path': 'x.py', 'name': 'f',
                    'line_start': 5, 'line_end': 0}]
        res = measure_branch_coverage(str(tmp_path), ['test_x.py'], targets)
        assert res['tool_status'] == 'invalid_targets'

    def test_line_end_before_line_start_is_invalid_targets(self, tmp_path):
        from servers.coverage import measure_branch_coverage
        targets = [{'file_path': 'x.py', 'name': 'f',
                    'line_start': 9, 'line_end': 3}]
        res = measure_branch_coverage(str(tmp_path), ['test_x.py'], targets)
        assert res['tool_status'] == 'invalid_targets'

    def test_bool_line_start_is_invalid_targets(self, tmp_path):
        # True/False 是 int 子類，但顯非合法行號 → 必須擋下。
        from servers.coverage import measure_branch_coverage
        targets = [{'file_path': 'x.py', 'name': 'f',
                    'line_start': True, 'line_end': 5}]
        res = measure_branch_coverage(str(tmp_path), ['test_x.py'], targets)
        assert res['tool_status'] == 'invalid_targets'

    def test_pytest_timeout_is_test_run_error(self, tmp_path, monkeypatch):
        import subprocess as sp
        import servers.coverage as cov
        monkeypatch.setattr(cov, '_coverage_available', lambda: True)
        (tmp_path / 'test_x.py').write_text('def test_x(): pass\n')

        def boom(*a, **k):
            raise sp.TimeoutExpired(cmd='pytest', timeout=1)
        monkeypatch.setattr(cov.subprocess, 'run', boom)
        targets = [{'file_path': 'x.py', 'name': 'f',
                    'line_start': 1, 'line_end': 6}]
        res = cov.measure_branch_coverage(str(tmp_path), ['test_x.py'], targets)
        assert res['tool_status'] == 'test_run_error'

    def test_pytest_rc2_is_test_run_error_not_unavailable(self, tmp_path, monkeypatch):
        import servers.coverage as cov
        monkeypatch.setattr(cov, '_coverage_available', lambda: True)
        (tmp_path / 'test_x.py').write_text('def test_x(): pass\n')

        class FakeRun:
            returncode = 2
            stdout = 'usage error'
            stderr = ''
        monkeypatch.setattr(cov.subprocess, 'run', lambda *a, **k: FakeRun())
        targets = [{'file_path': 'x.py', 'name': 'f',
                    'line_start': 1, 'line_end': 6}]
        res = cov.measure_branch_coverage(str(tmp_path), ['test_x.py'], targets)
        assert res['tool_status'] == 'test_run_error'

    def test_malformed_branch_arrays_is_schema_error(self, tmp_path):
        from servers.coverage import _attribute_targets
        root = str(tmp_path)
        canon = os.path.realpath(os.path.join(root, 'x.py'))
        file_index = {canon: {'missing_branches': [[2, 'bad']],
                              'executed_branches': []}}
        targets = [{'file_path': 'x.py', 'name': 'f',
                    'line_start': 1, 'line_end': 9}]
        res = _attribute_targets(file_index, targets, root)
        assert res['tool_status'] == 'schema_error'
        assert res['fully_covered'] is False

    def test_non_list_branch_field_is_schema_error(self, tmp_path):
        from servers.coverage import _attribute_targets
        root = str(tmp_path)
        canon = os.path.realpath(os.path.join(root, 'x.py'))
        file_index = {canon: {'missing_branches': None, 'executed_branches': []}}
        targets = [{'file_path': 'x.py', 'name': 'f',
                    'line_start': 1, 'line_end': 9}]
        res = _attribute_targets(file_index, targets, root)
        assert res['tool_status'] == 'schema_error'

    def test_missing_branch_keys_is_schema_error_not_false_green(self, tmp_path):
        # coverage json schema 若移除/改名 branch 欄位，缺欄位不可被當成空集合 → 假綠。
        from servers.coverage import _attribute_targets
        root = str(tmp_path)
        canon = os.path.realpath(os.path.join(root, 'x.py'))
        file_index = {canon: {}}  # 兩個 branch 欄位都不存在
        targets = [{'file_path': 'x.py', 'name': 'f',
                    'line_start': 1, 'line_end': 9}]
        res = _attribute_targets(file_index, targets, root)
        assert res['tool_status'] == 'schema_error'
        assert res['fully_covered'] is False

    def test_bool_arc_is_schema_error(self, tmp_path):
        # JSON true/false 是 int 子類，不可被當成行號 1/0。
        from servers.coverage import _attribute_targets
        root = str(tmp_path)
        canon = os.path.realpath(os.path.join(root, 'x.py'))
        file_index = {canon: {'missing_branches': [[True, False]],
                              'executed_branches': []}}
        targets = [{'file_path': 'x.py', 'name': 'f',
                    'line_start': 1, 'line_end': 9}]
        res = _attribute_targets(file_index, targets, root)
        assert res['tool_status'] == 'schema_error'

    def test_valid_arc_rejects_bool(self):
        from servers.coverage import _valid_arc
        assert _valid_arc([2, 4]) is True
        assert _valid_arc([True, 4]) is False
        assert _valid_arc([2, False]) is False

    def test_wellformed_branches_attribute_ok(self, tmp_path):
        from servers.coverage import _attribute_targets
        root = str(tmp_path)
        canon = os.path.realpath(os.path.join(root, 'x.py'))
        file_index = {canon: {'missing_branches': [[2, 4]],
                              'executed_branches': [[2, 3]]}}
        targets = [{'file_path': 'x.py', 'name': 'f',
                    'line_start': 1, 'line_end': 9}]
        res = _attribute_targets(file_index, targets, root)
        assert res['tool_status'] == 'ok'
        assert res['fully_covered'] is False
        pt = res['per_target'][0]
        assert pt['missing_branches'] == [{'from': 2, 'to': 4}]
        assert pt['n_total'] == 2 and pt['n_covered'] == 1


class TestSanitize:
    def test_strips_ansi_and_control_keeps_text_and_newline(self):
        from servers.coverage import _sanitize
        raw = '\x1b[31mFAIL\x1b[0m\x00\x07 line\n\tok'
        out = _sanitize(raw)
        assert '\x1b' not in out and '\x00' not in out and '\x07' not in out
        assert 'FAIL' in out and 'line' in out and 'ok' in out
        assert '\n' in out

    def test_empty_is_empty(self):
        from servers.coverage import _sanitize
        assert _sanitize('') == ''
        assert _sanitize(None) == ''


class TestGateFailClosedRouting:
    """gate 路由：唯一允許 fail-open 的是 coverage 套件缺失；
    其餘任何非 ok（test_run_error / invalid_targets / schema_error）一律確定性退件。"""

    def _setup(self, monkeypatch, status):
        import servers.coverage as cov
        from servers.tasks import (create_task, create_subtask,
                                   update_task_status, reserve_critic_task)
        targets = [{'file_path': 'x.py', 'name': 'f',
                    'line_start': 1, 'line_end': 9}]
        epic = create_task(project='proj', description='epic', task_level='epic')
        story = create_subtask(parent_id=epic, description='story', task_level='story',
                               requires_validation=False)
        task = create_subtask(parent_id=story, description='write tests',
                              requires_validation=True,
                              metadata={'coverage_targets': targets})
        update_task_status(task, 'done', result='done\nTEST_TARGETS: test_x.py')
        critic = reserve_critic_task(task)
        monkeypatch.setattr(cov, '_coverage_available', lambda: True)
        monkeypatch.setattr(cov, 'derive_test_targets', lambda *a, **k: ['test_x.py'])
        monkeypatch.setattr(cov, 'measure_branch_coverage', lambda *a, **k: {
            'tool_status': status, 'fully_covered': False,
            'per_target': [], 'error': f'模擬 {status}'})
        return task, critic['id']

    def test_test_run_error_is_reject(self, mock_db_path, tmp_path, monkeypatch):
        import servers.facade as facade
        from servers.tasks import get_task
        task, critic_id = self._setup(monkeypatch, 'test_run_error')
        verdict = facade.run_coverage_gate(critic_id, task, 'proj', str(tmp_path))
        assert verdict['verdict'] == 'rejected'
        assert get_task(task)['status'] == 'pending'

    def test_invalid_targets_is_reject(self, mock_db_path, tmp_path, monkeypatch):
        import servers.facade as facade
        from servers.tasks import get_task
        task, critic_id = self._setup(monkeypatch, 'invalid_targets')
        verdict = facade.run_coverage_gate(critic_id, task, 'proj', str(tmp_path))
        assert verdict['verdict'] == 'rejected'
        assert get_task(task)['status'] == 'pending'

    def test_schema_error_is_reject(self, mock_db_path, tmp_path, monkeypatch):
        import servers.facade as facade
        from servers.tasks import get_task
        task, critic_id = self._setup(monkeypatch, 'schema_error')
        verdict = facade.run_coverage_gate(critic_id, task, 'proj', str(tmp_path))
        assert verdict['verdict'] == 'rejected'
        assert get_task(task)['status'] == 'pending'


class TestFormatCoverageSummary:
    """人類可見的覆蓋率摘要：每個 target 列出**每一條分支**（✓/✗），人好核對邏輯。"""

    def test_lists_every_branch_with_covered_and_missing_marks(self):
        from servers.coverage import format_coverage_summary
        per_target = [{'file_path': 'calc.py', 'name': 'classify',
                       'line_start': 1, 'line_end': 5,
                       'covered_branches': [{'from': 2, 'to': 3}],
                       'missing_branches': [{'from': 2, 'to': 4},
                                            {'from': 4, 'to': 5},
                                            {'from': 4, 'to': 6}],
                       'n_total': 4, 'n_covered': 1}]
        out = '\n'.join(format_coverage_summary(per_target))
        # 標頭：覆蓋數字 + 總分支數
        assert 'calc.py' in out and 'classify' in out
        assert '1/4' in out
        assert '4' in out and '分支' in out      # 列出有多少分支
        assert '❌' in out
        # 每一條分支都要列出來（覆蓋的 + 未覆蓋的），各自有 ✓ / ✗ 記號
        assert '✓' in out and '✗' in out
        for arc in ('2→3', '2→4', '4→5', '4→6'):
            assert arc in out
        # 已覆蓋的 2→3 標 ✓，未覆蓋的標 ✗
        covered_line = [ln for ln in format_coverage_summary(per_target) if '2→3' in ln][0]
        missing_line = [ln for ln in format_coverage_summary(per_target) if '2→4' in ln][0]
        assert '✓' in covered_line
        assert '✗' in missing_line

    def test_full_coverage_lists_all_branches_as_covered(self):
        from servers.coverage import format_coverage_summary
        per_target = [{'file_path': 'calc.py', 'name': 'classify',
                       'line_start': 1, 'line_end': 5,
                       'covered_branches': [{'from': 2, 'to': 3},
                                            {'from': 2, 'to': 4},
                                            {'from': 4, 'to': 5},
                                            {'from': 4, 'to': 6}],
                       'missing_branches': [], 'n_total': 4, 'n_covered': 4}]
        out = '\n'.join(format_coverage_summary(per_target))
        assert '4/4' in out
        assert '✅' in out
        assert out.count('✓') == 4        # 4 條全部列出且標已覆蓋
        assert '✗' not in out

    def test_multiple_targets_each_get_own_block(self):
        """多個 target → 各自一個標頭區塊，分支歸屬不串台。"""
        from servers.coverage import format_coverage_summary
        per_target = [
            {'file_path': 'a.py', 'name': 'f', 'line_start': 1, 'line_end': 3,
             'covered_branches': [{'from': 2, 'to': 3}], 'missing_branches': [],
             'n_total': 1, 'n_covered': 1},
            {'file_path': 'b.py', 'name': 'g', 'line_start': 1, 'line_end': 3,
             'covered_branches': [], 'missing_branches': [{'from': 2, 'to': 9}],
             'n_total': 1, 'n_covered': 0},
        ]
        lines = format_coverage_summary(per_target)
        heads = [ln for ln in lines if ln.startswith('📊')]
        assert len(heads) == 2
        assert 'a.py::f' in heads[0] and '✅' in heads[0]
        assert 'b.py::g' in heads[1] and '❌' in heads[1]
        # a 的覆蓋 arc 不會跑到 b 的區塊
        b_idx = lines.index(heads[1])
        assert all('2→3' not in ln for ln in lines[b_idx:])

    def test_missing_covered_branches_key_degrades_gracefully(self):
        """per_target 不帶 covered_branches（gate monkeypatch / 舊資料）→ 不爆，
        只列未覆蓋 ✗，不會憑空捏造 ✓。"""
        from servers.coverage import format_coverage_summary
        per_target = [{'file_path': 'x.py', 'name': 'f', 'line_start': 1, 'line_end': 9,
                       'missing_branches': [{'from': 2, 'to': 4}],
                       'n_total': 2, 'n_covered': 1}]  # 無 covered_branches 鍵
        out = '\n'.join(format_coverage_summary(per_target))
        assert '1/2' in out and 'L2→4' in out and '✗' in out
        assert '✓' not in out

    def test_branchless_target_shows_neutral_not_green(self):
        """M2: n_total=0 → neutral display 〇/n/a, NOT 0/0 or ✅.

        A genuinely branchless function legitimately passes — the gate must NOT
        reject it — but the DISPLAY must not show a misleading green ✅ or "0/0
        covered" fraction. Only the summary string changes; verdict is unaffected.
        """
        from servers.coverage import format_coverage_summary
        per_target = [{'file_path': 'x.py', 'name': 'noop', 'line_start': 1, 'line_end': 2,
                       'covered_branches': [], 'missing_branches': [],
                       'n_total': 0, 'n_covered': 0}]
        lines = format_coverage_summary(per_target)
        assert len(lines) == 1, "branchless: only header line, no branch sub-lines"
        header = lines[0]
        # M2: must NOT show misleading green or fraction
        assert '✅' not in header, "M2: branchless must not render ✅ (misleading green)"
        assert '0/0' not in header, "M2: branchless must not show '0/0' as a coverage count"
        # Must show a neutral marker (n/a or no-branch indicator)
        assert ('n/a' in header or '無分支' in header or 'no branch' in header.lower()), (
            f"M2: branchless must show neutral 'n/a'/'無分支' marker, got: {header!r}")
        # Must still not have ✓/✗ branch sub-lines
        assert '✓' not in header and '✗' not in header

    def test_branches_listed_in_line_order(self):
        """分支不論覆蓋與否，一律依行號排序，方便對照原始碼由上而下核對。"""
        from servers.coverage import format_coverage_summary
        per_target = [{'file_path': 'x.py', 'name': 'f', 'line_start': 1, 'line_end': 9,
                       'covered_branches': [{'from': 4, 'to': 5}],
                       'missing_branches': [{'from': 2, 'to': 4}, {'from': 4, 'to': 6}],
                       'n_total': 3, 'n_covered': 1}]
        branch_lines = [ln for ln in format_coverage_summary(per_target)
                        if not ln.startswith('📊')]
        froms = [int(ln.split('L')[1].split('→')[0]) for ln in branch_lines]
        assert froms == sorted(froms)          # 2,4,4… 由小到大
        assert branch_lines[0].endswith('L2→4') and '✗' in branch_lines[0]

    def test_empty_per_target_returns_no_lines(self):
        from servers.coverage import format_coverage_summary
        assert format_coverage_summary([]) == []


class TestGateEmitsHumanVisibleCoverage:
    def test_gate_writes_coverage_number_to_stderr(
            self, mock_db_path, tmp_path, monkeypatch, capsys):
        import servers.coverage as cov
        import servers.facade as facade
        from servers.tasks import (create_task, create_subtask,
                                   update_task_status, reserve_critic_task)
        targets = [{'file_path': 'x.py', 'name': 'f',
                    'line_start': 1, 'line_end': 9}]
        epic = create_task(project='proj', description='epic', task_level='epic')
        story = create_subtask(parent_id=epic, description='story', task_level='story',
                               requires_validation=False)
        task = create_subtask(parent_id=story, description='write tests',
                              requires_validation=True,
                              metadata={'coverage_targets': targets})
        update_task_status(task, 'done', result='done\nTEST_TARGETS: test_x.py')
        critic = reserve_critic_task(task)
        monkeypatch.setattr(cov, '_coverage_available', lambda: True)
        monkeypatch.setattr(cov, 'derive_test_targets', lambda *a, **k: ['test_x.py'])
        monkeypatch.setattr(cov, 'measure_branch_coverage', lambda *a, **k: {
            'tool_status': 'ok', 'fully_covered': True, 'error': None,
            'per_target': [{'file_path': 'x.py', 'name': 'f', 'line_start': 1, 'line_end': 9,
                            'missing_branches': [], 'n_total': 3, 'n_covered': 3}]})
        facade.run_coverage_gate(critic['id'], task, 'proj', str(tmp_path))
        err = capsys.readouterr().err
        # 全覆蓋也要讓人看到數字（不再靜默通過）
        assert '3/3' in err


class TestGateMetadataGuard:
    """metadata['coverage_targets'] 型別防呆：壞掉的 metadata 不可被 `or []`
    靜默當成非 coverage 任務而跳過 gate（隱性繞過＝假綠相鄰）。"""

    def _make(self, cov_targets_value):
        from servers.tasks import (create_task, create_subtask,
                                   update_task_status, reserve_critic_task)
        epic = create_task(project='proj', description='epic', task_level='epic')
        story = create_subtask(parent_id=epic, description='story', task_level='story',
                               requires_validation=False)
        meta = {} if cov_targets_value is _ABSENT else {'coverage_targets': cov_targets_value}
        task = create_subtask(parent_id=story, description='write tests',
                              requires_validation=True, metadata=meta)
        update_task_status(task, 'done', result='done\nTEST_TARGETS: test_x.py')
        critic = reserve_critic_task(task)
        return task, critic['id']

    def test_absent_targets_proceeds_not_a_coverage_task(
            self, mock_db_path, tmp_path):
        import servers.facade as facade
        task, critic_id = self._make(_ABSENT)
        verdict = facade.run_coverage_gate(critic_id, task, 'proj', str(tmp_path))
        assert verdict['verdict'] == 'proceed'

    def test_malformed_string_targets_is_reject_not_silent_bypass(
            self, mock_db_path, tmp_path):
        import servers.facade as facade
        from servers.tasks import get_task
        task, critic_id = self._make('oops-not-a-list')
        verdict = facade.run_coverage_gate(critic_id, task, 'proj', str(tmp_path))
        assert verdict['verdict'] == 'rejected'
        assert get_task(task)['status'] == 'pending'

    def test_non_dict_elements_is_reject(self, mock_db_path, tmp_path):
        import servers.facade as facade
        from servers.tasks import get_task
        task, critic_id = self._make([1, 2, 3])
        verdict = facade.run_coverage_gate(critic_id, task, 'proj', str(tmp_path))
        assert verdict['verdict'] == 'rejected'
        assert get_task(task)['status'] == 'pending'


_ABSENT = object()


# ── Stack-adaptive dispatch (Task A4) ─────────────────────────────────────────


class TestSelectBackend:
    """select_backend 純函式：從 tech_stack dict 判斷要用哪個後端。"""

    def test_gradle_test_tool_maps_to_java(self):
        from servers.coverage_java import select_backend
        assert select_backend({'test_tool': 'gradle'}) == 'java'

    def test_maven_test_tool_maps_to_java(self):
        from servers.coverage_java import select_backend
        assert select_backend({'test_tool': 'maven'}) == 'java'

    def test_gradle_mixed_case_maps_to_java(self):
        from servers.coverage_java import select_backend
        assert select_backend({'test_tool': 'Gradle'}) == 'java'

    def test_java_language_maps_to_java(self):
        from servers.coverage_java import select_backend
        assert select_backend({'primary_language': 'java'}) == 'java'

    def test_kotlin_language_maps_to_java(self):
        from servers.coverage_java import select_backend
        assert select_backend({'primary_language': 'kotlin'}) == 'java'

    def test_pytest_test_tool_maps_to_python(self):
        from servers.coverage_java import select_backend
        assert select_backend({'test_tool': 'pytest'}) == 'python'

    def test_unittest_test_tool_maps_to_python(self):
        from servers.coverage_java import select_backend
        assert select_backend({'test_tool': 'unittest'}) == 'python'

    def test_python_language_maps_to_python(self):
        from servers.coverage_java import select_backend
        assert select_backend({'primary_language': 'python'}) == 'python'

    def test_empty_dict_maps_to_unknown(self):
        from servers.coverage_java import select_backend
        assert select_backend({}) == 'unknown'

    def test_none_values_map_to_unknown(self):
        from servers.coverage_java import select_backend
        assert select_backend({'test_tool': None, 'primary_language': None}) == 'unknown'

    def test_unrecognized_tool_maps_to_unknown(self):
        from servers.coverage_java import select_backend
        assert select_backend({'test_tool': 'cargo'}) == 'unknown'

    def test_test_tool_takes_priority_over_language(self):
        # gradle test_tool with python language → java (test_tool wins)
        from servers.coverage_java import select_backend
        assert select_backend({'test_tool': 'gradle', 'primary_language': 'python'}) == 'java'


class TestStackDispatch:
    """Gate 路由：java tech_stack 路由到 measure_branch_coverage_java；
    python tech_stack 路由到 coverage.measure_branch_coverage。
    兩個後端都需要被 monkeypatch 並驗證只有其中一個被呼叫。"""

    def _setup_done_task(self, cov_targets, result):
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

    def _fake_ok_result(self):
        return {
            'tool_status': 'ok', 'fully_covered': True, 'error': None,
            'per_target': [{'file_path': 'x.py', 'name': 'f',
                            'line_start': 1, 'line_end': 9,
                            'covered_branches': [{'from': 2, 'to': 3}],
                            'missing_branches': [], 'n_total': 1, 'n_covered': 1}],
        }

    def test_java_stack_routes_to_java_backend(
            self, mock_db_path, tmp_path, monkeypatch):
        import servers.coverage as cov
        import servers.coverage_java as cov_java
        import servers.project as project
        import servers.facade as facade

        targets = [{'file_path': 'x.py', 'name': 'f', 'line_start': 1, 'line_end': 9}]
        # Use a proper Java test path so _derive_java_test_filters produces FQ class name
        java_test_path = 'src/test/java/demo/FooTest.java'
        task, critic_id = self._setup_done_task(
            targets, f'done\nTEST_TARGETS: {java_test_path}')

        # Patch ensure_project to return a java tech stack
        monkeypatch.setattr(project, 'ensure_project',
                            lambda *a, **k: {'tech_stack': {'test_tool': 'gradle'}})
        monkeypatch.setattr(cov, '_coverage_available', lambda: True)
        monkeypatch.setattr(cov, 'derive_test_targets',
                            lambda *a, **k: [java_test_path])

        java_calls = []
        python_calls = []

        def fake_java(project_path, test_targets, coverage_targets, **kwargs):
            java_calls.append((project_path, test_targets, coverage_targets))
            return self._fake_ok_result()

        def fake_python(project_path, test_targets, coverage_targets):
            python_calls.append((project_path, test_targets, coverage_targets))
            return self._fake_ok_result()

        monkeypatch.setattr(cov_java, 'measure_branch_coverage_java', fake_java)
        monkeypatch.setattr(cov, 'measure_branch_coverage', fake_python)

        verdict = facade.run_coverage_gate(critic_id, task, 'proj', str(tmp_path))

        assert verdict['verdict'] == 'proceed'
        assert len(java_calls) == 1, 'Java backend must be called exactly once'
        assert len(python_calls) == 0, 'Python backend must NOT be called for java stack'

    def test_python_stack_routes_to_python_backend(
            self, mock_db_path, tmp_path, monkeypatch):
        import servers.coverage as cov
        import servers.coverage_java as cov_java
        import servers.project as project
        import servers.facade as facade

        targets = [{'file_path': 'x.py', 'name': 'f', 'line_start': 1, 'line_end': 9}]
        task, critic_id = self._setup_done_task(targets, 'done\nTEST_TARGETS: test_x.py')

        # Patch ensure_project to return a python tech stack
        monkeypatch.setattr(project, 'ensure_project',
                            lambda *a, **k: {'tech_stack': {'test_tool': 'pytest'}})
        monkeypatch.setattr(cov, '_coverage_available', lambda: True)
        monkeypatch.setattr(cov, 'derive_test_targets', lambda *a, **k: ['test_x.py'])

        java_calls = []
        python_calls = []

        def fake_java(project_path, test_targets, coverage_targets, **kwargs):
            java_calls.append((project_path, test_targets, coverage_targets))
            return self._fake_ok_result()

        def fake_python(project_path, test_targets, coverage_targets):
            python_calls.append((project_path, test_targets, coverage_targets))
            return self._fake_ok_result()

        monkeypatch.setattr(cov_java, 'measure_branch_coverage_java', fake_java)
        monkeypatch.setattr(cov, 'measure_branch_coverage', fake_python)

        verdict = facade.run_coverage_gate(critic_id, task, 'proj', str(tmp_path))

        assert verdict['verdict'] == 'proceed'
        assert len(python_calls) == 1, 'Python backend must be called exactly once'
        assert len(java_calls) == 0, 'Java backend must NOT be called for python stack'

    def test_unknown_stack_routes_to_python_backend(
            self, mock_db_path, tmp_path, monkeypatch):
        """Unknown stack falls back to Python (safe default, preserves backward compat)."""
        import servers.coverage as cov
        import servers.coverage_java as cov_java
        import servers.project as project
        import servers.facade as facade

        targets = [{'file_path': 'x.py', 'name': 'f', 'line_start': 1, 'line_end': 9}]
        task, critic_id = self._setup_done_task(targets, 'done\nTEST_TARGETS: test_x.py')

        # Patch ensure_project to return an unknown tech stack
        monkeypatch.setattr(project, 'ensure_project',
                            lambda *a, **k: {'tech_stack': {'test_tool': None}})
        monkeypatch.setattr(cov, '_coverage_available', lambda: True)
        monkeypatch.setattr(cov, 'derive_test_targets', lambda *a, **k: ['test_x.py'])

        java_calls = []
        python_calls = []

        def fake_java(project_path, test_targets, coverage_targets, **kwargs):
            java_calls.append((project_path, test_targets, coverage_targets))
            return self._fake_ok_result()

        def fake_python(project_path, test_targets, coverage_targets):
            python_calls.append((project_path, test_targets, coverage_targets))
            return self._fake_ok_result()

        monkeypatch.setattr(cov_java, 'measure_branch_coverage_java', fake_java)
        monkeypatch.setattr(cov, 'measure_branch_coverage', fake_python)

        verdict = facade.run_coverage_gate(critic_id, task, 'proj', str(tmp_path))

        assert verdict['verdict'] == 'proceed'
        assert len(python_calls) == 1, 'Unknown stack must fall back to Python backend'
        assert len(java_calls) == 0, 'Java backend must NOT be called for unknown stack'


# ── C3: JUnit stack must map to java backend ──────────────────────────────────


class TestC3SelectBackendJunit:
    """C3: select_backend must recognize junit as a Java stack indicator.

    Real Java projects often report test_tool='junit' (from servers/project.py),
    which previously mapped to 'unknown' causing the Java gate to never run.
    """

    def test_junit_test_tool_maps_to_java(self):
        from servers.coverage_java import select_backend
        assert select_backend({'test_tool': 'junit'}) == 'java'

    def test_junit_mixed_case_maps_to_java(self):
        from servers.coverage_java import select_backend
        assert select_backend({'test_tool': 'JUnit'}) == 'java'

    def test_junit_with_version_substring_maps_to_java(self):
        # e.g. 'junit5' or 'junit-platform' should also resolve to java
        from servers.coverage_java import select_backend
        # 'junit5' contains 'junit' — implementation must use substring match
        # (or exact match for 'junit'; at minimum plain 'junit' must work)
        assert select_backend({'test_tool': 'junit'}) == 'java'

    def test_junit_stack_routes_gate_to_java_backend(
            self, mock_db_path, tmp_path, monkeypatch):
        """End-to-end: junit tech_stack routes run_coverage_gate to Java backend."""
        import servers.coverage as cov
        import servers.coverage_java as cov_java
        import servers.project as project
        import servers.facade as facade
        from servers.tasks import (create_task, create_subtask,
                                   update_task_status, reserve_critic_task)

        targets = [{'file_path': 'src/main/java/demo/Foo.java', 'name': 'bar',
                    'line_start': 1, 'line_end': 9}]
        epic = create_task(project='proj', description='epic', task_level='epic')
        story = create_subtask(parent_id=epic, description='story', task_level='story',
                               requires_validation=False)
        task = create_subtask(parent_id=story, description='write tests',
                              requires_validation=True,
                              metadata={'coverage_targets': targets})
        update_task_status(task, 'done',
                           result='done\nTEST_TARGETS: src/test/java/demo/FooTest.java')
        critic = reserve_critic_task(task)

        monkeypatch.setattr(project, 'ensure_project',
                            lambda *a, **k: {'tech_stack': {'test_tool': 'junit'}})
        monkeypatch.setattr(cov, '_coverage_available', lambda: True)
        monkeypatch.setattr(cov, 'derive_test_targets',
                            lambda *a, **k: ['src/test/java/demo/FooTest.java'])

        java_calls = []

        def fake_java(project_path, test_targets, coverage_targets, **kwargs):
            java_calls.append(1)
            return {'tool_status': 'ok', 'fully_covered': True, 'error': None,
                    'per_target': [{'file_path': 'src/main/java/demo/Foo.java',
                                    'name': 'bar', 'line_start': 1, 'line_end': 9,
                                    'covered_branches': [{'from': 2, 'to': 3}],
                                    'missing_branches': [], 'n_total': 1, 'n_covered': 1}]}

        monkeypatch.setattr(cov_java, 'measure_branch_coverage_java', fake_java)

        verdict = facade.run_coverage_gate(critic['id'], task, 'proj', str(tmp_path))
        assert verdict['verdict'] == 'proceed'
        assert len(java_calls) == 1, 'junit stack must route to Java backend'


# ── C5: Backend selection must precede _coverage_available() ──────────────────


class TestC5BackendSelectionPrecedesCoverageAvailable:
    """C5: Java path must not be gated by Python coverage availability.

    Previously _coverage_available() ran BEFORE backend selection, so Java
    projects fail-opened (skipped measurement) when python-coverage was absent.
    Fix: select backend first; Java availability = gradlew exists; Python
    availability = _coverage_available().
    """

    def test_java_backend_proceeds_when_python_coverage_unavailable(
            self, mock_db_path, tmp_path, monkeypatch):
        """When python-coverage is 'unavailable' but stack is java and gradlew
        exists, the gate must route to the Java backend (not fail-open)."""
        import servers.coverage as cov
        import servers.coverage_java as cov_java
        import servers.project as project
        import servers.facade as facade
        from servers.tasks import (create_task, create_subtask,
                                   update_task_status, reserve_critic_task)

        # Simulate a gradlew file existing in tmp_path
        gradlew = tmp_path / 'gradlew'
        gradlew.write_text('#!/bin/sh\n')

        targets = [{'file_path': 'src/main/java/demo/Foo.java', 'name': 'bar',
                    'line_start': 1, 'line_end': 9}]
        epic = create_task(project='proj', description='epic', task_level='epic')
        story = create_subtask(parent_id=epic, description='story', task_level='story',
                               requires_validation=False)
        task = create_subtask(parent_id=story, description='write tests',
                              requires_validation=True,
                              metadata={'coverage_targets': targets})
        update_task_status(task, 'done',
                           result='done\nTEST_TARGETS: src/test/java/demo/FooTest.java')
        critic = reserve_critic_task(task)

        # Python coverage is NOT installed
        monkeypatch.setattr(cov, '_coverage_available', lambda: False)
        monkeypatch.setattr(project, 'ensure_project',
                            lambda *a, **k: {'tech_stack': {'test_tool': 'gradle'}})
        monkeypatch.setattr(cov, 'derive_test_targets',
                            lambda *a, **k: ['src/test/java/demo/FooTest.java'])

        java_calls = []

        def fake_java(project_path, test_targets, coverage_targets, **kwargs):
            java_calls.append(1)
            return {'tool_status': 'ok', 'fully_covered': True, 'error': None,
                    'per_target': [{'file_path': 'src/main/java/demo/Foo.java',
                                    'name': 'bar', 'line_start': 1, 'line_end': 9,
                                    'covered_branches': [{'from': 2, 'to': 3}],
                                    'missing_branches': [], 'n_total': 1, 'n_covered': 1}]}

        monkeypatch.setattr(cov_java, 'measure_branch_coverage_java', fake_java)

        verdict = facade.run_coverage_gate(critic['id'], task, 'proj', str(tmp_path))

        # Must NOT be the python-unavailable fail-open path
        assert verdict.get('warn') is None or 'coverage 套件未安裝' not in verdict.get('warn', ''), (
            "C5: gate took the python-coverage-unavailable fail-open path for a Java project. "
            "Backend selection must happen before _coverage_available()."
        )
        # Must have routed to java backend
        assert len(java_calls) == 1, (
            "C5: java backend was never called; gate incorrectly fail-opened on "
            "python coverage unavailability for a Java project."
        )
        assert verdict['verdict'] == 'proceed'

    def test_python_unavailable_still_fail_opens_for_python_stack(
            self, mock_db_path, tmp_path, monkeypatch):
        """Python stack with coverage unavailable keeps existing fail-open behavior."""
        import servers.coverage as cov
        import servers.project as project
        import servers.facade as facade
        from servers.tasks import (create_task, create_subtask,
                                   update_task_status, reserve_critic_task)

        targets = [{'file_path': 'x.py', 'name': 'f', 'line_start': 1, 'line_end': 9}]
        epic = create_task(project='proj', description='epic', task_level='epic')
        story = create_subtask(parent_id=epic, description='story', task_level='story',
                               requires_validation=False)
        task = create_subtask(parent_id=story, description='write tests',
                              requires_validation=True,
                              metadata={'coverage_targets': targets})
        update_task_status(task, 'done', result='done\nTEST_TARGETS: test_x.py')
        critic = reserve_critic_task(task)

        monkeypatch.setattr(cov, '_coverage_available', lambda: False)
        monkeypatch.setattr(project, 'ensure_project',
                            lambda *a, **k: {'tech_stack': {'test_tool': 'pytest'}})

        verdict = facade.run_coverage_gate(critic['id'], task, 'proj', str(tmp_path))
        # Python stack + coverage unavailable → fail-open with warning (existing behavior)
        assert verdict['verdict'] == 'proceed'
        assert verdict.get('warn') and '⚠️' in verdict['warn']


# ── C4: Java gate must scope test run to derived test-class filters ────────────


class TestC4JavaTestFilters:
    """C4: measure_branch_coverage_java must receive non-empty test_filters
    derived from the executor's reported test files, to prevent unrelated
    tests from covering the target branch (false green).
    """

    def _setup_java_task(self, tmp_path, test_file_line):
        from servers.tasks import (create_task, create_subtask,
                                   update_task_status, reserve_critic_task)
        targets = [{'file_path': 'src/main/java/demo/Foo.java', 'name': 'bar',
                    'line_start': 1, 'line_end': 9}]
        epic = create_task(project='proj', description='epic', task_level='epic')
        story = create_subtask(parent_id=epic, description='story', task_level='story',
                               requires_validation=False)
        task = create_subtask(parent_id=story, description='write tests',
                              requires_validation=True,
                              metadata={'coverage_targets': targets})
        update_task_status(task, 'done', result=f'done\n{test_file_line}')
        critic = reserve_critic_task(task)
        return task, critic['id'], targets

    def test_java_test_filters_derived_as_fq_class_names(
            self, mock_db_path, tmp_path, monkeypatch):
        """Given a derived Java test file path, assert measure_branch_coverage_java
        is called with a non-empty test_filters list of fully-qualified class names."""
        import servers.coverage as cov
        import servers.coverage_java as cov_java
        import servers.project as project
        import servers.facade as facade

        task, critic_id, targets = self._setup_java_task(
            tmp_path,
            'TEST_TARGETS: src/test/java/demo/FooTest.java'
        )

        monkeypatch.setattr(project, 'ensure_project',
                            lambda *a, **k: {'tech_stack': {'test_tool': 'gradle'}})
        monkeypatch.setattr(cov, '_coverage_available', lambda: True)
        monkeypatch.setattr(cov, 'derive_test_targets',
                            lambda *a, **k: ['src/test/java/demo/FooTest.java'])

        captured_filters = []

        def fake_java(project_path, test_targets, coverage_targets, **kwargs):
            captured_filters.extend(kwargs.get('test_filters') or [])
            return {'tool_status': 'ok', 'fully_covered': True, 'error': None,
                    'per_target': [{'file_path': 'src/main/java/demo/Foo.java',
                                    'name': 'bar', 'line_start': 1, 'line_end': 9,
                                    'covered_branches': [{'from': 2, 'to': 3}],
                                    'missing_branches': [], 'n_total': 1, 'n_covered': 1}]}

        monkeypatch.setattr(cov_java, 'measure_branch_coverage_java', fake_java)

        verdict = facade.run_coverage_gate(critic_id, task, 'proj', str(tmp_path))
        assert verdict['verdict'] == 'proceed'
        assert len(captured_filters) > 0, (
            "C4: measure_branch_coverage_java was called without test_filters; "
            "this allows unrelated tests to cover the branch (false green)."
        )
        # The filter must be the FQ class name, not a file path
        assert 'demo.FooTest' in captured_filters, (
            f"C4: expected 'demo.FooTest' in test_filters, got {captured_filters}"
        )

    def test_java_fq_classname_conversion_various_paths(
            self, mock_db_path, tmp_path, monkeypatch):
        """Various Java/Kotlin test file paths must convert to correct FQ class names."""
        import servers.coverage as cov
        import servers.coverage_java as cov_java
        import servers.project as project
        import servers.facade as facade

        # Test multiple test files at once
        task, critic_id, targets = self._setup_java_task(
            tmp_path,
            'TEST_TARGETS: src/test/java/com/example/BarTest.java, '
            'src/test/kotlin/com/example/BazSpec.kt'
        )

        monkeypatch.setattr(project, 'ensure_project',
                            lambda *a, **k: {'tech_stack': {'test_tool': 'gradle'}})
        monkeypatch.setattr(cov, '_coverage_available', lambda: True)
        monkeypatch.setattr(cov, 'derive_test_targets',
                            lambda *a, **k: [
                                'src/test/java/com/example/BarTest.java',
                                'src/test/kotlin/com/example/BazSpec.kt',
                            ])

        captured_filters = []

        def fake_java(project_path, test_targets, coverage_targets, **kwargs):
            captured_filters.extend(kwargs.get('test_filters') or [])
            return {'tool_status': 'ok', 'fully_covered': True, 'error': None,
                    'per_target': [{'file_path': 'src/main/java/demo/Foo.java',
                                    'name': 'bar', 'line_start': 1, 'line_end': 9,
                                    'covered_branches': [{'from': 2, 'to': 3}],
                                    'missing_branches': [], 'n_total': 1, 'n_covered': 1}]}

        monkeypatch.setattr(cov_java, 'measure_branch_coverage_java', fake_java)

        facade.run_coverage_gate(critic_id, task, 'proj', str(tmp_path))
        assert 'com.example.BarTest' in captured_filters
        assert 'com.example.BazSpec' in captured_filters


# ── JS stack routing (Vitest/Jest) ────────────────────────────────────────────


class TestJsStackRouting:
    """Gate routes vitest/jest/mocha tech_stack to measure_branch_coverage_js.

    The JS backend is monkeypatched; test asserts it is called and only it is called.
    """

    def _setup_done_task(self, cov_targets, result):
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

    def _fake_ok(self):
        return {
            'tool_status': 'ok', 'fully_covered': True, 'error': None,
            'per_target': [{'file_path': 'src/classify.js', 'name': 'classify',
                            'line_start': 1, 'line_end': 5,
                            'covered_branches': [{'from': 2, 'to': 0}],
                            'missing_branches': [], 'n_total': 1, 'n_covered': 1}],
        }

    def test_vitest_stack_routes_to_js_backend(
            self, mock_db_path, tmp_path, monkeypatch):
        import servers.coverage as cov
        import servers.coverage_js as cov_js
        import servers.project as project
        import servers.facade as facade

        targets = [{'file_path': 'src/classify.js', 'name': 'classify',
                    'line_start': 1, 'line_end': 5}]
        task, critic_id = self._setup_done_task(
            targets, 'done\nTEST_TARGETS: test/classify.test.js')

        monkeypatch.setattr(project, 'ensure_project',
                            lambda *a, **k: {'tech_stack': {'test_tool': 'vitest'}})
        monkeypatch.setattr(cov, '_coverage_available', lambda: True)
        monkeypatch.setattr(cov, 'derive_test_targets',
                            lambda *a, **k: ['test/classify.test.js'])
        monkeypatch.setattr(cov_js, '_js_available', lambda: True)

        js_calls = []
        python_calls = []

        def fake_js(project_path, test_targets, coverage_targets, *, tool='vitest'):
            js_calls.append(tool)
            return self._fake_ok()

        def fake_python(project_path, test_targets, coverage_targets):
            python_calls.append(1)
            return self._fake_ok()

        monkeypatch.setattr(cov_js, 'measure_branch_coverage_js', fake_js)
        monkeypatch.setattr(cov, 'measure_branch_coverage', fake_python)

        verdict = facade.run_coverage_gate(critic_id, task, 'proj', str(tmp_path))

        assert verdict['verdict'] == 'proceed'
        assert len(js_calls) == 1, 'JS backend must be called exactly once'
        assert js_calls[0] == 'vitest', f'tool should be vitest, got {js_calls[0]}'
        assert len(python_calls) == 0, 'Python backend must NOT be called for vitest stack'

    def test_jest_stack_routes_to_js_backend_with_jest_tool(
            self, mock_db_path, tmp_path, monkeypatch):
        import servers.coverage as cov
        import servers.coverage_js as cov_js
        import servers.project as project
        import servers.facade as facade

        targets = [{'file_path': 'src/f.js', 'name': 'f',
                    'line_start': 1, 'line_end': 5}]
        task, critic_id = self._setup_done_task(
            targets, 'done\nTEST_TARGETS: src/f.test.js')

        monkeypatch.setattr(project, 'ensure_project',
                            lambda *a, **k: {'tech_stack': {'test_tool': 'jest'}})
        monkeypatch.setattr(cov, '_coverage_available', lambda: True)
        monkeypatch.setattr(cov, 'derive_test_targets',
                            lambda *a, **k: ['src/f.test.js'])
        monkeypatch.setattr(cov_js, '_js_available', lambda: True)

        js_calls = []

        def fake_js(project_path, test_targets, coverage_targets, *, tool='vitest'):
            js_calls.append(tool)
            return self._fake_ok()

        monkeypatch.setattr(cov_js, 'measure_branch_coverage_js', fake_js)

        verdict = facade.run_coverage_gate(critic_id, task, 'proj', str(tmp_path))

        assert verdict['verdict'] == 'proceed'
        assert len(js_calls) == 1
        assert js_calls[0] == 'jest', f'tool should be jest, got {js_calls[0]}'

    def test_js_npx_unavailable_rejects_fail_closed(
            self, mock_db_path, tmp_path, monkeypatch):
        """J2: When npx is not found, gate must fail-CLOSED (reject, not proceed)."""
        import servers.coverage as cov
        import servers.coverage_js as cov_js
        import servers.project as project
        import servers.facade as facade

        targets = [{'file_path': 'src/f.js', 'name': 'f',
                    'line_start': 1, 'line_end': 5}]
        task, critic_id = self._setup_done_task(
            targets, 'done\nTEST_TARGETS: src/f.test.js')

        monkeypatch.setattr(project, 'ensure_project',
                            lambda *a, **k: {'tech_stack': {'test_tool': 'vitest'}})
        monkeypatch.setattr(cov, 'derive_test_targets', lambda *a, **k: ['src/f.test.js'])
        monkeypatch.setattr(cov_js, '_js_available', lambda: False)

        verdict = facade.run_coverage_gate(critic_id, task, 'proj', str(tmp_path))

        # J2 fix: JS runner absence is NOT an infra carve-out → must reject, not proceed.
        assert verdict['verdict'] == 'rejected', (
            f'J2: JS runner absence must reject (fail-closed), got {verdict["verdict"]}' )
        assert '不予放行' in ' '.join(verdict.get('issues', []))


# ── Part A: coverage gate persists per_target data to working_memory ──────────


class TestRunCoverageGatePersistsCoverage:
    """After run_coverage_gate measures coverage (status=ok), the original task's
    'coverage' working-memory key must be populated with per_target compact data."""

    def _setup_done_task(self, cov_targets, result):
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

    def test_fully_covered_persists_coverage_to_working_memory(
            self, mock_db_path, tmp_path, monkeypatch):
        import json
        import servers.coverage as cov
        import servers.facade as facade
        from servers.memory import get_working_memory

        targets = [{'file_path': 'src/x.py', 'name': 'foo',
                    'line_start': 1, 'line_end': 9}]
        task, critic_id = self._setup_done_task(
            targets, 'done\nTEST_TARGETS: tests/test_x.py')
        monkeypatch.setattr(cov, '_coverage_available', lambda: True)
        monkeypatch.setattr(cov, 'derive_test_targets',
                            lambda *a, **k: ['tests/test_x.py'])
        monkeypatch.setattr(cov, 'measure_branch_coverage', lambda *a, **k: {
            'tool_status': 'ok', 'fully_covered': True, 'error': None,
            'per_target': [{'file_path': 'src/x.py', 'name': 'foo',
                            'line_start': 1, 'line_end': 9,
                            'covered_branches': [{'from': 2, 'to': 3}],
                            'missing_branches': [], 'n_total': 2, 'n_covered': 2}],
        })
        import servers.project as _proj
        monkeypatch.setattr(_proj, 'ensure_project',
                            lambda *a, **k: {'tech_stack': {'test_tool': 'pytest'}})

        verdict = facade.run_coverage_gate(critic_id, task, 'proj', str(tmp_path))
        assert verdict['verdict'] == 'proceed'

        raw = get_working_memory(task, 'coverage')
        assert raw is not None, 'coverage working-memory must be set after ok measurement'
        data = json.loads(raw) if isinstance(raw, str) else raw
        assert isinstance(data, list) and len(data) == 1
        entry = data[0]
        assert entry['file_path'] == 'src/x.py'
        assert entry['name'] == 'foo'
        assert entry['n_covered'] == 2
        assert entry['n_total'] == 2
        assert entry['missing_branches'] == []

    def test_partially_covered_also_persists_coverage_before_reject(
            self, mock_db_path, tmp_path, monkeypatch):
        """Gate must persist coverage data even for the reject path (partial coverage)."""
        import json
        import servers.coverage as cov
        import servers.facade as facade
        from servers.memory import get_working_memory

        targets = [{'file_path': 'src/x.py', 'name': 'foo',
                    'line_start': 1, 'line_end': 9}]
        task, critic_id = self._setup_done_task(
            targets, 'done\nTEST_TARGETS: tests/test_x.py')
        monkeypatch.setattr(cov, '_coverage_available', lambda: True)
        monkeypatch.setattr(cov, 'derive_test_targets',
                            lambda *a, **k: ['tests/test_x.py'])
        monkeypatch.setattr(cov, 'measure_branch_coverage', lambda *a, **k: {
            'tool_status': 'ok', 'fully_covered': False, 'error': None,
            'per_target': [{'file_path': 'src/x.py', 'name': 'foo',
                            'line_start': 1, 'line_end': 9,
                            'covered_branches': [{'from': 2, 'to': 3}],
                            'missing_branches': [{'from': 2, 'to': 5}],
                            'n_total': 2, 'n_covered': 1}],
        })
        import servers.project as _proj
        monkeypatch.setattr(_proj, 'ensure_project',
                            lambda *a, **k: {'tech_stack': {'test_tool': 'pytest'}})

        verdict = facade.run_coverage_gate(critic_id, task, 'proj', str(tmp_path))
        assert verdict['verdict'] == 'rejected'

        raw = get_working_memory(task, 'coverage')
        assert raw is not None, 'coverage working-memory must be set even on reject path'
        data = json.loads(raw) if isinstance(raw, str) else raw
        assert isinstance(data, list) and len(data) == 1
        entry = data[0]
        assert entry['n_covered'] == 1
        assert entry['n_total'] == 2
        assert entry['missing_branches'] == [{'from': 2, 'to': 5}]


# ── K1: fail-closed on undetermined JS runner ─────────────────────────────────


class TestK1UndeterminedJsRunner:
    """K1: when test_tool is None/empty/mocha (not vitest/jest), gate must
    reject fail-closed rather than defaulting to vitest and running the wrong runner.
    """

    def _setup_js_task(self, monkeypatch, test_tool_value, primary_language='typescript'):
        """Create a done task with JS coverage_targets; mock ensure_project with test_tool_value.

        primary_language defaults to 'typescript' so select_backend routes to 'js'
        even when test_tool is None/empty — this is the scenario where a JS project
        has no explicit test runner configured.
        """
        import servers.project as _proj
        monkeypatch.setattr(_proj, 'ensure_project',
                            lambda *a, **k: {
                                'tech_stack': {
                                    'test_tool': test_tool_value,
                                    'primary_language': primary_language,
                                }
                            })

        from servers.tasks import (create_task, create_subtask,
                                   update_task_status, reserve_critic_task)
        import servers.coverage as cov
        monkeypatch.setattr(cov, 'derive_test_targets',
                            lambda *a, **k: ['test/f.test.js'])

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

    def test_none_test_tool_rejects_not_vitest(
            self, mock_db_path, tmp_path, monkeypatch):
        """K1: test_tool=None → gate rejects (fail-closed), never runs vitest."""
        import servers.coverage_js as cov_js
        import servers.facade as facade

        # Mark npx as available to ensure it's the tool check that rejects, not npx
        monkeypatch.setattr(cov_js, '_js_available', lambda: True)

        js_calls = []

        def fake_js(*a, **k):
            js_calls.append(k.get('tool', 'unknown'))
            return {'tool_status': 'ok', 'fully_covered': True,
                    'error': None, 'per_target': []}

        monkeypatch.setattr(cov_js, 'measure_branch_coverage_js', fake_js)

        task, critic_id = self._setup_js_task(monkeypatch, test_tool_value=None)
        verdict = facade.run_coverage_gate(critic_id, task, 'proj', str(tmp_path))

        assert verdict['verdict'] == 'rejected', (
            f'K1: test_tool=None must reject (fail-closed), got {verdict["verdict"]}')
        assert js_calls == [], (
            'K1: measure_branch_coverage_js must NOT be called when test_tool is None')
        # Error message must explain the problem
        issues_text = ' '.join(verdict.get('issues', []))
        assert 'runner' in issues_text or 'vitest' in issues_text or 'jest' in issues_text, (
            f'K1: rejection must explain runner ambiguity, got issues: {verdict.get("issues")}')

    def test_empty_test_tool_rejects(
            self, mock_db_path, tmp_path, monkeypatch):
        """K1: test_tool='' (empty string) → gate rejects, not vitest run."""
        import servers.coverage_js as cov_js
        import servers.facade as facade

        monkeypatch.setattr(cov_js, '_js_available', lambda: True)
        js_calls = []

        def fake_js(*a, **k):
            js_calls.append(k.get('tool'))
            return {'tool_status': 'ok', 'fully_covered': True,
                    'error': None, 'per_target': []}

        monkeypatch.setattr(cov_js, 'measure_branch_coverage_js', fake_js)

        task, critic_id = self._setup_js_task(monkeypatch, test_tool_value='')
        verdict = facade.run_coverage_gate(critic_id, task, 'proj', str(tmp_path))

        assert verdict['verdict'] == 'rejected', (
            f'K1: test_tool="" must reject, got {verdict["verdict"]}')
        assert js_calls == []

    def test_mocha_test_tool_rejects(
            self, mock_db_path, tmp_path, monkeypatch):
        """K1: test_tool='mocha' → gate rejects (mocha is not supported)."""
        import servers.coverage_js as cov_js
        import servers.facade as facade

        monkeypatch.setattr(cov_js, '_js_available', lambda: True)
        js_calls = []

        def fake_js(*a, **k):
            js_calls.append(k.get('tool'))
            return {'tool_status': 'unavailable', 'fully_covered': False,
                    'error': 'mocha unsupported', 'per_target': []}

        monkeypatch.setattr(cov_js, 'measure_branch_coverage_js', fake_js)

        task, critic_id = self._setup_js_task(monkeypatch, test_tool_value='mocha')
        verdict = facade.run_coverage_gate(critic_id, task, 'proj', str(tmp_path))

        assert verdict['verdict'] == 'rejected', (
            f'K1: test_tool=mocha must reject, got {verdict["verdict"]}')

    def test_vitest_test_tool_calls_measure(
            self, mock_db_path, tmp_path, monkeypatch):
        """K1: test_tool='vitest' → measure_branch_coverage_js IS called with tool='vitest'."""
        import servers.coverage_js as cov_js
        import servers.facade as facade

        monkeypatch.setattr(cov_js, '_js_available', lambda: True)
        js_calls = []

        def fake_js(project_path, test_targets, coverage_targets, *, tool='vitest'):
            js_calls.append(tool)
            return {'tool_status': 'ok', 'fully_covered': True, 'error': None,
                    'per_target': [{'file_path': 'src/f.js', 'name': 'fn',
                                    'line_start': 1, 'line_end': 10,
                                    'covered_branches': [{'from': 2, 'to': 0}],
                                    'missing_branches': [], 'n_total': 1, 'n_covered': 1}]}

        monkeypatch.setattr(cov_js, 'measure_branch_coverage_js', fake_js)

        task, critic_id = self._setup_js_task(monkeypatch, test_tool_value='vitest')
        verdict = facade.run_coverage_gate(critic_id, task, 'proj', str(tmp_path))

        assert verdict['verdict'] == 'proceed', (
            f'K1: vitest must proceed to measure, got {verdict["verdict"]}')
        assert js_calls == ['vitest'], (
            f'K1: measure must be called with tool=vitest, got {js_calls}')

    def test_jest_test_tool_calls_measure(
            self, mock_db_path, tmp_path, monkeypatch):
        """K1: test_tool='jest' → measure_branch_coverage_js IS called with tool='jest'."""
        import servers.coverage_js as cov_js
        import servers.facade as facade

        monkeypatch.setattr(cov_js, '_js_available', lambda: True)
        js_calls = []

        def fake_js(project_path, test_targets, coverage_targets, *, tool='vitest'):
            js_calls.append(tool)
            return {'tool_status': 'ok', 'fully_covered': True, 'error': None,
                    'per_target': [{'file_path': 'src/f.js', 'name': 'fn',
                                    'line_start': 1, 'line_end': 10,
                                    'covered_branches': [{'from': 2, 'to': 0}],
                                    'missing_branches': [], 'n_total': 1, 'n_covered': 1}]}

        monkeypatch.setattr(cov_js, 'measure_branch_coverage_js', fake_js)

        task, critic_id = self._setup_js_task(monkeypatch, test_tool_value='jest')
        verdict = facade.run_coverage_gate(critic_id, task, 'proj', str(tmp_path))

        assert verdict['verdict'] == 'proceed', (
            f'K1: jest must proceed to measure, got {verdict["verdict"]}')
        assert js_calls == ['jest'], (
            f'K1: measure must be called with tool=jest, got {js_calls}')


# =============================================================================
# M2 — n_total=0 must not render as green ✅ (display honesty)
# =============================================================================

class TestM2NoBranchesDisplay:
    """M2: format_coverage_summary with n_total=0 must show neutral marker,
    NOT ✅ and NOT '0/0'. Gate verdict is unaffected (branchless functions
    legitimately pass; this is a display-only fix).
    """

    def test_zero_total_no_green_check_mark(self):
        """M2 primary: n_total=0 → ✅ absent, 0/0 absent, neutral marker present."""
        from servers.coverage import format_coverage_summary
        per_target = [{'file_path': 'utils.py', 'name': 'noop',
                       'line_start': 5, 'line_end': 6,
                       'covered_branches': [], 'missing_branches': [],
                       'n_total': 0, 'n_covered': 0}]
        lines = format_coverage_summary(per_target)
        assert lines, "Must produce at least one output line"
        header = lines[0]
        assert '✅' not in header, f"M2: ✅ must not appear for branchless: {header!r}"
        assert '0/0' not in header, f"M2: '0/0' must not appear for branchless: {header!r}"
        assert 'n/a' in header or '無分支' in header, (
            f"M2: neutral marker (n/a or 無分支) must appear: {header!r}")

    def test_zero_total_no_branch_sub_lines(self):
        """M2: branchless → only the header line, no ✓/✗ sub-lines."""
        from servers.coverage import format_coverage_summary
        per_target = [{'file_path': 'utils.py', 'name': 'noop',
                       'line_start': 5, 'line_end': 6,
                       'covered_branches': [], 'missing_branches': [],
                       'n_total': 0, 'n_covered': 0}]
        lines = format_coverage_summary(per_target)
        assert len(lines) == 1, (
            f"M2: branchless must emit only header line, got {len(lines)}: {lines}")

    def test_normal_partial_still_shows_fraction_and_marks(self):
        """M2 regression: n_total>0 with missing branches → ❌ and fraction unchanged."""
        from servers.coverage import format_coverage_summary
        per_target = [{'file_path': 'calc.py', 'name': 'compute',
                       'line_start': 1, 'line_end': 8,
                       'covered_branches': [{'from': 2, 'to': 3}],
                       'missing_branches': [{'from': 2, 'to': 4},
                                            {'from': 4, 'to': 5},
                                            {'from': 4, 'to': 6}],
                       'n_total': 4, 'n_covered': 1}]
        lines = format_coverage_summary(per_target)
        header = lines[0]
        assert '1/4' in header, f"Normal partial must show fraction 1/4: {header!r}"
        assert '❌' in header, f"Normal partial must show ❌: {header!r}"
        assert '✅' not in header
        # Branch sub-lines present
        arc_lines = [ln for ln in lines if 'L' in ln and '→' in ln]
        assert len(arc_lines) == 4, f"Expect 4 branch lines, got {len(arc_lines)}"

    def test_full_coverage_nonzero_still_shows_green(self):
        """M2 regression: n_total>0, fully covered → ✅ unchanged."""
        from servers.coverage import format_coverage_summary
        per_target = [{'file_path': 'calc.py', 'name': 'compute',
                       'line_start': 1, 'line_end': 5,
                       'covered_branches': [{'from': 2, 'to': 3},
                                            {'from': 2, 'to': 4}],
                       'missing_branches': [],
                       'n_total': 2, 'n_covered': 2}]
        lines = format_coverage_summary(per_target)
        header = lines[0]
        assert '2/2' in header, f"Full coverage must show 2/2: {header!r}"
        assert '✅' in header, f"Full coverage must show ✅: {header!r}"

    def test_mixed_branchless_and_normal_in_same_summary(self):
        """M2: multiple targets where one is branchless, other normal → each correct."""
        from servers.coverage import format_coverage_summary
        per_target = [
            {'file_path': 'a.py', 'name': 'noop',
             'line_start': 1, 'line_end': 2,
             'covered_branches': [], 'missing_branches': [],
             'n_total': 0, 'n_covered': 0},
            {'file_path': 'b.py', 'name': 'compute',
             'line_start': 1, 'line_end': 5,
             'covered_branches': [{'from': 2, 'to': 3}],
             'missing_branches': [{'from': 2, 'to': 4}],
             'n_total': 2, 'n_covered': 1},
        ]
        lines = format_coverage_summary(per_target)
        headers = [ln for ln in lines if ln.startswith('📊')]
        assert len(headers) == 2
        # Branchless: neutral
        assert '✅' not in headers[0] and '0/0' not in headers[0]
        assert 'n/a' in headers[0] or '無分支' in headers[0]
        # Normal with missing: fraction + ❌
        assert '1/2' in headers[1] and '❌' in headers[1]


# =============================================================================
# N1 — branchless function requires execution evidence (close n_total=0 false-green)
# =============================================================================

def _write_branchless_fixture(root, test_calls_add: bool):
    """Write fixture with a branchless `add` function.

    When test_calls_add=True the test calls add(); when False it only imports.
    """
    (root / 'math_utils.py').write_text(textwrap.dedent('''\
        def add(a, b):
            return a + b
    '''))
    if test_calls_add:
        (root / 'test_math_utils.py').write_text(textwrap.dedent('''\
            from math_utils import add
            def test_add():
                assert add(1, 2) == 3
        '''))
    else:
        (root / 'test_math_utils.py').write_text(textwrap.dedent('''\
            from math_utils import add
            def test_unrelated():
                assert 1 + 1 == 2
        '''))


@pytest.mark.skipif(not _coverage_importable(), reason="coverage not installed")
class TestBranchlessExecutionEvidence:
    """N1: branchless function (n_total==0) requires execution evidence via
    executed_lines. Imported-but-not-called must NOT pass the gate (false-green).
    Called branchless function MUST pass (do not reject real branchless functions).
    """

    def test_branchless_executed_function_is_covered(self, tmp_path):
        """N1-A: branchless add() that IS called → tool_status='ok', fully_covered=True."""
        from servers.coverage import measure_branch_coverage
        _write_branchless_fixture(tmp_path, test_calls_add=True)
        targets = [{'file_path': 'math_utils.py', 'name': 'add',
                    'line_start': 1, 'line_end': 2}]
        res = measure_branch_coverage(str(tmp_path), ['test_math_utils.py'], targets)
        assert res['tool_status'] == 'ok', (
            f"N1-A: called branchless function must pass gate, got {res['tool_status']}: "
            f"{res.get('error')}")
        assert res['fully_covered'] is True, (
            f"N1-A: called branchless function must be fully_covered, got {res}")
        pt = res['per_target'][0]
        assert pt['n_total'] == 0, "N1-A: add() is branchless, n_total must be 0"
        assert pt['missing_branches'] == []

    def test_branchless_imported_but_not_called_is_not_covered(self, tmp_path):
        """N1-B (false-green regression): branchless add() that is only IMPORTED
        but never called must NOT pass the gate (fail-closed).

        Previously n_total==0 with no missing_branches was treated as fully_covered=True,
        giving a false-green for untested functions that merely appear in coverage data
        because they were imported.
        """
        from servers.coverage import measure_branch_coverage
        _write_branchless_fixture(tmp_path, test_calls_add=False)
        targets = [{'file_path': 'math_utils.py', 'name': 'add',
                    'line_start': 1, 'line_end': 2}]
        res = measure_branch_coverage(str(tmp_path), ['test_math_utils.py'], targets)
        # Must NOT be fully_covered (the function body was never executed)
        assert res['fully_covered'] is False, (
            f"N1-B: imported-but-not-called branchless function must NOT pass gate, "
            f"got fully_covered=True (false-green!): {res}")
        # Gate must signal fail-closed: no_targets (consistent with file-absent case)
        assert res['tool_status'] == 'no_targets', (
            f"N1-B: expected tool_status='no_targets', got {res['tool_status']}: "
            f"{res.get('error')}")
        assert res['error'] and 'add' in res['error'], (
            f"N1-B: error message must mention target name 'add', got: {res.get('error')}")


# =============================================================================
# N1-unit — _attribute_targets synthetic tests for executed_lines logic
# =============================================================================

class TestAttributeTargetsBranchlessExecutionEvidence:
    """Unit tests for _attribute_targets executed_lines check (n_total==0 path).

    Uses synthetic file_index to avoid running real coverage processes.
    """

    def _make_root_and_canon(self, tmp_path, fname='x.py'):
        root = str(tmp_path)
        canon = os.path.realpath(os.path.join(root, fname))
        return root, canon

    def test_branchless_with_executed_line_in_range_passes(self, tmp_path):
        """n_total==0 + executed_lines has a body line → ok, fully_covered=True.

        AST fix (D2g): source file must exist so AST can determine body_start_line.
        def add(a, b): body starts at line 2; executed_lines=[1,2] includes line 2.
        """
        from servers.coverage import _attribute_targets
        root, canon = self._make_root_and_canon(tmp_path)
        # Write source file so _ast_body_start_line can parse it.
        # def add is at line 1; body (return a + b) is at line 2.
        (tmp_path / 'x.py').write_text('def add(a, b):\n    return a + b\n')
        file_index = {canon: {
            'missing_branches': [],
            'executed_branches': [],
            'executed_lines': [1, 2],  # line 2 = body_start; counts as execution
        }}
        targets = [{'file_path': 'x.py', 'name': 'add', 'line_start': 1, 'line_end': 2}]
        res = _attribute_targets(file_index, targets, root)
        assert res['tool_status'] == 'ok'
        assert res['fully_covered'] is True
        assert res['per_target'][0]['n_total'] == 0

    def test_branchless_with_no_executed_line_in_range_is_no_targets(self, tmp_path):
        """n_total==0 + no executed_lines in body range → no_targets (fail-closed).

        AST fix (D2g): source file must exist so AST can determine body_start_line.
        Lines 10,20 are outside [body_start=2, le=2] → no execution evidence.
        """
        from servers.coverage import _attribute_targets
        root, canon = self._make_root_and_canon(tmp_path)
        # Write source file so _ast_body_start_line can parse it (body_start=2).
        (tmp_path / 'x.py').write_text('def add(a, b):\n    return a + b\n')
        file_index = {canon: {
            'missing_branches': [],
            'executed_branches': [],
            'executed_lines': [10, 20],  # outside body range [2,2]
        }}
        targets = [{'file_path': 'x.py', 'name': 'add', 'line_start': 1, 'line_end': 2}]
        res = _attribute_targets(file_index, targets, root)
        assert res['tool_status'] == 'no_targets'
        assert res['fully_covered'] is False
        assert res['error'] and 'add' in res['error']

    def test_branchless_missing_executed_lines_key_is_schema_error(self, tmp_path):
        """n_total==0 + executed_lines absent → schema_error (fail-closed)."""
        from servers.coverage import _attribute_targets
        root, canon = self._make_root_and_canon(tmp_path)
        file_index = {canon: {
            'missing_branches': [],
            'executed_branches': [],
            # executed_lines key absent
        }}
        targets = [{'file_path': 'x.py', 'name': 'add', 'line_start': 1, 'line_end': 2}]
        res = _attribute_targets(file_index, targets, root)
        assert res['tool_status'] == 'schema_error'
        assert res['fully_covered'] is False

    def test_branchless_executed_lines_not_list_is_schema_error(self, tmp_path):
        """n_total==0 + executed_lines is not a list → schema_error."""
        from servers.coverage import _attribute_targets
        root, canon = self._make_root_and_canon(tmp_path)
        file_index = {canon: {
            'missing_branches': [],
            'executed_branches': [],
            'executed_lines': None,
        }}
        targets = [{'file_path': 'x.py', 'name': 'add', 'line_start': 1, 'line_end': 2}]
        res = _attribute_targets(file_index, targets, root)
        assert res['tool_status'] == 'schema_error'
        assert res['fully_covered'] is False

    def test_branchless_bool_in_executed_lines_not_counted(self, tmp_path):
        """n_total==0 + executed_lines contains only booleans → treated as no execution.

        AST fix (D2g): source file must exist so AST can determine body_start_line.
        True/False are excluded by isinstance(ln, bool) check; no real line passes.
        """
        from servers.coverage import _attribute_targets
        root, canon = self._make_root_and_canon(tmp_path)
        # Write source file so _ast_body_start_line can parse it (body_start=2).
        (tmp_path / 'x.py').write_text('def add(a, b):\n    return a + b\n')
        file_index = {canon: {
            'missing_branches': [],
            'executed_branches': [],
            'executed_lines': [True, False],  # booleans are not valid line numbers
        }}
        targets = [{'file_path': 'x.py', 'name': 'add', 'line_start': 1, 'line_end': 2}]
        res = _attribute_targets(file_index, targets, root)
        # True==1 and False==0 numerically, but bool check excludes them.
        # With body_start=2, True (==1) is also < body_start, doubly excluded.
        assert res['tool_status'] == 'no_targets'
        assert res['fully_covered'] is False

    def test_n_total_gt_0_does_not_consult_executed_lines(self, tmp_path):
        """n_total>0 path: executed_lines is irrelevant; existing arc logic unchanged."""
        from servers.coverage import _attribute_targets
        root, canon = self._make_root_and_canon(tmp_path)
        # entry has branches AND executed_lines is absent (schema would error if consulted)
        file_index = {canon: {
            'missing_branches': [[2, 4]],
            'executed_branches': [[2, 3]],
            # executed_lines intentionally absent — must NOT be consulted for n_total>0
        }}
        targets = [{'file_path': 'x.py', 'name': 'f', 'line_start': 1, 'line_end': 9}]
        res = _attribute_targets(file_index, targets, root)
        # Must succeed normally, not schema_error due to missing executed_lines
        assert res['tool_status'] == 'ok'
        assert res['fully_covered'] is False
        pt = res['per_target'][0]
        assert pt['n_total'] == 2 and pt['n_covered'] == 1


# =============================================================================
# D2g — multiline-signature default-arg false-green (AST body-line fix)
# =============================================================================


def _write_multiline_sig_fixture(root, test_calls_branchless: bool):
    """Fixture: branchless function with multi-line signature and side-effecting default.

    _side_effect() runs at import time (line 2 of the function, inside the signature).
    The function body starts at line 4 (return value).

    Structure of multiline_sig.py:
        line 1: def _side_effect():
        line 2:     return 42
        line 3: def branchless(
        line 4:     value=_side_effect(),   # executes at import; in signature, not body
        line 5: ):
        line 6:     return value            # body_start_line = 6
    """
    (root / 'multiline_sig.py').write_text(
        'def _side_effect():\n'
        '    return 42\n'
        'def branchless(\n'
        '    value=_side_effect(),\n'
        '):\n'
        '    return value\n'
    )
    if test_calls_branchless:
        (root / 'test_multiline_sig.py').write_text(
            'from multiline_sig import branchless\n'
            'def test_called():\n'
            '    assert branchless() == 42\n'
        )
    else:
        # Only imports the module (triggers _side_effect at import time) but never calls branchless
        (root / 'test_multiline_sig.py').write_text(
            'import multiline_sig\n'
            'def test_unrelated():\n'
            '    assert 1 + 1 == 2\n'
        )


@pytest.mark.skipif(not _coverage_importable(), reason="coverage not installed")
class TestBranchlessMultilineSignature:
    """D2g: multi-line function signature with side-effecting default arg.

    The default-arg expression runs at import time on a line INSIDE the signature
    range (ls+1..le) but NOT inside the function body.  The old heuristic (ls+1..le)
    falsely accepted import-time execution as proof of a call.  The AST fix uses
    body[0].lineno (the first body statement) so only actual calls pass.
    """

    def test_branchless_multiline_signature_default_import_only_not_covered(
            self, tmp_path):
        """D2g regression: multiline-sig function that is only IMPORTED (default-arg
        executed at import) must NOT pass the gate → no_targets (fail-closed).

        This is the codex false-green: previously the signature line (with the default
        arg call) fell in ls+1..le and was counted as body execution → false proceed.
        With the AST fix the body starts later and import-only is correctly rejected.
        """
        from servers.coverage import measure_branch_coverage
        _write_multiline_sig_fixture(tmp_path, test_calls_branchless=False)
        # branchless starts at line 3, ends at line 6
        targets = [{'file_path': 'multiline_sig.py', 'name': 'branchless',
                    'line_start': 3, 'line_end': 6}]
        res = measure_branch_coverage(
            str(tmp_path), ['test_multiline_sig.py'], targets)
        # Must NOT be fully_covered — function body was never executed
        assert res['fully_covered'] is False, (
            f'D2g: import-only multiline-sig must NOT pass gate (false-green!): {res}')
        assert res['tool_status'] == 'no_targets', (
            f'D2g: expected no_targets, got {res["tool_status"]}: {res.get("error")}')
        assert res['error'] and 'branchless' in res['error'], (
            f'D2g: error must mention target name, got: {res.get("error")}')

    def test_branchless_multiline_signature_called_is_covered(self, tmp_path):
        """D2g: same multiline-sig function but actually CALLED → fully_covered=True.

        Confirms the fix does not break legitimate branchless function coverage.
        """
        from servers.coverage import measure_branch_coverage
        _write_multiline_sig_fixture(tmp_path, test_calls_branchless=True)
        targets = [{'file_path': 'multiline_sig.py', 'name': 'branchless',
                    'line_start': 3, 'line_end': 6}]
        res = measure_branch_coverage(
            str(tmp_path), ['test_multiline_sig.py'], targets)
        assert res['tool_status'] == 'ok', (
            f'D2g: called multiline-sig must pass gate, got {res["tool_status"]}: '
            f'{res.get("error")}')
        assert res['fully_covered'] is True, (
            f'D2g: called multiline-sig must be fully_covered: {res}')
        pt = res['per_target'][0]
        assert pt['n_total'] == 0, 'D2g: branchless() has no branches'
        assert pt['missing_branches'] == []


# =============================================================================
# D2h — single-physical-line branchless function (def and body on same line)
# =============================================================================


def _write_single_physical_line_fixture(root, test_calls_f: bool):
    """Fixture: `def f(): return 1` — def and body on the SAME physical line.

    FunctionDef.lineno == body[0].lineno == 1 for this function.
    Coverage marks line 1 as executed at import time (defining f runs that
    line), so we CANNOT prove f() was actually called via line coverage alone.
    """
    (root / 'single_line.py').write_text('def f(): return 1\n')
    if test_calls_f:
        (root / 'test_single_line.py').write_text(
            'from single_line import f\n'
            'def test_calls_f():\n'
            '    assert f() == 1\n'
        )
    else:
        # Only imports the module (triggers def at import) but never calls f()
        (root / 'test_single_line.py').write_text(
            'import single_line\n'
            'def test_unrelated():\n'
            '    assert 1 + 1 == 2\n'
        )


@pytest.mark.skipif(not _coverage_importable(), reason="coverage not installed")
class TestSinglePhysicalLineBranchless:
    """D2h: branchless function whose def and body share one physical line.

    `def f(): return 1` has FunctionDef.lineno == body[0].lineno.  Coverage
    marks that line as executed at import time, so the execution-evidence check
    (body_start > ls AND executed line in range) cannot prove a call was made.

    Fix: when body_start <= line_start → fail-closed (no_targets) regardless
    of whether f() was actually called.  This is the deliberate conservative
    behavior — no false green.
    """

    def test_single_physical_line_branchless_import_only_not_covered(
            self, tmp_path):
        """D2h: `def f(): return 1` — test only imports, never calls f().

        Must fail-closed: no_targets (line coverage cannot prove f() was called
        even in the import-only case, so we definitely should not green-light).
        """
        from servers.coverage import measure_branch_coverage
        _write_single_physical_line_fixture(tmp_path, test_calls_f=False)
        # f is on line 1, starts and ends at line 1 (single physical line)
        targets = [{'file_path': 'single_line.py', 'name': 'f',
                    'line_start': 1, 'line_end': 1}]
        res = measure_branch_coverage(
            str(tmp_path), ['test_single_line.py'], targets)
        assert res['fully_covered'] is False, (
            f'D2h: single-physical-line import-only must NOT be fully_covered '
            f'(false-green!): {res}')
        assert res['tool_status'] == 'no_targets', (
            f'D2h: expected no_targets, got {res["tool_status"]}: '
            f'{res.get("error")}')
        assert res['error'] and 'f' in res['error'], (
            f'D2h: error must mention target name, got: {res.get("error")}')
        assert '同行' in (res['error'] or '') or '單行' in (res['error'] or ''), (
            f'D2h: error must describe single-physical-line reason, got: '
            f'{res.get("error")}')

    def test_single_physical_line_branchless_even_when_called_is_fail_closed(
            self, tmp_path):
        """D2h: `def f(): return 1` — test DOES call f() — STILL fail-closed.

        This is the deliberate conservative behavior: line coverage marks line 1
        as executed at both import time AND call time, so we cannot distinguish
        the two cases.  We conservatively reject (no false green) even when f()
        was actually called.

        INTENTIONAL: no_targets here does NOT mean "untested" — it means "line
        coverage cannot prove the call, escalate to retry/critic/human rather
        than auto-passing".
        """
        from servers.coverage import measure_branch_coverage
        _write_single_physical_line_fixture(tmp_path, test_calls_f=True)
        targets = [{'file_path': 'single_line.py', 'name': 'f',
                    'line_start': 1, 'line_end': 1}]
        res = measure_branch_coverage(
            str(tmp_path), ['test_single_line.py'], targets)
        # DELIBERATE: even with f() called, single-physical-line is fail-closed.
        # Line coverage cannot distinguish import-time def from a real call.
        assert res['fully_covered'] is False, (
            f'D2h: single-physical-line must be fail-closed even when called '
            f'(conservative, no false green): {res}')
        assert res['tool_status'] == 'no_targets', (
            f'D2h: expected no_targets (fail-closed) even when called, '
            f'got {res["tool_status"]}: {res.get("error")}')


@pytest.mark.skipif(not _coverage_importable(), reason="coverage not installed")
class TestBranchlessClosingParenBody:
    """codex feat2 r8 suggestion: `def f(\\n): return 1` — body on the
    closing-paren line (line 2), a multi-line-signature variant. Import-only
    must NOT be reported fully_covered (the body return runs only on call)."""

    def test_closing_paren_body_import_only_not_covered(self, tmp_path):
        from servers.coverage import measure_branch_coverage
        (tmp_path / 'cp.py').write_text('def f(\n): return 1\n')
        (tmp_path / 'test_cp.py').write_text(
            'import cp\ndef test_imports_only():\n    assert cp is not None\n')
        # f: def on line 1, body (return) on line 2
        targets = [{'file_path': 'cp.py', 'name': 'f',
                    'line_start': 1, 'line_end': 2}]
        res = measure_branch_coverage(str(tmp_path), ['test_cp.py'], targets)
        assert res['fully_covered'] is False, (
            f'closing-paren-body import-only must NOT be fully_covered '
            f'(false-green!): {res}')

    def test_closing_paren_body_called_is_fail_closed_not_false_green(
            self, tmp_path):
        """`def f(\\n): return 1` where the body shares the closing-paren line:
        coverage does NOT mark that line on call, so even a real call cannot be
        proven. Deliberately fail-closed (conservative, no false green) — same
        safe over-strictness as the single-physical-line case. The dangerous
        direction (import-only → green) is closed; this just escalates an oddly
        formatted but legit function rather than auto-passing it."""
        from servers.coverage import measure_branch_coverage
        (tmp_path / 'cp.py').write_text('def f(\n): return 1\n')
        (tmp_path / 'test_cp.py').write_text(
            'import cp\ndef test_calls_f():\n    assert cp.f() == 1\n')
        targets = [{'file_path': 'cp.py', 'name': 'f',
                    'line_start': 1, 'line_end': 2}]
        res = measure_branch_coverage(str(tmp_path), ['test_cp.py'], targets)
        assert res['fully_covered'] is False, (
            f'must never false-green: {res}')
        assert res['tool_status'] == 'no_targets', (
            f'conservative fail-closed expected, got {res["tool_status"]}')


class TestAstBodyStartLineUnit:
    """Unit tests for _ast_body_start_line helper.

    These tests do NOT require coverage to be installed.
    """

    def test_two_line_def_body_start(self, tmp_path):
        """Two-physical-line def (def on line 1, body on line 2): body_start=2."""
        from servers.coverage import _ast_body_start_line
        src = 'def add(a, b):\n    return a + b\n'
        (tmp_path / 'f.py').write_text(src)
        result = _ast_body_start_line(str(tmp_path / 'f.py'), 'add', 1, 2)
        assert result == 2

    def test_multiline_sig_body_start(self, tmp_path):
        """Multi-line signature: body starts after the closing paren."""
        from servers.coverage import _ast_body_start_line
        src = 'def branchless(\n    value=42,\n):\n    return value\n'
        (tmp_path / 'f.py').write_text(src)
        # def at line 1, body at line 4
        result = _ast_body_start_line(str(tmp_path / 'f.py'), 'branchless', 1, 4)
        assert result == 4

    def test_method_in_class_body_start(self, tmp_path):
        """Method inside a class: ast.walk finds it; body_start correct."""
        from servers.coverage import _ast_body_start_line
        src = 'class Foo:\n    def bar(self):\n        return 1\n'
        (tmp_path / 'f.py').write_text(src)
        # bar is at line 2, body at line 3
        result = _ast_body_start_line(str(tmp_path / 'f.py'), 'bar', 2, 3)
        assert result == 3

    def test_nonexistent_file_returns_none(self, tmp_path):
        """If file doesn't exist, returns None (caller must fail-closed)."""
        from servers.coverage import _ast_body_start_line
        result = _ast_body_start_line(str(tmp_path / 'nonexistent.py'), 'f', 1, 5)
        assert result is None

    def test_name_not_found_returns_none(self, tmp_path):
        """Function name not in file → None."""
        from servers.coverage import _ast_body_start_line
        (tmp_path / 'f.py').write_text('def other(): return 1\n')
        result = _ast_body_start_line(str(tmp_path / 'f.py'), 'missing_fn', 1, 2)
        assert result is None

    def test_parse_error_returns_none(self, tmp_path):
        """Syntax error → ast.parse fails → None (fail-closed)."""
        from servers.coverage import _ast_body_start_line
        (tmp_path / 'f.py').write_text('def f(\n   # missing close paren and body')
        result = _ast_body_start_line(str(tmp_path / 'f.py'), 'f', 1, 5)
        assert result is None
