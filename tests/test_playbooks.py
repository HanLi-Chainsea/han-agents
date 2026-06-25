"""Playbook 載入、分類、格式化、fail-open 測試"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestLoadPlaybooks:
    def test_loads_playbooks(self):
        from servers.playbooks import load_playbooks
        pbs = load_playbooks()
        names = {pb.name for pb in pbs.values()}
        assert {"unit_test", "code_review", "integration_test", "e2e_test"}.issubset(names)

    def test_playbook_has_sections(self):
        from servers.playbooks import load_playbooks
        pbs = load_playbooks()
        ut = pbs["unit_test"]
        assert ut.match  # 非空關鍵字列表
        assert "AAA" in ut.executor_principles or "Arrange" in ut.executor_principles
        assert "REJECT" in ut.critic_checklist

    def test_refactor_playbook_loaded(self):
        from servers.playbooks import load_playbooks
        pbs = load_playbooks(force_reload=True)
        assert "refactor" in pbs
        rf = pbs["refactor"]
        assert rf.match
        assert "Extract Method" in rf.executor_principles
        assert "characterization" in rf.executor_principles.lower()
        assert "build.gradle" in rf.executor_principles
        assert "REJECT" in rf.critic_checklist

    def test_unit_test_playbook_covers_null_state(self):
        # 同事使用回饋：可為 null/None 的狀態，即使規格沒寫明也常有對應行為，
        # 漏測等於放掉一整類迴歸。playbook 必須明確「要求釘住」null 行為（executor + critic）。
        # 斷言語意而非僅字串出現，避免「不用測 null」之類反向文字誤綠。
        from servers.playbooks import load_playbooks
        pbs = load_playbooks(force_reload=True)
        ut = pbs["unit_test"]
        exec_null = [ln for ln in ut.executor_principles.splitlines() if "null" in ln.lower()]
        assert exec_null, "executor principles 未提及 null"
        # 必須是「要求釘住 null 行為」的正向指示
        assert any("釘住" in ln for ln in exec_null)
        critic_null = [ln for ln in ut.critic_checklist.splitlines() if "null" in ln.lower()]
        assert critic_null, "critic checklist 未提及 null"
        # critic 的 null 規則必須帶 REJECT 後果，否則形同無效
        assert any("REJECT" in ln for ln in critic_null)


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

    def test_e2e_test_match(self):
        from servers.playbooks import resolve_playbook
        pb = resolve_playbook("Write E2E tests for the checkout journey")
        assert pb is not None and pb.name == "e2e_test"

    def test_e2e_chinese_match(self):
        from servers.playbooks import resolve_playbook
        pb = resolve_playbook("幫登入到結帳做端對端測試")
        assert pb is not None and pb.name == "e2e_test"

    def test_integration_not_misclassified_as_e2e(self):
        from servers.playbooks import resolve_playbook
        pb = resolve_playbook("Write integration tests for auth module")
        assert pb is not None and pb.name == "integration_test"

    def test_no_match_returns_none(self):
        from servers.playbooks import resolve_playbook
        assert resolve_playbook("Fix bug in parser logic") is None

    def test_refactor_three_step_descriptions_match(self):
        from servers.playbooks import resolve_playbook
        descs = [
            "Write characterization tests pinning current behavior of foo in servers/x.py (refactor-for-testability safety net). Do not judge correctness; pin every branch's current behavior.",
            "Refactor for testability: apply Extract Method to foo in servers/x.py. Behavior-preserving, mechanical.",
            "Verify refactor of foo in servers/x.py: rerun characterization tests, must stay green.",
        ]
        for d in descs:
            pb = resolve_playbook(d)
            assert pb is not None and pb.name == "refactor", d


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


class TestParseRobustness:
    def test_bom_and_leading_blank_lines(self):
        from servers.playbooks import _parse_playbook
        raw = '﻿\n\n---\nname: x\nmatch: ["foo"]\n---\n## Executor Principles\n- a\n## Critic Checklist\n- b\n'
        pb = _parse_playbook(raw)
        assert pb is not None
        assert pb.name == "x"
        assert pb.match == ["foo"]
        assert "a" in pb.executor_principles

    def test_load_playbooks_failopen_on_listdir_error(self, monkeypatch):
        import servers.playbooks as pbmod
        monkeypatch.setattr(pbmod, "_CACHE", None)
        # _PLAYBOOK_DIR 指向存在的目錄，但讓 listdir 拋錯（模擬權限/race）
        monkeypatch.setattr(pbmod.os.path, "isdir", lambda p: True)
        def boom(p):
            raise PermissionError("denied")
        monkeypatch.setattr(pbmod.os, "listdir", boom)
        assert pbmod.load_playbooks(force_reload=True) == {}
        assert pbmod.resolve_playbook("write unit tests for x") is None


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

    def test_unit_test_executor_prompt_injects_null_principle(self):
        # null 原則必須真的進到注入給 executor 的 prompt，不只是躺在 playbook 檔裡
        from servers.facade import _build_executor_prompt
        task = {"id": "t3", "description": "Write unit tests for servers/memory.py",
                "assigned_agent": "executor"}
        prompt = _build_executor_prompt(task, "proj", "/tmp/proj")
        assert "null" in prompt.lower()

    def test_unit_test_critic_prompt_injects_null_rule(self):
        # 對稱：null 的 REJECT 規則也必須進到注入給 critic 的 prompt
        from servers.facade import _build_critic_prompt
        critic_task = {"id": "c3", "original_task_id": "t3",
                       "original_description": "Write unit tests for servers/memory.py",
                       "result": "done"}
        prompt = _build_critic_prompt(critic_task, "proj", "/tmp/proj")
        assert "null" in prompt.lower()


class TestUnitTestCommandEnvInlining:
    """回歸防護：/han:unit-test 指令的 env 必須 inline，不能靠不跨呼叫保留的獨立 export。"""

    def _command_text(self):
        here = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(os.path.dirname(here), "commands", "han", "unit-test.md")
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_no_standalone_export_of_han_vars(self):
        import re
        for ln in self._command_text().splitlines():
            assert not re.match(r"\s*export\s+HAN_", ln), f"殘留無效的獨立 export：{ln}"

    def test_every_python_block_inlines_project_env(self):
        # 每個 `python3 - <<'PY'` 區塊都呼叫 os.environ['HAN_PROJECT'/'HAN_PROJECT_PATH']，
        # 故其啟動行必須在 `python3` 之前 inline 帶上這兩個值（shell state 不跨 Bash 呼叫保留）。
        # 只看 `python3` 前綴，避免被後方註解或字串誤綠。
        import re
        starts = [ln for ln in self._command_text().splitlines() if "python3 - <<'PY'" in ln]
        assert len(starts) >= 2, "預期至少兩個 python heredoc 區塊"
        for ln in starts:
            prefix = ln.split("python3", 1)[0]
            assert re.search(r"\bHAN_PROJECT=", prefix), f"區塊未在 python3 前 inline HAN_PROJECT：{ln}"
            assert re.search(r"\bHAN_PROJECT_PATH=", prefix), f"區塊未在 python3 前 inline HAN_PROJECT_PATH：{ln}"

    def test_user_derived_target_is_single_quoted(self):
        # HAN_TARGET 是唯一來自使用者輸入、會進 shell 的值；必須以單引號包住，shell 不展開（防注入）。
        import re
        target_lines = [ln for ln in self._command_text().splitlines()
                        if "HAN_TARGET=" in ln and "python3" in ln]
        assert target_lines, "找不到帶 HAN_TARGET 的 python 啟動行"
        for ln in target_lines:
            assert re.search(r"HAN_TARGET='[^']*'", ln), f"HAN_TARGET 未以單引號包住：{ln}"


class TestUnitTestCommandUsesGatedDispatch:
    def _command_text(self):
        here = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(os.path.dirname(here), "commands", "han", "unit-test.md")
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_loop_imports_gated_dispatch(self):
        text = self._command_text()
        assert "get_next_dispatch_gated" in text, "迴圈未改用 gated dispatch"

    def test_gated_dispatch_block_inlines_project_env(self):
        # gate 需要 project_path 真跑 coverage → 該 python 區塊必須 inline HAN_PROJECT_PATH
        import re
        for ln in self._command_text().splitlines():
            if "get_next_dispatch_gated" in ln and "import" not in ln:
                pass
        starts = [ln for ln in self._command_text().splitlines()
                  if "python3 - <<'PY'" in ln and "HAN_EPIC=" in ln]
        assert starts, "找不到帶 HAN_EPIC 的派工迴圈 python 區塊"
        for ln in starts:
            prefix = ln.split("python3", 1)[0]
            assert "HAN_PROJECT_PATH=" in prefix
            assert "HAN_PROJECT=" in prefix


class TestUnitTestPlaybookBranchCoverage:
    def _pb(self):
        from servers.playbooks import load_playbooks
        return load_playbooks(force_reload=True)["unit_test"]

    def test_executor_requires_every_branch_and_reports_test_targets(self):
        ep = self._pb().executor_principles
        assert "分支" in ep
        assert "TEST_TARGETS" in ep          # 結構化回報 marker
        assert "pragma" in ep.lower()         # 不可達分支說明

    def test_critic_notes_tool_enforced_coverage(self):
        cc = self._pb().critic_checklist
        assert "分支" in cc
        # 工具不可用時 critic 要手動核對
        assert "工具" in cc


class TestCompactEvidenceBlock:
    """Token-saving: executor emits compact evidence; critic reads actual test files."""

    def _executor_prompt(self, desc="Write unit tests for servers/memory.py"):
        from servers.facade import _build_executor_prompt
        task = {"id": "t-ev1", "description": desc, "assigned_agent": "executor"}
        return _build_executor_prompt(task, "proj", "/tmp/proj")

    def _critic_prompt(self, result="TEST_TARGETS: tests/test_x.py\nRESULT: PASS 5\nCHANGED: tests/test_x.py\nCMD: pytest tests/test_x.py"):
        from servers.facade import _build_critic_prompt
        critic_task = {"id": "c-ev1", "original_task_id": "t-ev1",
                       "original_description": "Write unit tests for servers/memory.py",
                       "result": result}
        return _build_critic_prompt(critic_task, "proj", "/tmp/proj")

    def test_executor_prompt_requires_compact_evidence_block(self):
        """Executor prompt must instruct the four-line compact evidence block."""
        prompt = self._executor_prompt()
        # All four required field labels must be present
        assert "TEST_TARGETS:" in prompt
        assert "RESULT:" in prompt
        assert "CHANGED:" in prompt
        assert "CMD:" in prompt
        # Must tell executor NOT to reproduce/paste the full test file
        prompt_lower = prompt.lower()
        assert "do not reproduce" in prompt_lower or "not reproduce" in prompt_lower or \
               "replaces verbose prose" in prompt_lower or "the block replaces" in prompt_lower
        # Must still require actually running the tests
        assert "run" in prompt_lower or "execute" in prompt_lower or "actually" in prompt_lower

    def test_critic_prompt_instructs_read_test_files(self):
        """Critic prompt must instruct reading the test file(s) listed in TEST_TARGETS."""
        prompt = self._critic_prompt()
        prompt_lower = prompt.lower()
        # Must mention TEST_TARGETS
        assert "test_targets" in prompt_lower
        # Must instruct reading files (relative to PROJECT_PATH)
        assert "read" in prompt_lower
        assert "project_path" in prompt_lower
        # Result text is still present (compact form)
        assert "TEST_TARGETS:" in prompt

    def test_critic_prompt_fail_closed_on_missing_evidence(self):
        """Critic prompt must explicitly say to REJECT when evidence is missing."""
        prompt = self._critic_prompt()
        # Must contain a fail-closed REJECT instruction
        assert "REJECTED" in prompt or "REJECT" in prompt
        # Must cover: missing TEST_TARGETS line
        assert "no TEST_TARGETS" in prompt or "no test_targets" in prompt.lower() or \
               "TEST_TARGETS: line" in prompt or "test_targets: line" in prompt.lower()
        # Must cover: file cannot be read
        assert "cannot be read" in prompt or "unreadable" in prompt
        # Must cover: no real assertions (assert True / empty bodies)
        assert "assert True" in prompt or "empty" in prompt.lower()
        # Must explicitly state: NEVER approve without evidence
        assert "NEVER" in prompt or "never" in prompt

    def test_critic_prompt_still_has_guardrail_and_checklist(self):
        """Guardrail policy section and verdict output format must still be present."""
        prompt = self._critic_prompt()
        # Verdict output format preserved
        assert "## 驗證結果: APPROVED" in prompt
        assert "## 驗證結果: REJECTED" in prompt
        # Checklist still injected for unit_test description (playbook section)
        assert "REJECT" in prompt  # playbook critic checklist has REJECT conditions
        # 'fail-closed' label or guardrail section present
        assert "fail-closed" in prompt.lower() or "FAIL-CLOSED" in prompt


class TestCriticPromptInstructsActionableIssues:
    """Fix B regression guard: critic prompt must instruct finish_validation
    with specific, actionable issues on REJECT/CONDITIONAL."""

    def _critic_prompt(self):
        from servers.facade import _build_critic_prompt
        critic_task = {
            "id": "c-fv1", "original_task_id": "t-fv1",
            "original_description": "Write unit tests for servers/memory.py",
            "result": "TEST_TARGETS: tests/test_x.py\nRESULT: PASS 3\nCHANGED: tests/test_x.py\nCMD: pytest tests/test_x.py"
        }
        return _build_critic_prompt(critic_task, "proj", "/tmp/proj")

    def test_critic_prompt_instructs_finish_validation_with_actionable_issues(self):
        """Critic prompt must explicitly instruct calling finish_validation with
        a specific, actionable issues list on REJECT (not vague labels)."""
        prompt = self._critic_prompt()
        prompt_lower = prompt.lower()

        # Must mention finish_validation call
        assert 'finish_validation' in prompt, (
            'critic prompt must instruct calling finish_validation')

        # Must show approved=False pattern for reject
        assert 'approved=False' in prompt, (
            'critic prompt must show approved=False for reject')

        # Must include issues= parameter instruction
        assert 'issues=' in prompt, (
            'critic prompt must instruct passing issues= to finish_validation')

        # Must warn against vague issues
        assert 'vague' in prompt_lower or 'not acceptable' in prompt_lower, (
            'critic prompt must warn that vague issues are not acceptable')

        # Must instruct actionable / specific items
        assert ('actionable' in prompt_lower or 'specific' in prompt_lower
                or 'concrete' in prompt_lower), (
            'critic prompt must instruct specific/actionable issues')

        # Existing content still intact
        assert '## 驗證結果: APPROVED' in prompt
        assert '## 驗證結果: REJECTED' in prompt
        assert 'finish_validation' in prompt
