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


class TestFormatSections:
    def test_executor_section_format(self):
        from servers.playbooks import load_playbooks, executor_section
        ut = load_playbooks()["unit_test"]
        text = executor_section(ut)
        assert text.startswith("## Playbook: unit_test — Principles\n\n")
        assert text.endswith("\n")

    def test_critic_section_format(self):
        from servers.playbooks import load_playbooks, critic_section
        ut = load_playbooks()["unit_test"]
        text = critic_section(ut)
        assert text.startswith("## Playbook: unit_test — Checklist\n\n")

    def test_empty_sections_return_empty_string(self):
        from servers.playbooks import Playbook, executor_section, critic_section
        pb = Playbook(name="empty")
        assert executor_section(pb) == ""
        assert critic_section(pb) == ""

    def test_malformed_match_does_not_crash_resolve(self):
        # fail-open: a playbook with bad match must not crash resolve_playbook
        from servers.playbooks import _parse_playbook
        pb = _parse_playbook('---\nname: bad\nmatch: [1, 2, 3]\n---\n## Executor Principles\nx\n')
        assert pb is not None
        assert pb.match == []


class TestPromptInjection:
    def test_executor_prompt_has_unit_test_principles(self):
        from servers.facade import _build_executor_prompt
        task = {"id": "t1", "description": "Write unit tests for servers/memory.py",
                "assigned_agent": "executor"}
        prompt = _build_executor_prompt(task, "proj", "/tmp/proj")
        assert "AAA" in prompt or "Arrange" in prompt
        assert "FIRST" in prompt

    def test_critic_prompt_requires_tests_run(self):
        from servers.facade import _build_critic_prompt
        critic_task = {"id": "c1", "original_task_id": "t1",
                       "original_description": "Write unit tests for x",
                       "result": "done"}
        prompt = _build_critic_prompt(critic_task, "proj", "/tmp/proj")
        assert "實際被執行" in prompt

    def test_non_test_task_has_no_test_principles(self):
        from servers.facade import _build_executor_prompt
        task = {"id": "t2", "description": "Fix bug in parser logic",
                "assigned_agent": "executor"}
        prompt = _build_executor_prompt(task, "proj", "/tmp/proj")
        assert "FIRST" not in prompt
        assert "Beyoncé" not in prompt
