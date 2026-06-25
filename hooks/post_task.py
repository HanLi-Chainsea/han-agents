#!/usr/bin/env python3
"""
PostToolUse hook for HAN task lifecycle and guardrail observation.

Task events keep the existing lifecycle automation:
- executor -> finish_task()
- critic -> finish_validation()

Bash/Write events are inspected with provider-neutral guardrail helpers. When a
policy issue is detected, the hook returns additional context and records a
local trace span when TRACE_ID is available in the tool input or prompt.
"""

import json
import os
import re
import sys
from typing import Any, Dict, Optional

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BASE_DIR)

from hooks.harness_guardrail import (
    evaluate_tool_guardrail,
    extract_assignment,
    log,
    violation_message,
)


def _hook_context(message: str) -> Dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": message,
        }
    }


def _agent_output_text(tool_response: Any) -> str:
    """Extract the agent's final text from a tool_response.

    - If tool_response is a str, return it directly.
    - If tool_response is a dict, try common text keys in order, returning
      the first that is a non-empty str.
    - Otherwise fall back to str(tool_response).
    """
    if isinstance(tool_response, str):
        return tool_response
    if isinstance(tool_response, dict):
        for key in ('output', 'result', 'text', 'response', 'content', 'final'):
            value = tool_response.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return str(tool_response)


def _handle_guardrail_event(input_data: Dict[str, Any]) -> Optional[Dict]:
    tool_name = input_data.get("tool_name", "")
    result = evaluate_tool_guardrail(input_data)
    if result is None:
        return None

    from servers.guardrails import enforce_result

    enforcement = enforce_result(result)
    if enforcement["action"] == "allow":
        return None

    detail = violation_message(result)
    label = "warning" if enforcement["action"] == "warn" else enforcement["action"]
    output = _hook_context(f"HAN guardrail {label} for {tool_name}: {detail}")
    if enforcement["action"] == "block":
        output["decision"] = "block"
        output["reason"] = detail
    output["guardrail"] = {
        "mode": enforcement["mode"],
        "action": enforcement["action"],
        "violations": result.get("violations", []),
    }
    return output


def _handle_task_event(input_data: Dict[str, Any]) -> Optional[Dict]:
    tool_input = input_data.get("tool_input", {})
    tool_response = input_data.get("tool_response", {})

    prompt = tool_input.get("prompt", "")
    subagent_type = tool_input.get("subagent_type", "")

    task_id = extract_assignment(prompt, "TASK_ID")
    if not task_id:
        log(f"No TASK_ID found in prompt for {subagent_type}")
        return None

    agent_id = tool_response.get("agentId") if isinstance(tool_response, dict) else None
    log(f"[{subagent_type}] task_id={task_id}, agent_id={agent_id}")

    from servers.tasks import update_task
    from servers.facade import finish_task, finish_validation

    if subagent_type == "executor":
        if agent_id:
            update_task(task_id, executor_agent_id=agent_id)
            log(f"Recorded agentId {agent_id} for task {task_id}")

        # Fix (a): save the executor's REAL output so the critic can read it.
        output_text = _agent_output_text(tool_response)
        result = finish_task(task_id, success=True, result=(output_text or "Executor completed"))
        log(f"finish_task result: {result}")
        return None

    if subagent_type == "critic":
        original_task_id = extract_assignment(prompt, "ORIGINAL_TASK_ID")
        if not original_task_id:
            log("ERROR: ORIGINAL_TASK_ID not found in prompt")
            return None

        response_text = str(tool_response)
        unparseable = False

        verdict_match = re.search(
            r'驗證結果\s*[:：]\s*(APPROVED|CONDITIONAL|REJECTED)',
            response_text,
            re.IGNORECASE,
        )

        if verdict_match:
            verdict = verdict_match.group(1).upper()
        elif re.search(r'^#+\s*REJECTED\s*$', response_text, re.MULTILINE | re.IGNORECASE):
            verdict = "REJECTED"
        elif re.search(r'^#+\s*CONDITIONAL\s*$', response_text, re.MULTILINE | re.IGNORECASE):
            verdict = "CONDITIONAL"
        elif re.search(r'^#+\s*APPROVED\s*$', response_text, re.MULTILINE | re.IGNORECASE):
            verdict = "APPROVED"
        else:
            # Fix (b): fail-closed — unparseable must NEVER auto-approve.
            verdict = "REJECTED"
            unparseable = True

        approved = verdict != "REJECTED"
        log(f"Critic verdict: {verdict} (unparseable={unparseable}) for task {original_task_id}")

        # Fix (c): persist the reject reason for ALL non-approved paths so the
        # executor's retry prompt picks it up.
        if verdict in ("REJECTED", "CONDITIONAL"):
            if unparseable:
                reason = (
                    "Critic 未輸出可解析的 verdict（APPROVED/CONDITIONAL/REJECTED），"
                    "fail-closed 退回重驗。\n" + response_text[:800]
                )
            else:
                reason = response_text
            try:
                from servers.memory import set_working_memory
                set_working_memory(original_task_id, 'critic_suggestions', reason)
                log("Saved critic_suggestions to working_memory")
            except Exception as exc:
                log(f"Failed to save critic_suggestions: {exc}")

        result = finish_validation(task_id, original_task_id, approved=approved)
        log(f"finish_validation result: {result}")

        if verdict == "CONDITIONAL":
            return _hook_context(
                f"任務 {original_task_id} 有條件通過。建議存於 working_memory['critic_suggestions']。"
            )

    return None


def handle_event(input_data: Dict[str, Any]) -> Optional[Dict]:
    """Handle a PostToolUse event and return optional hook output."""
    tool_name = input_data.get("tool_name", "")
    if tool_name == "Task":
        return _handle_task_event(input_data)
    return _handle_guardrail_event(input_data)


def main() -> int:
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0

    try:
        output = handle_event(input_data)
        if output:
            print(json.dumps(output, ensure_ascii=False))
    except Exception as exc:
        log(f"ERROR: {str(exc)}")
        import traceback
        log(traceback.format_exc())
    return 0


if __name__ == "__main__":
    sys.exit(main())
