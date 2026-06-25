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
from typing import Any, Dict, List, Optional

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


# =============================================================================
# Path Containment Helper
# =============================================================================

def _paths_within_root(paths: List[str], project_root: str) -> bool:
    """Return True iff ALL paths are safely contained within project_root.

    Rejects:
    - Absolute paths (start with /)
    - Any path whose realpath is not inside realpath(project_root)
      (catches ../ traversal, symlink escapes, etc.)

    Uses os.path.commonpath to detect containment. Returns False on any
    path that escapes, or if project_root is unavailable/empty.
    """
    if not project_root:
        return False
    try:
        real_root = os.path.realpath(project_root)
    except Exception:
        return False

    for path in paths:
        path = path.strip()
        if not path:
            continue
        # Reject absolute paths outright
        if os.path.isabs(path):
            return False
        try:
            resolved = os.path.realpath(os.path.join(real_root, path))
            # commonpath will raise ValueError if paths are on different drives
            common = os.path.commonpath([real_root, resolved])
            if common != real_root:
                return False
        except (ValueError, Exception):
            return False
    return True


# =============================================================================
# Verdict Parsing (robust: collect ALL matches, conflict → REJECTED)
# =============================================================================

def _parse_verdict(response_text: str):
    """Parse the critic verdict from response_text.

    Collects ALL verdict markers from two sources:
      1. '驗證結果: X' pattern (inline or heading)
      2. '^#+ X$' standalone heading markers

    Returns (verdict: str, unparseable: bool):
    - ZERO matches → ('REJECTED', True)   — unparseable, fail-closed
    - MULTIPLE DISTINCT verdicts → ('REJECTED', False) — conflicting, fail-closed
    - SINGLE consistent verdict → (verdict, False)
    """
    verdicts_found = set()

    # Pattern 1: 驗證結果: VERDICT (anywhere in text, with any surrounding)
    for m in re.finditer(
        r'驗證結果\s*[:：]\s*(APPROVED|CONDITIONAL|REJECTED)',
        response_text,
        re.IGNORECASE,
    ):
        verdicts_found.add(m.group(1).upper())

    # Pattern 2: ^#+ VERDICT$ (markdown headings)
    for m in re.finditer(
        r'^#+\s*(APPROVED|CONDITIONAL|REJECTED)\s*$',
        response_text,
        re.MULTILINE | re.IGNORECASE,
    ):
        verdicts_found.add(m.group(1).upper())

    if not verdicts_found:
        return "REJECTED", True   # unparseable → fail-closed

    if len(verdicts_found) > 1:
        # Conflicting verdicts → fail-closed
        return "REJECTED", False

    return verdicts_found.pop(), False


# =============================================================================
# Deterministic Evidence Gate (test tasks only)
# =============================================================================

def _check_test_evidence(result_text: str, project_root: str):
    """Check that executor result contains valid test evidence.

    Returns (ok: bool, reason: str):
    - ok=True  → evidence is present and valid (TEST_TARGETS + RESULT: PASS,
                  all paths within project_root)
    - ok=False → missing/malformed evidence or path escape

    The 'reason' string is human-readable for critic_suggestions.
    """
    if not result_text:
        return False, "缺可信 evidence: result 為空，需含 TEST_TARGETS + RESULT: PASS，且測試檔須在專案內"

    # Look for TEST_TARGETS: line with at least one path
    tt_match = re.search(
        r'^TEST_TARGETS:\s*(.+)$',
        result_text,
        re.MULTILINE | re.IGNORECASE,
    )
    if not tt_match:
        return False, (
            "缺可信 evidence: result 需含 TEST_TARGETS + RESULT: PASS，"
            "且測試檔須在專案內"
        )

    targets_raw = tt_match.group(1).strip()
    # Split by comma to get individual paths
    paths = [p.strip() for p in targets_raw.split(',') if p.strip()]
    if not paths:
        return False, (
            "缺可信 evidence: TEST_TARGETS: 行存在但無有效路徑，"
            "需含 RESULT: PASS，且測試檔須在專案內"
        )

    # Validate paths are within project root
    if not _paths_within_root(paths, project_root):
        return False, (
            "缺可信 evidence: TEST_TARGETS 中含路徑逸出（絕對路徑或 ../ 穿透），"
            "測試檔必須在專案目錄內"
        )

    # Look for RESULT: PASS line
    result_match = re.search(
        r'^RESULT:\s*PASS',
        result_text,
        re.MULTILINE | re.IGNORECASE,
    )
    if not result_match:
        return False, (
            "缺可信 evidence: result 含 TEST_TARGETS 但缺 RESULT: PASS，"
            "測試必須真正通過才能 APPROVED"
        )

    return True, ""


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

        # Robust verdict parsing: collect ALL matches; conflict/zero → REJECTED
        verdict, unparseable = _parse_verdict(response_text)
        approved = verdict != "REJECTED"

        log(f"Critic verdict: {verdict} (unparseable={unparseable}) for task {original_task_id}")

        # Deterministic evidence gate for test tasks (before trusting any APPROVED/CONDITIONAL)
        if approved:
            try:
                from servers.tasks import get_task
                from servers.playbooks import is_test_task

                orig_task = get_task(original_task_id)
                orig_description = (orig_task or {}).get('description', '')

                if orig_task and is_test_task(orig_description):
                    # Get project_path: extract from the critic prompt (injected by dispatch)
                    project_path = extract_assignment(prompt, "PROJECT_PATH")
                    # Fallback: try to derive from the stored executor result in task
                    result_text = (orig_task or {}).get('result') or ''

                    evidence_ok, evidence_reason = _check_test_evidence(
                        result_text, project_path or ""
                    )
                    if not evidence_ok:
                        log(
                            f"Evidence gate OVERRIDE: LLM said {verdict} but trusted code "
                            f"cannot confirm evidence → REJECTED. Reason: {evidence_reason}"
                        )
                        verdict = "REJECTED"
                        approved = False
                        # Prepend to reason so executor sees it clearly
                        response_text = evidence_reason + "\n\n" + response_text
            except Exception as exc:
                log(f"Evidence gate check error (fail-closed for test tasks): {exc}")
                # If we can't check → fail-closed for test tasks
                verdict = "REJECTED"
                approved = False

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
