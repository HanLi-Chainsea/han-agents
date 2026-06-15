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
