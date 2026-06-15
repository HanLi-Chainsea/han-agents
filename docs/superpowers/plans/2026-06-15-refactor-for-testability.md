# 為可測試性重構（/han:refactor + /han:run）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `/han:refactor`（純規劃：掃可測試性熱點→分類→建可執行任務樹+建議報告，不改碼）與 `/han:run`（通用執行：消費任何 epic，驅動 executor→critic 派工迴圈）。

**Architecture:** 規劃／執行分離。確定性的熱點偵測放 Python（`scan_refactor_candidates`）；需判斷的型錄分類由指令層主代理做；建任務樹是另一個受測 Python helper（`build_refactor_epic`）建出每個高把握項的「characterization-test → refactor → verify」三步相依鏈。執行端 `/han:run` 沿用既有 `get_next_dispatch` 迴圈，playbook 依任務描述自動注入。完全復用 `servers/tasks.py` 持久化、`task_dependencies` 排序、`servers/playbooks.py` 注入；現有指令不動。

**Tech Stack:** Python 3、SQLite（HAN brain DB）、pytest、Claude Code slash commands（markdown）、HAN recipe/playbook/dispatch。

**規格來源：** [docs/superpowers/specs/2026-06-15-refactor-for-testability-design.md](../specs/2026-06-15-refactor-for-testability-design.md)

**分支：** 已在 `feat/refactor-for-testability-spec`，所有 commit 落此分支。

---

## File Structure

| 檔案 | 動作 | 職責 |
|---|---|---|
| `reference/playbooks/refactor.md` | Create | 重構型錄（信心閘）+ Executor Principles + Critic Checklist + build.gradle 護欄 |
| `servers/recipes.py` | Modify | 新增 `_detect_hotspots`、`scan_refactor_candidates`、`_find_pending_refactor_epic`、`build_refactor_epic` |
| `servers/facade.py` | Modify | 新增 `find_latest_pending_epic`（run 端：找最新 pending epic） |
| `commands/han/refactor.md` | Create | `/han:refactor` 指令（掃描→分類→建 epic→寫報告，不改碼不派工） |
| `commands/han/run.md` | Create | `/han:run` 指令（通用執行任一 epic 的派工迴圈） |
| `tests/test_refactor.py` | Create | recipe/helper 的單元測試 |
| `tests/test_playbooks.py` | Modify | refactor playbook 載入/分類斷言 |

**設計約束（寫進相關 task）：**
- 熱點偵測只用 Code Graph 已有的欄位：方法行數（`line_end - line_start`）與呼叫 fan-out（`code_edges.kind='calls'` 出邊數）。**巢狀深度／cyclomatic 需讀原始碼，v1 不做。**
- 型錄分類是「判斷」，留在指令層（markdown）由主代理做；Python 只做確定性的掃描與建樹。
- 三步任務描述都必須含關鍵字讓 `resolve_playbook` 命中 refactor playbook。

---

## Task 1: refactor playbook（型錄 + 原則 + 護欄）

**Files:**
- Create: `reference/playbooks/refactor.md`
- Test: `tests/test_playbooks.py`（既有檔，新增斷言）

- [ ] **Step 1: 先寫會失敗的 playbook 測試**

在 `tests/test_playbooks.py` 的 `TestLoadPlaybooks` 與 `TestResolvePlaybook` 類別中**各新增**下列測試方法：

```python
    # 加進 class TestLoadPlaybooks
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
```

```python
    # 加進 class TestResolvePlaybook
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
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python3 -m pytest tests/test_playbooks.py -k "refactor" -v`
Expected: FAIL（`refactor` 不在 playbooks；resolve 回 None）

- [ ] **Step 3: 建立 playbook 檔**

Create `reference/playbooks/refactor.md`，內容如下（`match` 必須是單行 JSON array）：

```markdown
---
name: refactor
match: ["refactor for testability", "characterization", "refactor", "重構"]
---

## Executor Principles
- **行為不變（behavior-preserving）**：只改結構不改可觀察行為；只套用機械式、可被測試釘住的重構。
- **characterization-test-first**：legacy code 通常沒測試，「測試保持綠」一開始不成立。重構前必須先有釘住「現在實際行為」的 characterization test 並跑綠；改完重跑仍綠才算完成。
- characterization test 的職責是「釘住現在**每個分支**實際走的行為」，**不替工程師判斷 business 對錯**（情境/斷言對錯是工程師的事）。
- 只做高把握型錄項（行為不變、區域範圍、不改 public 契約、不重接依賴）：
  Extract Method / Function、Extract / Introduce Variable、Inline Variable / 簡單 Inline Method、Rename（區域/private 符號）、Decompose Conditional、Replace Magic Number/String with Constant。
- 遇到沒把握項（Introduce Interface / 依賴注入、Move Method / Move Class、改 public API 簽章、打斷共享可變狀態/全域、繼承改組合、動到並行/IO/框架生命週期）→ **停止並回報受阻、降級為建議**，不可硬重構。
- characterization test 寫不出來（行為無法釘住）→ 視為沒把握，回報受阻，不重構。
- **建置環境護欄（JDK / Gradle / 依賴 / CI）**：遇相關問題時——(1) 優先非侵入式處理；(2) 若需改 root `build.gradle`/`build.gradle.kts`/`gradle.properties`/`settings.gradle`，**必須停止並標記人工確認**；(3) **不得為了測試通過而改變專案目標 JDK 版本**。原因：上雲版本固定，改版本會讓「上雲能不能跑」變未知數——寧可回報受阻也不動版本。

## Critic Checklist
- [ ] 重構前已有 characterization test 且**重構前後皆跑綠**（缺測試就重構 → REJECT）
- [ ] 行為未被改變（characterization test 未破裂、未被竄改放水 → 否則 REJECT）
- [ ] 只套用了高把握型錄項；未擅自做沒把握類重構（Introduce Interface、Move、改簽章、打斷依賴等 → 違反即 REJECT）
- [ ] characterization test 有釘住分支行為，而非空殼/恆真斷言（assert True → REJECT）
- [ ] **未擅自改 `build.gradle`/`build.gradle.kts`/`gradle.properties`/`settings.gradle`，且未為通過而變更目標 JDK 版本**（違反即 REJECT，破壞上雲版本一致性）
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python3 -m pytest tests/test_playbooks.py -k "refactor" -v`
Expected: PASS（2 個新測試）

也跑既有 playbook 測試確保沒回歸：
Run: `python3 -m pytest tests/test_playbooks.py -v`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add reference/playbooks/refactor.md tests/test_playbooks.py
git commit -m "feat(playbooks): add refactor playbook (catalog + characterization-first + guardrail)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: 熱點偵測 `_detect_hotspots` + `scan_refactor_candidates`

**Files:**
- Modify: `servers/recipes.py`（檔尾、`RECIPES` 定義之前加入函式；**不註冊進 `RECIPES`**——它不建 epic）
- Test: `tests/test_refactor.py`（新檔）

熱點判準（確定性、只用 Code Graph）：函式節點的行數 `length = line_end - line_start`；呼叫 fan-out = 該節點 `kind='calls'` 出邊數。`length >= LONG_METHOD_LINES` 或 `fan_out >= HIGH_FANOUT` 即為熱點；排序分數 `score = length + fan_out * 5`（高→低）。

- [ ] **Step 1: 寫會失敗的測試**

Create `tests/test_refactor.py`：

```python
"""為可測試性重構：scan / build / run-side helper 測試"""
import os
import sys
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _seed_code(db_path, project, funcs, calls=None):
    """funcs: list of (func_id, name, file_path, line_start, line_end)
       calls: list of (from_id, to_id)  # kind='calls'"""
    conn = sqlite3.connect(db_path)
    for fid, name, fp, ls, le in funcs:
        conn.execute(
            """INSERT INTO code_nodes
               (id, project, kind, name, file_path, line_start, line_end, language)
               VALUES (?,?,?,?,?,?,?,?)""",
            (fid, project, "function", name, fp, ls, le, "python"))
    for frm, to in (calls or []):
        conn.execute(
            """INSERT INTO code_edges (project, from_id, to_id, kind)
               VALUES (?,?,?,?)""", (project, frm, to, "calls"))
    conn.commit()
    conn.close()


class TestDetectHotspots:
    def test_long_method_is_hotspot(self, mock_db_path, monkeypatch, tmp_path):
        from servers import recipes
        monkeypatch.setattr(recipes, "_ensure_synced",
                            lambda p, path: {"test_tool": "pytest"})
        _seed_code(mock_db_path, "rf", [
            ("f.long", "long_fn", "servers/a.py", 1, 80),    # length 79 → hotspot
            ("f.short", "short_fn", "servers/a.py", 1, 5),   # length 4 → not
        ])
        spots = recipes._detect_hotspots("rf", None)
        names = [s["name"] for s in spots]
        assert "long_fn" in names
        assert "short_fn" not in names

    def test_high_fanout_is_hotspot(self, mock_db_path, monkeypatch, tmp_path):
        from servers import recipes
        monkeypatch.setattr(recipes, "_ensure_synced",
                            lambda p, path: {"test_tool": "pytest"})
        funcs = [("f.hub", "hub", "servers/b.py", 1, 10)]
        funcs += [(f"f.c{i}", f"c{i}", "servers/b.py", 20 + i, 21 + i)
                  for i in range(9)]
        calls = [("f.hub", f"f.c{i}") for i in range(9)]   # fan_out 9 → hotspot
        _seed_code(mock_db_path, "rf2", funcs, calls)
        spots = recipes._detect_hotspots("rf2", None)
        hub = [s for s in spots if s["name"] == "hub"]
        assert hub and hub[0]["fan_out"] == 9

    def test_skips_test_files_and_respects_target(self, mock_db_path, monkeypatch):
        from servers import recipes
        monkeypatch.setattr(recipes, "_ensure_synced",
                            lambda p, path: {"test_tool": "pytest"})
        _seed_code(mock_db_path, "rf3", [
            ("f.a", "a_fn", "servers/x.py", 1, 80),
            ("f.b", "b_fn", "other/y.py", 1, 80),
            ("f.t", "t_fn", "tests/test_x.py", 1, 80),
        ])
        spots = recipes._detect_hotspots("rf3", "servers/")
        files = {s["file_path"] for s in spots}
        assert files == {"servers/x.py"}   # 排除 other/ 與 tests/


class TestScanRefactorCandidates:
    def test_truncation_reported(self, mock_db_path, monkeypatch, tmp_path):
        from servers import recipes
        monkeypatch.setattr(recipes, "_ensure_synced",
                            lambda p, path: {"test_tool": "pytest"})
        funcs = [(f"f.{i}", f"fn{i}", "servers/c.py", 1, 100)
                 for i in range(5)]
        _seed_code(mock_db_path, "rf4", funcs)
        r = recipes.scan_refactor_candidates(
            "rf4", str(tmp_path), target_path="servers/", max_candidates=2)
        assert len(r["candidates"]) == 2
        assert r["total_hotspots"] == 5
        assert r["truncated"] is True
        assert "3" in r["message"]   # 明講被截斷數量

    def test_no_hotspots_message(self, mock_db_path, monkeypatch, tmp_path):
        from servers import recipes
        monkeypatch.setattr(recipes, "_ensure_synced",
                            lambda p, path: {"test_tool": "pytest"})
        _seed_code(mock_db_path, "rf5", [("f.s", "s", "servers/d.py", 1, 3)])
        r = recipes.scan_refactor_candidates(
            "rf5", str(tmp_path), target_path="servers/")
        assert r["candidates"] == []
        assert r["truncated"] is False
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python3 -m pytest tests/test_refactor.py -v`
Expected: FAIL（`_detect_hotspots` / `scan_refactor_candidates` 不存在）

- [ ] **Step 3: 實作（加在 `servers/recipes.py` 檔尾，`# Recipe registry` 之前）**

```python
# === 為可測試性重構：掃描（確定性，不建 epic、不改碼）===

LONG_METHOD_LINES = 40
HIGH_FANOUT = 8


def _call_fanout(project: str) -> Dict[str, int]:
    """回傳每個來源節點的 'calls' 出邊數（一次 group-by 查詢）。"""
    from servers import managed_connection
    with managed_connection() as db:
        cur = db.cursor()
        cur.execute(
            "SELECT from_id, COUNT(*) FROM code_edges "
            "WHERE project = ? AND kind = 'calls' GROUP BY from_id",
            (project,))
        return {row[0]: row[1] for row in cur.fetchall()}


def _in_target(file_path: str, target_path: Optional[str]) -> bool:
    if not target_path:
        return True
    tp = target_path.rstrip('/')
    return (file_path == tp or file_path == './' + tp
            or file_path.startswith(tp + '/')
            or file_path.startswith('./' + tp + '/'))


def _detect_hotspots(project: str, target_path: Optional[str]) -> List[Dict]:
    """掃描可測試性熱點（過長方法或高 fan-out）。只讀 Code Graph，純確定性。

    回傳依 score 由高至低排序的熱點清單；每項：
      {id, file_path, name, line_start, line_end, length, fan_out, score}
    """
    from servers.code_graph import get_code_nodes

    fanout = _call_fanout(project)
    nodes: List[Dict] = []
    for kind in ('function', 'method'):
        offset = 0
        while True:
            page = get_code_nodes(project, kind=kind, limit=500, offset=offset)
            nodes.extend(page)
            if len(page) < 500:
                break
            offset += 500

    spots: List[Dict] = []
    for n in nodes:
        fp = n.get('file_path') or ''
        if is_test_file(fp) or not _in_target(fp, target_path):
            continue
        ls = n.get('line_start') or 0
        le = n.get('line_end') or 0
        length = max(0, le - ls)
        fan_out = fanout.get(n.get('id'), 0)
        if length >= LONG_METHOD_LINES or fan_out >= HIGH_FANOUT:
            spots.append({
                'id': n.get('id'),
                'file_path': fp,
                'name': n.get('name') or '?',
                'line_start': ls,
                'line_end': le,
                'length': length,
                'fan_out': fan_out,
                'score': length + fan_out * 5,
            })
    spots.sort(key=lambda s: s['score'], reverse=True)
    return spots


def _find_pending_refactor_epic(project: str) -> Optional[str]:
    """同專案是否已有 pending 的 refactor epic（被動提示用）。"""
    from servers.tasks import get_epic_tasks
    for epic in get_epic_tasks(project):  # created_at DESC
        if (epic.get('status') == 'pending'
                and (epic.get('description') or '').startswith(
                    'Refactor for Testability')):
            return epic.get('id')
    return None


def scan_refactor_candidates(
    project_name: str,
    project_path: str,
    target_path: str = None,
    max_candidates: int = 20,
) -> Dict:
    """掃可測試性熱點候選。**不分類、不建 epic、不改原始碼。**

    分類（高/低把握）由指令層主代理依 refactor playbook 型錄進行。
    """
    _ensure_synced(project_name, project_path)
    hotspots = _detect_hotspots(project_name, target_path)
    truncated = len(hotspots) > max_candidates
    candidates = hotspots[:max_candidates]
    existing = _find_pending_refactor_epic(project_name)

    scope = f" under {target_path}" if target_path else ""
    if not candidates:
        msg = f"No testability hotspots found{scope}."
    else:
        msg = (f"Found {len(hotspots)} testability hotspot(s){scope}; "
               f"returning top {len(candidates)}.")
        if truncated:
            msg += f" Truncated {len(hotspots) - len(candidates)} (raise max_candidates to see more)."
    if existing:
        msg += f" NOTE: a pending refactor epic already exists: {existing}."

    return {
        'candidates': candidates,
        'total_hotspots': len(hotspots),
        'truncated': truncated,
        'existing_pending_epic': existing,
        'message': msg,
    }
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python3 -m pytest tests/test_refactor.py -v`
Expected: 全 PASS（5 個測試）

- [ ] **Step 5: Commit**

```bash
git add servers/recipes.py tests/test_refactor.py
git commit -m "feat(recipes): add deterministic testability-hotspot scan (length + call fan-out)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: `build_refactor_epic`（建三步相依鏈）

**Files:**
- Modify: `servers/recipes.py`（接在 Task 2 函式之後）
- Test: `tests/test_refactor.py`（新增 class）

每個高把握項建一個 story，story 下三個相依 task：characterization-test → refactor → verify（`depends_on` 串成鏈）。

- [ ] **Step 1: 寫會失敗的測試（加進 `tests/test_refactor.py`）**

```python
class TestBuildRefactorEpic:
    def test_builds_three_step_dependency_chain(self, mock_db_path):
        from servers import recipes
        from servers.tasks import get_next_task, update_task_status
        items = [{
            "file_path": "servers/x.py", "name": "foo",
            "refactor_type": "Extract Method",
            "line_start": 1, "line_end": 80,
        }]
        r = recipes.build_refactor_epic("rfb", items)
        assert r["epic_id"] is not None
        assert r["story_count"] == 1
        assert r["task_count"] == 3

        # story 是 epic 的子節點
        import sqlite3, os
        conn = sqlite3.connect(os.environ["HAN_DB_PATH"])
        story_id = conn.execute(
            "SELECT id FROM tasks WHERE epic_id=? AND task_level='story'",
            (r["epic_id"],)).fetchone()[0]
        conn.close()

        # 依賴排序：第一個可派的應是 characterization（無未完成依賴）
        t1 = get_next_task(story_id)
        assert "characterization" in t1["description"].lower()
        # 在 t1 完成前，refactor/verify 不應被選出
        update_task_status(t1["id"], "done")
        t2 = get_next_task(story_id)
        assert t2["description"].lower().startswith("refactor for testability")
        update_task_status(t2["id"], "done")
        t3 = get_next_task(story_id)
        assert "verify refactor" in t3["description"].lower()
        update_task_status(t3["id"], "done")
        assert get_next_task(story_id) is None

    def test_empty_items_no_epic(self, mock_db_path):
        from servers import recipes
        r = recipes.build_refactor_epic("rfb2", [])
        assert r["epic_id"] is None
        assert r["task_count"] == 0

    def test_task_descriptions_match_refactor_playbook(self, mock_db_path):
        from servers import recipes
        from servers.playbooks import resolve_playbook
        import sqlite3, os
        recipes.build_refactor_epic("rfb3", [{
            "file_path": "servers/y.py", "name": "bar",
            "refactor_type": "Decompose Conditional",
            "line_start": 1, "line_end": 60}])
        conn = sqlite3.connect(os.environ["HAN_DB_PATH"])
        descs = [row[0] for row in conn.execute(
            "SELECT description FROM tasks WHERE project='rfb3' "
            "AND task_level='task'").fetchall()]
        conn.close()
        assert len(descs) == 3
        for d in descs:
            pb = resolve_playbook(d)
            assert pb is not None and pb.name == "refactor", d
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python3 -m pytest tests/test_refactor.py::TestBuildRefactorEpic -v`
Expected: FAIL（`build_refactor_epic` 不存在）

- [ ] **Step 3: 實作（加在 `servers/recipes.py` Task 2 函式之後）**

```python
def build_refactor_epic(project_name: str, items: List[Dict]) -> Dict:
    """為高把握重構項建任務樹。

    items: 每項 {file_path, name, refactor_type, line_start, line_end}
    每項 → 1 story + 3 相依 task：characterization-test → refactor → verify。
    items 為空 → 不建 epic。
    """
    from servers.tasks import create_task, create_subtask

    if not items:
        return {'epic_id': None, 'story_count': 0, 'task_count': 0}

    epic_id = create_task(
        project=project_name,
        description=f"Refactor for Testability: {len(items)} units",
        priority=7, task_level='epic')

    task_count = 0
    for it in items:
        sym = it.get('name', '?')
        fp = it.get('file_path', '?')
        rtype = it.get('refactor_type', 'Extract Method')

        story_id = create_task(
            project=project_name,
            description=f"Refactor for testability: {sym} in {fp}",
            task_level='story', epic_id=epic_id, priority=7)

        t1 = create_subtask(
            parent_id=story_id,
            description=(
                f"Write characterization tests pinning current behavior of "
                f"{sym} in {fp} (refactor-for-testability safety net). "
                f"Do not judge correctness; pin every branch's current behavior."),
            assigned_agent='executor', requires_validation=True,
            task_level='task', epic_id=epic_id, story_id=story_id)
        t2 = create_subtask(
            parent_id=story_id,
            description=(
                f"Refactor for testability: apply {rtype} to {sym} in {fp}. "
                f"Behavior-preserving, mechanical."),
            assigned_agent='executor', depends_on=[t1],
            requires_validation=True,
            task_level='task', epic_id=epic_id, story_id=story_id)
        create_subtask(
            parent_id=story_id,
            description=(
                f"Verify refactor of {sym} in {fp}: rerun characterization "
                f"tests, must stay green."),
            assigned_agent='executor', depends_on=[t2],
            requires_validation=True,
            task_level='task', epic_id=epic_id, story_id=story_id)
        task_count += 3

    return {'epic_id': epic_id, 'story_count': len(items),
            'task_count': task_count}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python3 -m pytest tests/test_refactor.py -v`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add servers/recipes.py tests/test_refactor.py
git commit -m "feat(recipes): build_refactor_epic — characterization→refactor→verify chains

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: `find_latest_pending_epic`（run 端通用選 epic）

**Files:**
- Modify: `servers/facade.py`（檔尾新增函式）
- Test: `tests/test_refactor.py`（新增 class）

- [ ] **Step 1: 寫會失敗的測試（加進 `tests/test_refactor.py`）**

```python
class TestFindLatestPendingEpic:
    def test_returns_latest_pending(self, mock_db_path):
        from servers.tasks import create_task, update_task_status
        from servers.facade import find_latest_pending_epic
        e1 = create_task(project="rfe", description="Refactor for Testability: 1 units",
                         priority=7, task_level="epic")
        e2 = create_task(project="rfe", description="Unit Test Coverage: ...",
                         priority=7, task_level="epic")
        # e1 設為 done → 應回最新 pending 的 e2
        update_task_status(e1, "done")
        got = find_latest_pending_epic("rfe")
        assert got is not None and got["id"] == e2

    def test_none_when_no_pending(self, mock_db_path):
        from servers.tasks import create_task, update_task_status
        from servers.facade import find_latest_pending_epic
        e = create_task(project="rfe2", description="X", priority=7, task_level="epic")
        update_task_status(e, "done")
        assert find_latest_pending_epic("rfe2") is None
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python3 -m pytest tests/test_refactor.py::TestFindLatestPendingEpic -v`
Expected: FAIL（`find_latest_pending_epic` 不存在）

- [ ] **Step 3: 實作（加在 `servers/facade.py` 檔尾）**

```python
def find_latest_pending_epic(project_name: str) -> Optional[Dict]:
    """回傳該專案最新（created_at DESC）狀態為 pending 的 epic，無則 None。

    供 /han:run 在未指定 epic_id 時選預設 epic。通用：不限 refactor。
    回傳 {'id', 'description', 'status', ...}（get_epic_tasks 的 epic dict）。
    """
    from servers.tasks import get_epic_tasks
    for epic in get_epic_tasks(project_name):  # 已 ORDER BY created_at DESC
        if epic.get('status') == 'pending':
            return epic
    return None
```

> 註：`facade.py` 已 `from typing import ... Optional, Dict`（沿用既有 import；若無則於檔頭補）。

- [ ] **Step 4: 跑測試確認通過**

Run: `python3 -m pytest tests/test_refactor.py -v`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add servers/facade.py tests/test_refactor.py
git commit -m "feat(facade): find_latest_pending_epic for generic /han:run default

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: `/han:refactor` 指令（規劃，不改碼）

**Files:**
- Create: `commands/han/refactor.md`

此檔是給主代理的 prompt（無 pytest）。流程：環境變數傳值 → 呼叫 `scan_refactor_candidates` → 主代理依 refactor playbook 型錄分類候選 → 高把握呼叫 `build_refactor_epic`、低把握收進建議 → 寫報告檔 → 回報。

- [ ] **Step 1: 建立指令檔**

Create `commands/han/refactor.md`：

````markdown
---
description: 'HAN：分析可測試性熱點並產出重構規劃（高把握→可執行任務樹；沒把握→建議報告）。只規劃、不改碼、不派工。'
---

# /han:refactor — 為可測試性重構（規劃）

把 `$ARGUMENTS` 當作分析範圍，掃出「測不動的糾纏鏈路」熱點，依**重構型錄**分類：
高把握（機械式、行為不變）建成可執行任務樹（含 characterization-test 安全網）；沒把握的只列為建議交人決定。
**本指令只規劃：不修改任何原始碼、不派工。** 要實際執行請接 `/han:run`。

## 範圍解讀（`$ARGUMENTS`）
- 路徑（如 `servers/`）→ 當 `target_path`
- 模組名 / 自然語言 → 對應到路徑；對不到就用整個專案
- 空白 → 整個專案

## 執行步驟

> 安全準則：**所有專案/路徑/範圍值一律透過環境變數傳入 Python，絕不內插進 Python 程式碼字串**。`{{HAN_DIR}}` 由安裝程序替換為安全字面量。

1. 設定環境變數：
```bash
export HAN_PROJECT_PATH="$(pwd)"
export HAN_PROJECT="$(basename "$HAN_PROJECT_PATH")"
export HAN_TARGET="servers/"   # ← 換成解讀出的範圍路徑；整個專案則留空字串 ""
```

2. 掃描候選（不建 epic、不改碼）：
```bash
python3 - <<'PY'
import os, sys, json
sys.path.insert(0, {{HAN_DIR}})
from servers.recipes import scan_refactor_candidates
r = scan_refactor_candidates(
    project_name=os.environ['HAN_PROJECT'],
    project_path=os.environ['HAN_PROJECT_PATH'],
    target_path=(os.environ.get('HAN_TARGET') or None))
print(r['message'])
print('CANDIDATES_JSON_START')
print(json.dumps(r['candidates'], ensure_ascii=False))
print('CANDIDATES_JSON_END')
PY
```
- 候選為空 → 回報訊息後**停止**（沒有熱點）。

3. **分類（主代理判斷）**：讀 `reference/playbooks/refactor.md` 的型錄表，對每個候選讀其原始碼（`file_path` 的 `line_start`–`line_end`），判定需要的重構型錄項屬「高把握」或「沒把握」：
   - **高把握**（Extract Method/Variable、Inline、Rename、Decompose Conditional、Replace Magic Number 等，且區域範圍、不改 public 契約、可被 characterization test 釘住）→ 收進 `high` 清單，每項記 `{file_path, name, refactor_type, line_start, line_end}`。
   - **沒把握**（Introduce Interface/DI、Move、改簽章、打斷共享狀態、繼承改組合、動到並行/IO/框架，或無法寫 characterization test）→ 收進 `low` 清單，記位置 + 型錄項 + 理由。

4. 建可執行任務樹（只放高把握；值用環境變數/檔案傳，勿內插）：把 `high` 清單寫進暫存 JSON 後讀入——
```bash
cat > /tmp/han_refactor_high.json <<'JSON'
[ ...把 high 清單貼成 JSON 陣列... ]
JSON
python3 - <<'PY'
import os, sys, json
sys.path.insert(0, {{HAN_DIR}})
from servers.recipes import build_refactor_epic
items = json.load(open('/tmp/han_refactor_high.json', encoding='utf-8'))
r = build_refactor_epic(os.environ['HAN_PROJECT'], items)
print(r['message'] if 'message' in r else r)
print('EPIC', r.get('epic_id'))
PY
```
- `high` 為空 → 不建 epic（`EPIC None`）。

5. 寫計畫報告（用 Write 工具）至 `.han/refactor-plan-<ts>.md`（先 `mkdir -p .han`，`ts` 用 `date +%Y%m%d-%H%M%S`）：
   - 標頭：target、掃描熱點數、是否截斷、`epic_id`。
   - 區段 A「已排入計畫（高把握）」：逐項列 檔案/方法、重構型錄項、三步任務。
   - 區段 B「建議／需人工決定（沒把握）」：逐項列 位置、型錄項、**判定為沒把握的理由**。

6. 收尾回報：`epic_id`、報告路徑、高把握任務數、沒把握建議數。提示「要執行請跑 `/han:run <epic_id>`」。

## 重要
- **絕不修改原始碼、絕不派工**——這是純規劃指令。
- 分類務必依 `reference/playbooks/refactor.md` 型錄；拿不準的一律歸「沒把握／建議」（寧可保守）。
````

- [ ] **Step 2: 冒煙驗證（對 han-agents 自身跑掃描，確認管線可通、不改碼）**

```bash
cd /home/agent/han-agents
export HAN_PROJECT_PATH="$(pwd)"; export HAN_PROJECT="$(basename "$HAN_PROJECT_PATH")"
python3 - <<'PY'
import os, sys, json
sys.path.insert(0, os.environ['HAN_PROJECT_PATH'])
from servers.recipes import scan_refactor_candidates
r = scan_refactor_candidates(os.environ['HAN_PROJECT'], os.environ['HAN_PROJECT_PATH'], target_path="servers/")
print(r['message'])
print("count:", len(r['candidates']))
print("sample:", json.dumps(r['candidates'][:2], ensure_ascii=False))
PY
git checkout -- . 2>/dev/null; git status --short   # 確認工作區未被改動
```
Expected: 印出訊息 + 候選數（可為 0）；`git status --short` 對原始碼**無任何改動**（只多了未追蹤的指令檔）。

- [ ] **Step 3: Commit**

```bash
git add commands/han/refactor.md
git commit -m "feat(commands): add /han:refactor (plan-only testability refactoring)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: `/han:run` 指令（通用執行）

**Files:**
- Create: `commands/han/run.md`

- [ ] **Step 1: 建立指令檔**

Create `commands/han/run.md`：

````markdown
---
description: 'HAN：通用執行器——消費任一規劃產出的任務樹（epic），驅動 executor→critic 派工迴圈把它做完。'
---

# /han:run — 通用執行任務樹

把 `$ARGUMENTS` 當作 `epic_id` 執行；省略則自動取本專案**最新 pending epic**（先印出選了哪個再跑）。
驅動 `get_next_dispatch` → `Agent` 派工迴圈直到完成。可接在 `/han:refactor`（或未來 plan/feat）規劃之後。

## 執行步驟

> `{{HAN_DIR}}` 由安裝程序替換為安全字面量。值一律走環境變數，勿內插。

1. 設定環境變數（`HAN_EPIC` 為 `$ARGUMENTS`；空白代表自動選）：
```bash
export HAN_PROJECT_PATH="$(pwd)"
export HAN_PROJECT="$(basename "$HAN_PROJECT_PATH")"
export HAN_EPIC=""   # ← 有指定就填 epic_id；否則留空自動選最新 pending
```

2. 解析要執行的 epic：
```bash
python3 - <<'PY'
import os, sys
sys.path.insert(0, {{HAN_DIR}})
eid = os.environ.get('HAN_EPIC') or None
if not eid:
    from servers.facade import find_latest_pending_epic
    epic = find_latest_pending_epic(os.environ['HAN_PROJECT'])
    if not epic:
        print('NO_PENDING_EPIC'); sys.exit(0)
    eid = epic['id']
    print(f"自動選用最新 pending epic：{eid} — {epic.get('description','')}")
else:
    print(f"使用指定 epic：{eid}")
print('RESOLVED_EPIC', eid)
PY
```
- 輸出 `NO_PENDING_EPIC` → 回報「找不到可執行的 epic，請先 `/han:refactor` 或指定 epic_id」並**停止**。
- 否則記下 `RESOLVED_EPIC` 後面的 epic_id。

3. 驅動派工迴圈（重複，直到 `action != 'dispatch'`）。把 epic_id 放進 `HAN_EPIC` 再執行：
```bash
HAN_EPIC="<resolved_epic_id>" python3 - <<'PY'
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

4. 收尾回報：完成幾條鏈/任務、改了哪些檔、（若是 refactor epic）characterization test pass/fail 摘要。

## 重要
- **通用**：對任何 recipe/planner 產出的 epic 都能執行，不綁 refactor。
- 一定要跑完 dispatch 迴圈讓 executor 真的做、critic 真的驗——不要只解析 epic 就回報。
- playbook（含行為不變、characterization-first、build.gradle 護欄）由派工 prompt 依任務描述**自動注入**，無須手動帶。
````

- [ ] **Step 2: 冒煙驗證（epic 解析邏輯，用既有 recipe 建一個 epic 再讓 run 找到它）**

```bash
cd /home/agent/han-agents
python3 - <<'PY'
import os, sys
sys.path.insert(0, os.getcwd())
# 用測試專案名，避免污染真實資料
from servers.tasks import create_task
from servers.facade import find_latest_pending_epic
proj = "smoke_run_demo"
eid = create_task(project=proj, description="Refactor for Testability: 1 units", priority=7, task_level="epic")
got = find_latest_pending_epic(proj)
assert got and got["id"] == eid, got
print("OK 解析到 epic:", got["id"], got["description"])
PY
```
Expected: `OK 解析到 epic: ...`

> 註：此冒煙會在真實 brain DB 寫入一個 `smoke_run_demo` 測試 epic。驗證後清掉：
```bash
python3 - <<'PY'
import os, sys; sys.path.insert(0, os.getcwd())
from servers import managed_connection
with managed_connection() as db:
    db.cursor().execute("DELETE FROM tasks WHERE project='smoke_run_demo'"); db.commit()
print("cleaned")
PY
```

- [ ] **Step 3: Commit**

```bash
git add commands/han/run.md
git commit -m "feat(commands): add /han:run (generic epic executor / dispatch loop)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 7: 全量回歸 + 文件

**Files:**
- Modify: `README.md` / `README.zh-TW.md` / `SKILL.md`（指令清單加 `/han:refactor`、`/han:run`）

- [ ] **Step 1: 全量測試**

Run: `python3 -m pytest tests/ -q`
Expected: 全 PASS（無回歸）

- [ ] **Step 2: 把兩個新指令加進指令清單文件**

在 `README.md`、`README.zh-TW.md`、`SKILL.md` 既有的 `/han:*` 指令列表處，新增兩列（用各檔現有格式）：
- `/han:refactor <path>` — 分析可測試性熱點、產出重構規劃（高把握→可執行任務樹；沒把握→建議）。只規劃不改碼。
- `/han:run [epic_id]` — 通用執行器，消費任一 epic 跑 executor→critic 派工迴圈。

> 用 `Grep` 找各檔現有的 `/han:unit-test` 那一行，照同樣排版插入兩行。

- [ ] **Step 3: Commit**

```bash
git add README.md README.zh-TW.md SKILL.md
git commit -m "docs: list /han:refactor and /han:run in command references

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review（撰寫後檢查，已修正）

**1. Spec coverage：**
- §4.1 scan（確定性熱點掃描）→ Task 2 ✅
- §4.1b epic 建立（三步相依鏈）→ Task 3 ✅
- §4.2 refactor playbook（型錄+原則+checklist+護欄）→ Task 1 ✅
- §4.3 `/han:refactor` 指令（掃描→分類→建樹→報告）→ Task 5 ✅
- §4.4 `/han:run` 指令（latest pending epic + 派工迴圈）→ Task 4（helper）+ Task 6 ✅
- §4.5 報告格式 → Task 5 Step 1（區段 A/B）✅
- §6 build.gradle 護欄 → Task 1 playbook ✅
- §7 錯誤處理（無候選、無 pending epic、blocked/waiting、寫不出 characterization）→ Task 2/5/6 訊息 + playbook ✅
- §8 測試策略 → Task 1–4 測試 ✅
- §9 不做（不動現有指令、不做分支覆蓋率、不做手動記憶寫入）→ 計畫未涉及 ✅

**2. Placeholder scan：** 無 TBD/TODO；指令檔內 `[ ...把 high 清單貼成 JSON... ]` 是主代理執行期填入的資料佔位（prompt 設計如此），非計畫缺漏。

**3. Type consistency：**
- `scan_refactor_candidates` 回傳鍵 `candidates/total_hotspots/truncated/existing_pending_epic/message` — Task 2 測試與 Task 5 指令一致 ✅
- 候選/items dict 欄位 `file_path/name/refactor_type/line_start/line_end` — Task 2 產出、Task 3 消費一致 ✅
- `build_refactor_epic` 回傳 `epic_id/story_count/task_count` — Task 3 測試一致 ✅
- `find_latest_pending_epic` 回 epic dict（含 `id`/`description`）— Task 4 測試與 Task 6 指令一致 ✅
- 三步任務描述（characterization / refactor for testability / verify refactor）在 Task 1 測試、Task 3 實作、Task 3 測試三處字串一致 ✅
- playbook 名稱 `refactor`、`match` 含 `refactor`/`characterization` — Task 1 與 Task 3 描述可被命中一致 ✅
