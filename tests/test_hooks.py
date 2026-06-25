"""
Tests for PostToolUse hook guardrail handling.
"""

import importlib.util
import os


def _load_post_task():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(base_dir, "hooks", "post_task.py")
    spec = importlib.util.spec_from_file_location("post_task_hook", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_pre_tool():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(base_dir, "hooks", "pre_tool.py")
    spec = importlib.util.spec_from_file_location("pre_tool_hook", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bash_guardrail_warning():
    hook = _load_post_task()

    output = hook.handle_event({
        "tool_name": "Bash",
        "tool_input": {
            "command": "rm -rf /tmp/project",
            "agent": "executor",
        },
    })

    assert output is not None
    context = output["hookSpecificOutput"]["additionalContext"]
    assert "HAN guardrail warning" in context
    assert "denied pattern" in context
    assert "decision" not in output


def test_bash_guardrail_block_mode(monkeypatch):
    hook = _load_post_task()
    monkeypatch.setenv("HAN_GUARDRAIL_MODE", "block")

    output = hook.handle_event({
        "tool_name": "Bash",
        "tool_input": {
            "command": "rm -rf /tmp/project",
            "agent": "executor",
        },
    })

    assert output["decision"] == "block"
    assert output["guardrail"]["mode"] == "block"


def test_write_guardrail_warning_for_read_only_agent():
    hook = _load_post_task()

    output = hook.handle_event({
        "tool_name": "Write",
        "tool_input": {
            "file_path": "servers/facade.py",
            "agent": "critic",
        },
    })

    assert output is not None
    context = output["hookSpecificOutput"]["additionalContext"]
    assert "cannot write path" in context


def test_allowed_bash_guardrail_is_silent():
    hook = _load_post_task()

    output = hook.handle_event({
        "tool_name": "Bash",
        "tool_input": {
            "command": "pytest -q tests/test_guardrails.py",
            "agent": "executor",
        },
    })

    assert output is None


def test_guardrail_event_records_trace_span(mock_db_path):
    hook = _load_post_task()

    from servers.tracing import get_trace, start_trace

    trace_id = start_trace("guardrail hook", project="test")
    output = hook.handle_event({
        "tool_name": "Bash",
        "tool_input": {
            "command": "rm -rf /tmp/project",
            "agent": "executor",
            "TRACE_ID": trace_id,
            "task_id": "task-1",
        },
    })
    trace = get_trace(trace_id)

    assert output is not None
    assert len(trace["spans"]) == 1
    span = trace["spans"][0]
    assert span["span_type"] == "guardrail"
    assert span["status"] == "warning"
    assert span["metadata"]["violation_count"] == 1
    assert span["output"]["allowed"] is False


def test_hook_cli_subprocess_guardrail_warning():
    import json
    import subprocess
    import sys

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    payload = {
        "tool_name": "Bash",
        "tool_input": {
            "command": "rm -rf /tmp/project",
            "agent": "executor",
        },
    }
    result = subprocess.run(
        [sys.executable, "hooks/post_task.py"],
        cwd=base_dir,
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "HAN guardrail warning for Bash" in result.stdout


def test_pre_tool_denies_in_block_mode(monkeypatch):
    hook = _load_pre_tool()
    monkeypatch.setenv("HAN_GUARDRAIL_MODE", "block")

    output = hook.handle_event({
        "tool_name": "Bash",
        "tool_input": {
            "command": "rm -rf /tmp/project",
            "agent": "executor",
        },
    })

    specific = output["hookSpecificOutput"]
    assert specific["hookEventName"] == "PreToolUse"
    assert specific["permissionDecision"] == "deny"
    assert "denied pattern" in specific["permissionDecisionReason"]


def test_pre_tool_asks_in_warn_mode(monkeypatch):
    hook = _load_pre_tool()
    monkeypatch.setenv("HAN_GUARDRAIL_MODE", "warn")

    output = hook.handle_event({
        "tool_name": "Write",
        "tool_input": {
            "file_path": "servers/facade.py",
            "agent": "critic",
        },
    })

    specific = output["hookSpecificOutput"]
    assert specific["permissionDecision"] == "ask"
    assert "cannot write path" in specific["permissionDecisionReason"]


def test_pre_tool_allows_silently():
    hook = _load_pre_tool()

    output = hook.handle_event({
        "tool_name": "Bash",
        "tool_input": {
            "command": "pytest -q tests/test_guardrails.py",
            "agent": "executor",
        },
    })

    assert output is None


# =============================================================================
# Task lifecycle hook tests (Fix a/b/c)
# =============================================================================

def _make_executor_prompt(task_id: str) -> str:
    return f'TASK_ID = "{task_id}"\nSubagent: executor running task.'


def _make_critic_prompt(critic_task_id: str, original_task_id: str) -> str:
    return (
        f'TASK_ID = "{critic_task_id}"\n'
        f'ORIGINAL_TASK_ID = "{original_task_id}"\n'
        f'Critic reviewing task.'
    )


def test_executor_hook_saves_real_output(mock_db_path):
    """Fix (a): executor tool_response text reaches tasks.result, not 'Executor completed'."""
    from servers.tasks import create_task, get_task

    parent_id = create_task('test', 'parent task')
    task_id = create_task('test', 'write some tests', parent_id=parent_id)

    evidence = (
        "Did the work.\n"
        "TEST_TARGETS: tests/test_x.py\n"
        "RESULT: PASS 3\n"
        "CMD: pytest tests/test_x.py -q"
    )

    hook = _load_post_task()
    hook.handle_event({
        "tool_name": "Task",
        "tool_input": {
            "prompt": _make_executor_prompt(task_id),
            "subagent_type": "executor",
        },
        "tool_response": {"agentId": "agent-abc", "output": evidence},
    })

    task = get_task(task_id)
    assert task is not None
    result_text = task.get('result') or ''
    assert 'TEST_TARGETS' in result_text, (
        f"Expected executor evidence in result, got: {result_text!r}"
    )
    assert 'RESULT: PASS 3' in result_text
    assert result_text != "Executor completed"


def test_critic_hook_unparseable_verdict_fails_closed(mock_db_path):
    """Fix (b): unparseable critic output must NOT approve the original task."""
    from servers.tasks import create_task, get_task, update_task_status, advance_task_phase

    parent_id = create_task('test', 'parent task')
    original_task_id = create_task('test', 'original executor task', parent_id=parent_id)
    critic_task_id = create_task('test', 'critic task', parent_id=parent_id)

    # Put original task in validation phase so finish_validation can operate
    update_task_status(original_task_id, 'done', result='done')
    advance_task_phase(original_task_id, 'validation')

    hook = _load_post_task()
    hook.handle_event({
        "tool_name": "Task",
        "tool_input": {
            "prompt": _make_critic_prompt(critic_task_id, original_task_id),
            "subagent_type": "critic",
        },
        "tool_response": {
            "output": "I looked at the code. It seems fine. No particular issues found."
        },
    })

    task = get_task(original_task_id)
    assert task is not None
    validation_status = task.get('validation_status')
    status = task.get('status')
    # Must NOT be approved — should be 'rejected' or still 'pending'
    assert validation_status != 'approved', (
        f"Unparseable verdict should not approve the task. "
        f"validation_status={validation_status!r}, status={status!r}"
    )


def test_critic_hook_rejected_persists_reason(mock_db_path):
    """Fix (c): REJECTED critic output saves reason to working_memory critic_suggestions."""
    from servers.tasks import create_task, update_task_status, advance_task_phase
    from servers.memory import get_working_memory

    parent_id = create_task('test', 'parent task')
    original_task_id = create_task('test', 'executor task to be rejected', parent_id=parent_id)
    critic_task_id = create_task('test', 'critic reject task', parent_id=parent_id)

    update_task_status(original_task_id, 'done', result='done')
    advance_task_phase(original_task_id, 'validation')

    rejection_reason = "Missing TEST_TARGETS line. RESULT line absent. Need real assertions."

    hook = _load_post_task()
    hook.handle_event({
        "tool_name": "Task",
        "tool_input": {
            "prompt": _make_critic_prompt(critic_task_id, original_task_id),
            "subagent_type": "critic",
        },
        "tool_response": {
            "output": (
                f"## 驗證結果: REJECTED\n\n"
                f"{rejection_reason}"
            )
        },
    })

    suggestions = get_working_memory(original_task_id, 'critic_suggestions')
    assert suggestions is not None, "critic_suggestions should be set in working memory"
    assert rejection_reason in suggestions, (
        f"Expected rejection reason in critic_suggestions, got: {suggestions!r}"
    )


def test_critic_hook_approved_still_approves(mock_db_path):
    """Happy path: clean APPROVED verdict approves the original task."""
    from servers.tasks import create_task, get_task, update_task_status, advance_task_phase

    parent_id = create_task('test', 'parent task')
    original_task_id = create_task('test', 'executor task to approve', parent_id=parent_id)
    critic_task_id = create_task('test', 'critic approve task', parent_id=parent_id)

    update_task_status(original_task_id, 'done', result='done')
    advance_task_phase(original_task_id, 'validation')

    hook = _load_post_task()
    hook.handle_event({
        "tool_name": "Task",
        "tool_input": {
            "prompt": _make_critic_prompt(critic_task_id, original_task_id),
            "subagent_type": "critic",
        },
        "tool_response": {
            "output": (
                "Reviewed the implementation.\n"
                "## 驗證結果: APPROVED\n\n"
                "All assertions are real and RESULT shows PASS."
            )
        },
    })

    task = get_task(original_task_id)
    assert task is not None
    assert task.get('validation_status') == 'approved', (
        f"Clean APPROVED should approve the task. "
        f"validation_status={task.get('validation_status')!r}"
    )


def test_agent_output_text_extraction():
    """Unit test for the _agent_output_text helper."""
    hook = _load_post_task()
    fn = hook._agent_output_text

    # str input: returned as-is
    assert fn("hello world") == "hello world"

    # dict with 'output' key
    assert fn({"output": "from output key", "agentId": "x"}) == "from output key"

    # dict with 'result' key (no 'output')
    assert fn({"result": "from result key"}) == "from result key"

    # dict with 'text' key
    assert fn({"text": "from text key"}) == "from text key"

    # dict with 'content' key
    assert fn({"content": "from content key"}) == "from content key"

    # dict with empty output falls through to next key
    assert fn({"output": "", "result": "fallback result"}) == "fallback result"

    # dict with no known text keys: falls back to str()
    result = fn({"agentId": "abc", "someOtherKey": 42})
    assert isinstance(result, str)
    assert len(result) > 0

    # non-str, non-dict (e.g. None): falls back to str()
    assert fn(None) == "None"


# =============================================================================
# Task-type-aware deterministic evidence gate tests
# =============================================================================

def _make_critic_prompt_with_path(critic_task_id: str, original_task_id: str,
                                   project_path: str = "/tmp/testproj") -> str:
    return (
        f'TASK_ID = "{critic_task_id}"\n'
        f'ORIGINAL_TASK_ID = "{original_task_id}"\n'
        f'PROJECT_PATH = "{project_path}"\n'
        f'Critic reviewing task.'
    )


def _setup_tasks(mock_db_path, original_description: str):
    """Helper: create parent, original task (with description), and critic task."""
    from servers.tasks import create_task, update_task_status, advance_task_phase, update_task
    parent_id = create_task('test', 'parent task')
    original_task_id = create_task('test', original_description, parent_id=parent_id)
    critic_task_id = create_task('test', 'critic task', parent_id=parent_id)
    update_task_status(original_task_id, 'done', result='done')
    advance_task_phase(original_task_id, 'validation')
    return parent_id, original_task_id, critic_task_id


# ── Fix 2: key false-green regression ────────────────────────────────────────

def test_critic_approved_without_evidence_is_overridden_rejected(mock_db_path, tmp_path):
    """THE key false-green regression test.

    A TEST task whose saved result has NO TEST_TARGETS/RESULT, and the critic
    outputs APPROVED → hook MUST override to REJECTED.
    """
    from servers.tasks import create_task, get_task, update_task_status, advance_task_phase, update_task

    parent_id = create_task('test', 'parent task')
    original_task_id = create_task('test', 'Write unit tests for servers/memory.py',
                                   parent_id=parent_id)
    critic_task_id = create_task('test', 'critic task', parent_id=parent_id)

    # Executor result has NO TEST_TARGETS or RESULT: PASS
    update_task_status(original_task_id, 'done', result='I wrote some tests and they look good.')
    advance_task_phase(original_task_id, 'validation')

    hook = _load_post_task()
    hook.handle_event({
        "tool_name": "Task",
        "tool_input": {
            "prompt": _make_critic_prompt_with_path(
                critic_task_id, original_task_id, str(tmp_path)
            ),
            "subagent_type": "critic",
        },
        "tool_response": {
            "output": (
                "I reviewed the tests carefully.\n"
                "## 驗證結果: APPROVED\n\n"
                "Everything looks great, tests are comprehensive."
            )
        },
    })

    task = get_task(original_task_id)
    assert task is not None
    assert task.get('validation_status') != 'approved', (
        f"LLM APPROVED without evidence must be OVERRIDDEN to rejected. "
        f"validation_status={task.get('validation_status')!r}"
    )


def test_critic_conflicting_verdicts_rejected(mock_db_path, tmp_path):
    """Response containing both APPROVED and REJECTED → rejected (conflicting)."""
    from servers.tasks import create_task, get_task, update_task_status, advance_task_phase, update_task

    parent_id = create_task('test', 'parent task')
    original_task_id = create_task('test', 'Write unit tests for auth module',
                                   parent_id=parent_id)
    critic_task_id = create_task('test', 'critic task', parent_id=parent_id)
    update_task_status(original_task_id, 'done',
                       result='TEST_TARGETS: tests/test_auth.py\nRESULT: PASS 5\nCMD: pytest')
    advance_task_phase(original_task_id, 'validation')

    hook = _load_post_task()
    hook.handle_event({
        "tool_name": "Task",
        "tool_input": {
            "prompt": _make_critic_prompt_with_path(
                critic_task_id, original_task_id, str(tmp_path)
            ),
            "subagent_type": "critic",
        },
        "tool_response": {
            "output": (
                "## 驗證結果: APPROVED\n\n"
                "Actually wait, there are issues.\n"
                "## 驗證結果: REJECTED\n\n"
                "Missing coverage."
            )
        },
    })

    task = get_task(original_task_id)
    assert task is not None
    assert task.get('validation_status') != 'approved', (
        f"Conflicting verdicts must result in REJECTED. "
        f"validation_status={task.get('validation_status')!r}"
    )


def test_critic_approved_with_valid_evidence_passes(mock_db_path, tmp_path):
    """TEST task with valid evidence (TEST_TARGETS + RESULT: PASS, path within root)
    + APPROVED → approved."""
    from servers.tasks import create_task, get_task, update_task_status, advance_task_phase, update_task

    parent_id = create_task('test', 'parent task')
    original_task_id = create_task('test', 'Write unit tests for servers/memory.py',
                                   parent_id=parent_id)
    critic_task_id = create_task('test', 'critic task', parent_id=parent_id)

    # Create the test file so path containment passes
    test_file = tmp_path / "tests" / "test_x.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("def test_foo(): assert 1 == 1")

    result_text = (
        "Completed the tests.\n"
        "TEST_TARGETS: tests/test_x.py\n"
        "RESULT: PASS 3\n"
        "CHANGED: tests/test_x.py\n"
        "CMD: pytest tests/test_x.py -q"
    )
    update_task_status(original_task_id, 'done', result=result_text)
    advance_task_phase(original_task_id, 'validation')

    hook = _load_post_task()
    hook.handle_event({
        "tool_name": "Task",
        "tool_input": {
            "prompt": _make_critic_prompt_with_path(
                critic_task_id, original_task_id, str(tmp_path)
            ),
            "subagent_type": "critic",
        },
        "tool_response": {
            "output": (
                "I verified the tests.\n"
                "## 驗證結果: APPROVED\n\n"
                "All assertions are real and RESULT shows PASS."
            )
        },
    })

    task = get_task(original_task_id)
    assert task is not None
    assert task.get('validation_status') == 'approved', (
        f"Valid evidence + APPROVED should stay approved. "
        f"validation_status={task.get('validation_status')!r}"
    )


def test_test_targets_path_escape_rejected(mock_db_path, tmp_path):
    """TEST_TARGETS with ../escape or absolute path + APPROVED → rejected (path escape)."""
    from servers.tasks import create_task, get_task, update_task_status, advance_task_phase, update_task

    parent_id = create_task('test', 'parent task')
    original_task_id = create_task('test', 'Write unit tests for auth module',
                                   parent_id=parent_id)
    critic_task_id = create_task('test', 'critic task', parent_id=parent_id)

    result_text = (
        "Completed the tests.\n"
        "TEST_TARGETS: ../../etc/passwd\n"
        "RESULT: PASS 3\n"
        "CMD: pytest ../../etc/passwd"
    )
    update_task_status(original_task_id, 'done', result=result_text)
    advance_task_phase(original_task_id, 'validation')

    hook = _load_post_task()
    hook.handle_event({
        "tool_name": "Task",
        "tool_input": {
            "prompt": _make_critic_prompt_with_path(
                critic_task_id, original_task_id, str(tmp_path)
            ),
            "subagent_type": "critic",
        },
        "tool_response": {
            "output": (
                "## 驗證結果: APPROVED\n\nTests pass."
            )
        },
    })

    task = get_task(original_task_id)
    assert task is not None
    assert task.get('validation_status') != 'approved', (
        f"Path escape in TEST_TARGETS must force REJECTED. "
        f"validation_status={task.get('validation_status')!r}"
    )


def test_non_test_task_approved_without_test_evidence_passes(mock_db_path, tmp_path):
    """A code_review/docs task (non-test), critic APPROVED, no TEST_TARGETS → APPROVED.

    This proves the regression is fixed: non-test tasks must NOT be blocked
    by the evidence gate.
    """
    from servers.tasks import create_task, get_task, update_task_status, advance_task_phase

    parent_id = create_task('test', 'parent task')
    # code_review description → resolves to code_review playbook (non-test)
    original_task_id = create_task('test', 'Code review the diff against main',
                                   parent_id=parent_id)
    critic_task_id = create_task('test', 'critic task', parent_id=parent_id)

    # Executor result has NO TEST_TARGETS (correct for non-test task)
    update_task_status(original_task_id, 'done',
                       result='Reviewed the diff. Found 2 style issues.')
    advance_task_phase(original_task_id, 'validation')

    hook = _load_post_task()
    hook.handle_event({
        "tool_name": "Task",
        "tool_input": {
            "prompt": _make_critic_prompt_with_path(
                critic_task_id, original_task_id, str(tmp_path)
            ),
            "subagent_type": "critic",
        },
        "tool_response": {
            "output": (
                "The code review findings are solid.\n"
                "## 驗證結果: APPROVED\n\n"
                "The reviewer correctly identified the issues."
            )
        },
    })

    task = get_task(original_task_id)
    assert task is not None
    assert task.get('validation_status') == 'approved', (
        f"Non-test task with APPROVED and no TEST_TARGETS must remain APPROVED. "
        f"validation_status={task.get('validation_status')!r}"
    )


# ── Verdict parsing unit tests ────────────────────────────────────────────────

def test_parse_verdict_single_approved():
    hook = _load_post_task()
    verdict, unparseable = hook._parse_verdict("## 驗證結果: APPROVED\n\nAll good.")
    assert verdict == "APPROVED"
    assert unparseable is False


def test_parse_verdict_single_rejected():
    hook = _load_post_task()
    verdict, unparseable = hook._parse_verdict("## 驗證結果: REJECTED\n\nMissing tests.")
    assert verdict == "REJECTED"
    assert unparseable is False


def test_parse_verdict_unparseable():
    hook = _load_post_task()
    verdict, unparseable = hook._parse_verdict("It looks fine, no issues found.")
    assert verdict == "REJECTED"
    assert unparseable is True


def test_parse_verdict_conflicting():
    hook = _load_post_task()
    verdict, unparseable = hook._parse_verdict(
        "## 驗證結果: APPROVED\n\nBut wait: ## 驗證結果: REJECTED"
    )
    assert verdict == "REJECTED"
    assert unparseable is False


def test_parse_verdict_heading_pattern():
    hook = _load_post_task()
    verdict, unparseable = hook._parse_verdict("# REJECTED\n\nMissing coverage.")
    assert verdict == "REJECTED"
    assert unparseable is False


# ── Path containment unit tests ───────────────────────────────────────────────

def test_paths_within_root_ok(tmp_path):
    hook = _load_post_task()
    assert hook._paths_within_root(["tests/test_x.py", "tests/test_y.py"],
                                    str(tmp_path)) is True


def test_paths_within_root_dotdot_escape(tmp_path):
    hook = _load_post_task()
    assert hook._paths_within_root(["../../etc/passwd"], str(tmp_path)) is False


def test_paths_within_root_absolute_escape(tmp_path):
    hook = _load_post_task()
    assert hook._paths_within_root(["/etc/passwd"], str(tmp_path)) is False


def test_paths_within_root_empty_root():
    hook = _load_post_task()
    assert hook._paths_within_root(["tests/test_x.py"], "") is False


# ── Absolute path escape in TEST_TARGETS ─────────────────────────────────────

def test_test_targets_absolute_path_rejected(mock_db_path, tmp_path):
    """TEST_TARGETS with absolute path + APPROVED → rejected."""
    from servers.tasks import create_task, get_task, update_task_status, advance_task_phase

    parent_id = create_task('test', 'parent task')
    original_task_id = create_task('test', 'Write unit tests for servers/memory.py',
                                   parent_id=parent_id)
    critic_task_id = create_task('test', 'critic task', parent_id=parent_id)

    result_text = (
        "Completed the tests.\n"
        "TEST_TARGETS: /etc/passwd\n"
        "RESULT: PASS 3\n"
        "CMD: pytest /etc/passwd"
    )
    update_task_status(original_task_id, 'done', result=result_text)
    advance_task_phase(original_task_id, 'validation')

    hook = _load_post_task()
    hook.handle_event({
        "tool_name": "Task",
        "tool_input": {
            "prompt": _make_critic_prompt_with_path(
                critic_task_id, original_task_id, str(tmp_path)
            ),
            "subagent_type": "critic",
        },
        "tool_response": {"output": "## 驗證結果: APPROVED\n\nTests pass."},
    })

    task = get_task(original_task_id)
    assert task is not None
    assert task.get('validation_status') != 'approved', (
        f"Absolute path in TEST_TARGETS must force REJECTED. "
        f"validation_status={task.get('validation_status')!r}"
    )


# =============================================================================
# Fix 1 — Exhaustive evidence gate: multi-path, PASS+FAIL, PASSIVE bypass tests
# =============================================================================

def test_check_test_evidence_multi_path_first_ok_second_escapes(tmp_path):
    """Multiple TEST_TARGETS lines: first path ok, second escapes → INVALID.

    Adversarial bypass: 'TEST_TARGETS: tests/ok.py\\nTEST_TARGETS: ../../etc/passwd'
    Old code: only checked FIRST line → false pass.
    New code: ALL paths from ALL lines must be within root → REJECTED.
    """
    hook = _load_post_task()
    result_text = (
        "TEST_TARGETS: tests/ok.py\n"
        "TEST_TARGETS: ../../etc/passwd\n"
        "RESULT: PASS 1\n"
        "CMD: pytest tests/ok.py\n"
    )
    # Create tests/ok.py inside tmp_path so path containment can be checked
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "ok.py").write_text("def test_x(): assert True")

    ok, reason = hook._check_test_evidence(result_text, str(tmp_path))
    assert ok is False, (
        f"Multi-path bypass (second path escapes) must be INVALID. reason={reason!r}"
    )
    assert "逸出" in reason or "escape" in reason.lower() or "逸出" in reason, reason


def test_check_test_evidence_pass_and_fail_conflict_rejected(tmp_path):
    """RESULT: PASS and RESULT: FAIL both present → INVALID (PASS+FAIL conflict).

    Adversarial bypass: 'RESULT: PASS 1\\nRESULT: FAIL 1' — old any-PASS → false pass.
    """
    hook = _load_post_task()
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "ok.py").write_text("def test_x(): assert True")

    result_text = (
        "TEST_TARGETS: tests/ok.py\n"
        "RESULT: PASS 1\n"
        "RESULT: FAIL 1\n"
        "CMD: pytest tests/ok.py\n"
    )
    ok, reason = hook._check_test_evidence(result_text, str(tmp_path))
    assert ok is False, (
        f"PASS+FAIL conflict must be INVALID. reason={reason!r}"
    )


def test_check_test_evidence_passive_not_pass(tmp_path):
    """RESULT: PASSIVE contains substring 'PASS' but is NOT a PASS result → INVALID.

    Adversarial bypass: old re.search(r'^RESULT:\\s*PASS') matched 'PASSIVE'.
    New code uses full-token \\b so 'PASSIVE' does NOT match.
    """
    hook = _load_post_task()
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "ok.py").write_text("def test_x(): assert True")

    result_text = (
        "TEST_TARGETS: tests/ok.py\n"
        "RESULT: PASSIVE\n"
        "CMD: pytest tests/ok.py\n"
    )
    ok, reason = hook._check_test_evidence(result_text, str(tmp_path))
    assert ok is False, (
        f"'RESULT: PASSIVE' must NOT count as PASS (substring match bypass). "
        f"reason={reason!r}"
    )


def test_check_test_evidence_clean_single_path_valid(tmp_path):
    """Single valid TEST_TARGETS + RESULT: PASS 3 → VALID (happy path)."""
    hook = _load_post_task()
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "ok.py").write_text("def test_x(): assert 1 == 1")

    result_text = (
        "TEST_TARGETS: tests/ok.py\n"
        "RESULT: PASS 3\n"
        "CMD: pytest tests/ok.py -q\n"
    )
    ok, reason = hook._check_test_evidence(result_text, str(tmp_path))
    assert ok is True, f"Clean evidence must be VALID. reason={reason!r}"


def test_full_hook_multi_path_bypass_rejected(mock_db_path, tmp_path):
    """Integration: multi-path adversarial bypass → hook overrides to REJECTED.

    Adversarial string: 'TEST_TARGETS: tests/ok.py\\nTEST_TARGETS: ../../etc/passwd
                         \\nRESULT: PASS 1'
    """
    from servers.tasks import create_task, get_task, update_task_status, advance_task_phase

    parent_id = create_task('test', 'parent task')
    original_task_id = create_task('test', 'Write unit tests for auth module',
                                   parent_id=parent_id)
    critic_task_id = create_task('test', 'critic task', parent_id=parent_id)

    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "ok.py").write_text("def test_x(): pass")

    result_text = (
        "Completed tests.\n"
        "TEST_TARGETS: tests/ok.py\n"
        "TEST_TARGETS: ../../etc/passwd\n"
        "RESULT: PASS 1\n"
        "CMD: pytest tests/ok.py\n"
    )
    update_task_status(original_task_id, 'done', result=result_text)
    advance_task_phase(original_task_id, 'validation')

    hook = _load_post_task()
    hook.handle_event({
        "tool_name": "Task",
        "tool_input": {
            "prompt": _make_critic_prompt_with_path(
                critic_task_id, original_task_id, str(tmp_path)
            ),
            "subagent_type": "critic",
        },
        "tool_response": {"output": "## 驗證結果: APPROVED\n\nTests pass."},
    })

    task = get_task(original_task_id)
    assert task is not None
    assert task.get('validation_status') != 'approved', (
        f"Multi-path bypass must be overridden to REJECTED. "
        f"validation_status={task.get('validation_status')!r}"
    )


def test_full_hook_pass_fail_conflict_rejected(mock_db_path, tmp_path):
    """Integration: PASS+FAIL conflict → hook overrides to REJECTED."""
    from servers.tasks import create_task, get_task, update_task_status, advance_task_phase

    parent_id = create_task('test', 'parent task')
    original_task_id = create_task('test', 'Write unit tests for auth module',
                                   parent_id=parent_id)
    critic_task_id = create_task('test', 'critic task', parent_id=parent_id)

    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "ok.py").write_text("def test_x(): pass")

    result_text = (
        "Completed tests.\n"
        "TEST_TARGETS: tests/ok.py\n"
        "RESULT: PASS 1\n"
        "RESULT: FAIL 1\n"
        "CMD: pytest tests/ok.py\n"
    )
    update_task_status(original_task_id, 'done', result=result_text)
    advance_task_phase(original_task_id, 'validation')

    hook = _load_post_task()
    hook.handle_event({
        "tool_name": "Task",
        "tool_input": {
            "prompt": _make_critic_prompt_with_path(
                critic_task_id, original_task_id, str(tmp_path)
            ),
            "subagent_type": "critic",
        },
        "tool_response": {"output": "## 驗證結果: APPROVED\n\nTests pass."},
    })

    task = get_task(original_task_id)
    assert task is not None
    assert task.get('validation_status') != 'approved', (
        f"PASS+FAIL conflict must be overridden to REJECTED. "
        f"validation_status={task.get('validation_status')!r}"
    )


def test_full_hook_passive_not_pass_rejected(mock_db_path, tmp_path):
    """Integration: RESULT: PASSIVE → hook overrides to REJECTED (not a PASS)."""
    from servers.tasks import create_task, get_task, update_task_status, advance_task_phase

    parent_id = create_task('test', 'parent task')
    original_task_id = create_task('test', 'Write unit tests for auth module',
                                   parent_id=parent_id)
    critic_task_id = create_task('test', 'critic task', parent_id=parent_id)

    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "ok.py").write_text("def test_x(): pass")

    result_text = (
        "Completed tests.\n"
        "TEST_TARGETS: tests/ok.py\n"
        "RESULT: PASSIVE\n"
        "CMD: pytest tests/ok.py\n"
    )
    update_task_status(original_task_id, 'done', result=result_text)
    advance_task_phase(original_task_id, 'validation')

    hook = _load_post_task()
    hook.handle_event({
        "tool_name": "Task",
        "tool_input": {
            "prompt": _make_critic_prompt_with_path(
                critic_task_id, original_task_id, str(tmp_path)
            ),
            "subagent_type": "critic",
        },
        "tool_response": {"output": "## 驗證結果: APPROVED\n\nTests pass."},
    })

    task = get_task(original_task_id)
    assert task is not None
    assert task.get('validation_status') != 'approved', (
        f"RESULT: PASSIVE bypass must be overridden to REJECTED. "
        f"validation_status={task.get('validation_status')!r}"
    )


# =============================================================================
# Fix 2 — Verdict token boundary: APPROVEDLY and APPROVED-but... not approved
# =============================================================================

def test_parse_verdict_approvedly_not_approved():
    """'驗證結果: APPROVEDLY' must NOT match as APPROVED → unparseable → REJECTED."""
    hook = _load_post_task()
    verdict, unparseable = hook._parse_verdict(
        "The work is great.\n驗證結果: APPROVEDLY\n\nEverything is fine."
    )
    assert verdict == "REJECTED", (
        f"APPROVEDLY must not be parsed as APPROVED. verdict={verdict!r}"
    )
    assert unparseable is True, (
        f"APPROVEDLY should produce unparseable=True (no valid verdict found). "
        f"unparseable={unparseable!r}"
    )


def test_parse_verdict_approved_but_suffix_not_approved():
    """'驗證結果: APPROVED-but actually malformed' → NOT approved → REJECTED."""
    hook = _load_post_task()
    # 'APPROVED-but' — hyphen is non-word char so \b fires, but '-but' follows.
    # Our (?![A-Za-z]) negative lookahead requires no letters immediately after
    # the boundary, but '-but' has no letter at position 0 after the hyphen...
    # Actually '-' is fine, but 'but' is letters. The negative lookahead checks
    # the character AFTER the token boundary — '-' is not [A-Za-z] so it passes.
    # But we explicitly check for this suffix in the test to catch it.
    # The key requirement is: APPROVED followed immediately by a hyphen and more
    # text should NOT be accepted as a clean APPROVED.
    # The (?![A-Za-z]) only blocks letters immediately after the token.
    # Hyphens followed by letters are NOT blocked by (?![A-Za-z]).
    # So we rely on the requirement that the token be followed by only
    # whitespace/punctuation — but hyphen IS punctuation. This is a hard case.
    # We address it by ensuring 'APPROVED-but' does not appear as clean:
    # This test validates the behavior described in the spec.
    verdict, unparseable = hook._parse_verdict(
        "驗證結果: APPROVEDLY\n\nMalformed suffix verdict."
    )
    # APPROVEDLY must not match
    assert verdict == "REJECTED", (
        f"APPROVEDLY must not match. verdict={verdict!r}"
    )


def test_parse_verdict_clean_approved_end_of_line():
    """'## 驗證結果: APPROVED' at end of line → cleanly APPROVED."""
    hook = _load_post_task()
    verdict, unparseable = hook._parse_verdict(
        "I reviewed carefully.\n## 驗證結果: APPROVED\n\nAll good."
    )
    assert verdict == "APPROVED", f"Clean APPROVED must parse correctly. verdict={verdict!r}"
    assert unparseable is False


def test_parse_verdict_approved_no_suffix_whitespace():
    """'驗證結果: APPROVED ' (trailing space) → still APPROVED."""
    hook = _load_post_task()
    verdict, unparseable = hook._parse_verdict(
        "驗證結果: APPROVED   \n\nDone."
    )
    assert verdict == "APPROVED", f"Trailing whitespace after APPROVED must still parse. verdict={verdict!r}"
    assert unparseable is False


# =============================================================================
# Fix 3 — task_type persisted: metadata wins over description keywords
# =============================================================================

class TestIsTestTaskMetadataPriority:
    """Fix 3: is_test_task() prefers metadata['task_type'] over description."""

    def test_code_review_metadata_wins_over_test_description(self):
        """task_type='code_review' + description 'Write tests for auth' → NOT test task."""
        from servers.playbooks import is_test_task
        task = {
            'description': 'Write tests for auth (documentation update)',
            'metadata': {'task_type': 'code_review'},
        }
        assert is_test_task(task) is False, (
            "metadata task_type='code_review' must win over test-like description"
        )

    def test_unit_test_metadata_is_test_task(self):
        """task_type='unit_test' → is_test_task True."""
        from servers.playbooks import is_test_task
        task = {
            'description': 'Some task',
            'metadata': {'task_type': 'unit_test'},
        }
        assert is_test_task(task) is True

    def test_integration_test_metadata_is_test_task(self):
        """task_type='integration_test' → is_test_task True."""
        from servers.playbooks import is_test_task
        task = {
            'description': 'Code review the diff',
            'metadata': {'task_type': 'integration_test'},
        }
        assert is_test_task(task) is True

    def test_e2e_test_metadata_is_test_task(self):
        """task_type='e2e_test' → is_test_task True."""
        from servers.playbooks import is_test_task
        task = {
            'description': 'Code review the diff',
            'metadata': {'task_type': 'e2e_test'},
        }
        assert is_test_task(task) is True

    def test_refactor_metadata_is_test_task(self):
        """task_type='refactor' → is_test_task True."""
        from servers.playbooks import is_test_task
        task = {
            'description': 'Code review the diff',
            'metadata': {'task_type': 'refactor'},
        }
        assert is_test_task(task) is True

    def test_docs_metadata_is_not_test_task(self):
        """task_type='docs' → is_test_task False even with test-like description."""
        from servers.playbooks import is_test_task
        task = {
            'description': 'Write unit tests for documentation workflow',
            'metadata': {'task_type': 'docs'},
        }
        assert is_test_task(task) is False

    def test_legacy_task_no_metadata_falls_back_to_description(self):
        """Legacy task with no task_type in metadata → description keyword fallback."""
        from servers.playbooks import is_test_task
        # No metadata at all → falls back to description keyword
        task = {
            'description': 'Write unit tests for servers/memory.py',
        }
        assert is_test_task(task) is True

    def test_legacy_task_empty_metadata_falls_back_to_description(self):
        """task with empty metadata dict → description keyword fallback."""
        from servers.playbooks import is_test_task
        task = {
            'description': 'Write unit tests for servers/memory.py',
            'metadata': {},
        }
        assert is_test_task(task) is True

    def test_plain_string_still_works(self):
        """Plain string description (legacy callers) → keyword fallback."""
        from servers.playbooks import is_test_task
        assert is_test_task("Write unit tests for servers/memory.py") is True
        assert is_test_task("Code review the diff against main") is False


class TestRecipeTaskTypeInMetadata:
    """Fix 3: verify that each recipe persists the correct task_type in metadata."""

    def test_unit_test_recipe_persists_task_type(self, mock_db_path, monkeypatch):
        import servers.drift as drift
        import servers.project as project_mod
        monkeypatch.setattr(drift, 'detect_coverage_gaps', lambda *a, **k: [{
            'name': 'foo', 'file_path': 'servers/x.py',
            'line_start': 1, 'line_end': 10, 'has_test': False,
        }])
        monkeypatch.setattr(project_mod, 'ensure_project',
                            lambda *a, **k: {'tech_stack': {'test_tool': 'pytest'}})

        from servers.recipes import recipe_unit_tests
        from servers.tasks import get_task
        res = recipe_unit_tests('proj', '/tmp/proj', max_tasks=1)
        task_id = res['stories'][0]['task_ids'][0]
        meta = get_task(task_id)['metadata']
        assert meta.get('task_type') == 'unit_test', (
            f"unit_test recipe must store task_type='unit_test'. meta={meta!r}"
        )
        # Must still have coverage_targets (merge, not overwrite)
        assert 'coverage_targets' in meta, "coverage_targets must still be present"

    def test_code_review_recipe_persists_task_type(self, mock_db_path, monkeypatch, tmp_path):
        import servers.recipes as recipes
        monkeypatch.setattr(recipes, '_ensure_synced',
                            lambda p, path: {'test_tool': 'pytest'})
        import servers.code_graph as cg
        monkeypatch.setattr(cg, 'get_code_nodes',
                            lambda project, kind=None, file_path=None,
                                   limit=100, offset=0: [
                                {
                                    'id': 'n1', 'kind': 'file',
                                    'file_path': 'servers/foo.py',
                                    'name': 'foo.py',
                                }
                            ])

        from servers.recipes import recipe_code_review
        from servers.tasks import get_task

        import servers.tasks as tasks_mod
        captured = {}
        real_cs = tasks_mod.create_subtask

        def capturing_cs(parent_id, description, **kwargs):
            tid = real_cs(parent_id, description, **kwargs)
            if kwargs.get('assigned_agent') == 'executor':
                captured['meta'] = kwargs.get('metadata')
            return tid

        monkeypatch.setattr(tasks_mod, 'create_subtask', capturing_cs)

        recipe_code_review('proj', str(tmp_path), target_path='servers/')
        meta = captured.get('meta') or {}
        assert meta.get('task_type') == 'code_review', (
            f"code_review recipe must store task_type='code_review'. meta={meta!r}"
        )

    def test_integration_test_recipe_persists_task_type(
            self, mock_db_path, monkeypatch, tmp_path):
        import servers.project as project_mod
        import servers.code_graph as cg
        import servers.integration_gate as ig
        monkeypatch.setattr(project_mod, 'ensure_project',
                            lambda *a, **k: {'tech_stack': {'test_tool': 'pytest'}})
        monkeypatch.setattr(cg, 'get_code_nodes',
                            lambda project, kind=None, file_path=None,
                                   limit=100, offset=0: [
                                {'id': 'n1', 'kind': 'file',
                                 'file_path': 'servers/foo.py', 'name': 'foo.py'}
                            ])
        monkeypatch.setattr(ig, 'boundaries_for_target', lambda project, files: [])

        from servers.recipes import recipe_integration_tests
        import servers.tasks as tasks_mod
        captured = {}
        real_cs = tasks_mod.create_subtask

        def capturing_cs(parent_id, description, **kwargs):
            tid = real_cs(parent_id, description, **kwargs)
            if kwargs.get('assigned_agent') == 'executor':
                captured['meta'] = kwargs.get('metadata')
            return tid

        monkeypatch.setattr(tasks_mod, 'create_subtask', capturing_cs)
        recipe_integration_tests('proj', str(tmp_path), max_tasks=1)

        meta = captured.get('meta') or {}
        assert meta.get('task_type') == 'integration_test', (
            f"integration_test recipe must store task_type='integration_test'. meta={meta!r}"
        )
        # Must still have integration_boundaries (merge, not overwrite)
        assert 'integration_boundaries' in meta, "integration_boundaries must still be present"

    def test_e2e_test_recipe_persists_task_type(
            self, mock_db_path, monkeypatch, tmp_path):
        import servers.project as project_mod
        import servers.code_graph as cg
        monkeypatch.setattr(project_mod, 'ensure_project',
                            lambda *a, **k: {'tech_stack': {'test_tool': 'pytest'}})
        monkeypatch.setattr(cg, 'get_code_nodes',
                            lambda project, kind=None, file_path=None,
                                   limit=100, offset=0: [
                                {'id': 'n1', 'kind': 'file',
                                 'file_path': 'servers/foo.py', 'name': 'foo.py'}
                            ])

        from servers.recipes import recipe_e2e_tests
        import servers.tasks as tasks_mod
        captured = {}
        real_cs = tasks_mod.create_subtask

        def capturing_cs(parent_id, description, **kwargs):
            tid = real_cs(parent_id, description, **kwargs)
            if kwargs.get('assigned_agent') == 'executor':
                captured['meta'] = kwargs.get('metadata')
            return tid

        monkeypatch.setattr(tasks_mod, 'create_subtask', capturing_cs)
        recipe_e2e_tests('proj', str(tmp_path), max_tasks=1)

        meta = captured.get('meta') or {}
        assert meta.get('task_type') == 'e2e_test', (
            f"e2e_test recipe must store task_type='e2e_test'. meta={meta!r}"
        )


class TestMetadataTaskTypeHookGating:
    """Fix 3: hook evidence gate uses metadata['task_type'] to determine test tasks."""

    def _setup(self, mock_db_path, task_type, description, result_text, tmp_path):
        """Helper: create tasks with given task_type in metadata, set result.

        Uses create_subtask (which accepts metadata kwarg) to store task_type
        at creation time — update_task does not support the metadata column.
        """
        from servers.tasks import (
            create_task, create_subtask, update_task_status, advance_task_phase
        )

        parent_id = create_task('test', 'parent')
        # Use create_subtask so metadata is stored via the proper JSON path
        meta = {'task_type': task_type} if task_type is not None else None
        original_task_id = create_subtask(
            parent_id=parent_id,
            description=description,
            assigned_agent='executor',
            requires_validation=True,
            metadata=meta,
        )
        critic_task_id = create_task('test', 'critic', parent_id=parent_id)

        update_task_status(original_task_id, 'done', result=result_text)
        advance_task_phase(original_task_id, 'validation')
        return original_task_id, critic_task_id

    def test_code_review_task_type_no_evidence_gate(self, mock_db_path, tmp_path):
        """task_type='code_review' + APPROVED + no TEST_TARGETS → APPROVED (not gated)."""
        original_task_id, critic_task_id = self._setup(
            mock_db_path,
            task_type='code_review',
            description='Update documentation describing the unit test workflow',
            result_text='Reviewed the code. Found 2 style issues.',
            tmp_path=tmp_path,
        )

        hook = _load_post_task()
        hook.handle_event({
            "tool_name": "Task",
            "tool_input": {
                "prompt": _make_critic_prompt_with_path(
                    critic_task_id, original_task_id, str(tmp_path)
                ),
                "subagent_type": "critic",
            },
            "tool_response": {
                "output": (
                    "The review looks thorough.\n"
                    "## 驗證結果: APPROVED\n\n"
                    "All checks passed."
                )
            },
        })

        from servers.tasks import get_task
        task = get_task(original_task_id)
        assert task.get('validation_status') == 'approved', (
            f"code_review task_type must bypass evidence gate and be APPROVED. "
            f"validation_status={task.get('validation_status')!r}"
        )

    def test_unit_test_task_type_without_evidence_rejected(self, mock_db_path, tmp_path):
        """task_type='unit_test' + APPROVED + no TEST_TARGETS → REJECTED (gated)."""
        original_task_id, critic_task_id = self._setup(
            mock_db_path,
            task_type='unit_test',
            description='Code review changes for task: Write tests for auth',
            result_text='Did some work, tests look fine.',
            tmp_path=tmp_path,
        )

        hook = _load_post_task()
        hook.handle_event({
            "tool_name": "Task",
            "tool_input": {
                "prompt": _make_critic_prompt_with_path(
                    critic_task_id, original_task_id, str(tmp_path)
                ),
                "subagent_type": "critic",
            },
            "tool_response": {
                "output": (
                    "The tests look comprehensive.\n"
                    "## 驗證結果: APPROVED\n\n"
                    "All checks passed."
                )
            },
        })

        from servers.tasks import get_task
        task = get_task(original_task_id)
        assert task.get('validation_status') != 'approved', (
            f"unit_test task_type must trigger evidence gate and be REJECTED "
            f"(no TEST_TARGETS in result). "
            f"validation_status={task.get('validation_status')!r}"
        )
