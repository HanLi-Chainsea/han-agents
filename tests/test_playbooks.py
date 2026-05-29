"""Playbook 載入、分類、格式化、fail-open 測試"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestLoadPlaybooks:
    def test_loads_three_playbooks(self):
        from servers.playbooks import load_playbooks
        pbs = load_playbooks()
        names = {pb.name for pb in pbs.values()}
        assert {"unit_test", "code_review", "integration_test"}.issubset(names)

    def test_playbook_has_sections(self):
        from servers.playbooks import load_playbooks
        pbs = load_playbooks()
        ut = pbs["unit_test"]
        assert ut.match  # 非空關鍵字列表
        assert "AAA" in ut.executor_principles or "Arrange" in ut.executor_principles
        assert "REJECT" in ut.critic_checklist


class TestResolvePlaybook:
    def test_unit_test_match(self):
        from servers.playbooks import resolve_playbook
        pb = resolve_playbook("Write unit tests for servers/memory.py")
        assert pb is not None and pb.name == "unit_test"

    def test_code_review_match(self):
        from servers.playbooks import resolve_playbook
        pb = resolve_playbook("Code review the diff against main")
        assert pb is not None and pb.name == "code_review"

    def test_integration_test_match(self):
        from servers.playbooks import resolve_playbook
        pb = resolve_playbook("Write integration tests for auth module")
        assert pb is not None and pb.name == "integration_test"

    def test_no_match_returns_none(self):
        from servers.playbooks import resolve_playbook
        assert resolve_playbook("Fix bug in parser logic") is None


class TestFailOpen:
    def test_missing_dir_returns_empty(self, monkeypatch):
        import servers.playbooks as pbmod
        monkeypatch.setattr(pbmod, "_PLAYBOOK_DIR", "/nonexistent/path/xyz")
        monkeypatch.setattr(pbmod, "_CACHE", None)
        assert pbmod.load_playbooks(force_reload=True) == {}
        assert pbmod.resolve_playbook("write unit tests for x") is None
