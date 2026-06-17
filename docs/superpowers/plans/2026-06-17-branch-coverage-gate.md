# 分支覆蓋率硬關（Branch-Coverage Gate）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓 `/han:unit-test` 派工迴圈在 executor 寫完測試後，用 `coverage.py --branch` 工具量測「本次 target 行範圍內」的分支覆蓋率；有未覆蓋分支就以具體行號自動走 `finish_validation` 退回 executor 補測——確定性、不靠 LLM 判讀。

**Architecture:** 新增單一職責模組 `servers/coverage.py`（純量測：跑 `coverage run --branch`、用行範圍把 branch arc 歸因到 target）。資料層讓 `detect_coverage_gaps` 帶 `line_end`、`recipe_unit_tests` 把結構化 `coverage_targets` 存進 task metadata。facade 加一層 `get_next_dispatch_gated`：當底層派的是 critic 時先跑 gate，量到未覆蓋就 `finish_validation(approved=False)` 並改派 executor 重試；其餘一律 fail-open 照常派 LLM critic。`/han:unit-test` 指令把迴圈的 `get_next_dispatch` 換成 `get_next_dispatch_gated`。playbook 補 executor/critic 原則。

**Tech Stack:** Python、`coverage.py`（`--branch`，subprocess CLI）、pytest、HAN recipe + playbook + facade dispatch。

**Spec:** [docs/superpowers/specs/2026-06-17-branch-coverage-gate-design.md](../specs/2026-06-17-branch-coverage-gate-design.md)

---

## 已驗證的關鍵事實（實作前已讀碼／實測確認）

- **Code Graph 已有 `line_end`：** `code_nodes` 表有 `line_end` 欄，`get_code_nodes` 用 `SELECT *` 回傳含此欄（[servers/code_graph.py:282](../../../servers/code_graph.py)）。`detect_coverage_gaps` 只是漏帶。
- **`coverage json` 格式（實測 coverage 7.14）：** `data['files'][path]` 有 `missing_branches`、`executed_branches`（皆為 `[[src_line, dest_line], ...]`）、`executed_lines`、`excluded_lines`。`# pragma: no cover` 的行會進 `excluded_lines`、**不會**出現在 `missing_branches`——pragma 自動被尊重，無需特別處理。
- **`finish_validation` 簽名：** `finish_validation(task_id, original_task_id, approved, issues=None, suggestions=None)`（第一個位置參數是 **critic 任務 id**，[servers/facade.py:1229](../../../servers/facade.py)）。rejected 路徑自動 `rejection_count++`、達 `MAX_RETRIES` 轉 blocked + 開 human-review、否則設 pending/phase=execution、回 `next_action='resume_executor'`。
- **critic dispatch：** `get_next_dispatch` step 1 對 unvalidated task 立刻 `reserve_critic_task()` 並回傳 `subagent_type='critic'`、`task_id=<critic task id>`（[servers/facade.py:1646-1671](../../../servers/facade.py)）。`reserve_critic_task` 回傳的 dict 含 `original_task_id`、`result`（executor 輸出）（[servers/tasks.py:553-557](../../../servers/tasks.py)）。
- **task metadata：** `tasks.metadata` 是 TEXT(JSON) 欄；`get_task` 回傳已 parse 的 `metadata` dict（[servers/tasks.py:243](../../../servers/tasks.py)）。branch 另存於同一 dict 的 `branch` 鍵，其 setter 做 read-merge-write（[servers/tasks.py:705-718](../../../servers/tasks.py)），故新增 `coverage_targets` 鍵不會被 branch 寫入清掉。
- **CI 只裝 pytest：** [.github/workflows/tests.yml:22](../../../.github/workflows/tests.yml)。量測「ok」路徑的測試需要 coverage，故 CI 與 requirements 要補裝。

---

## File Structure

- **Create** `servers/coverage.py` — 純量測模組。`measure_branch_coverage()`（跑工具、回逐 target 結果）、`derive_test_targets()`（從 executor 輸出/慣例推出要跑的測試檔）、`format_missing_issues()`（把未覆蓋分支轉成人類可讀 issue 字串）。不碰 DB、不碰 facade。
- **Modify** `servers/drift.py` — `detect_coverage_gaps` 的 gap dict 補 `line_end`。
- **Modify** `servers/tasks.py` — `create_subtask` 加 `metadata: Dict = None` 參數，INSERT 時序列化進 metadata 欄。
- **Modify** `servers/recipes.py` — `recipe_unit_tests` 建 task 時把該 task 的 `coverage_targets`（結構化清單）寫進 metadata。
- **Modify** `servers/facade.py` — critic dispatch 回傳補 `original_task_id`；新增 `run_coverage_gate()`（呼叫 coverage + finish_validation）與 `get_next_dispatch_gated()`（包住 get_next_dispatch 的 gate 迴圈）。
- **Modify** `commands/han/unit-test.md` — 迴圈改呼叫 `get_next_dispatch_gated`。
- **Modify** `reference/playbooks/unit-test.md` — executor 補「每條分支要測到 + 結構化回報 TEST_TARGETS + pragma 說明理由」；critic 補「覆蓋已由工具上游強制；工具不可用時手動逐分支核對」。
- **Modify** `.github/workflows/tests.yml`、`requirements.txt` — 補裝 `coverage`。
- **Create** `tests/test_coverage_gate.py` — coverage.py 與 gate 的單元測試。
- **Modify** `tests/test_playbooks.py` — playbook 與指令 markdown 回歸測試。

---

## Task 1: 測試依賴 — 安裝 coverage

**Files:**
- Modify: `requirements.txt`
- Modify: `.github/workflows/tests.yml:19-22`

- [ ] **Step 1: 把 coverage 加進 requirements.txt**

在 `requirements.txt` 末尾新增一行：

```
coverage>=7.0
```

- [ ] **Step 2: CI 安裝 coverage**

把 [.github/workflows/tests.yml](../../../.github/workflows/tests.yml) 的安裝步驟（第 22 行）：

```yaml
          python -m pip install pytest
```

改成：

```yaml
          python -m pip install pytest coverage
```

- [ ] **Step 3: 本機確認 coverage 可用**

Run: `python3 -m coverage --version`
Expected: 印出 `Coverage.py, version 7.x ...`（若未裝先 `python3 -m pip install coverage`）。

- [ ] **Step 4: Commit**

```bash
git add requirements.txt .github/workflows/tests.yml
git commit -m "build: add coverage dep for branch-coverage gate tests"
```

---

## Task 2: 資料層 C1 — detect_coverage_gaps 帶 line_end

**Files:**
- Modify: `servers/drift.py:401-409`
- Test: `tests/test_coverage_gate.py`

- [ ] **Step 1: 寫失敗測試**

建立 `tests/test_coverage_gate.py`，內容：

```python
"""分支覆蓋率硬關：資料層、量測、gate 測試"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestCoverageGapHasLineEnd:
    def test_gap_dict_includes_line_end(self, monkeypatch):
        import servers.drift as drift

        fake_nodes = [{
            'id': 'n1', 'kind': 'function', 'name': 'foo',
            'file_path': 'servers/x.py', 'line_start': 10, 'line_end': 25,
            'visibility': 'public',
        }]
        monkeypatch.setattr(drift, '_fetch_all',
                            lambda fn, page_size=500: fake_nodes if 'nodes' in fn.__qualname__ or True else [])
        # 兩種 edge 查詢都回空 → 無覆蓋 → 一定成為 gap
        import servers.code_graph as cg
        monkeypatch.setattr(cg, 'get_code_nodes', lambda *a, **k: [])
        monkeypatch.setattr(cg, 'get_code_edges', lambda *a, **k: [])

        # _fetch_all 第一次（nodes）回 fake_nodes，之後（edges）回 []
        calls = {'n': 0}
        def fake_fetch(fn, page_size=500):
            calls['n'] += 1
            return fake_nodes if calls['n'] == 1 else []
        monkeypatch.setattr(drift, '_fetch_all', fake_fetch)

        gaps = drift.detect_coverage_gaps('proj')
        assert len(gaps) == 1
        assert gaps[0]['line_start'] == 10
        assert gaps[0]['line_end'] == 25
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `pytest tests/test_coverage_gate.py::TestCoverageGapHasLineEnd -v`
Expected: FAIL — `KeyError: 'line_end'`（gap dict 目前無此鍵）。

- [ ] **Step 3: 在 gap dict 補 line_end**

把 [servers/drift.py:401-409](../../../servers/drift.py) 的：

```python
        if not has_test:
            gaps.append({
                'node_id': node['id'],
                'node_kind': node['kind'],
                'name': node['name'],
                'file_path': node.get('file_path'),
                'line_start': node.get('line_start'),
                'has_test': False
            })
```

改成（多一行 `line_end`）：

```python
        if not has_test:
            gaps.append({
                'node_id': node['id'],
                'node_kind': node['kind'],
                'name': node['name'],
                'file_path': node.get('file_path'),
                'line_start': node.get('line_start'),
                'line_end': node.get('line_end'),
                'has_test': False
            })
```

- [ ] **Step 4: 跑測試確認通過**

Run: `pytest tests/test_coverage_gate.py::TestCoverageGapHasLineEnd -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add servers/drift.py tests/test_coverage_gate.py
git commit -m "feat(drift): detect_coverage_gaps carries line_end (C1)"
```

---

## Task 3: 資料層 C1 — create_subtask 接受 metadata

**Files:**
- Modify: `servers/tasks.py:141-196`
- Test: `tests/test_coverage_gate.py`

- [ ] **Step 1: 寫失敗測試**

在 `tests/test_coverage_gate.py` 末尾新增：

```python
class TestCreateSubtaskMetadata:
    def test_metadata_persisted_and_readable(self, mock_db_path):
        # mock_db_path（tests/conftest.py）把 servers.BRAIN_DB 指向隔離測試 DB，
        # 並已執行 tasks.metadata 欄遷移，故不污染真實 brain.db。
        from servers.tasks import create_task, create_subtask, get_task
        epic = create_task(project='proj', description='epic', task_level='epic')
        cov = [{'file_path': 'servers/x.py', 'name': 'foo',
                'line_start': 10, 'line_end': 25}]
        tid = create_subtask(parent_id=epic, description='write tests',
                             metadata={'coverage_targets': cov})
        task = get_task(tid)
        assert task['metadata']['coverage_targets'] == cov
```

> 註：DB 隔離一律用既有的 `mock_db_path` fixture（`tests/conftest.py`）——它 patch `servers.BRAIN_DB`、重置 `_db_initialized`、並補跑 `tasks.metadata` 等欄位遷移。**不要**用 `servers.db`（無此模組）。

- [ ] **Step 2: 跑測試確認失敗**

Run: `pytest tests/test_coverage_gate.py::TestCreateSubtaskMetadata -v`
Expected: FAIL — `create_subtask() got an unexpected keyword argument 'metadata'`。

- [ ] **Step 3: 加 metadata 參數**

把 [servers/tasks.py:141-148](../../../servers/tasks.py) 的簽名：

```python
def create_subtask(parent_id: str, description: str,
                   assigned_agent: str = 'executor',
                   depends_on: List[str] = None,
                   priority: int = 5,
                   requires_validation: bool = True,
                   task_level: str = 'task',
                   epic_id: str = None,
                   story_id: str = None) -> str:
```

改成（加最後一個參數）：

```python
def create_subtask(parent_id: str, description: str,
                   assigned_agent: str = 'executor',
                   depends_on: List[str] = None,
                   priority: int = 5,
                   requires_validation: bool = True,
                   task_level: str = 'task',
                   epic_id: str = None,
                   story_id: str = None,
                   metadata: Dict = None) -> str:
```

把 [servers/tasks.py:180-186](../../../servers/tasks.py) 的 INSERT：

```python
        task_id = str(uuid.uuid4())[:8]
        cursor.execute('''
            INSERT INTO tasks (id, parent_id, project, description, assigned_agent,
                             priority, requires_validation, task_level, epic_id, story_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (task_id, parent_id, project, description, assigned_agent, priority,
              1 if requires_validation else 0, task_level, epic_id, story_id))
```

改成（加 metadata 欄）：

```python
        task_id = str(uuid.uuid4())[:8]
        metadata_json = json.dumps(metadata, ensure_ascii=False) if metadata else None
        cursor.execute('''
            INSERT INTO tasks (id, parent_id, project, description, assigned_agent,
                             priority, requires_validation, task_level, epic_id, story_id,
                             metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (task_id, parent_id, project, description, assigned_agent, priority,
              1 if requires_validation else 0, task_level, epic_id, story_id,
              metadata_json))
```

確認檔案頂部已 `import json`（若無則加）。`Dict` 來自既有 `from typing import ...`（[servers/tasks.py](../../../servers/tasks.py) 頂部，若缺則補 `Dict`）。

- [ ] **Step 4: 跑測試確認通過**

Run: `pytest tests/test_coverage_gate.py::TestCreateSubtaskMetadata -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add servers/tasks.py tests/test_coverage_gate.py
git commit -m "feat(tasks): create_subtask accepts structured metadata (C1)"
```

---

## Task 4: 資料層 C1 — recipe_unit_tests 寫入 coverage_targets

**Files:**
- Modify: `servers/recipes.py:130-177`
- Test: `tests/test_coverage_gate.py`

- [ ] **Step 1: 寫失敗測試**

在 `tests/test_coverage_gate.py` 末尾新增：

```python
class TestRecipePersistsCoverageTargets:
    def test_task_metadata_has_coverage_targets(self):
        # 純函式單元：直接測「把 file_gaps 轉成 coverage_targets」的形狀
        from servers.recipes import _gaps_to_coverage_targets
        gaps = [
            {'name': 'foo', 'file_path': 'servers/x.py', 'line_start': 10, 'line_end': 25},
            {'name': 'bar', 'file_path': 'servers/x.py', 'line_start': 30, 'line_end': 40},
        ]
        targets = _gaps_to_coverage_targets(gaps)
        assert targets == [
            {'file_path': 'servers/x.py', 'name': 'foo', 'line_start': 10, 'line_end': 25},
            {'file_path': 'servers/x.py', 'name': 'bar', 'line_start': 30, 'line_end': 40},
        ]

    def test_recipe_actually_passes_metadata_to_created_task(self, mock_db_path, monkeypatch):
        # 整合測試（防假綠）：純函式對了不代表 recipe 真的把 metadata= 傳進 create_subtask。
        # monkeypatch 掉 gaps 來源與 ensure_project（避免真跑 sync / Code Graph），
        # 用 get_task 直接讀回建出來的 task，斷言 metadata.coverage_targets 真的落地。
        # 同時驗證：同檔 3 個 gaps 全進 metadata（不被 max_tasks 預算誤切）。
        import servers.drift as drift
        import servers.project as project
        fake_gaps = [
            {'name': 'foo', 'file_path': 'servers/x.py', 'line_start': 10, 'line_end': 25, 'has_test': False},
            {'name': 'bar', 'file_path': 'servers/x.py', 'line_start': 30, 'line_end': 40, 'has_test': False},
            {'name': 'baz', 'file_path': 'servers/x.py', 'line_start': 45, 'line_end': 60, 'has_test': False},
        ]
        monkeypatch.setattr(drift, 'detect_coverage_gaps', lambda *a, **k: list(fake_gaps))
        monkeypatch.setattr(project, 'ensure_project',
                            lambda *a, **k: {'tech_stack': {'test_tool': 'pytest'}})

        from servers.recipes import recipe_unit_tests
        from servers.tasks import get_task
        # max_tasks=1：同檔所有 gaps 仍須全進該 task 的 metadata（remaining 預算不可切 gaps）
        res = recipe_unit_tests('proj', '/tmp/proj', max_tasks=1)
        task_id = res['stories'][0]['task_ids'][0]
        meta = get_task(task_id)['metadata']
        assert meta['coverage_targets'] == [
            {'file_path': 'servers/x.py', 'name': 'foo', 'line_start': 10, 'line_end': 25},
            {'file_path': 'servers/x.py', 'name': 'bar', 'line_start': 30, 'line_end': 40},
            {'file_path': 'servers/x.py', 'name': 'baz', 'line_start': 45, 'line_end': 60},
        ]
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `pytest tests/test_coverage_gate.py::TestRecipePersistsCoverageTargets -v`
Expected: FAIL — `cannot import name '_gaps_to_coverage_targets'`。

- [ ] **Step 3: 加 helper 並在建 task 時帶入 metadata**

在 [servers/recipes.py](../../../servers/recipes.py) 的 `recipe_unit_tests` 之前（約第 49 行）新增純函式：

```python
def _gaps_to_coverage_targets(file_gaps: List[Dict]) -> List[Dict]:
    """把同一檔案的 coverage gaps 轉成 gate 用的結構化 target 清單。"""
    return [{
        'file_path': g.get('file_path'),
        'name': g.get('name'),
        'line_start': g.get('line_start'),
        'line_end': g.get('line_end'),
    } for g in file_gaps]
```

把 [servers/recipes.py:153-173](../../../servers/recipes.py)（Task 建立段）：

```python
        # Task: 每個檔案一個 executor task（batch 所有 gaps）
        remaining = max_tasks - task_count
        batch_names = gap_names[:remaining]

        task_desc = (
            f"Write unit tests for {file_path}. "
            f"Test targets: {', '.join(batch_names[:5])}"
        )
        if len(batch_names) > 5:
            task_desc += f" and {len(batch_names) - 5} more"
        task_desc += f". Test tool: {test_tool}"

        task_id = create_subtask(
            parent_id=story_id,
            description=task_desc,
            assigned_agent='executor',
            requires_validation=True,
            task_level='task',
            epic_id=epic_id,
            story_id=story_id,
        )
```

改成（帶 metadata；**移除 `[:remaining]` 切片**）：

```python
        # Task: 每個檔案一個 executor task（batch 該檔案「全部」gaps）
        # 注意：remaining = max_tasks - task_count 是「任務數預算」，只用來決定要不要
        # 再開下一個檔案的 task（迴圈頂部 if task_count >= max_tasks: break 已處理），
        # 絕不可拿來切某一檔案的 gaps——一個檔案一個 task，其所有 gaps 都要進 metadata。
        task_desc = (
            f"Write unit tests for {file_path}. "
            f"Test targets: {', '.join(gap_names[:5])}"
        )
        if len(gap_names) > 5:
            task_desc += f" and {len(gap_names) - 5} more"
        task_desc += f". Test tool: {test_tool}"

        task_id = create_subtask(
            parent_id=story_id,
            description=task_desc,
            assigned_agent='executor',
            requires_validation=True,
            task_level='task',
            epic_id=epic_id,
            story_id=story_id,
            metadata={'coverage_targets': _gaps_to_coverage_targets(file_gaps)},
        )
```

> 註：原碼的 `remaining`/`batch_names` 變數一併移除（描述改用 `gap_names`，顯示仍只取前 5 個並以「and N more」收尾）。

- [ ] **Step 4: 跑測試確認通過**

Run: `pytest tests/test_coverage_gate.py::TestRecipePersistsCoverageTargets -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add servers/recipes.py tests/test_coverage_gate.py
git commit -m "feat(recipes): persist coverage_targets into task metadata (C1)"
```

---

## Task 5: 量測核心 — servers/coverage.py measure_branch_coverage

**Files:**
- Create: `servers/coverage.py`
- Test: `tests/test_coverage_gate.py`

- [ ] **Step 1: 寫失敗測試（真跑 coverage 對 fixture）**

在 `tests/test_coverage_gate.py` 末尾新增。測試會在暫存目錄寫一個含分支的 source + 只覆蓋部分分支的測試，真的跑量測：

```python
import textwrap

import pytest


def _coverage_importable():
    try:
        import importlib.util
        return importlib.util.find_spec('coverage') is not None
    except Exception:
        return False


def _write_fixture(root):
    (root / 'sample.py').write_text(textwrap.dedent('''\
        def classify(n):
            if n > 0:
                return "pos"
            if n < 0:
                return "neg"
            return "zero"

        def guarded(x):
            if x is None:        # pragma: no cover
                return "none"
            return "val"
    '''))
    (root / 'test_sample.py').write_text(textwrap.dedent('''\
        from sample import classify, guarded
        def test_pos():
            assert classify(5) == "pos"
        def test_val():
            assert guarded(1) == "val"
    '''))


@pytest.mark.skipif(not _coverage_importable(), reason="coverage not installed")
class TestMeasureBranchCoverage:
    def test_detects_missing_branches_in_target_range(self, tmp_path):
        from servers.coverage import measure_branch_coverage
        _write_fixture(tmp_path)
        # classify 在 L1-6；只測了 n>0 一條，n<0 / zero 分支未覆蓋
        targets = [{'file_path': 'sample.py', 'name': 'classify',
                    'line_start': 1, 'line_end': 6}]
        res = measure_branch_coverage(str(tmp_path), ['test_sample.py'], targets)
        assert res['tool_status'] == 'ok'
        assert res['fully_covered'] is False
        pt = res['per_target'][0]
        assert pt['name'] == 'classify'
        # 至少抓到 n<0（4→5）與 fallthrough（2→4 或 4→6）這類未覆蓋 arc
        assert len(pt['missing_branches']) >= 1
        assert pt['n_total'] >= pt['n_covered'] + 1

    def test_out_of_range_branches_not_attributed(self, tmp_path):
        from servers.coverage import measure_branch_coverage
        _write_fixture(tmp_path)
        # 只看 guarded（L8-11）；其 None 分支有 pragma → 不算 missing；val 已測 → 全覆蓋
        targets = [{'file_path': 'sample.py', 'name': 'guarded',
                    'line_start': 8, 'line_end': 11}]
        res = measure_branch_coverage(str(tmp_path), ['test_sample.py'], targets)
        assert res['tool_status'] == 'ok'
        # classify 的未覆蓋分支（L1-6）不得被算進 guarded
        pt = res['per_target'][0]
        for arc in pt['missing_branches']:
            assert 8 <= arc['from'] <= 11
        # pragma 行的分支被尊重 → guarded 視為全覆蓋
        assert pt['missing_branches'] == []

    def test_pytest_failure_is_tests_failed(self, tmp_path):
        # 測試「真的失敗」必須是確定性退件狀態（tests_failed），絕不可 fail-open。
        # 這是本功能的核心：失敗由工具判定，不交給 LLM 宣稱。
        from servers.coverage import measure_branch_coverage
        _write_fixture(tmp_path)
        (tmp_path / 'test_broken.py').write_text('def test_x():\n    assert False\n')
        targets = [{'file_path': 'sample.py', 'name': 'classify',
                    'line_start': 1, 'line_end': 6}]
        res = measure_branch_coverage(str(tmp_path), ['test_broken.py'], targets)
        assert res['tool_status'] == 'tests_failed'
        assert res['error']

    def test_target_not_exercised_is_no_targets(self, tmp_path):
        # 測試全綠，但根本沒 import/執行到 target 檔 → no_targets（確定性退件）。
        # 否則「沒測到卻過關」就是把覆蓋判斷讓給 LLM 的破口。
        from servers.coverage import measure_branch_coverage
        _write_fixture(tmp_path)
        (tmp_path / 'test_unrelated.py').write_text(
            'def test_math():\n    assert 1 + 1 == 2\n')
        targets = [{'file_path': 'sample.py', 'name': 'classify',
                    'line_start': 1, 'line_end': 6}]
        res = measure_branch_coverage(str(tmp_path), ['test_unrelated.py'], targets)
        assert res['tool_status'] == 'no_targets'
        assert res['error']

    def test_no_tests_collected_rc5_is_no_targets(self, tmp_path):
        # pytest 收集到 0 個測試 → exit code 5。屬 Critical 1 明列行為：
        # 沒有任何測試被跑 ≠ 覆蓋成功，必須是確定性的 no_targets（→ 退件），不可 fail-open。
        from servers.coverage import measure_branch_coverage
        _write_fixture(tmp_path)
        # 檔案存在（通過 os.path.isfile 過濾）但無任何 test_ 函式 → pytest rc==5
        (tmp_path / 'test_empty.py').write_text('# no tests here\nVALUE = 1\n')
        targets = [{'file_path': 'sample.py', 'name': 'classify',
                    'line_start': 1, 'line_end': 6}]
        res = measure_branch_coverage(str(tmp_path), ['test_empty.py'], targets)
        assert res['tool_status'] == 'no_targets'
        assert res['error']

    def test_no_coverage_file_left_behind(self, tmp_path):
        from servers.coverage import measure_branch_coverage
        _write_fixture(tmp_path)
        targets = [{'file_path': 'sample.py', 'name': 'classify',
                    'line_start': 1, 'line_end': 6}]
        measure_branch_coverage(str(tmp_path), ['test_sample.py'], targets)
        leftovers = [p for p in os.listdir(tmp_path) if p.startswith('.coverage')]
        assert leftovers == []


class TestMeasureUnavailableWhenNotInstalled:
    def test_missing_coverage_module_is_unavailable(self, tmp_path, monkeypatch):
        # coverage 套件缺失＝真正 infra 問題 → unavailable（唯一該 fail-open 的類別）。
        import servers.coverage as cov
        monkeypatch.setattr(cov, '_coverage_available', lambda: False)
        res = cov.measure_branch_coverage(str(tmp_path), ['test_x.py'],
                                          [{'file_path': 'x.py', 'name': 'f',
                                            'line_start': 1, 'line_end': 3}])
        assert res['tool_status'] == 'unavailable'
        assert 'coverage' in (res['error'] or '').lower()
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `pytest tests/test_coverage_gate.py -k "Measure" -v`
Expected: FAIL — `No module named 'servers.coverage'`。

- [ ] **Step 3: 實作 servers/coverage.py**

建立 [servers/coverage.py](../../../servers/coverage.py)：

```python
"""分支覆蓋率量測（單一職責：純量測，不碰 DB / facade）。

用 `python -m coverage run --branch` 跑指定測試，產 json，再用「行範圍」把
branch arc 歸因到本次 target 函式。非侵入：隔離 data file、不寫專案設定檔。

已知限制（v1，刻意）：行範圍會把 target 範圍內的巢狀 function/lambda 分支也算入，
屬偏保守的過度涵蓋（要求多測、不會漏算）。AST 精確 scope 留待 v2。
"""

import json
import os
import subprocess
import sys
import tempfile
from typing import Dict, List, Optional

_TIMEOUT_SEC = 300


def _coverage_available() -> bool:
    try:
        import importlib.util
        return importlib.util.find_spec('coverage') is not None
    except Exception:
        return False


def _canonical_in_root(root: str, path: str) -> Optional[str]:
    """把 path 解析為絕對路徑並確認落在 root 下；否則回 None。"""
    root_abs = os.path.realpath(root)
    cand = path if os.path.isabs(path) else os.path.join(root_abs, path)
    cand = os.path.realpath(cand)
    if cand == root_abs or cand.startswith(root_abs + os.sep):
        return cand
    return None


def _build_file_index(files: Dict, root: str) -> Dict[str, Dict]:
    """coverage json 的 files key 可能是相對/絕對/帶 ./ → 建 realpath → entry 映射。"""
    idx = {}
    for key, entry in files.items():
        ap = key if os.path.isabs(key) else os.path.join(os.path.realpath(root), key)
        idx[os.path.realpath(ap)] = entry
    return idx


def _result(status: str, error: Optional[str] = None,
            per_target: Optional[List[Dict]] = None,
            fully_covered: bool = False) -> Dict:
    return {'tool_status': status, 'fully_covered': fully_covered,
            'per_target': per_target or [], 'error': error}


def _valid_arc(arc) -> bool:
    """防 coverage json 未來版本格式異動：只接受 [int, int] 形狀的 arc。"""
    return (isinstance(arc, (list, tuple)) and len(arc) == 2
            and isinstance(arc[0], int) and isinstance(arc[1], int))


def measure_branch_coverage(project_path: str,
                            test_targets: List[str],
                            coverage_targets: List[Dict]) -> Dict:
    """量測 coverage_targets 各函式行範圍內的分支覆蓋。

    Returns: {
        'tool_status': 'ok' | 'tests_failed' | 'no_targets' | 'unavailable',
        'fully_covered': bool,
        'per_target': [{'file_path','name','line_start','line_end',
                        'missing_branches':[{'from','to'}],'n_total','n_covered'}],
        'error': str | None,
    }

    fail-state 分流（對齊「工具確認、非 LLM 宣稱」的核心意圖）：
      - 'ok'          測試全綠且 target 有被執行；fully_covered 再決定過/退。
      - 'tests_failed' pytest 有測試失敗（rc==1）→ 上游**確定性退件**。
      - 'no_targets'   無測試被收集（rc==5）或 target 檔不在報告（沒被 import/執行）
                       → 上游**確定性退件**（要求補測/回報測試）。
      - 'unavailable'  真正 infra 問題：coverage 未安裝、空 test_targets、pytest
                       中斷/內部錯（rc∈{2,3,4}或逾時）、json 產製/解析失敗
                       → 上游 **fail-open**（退回 LLM critic）。
    只有 'unavailable' 才 fail-open；測試失敗與沒測到一律確定性退件，避免破口。
    """
    if not _coverage_available():
        return _result('unavailable', 'coverage 套件未安裝')
    if not test_targets:
        return _result('unavailable', '無可量測的 test_targets')

    root = os.path.realpath(project_path)
    # 限制 test_targets 在 root 下且實際存在
    safe_tests = []
    for t in test_targets:
        canon = _canonical_in_root(root, t)
        if canon and os.path.isfile(canon):
            safe_tests.append(canon)
    if not safe_tests:
        return _result('unavailable', 'test_targets 不在專案根目錄下或不存在')

    with tempfile.TemporaryDirectory() as tmp:
        data_file = os.path.join(tmp, '.coverage')
        json_file = os.path.join(tmp, 'cov.json')
        env = dict(os.environ, COVERAGE_FILE=data_file)
        try:
            run = subprocess.run(
                [sys.executable, '-m', 'coverage', 'run', '--branch',
                 '--data-file', data_file, '-m', 'pytest', '-q', *safe_tests],
                cwd=root, env=env, capture_output=True, text=True,
                timeout=_TIMEOUT_SEC,
            )
        except subprocess.TimeoutExpired:
            return _result('unavailable', f'pytest 逾時（>{_TIMEOUT_SEC}s）')

        rc = run.returncode
        tail = ((run.stdout or '')[-500:] + (run.stderr or '')[-500:]).strip()[-400:]
        # pytest exit codes: 0=全過, 1=有測試失敗, 5=未收集到測試, 2/3/4=中斷/內部/用法錯
        if rc == 1:
            return _result('tests_failed', f'測試未通過 (rc=1): {tail}')
        if rc == 5:
            return _result('no_targets', f'未收集到任何測試 (rc=5): {tail}')
        if rc != 0:
            return _result('unavailable', f'pytest 異常 (rc={rc}): {tail}')

        try:
            rep = subprocess.run(
                [sys.executable, '-m', 'coverage', 'json',
                 '--data-file', data_file, '-o', json_file],
                cwd=root, env=env, capture_output=True, text=True,
                timeout=_TIMEOUT_SEC,
            )
        except subprocess.TimeoutExpired:
            return _result('unavailable', 'coverage json 產製逾時')
        if rep.returncode != 0 or not os.path.exists(json_file):
            return _result('unavailable', f'coverage json 產製失敗: {(rep.stderr or "")[-300:]}')

        try:
            with open(json_file, encoding='utf-8') as fh:
                data = json.load(fh)
        except (ValueError, OSError) as e:
            return _result('unavailable', f'coverage json 解析失敗: {e}')

    if not isinstance(data, dict) or not isinstance(data.get('files'), dict):
        return _result('unavailable', 'coverage json 格式非預期（缺 files dict）')

    file_index = _build_file_index(data['files'], root)

    per_target = []
    for t in coverage_targets:
        fp = t.get('file_path') or ''
        ls = t.get('line_start') or 0
        le = t.get('line_end') or ls
        canon = _canonical_in_root(root, fp)
        entry = file_index.get(canon) if canon else None
        if entry is None:
            # target 檔不在報告：測試全綠卻沒 import/執行到它 → 確定性退件
            return _result('no_targets', f'target 檔未被測試執行（未覆蓋）: {fp}')
        in_range = lambda arc: ls <= arc[0] <= le
        missing = [a for a in entry.get('missing_branches', []) if _valid_arc(a) and in_range(a)]
        executed = [a for a in entry.get('executed_branches', []) if _valid_arc(a) and in_range(a)]
        per_target.append({
            'file_path': fp, 'name': t.get('name'),
            'line_start': ls, 'line_end': le,
            'missing_branches': [{'from': a[0], 'to': a[1]} for a in missing],
            'n_total': len(missing) + len(executed),
            'n_covered': len(executed),
        })

    fully = all(not pt['missing_branches'] for pt in per_target)
    return _result('ok', None, per_target=per_target, fully_covered=fully)
```

- [ ] **Step 4: 跑測試確認通過**

Run: `pytest tests/test_coverage_gate.py -k "Measure" -v`
Expected: PASS（`TestMeasureBranchCoverage` 需 coverage 已裝；`TestMeasureFailOpenWhenNotInstalled` 無條件通過）。

- [ ] **Step 5: Commit**

```bash
git add servers/coverage.py tests/test_coverage_gate.py
git commit -m "feat(coverage): measure_branch_coverage with line-range attribution"
```

---

## Task 6: 量測輔助 — derive_test_targets 與 format_missing_issues

**Files:**
- Modify: `servers/coverage.py`
- Test: `tests/test_coverage_gate.py`

- [ ] **Step 1: 寫失敗測試**

在 `tests/test_coverage_gate.py` 末尾新增：

```python
class TestDeriveTestTargets:
    def test_parses_marker_from_executor_result(self, tmp_path):
        from servers.coverage import derive_test_targets
        (tmp_path / 'test_a.py').write_text('def test_a(): pass\n')
        (tmp_path / 'test_b.py').write_text('def test_b(): pass\n')
        result = "做完了。\nTEST_TARGETS: test_a.py, test_b.py\n其他說明。"
        got = derive_test_targets(str(tmp_path), result, [])
        assert got == ['test_a.py', 'test_b.py']

    def test_marker_paths_must_exist_and_be_tests(self, tmp_path):
        from servers.coverage import derive_test_targets
        (tmp_path / 'test_a.py').write_text('def test_a(): pass\n')
        result = "TEST_TARGETS: test_a.py, not_a_test.py, missing_test.py"
        got = derive_test_targets(str(tmp_path), result, [])
        # not_a_test.py 非測試命名；missing_test.py 不存在 → 都剔除
        assert got == ['test_a.py']

    def test_heuristic_fallback_by_stem(self, tmp_path):
        from servers.coverage import derive_test_targets
        srcdir = tmp_path / 'servers'
        srcdir.mkdir()
        (srcdir / 'memory.py').write_text('def f(): pass\n')
        tdir = tmp_path / 'tests'
        tdir.mkdir()
        (tdir / 'test_memory.py').write_text('def test_f(): pass\n')
        # 無 marker → 用 coverage_targets 的檔名 stem 找 test_memory.py
        got = derive_test_targets(str(tmp_path), "沒有 marker",
                                  [{'file_path': 'servers/memory.py', 'name': 'f',
                                    'line_start': 1, 'line_end': 1}])
        assert any(p.endswith('test_memory.py') for p in got)


class TestFormatMissingIssues:
    def test_human_readable_issue_lines(self):
        from servers.coverage import format_missing_issues
        per_target = [{
            'file_path': 'servers/x.py', 'name': 'classify',
            'line_start': 1, 'line_end': 6,
            'missing_branches': [{'from': 2, 'to': 4}, {'from': 4, 'to': 5}],
            'n_total': 4, 'n_covered': 2,
        }]
        issues = format_missing_issues(per_target)
        assert len(issues) == 1
        assert 'servers/x.py' in issues[0]
        assert 'classify' in issues[0]
        assert '2→4' in issues[0] and '4→5' in issues[0]
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `pytest tests/test_coverage_gate.py -k "DeriveTestTargets or FormatMissingIssues" -v`
Expected: FAIL — `cannot import name 'derive_test_targets'`。

- [ ] **Step 3: 實作兩個 helper**

在 [servers/coverage.py](../../../servers/coverage.py) 末尾新增。`is_test_file` 沿用 recipes 的判定，避免重複定義：

```python
import re as _re

_MARKER_RE = _re.compile(r'^\s*TEST_TARGETS:\s*(.+)$', _re.MULTILINE)


def _is_test_path(path: str) -> bool:
    from servers.recipes import is_test_file
    return is_test_file(path)


def derive_test_targets(project_path: str,
                        executor_result: Optional[str],
                        coverage_targets: List[Dict]) -> List[str]:
    """決定要餵給 coverage 的測試檔（相對專案根）。

    1. 優先：從 executor 輸出解析 `TEST_TARGETS:` marker（逗號/空白分隔）。
    2. 後備：用各 coverage_target 的檔名 stem，找 test_<stem>.py / <stem>_test.py。
    只保留「存在且為測試命名」的路徑。回傳去重排序後的相對路徑清單（可能為空）。
    """
    root = os.path.realpath(project_path)
    found: List[str] = []

    # 1. marker
    for m in _MARKER_RE.findall(executor_result or ''):
        for raw in _re.split(r'[,\s]+', m.strip()):
            if not raw:
                continue
            canon = _canonical_in_root(root, raw)
            if canon and os.path.isfile(canon) and _is_test_path(raw):
                rel = os.path.relpath(canon, root)
                if rel not in found:
                    found.append(rel)
    if found:
        return sorted(found)

    # 2. 後備：stem 啟發式（限縮——剪掉 build/dist/site-packages 等噪音目錄、設候選上限）
    stems = set()
    for t in coverage_targets:
        fp = t.get('file_path') or ''
        s = os.path.splitext(os.path.basename(fp))[0]
        if s:
            stems.add(s)
    wanted = set()
    for s in stems:
        wanted.add(f'test_{s}.py')
        wanted.add(f'{s}_test.py')
    if not wanted:
        return []
    _PRUNE = {'.git', '.hg', '.svn', '.venv', 'venv', 'env', '__pycache__',
              'node_modules', 'build', 'dist', '.tox', '.eggs', '.mypy_cache',
              '.pytest_cache', 'site-packages'}
    _MAX_FALLBACK = 50
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _PRUNE]
        for fn in filenames:
            if fn in wanted:
                rel = os.path.relpath(os.path.join(dirpath, fn), root)
                if rel not in found:
                    found.append(rel)
                    if len(found) >= _MAX_FALLBACK:
                        return sorted(found)
    # 後備找不到 → 回空清單；由 run_coverage_gate 決定確定性退件（要求 executor 回報 TEST_TARGETS）
    return sorted(found)


def format_missing_issues(per_target: List[Dict]) -> List[str]:
    """把有未覆蓋分支的 target 轉成人類可讀 issue 字串（給 finish_validation）。"""
    issues = []
    for pt in per_target:
        if not pt['missing_branches']:
            continue
        arcs = ', '.join(f"{a['from']}→{a['to']}" for a in pt['missing_branches'])
        issues.append(
            f"{pt['file_path']} 函式 {pt['name']} (L{pt['line_start']}-{pt['line_end']})："
            f"分支未覆蓋 {arcs}（{len(pt['missing_branches'])} 條未覆蓋 / 共 {pt['n_total']} 條）"
        )
    return issues
```

- [ ] **Step 4: 跑測試確認通過**

Run: `pytest tests/test_coverage_gate.py -k "DeriveTestTargets or FormatMissingIssues" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add servers/coverage.py tests/test_coverage_gate.py
git commit -m "feat(coverage): derive_test_targets + format_missing_issues"
```

---

## Task 7: facade — critic dispatch 帶 original_task_id

**Files:**
- Modify: `servers/facade.py:1663-1671`
- Test: `tests/test_coverage_gate.py`

- [ ] **Step 1: 寫失敗測試**

在 `tests/test_coverage_gate.py` 末尾新增（用真實 DB fixture 建 epic→task→done，呼叫 get_next_dispatch）：

```python
class TestCriticDispatchHasOriginalTaskId:
    def test_critic_dispatch_exposes_original_task_id(self, mock_db_path, tmp_path):
        # mock_db_path：隔離 DB；tmp_path：當 project_path 傳給 get_next_dispatch。
        from servers.tasks import create_task, create_subtask, update_task_status
        from servers.facade import get_next_dispatch

        epic = create_task(project='proj', description='epic', task_level='epic')
        story = create_subtask(parent_id=epic, description='story', task_level='story',
                               requires_validation=False)
        task = create_subtask(parent_id=story, description='write tests',
                              requires_validation=True)
        update_task_status(task, 'done', result='done\nTEST_TARGETS: tests/test_x.py')

        inst = get_next_dispatch(epic, 'proj', str(tmp_path))
        assert inst['subagent_type'] == 'critic'
        assert inst['original_task_id'] == task
```

> 註：若 epic→story→task 的階層建立 API 與此略有出入，先用 `pytest tests/test_tasks*.py` 既有測試確認正確的建樹方式，再對齊。

- [ ] **Step 2: 跑測試確認失敗**

Run: `pytest tests/test_coverage_gate.py::TestCriticDispatchHasOriginalTaskId -v`
Expected: FAIL — `KeyError: 'original_task_id'`。

- [ ] **Step 3: 補 original_task_id 到 critic dispatch**

把 [servers/facade.py:1663-1671](../../../servers/facade.py) 的 critic dispatch return：

```python
            return _record_dispatch_decision(trace_id, parent_id, project_name, project_path, {
                'action': 'dispatch',
                'subagent_type': 'critic',
                'model_tier': _AGENT_TIERS['critic'],
                'prompt': critic_prompt,
                'task_id': critic_task['id'],
                'progress': f'{total_done}/{total_all} tasks complete',
                'message': f"Validating: {task['description'][:60]}",
            })
```

改成（加 `original_task_id`）：

```python
            return _record_dispatch_decision(trace_id, parent_id, project_name, project_path, {
                'action': 'dispatch',
                'subagent_type': 'critic',
                'model_tier': _AGENT_TIERS['critic'],
                'prompt': critic_prompt,
                'task_id': critic_task['id'],
                'original_task_id': critic_task['original_task_id'],
                'progress': f'{total_done}/{total_all} tasks complete',
                'message': f"Validating: {task['description'][:60]}",
            })
```

- [ ] **Step 4: 跑測試確認通過**

Run: `pytest tests/test_coverage_gate.py::TestCriticDispatchHasOriginalTaskId -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add servers/facade.py tests/test_coverage_gate.py
git commit -m "feat(facade): critic dispatch exposes original_task_id"
```

---

## Task 8: facade — run_coverage_gate 與 get_next_dispatch_gated

**Files:**
- Modify: `servers/facade.py`（新增兩函式，建議置於 `get_next_dispatch` 之後）
- Test: `tests/test_coverage_gate.py`

- [ ] **Step 1: 寫失敗測試（用 monkeypatch 隔離量測，只驗 gate 控制流）**

在 `tests/test_coverage_gate.py` 末尾新增：

```python
class TestRunCoverageGate:
    def _setup_done_task(self, cov_targets, result):
        # 依賴測試方法已掛 mock_db_path fixture（隔離 DB）。
        from servers.tasks import (create_task, create_subtask,
                                   update_task_status, reserve_critic_task)
        epic = create_task(project='proj', description='epic', task_level='epic')
        story = create_subtask(parent_id=epic, description='story', task_level='story',
                               requires_validation=False)
        task = create_subtask(parent_id=story, description='write tests',
                              requires_validation=True,
                              metadata={'coverage_targets': cov_targets})
        update_task_status(task, 'done', result=result)
        critic = reserve_critic_task(task)
        return task, critic['id']

    def test_uncovered_rejects_and_writes_rejection_context(
            self, mock_db_path, tmp_path, monkeypatch):
        import servers.coverage as cov
        import servers.facade as facade
        from servers.memory import get_working_memory
        from servers.tasks import get_task
        targets = [{'file_path': 'x.py', 'name': 'f', 'line_start': 1, 'line_end': 9}]
        task, critic_id = self._setup_done_task(targets, 'done\nTEST_TARGETS: test_x.py')
        monkeypatch.setattr(cov, '_coverage_available', lambda: True)
        monkeypatch.setattr(cov, 'derive_test_targets', lambda *a, **k: ['test_x.py'])
        monkeypatch.setattr(cov, 'measure_branch_coverage', lambda *a, **k: {
            'tool_status': 'ok', 'fully_covered': False, 'error': None,
            'per_target': [{'file_path': 'x.py', 'name': 'f', 'line_start': 1, 'line_end': 9,
                            'missing_branches': [{'from': 2, 'to': 4}],
                            'n_total': 2, 'n_covered': 1}]})
        verdict = facade.run_coverage_gate(critic_id, task, 'proj', str(tmp_path))
        assert verdict['verdict'] == 'rejected'
        # 原任務被 finish_validation 設回 pending（待重做）
        assert get_task(task)['status'] == 'pending'
        # Critical 2：行號必須寫進 working_memory['critic_suggestions']
        #（_get_rejected_tasks 的唯一來源；finish_validation 不寫此鍵）
        wm = get_working_memory(task, 'critic_suggestions')
        assert wm and '2→4' in wm

    def test_tests_failed_is_deterministic_reject(
            self, mock_db_path, tmp_path, monkeypatch):
        # 測試真的失敗 → 確定性退件，絕不 fail-open（核心意圖：工具確認非 LLM 宣稱）
        import servers.coverage as cov
        import servers.facade as facade
        from servers.tasks import get_task
        targets = [{'file_path': 'x.py', 'name': 'f', 'line_start': 1, 'line_end': 9}]
        task, critic_id = self._setup_done_task(targets, 'done\nTEST_TARGETS: test_x.py')
        monkeypatch.setattr(cov, '_coverage_available', lambda: True)
        monkeypatch.setattr(cov, 'derive_test_targets', lambda *a, **k: ['test_x.py'])
        monkeypatch.setattr(cov, 'measure_branch_coverage', lambda *a, **k: {
            'tool_status': 'tests_failed', 'fully_covered': False,
            'per_target': [], 'error': '測試未通過 (rc=1): assert False'})
        verdict = facade.run_coverage_gate(critic_id, task, 'proj', str(tmp_path))
        assert verdict['verdict'] == 'rejected'
        assert get_task(task)['status'] == 'pending'

    def test_no_test_targets_is_deterministic_reject(
            self, mock_db_path, tmp_path, monkeypatch):
        # 推不出測試檔 → 退件要求回報 TEST_TARGETS（非 fail-open）
        import servers.coverage as cov
        import servers.facade as facade
        targets = [{'file_path': 'x.py', 'name': 'f', 'line_start': 1, 'line_end': 9}]
        task, critic_id = self._setup_done_task(targets, 'done（沒有 marker）')
        monkeypatch.setattr(cov, '_coverage_available', lambda: True)
        monkeypatch.setattr(cov, 'derive_test_targets', lambda *a, **k: [])
        verdict = facade.run_coverage_gate(critic_id, task, 'proj', str(tmp_path))
        assert verdict['verdict'] == 'rejected'
        assert any('TEST_TARGETS' in i for i in verdict['issues'])

    def test_fully_covered_proceeds_to_critic(self, mock_db_path, tmp_path, monkeypatch):
        import servers.coverage as cov
        import servers.facade as facade
        targets = [{'file_path': 'x.py', 'name': 'f', 'line_start': 1, 'line_end': 9}]
        task, critic_id = self._setup_done_task(targets, 'done\nTEST_TARGETS: test_x.py')
        monkeypatch.setattr(cov, '_coverage_available', lambda: True)
        monkeypatch.setattr(cov, 'derive_test_targets', lambda *a, **k: ['test_x.py'])
        monkeypatch.setattr(cov, 'measure_branch_coverage', lambda *a, **k: {
            'tool_status': 'ok', 'fully_covered': True, 'error': None,
            'per_target': [{'file_path': 'x.py', 'name': 'f', 'line_start': 1, 'line_end': 9,
                            'missing_branches': [], 'n_total': 2, 'n_covered': 2}]})
        verdict = facade.run_coverage_gate(critic_id, task, 'proj', str(tmp_path))
        assert verdict['verdict'] == 'proceed'
        assert verdict.get('warn') in (None, '')

    def test_unavailable_proceeds_with_warning(self, mock_db_path, tmp_path, monkeypatch):
        # 唯一 fail-open 類別：coverage 套件缺失等真正 infra 問題
        import servers.coverage as cov
        import servers.facade as facade
        targets = [{'file_path': 'x.py', 'name': 'f', 'line_start': 1, 'line_end': 9}]
        task, critic_id = self._setup_done_task(targets, 'done\nTEST_TARGETS: test_x.py')
        monkeypatch.setattr(cov, '_coverage_available', lambda: False)
        verdict = facade.run_coverage_gate(critic_id, task, 'proj', str(tmp_path))
        assert verdict['verdict'] == 'proceed'
        assert '⚠️' in verdict['warn']

    def test_uncovered_at_retry_limit_returns_blocked(
            self, mock_db_path, tmp_path, monkeypatch):
        # 邊界（codex Major）：原任務已達 MAX_RETRIES-1 次退件，這次再退就達上限。
        # finish_validation 會把任務設 'blocked' 並開 human_review；gate 必須把 blocked 往上帶，
        # 不能仍回 'rejected'（否則 get_next_dispatch_gated 會 continue → 誤判成 waiting，任務卡死）。
        import servers.coverage as cov
        import servers.facade as facade
        from servers.tasks import get_task, update_task
        targets = [{'file_path': 'x.py', 'name': 'f', 'line_start': 1, 'line_end': 9}]
        task, critic_id = self._setup_done_task(targets, 'done\nTEST_TARGETS: test_x.py')
        # 預先把 rejection_count 設到上限前一格：retry_count = (MAX_RETRIES-1)+1 = MAX_RETRIES → blocked
        update_task(task, rejection_count=facade.MAX_RETRIES - 1)
        monkeypatch.setattr(cov, '_coverage_available', lambda: True)
        monkeypatch.setattr(cov, 'derive_test_targets', lambda *a, **k: ['test_x.py'])
        monkeypatch.setattr(cov, 'measure_branch_coverage', lambda *a, **k: {
            'tool_status': 'ok', 'fully_covered': False, 'error': None,
            'per_target': [{'file_path': 'x.py', 'name': 'f', 'line_start': 1, 'line_end': 9,
                            'missing_branches': [{'from': 2, 'to': 4}],
                            'n_total': 2, 'n_covered': 1}]})
        verdict = facade.run_coverage_gate(critic_id, task, 'proj', str(tmp_path))
        assert verdict['verdict'] == 'blocked'
        assert get_task(task)['status'] == 'blocked'


class TestGetNextDispatchGated:
    def test_gated_rejects_then_executor_prompt_carries_missing_arc(
            self, mock_db_path, tmp_path, monkeypatch):
        import servers.coverage as cov
        import servers.facade as facade
        from servers.tasks import create_task, create_subtask, update_task_status
        epic = create_task(project='proj', description='epic', task_level='epic')
        story = create_subtask(parent_id=epic, description='story', task_level='story',
                               requires_validation=False)
        task = create_subtask(parent_id=story, description='write tests',
                              requires_validation=True,
                              metadata={'coverage_targets':
                                        [{'file_path': 'x.py', 'name': 'f',
                                          'line_start': 1, 'line_end': 9}]})
        update_task_status(task, 'done', result='done\nTEST_TARGETS: test_x.py')

        monkeypatch.setattr(cov, '_coverage_available', lambda: True)
        monkeypatch.setattr(cov, 'derive_test_targets', lambda *a, **k: ['test_x.py'])
        # 注意：不 monkeypatch format_missing_issues —— 用真實格式化，確保 prompt 真的帶行號
        monkeypatch.setattr(cov, 'measure_branch_coverage', lambda *a, **k: {
            'tool_status': 'ok', 'fully_covered': False, 'error': None,
            'per_target': [{'file_path': 'x.py', 'name': 'f', 'line_start': 1, 'line_end': 9,
                            'missing_branches': [{'from': 2, 'to': 4}],
                            'n_total': 2, 'n_covered': 1}]})

        inst = facade.get_next_dispatch_gated(epic, 'proj', str(tmp_path))
        # gate 退件後跳過 critic、回 executor 重試
        assert inst['subagent_type'] == 'executor'
        assert inst['task_id'] == task
        # 關鍵（codex Critical 2）：未覆蓋行號真的進到 executor 重試 prompt，
        # 不是只 assert subagent_type == executor
        assert '2→4' in inst['prompt']
        assert 'x.py' in inst['prompt']

    def test_gated_returns_blocked_at_retry_limit_not_waiting(
            self, mock_db_path, tmp_path, monkeypatch):
        # 邊界（codex Major）：退到 MAX_RETRIES 時，gated dispatch 必須回 action='blocked'
        # （指向 human_review），不可 continue 後因 get_next_dispatch 抓不到 'blocked' 而回 'waiting'。
        import servers.coverage as cov
        import servers.facade as facade
        from servers.tasks import create_task, create_subtask, update_task_status, update_task
        epic = create_task(project='proj', description='epic', task_level='epic')
        story = create_subtask(parent_id=epic, description='story', task_level='story',
                               requires_validation=False)
        task = create_subtask(parent_id=story, description='write tests',
                              requires_validation=True,
                              metadata={'coverage_targets':
                                        [{'file_path': 'x.py', 'name': 'f',
                                          'line_start': 1, 'line_end': 9}]})
        update_task_status(task, 'done', result='done\nTEST_TARGETS: test_x.py')
        update_task(task, rejection_count=facade.MAX_RETRIES - 1)
        monkeypatch.setattr(cov, '_coverage_available', lambda: True)
        monkeypatch.setattr(cov, 'derive_test_targets', lambda *a, **k: ['test_x.py'])
        monkeypatch.setattr(cov, 'measure_branch_coverage', lambda *a, **k: {
            'tool_status': 'ok', 'fully_covered': False, 'error': None,
            'per_target': [{'file_path': 'x.py', 'name': 'f', 'line_start': 1, 'line_end': 9,
                            'missing_branches': [{'from': 2, 'to': 4}],
                            'n_total': 2, 'n_covered': 1}]})
        inst = facade.get_next_dispatch_gated(epic, 'proj', str(tmp_path))
        assert inst['action'] == 'blocked'
        assert inst.get('subagent_type') != 'critic'
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `pytest tests/test_coverage_gate.py -k "RunCoverageGate or GetNextDispatchGated" -v`
Expected: FAIL — `module 'servers.facade' has no attribute 'run_coverage_gate'`。

- [ ] **Step 3: 實作 run_coverage_gate 與 get_next_dispatch_gated**

在 [servers/facade.py](../../../servers/facade.py) 的 `get_next_dispatch` 函式之後新增：

```python
def _gate_reject(critic_task_id: str, original_task_id: str,
                 issues: List[str]) -> Dict:
    """確定性退件。

    關鍵：executor 重試 prompt 的 rejection context 來自 working_memory['critic_suggestions']
    （見 _get_rejected_tasks），而 finish_validation **不寫**此鍵。故 gate 必須先自己寫，
    行號才會出現在 executor 重試 prompt 裡。值為字串（直接被注入 prompt）。

    重試上限：達 MAX_RETRIES 時 finish_validation 會把原任務設為 'blocked' 並回
    status='blocked'/next_action='human_review'（servers/facade.py finish_validation 的
    rejected 分支）。但 get_next_dispatch 的 blocked 偵測只看 get_task_progress(...).failed>0，
    **抓不到 'blocked' 狀態** → 會誤判成 'waiting'（任務卡死、無人察覺）。故 gate 必須自己把
    blocked 往上帶，讓 get_next_dispatch_gated 直接回 action='blocked'（指向 human_review）。
    """
    from servers.memory import set_working_memory
    set_working_memory(original_task_id, 'critic_suggestions', '\n'.join(issues))
    fv = finish_validation(critic_task_id, original_task_id, approved=False, issues=issues) or {}
    if fv.get('status') == 'blocked':
        return {'verdict': 'blocked', 'issues': issues,
                'message': fv.get('message'), 'review_id': fv.get('review_id')}
    return {'verdict': 'rejected', 'issues': issues}


def run_coverage_gate(critic_task_id: str,
                      original_task_id: str,
                      project_name: str,
                      project_path: str) -> Dict:
    """對一個待派 critic 的任務跑分支覆蓋率 gate。

    fail-state 分流（對齊「工具確認、非 LLM 宣稱」）：
      - 有未覆蓋分支 / 測試失敗(tests_failed) / 沒測到(no_targets) / 推不出測試檔
        → **確定性退件**（_gate_reject：寫 critic_suggestions + finish_validation），
        回 {'verdict':'rejected'}；若該退件令原任務達 MAX_RETRIES，_gate_reject 改回
        {'verdict':'blocked', ...}（上游轉 action='blocked' → human_review）。
      - 全覆蓋 → {'verdict':'proceed', 'warn': None}。
      - 真正 infra（coverage 未安裝 / unavailable）→ fail-open，回
        {'verdict':'proceed', 'warn': <警示字串>}（上游把警示前置到 critic prompt）。
    """
    import sys
    from servers.tasks import get_task
    from servers import coverage as cov

    original = get_task(original_task_id)
    metadata = (original or {}).get('metadata') or {}
    coverage_targets = metadata.get('coverage_targets') or []
    result_text = (original or {}).get('result') or ''

    # 沒有結構化 target（非 unit_test recipe 建的任務）→ 不攔，照常派 critic
    if not coverage_targets:
        return {'verdict': 'proceed', 'warn': None}

    # coverage 套件缺失＝真正 infra → fail-open
    if not cov._coverage_available():
        return {'verdict': 'proceed',
                'warn': '⚠️ coverage 套件未安裝，本任務回退人工逐分支核對。'}

    sys.stderr.write('… 量測分支覆蓋率中 …\n')  # UX：pytest 較久時別讓使用者以為當機

    test_targets = cov.derive_test_targets(project_path, result_text, coverage_targets)
    if not test_targets:
        # 推不出測試檔 → 確定性退件，要求 executor 用 marker 回報
        return _gate_reject(critic_task_id, original_task_id, [
            '未能確認你寫的測試檔。請在回報中以**獨立一行** '
            '`TEST_TARGETS: <相對專案根路徑>, ...` 列出本次測試檔，gate 才能量測分支覆蓋。'])

    res = cov.measure_branch_coverage(project_path, test_targets, coverage_targets)
    status = res['tool_status']

    if status == 'ok':
        if res['fully_covered']:
            return {'verdict': 'proceed', 'warn': None}
        issues = cov.format_missing_issues(res['per_target'])
        issues.append('若為真正不可達/防禦性分支，請用 `# pragma: no cover` 並在回報說明理由。')
        return _gate_reject(critic_task_id, original_task_id, issues)

    if status in ('tests_failed', 'no_targets'):
        # 工具判定失敗/沒測到 → 確定性退件，不交給 LLM 宣稱
        return _gate_reject(critic_task_id, original_task_id,
                            [f'分支覆蓋率 gate：{res.get("error")}'])

    # status == 'unavailable' → 真正 infra 問題 → fail-open
    return {'verdict': 'proceed',
            'warn': f"⚠️ 分支覆蓋率工具未量到（{res.get('error')}），本任務回退人工逐分支核對。"}


def get_next_dispatch_gated(parent_id: str,
                            project_name: str,
                            project_path: str,
                            trace_id: str = None) -> Dict:
    """get_next_dispatch + 分支覆蓋率 gate。

    底層派 critic 時先跑 gate：確定性退件 → finish_validation 已處理，重取下一個 dispatch
    （會變成 executor 重試）；其餘照常回傳 critic（fail-open 時把警示前置到 prompt）。
    其他 action（executor/done/blocked）原樣回傳。
    防護：記住本次已跑過 gate 的 critic id，若同一 critic 又被派回（理應不會——
    finish_validation 會把任務推進到 pending/execution）→ 直接回傳避免無限迴圈。
    """
    seen_critics = set()
    while True:
        inst = get_next_dispatch(parent_id, project_name, project_path, trace_id)
        if inst.get('action') != 'dispatch' or inst.get('subagent_type') != 'critic':
            return inst
        critic_id = inst.get('task_id')
        if critic_id in seen_critics:
            return inst  # 防護：避免狀態未推進時的無限迴圈
        seen_critics.add(critic_id)
        verdict = run_coverage_gate(
            critic_id, inst['original_task_id'], project_name, project_path)
        if verdict['verdict'] == 'blocked':
            # 達 MAX_RETRIES：finish_validation 已開 human_review。直接回 blocked，
            # 不再 continue（否則 get_next_dispatch 的 blocked 偵測抓不到 'blocked' 狀態 → 誤回 waiting）。
            return {
                'action': 'blocked',
                'progress': inst.get('progress'),
                'message': verdict.get('message') or '任務已達最大驗證重試次數，需人工審查。',
                'review_id': verdict.get('review_id'),
            }
        if verdict['verdict'] == 'rejected':
            continue  # 退件已處理，迴圈重取 → executor 重試
        if verdict.get('warn'):
            inst['prompt'] = verdict['warn'] + '\n\n' + inst['prompt']
        return inst
```

- [ ] **Step 4: 跑測試確認通過**

Run: `pytest tests/test_coverage_gate.py -k "RunCoverageGate or GetNextDispatchGated" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add servers/facade.py tests/test_coverage_gate.py
git commit -m "feat(facade): coverage gate routes uncovered branches via finish_validation (C2)"
```

---

## Task 9: 指令 — /han:unit-test 迴圈改用 gated dispatch

**Files:**
- Modify: `commands/han/unit-test.md:50-62`
- Test: `tests/test_playbooks.py`

- [ ] **Step 1: 寫失敗的 markdown 回歸測試**

在 [tests/test_playbooks.py](../../../tests/test_playbooks.py) 末尾新增：

```python
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
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `pytest tests/test_playbooks.py::TestUnitTestCommandUsesGatedDispatch -v`
Expected: FAIL — `get_next_dispatch_gated` not in text。

- [ ] **Step 3: 改指令迴圈**

把 [commands/han/unit-test.md:50-62](../../../commands/han/unit-test.md) 整段（步驟 3）：

````markdown
3. 驅動派工迴圈（重複，直到 `action != 'dispatch'`）。每一輪都把步驟 2 印出的同一個 epic_id 放進 `HAN_EPIC` 前綴，並 inline 重算其餘環境變數再執行：
```bash
HAN_EPIC="<epic_id>" HAN_PROJECT="$(basename "$(pwd)")" HAN_PROJECT_PATH="$(pwd)" python3 - <<'PY'
import os, sys, json
sys.path.insert(0, {{HAN_DIR}})
from servers.facade import get_next_dispatch
inst = get_next_dispatch(os.environ['HAN_EPIC'], os.environ['HAN_PROJECT'], os.environ['HAN_PROJECT_PATH'])
print(json.dumps({k: inst.get(k) for k in ('action','subagent_type','task_id','progress','message')}, ensure_ascii=False))
print('PROMPT_START'); print(inst.get('prompt','')); print('PROMPT_END')
PY
```
- `action == 'dispatch'`：用 **Agent 工具**（Claude Code 派工工具，舊稱 Task）派發，`subagent_type` 用回傳值、`prompt` 用 `PROMPT_START`…`PROMPT_END` 之間的內容。子代理完成後再次 dispatch。
- `action == 'done'`：完成；`blocked`/`waiting`：回報 `message` 並停止。
````

替換為（改用 `get_next_dispatch_gated`，並加一句說明 gate）：

````markdown
3. 驅動派工迴圈（重複，直到 `action != 'dispatch'`）。每一輪都把步驟 2 印出的同一個 epic_id 放進 `HAN_EPIC` 前綴，並 inline 重算其餘環境變數再執行。**用 `get_next_dispatch_gated`**：當下一步是 critic 驗證時，它會先用 `coverage --branch` 量測本次 target 的分支覆蓋——有未覆蓋分支就直接走 `finish_validation` 退回 executor 補測（帶具體行號、不派 critic、不費 token）；全覆蓋或工具不可用才照常派 critic：
```bash
HAN_EPIC="<epic_id>" HAN_PROJECT="$(basename "$(pwd)")" HAN_PROJECT_PATH="$(pwd)" python3 - <<'PY'
import os, sys, json
sys.path.insert(0, {{HAN_DIR}})
from servers.facade import get_next_dispatch_gated
inst = get_next_dispatch_gated(os.environ['HAN_EPIC'], os.environ['HAN_PROJECT'], os.environ['HAN_PROJECT_PATH'])
print(json.dumps({k: inst.get(k) for k in ('action','subagent_type','task_id','progress','message')}, ensure_ascii=False))
print('PROMPT_START'); print(inst.get('prompt','')); print('PROMPT_END')
PY
```
- `action == 'dispatch'`：用 **Agent 工具**（Claude Code 派工工具，舊稱 Task）派發，`subagent_type` 用回傳值、`prompt` 用 `PROMPT_START`…`PROMPT_END` 之間的內容。子代理完成後再次 dispatch。
- `action == 'done'`：完成；`blocked`/`waiting`：回報 `message` 並停止。
````

- [ ] **Step 4: 跑測試確認通過**

Run: `pytest tests/test_playbooks.py::TestUnitTestCommandUsesGatedDispatch -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add commands/han/unit-test.md tests/test_playbooks.py
git commit -m "feat(unit-test): drive loop via get_next_dispatch_gated"
```

---

## Task 10: playbook — unit_test 補分支覆蓋原則

**Files:**
- Modify: `reference/playbooks/unit-test.md`
- Test: `tests/test_playbooks.py`

- [ ] **Step 1: 先看現有 playbook 結構**

Run: `grep -n "Executor\|Critic\|##" reference/playbooks/unit-test.md`
讀出 Executor Principles 與 Critic Checklist 兩個區段的實際標題與位置（下一步要在對的區段插入）。

- [ ] **Step 2: 寫失敗測試**

在 [tests/test_playbooks.py](../../../tests/test_playbooks.py) 末尾新增：

```python
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
```

- [ ] **Step 3: 跑測試確認失敗**

Run: `pytest tests/test_playbooks.py::TestUnitTestPlaybookBranchCoverage -v`
Expected: FAIL（playbook 尚未含這些字串）。

- [ ] **Step 4: 在 playbook 補原則**

在 [reference/playbooks/unit-test.md](../../../reference/playbooks/unit-test.md) 的 **Executor Principles** 區段末尾新增：

```markdown
- **分支全覆蓋（工具強制）**：本次 target 函式的每一條分支（含 if/else、null/None 路徑、early return、except）都必須被測試走到。上游會用 `coverage --branch` 量測，未覆蓋的分支會帶**具體行號**自動退件。
- **結構化回報測試檔**：完成後在回報中**獨立一行**列出本次新增/相關的測試檔路徑，格式固定：`TEST_TARGETS: tests/test_x.py, tests/test_y.py`（相對專案根、逗號分隔）。這是覆蓋率 gate 用來決定要跑哪些測試的依據。
- **不可達分支**：確認為真正不可達/防禦性的分支，用 `# pragma: no cover`（或 `# pragma: no branch`）標記，並在回報**說明理由**；gate 會尊重 pragma、不計入未覆蓋。
```

在 **Critic Checklist** 區段末尾新增：

```markdown
- **分支覆蓋（上游已工具強制）**：本次 target 的分支覆蓋已由 `coverage --branch` 在派你之前強制；你拿到此任務代表已全覆蓋或工具不可用。**若 prompt 開頭標記「分支覆蓋率工具未量到」，你必須手動逐分支核對**（含 null/None 路徑——一條 null 分支即一條分支），未覆蓋則 REJECT。
```

- [ ] **Step 5: 跑測試確認通過**

Run: `pytest tests/test_playbooks.py::TestUnitTestPlaybookBranchCoverage -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add reference/playbooks/unit-test.md tests/test_playbooks.py
git commit -m "feat(playbook): unit_test branch-coverage executor/critic principles"
```

---

## Task 11: 全套件回歸 + 收尾

**Files:**
- 無新增；驗證整體。

- [ ] **Step 1: 跑全測試套件**

Run: `pytest -q`
Expected: 全綠（含既有測試未被破壞）。若 `TestMeasureBranchCoverage` 因環境無 coverage 被 skip，屬預期；CI 已裝 coverage 故 CI 上會實跑。

- [ ] **Step 2: 跑既有 harness eval / 冒煙（與 CI 一致）**

Run: `python cli/main.py eval`
Expected: 通過（無 regression）。

- [ ] **Step 3: 端到端手測（在一個 Python 測試專案，非 aipoolserver；aipoolserver 是 Java 且唯讀）**

挑一個小型 Python 專案或在暫存目錄建一個含未測函式的小專案，跑 `/han:unit-test <target>`，確認：
- 迴圈會在某輪因分支未覆蓋而退回 executor（而非直接派 critic）；
- 退件 issue 帶具體行號；
- 補測後再次量測通過 → 才派 LLM critic。

> 不要對 `/home/agent/claude_projects/code-qa-test/aipoolserver` 跑（唯讀、Java 非本 v1 範圍）。

- [ ] **Step 4: 最終 commit（若手測有微調）**

```bash
git add -A
git commit -m "test: branch-coverage gate end-to-end verification"
```

---

## Self-Review

**1. Spec coverage（逐條對照 spec）：**
- C1 資料層（line_end + coverage_targets metadata）→ Task 2/3/4 ✅（Task 4 含整合測試，防「helper 對但 recipe 沒傳 metadata=」假綠）
- `servers/coverage.py` 量測（隔離 data file、list argv/timeout、路徑正規化、行範圍歸因、pragma 自動尊重、json 格式 isinstance 防護）→ Task 5 ✅
- 量測輔助（test_targets 推導 + 噪音目錄剪枝/候選上限、issue 格式化）→ Task 6 ✅
- C2 gate 走 finish_validation：**確定性退件**（未覆蓋 / tests_failed / no_targets / 推不出測試檔），**僅 unavailable 才 fail-open**；退件時 gate 自寫 `critic_suggestions` 讓行號進到 executor 重試 prompt → Task 7/8 ✅
- 指令迴圈改用 gated dispatch → Task 9 ✅
- playbook executor/critic 原則 → Task 10 ✅
- 測試（量測 fixtures、行範圍過濾、**fail-state 四分**、無 .coverage 殘留、rejection context 真進 prompt、資料流、markdown 回歸）→ 散落 Task 2–10，Task 11 總驗 ✅
- 護欄（非侵入：不寫 .coveragerc/pyproject/build.gradle；值走環境變數；DB 測試一律用 mock_db_path fixture，不污染 brain.db）→ coverage.py 用隔離暫存、指令沿用 inline env 慣例 ✅
- YAGNI 排除（Java、百分比門檻、AST、零分支 executed 檢查）→ 計畫未實作，符合 ✅

**2. Placeholder scan：** 各步驟均含完整程式碼與確切指令／預期輸出；無 TBD/TODO。Task 3、7、10 標註了「先 grep 確認既有名稱／結構」的前置查核（因階層 API、playbook 標題需對齊現況），非 placeholder 而是對齊現有碼的必要步驟。

**3. Type consistency：**
- `measure_branch_coverage(project_path, test_targets, coverage_targets)` 回傳 `tool_status ∈ {'ok','tests_failed','no_targets','unavailable'}`、`per_target[].missing_branches=[{'from','to'}]` —— Task 5 定義，Task 6/8 測試與 `format_missing_issues`/`run_coverage_gate` 消費的狀態分流一致 ✅
- `coverage_targets` 元素 `{file_path,name,line_start,line_end}` —— Task 4 `_gaps_to_coverage_targets` 產出、Task 5/8 消費一致 ✅
- `run_coverage_gate(...) -> {'verdict': 'rejected'|'blocked'|'proceed', 'warn'?, 'issues'?, 'message'?, 'review_id'?}`；`_gate_reject` 共用退件路徑（寫 critic_suggestions + finish_validation），達 MAX_RETRIES 時回 `'blocked'`，`get_next_dispatch_gated` 轉 `action='blocked'`（human_review）—— Task 8 定義並由 `get_next_dispatch_gated` 消費一致 ✅
- rejection context 資料流：`_gate_reject` 寫 `working_memory['critic_suggestions']`（字串）→ `_get_rejected_tasks` 讀為 `_rejection_context` → `get_next_dispatch` 注入 `_build_executor_prompt` → executor 重試 prompt 帶行號（Task 8 測試端到端 assert `'2→4' in inst['prompt']`）✅
- `finish_validation(critic_task_id, original_task_id, approved=False, issues=...)` 對齊實際簽名（第一參數＝critic 任務 id）✅
- critic dispatch 的 `original_task_id`（Task 7 新增）被 `get_next_dispatch_gated`（Task 8）讀取，名稱一致 ✅
- DB 隔離：所有觸及 DB 的測試用 `mock_db_path` fixture（patch `servers.BRAIN_DB`）；無 `servers.db`（不存在）依賴 ✅
