# Task Playbooks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓使用者能呼叫 han-agents 完整跑完 unit test / code review / integration test 流程，並透過「任務原則 playbook」（取自業界最佳實踐）注入 executor/critic prompt 拉高品質。

**Architecture:** 新增 `reference/playbooks/*.md`（3 個原則檔）+ `servers/playbooks.py`（載入/分類/格式化）。在 `facade._build_executor_prompt` 與 `_build_critic_prompt` 兩個注入點，依 `task.description` 關鍵字解析 playbook 並注入原則；未命中則維持原樣（fail-open，向後相容）。擴充 `recipes.py` 加入 `recipe_code_review` 與 `recipe_integration_tests`，並小幅修正 `drift.detect_coverage_gaps`（修靜默截斷、認 `tested_by` 邊、納入 `method` 節點）。

**Tech Stack:** Python 3.8+、SQLite、pytest。手寫輕量 frontmatter 解析（不引入 PyYAML）。

---

## File Structure

| 檔案 | 責任 |
|------|------|
| `reference/playbooks/unit-test.md` | 單元測試原則（AAA/FIRST/Test Behavior） |
| `reference/playbooks/code-review.md` | 程式碼審查原則（Google eng-practices） |
| `reference/playbooks/integration-test.md` | 整合測試原則（Test Pyramid） |
| `servers/playbooks.py` | 載入 playbook、依描述分類、格式化成 prompt 區塊 |
| `servers/facade.py` | 兩個 prompt builder 注入 playbook（改 2 函式） |
| `servers/code_graph.py` | `get_code_nodes`/`get_code_edges` 加 `offset` 參數（修靜默截斷） |
| `servers/drift.py` | `detect_coverage_gaps` 小改 |
| `servers/recipes.py` | 加 2 recipe + 更新 SCHEMA docstring + 註冊 |
| `SKILL.md` | 更新 recipe 清單/範例 |
| `reference/agents/pfc.md` | 更新 recipe 路由說明 |
| `tests/test_playbooks.py` | playbook 載入/分類/注入/fail-open 測試 |
| `tests/test_recipes.py` | 兩個新 recipe 的任務樹建立測試 |
| `tests/test_drift.py` | （擴充）gap 偵測小改測試 |

---

## Task 1: 建立 3 個 playbook markdown 檔

**Files:**
- Create: `reference/playbooks/unit-test.md`
- Create: `reference/playbooks/code-review.md`
- Create: `reference/playbooks/integration-test.md`

- [ ] **Step 1: 建立 unit-test.md**

建立 `reference/playbooks/unit-test.md`，內容如下（逐字）：

```markdown
---
name: unit_test
match: ["unit test", "單元測試", "write tests for", "寫測試", "撰寫測試"]
---

## Executor Principles
- 以 AAA 組織每個測試：先 Arrange 準備、再 Act 執行、最後 Assert 驗證
- 遵守 FIRST：Fast（快）、Independent（彼此不依賴、不共享狀態）、Repeatable（多次執行結果一致，隔離外部依賴）、Self-validating（純 pass/fail，不需人工判讀）、Timely（緊貼被測程式）
- 測「可觀察行為與契約」：透過 public API、驗 state 而非與協作者的互動細節，重構不應使測試破裂
- 涵蓋 happy path、邊界值、錯誤/例外路徑、空輸入（Beyoncé Rule：重要行為就要有測試）
- 一個測試只驗一個行為，命名描述「行為與預期」
- 寫完必須用專案 test_tool 實際執行，並在輸出回報 pass/fail 與執行指令
- 不得寫空殼或恆真斷言（assert True）來騙過驗證

## Critic Checklist
- [ ] 測試有實際被執行且全數通過（executor 須附執行輸出，否則 REJECT）
- [ ] 測的是行為/契約（public API、驗 state），而非實作細節
- [ ] 涵蓋錯誤路徑與邊界，而非只有 happy path
- [ ] 符合 FIRST：獨立、可重複、自我驗證
- [ ] 斷言有意義且每測只驗一件事；命名表達行為與預期
```

- [ ] **Step 2: 建立 code-review.md**

建立 `reference/playbooks/code-review.md`，內容如下（逐字）：

```markdown
---
name: code_review
match: ["code review", "程式碼審查", "審查", "review the diff", "review changes", "審 code"]
---

## Executor Principles
- 最高原則：讓整體程式碼健康度隨時間提升；不追求完美，達到「明確讓系統更健康」即可通過
- 依序逐項看：Design（架構/整合是否合理）→ Functionality（含邊界、並行、像使用者一樣思考）→ Complexity（是否過度複雜、能否被快速理解）→ Tests（測試是否齊全且設計良好）→ Naming → Comments（解釋 why 而非 what）→ Style/Consistency（風格指南為準）→ Documentation
- 每行都看（Every Line），並理解周邊 Context 與影響範圍
- 也要肯定做得好的地方（Good Things）
- 每個發現給具體 file:line，分級 Critical / Warning / Info（Nit），並區分「必修」與「建議」

## Critic Checklist
- [ ] 是否涵蓋 Design / Functionality / Complexity / Tests 等核心面向
- [ ] 每個發現是否具體可定位（file:line）且分級
- [ ] 是否區分必修（阻擋合併）與建議（nit）
- [ ] 結論是否回答「此變更是否提升整體 code health」
```

- [ ] **Step 3: 建立 integration-test.md**

建立 `reference/playbooks/integration-test.md`，內容如下（逐字）：

```markdown
---
name: integration_test
match: ["integration test", "整合測試", "整合測", "整合測程式"]
---

## Executor Principles
- 測試焦點是「跨元件/跨模組在邊界上的真實協作」，而非孤立邏輯（孤立邏輯歸 unit test）
- 凡有序列化/反序列化、外部協作者（DB、API、queue）的讀寫，都要有整合測試驗證資料流與契約
- 外部依賴盡量在本地跑（容器 / infra 的 test double），不打正式環境
- 採 narrow integration test：聚焦單一邊界，端到端資料流走完整路徑
- 清理副作用（測試後還原狀態），確保 Repeatable
- 寫完必須實際執行並回報 pass/fail 與指令

## Critic Checklist
- [ ] 測試是否真的跨越邊界（真實協作），而非偽裝成整合測試的 unit test
- [ ] 是否覆蓋序列化/反序列化、外部協作者的讀寫與 API 契約
- [ ] 是否實際被執行且通過（附輸出，否則 REJECT）
- [ ] 副作用是否清理、是否可重複執行
```

- [ ] **Step 4: Commit**

```bash
git add reference/playbooks/
git commit -m "feat(playbooks): add unit-test/code-review/integration-test principle files"
```

---

## Task 2: 建立 `servers/playbooks.py`（載入 + 分類 + 格式化）

**Files:**
- Create: `servers/playbooks.py`
- Test: `tests/test_playbooks.py`

- [ ] **Step 1: 寫 failing test（載入與分類）**

建立 `tests/test_playbooks.py`：

```python
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
```

- [ ] **Step 2: 跑測試確認 FAIL**

Run: `python -m pytest tests/test_playbooks.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'servers.playbooks'`）

- [ ] **Step 3: 實作 `servers/playbooks.py`**

建立 `servers/playbooks.py`：

```python
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
from typing import Dict, List, Optional

# playbook 檔案位於 han-agents 安裝目錄（與 servers/ 同層的 reference/）
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PLAYBOOK_DIR = os.path.join(_BASE_DIR, "reference", "playbooks")

_CACHE: Optional[Dict[str, "Playbook"]] = None


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
        match: ["kw1", "kw2", ...]
        ---
        ## Executor Principles
        ...
        ## Critic Checklist
        ...
    """
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
                match = json.loads(raw)
            except Exception:
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
    """載入所有 playbook（快取）。目錄缺失 → 回空 dict（fail-open）。"""
    global _CACHE
    if _CACHE is not None and not force_reload:
        return _CACHE
    result: Dict[str, Playbook] = {}
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
```

- [ ] **Step 4: 跑測試確認 PASS**

Run: `python -m pytest tests/test_playbooks.py -v`
Expected: PASS（5 個測試全過）

- [ ] **Step 5: 加 fail-open 測試**

在 `tests/test_playbooks.py` 末端追加：

```python
class TestFailOpen:
    def test_missing_dir_returns_empty(self, monkeypatch):
        import servers.playbooks as pbmod
        monkeypatch.setattr(pbmod, "_PLAYBOOK_DIR", "/nonexistent/path/xyz")
        monkeypatch.setattr(pbmod, "_CACHE", None)
        assert pbmod.load_playbooks(force_reload=True) == {}
        assert pbmod.resolve_playbook("write unit tests for x") is None
```

- [ ] **Step 6: 跑測試確認 PASS**

Run: `python -m pytest tests/test_playbooks.py -v`
Expected: PASS（6 個測試全過）

- [ ] **Step 7: Commit**

```bash
git add servers/playbooks.py tests/test_playbooks.py
git commit -m "feat(playbooks): add loader, keyword resolver, prompt section formatters"
```

---

## Task 3: 在 facade prompt builder 注入 playbook

**Files:**
- Modify: `servers/facade.py`（`_build_executor_prompt` 約 1860、`_build_critic_prompt` 約 1940）
- Test: `tests/test_playbooks.py`（追加）

- [ ] **Step 1: 寫 failing test（注入行為）**

在 `tests/test_playbooks.py` 追加：

```python
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
```

- [ ] **Step 2: 跑測試確認 FAIL**

Run: `python -m pytest tests/test_playbooks.py::TestPromptInjection -v`
Expected: FAIL（prompt 不含原則字樣）

- [ ] **Step 3: 修改 `_build_executor_prompt`**

在 `servers/facade.py` 的 `_build_executor_prompt` 中，於組 `prompt` f-string 前加入 playbook 區塊計算。找到這段（約 1910-1937）：

```python
    rejection_section = ""
    if rejection_context:
```

在它**之前**插入：

```python
    # Playbook 原則注入（依描述分類；未命中則為空，fail-open）
    playbook_section = ""
    try:
        from servers.playbooks import resolve_playbook, executor_section
        pb = resolve_playbook(description)
        if pb:
            playbook_section = "\n" + executor_section(pb)
    except Exception:
        playbook_section = ""
```

然後把 f-string 中的：

```python
{context_section}
{rejection_section}
{policy_section}
## Instructions
```

改為：

```python
{context_section}
{rejection_section}
{playbook_section}
{policy_section}
## Instructions
```

- [ ] **Step 4: 修改 `_build_critic_prompt`**

在 `servers/facade.py` 的 `_build_critic_prompt` 中，找到 `policy_section = _build_guardrail_policy_section('critic')` 之後、組 prompt 之前，插入：

```python
    # Playbook 驗收清單注入（依原始任務描述分類；fail-open）
    playbook_section = ""
    try:
        from servers.playbooks import resolve_playbook, critic_section
        pb = resolve_playbook(description)
        if pb:
            playbook_section = "\n" + critic_section(pb)
    except Exception:
        playbook_section = ""
```

然後把 prompt f-string 中的：

```python
3. Is the code quality acceptable?

{policy_section}
```

改為：

```python
3. Is the code quality acceptable?
{playbook_section}
{policy_section}
```

- [ ] **Step 5: 跑測試確認 PASS**

Run: `python -m pytest tests/test_playbooks.py -v`
Expected: PASS（全部，含 TestPromptInjection 3 個）

- [ ] **Step 6: 確認既有 facade 測試無回歸**

Run: `python -m pytest tests/test_facade.py -v`
Expected: PASS（沿用既有，無新失敗）

- [ ] **Step 7: Commit**

```bash
git add servers/facade.py tests/test_playbooks.py
git commit -m "feat(playbooks): inject principles into executor/critic prompts (fail-open)"
```

---

## Task 4: 修正 `detect_coverage_gaps`（靜默截斷 + tested_by + method）

**Files:**
- Modify: `servers/code_graph.py`（`get_code_nodes` 271-294、`get_code_edges` 295-323）
- Modify: `servers/drift.py`（`detect_coverage_gaps` 355-415）
- Test: `tests/test_drift.py`（追加）

- [ ] **Step 1: 寫 failing test**

在 `tests/test_drift.py` 末端追加（檔案開頭已有 `sys.path` 設定；若無，參照其他 test 檔加上）：

```python
class TestCoverageGapsImprovements:
    def _seed(self, db_path):
        import sqlite3
        conn = sqlite3.connect(db_path)
        nodes = [
            ("func.a.py:foo", "cov", "function", "foo", "a.py", 1, 5, "python"),
            ("method.a.py:Bar.baz", "cov", "method", "baz", "a.py", 6, 10, "python"),
            ("func.a.py:tested_fn", "cov", "function", "tested_fn", "a.py", 11, 15, "python"),
        ]
        for n in nodes:
            conn.execute("""INSERT INTO code_nodes
                (id, project, kind, name, file_path, line_start, line_end, language)
                VALUES (?,?,?,?,?,?,?,?)""", n)
        # tested_by 邊：tested_fn 被測試覆蓋
        conn.execute("""INSERT INTO code_edges (project, from_id, to_id, kind)
            VALUES ('cov', 'func.a.py:tested_fn', 'test.a_test.py:test_it', 'tested_by')""")
        conn.commit()
        conn.close()

    def test_method_included_and_tested_by_excluded(self, mock_db_path):
        self._seed(mock_db_path)
        from servers.drift import detect_coverage_gaps
        gaps = detect_coverage_gaps("cov")
        names = {g["name"] for g in gaps}
        assert "foo" in names          # 未測 function → gap
        assert "baz" in names          # method 也要納入 → gap
        assert "tested_fn" not in names  # 有 tested_by 邊 → 非 gap
```

- [ ] **Step 2: 跑測試確認 FAIL**

Run: `python -m pytest tests/test_drift.py::TestCoverageGapsImprovements -v`
Expected: FAIL（`baz` 未被納入，或 `tested_fn` 仍被當 gap）

- [ ] **Step 3: 為 getter 加 `offset` 參數（修靜默截斷的基礎）**

在 `servers/code_graph.py` 的 `get_code_nodes` 簽名加入 `offset`：

```python
def get_code_nodes(
    project: str,
    kind: str = None,
    file_path: str = None,
    limit: int = 100,
    offset: int = 0
) -> List[Dict]:
```

並把該函式內的：

```python
        query += " ORDER BY file_path, line_start LIMIT ?"
        params.append(limit)
```

改為：

```python
        query += " ORDER BY file_path, line_start LIMIT ? OFFSET ?"
        params.append(limit)
        params.append(offset)
```

同樣為 `get_code_edges` 加 `offset`：

```python
def get_code_edges(
    project: str,
    from_id: str = None,
    to_id: str = None,
    kind: str = None,
    limit: int = 100,
    offset: int = 0
) -> List[Dict]:
```

並把：

```python
        query += " LIMIT ?"
        params.append(limit)
```

改為：

```python
        query += " LIMIT ? OFFSET ?"
        params.append(limit)
        params.append(offset)
```

- [ ] **Step 4: 改寫 `detect_coverage_gaps`**

把 `servers/drift.py` 的 `detect_coverage_gaps`（355-415）整段替換為：

```python
def detect_coverage_gaps(project: str) -> List[Dict]:
    """
    偵測測試覆蓋缺口

    找出沒有對應測試的重要程式碼。
    - 分批取完所有節點/邊，避免靜默截斷漏報（大專案）
    - 同時認 'tests' 與 'tested_by' 兩種測試關係邊
    - 重要節點納入 function / class / api / method
    """
    from servers.code_graph import get_code_nodes, get_code_edges

    # 分批取完所有 nodes（修靜默截斷）
    nodes = _fetch_all(lambda off: get_code_nodes(project, limit=500, offset=off))

    # 收集被測試覆蓋的目標 id（兩種邊方向都認）
    covered_ids = set()
    for e in _fetch_all(lambda off: get_code_edges(project, kind='tests', limit=500, offset=off)):
        covered_ids.add(e['to_id'])      # test --tests--> target
    for e in _fetch_all(lambda off: get_code_edges(project, kind='tested_by', limit=500, offset=off)):
        covered_ids.add(e['from_id'])    # target --tested_by--> test

    gaps = []
    important_kinds = {'function', 'class', 'api', 'method'}

    for node in nodes:
        if node['kind'] not in important_kinds:
            continue
        if 'test' in node.get('file_path', '').lower():
            continue
        if node.get('visibility') == 'private':
            continue

        has_test = node['id'] in covered_ids

        # 檔名啟發式 fallback
        if not has_test:
            file_path = node.get('file_path', '')
            file_stem = os.path.splitext(os.path.basename(file_path))[0]
            test_patterns = [f"{file_stem}.test", f"{file_stem}.spec", f"test_{file_stem}"]
            for test_node in nodes:
                if test_node['kind'] == 'file' and 'test' in test_node.get('file_path', '').lower():
                    test_file = os.path.basename(test_node.get('file_path', '')).lower()
                    if any(p.lower() in test_file for p in test_patterns):
                        has_test = True
                        break

        if not has_test:
            gaps.append({
                'node_id': node['id'],
                'node_kind': node['kind'],
                'name': node['name'],
                'file_path': node.get('file_path'),
                'line_start': node.get('line_start'),
                'has_test': False
            })

    return gaps


def _fetch_all(fetch_page, page_size: int = 500) -> List[Dict]:
    """分批取完所有結果，避免單次 LIMIT 靜默截斷。"""
    out: List[Dict] = []
    offset = 0
    while True:
        page = fetch_page(offset)
        out.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
    return out
```

> 註：`_fetch_all` 的 `page_size` 須與 lambda 內 `limit=500` 一致，否則終止條件失準。

- [ ] **Step 5: 跑測試確認 PASS**

Run: `python -m pytest tests/test_drift.py -v`
Expected: PASS（含新 `TestCoverageGapsImprovements`，且既有 drift 測試無回歸）

- [ ] **Step 6: 確認 code_graph 測試無回歸**

Run: `python -m pytest tests/ -k "code_graph or backend or extractor" -v`
Expected: PASS（offset 為 additive 參數，預設 0，不影響既有呼叫）

- [ ] **Step 7: Commit**

```bash
git add servers/code_graph.py servers/drift.py tests/test_drift.py
git commit -m "fix(drift): paginate coverage scan, recognize tested_by edges and method nodes"
```

---

## Task 5: `recipe_code_review`

**Files:**
- Modify: `servers/recipes.py`
- Test: `tests/test_recipes.py`

- [ ] **Step 1: 寫 failing test**

建立 `tests/test_recipes.py`：

```python
"""Recipe 任務樹建立測試"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _seed_files(db_path, project, files):
    import sqlite3
    conn = sqlite3.connect(db_path)
    for i, fp in enumerate(files):
        conn.execute("""INSERT INTO code_nodes
            (id, project, kind, name, file_path, line_start, line_end, language)
            VALUES (?,?,?,?,?,?,?,?)""",
            (f"file.{fp}", project, "file", os.path.basename(fp), fp, 1, 50, "python"))
    conn.commit()
    conn.close()


class TestRecipeCodeReview:
    def test_builds_tasks_from_target_path(self, mock_db_path, monkeypatch, tmp_path):
        _seed_files(mock_db_path, "cr",
                    ["servers/foo.py", "servers/bar.py", "tests/test_foo.py"])
        # 避免實跑 ensure_project 的 sync（已手動 seed）
        from servers import recipes
        monkeypatch.setattr(recipes, "_ensure_synced",
                            lambda p, path: {"test_tool": "pytest"})
        result = recipes.recipe_code_review(
            "cr", str(tmp_path), target_path="servers/")
        assert result["epic_id"] is not None
        assert result["task_count"] == 2  # 跳過 tests/ 下檔案

    def test_no_target_no_git_returns_zero(self, mock_db_path, monkeypatch, tmp_path):
        from servers import recipes
        monkeypatch.setattr(recipes, "_ensure_synced",
                            lambda p, path: {"test_tool": "pytest"})
        # tmp_path 非 git repo，且未給 target_path
        result = recipes.recipe_code_review("cr2", str(tmp_path))
        assert result["task_count"] == 0
        assert "target_path" in result["message"]
```

- [ ] **Step 2: 跑測試確認 FAIL**

Run: `python -m pytest tests/test_recipes.py::TestRecipeCodeReview -v`
Expected: FAIL（`AttributeError: module 'servers.recipes' has no attribute 'recipe_code_review'`）

- [ ] **Step 3: 在 `servers/recipes.py` 加入 helper 與 recipe**

在 `servers/recipes.py` 的 import 區下方加入 helper（供測試 monkeypatch 與重用）：

```python
import subprocess


def _ensure_synced(project_name: str, project_path: str) -> Dict:
    """確保專案已初始化並同步 Code Graph，回 tech_stack。"""
    from servers.project import ensure_project
    proj = ensure_project(project_name, project_path)
    return proj.get('tech_stack', {})


def _list_source_files(project_name: str, target_path: str = None) -> List[str]:
    """從 Code Graph 取 file 節點，過濾 target_path、跳過測試檔。"""
    from servers.code_graph import get_code_nodes
    files = []
    offset = 0
    while True:
        page = get_code_nodes(project_name, kind='file', limit=500, offset=offset)
        files.extend(page)
        if len(page) < 500:
            break
        offset += 500
    result = []
    for n in files:
        fp = n.get('file_path') or ''
        if 'test' in fp.lower():
            continue
        if target_path:
            tp = target_path.rstrip('/')
            if not (fp.startswith(tp) or fp.startswith('./' + tp)):
                continue
        result.append(fp)
    return sorted(set(result))


def _git_changed_files(project_path: str, diff_base: str) -> Optional[List[str]]:
    """回傳 git diff 變更檔；非 git repo 或失敗回 None。"""
    try:
        out = subprocess.run(
            ["git", "-C", project_path, "diff", "--name-only", diff_base],
            capture_output=True, text=True, timeout=10)
        if out.returncode != 0:
            return None
        return [f for f in out.stdout.splitlines() if f.strip()]
    except Exception:
        return None
```

接著加入 recipe 本體：

```python
def recipe_code_review(
    project_name: str,
    project_path: str,
    target_path: str = None,
    diff_base: str = "HEAD",
    max_tasks: int = 20
) -> Dict:
    """為待審查的檔案建立 code review 任務樹。

    目標來源：
    - 有 target_path → 取該路徑下的原始碼檔（跳過測試檔）
    - 否則 → git diff --name-only <diff_base> 的變更檔
    - 第一次/無 git/無 diff 且未給 target_path → task_count=0 + 明確訊息
    """
    from servers.tasks import create_task, create_subtask

    _ensure_synced(project_name, project_path)

    if target_path:
        files = _list_source_files(project_name, target_path)
    else:
        changed = _git_changed_files(project_path, diff_base)
        if changed is None:
            return {'epic_id': None, 'task_count': 0, 'story_count': 0,
                    'message': '非 git repo 或無法取得 diff。請指定 target_path。'}
        files = [f for f in changed if 'test' not in f.lower()]

    if not files:
        return {'epic_id': None, 'task_count': 0, 'story_count': 0,
                'message': '沒有可審查的檔案。請指定 target_path 或先有改動。'}

    epic_id = create_task(
        project=project_name,
        description=f"Code Review: {len(files)} files",
        priority=7, task_level='epic')

    task_count = 0
    for fp in files:
        if task_count >= max_tasks:
            break
        story_id = create_task(
            project=project_name,
            description=f"Code review {fp}",
            task_level='story', epic_id=epic_id, priority=7)
        create_subtask(
            parent_id=story_id,
            description=f"Code review {fp}. 依 playbook 原則逐項審查並分級回報。",
            assigned_agent='executor', requires_validation=True,
            task_level='task', epic_id=epic_id, story_id=story_id)
        task_count += 1

    return {
        'epic_id': epic_id, 'task_count': task_count, 'story_count': task_count,
        'message': (f"Created {task_count} code review tasks. "
                    f"Use get_next_dispatch('{epic_id}', ...) to start."),
    }
```

- [ ] **Step 4: 跑測試確認 PASS**

Run: `python -m pytest tests/test_recipes.py::TestRecipeCodeReview -v`
Expected: PASS（2 個測試）

- [ ] **Step 5: Commit**

```bash
git add servers/recipes.py tests/test_recipes.py
git commit -m "feat(recipes): add recipe_code_review (git diff or target_path)"
```

---

## Task 6: `recipe_integration_tests`

**Files:**
- Modify: `servers/recipes.py`
- Test: `tests/test_recipes.py`（追加）

- [ ] **Step 1: 寫 failing test**

在 `tests/test_recipes.py` 追加：

```python
class TestRecipeIntegrationTests:
    def test_groups_by_module(self, mock_db_path, monkeypatch, tmp_path):
        _seed_files(mock_db_path, "it",
                    ["servers/auth/login.py", "servers/auth/token.py",
                     "servers/user/profile.py"])
        from servers import recipes
        monkeypatch.setattr(recipes, "_ensure_synced",
                            lambda p, path: {"test_tool": "pytest"})
        result = recipes.recipe_integration_tests(
            "it", str(tmp_path), target_path="servers/")
        assert result["epic_id"] is not None
        # auth 與 user 兩個模組 → 2 個 story
        assert result["story_count"] == 2

    def test_task_description_classifiable(self, mock_db_path, monkeypatch, tmp_path):
        _seed_files(mock_db_path, "it2", ["servers/auth/login.py"])
        from servers import recipes
        from servers.playbooks import resolve_playbook
        monkeypatch.setattr(recipes, "_ensure_synced",
                            lambda p, path: {"test_tool": "pytest"})
        recipes.recipe_integration_tests("it2", str(tmp_path), target_path="servers/")
        # 任務描述須能被 playbook 分類為 integration_test（閉環驗證）
        pb = resolve_playbook("Write integration tests for module servers/auth")
        assert pb is not None and pb.name == "integration_test"
```

- [ ] **Step 2: 跑測試確認 FAIL**

Run: `python -m pytest tests/test_recipes.py::TestRecipeIntegrationTests -v`
Expected: FAIL（無 `recipe_integration_tests`）

- [ ] **Step 3: 加入 recipe 本體**

在 `servers/recipes.py` 加入：

```python
def recipe_integration_tests(
    project_name: str,
    project_path: str,
    target_path: str = None,
    max_tasks: int = 20
) -> Dict:
    """為各模組建立整合測試任務樹（以模組/目錄為單位，非單一 function）。"""
    from collections import defaultdict
    from servers.tasks import create_task, create_subtask

    tech = _ensure_synced(project_name, project_path)
    test_tool = tech.get('test_tool', 'unknown')

    files = _list_source_files(project_name, target_path)
    if not files:
        return {'epic_id': None, 'task_count': 0, 'story_count': 0,
                'message': '沒有可建立整合測試的檔案。請指定 target_path 或先 sync。'}

    # 以「目錄」為模組分組（取檔案所在目錄）
    by_module = defaultdict(list)
    for fp in files:
        module = os.path.dirname(fp) or fp
        by_module[module].append(fp)

    epic_id = create_task(
        project=project_name,
        description=f"Integration Tests: {len(by_module)} modules",
        priority=7, task_level='epic')

    task_count = 0
    for module in sorted(by_module.keys()):
        if task_count >= max_tasks:
            break
        story_id = create_task(
            project=project_name,
            description=f"Integration tests for module {module}",
            task_level='story', epic_id=epic_id, priority=7)
        create_subtask(
            parent_id=story_id,
            description=(f"Write integration tests for module {module}. "
                        f"涵蓋跨檔案協作與邊界。Test tool: {test_tool}"),
            assigned_agent='executor', requires_validation=True,
            task_level='task', epic_id=epic_id, story_id=story_id)
        task_count += 1

    return {
        'epic_id': epic_id, 'task_count': task_count,
        'story_count': len(by_module), 'modules': sorted(by_module.keys()),
        'message': (f"Created {task_count} integration test tasks across "
                    f"{len(by_module)} modules. "
                    f"Use get_next_dispatch('{epic_id}', ...) to start."),
    }
```

- [ ] **Step 4: 跑測試確認 PASS**

Run: `python -m pytest tests/test_recipes.py -v`
Expected: PASS（全部 recipe 測試）

- [ ] **Step 5: Commit**

```bash
git add servers/recipes.py tests/test_recipes.py
git commit -m "feat(recipes): add recipe_integration_tests (group by module)"
```

---

## Task 7: 註冊 recipes + 更新 SCHEMA docstring

**Files:**
- Modify: `servers/recipes.py`（`RECIPES` 約 181、`SCHEMA` docstring 14-34）
- Test: `tests/test_recipes.py`（追加）

- [ ] **Step 1: 寫 failing test**

在 `tests/test_recipes.py` 追加：

```python
class TestRecipeRegistry:
    def test_all_three_registered(self):
        from servers.recipes import RECIPES
        assert set(RECIPES.keys()) >= {"unit_tests", "code_review", "integration_tests"}

    def test_run_recipe_dispatches_code_review(self, mock_db_path, monkeypatch, tmp_path):
        from servers import recipes
        monkeypatch.setattr(recipes, "_ensure_synced",
                            lambda p, path: {"test_tool": "pytest"})
        result = recipes.run_recipe("code_review", project_name="rr",
                                    project_path=str(tmp_path), target_path="servers/")
        assert "task_count" in result
```

- [ ] **Step 2: 跑測試確認 FAIL**

Run: `python -m pytest tests/test_recipes.py::TestRecipeRegistry -v`
Expected: FAIL（`code_review` 未註冊）

- [ ] **Step 3: 更新 `RECIPES` 註冊表**

把 `servers/recipes.py` 的：

```python
# Recipe registry
RECIPES = {
    'unit_tests': recipe_unit_tests,
}
```

改為：

```python
# Recipe registry
RECIPES = {
    'unit_tests': recipe_unit_tests,
    'code_review': recipe_code_review,
    'integration_tests': recipe_integration_tests,
}
```

- [ ] **Step 4: 更新 `SCHEMA` docstring**

把 `servers/recipes.py` 開頭的 `SCHEMA`（14-34）整段替換為：

```python
SCHEMA = """
=== Recipes ===

recipe_unit_tests(project_name, project_path, target_path=None, max_tasks=20) -> Dict
    為未測試的程式碼建立 unit test 任務樹。
    自動：sync Code Graph → 偵測覆蓋缺口 → 建立 Epic/Story/Task

recipe_code_review(project_name, project_path, target_path=None, diff_base="HEAD", max_tasks=20) -> Dict
    為待審查檔案建立 code review 任務樹。
    目標來源：target_path（指定路徑）或 git diff（預設 HEAD）。
    第一次/無 git/無 diff 且未給 target_path → task_count=0 + 明確訊息。

recipe_integration_tests(project_name, project_path, target_path=None, max_tasks=20) -> Dict
    為各模組建立整合測試任務樹（以目錄為模組分組）。

所有 recipe 回傳含 'epic_id' 供 get_next_dispatch() 消費。

run_recipe(name, **kwargs) -> Dict
    按名稱執行 recipe。
    Available: 'unit_tests', 'code_review', 'integration_tests'
"""
```

- [ ] **Step 5: 跑測試確認 PASS**

Run: `python -m pytest tests/test_recipes.py -v`
Expected: PASS（全部）

- [ ] **Step 6: Commit**

```bash
git add servers/recipes.py tests/test_recipes.py
git commit -m "feat(recipes): register code_review/integration_tests + sync SCHEMA docstring"
```

---

## Task 8: 更新前門文件（SKILL.md + pfc.md 清理）

**Files:**
- Modify: `SKILL.md`（約 163）
- Modify: `reference/agents/pfc.md`

- [ ] **Step 1: 更新 SKILL.md recipe 清單**

把 `SKILL.md` 的：

```markdown
**Available Recipes**: `unit_tests` (more coming)
```

改為：

```markdown
**Available Recipes**: `unit_tests`、`code_review`、`integration_tests`

```python
from servers.recipes import run_recipe
# 使用者：「幫 servers/ 寫單元測試」
run_recipe('unit_tests', project_name='p', project_path='/path', target_path='servers/')
# 使用者：「code review 這次改動」
run_recipe('code_review', project_name='p', project_path='/path')  # 預設 git diff HEAD
# 使用者：「幫 auth 模組寫整合測試」
run_recipe('integration_tests', project_name='p', project_path='/path', target_path='servers/auth/')
```
```

- [ ] **Step 2: 更新 pfc.md 路由說明**

在 `reference/agents/pfc.md` 中，找到描述任務規劃/recipe 的段落（搜尋 `recipe` 或 `unit_test`；若無相關段落，則在「任務分解」相關章節後）新增一段：

```markdown
## Recipe 路由（測試/審查類任務）

當使用者意圖明確屬於下列類型時，優先用 run_recipe 自動建任務樹，不必手動逐一 create_task：

| 使用者意圖 | recipe | 備註 |
|-----------|--------|------|
| 寫單元測試 / unit test | `unit_tests` | 自動偵測覆蓋缺口 |
| code review / 審查改動 | `code_review` | 預設審 git diff，可傳 target_path |
| 整合測試 / integration test | `integration_tests` | 以模組為單位 |

範例：
```python
from servers.recipes import run_recipe
result = run_recipe('unit_tests', project_name=PROJECT,
                    project_path=PROJECT_PATH, target_path='servers/')
# 之後用 get_next_dispatch(result['epic_id'], ...) 驅動派工
```

這些任務的品質原則由 playbook 自動注入 executor/critic prompt（reference/playbooks/），PFC 無須重複描述測試/審查細則。
```

- [ ] **Step 3: Commit**

```bash
git add SKILL.md reference/agents/pfc.md
git commit -m "docs: route test/review intents to recipes; refresh recipe list"
```

---

## Task 9: 全量驗收

- [ ] **Step 1: 跑本功能全部測試**

Run: `python -m pytest tests/test_playbooks.py tests/test_recipes.py tests/test_drift.py -v`
Expected: PASS（全綠）

- [ ] **Step 2: 跑既有測試確認無回歸**

Run: `python -m pytest tests/ -q`
Expected: PASS（無新失敗；既有跳過項維持）

- [ ] **Step 3: 確認無殘留 placeholder/TODO**

Run: `grep -rn "TODO\|TBD\|FIXME" servers/playbooks.py servers/recipes.py reference/playbooks/`
Expected: 無輸出（或僅既有無關項）

- [ ] **Step 4: 最終 commit（若有殘留變更）**

```bash
git add -A && git commit -m "test(playbooks): full suite green for task playbooks feature" || echo "nothing to commit"
```

---

## Self-Review 註記

- **Spec 覆蓋**：playbook 3 檔（T1）、loader/resolver（T2）、注入（T3）、Code Graph 小改（T4）、code_review recipe（T5）、integration recipe（T6）、註冊+docstring 清理（T7）、SKILL.md/pfc.md 清理（T8）、驗收（T9）。spec §1-§9 全有對應任務。
- **型別一致**：`Playbook` dataclass 欄位（`name`/`match`/`executor_principles`/`critic_checklist`）在 T2 定義，T3 透過 `executor_section`/`critic_section` 使用，一致。recipe 回傳 dict 的 key（`epic_id`/`task_count`/`story_count`/`message`）跨 T5-T7 一致。
- **fail-open**：T2 Step5 + T3 的 try/except 雙重保障。
- **無 placeholder**：所有 code step 均含完整程式碼與精確指令。
