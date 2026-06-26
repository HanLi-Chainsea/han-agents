"""
HAN System - Task Playbooks

載入 reference/playbooks/*.md，依任務描述分類，
格式化成 executor / critic prompt 區塊。

playbook 與「任務怎麼產生」正交：recipe 與 PFC 產生的任務描述
都長得像「Write unit tests for X」，因此用描述關鍵字分類即可，
涵蓋兩條來源、零 schema 改動。

fail-open：playbook 目錄缺失或解析失敗 → resolve_playbook 回 None，
呼叫端維持原 prompt，絕不擋任務。
"""

import os
import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Union

# playbook 檔案位於 han-agents 安裝目錄（與 servers/ 同層的 reference/）
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PLAYBOOK_DIR = os.path.join(_BASE_DIR, "reference", "playbooks")

_CACHE: Optional[Dict[str, "Playbook"]] = None

# Test-type playbook names: these tasks require actual test execution evidence.
# refactor is included because it requires characterization tests.
_TEST_TASK_PLAYBOOK_NAMES = frozenset({
    "unit_test",
    "integration_test",
    "e2e_test",
    "refactor",
})

# Task type metadata values that map to test tasks.
# Non-test types (code_review, docs, analysis, …) are excluded.
_TEST_TASK_TYPES = frozenset({
    "unit_test",
    "integration_test",
    "e2e_test",
    "refactor",
})


@dataclass
class Playbook:
    name: str
    match: List[str] = field(default_factory=list)
    executor_principles: str = ""
    critic_checklist: str = ""


def _parse_playbook(text: str) -> Optional[Playbook]:
    """解析單一 playbook markdown（手寫，不依賴 PyYAML）。

    格式：
        ---
        name: <str>
        match: ["kw1", "kw2", ...]   # 必須是 JSON array，且元素均為字串，寫在同一行
        ---
        ## Executor Principles        # heading 用單一空格（"## Executor Principles"）
        ...
        ## Critic Checklist
        ...
    """
    # 容錯：去除 UTF-8 BOM 與開頭空白，避免 playbook 因前綴雜訊靜默失效
    text = text.lstrip("﻿").lstrip()
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    frontmatter, body = parts[1], parts[2]

    name = ""
    match: List[str] = []
    for line in frontmatter.splitlines():
        line = line.strip()
        if line.startswith("name:"):
            name = line[len("name:"):].strip()
        elif line.startswith("match:"):
            raw = line[len("match:"):].strip()
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = None
            if isinstance(parsed, list) and all(isinstance(k, str) for k in parsed):
                match = parsed
            else:
                match = []
    if not name:
        return None

    # 以 markdown heading 切出兩段
    executor = _extract_section(body, "## Executor Principles")
    critic = _extract_section(body, "## Critic Checklist")
    return Playbook(name=name, match=match,
                    executor_principles=executor, critic_checklist=critic)


def _extract_section(body: str, heading: str) -> str:
    """取出某個 ## heading 到下一個 ## heading（或結尾）之間的內容。"""
    lines = body.splitlines()
    out: List[str] = []
    capturing = False
    for line in lines:
        if line.strip() == heading:
            capturing = True
            continue
        if capturing and line.startswith("## "):
            break
        if capturing:
            out.append(line)
    return "\n".join(out).strip()


def load_playbooks(force_reload: bool = False) -> Dict[str, Playbook]:
    """載入所有 playbook（快取）。目錄缺失或列舉失敗 → 回空 dict（fail-open）。"""
    global _CACHE
    if _CACHE is not None and not force_reload:
        return _CACHE
    result: Dict[str, Playbook] = {}
    try:
        if os.path.isdir(_PLAYBOOK_DIR):
            for fname in sorted(os.listdir(_PLAYBOOK_DIR)):
                if not fname.endswith(".md"):
                    continue
                try:
                    with open(os.path.join(_PLAYBOOK_DIR, fname), encoding="utf-8") as f:
                        pb = _parse_playbook(f.read())
                    if pb:
                        result[pb.name] = pb
                except Exception:
                    continue  # 單檔壞掉不影響其他
    except Exception:
        result = {}  # 目錄列舉失敗 → fail-open
    _CACHE = result
    return result


def resolve_playbook(description: str) -> Optional[Playbook]:
    """依描述關鍵字分類。多重命中時取最長關鍵字（最具體）。無命中回 None。"""
    if not description:
        return None
    desc = description.lower()
    best: Optional[Playbook] = None
    best_len = 0
    for pb in load_playbooks().values():
        for kw in pb.match:
            if kw.lower() in desc and len(kw) > best_len:
                best = pb
                best_len = len(kw)
    return best


def is_test_task(task_or_description: Union[Dict, str]) -> bool:
    """Return True if the task is a test-type task that requires evidence gating.

    Fix 3 — Persisted task_type wins over keyword inference:

    Resolution order:
    1. If task_or_description is a dict AND metadata['task_type'] is present:
       - task_type in _TEST_TASK_TYPES  → True
       - task_type NOT in _TEST_TASK_TYPES → False  (metadata always wins)
       No keyword fallback when task_type is set.
    2. Otherwise (no metadata, or task_or_description is a plain string):
       Fall back to playbook keyword resolution (legacy behaviour).

    Test-type playbooks / task_types: unit_test, integration_test, e2e_test, refactor.
    Refactor is included because it requires characterization tests.

    fail-open: if playbook resolution fails or returns None → False
    (non-test path is safe; only test tasks need evidence gating).
    """
    try:
        # -- Prefer metadata['task_type'] when present -----------------------
        if isinstance(task_or_description, dict):
            metadata = task_or_description.get('metadata') or {}
            if isinstance(metadata, dict) and 'task_type' in metadata:
                task_type = metadata['task_type']
                return task_type in _TEST_TASK_TYPES

            # No task_type in metadata — fall back to description keyword check
            description = task_or_description.get('description', '')
        else:
            # Plain string path (legacy callers)
            description = task_or_description

        # -- Keyword / playbook fallback ------------------------------------
        pb = resolve_playbook(description)
        if pb is None:
            return False
        return pb.name in _TEST_TASK_PLAYBOOK_NAMES
    except Exception:
        return False


def executor_section(pb: Playbook) -> str:
    """格式化成 executor prompt 區塊。"""
    if not pb.executor_principles:
        return ""
    return f"## Playbook: {pb.name} — Principles\n\n{pb.executor_principles}\n"


def critic_section(pb: Playbook) -> str:
    """格式化成 critic 驗收清單區塊。"""
    if not pb.critic_checklist:
        return ""
    return f"## Playbook: {pb.name} — Checklist\n\n{pb.critic_checklist}\n"
