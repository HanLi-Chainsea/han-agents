"""cli_views —— /han 唯讀指令的查詢+格式化，鎖底層 API 欄位契約。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestFmtSync:
    def test_formats_real_sync_keys(self):
        from servers.cli_views import _fmt_sync
        s = _fmt_sync({"files_processed": 3, "files_skipped": 1, "nodes_added": 5,
                       "nodes_updated": 2, "edges_added": 7, "duration_ms": 42, "errors": []})
        assert "files_processed=3" in s and "+5" in s and "+7" in s and "42ms" in s

    def test_handles_none(self):
        from servers.cli_views import _fmt_sync
        assert isinstance(_fmt_sync(None), str)


class TestImpactReport:
    def test_shows_incoming_and_outgoing(self, sample_code_graph):
        # sample graph: authenticate --calls--> validate_token，同檔
        from servers.cli_views import impact_report
        out = impact_report("test", "src/auth/login.py")
        assert "authenticate" in out and "validate_token" in out
        assert "扇入" in out and "扇出" in out

    def test_symbol_lookup(self, sample_code_graph):
        from servers.cli_views import impact_report
        out = impact_report("test", "authenticate")
        # authenticate 扇出 validate_token via calls
        assert "validate_token" in out
        assert "calls" in out

    def test_not_found(self, sample_code_graph):
        from servers.cli_views import impact_report
        assert "找不到目標節點" in impact_report("test", "no_such_symbol_xyz")


class TestBlastRadius:
    def test_returns_summary(self, sample_code_graph):
        from servers.cli_views import blast_radius
        out = blast_radius("test", "src/auth/login.py")
        assert "authenticate" in out


class TestRecallReport:
    def test_formats_found_memory(self, mock_db_path):
        from servers.memory import store_memory
        store_memory(category="lesson", content="Always validate token expiry first",
                     title="Token validation", project="rp")
        from servers.cli_views import recall_report
        out = recall_report("rp", "token")
        assert "[lesson]" in out and "Token validation" in out

    def test_no_memory_message(self, mock_db_path):
        from servers.cli_views import recall_report
        assert "無相關記憶" in recall_report("rp2", "zzz_nonexistent_topic")


class TestStringWrappers:
    def test_status_report_is_str(self, sample_code_graph, tmp_path):
        from servers.cli_views import status_report
        assert isinstance(status_report(str(tmp_path)), str)

    def test_drift_report_is_str(self, sample_graph_data):
        from servers.cli_views import drift_report
        assert isinstance(drift_report("test"), str)


class TestInitReport:
    def test_exposes_tech_stack_fields(self, mock_db_path, tmp_path):
        # 空目錄：ensure_project 會建專案 + 空 tech_stack；驗證欄位存取正確（frameworks 複數）
        from servers.cli_views import init_report
        out = init_report("ip", str(tmp_path))
        assert "previously_initialized:" in out
        assert "frameworks:" in out        # 必須是複數鍵
        assert "test_tool:" in out
        assert "code_graph:" in out
