"""cli_views —— /han 唯讀指令的查詢+格式化，鎖底層 API 欄位契約。

設計原則：斷言**實際值**（非只標籤），且 impact/recall/blast 用真實 API（fixtures
走真 schema），所以「cli_views 讀錯欄位」或「底層 API 改欄位名」都會讓測試紅。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestFmtSync:
    def test_locks_every_sync_field(self):
        """6 個數值欄位 + errors 的值都必須出現——cli_views 漏讀任一鍵即失敗。"""
        from servers.cli_views import _fmt_sync
        s = _fmt_sync({"files_processed": 3, "files_skipped": 1, "nodes_added": 5,
                       "nodes_updated": 2, "edges_added": 7, "duration_ms": 42,
                       "errors": ["boom"]})
        assert "files_processed=3" in s     # files_processed
        assert "+5" in s                     # nodes_added
        assert "~2" in s                     # nodes_updated
        assert "+7" in s                     # edges_added
        assert "skipped=1" in s              # files_skipped
        assert "42ms" in s                   # duration_ms
        assert "errors=1" in s               # errors

    def test_handles_none(self):
        from servers.cli_views import _fmt_sync
        assert isinstance(_fmt_sync(None), str)


class TestImpactReport:
    def test_locks_dep_field_values(self, sample_code_graph):
        # authenticate --calls--> validate_token；斷言實際 kind/relation/depth 值
        from servers.cli_views import impact_report
        out = impact_report("test", "src/auth/login.py")
        assert "authenticate" in out and "validate_token" in out
        assert "(function)" in out           # dependency 的 kind 值
        assert "via calls" in out            # relation 值
        assert "[深度 1]" in out             # depth 值（incoming 分支）
        assert "扇入" in out and "扇出" in out

    def test_missing_dep_key_raises(self, sample_code_graph, monkeypatch):
        """若 get_code_dependencies 改欄位名（少了 kind），impact_report 應 KeyError 而非靜默。"""
        import servers.cli_views as cv
        from servers import code_graph
        real = code_graph.get_code_dependencies

        def drifted(*a, **k):
            return [{kk: vv for kk, vv in d.items() if kk != "kind"} for d in real(*a, **k)]
        monkeypatch.setattr(code_graph, "get_code_dependencies", drifted)
        import pytest
        with pytest.raises(KeyError):
            cv.impact_report("test", "src/auth/login.py")

    def test_not_found(self, sample_code_graph):
        from servers.cli_views import impact_report
        assert "找不到目標節點" in impact_report("test", "no_such_symbol_xyz")

    def test_truncated_and_not_found_warns(self, monkeypatch):
        """全庫掃描達上限且找不到目標時，必須附截斷警告（不可被提前 return 吃掉）。"""
        import servers.cli_views as cv
        from servers import code_graph

        def fake_nodes(project, kind=None, file_path=None, limit=100, offset=0):
            if file_path:
                return []  # 路徑查無
            return [{"id": f"n{i}", "kind": "function", "name": f"n{i}", "file_path": "x"}
                    for i in range(cv._NODE_LIMIT)]  # 掃描達上限、無一匹配
        monkeypatch.setattr(code_graph, "get_code_nodes", fake_nodes)
        out = cv.impact_report("p", "zzz_target")
        assert "找不到目標節點" in out
        assert "未掃描範圍" in out  # 截斷警告


class TestBlastRadius:
    def test_returns_summary(self, sample_code_graph):
        from servers.cli_views import blast_radius
        out = blast_radius("test", "src/auth/login.py")
        assert "authenticate" in out and "func.src/auth/login.py:authenticate" in out


class TestRecallReport:
    def test_locks_content_value(self, mock_db_path):
        from servers.memory import store_memory
        store_memory(category="lesson", content="Always validate token expiry first",
                     title="Token validation", project="rp")
        from servers.cli_views import recall_report
        out = recall_report("rp", "token")
        assert "[lesson]" in out                       # category 值
        assert "Token validation" in out               # title 值
        assert "Always validate token expiry" in out   # content 值（漏讀 content 即失敗）

    def test_no_memory_message(self, mock_db_path):
        from servers.cli_views import recall_report
        assert "無相關記憶" in recall_report("rp2", "zzz_nonexistent_topic")


class TestSyncAndInitMapping:
    def test_init_report_surfaces_all_fields(self, monkeypatch):
        """monkeypatch ensure_project 回已知 dict，斷言每個欄位的值都出現——
        cli_views 讀錯鍵（如 framework 單數）即失敗。"""
        import servers.cli_views as cv
        from servers import project
        monkeypatch.setattr(project, "ensure_project", lambda p, path: {
            "already_initialized": True,
            "tech_stack": {"primary_language": "python",
                           "frameworks": ["FastAPI"], "test_tool": "pytest"},
            "sync_result": {"files_processed": 9, "files_skipped": 0, "nodes_added": 11,
                            "nodes_updated": 0, "edges_added": 4, "duration_ms": 7, "errors": []},
        })
        out = cv.init_report("p", "/x")
        assert "previously_initialized: True" in out
        assert "python" in out                 # primary_language
        assert "FastAPI" in out                # frameworks（複數鍵）
        assert "pytest" in out                 # test_tool
        assert "files_processed=9" in out and "+11" in out   # sync_result

    def test_sync_report_uses_real_keys(self, monkeypatch):
        import servers.cli_views as cv
        from servers import facade
        monkeypatch.setattr(facade, "sync", lambda path, proj, incremental=True: {
            "files_processed": 2, "files_skipped": 1, "nodes_added": 3, "nodes_updated": 1,
            "edges_added": 6, "duration_ms": 5, "errors": []})
        out = cv.sync_report("/x", "p")
        assert "files_processed=2" in out and "+3" in out and "+6" in out


class TestStringWrappers:
    def test_status_report_is_str(self, sample_code_graph, tmp_path):
        from servers.cli_views import status_report
        assert isinstance(status_report(str(tmp_path)), str)

    def test_drift_report_is_str(self, sample_graph_data):
        from servers.cli_views import drift_report
        assert isinstance(drift_report("test"), str)
