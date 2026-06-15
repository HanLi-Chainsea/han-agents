"""為可測試性重構：scan / build / run-side helper 測試"""
import os
import sys
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _seed_code(db_path, project, funcs, calls=None):
    """funcs: list of (func_id, name, file_path, line_start, line_end)
       calls: list of (from_id, to_id)  # kind='calls'"""
    conn = sqlite3.connect(db_path)
    for fid, name, fp, ls, le in funcs:
        conn.execute(
            """INSERT INTO code_nodes
               (id, project, kind, name, file_path, line_start, line_end, language)
               VALUES (?,?,?,?,?,?,?,?)""",
            (fid, project, "function", name, fp, ls, le, "python"))
    for frm, to in (calls or []):
        conn.execute(
            """INSERT INTO code_edges (project, from_id, to_id, kind)
               VALUES (?,?,?,?)""", (project, frm, to, "calls"))
    conn.commit()
    conn.close()


class TestDetectHotspots:
    def test_long_method_is_hotspot(self, mock_db_path, monkeypatch, tmp_path):
        from servers import recipes
        monkeypatch.setattr(recipes, "_ensure_synced",
                            lambda p, path: {"test_tool": "pytest"})
        _seed_code(mock_db_path, "rf", [
            ("f.long", "long_fn", "servers/a.py", 1, 80),    # length 79 -> hotspot
            ("f.short", "short_fn", "servers/a.py", 1, 5),   # length 4 -> not
        ])
        spots = recipes._detect_hotspots("rf", None)
        names = [s["name"] for s in spots]
        assert "long_fn" in names
        assert "short_fn" not in names

    def test_high_fanout_is_hotspot(self, mock_db_path, monkeypatch, tmp_path):
        from servers import recipes
        monkeypatch.setattr(recipes, "_ensure_synced",
                            lambda p, path: {"test_tool": "pytest"})
        funcs = [("f.hub", "hub", "servers/b.py", 1, 10)]
        funcs += [(f"f.c{i}", f"c{i}", "servers/b.py", 20 + i, 21 + i)
                  for i in range(9)]
        calls = [("f.hub", f"f.c{i}") for i in range(9)]   # fan_out 9 -> hotspot
        _seed_code(mock_db_path, "rf2", funcs, calls)
        spots = recipes._detect_hotspots("rf2", None)
        hub = [s for s in spots if s["name"] == "hub"]
        assert hub and hub[0]["fan_out"] == 9

    def test_skips_test_files_and_respects_target(self, mock_db_path, monkeypatch):
        from servers import recipes
        monkeypatch.setattr(recipes, "_ensure_synced",
                            lambda p, path: {"test_tool": "pytest"})
        _seed_code(mock_db_path, "rf3", [
            ("f.a", "a_fn", "servers/x.py", 1, 80),
            ("f.b", "b_fn", "other/y.py", 1, 80),
            ("f.t", "t_fn", "tests/test_x.py", 1, 80),
        ])
        spots = recipes._detect_hotspots("rf3", "servers/")
        files = {s["file_path"] for s in spots}
        assert files == {"servers/x.py"}   # excludes other/ and tests/


class TestScanRefactorCandidates:
    def test_truncation_reported(self, mock_db_path, monkeypatch, tmp_path):
        from servers import recipes
        monkeypatch.setattr(recipes, "_ensure_synced",
                            lambda p, path: {"test_tool": "pytest"})
        funcs = [(f"f.{i}", f"fn{i}", "servers/c.py", 1, 100)
                 for i in range(5)]
        _seed_code(mock_db_path, "rf4", funcs)
        r = recipes.scan_refactor_candidates(
            "rf4", str(tmp_path), target_path="servers/", max_candidates=2)
        assert len(r["candidates"]) == 2
        assert r["total_hotspots"] == 5
        assert r["truncated"] is True
        assert "3" in r["message"]   # explicitly states truncated count

    def test_no_hotspots_message(self, mock_db_path, monkeypatch, tmp_path):
        from servers import recipes
        monkeypatch.setattr(recipes, "_ensure_synced",
                            lambda p, path: {"test_tool": "pytest"})
        _seed_code(mock_db_path, "rf5", [("f.s", "s", "servers/d.py", 1, 3)])
        r = recipes.scan_refactor_candidates(
            "rf5", str(tmp_path), target_path="servers/")
        assert r["candidates"] == []
        assert r["truncated"] is False

    def test_max_candidates_clamped(self, mock_db_path, monkeypatch, tmp_path):
        from servers import recipes
        monkeypatch.setattr(recipes, "_ensure_synced",
                            lambda p, path: {"test_tool": "pytest"})
        funcs = [(f"f.{i}", f"fn{i}", "servers/c.py", 1, 100)
                 for i in range(5)]
        _seed_code(mock_db_path, "rf6", funcs)
        # max_candidates=0 must clamp to 1, not misreport "no hotspots"
        r = recipes.scan_refactor_candidates(
            "rf6", str(tmp_path), target_path="servers/", max_candidates=0)
        assert len(r["candidates"]) == 1
        assert r["total_hotspots"] == 5
        assert r["truncated"] is True


class TestBuildRefactorEpic:
    def test_builds_three_step_dependency_chain(self, mock_db_path):
        from servers import recipes
        from servers.tasks import get_next_task, update_task_status
        items = [{
            "file_path": "servers/x.py", "name": "foo",
            "refactor_type": "Extract Method",
            "line_start": 1, "line_end": 80,
        }]
        r = recipes.build_refactor_epic("rfb", items)
        assert r["epic_id"] is not None
        assert r["story_count"] == 1
        assert r["task_count"] == 3

        # story is a child of the epic
        import sqlite3, os
        conn = sqlite3.connect(os.environ["HAN_DB_PATH"])
        story_id = conn.execute(
            "SELECT id FROM tasks WHERE epic_id=? AND task_level='story'",
            (r["epic_id"],)).fetchone()[0]
        conn.close()

        # dependency ordering: first dispatchable task = characterization (no unmet deps)
        t1 = get_next_task(story_id)
        assert "characterization" in t1["description"].lower()
        # before t1 done, refactor/verify must NOT be selected
        update_task_status(t1["id"], "done")
        t2 = get_next_task(story_id)
        assert t2["description"].lower().startswith("refactor for testability")
        update_task_status(t2["id"], "done")
        t3 = get_next_task(story_id)
        assert "verify refactor" in t3["description"].lower()
        update_task_status(t3["id"], "done")
        assert get_next_task(story_id) is None

    def test_empty_items_no_epic(self, mock_db_path):
        from servers import recipes
        r = recipes.build_refactor_epic("rfb2", [])
        assert r["epic_id"] is None
        assert r["task_count"] == 0

    def test_rejects_low_confidence_type(self, mock_db_path):
        from servers import recipes
        r = recipes.build_refactor_epic("rfbX", [{
            "file_path": "servers/x.py", "name": "foo",
            "refactor_type": "Introduce Interface",
            "line_start": 1, "line_end": 80}])
        assert r["epic_id"] is None
        assert r["task_count"] == 0
        assert len(r["rejected"]) == 1

    def test_rejects_missing_fields(self, mock_db_path):
        from servers import recipes
        r = recipes.build_refactor_epic("rfbM", [{
            "file_path": "servers/x.py",
            "refactor_type": "Extract Method",
            "line_start": 1, "line_end": 80}])  # missing name
        assert r["epic_id"] is None
        assert r["task_count"] == 0
        assert len(r["rejected"]) == 1

    def test_line_numbers_in_description(self, mock_db_path):
        from servers import recipes
        import sqlite3, os
        recipes.build_refactor_epic("rfbL", [{
            "file_path": "servers/x.py", "name": "foo",
            "refactor_type": "Extract Method",
            "line_start": 1, "line_end": 80}])
        conn = sqlite3.connect(os.environ["HAN_DB_PATH"])
        descs = [row[0] for row in conn.execute(
            "SELECT description FROM tasks WHERE project='rfbL'").fetchall()]
        conn.close()
        assert any("lines 1-80" in d for d in descs)

    def test_task_descriptions_match_refactor_playbook(self, mock_db_path):
        from servers import recipes
        from servers.playbooks import resolve_playbook
        import sqlite3, os
        recipes.build_refactor_epic("rfb3", [{
            "file_path": "servers/y.py", "name": "bar",
            "refactor_type": "Decompose Conditional",
            "line_start": 1, "line_end": 60}])
        conn = sqlite3.connect(os.environ["HAN_DB_PATH"])
        descs = [row[0] for row in conn.execute(
            "SELECT description FROM tasks WHERE project='rfb3' "
            "AND task_level='task'").fetchall()]
        conn.close()
        assert len(descs) == 3
        for d in descs:
            pb = resolve_playbook(d)
            assert pb is not None and pb.name == "refactor", d


class TestFindLatestPendingEpic:
    def test_returns_latest_pending(self, mock_db_path):
        from servers.tasks import create_task, update_task_status
        from servers.facade import find_latest_pending_epic
        e1 = create_task(project="rfe", description="Refactor for Testability: 1 units",
                         priority=7, task_level="epic")
        e2 = create_task(project="rfe", description="Unit Test Coverage: ...",
                         priority=7, task_level="epic")
        # e2 is the expected one: give it a pending child task so it has undone work
        create_task(project="rfe", description="t",
                    priority=7, task_level="task", epic_id=e2)
        # e1 set to done -> should return the latest pending with work, e2
        update_task_status(e1, "done")
        got = find_latest_pending_epic("rfe")
        assert got is not None and got["id"] == e2

    def test_none_when_no_pending(self, mock_db_path):
        from servers.tasks import create_task, update_task_status
        from servers.facade import find_latest_pending_epic
        e = create_task(project="rfe2", description="X", priority=7, task_level="epic")
        update_task_status(e, "done")
        assert find_latest_pending_epic("rfe2") is None

    def test_latest_among_multiple_pending(self, mock_db_path):
        from servers.tasks import create_task
        from servers.facade import find_latest_pending_epic
        e1 = create_task(project="rfe3", description="epic one",
                         priority=7, task_level="epic")
        e2 = create_task(project="rfe3", description="epic two",
                         priority=7, task_level="epic")
        # both pending and each has a pending child task; the later-created epic
        # (e2) must win even on same-second created_at
        create_task(project="rfe3", description="t1",
                    priority=7, task_level="task", epic_id=e1)
        create_task(project="rfe3", description="t2",
                    priority=7, task_level="task", epic_id=e2)
        got = find_latest_pending_epic("rfe3")
        assert got is not None and got["id"] == e2

    def test_skips_epic_whose_tasks_all_done(self, mock_db_path):
        from servers.tasks import create_task, update_task_status
        from servers.facade import find_latest_pending_epic
        # e_done: a pending epic whose only child task is done -> must be skipped
        e_done = create_task(project="rfe4", description="completed epic",
                             priority=7, task_level="epic")
        t_done = create_task(project="rfe4", description="done task",
                             priority=7, task_level="task", epic_id=e_done)
        update_task_status(t_done, "done")
        # if it were the only epic, result must be None (regression for re-select bug)
        assert find_latest_pending_epic("rfe4") is None

        # now add a genuinely pending epic with undone work -> THAT one must win,
        # proving the all-done epic no longer shadows it
        e_real = create_task(project="rfe4", description="real pending epic",
                             priority=7, task_level="epic")
        create_task(project="rfe4", description="pending task",
                    priority=7, task_level="task", epic_id=e_real)
        got = find_latest_pending_epic("rfe4")
        assert got is not None and got["id"] == e_real
