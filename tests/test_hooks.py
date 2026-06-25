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
