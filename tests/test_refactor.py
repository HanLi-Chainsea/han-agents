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
