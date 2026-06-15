# Workflow Guide

## Task Recovery

### New Conversation

```python
import sys, os
from servers import HAN_BASE_DIR
sys.path.insert(0, HAN_BASE_DIR)
from servers.memory import get_project_context, load_checkpoint

project = os.path.basename(os.getcwd())
context = get_project_context(project)

if context['active_tasks']:
    task = context['active_tasks'][0]
    checkpoint = load_checkpoint(task['id'])
```

### Known Task ID

```python
from servers.tasks import get_task_progress
progress = get_task_progress('TASK_ID')
checkpoint = load_checkpoint('TASK_ID')
```

## Project Initialization

初始化專案（sync Code Graph + 偵測技術棧 + 存 DB）：

```python
from servers.project import ensure_project
result = ensure_project('my-project', '/path/to/project')
# result['tech_stack'] → {'primary_language': 'python', 'test_tool': 'pytest', ...}
```

Or via CLI:

```bash
cd <path-to-han-agents>
python scripts/init_project.py my-project /path/to/project
```

專案資訊存在 DB 的 `long_term_memory` 中，不在專案目錄建立任何檔案。

## Starting Tasks

```python
from servers.tasks import create_task, create_subtask

task_id = create_task(project="PROJECT", description="Task", priority=8)
subtask_1 = create_subtask(task_id, "Step 1", assigned_agent='executor')
subtask_2 = create_subtask(task_id, "Step 2", depends_on=[subtask_1])
```

## Automated Workflow (Recommended)

**Recipe + Dispatch Loop** — 自動建任務樹、自動派發 agent：

```python
from servers.recipes import recipe_unit_tests
from servers.facade import get_next_dispatch

# 1. Recipe 自動分析專案、建立任務樹
result = recipe_unit_tests('my-project', '/path/to/project')
print(result['message'])  # "Created 12 test tasks across 8 files."

# 2. Dispatch loop — 重複呼叫直到完成
while True:
    inst = get_next_dispatch(result['epic_id'], 'my-project', '/path/to/project')
    if inst['action'] != 'dispatch':
        print(inst['message'])
        break
    # Claude Code: 用 Agent tool 派發
    Agent(subagent_type=inst['subagent_type'], prompt=inst['prompt'])
    # 其他平台: 直接在 context 中執行 inst['prompt']
```

**`get_next_dispatch()` 自動處理**:
- Executor → Critic validation loop（幂等，不會建重複 critic）
- Rejected → retry with feedback context
- All done → Memory agent stores lessons（等完成才返回 done）
- 返回 `model_tier`（planner/worker/fast）供平台選擇模型

## Manual Agent Dispatch (Advanced)

```
PFC (plan) → Executor (do) → Critic (verify) → Memory (store)
     ↑                            │
     └────── REJECTED ────────────┘
```

### Dispatch Executor

```python
Agent(
    subagent_type='executor',
    prompt=f'''TASK_ID = "{task_id}"
Task: [description]
Source: [file]
Steps: 1. Read 2. Execute 3. Verify'''
)
```

### Dispatch Critic

```python
Agent(
    subagent_type='critic',
    prompt=f'''TASK_ID = "{critic_id}"
ORIGINAL_TASK_ID = "{original_id}"
Target: [file]
Criteria: Coverage >= 80%, Edge cases'''
)
```

## Validation Cycle

### Hook Flow

```
Executor ends → Hook: finish_task() → Phase: validation
Critic outputs APPROVED/CONDITIONAL/REJECTED → Hook: finish_validation()
```

### Handling Results

| Result | Action |
|--------|--------|
| APPROVED | Phase → documentation |
| CONDITIONAL | Store suggestions, continue |
| REJECTED | Phase → execution (retry) |

### Resume Rejected Task

```python
task = get_task(original_task_id)
if task.get('executor_agent_id'):
    Agent(subagent_type='executor', resume=task['executor_agent_id'],
         prompt="Fix based on Critic feedback")
```

## Micro-Nap

```python
save_checkpoint(
    project='PROJECT',
    task_id=task_id,
    agent='pfc',
    state={'completed': [...], 'pending': [...]},
    summary='Phase 1 complete'
)
print("Suggest new conversation. Resume: Continue task {task_id}")
```

## Task/Memory APIs

```python
# Tasks
from servers.tasks import (
    create_task, create_subtask, get_task, update_task_status,
    get_next_task, get_task_progress, get_unvalidated_tasks,
    mark_validated, advance_task_phase, reserve_critic_task,
    get_epic_tasks, get_story_tasks, get_hierarchy_summary,
)
# Status: pending, running, done, failed, blocked
# Phase: execution, validation, documentation, completed
# Task levels: epic, story, task, bug

# Recipes
from servers.recipes import recipe_unit_tests, run_recipe

# Memory
from servers.memory import (
    search_memory, store_memory, get_working_memory, set_working_memory,
    save_checkpoint, load_checkpoint, add_episode
)
```

## Drift Detection

在開始重要修改前檢查 Skill vs Code 偏差：

```python
from servers.facade import check_drift

report = check_drift('/path/to/project', 'my-project')
if report['has_drift']:
    print(f"⚠️ {report['summary']}")
    for d in report['drifts']:
        print(f"  - [{d['severity']}] {d['description']}")
```

## 複雜任務原則

適用於：大型 codebase、多檔案變更、陌生專案

### 1. Research First（研究先行）

> 大型專案先派發 **Researcher** 收集 context，再規劃任務。

需要了解：
- 構建工具和版本（Maven/Gradle/npm）
- 測試框架（JUnit, Mockito, Jest）
- 專案慣例（命名規則、目錄結構）
- 目標範圍內的檔案清單

### 2. Context Precision（精準 context）

> 只讀必要檔案，避免 context 污染。

**錯誤**：「讀取整個 src/ 資料夾」
**正確**：「讀取被測類 + 依賴介面 + 相似範例」

### 3. Compile-First（編譯優先）

> 先確保編譯通過，再追求功能正確。

寫完代碼後立即執行編譯驗證，不要等到最後才發現依賴錯誤。

### 4. Incremental Fix（增量修復）

> 錯誤逐個修，不要一次改太多。

一次修一個編譯錯誤，確認修復後再處理下一個。

### 5. Memory Learning（記憶學習）

> 踩過的坑存入 Memory，下次避免。

```python
store_memory(
    category='error',
    title='Mock Strategy Error',
    content='使用 @Mock 而非 @MockBean 導致 Spring context 注入失敗',
    importance=8
)
```

---

## Best Practices

1. Use `recipe_unit_tests()` + `get_next_dispatch()` for automated workflows
2. Parallel dispatch for independent tasks
3. Critic validates after each Executor (auto-handled by dispatch loop)
4. Store important discoveries to Memory
5. Checkpoint regularly for long-running tasks
6. Check drift before major changes
7. Use `ensure_project()` for project initialization (no manual SKILL.md needed)
