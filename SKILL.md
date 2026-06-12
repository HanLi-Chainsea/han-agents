---
name: han-agents
description: |
  Multi-agent task system for complex tasks. Three-layer architecture (Skill + Code Graph + Memory),
  task lifecycle with validation, semantic search, drift detection. Use when: user requests PFC agent,
  complex multi-step tasks, multi-agent coordination, or mentions han.
allowed-tools: Read, Write, Bash, Glob, Grep, Task
---

# HAN-Agents

**HAN** = **H**ierarchical **A**pproached **N**euromorphic Agents

> **Zero-Config**: Clone 到 skills 目錄即可使用，資料庫會在首次呼叫時自動建立。

## Auto-Setup (首次使用時執行，冪等可重複，跨平台)

```python
import sys, os

# 動態偵測 han-agents 位置（支援所有平台）
han_dir = os.path.dirname(os.path.abspath('__file__'))  # SKILL.md 所在目錄
# 若上面不正確，fallback 到常見位置
if not os.path.exists(os.path.join(han_dir, 'servers')):
    for candidate in [
        os.path.expanduser('~/.claude/skills/han-agents'),
        os.path.expanduser('~/.cursor/skills/han-agents'),
        os.path.expanduser('~/.codeium/windsurf/skills/han-agents'),
        os.path.expanduser('~/.cline/skills/han-agents'),
        os.path.expanduser('~/.codex/skills/han-agents'),
        os.path.expanduser('~/.gemini/skills/han-agents'),
    ]:
        if os.path.exists(os.path.join(candidate, 'servers')):
            han_dir = candidate
            break

sys.path.insert(0, han_dir)

# 一鍵設定：DB + agents + hooks（自動偵測平台，跳過不支援的功能）
from servers.platform import auto_setup
result = auto_setup()
print(f"HAN-Agents ready. Platform: {result['platform']}")
```

## Quick Start

```python
import sys, os
from servers import HAN_BASE_DIR
sys.path.insert(0, HAN_BASE_DIR)

from servers.facade import get_full_context, check_drift, sync, finish_task, get_next_dispatch
from servers.tasks import create_task, create_subtask, get_task_progress, get_epic_tasks, get_hierarchy_summary
from servers.recipes import recipe_unit_tests, run_recipe
from servers.project import ensure_project
from servers.memory import search_memory_semantic, store_memory, save_checkpoint, load_checkpoint
from servers.code_graph import get_class_dependencies_bfs
```

**DB**: `<han-agents>/brain/brain.db`（首次呼叫時自動建立）

## Project Initialization

專案初始化會在首次使用時自動觸發（lazy init）：

```python
from servers.project import ensure_project
result = ensure_project('my-project', '/path/to/project')
# 自動 sync Code Graph + 偵測技術棧 + 存 DB
# result['tech_stack'] → {'primary_language': 'python', 'test_tool': 'pytest', ...}
```

## When to Use

| Use PFC System | Direct Execution |
|----------------|------------------|
| 3+ step tasks | Single-step tasks |
| Needs planning/validation | Quick fixes |
| User requests agents | Clear instructions |
| Skill consistency checks | Read-only queries |

## Agents

| Agent | subagent_type | model_tier | Purpose |
|-------|---------------|------------|---------|
| PFC | `pfc` | `planner` | Planning, decomposition |
| Executor | `executor` | `worker` | Task execution |
| Critic | `critic` | `worker` | Validation |
| Memory | `memory` | `fast` | Knowledge storage |
| Researcher | `researcher` | `worker` | Information gathering |
| Drift Detector | `drift-detector` | `fast` | Skill-Code drift |

## Workflow

```
PFC (plan) → Executor (do) → Critic (verify) → Memory (store)
                                  ↓ REJECTED
                            Executor (fix) → Critic (re-verify)
```

## Key APIs

```python
# Three-layer context (requires project_path)
context = get_full_context({'flow_id': 'flow.auth'}, '/path/to/project', 'my-project')

# Task management
task_id = create_task(project="PROJECT", description="Task", priority=8)
subtask = create_subtask(task_id, "Step 1", assigned_agent='executor')

# Drift detection (Skill vs Code)
report = check_drift('/path/to/project', 'my-project', 'auth')
if report['has_drift']:
    for d in report['drifts']: print(f"[{d['type']}] {d['description']}")

# Semantic search
result = search_memory_semantic("auth pattern", limit=5, rerank_mode='claude')

# Checkpoint
save_checkpoint(project='P', task_id=id, agent='pfc', state={...}, summary='...')
checkpoint = load_checkpoint(task_id)

# Store memory
store_memory(category='pattern', title='Title', content='...', project='P', importance=8)

# BFS dependency graph (for Unit Test context)
deps = get_class_dependencies_bfs('my-project', 'UserService', max_depth=2)
# Returns: root, root_file, dependencies (with depth, via edge type)

# Hierarchical tasks (Jira-like: Epic → Story → Task → Bug)
epic_id = create_task(project='P', description='Epic task', task_level='epic')
story_id = create_task(project='P', description='Story', task_level='story', epic_id=epic_id)
task_id = create_task(project='P', description='Task', task_level='task', story_id=story_id)

# Query hierarchy
epics = get_epic_tasks('P')  # Returns epics with nested stories
summary = get_hierarchy_summary('P')  # {epics: N, stories: N, tasks: N, bugs: N}
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
    # Claude Code: 用 Task tool 派發
    Task(subagent_type=inst['subagent_type'], prompt=inst['prompt'])
    # 其他平台: 直接在 context 中執行 inst['prompt']
```

**Available Recipes**: `unit_tests`、`code_review`、`integration_tests`、`e2e_tests`

```python
from servers.recipes import run_recipe
# 使用者：「幫 servers/ 寫單元測試」
run_recipe('unit_tests', project_name='p', project_path='/path', target_path='servers/')
# 使用者：「code review 這次改動」
run_recipe('code_review', project_name='p', project_path='/path')  # 預設 git diff HEAD
# 使用者：「幫 auth 模組寫整合測試」
run_recipe('integration_tests', project_name='p', project_path='/path', target_path='servers/auth/')
# 使用者：「幫結帳流程寫 E2E」
run_recipe('e2e_tests', project_name='p', project_path='/path', target_path='servers/checkout/')
```

### Slash 指令（直接觸發，免先叫 han）

`commands/han/*.md` 提供斜線指令，由 `auto_setup()` 自動安裝到 `~/.claude/commands/han/`
（安裝時把 `{{HAN_DIR}}` 替換為實際 han 路徑）。斜線指令是**確定觸發**，一定走
dispatch → playbook 一定注入：

| 指令 | 形狀 | 說明 |
|------|------|------|
| `/han:unit-test <範圍>` | recipe + dispatch | 找缺口、寫測試、跑過 |
| `/han:integration-test <範圍>` | recipe + dispatch | 模組整合測試 |
| `/han:e2e <範圍>` | recipe + dispatch | 關鍵旅程 E2E |
| `/han:review <code 或 想法>` | 單次（不改原始碼） | 帶脈絡批判；有 PR/MR 預設貼 comment，否則顯示，`--out` 才寫檔 |
| `/han:drift` | 單次讀 | SSOT(意圖) vs Code(現實) 偏差報告 |
| `/han:impact <檔案/符號>` | 單次讀 | 改動影響半徑（誰呼叫、依賴誰） |
| `/han:recall <主題>` | 單次讀 | 撈過往決策/教訓（長期記憶） |
| `/han:status` | 單次讀 | 專案狀態速覽（圖譜/任務/記憶） |
| `/han:sync` | 維護 | 增量同步 Code Graph（不改原始碼） |
| `/han:help` | 單次讀 | 列出所有 /han 指令與用途（動態） |

**get_next_dispatch() 自動處理**:
- Executor → Critic validation loop（幂等，不會建重複 critic）
- Rejected → retry with feedback context
- All done → Memory agent stores lessons（等完成才返回 done）
- 返回 `model_tier`（planner/worker/fast）供平台選擇模型

## Manual Agent Dispatch (Advanced)

直接使用 Task tool 派發 agent：

```
Task(
    subagent_type='pfc',        # 或 executor, critic, researcher, memory, drift-detector
    prompt='任務描述...'
)
```

**⭐ 派發 PFC 時必須包含的指示：**

```
Task(
    subagent_type='pfc',
    prompt=f'''PROJECT = "{project_name}"
PROJECT_PATH = "{project_path}"

任務：{user_request}

**要求：**
1. 先執行 sync(PROJECT_PATH, PROJECT) 同步 Code Graph
2. 分析任務範圍，使用 Code Graph 確認相關檔案
3. 規劃子任務 DAG
4. **必須呼叫 create_task() 將任務寫入 DB**
5. 輸出派發指令供主對話執行
'''
)
```

> ⚠️ **必要參數**：
> - `PROJECT` 和 `PROJECT_PATH` 是必填，供 agent 執行 `sync()` 和 `create_task()` 使用
> - prompt 必須明確要求 PFC 「呼叫 create_task() 將任務寫入 DB」，否則 PFC 可能只輸出規劃文字而不建立任務

**⭐ 派發 PFC 時的檔案範圍處理：**

> ⚠️ **主對話不要自己搜尋檔案後放入 prompt** — 這會讓 PFC 誤以為是使用者指定範圍而漏掉檔案

| 情況 | 主對話行為 | PFC 行為 |
|------|-----------|----------|
| 使用者明確指定檔案/範圍 | 將指定內容放入 prompt | 以指定範圍為主，用 Code Graph 檢查相關聯檔案 |
| 使用者沒指定範圍 | **直接傳任務描述，不搜尋檔案** | 自行使用 Code Graph 查詢完整範圍 |

```python
# ✅ 正確：使用者說「幫 src/ 寫測試」→ 直接傳描述
Task(subagent_type='pfc', prompt='為 src/ 下的模組撰寫單元測試')

# ❌ 錯誤：主對話自己先 glob 再傳（可能漏檔案，且 PFC 會誤以為是指定範圍）
files = glob('src/**/*.ts')  # 主對話搜尋
Task(subagent_type='pfc', prompt=f'為以下檔案寫測試:\n{files}')
```

**派發流程：**
1. 主對話判斷：使用者有沒有明確指定檔案範圍？
2. **沒指定 → 直接傳任務描述給 PFC，不要先搜尋**
3. PFC 用 **Code Graph 確認完整範圍**
4. PFC 規劃後輸出「派發指令」
5. **主對話**使用 Task tool 執行派發

## ⭐ 主對話：收到 PFC 輸出後的處理

**PFC agent 返回後，主對話必須：**

```python
# 1. 檢查 DB 任務狀態
from servers.tasks import get_task_progress
progress = get_task_progress(project="PROJECT_NAME")
pending_tasks = [t for t in progress.get('tasks', []) if t['status'] == 'pending']

# 2. 根據任務的 assigned_agent 派發對應 agent
# 3. 使用 Task tool 派發（executor/critic/researcher/memory 等）
```

**主對話職責（不可省略）：**
| 步驟 | 動作 |
|------|------|
| PFC 完成 | 讀取 DB 確認任務已建立 |
| 用戶確認 | 根據 `assigned_agent` 派發對應 agent |
| Agent 完成 | 檢查任務是否需要驗證，派發 critic |
| 全部完成 | 派發 memory 儲存經驗 |

**❌ 禁止：** 主對話看到 PFC 輸出後不操作 DB，只等用戶手動提醒

**範例 - 派發 Executor：**
```
Task(
    subagent_type='executor',
    prompt=f'''TASK_ID = "{subtask_id}"
Task: [description]
Source: [file path]
Steps: 1. Read 2. Execute 3. Verify'''
)
```

**範例 - 派發 Critic：**
```
Task(
    subagent_type='critic',
    prompt=f'''PROJECT = "{project_name}"
PROJECT_PATH = "{project_path}"
TASK_ID = "{critic_task_id}"
ORIGINAL_TASK_ID = "{original_task_id}"

**要求：**
1. 先執行 sync(PROJECT_PATH, PROJECT) 同步 Code Graph（確保驗證最新狀態）
2. 驗證任務產出...'''
)
```

> ⚠️ Critic 也需要 `PROJECT_PATH`，因為 Executor 可能修改了檔案，需要 sync 後才能正確驗證

## Scripts

```bash
python <han-agents>/scripts/install.py        # (Optional) 手動安裝
python <han-agents>/scripts/doctor.py         # Diagnostics
python <han-agents>/scripts/sync.py PATH      # Graph sync
python <han-agents>/scripts/init_project.py NAME [PATH]  # Init project
```

## Reference

- [API_REFERENCE.md](reference/API_REFERENCE.md) - Complete API
- [WORKFLOW_GUIDE.md](reference/WORKFLOW_GUIDE.md) - Detailed workflow
- [GRAPH_GUIDE.md](reference/GRAPH_GUIDE.md) - Graph operations
- [TROUBLESHOOTING.md](reference/TROUBLESHOOTING.md) - Common issues
- [reference/agents/](reference/agents/) - Agent definitions
