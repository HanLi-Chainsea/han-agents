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
from typing import Any, Dict, List, Optional, Tuple

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
# Verdict Parsing — strict line-by-line (closes prefix/suffix/mixed bypasses)
# =============================================================================

# Verdict keywords (for bearing-but-malformed detection)
_VERDICT_KEYWORDS = re.compile(
    r'(?:APPROVED|CONDITIONAL|REJECTED|驗證結果)',
    re.IGNORECASE,
)

# A CLEAN verdict line (after strip):
#   optional leading #s + optional space, optional 驗證結果[:：] prefix,
#   then EXACTLY the verdict token, then END OF LINE — nothing else.
_CLEAN_VERDICT_LINE = re.compile(
    r'^(?:#+\s*)?(?:驗證結果\s*[:：]\s*)?(APPROVED|CONDITIONAL|REJECTED)$',
    re.IGNORECASE,
)


def _normalize_to_lines(text: str) -> List[str]:
    """Normalize literal \\n (two-char sequence) and real newlines into lines.

    The str() pipeline for a tool_response dict can produce literal backslash-n
    sequences instead of real newlines. Normalize both so line-by-line parsing
    works regardless of origin.
    """
    # Replace literal two-char \\n with real newline, then split
    normalized = text.replace('\\n', '\n').replace('\\r', '\n')
    return normalized.splitlines()


def _parse_verdict(text: str) -> Tuple[str, bool]:
    """Parse the critic verdict from text using strict line-by-line rules.

    Algorithm:
    1. Normalize literal \\n sequences and real newlines, split into lines.
    2. For each line (after stripping):
       a. Check if it is a CLEAN verdict line (matches _CLEAN_VERDICT_LINE).
       b. Check if it is VERDICT-BEARING-BUT-MALFORMED: contains a verdict
          keyword or '驗證結果' but is NOT a clean verdict line.
    3. Collect all clean verdicts. Accumulate any malformed verdict-bearing lines.
    4. Rules:
       - ZERO clean verdicts → unparseable (return 'REJECTED', True)
       - ANY malformed verdict-bearing line → unparseable (return 'REJECTED', True)
         even if a clean verdict also exists
       - CONFLICTING clean verdicts (>1 distinct value), no malformed →
         return ('REJECTED', False)
       - Exactly ONE consistent clean verdict, zero malformed →
         return (verdict, False)

    Examples:
      'Critic cannot conclude 驗證結果: APPROVED' → malformed prefix → unparseable
      '驗證結果: APPROVED\\n驗證結果: REJECTED-but malformed' → malformed REJECTED line → unparseable
      'APPROVEDLY' → malformed → unparseable
      '## 驗證結果: APPROVED' → clean → APPROVED
      '驗證結果: APPROVED\\n驗證結果: REJECTED' → two clean, conflicting → REJECTED (not unparseable)
    """
    lines = _normalize_to_lines(text)

    clean_verdicts: List[str] = []
    has_malformed = False

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        clean_match = _CLEAN_VERDICT_LINE.match(line)
        if clean_match:
            clean_verdicts.append(clean_match.group(1).upper())
        elif _VERDICT_KEYWORDS.search(line):
            # Contains a verdict keyword but is NOT a clean verdict line → malformed
            has_malformed = True

    if not clean_verdicts:
        # No parseable verdict found
        return 'REJECTED', True

    if has_malformed:
        # Any malformed verdict-bearing line → fail-closed, even with clean verdicts
        return 'REJECTED', True

    distinct = set(clean_verdicts)
    if len(distinct) > 1:
        # Conflicting clean verdicts → fail-closed (not unparseable, just conflicting)
        return 'REJECTED', False

    return distinct.pop(), False


# =============================================================================
# Deterministic Evidence Gate (test tasks only)
# =============================================================================

# Strict RESULT line patterns (anchored full-line):
#   PASS: RESULT: PASS  OR  RESULT: PASS <integer>  (nothing else)
#   FAIL: RESULT: FAIL  OR  RESULT: FAIL <integer>  OR  RESULT: FAIL <integer> <anything>
_RESULT_PASS = re.compile(
    r'^\s*RESULT:\s*PASS(?:\s+\d+)?\s*$',
    re.IGNORECASE,
)
_RESULT_FAIL = re.compile(
    r'^\s*RESULT:\s*FAIL(?:\s+\d+)?(?:\s+.*)?\s*$',
    re.IGNORECASE,
)
# Any line starting with RESULT: (including bare "RESULT:")
_RESULT_LINE = re.compile(
    r'^\s*RESULT:',
    re.IGNORECASE,
)
# CMD: line with at least one non-whitespace character after CMD:
_CMD_LINE = re.compile(
    r'^\s*CMD:\s*(\S.*)$',
    re.IGNORECASE | re.MULTILINE,
)


def _check_test_evidence(result_text: str, project_root: str):
    """Check that executor result contains valid test evidence.

    Exhaustive evidence checks:
    - Collect ALL TEST_TARGETS: lines and ALL paths across them (comma or
      space separated).  EVERY path must be within project_root AND exist on
      disk as a regular file.  ANY path escaping the root, not existing, or
      being a directory -> REJECT.
    - Collect ALL RESULT: lines (including bare 'RESULT:' with nothing after).
      Parse each line with STRICT whole-line patterns:
        PASS pattern: ^\\s*RESULT:\\s*PASS(?:\\s+\\d+)?\\s*$
        FAIL pattern: ^\\s*RESULT:\\s*FAIL(?:\\s+\\d+)?(?:\\s+.*)?\\s*$
      Any RESULT: line matching NEITHER -> MALFORMED -> evidence INVALID -> REJECT.
      Rules:
        - ANY malformed RESULT: line -> evidence INVALID -> REJECT
        - At least one clean RESULT: PASS required
        - Zero clean RESULT: FAIL allowed; any FAIL -> REJECT
      So:
        'RESULT: PASS FAIL'      -> malformed -> REJECT
        'RESULT: PASS / FAIL'    -> malformed -> REJECT
        'RESULT: PASS - FAIL 1'  -> malformed -> REJECT
        'RESULT: PASS tests failed' -> malformed -> REJECT (non-digit after PASS token)
        'RESULT:'                -> malformed (bare, no PASS/FAIL) -> REJECT
        'RESULT: PASS 3'         -> valid PASS
        'RESULT: PASSIVE'        -> malformed (not PASS or FAIL token) -> REJECT
    - A non-empty CMD: line must be present: ^\\s*CMD:\\s*(\\S.*)$
      Missing or empty CMD -> evidence INVALID -> REJECT.

    Returns (ok: bool, reason: str):
    - ok=True  -> evidence is present and valid
    - ok=False -> missing/malformed evidence, path escape, non-existent file, or missing CMD
    """
    if not result_text:
        return False, "缺可信 evidence: result 為空，需含 TEST_TARGETS + RESULT: PASS，且測試檔須在專案內"

    # ── Collect ALL TEST_TARGETS: lines ──────────────────────────────────────
    tt_matches = re.findall(
        r'^TEST_TARGETS:\s*(.+)$',
        result_text,
        re.MULTILINE | re.IGNORECASE,
    )
    if not tt_matches:
        return False, (
            "缺可信 evidence: result 需含 TEST_TARGETS + RESULT: PASS，"
            "且測試檔須在專案內"
        )

    # Collect all paths from ALL TEST_TARGETS: lines.
    # Each line may list multiple paths separated by commas or spaces.
    all_paths: List[str] = []
    for raw in tt_matches:
        # Split by comma first, then by whitespace within each segment
        for segment in raw.split(','):
            segment = segment.strip()
            if segment:
                # Further split by whitespace to catch space-separated paths
                for p in segment.split():
                    p = p.strip()
                    if p:
                        all_paths.append(p)

    if not all_paths:
        return False, (
            "缺可信 evidence: TEST_TARGETS: 行存在但無有效路徑，"
            "需含 RESULT: PASS，且測試檔須在專案內"
        )

    # ALL paths must be within project root -- any escape -> REJECT
    if not _paths_within_root(all_paths, project_root):
        return False, (
            "缺可信 evidence: TEST_TARGETS 中含路徑逸出（絕對路徑或 ../ 穿透），"
            "測試檔必須在專案目錄內"
        )

    # ALL paths must exist on disk and be regular files (not directories).
    # A non-existent or directory target means the executor's PASS claim is
    # unverifiable -- reject immediately.
    if project_root:
        try:
            real_root = os.path.realpath(project_root)
            for p in all_paths:
                real = os.path.realpath(os.path.join(real_root, p))
                if not os.path.isfile(real):
                    return False, (
                        "缺可信 evidence: TEST_TARGETS 路徑不存在或非一般檔案："
                        f" {p!r}（路徑不存在或為目錄，無法驗證 PASS 聲稱）"
                    )
        except Exception as exc:
            return False, (
                f"缺可信 evidence: 驗證 TEST_TARGETS 路徑時發生錯誤：{exc}"
            )

    # ── Collect ALL RESULT: lines (including bare RESULT:) ─────────────────
    # We must find every line starting with RESULT: -- including bare ones with
    # nothing after the colon (e.g. "RESULT:").
    # Split into lines and check each line.
    all_lines = result_text.splitlines()

    pass_count = 0
    fail_count = 0
    malformed_count = 0

    for line in all_lines:
        if not _RESULT_LINE.match(line):
            continue
        # This line starts with RESULT: -- classify it strictly
        if _RESULT_PASS.match(line):
            pass_count += 1
        elif _RESULT_FAIL.match(line):
            fail_count += 1
        else:
            # Matches RESULT: prefix but not PASS or FAIL pattern -> MALFORMED
            malformed_count += 1

    if malformed_count > 0:
        return False, (
            "缺可信 evidence: RESULT: 行格式不合法（如 PASS-FAIL、PASS/FAIL、PASSIVE、bare RESULT:），"
            "RESULT 必須為 PASS 或 FAIL 加可選數量，不可含其他連接字元"
        )

    if pass_count == 0:
        return False, (
            "缺可信 evidence: result 含 TEST_TARGETS 但缺 RESULT: PASS，"
            "測試必須真正通過才能 APPROVED"
        )

    if fail_count > 0:
        return False, (
            "缺可信 evidence: result 含 RESULT: FAIL（或同時含 PASS 與 FAIL），"
            "存在任何 FAIL 即不可 APPROVED"
        )

    # ── CMD: line must be present and non-empty ─────────────────────
    # The executor must report the test command it ran.  A missing or empty CMD
    # means the PASS claim cannot be attributed to any real run.
    if not _CMD_LINE.search(result_text):
        return False, (
            "缺可信 evidence: 缺少 CMD: 行（需含測試指令，如 CMD: pytest tests/...），"
            "無法確認 executor 實際執行了測試"
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

        # Use the real agent output text (not str(tool_response)) for parsing
        response_text = _agent_output_text(tool_response)

        # Strict line-by-line verdict parsing: prefix/suffix/mixed → fail-closed
        verdict, unparseable = _parse_verdict(response_text)
        approved = verdict != "REJECTED"

        log(f"Critic verdict: {verdict} (unparseable={unparseable}) for task {original_task_id}")

        # Deterministic evidence gate for test tasks (before trusting any APPROVED/CONDITIONAL)
        if approved:
            try:
                from servers.tasks import get_task
                from servers.playbooks import is_test_task

                orig_task = get_task(original_task_id)

                # Fix 3: is_test_task now accepts the full task dict, preferring
                # metadata['task_type'] over keyword inference.
                if orig_task and is_test_task(orig_task):
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
