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
