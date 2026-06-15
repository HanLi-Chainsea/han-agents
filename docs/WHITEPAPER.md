# HAN-Agents 技術白皮書

> **版本**：1.0.0
> **最後更新**：2026-01-16
> **適用對象**：接手工程師、技術主管、系統架構師

---

## 目錄

1. [專案概述](#1-專案概述)
2. [核心架構](#2-核心架構)
3. [系統組件](#3-系統組件)
4. [代理系統](#4-代理系統)
5. [資料庫設計](#5-資料庫設計)
6. [API 參考](#6-api-參考)
7. [安裝與部署](#7-安裝與部署)
8. [開發指南](#8-開發指南)
9. [常見問題](#9-常見問題)
10. [附錄](#附錄)

---

## 1. 專案概述

### 1.1 什麼是 HAN-Agents？

**HAN-Agents**（**H**ierarchical **A**pproached **N**euromorphic Agents）是一套多代理任務協調系統，設計用於輔助 AI 工具（特別是 Claude Code）執行複雜的軟體開發任務。

系統透過模擬人腦的決策機制，將複雜任務分解、執行、驗證，並累積經驗記憶。

### 1.2 核心價值

| 能力 | 說明 |
|------|------|
| **任務規劃** | 將複雜任務自動分解為可執行的子任務 |
| **品質驗證** | 透過 Critic 代理驗證任務產出 |
| **經驗累積** | 語義記憶系統儲存學習成果 |
| **偏差偵測** | 自動檢測文檔與程式碼的不一致 |
| **程式碼分析** | AST 解析建構程式碼關係圖 |

### 1.3 技術規格

- **程式語言**：Python 3.8+
- **資料庫**：SQLite 3.35+（支援 FTS5）
- **程式碼行數**：約 12,000 行
- **標準相容**：[Agent Skills](https://agentskills.io) 標準
- **支援平台**：Claude Code、Cursor、Windsurf、Cline、Codex CLI、Gemini CLI、Antigravity、Kiro
- **授權**：MIT

---

## 2. 核心架構

### 2.1 三層真相架構

HAN-Agents 的核心設計理念是「三層真相」（Three-Layer Truth），每層代表不同面向的真相：

```
┌─────────────────────────────────────────────────────────────┐
│                  SSOT Layer（意圖層）                         │
│                   「應該怎樣」                                 │
├─────────────────────────────────────────────────────────────┤
│  ・SKILL.md — 專案定義入口                                   │
│  ・reference/*.md — 參考文檔                                 │
│  ・儲存於 project_nodes, project_edges 表                    │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│                Code Graph Layer（現實層）                     │
│                   「實際怎樣」                                 │
├─────────────────────────────────────────────────────────────┤
│  ・AST 解析的程式碼結構                                       │
│  ・檔案、類別、函式、介面等節點                               │
│  ・import、call、extends 等關係邊                             │
│  ・儲存於 code_nodes, code_edges 表                          │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│                 Memory Layer（經驗層）                        │
│                   「記住怎樣」                                 │
├─────────────────────────────────────────────────────────────┤
│  ・長期語義記憶（學習成果、最佳實踐）                          │
│  ・工作記憶（當前任務狀態）                                    │
│  ・儲存於 long_term_memory, working_memory 表                │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 衝突處理原則

當三層真相之間出現不一致時：

| 情境 | 處理方式 |
|------|----------|
| SSOT 說 X，Code 做 Y | 標記為「實作偏差」→ 人類決策 |
| Code 有 X，SSOT 沒記載 | 標記為「未文檔化功能」→ 補文檔 |
| SSOT 說 X，Test 失敗 | 標記為「破壞承諾」→ 高優先修復 |
| Code + Test 一致，SSOT 不同 | SSOT 過時 → 更新文檔 |

### 2.3 資料流向

```
git pull / 程式碼變更
        │
        ▼
┌───────────────────┐     ┌───────────────────┐
│ Code Graph        │     │ SSOT Loader       │
│ Extractor         │     │                   │
│ (AST 解析)        │     │ (Markdown 解析)   │
└────────┬──────────┘     └────────┬──────────┘
         │                         │
         ▼                         ▼
┌─────────────────────────────────────────────┐
│              brain.db (SQLite)               │
├─────────────────────────────────────────────┤
│  code_nodes, code_edges      ← 現實層       │
│  project_nodes, project_edges ← 意圖層      │
│  long_term_memory            ← 經驗層       │
│  tasks, agent_logs           ← 任務管理     │
└─────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────┐
│              Agent System                    │
├─────────────────────────────────────────────┤
│  PFC     → 規劃任務                          │
│  Executor → 執行任務                         │
│  Critic  → 驗證結果                          │
│  Memory  → 儲存經驗                          │
└─────────────────────────────────────────────┘
```

---

## 3. 系統組件

### 3.1 目錄結構

```
~/.claude/skills/han-agents/
├── SKILL.md                    # Skill 入口定義
├── README.md                   # 快速開始指南
│
├── reference/                  # 參考文檔
│   ├── ARCHITECTURE.md         # 架構設計
│   ├── API_REFERENCE.md        # API 文檔
│   ├── WORKFLOW_GUIDE.md       # 工作流程
│   ├── MEMORY_GUIDE.md         # 記憶操作
│   ├── GRAPH_GUIDE.md          # Graph 操作
│   ├── TROUBLESHOOTING.md      # 問題排解
│   └── agents/                 # 代理定義
│       ├── pfc.md
│       ├── executor.md
│       ├── critic.md
│       ├── researcher.md
│       ├── memory.md
│       └── drift-detector.md
│
├── brain/                      # 資料庫
│   ├── brain.db                # 主資料庫
│   └── schema.sql              # Schema 定義
│
├── servers/                    # 核心服務層
│   ├── facade.py               # 統一入口 API ⭐
│   ├── code_graph.py           # Code Graph 操作
│   ├── graph.py                # SSOT Graph 操作
│   ├── ssot.py                 # SKILL.md 解析
│   ├── memory.py               # 記憶操作
│   ├── tasks.py                # 任務管理
│   ├── drift.py                # 偏差分析
│   └── registry.py             # 類型註冊
│
├── tools/                      # 工具
│   └── code_graph_extractor/   # AST 提取器
│       └── extractor.py
│
├── cli/                        # 命令列工具
│   ├── main.py                 # CLI 入口
│   └── doctor.py               # 診斷工具
│
├── scripts/                    # 輔助腳本
│   ├── install.py              # 安裝腳本
│   ├── doctor.py               # 診斷腳本
│   ├── sync.py                 # Graph 同步
│   └── init_project.py         # 專案初始化
│
└── tests/                      # 測試
    ├── test_facade.py
    ├── test_graph.py
    └── ...
```

### 3.2 核心模組職責

| 模組 | 檔案 | 職責 |
|------|------|------|
| **Facade** | `servers/facade.py` | 統一入口，封裝所有對外 API |
| **Code Graph** | `servers/code_graph.py` | 程式碼結構圖操作、增量更新 |
| **SSOT** | `servers/ssot.py` | SKILL.md 動態解析 |
| **Graph** | `servers/graph.py` | SSOT Graph 節點/邊操作 |
| **Memory** | `servers/memory.py` | 語義記憶、工作記憶、檢查點 |
| **Tasks** | `servers/tasks.py` | 任務生命週期管理 |
| **Drift** | `servers/drift.py` | 偏差分析資料提供 |
| **Registry** | `servers/registry.py` | 節點/邊類型註冊 |
| **Extractor** | `tools/.../extractor.py` | AST 解析（支援 TS/Py/Go） |

### 3.3 依賴關係

```
facade.py ─────┬──→ code_graph.py ──→ registry.py
               │                   └──→ extractor.py
               ├──→ graph.py
               ├──→ ssot.py
               ├──→ memory.py
               ├──→ tasks.py
               └──→ drift.py ──→ ssot.py
                             └──→ code_graph.py
```

---

## 4. 代理系統

### 4.1 代理概覽

系統包含六個專職代理，各司其職：

| 代理 | 識別符 | 職責 | 觸發時機 |
|------|--------|------|----------|
| **PFC** | `pfc` | 任務規劃與分解 | 複雜任務開始時 |
| **Executor** | `executor` | 具體任務執行 | 子任務待執行 |
| **Critic** | `critic` | 品質驗證 | 任務完成後 |
| **Memory** | `memory` | 經驗儲存與檢索 | 學習新知識時 |
| **Researcher** | `researcher` | 資訊收集與分析 | 需要調研時 |
| **Drift Detector** | `drift-detector` | 偏差偵測 | 定期檢查或手動觸發 |

### 4.2 工作流程

標準的任務執行流程：

```
┌─────────┐     ┌──────────┐     ┌─────────┐     ┌─────────┐
│   PFC   │ ──→ │ Executor │ ──→ │ Critic  │ ──→ │ Memory  │
│ (規劃)  │     │  (執行)  │     │ (驗證)  │     │ (儲存)  │
└─────────┘     └──────────┘     └────┬────┘     └─────────┘
                                      │
                                      │ REJECTED
                                      ▼
                                ┌──────────┐
                                │ Executor │
                                │  (修復)  │
                                └────┬─────┘
                                     │
                                     └──→ Critic (重新驗證)
```

### 4.3 代理派發方式

透過 Claude Code 的 Agent tool 派發代理：

```python
# 派發 PFC 規劃任務
Agent(
    subagent_type='pfc',
    prompt=f'''PROJECT = "{project_name}"
PROJECT_PATH = "{project_path}"

任務：{user_request}

要求：
1. 執行 sync(PROJECT_PATH, PROJECT) 同步 Code Graph
2. 分析任務範圍
3. 規劃子任務 DAG
4. 呼叫 create_task() 將任務寫入 DB
'''
)

# 派發 Executor 執行任務
Agent(
    subagent_type='executor',
    prompt=f'''TASK_ID = "{task_id}"
Task: {description}
Steps: 1. Read 2. Execute 3. Verify'''
)

# 派發 Critic 驗證
Agent(
    subagent_type='critic',
    prompt=f'''PROJECT = "{project_name}"
PROJECT_PATH = "{project_path}"
TASK_ID = "{task_id}"

驗證標準：...'''
)
```

### 4.4 PFC 代理詳解

PFC（Prefrontal Cortex）是系統的規劃中樞，負責：

1. **任務分析**：理解使用者需求
2. **Code Graph 查詢**：找出相關檔案和依賴
3. **任務分解**：建立子任務 DAG
4. **資源分配**：指派適當的執行代理

**輸入**：
- PROJECT / PROJECT_PATH
- 任務描述
- （可選）使用者指定的檔案範圍

**輸出**：
- 寫入 DB 的任務記錄
- 派發指令供主對話執行

### 4.5 Critic 代理詳解

Critic 負責品質把關，使用 Code Graph 增強驗證：

1. **完整性檢查**：所有要求是否實現
2. **依賴驗證**：修改是否破壞其他模組
3. **風格一致**：是否符合專案規範

**驗證結果**：
- `APPROVED`：通過，可標記完成
- `REJECTED`：不通過，需重做
- `CONDITIONAL`：條件通過，小問題可後續處理

---

## 5. 資料庫設計

### 5.1 資料庫位置

```
~/.claude/skills/han-agents/brain/brain.db
```

### 5.2 表結構概覽

#### 任務管理層

```sql
-- 主任務表
CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    parent_id TEXT,
    project TEXT NOT NULL,
    description TEXT NOT NULL,
    status TEXT DEFAULT 'pending',  -- pending/in_progress/validation/completed/failed
    phase TEXT DEFAULT 'execution', -- execution/validation/documentation/completed
    priority INTEGER DEFAULT 5,
    assigned_agent TEXT,
    created_at TIMESTAMP,
    completed_at TIMESTAMP
);

-- 任務依賴
CREATE TABLE task_dependencies (
    task_id TEXT,
    depends_on TEXT,
    PRIMARY KEY (task_id, depends_on)
);
```

#### SSOT 層

```sql
-- 專案節點（從 SKILL.md 解析）
CREATE TABLE project_nodes (
    id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    kind TEXT NOT NULL,         -- heading/link/section
    name TEXT NOT NULL,
    content TEXT,
    metadata TEXT               -- JSON
);

-- 專案邊
CREATE TABLE project_edges (
    source TEXT,
    target TEXT,
    kind TEXT NOT NULL,         -- contains/references/links_to
    metadata TEXT
);
```

#### Code Graph 層

```sql
-- 程式碼節點
CREATE TABLE code_nodes (
    id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    kind TEXT NOT NULL,         -- file/class/function/interface
    name TEXT NOT NULL,
    file_path TEXT,
    line_start INTEGER,
    line_end INTEGER,
    signature TEXT,
    metadata TEXT
);

-- 程式碼邊
CREATE TABLE code_edges (
    source TEXT,
    target TEXT,
    kind TEXT NOT NULL,         -- imports/calls/extends/implements
    metadata TEXT
);

-- 檔案雜湊（增量更新用）
CREATE TABLE file_hashes (
    file_path TEXT PRIMARY KEY,
    hash TEXT NOT NULL,
    last_updated TIMESTAMP
);
```

#### 記憶層

```sql
-- 長期記憶
CREATE TABLE long_term_memory (
    id TEXT PRIMARY KEY,
    category TEXT,              -- pattern/decision/lesson/fact
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    project TEXT,
    importance INTEGER DEFAULT 5,
    access_count INTEGER DEFAULT 0,
    created_at TIMESTAMP,
    last_accessed TIMESTAMP
);

-- 工作記憶
CREATE TABLE working_memory (
    id TEXT PRIMARY KEY,
    task_id TEXT,
    context_type TEXT,          -- task_context/decision_point/checkpoint
    content TEXT NOT NULL,
    expires_at TIMESTAMP
);

-- 全文搜索索引
CREATE VIRTUAL TABLE memory_fts USING fts5(
    title, content, category,
    content='long_term_memory'
);
```

#### 類型註冊

```sql
-- 節點類型註冊
CREATE TABLE node_kind_registry (
    kind TEXT PRIMARY KEY,
    display_name TEXT,
    description TEXT,
    icon TEXT,
    color TEXT,
    source TEXT                 -- manual/ast/ssot
);

-- 邊類型註冊
CREATE TABLE edge_kind_registry (
    kind TEXT PRIMARY KEY,
    display_name TEXT,
    description TEXT,
    source_kinds TEXT,          -- JSON array
    target_kinds TEXT           -- JSON array
);
```

### 5.3 資料隔離原則

| 資料層 | 同步方式 | 說明 |
|--------|----------|------|
| SSOT (Markdown) | Git | 團隊共享，版本控制 |
| Code Graph | 本地建構 | 從程式碼重建，確定性 |
| Memory | **不同步** | 個人私有，保護隱私 |

---

## 6. API 參考

### 6.1 Facade API（推薦使用）

```python
import sys, os
sys.path.insert(0, os.path.expanduser('~/.claude/skills/han-agents'))

from servers.facade import (
    # 同步
    sync,                       # 同步 Code Graph
    status,                     # 取得專案狀態

    # 三層查詢
    get_full_context,           # 取得完整 context
    format_context_for_agent,   # 格式化為 Markdown

    # 驗證
    validate_with_graph,        # Graph 增強驗證
    format_validation_report,   # 格式化驗證報告
    finish_validation,          # 完成驗證任務

    # 偏差偵測
    check_drift,                # 檢查偏差
    get_drift_context,          # 取得偏差分析資料

    # 任務完成
    finish_task,                # 標記任務完成
)
```

### 6.2 任務管理 API

```python
from servers.tasks import (
    create_task,                # 建立主任務
    create_subtask,             # 建立子任務
    get_task_progress,          # 取得進度
    update_task_status,         # 更新狀態
    get_pending_tasks,          # 取得待處理任務
    get_unvalidated_tasks,      # 取得待驗證任務
)

# 範例
task_id = create_task(
    project="my-project",
    description="實作用戶認證功能",
    priority=8
)

subtask_id = create_subtask(
    parent_id=task_id,
    description="建立 Login 元件",
    assigned_agent='executor'
)
```

### 6.3 記憶 API

```python
from servers.memory import (
    # 語義搜索
    search_memory_semantic,

    # 儲存
    store_memory,

    # 檢查點（微睡眠）
    save_checkpoint,
    load_checkpoint,
)

# 語義搜索範例
results = search_memory_semantic(
    query="認證模式",
    limit=5,
    rerank_mode='claude'        # 使用 Claude 重排序
)

# 儲存記憶
store_memory(
    category='pattern',
    title='JWT 認證流程',
    content='1. 驗證 token...',
    project='my-project',
    importance=8
)

# 檢查點
save_checkpoint(
    project='my-project',
    task_id='task-123',
    agent='pfc',
    state={'step': 3, 'decisions': [...]},
    summary='完成任務分解'
)
```

### 6.4 Code Graph API

```python
from servers.code_graph import (
    sync_from_directory,        # 從目錄同步
    get_code_nodes,             # 取得節點
    get_code_edges,             # 取得邊
    find_callers,               # 找呼叫者
    find_callees,               # 找被呼叫者
    get_file_dependencies,      # 取得檔案依賴
)

# 同步範例
sync_from_directory(
    project_dir='/path/to/project',
    project='my-project',
    incremental=True            # 增量更新
)

# 查詢範例
nodes = get_code_nodes(
    project='my-project',
    kind='function',
    name_pattern='handle*'
)
```

### 6.5 完整 API 參考

詳見 [reference/API_REFERENCE.md](reference/API_REFERENCE.md)

---

## 7. 安裝與部署

### 7.1 系統需求

- Python 3.8+
- SQLite 3.35+（FTS5 支援）
- 支援 [Agent Skills](https://agentskills.io) 標準的 AI 編碼工具

### 7.2 支援平台

| 平台 | Skills 目錄 | 範圍 |
|------|------------|------|
| **Claude Code** | `~/.claude/skills/` | 全域 |
| **Cursor** | `~/.cursor/skills/` 或 `.cursor/skills/` | 全域/專案 |
| **Windsurf** | `.windsurf/skills/` | 專案 |
| **Cline** | `~/.cline/skills/` 或 `.cline/skills/` | 全域/專案 |
| **Codex CLI** | `~/.codex/skills/` | 全域 |
| **Gemini CLI** | `.gemini/skills/` | 專案 |
| **Antigravity** | `~/.gemini/antigravity/skills/` 或 `.agent/skills/` | 全域/專案 |
| **Kiro** | Powers 系統（一鍵安裝） | - |

### 7.3 安裝步驟

#### Step 1: Clone 到對應的 Skills 目錄

**Claude Code（推薦）：**
```bash
# macOS/Linux
git clone https://github.com/Stanley-1013/han-agents.git ~/.claude/skills/han-agents

# Windows (PowerShell)
git clone https://github.com/Stanley-1013/han-agents.git "$env:USERPROFILE\.claude\skills\han-agents"
```

然後在 `~/.claude/settings.json` 新增：
```json
{
  "skills": ["~/.claude/skills/han-agents"]
}
```

**Cursor：**
```bash
# macOS/Linux (全域)
git clone https://github.com/Stanley-1013/han-agents.git ~/.cursor/skills/han-agents

# 專案層級
git clone https://github.com/Stanley-1013/han-agents.git .cursor/skills/han-agents
```

**其他平台**：請參考 [README.md](../README.md) 的完整安裝指引。

#### Step 2: 執行安裝腳本（僅 Claude Code）

```bash
# macOS/Linux
python ~/.claude/skills/han-agents/scripts/install.py --skip-prompts

# Windows
python "%USERPROFILE%\.claude\skills\han-agents\scripts\install.py" --skip-prompts
```

**安裝選項：**
- `--skip-prompts`：非互動模式（建議用於腳本）
- `--all`：執行所有可選設定步驟
- `--add-claude-md`：新增設定到專案的 CLAUDE.md
- `--init-ssot`：初始化專案 SSOT INDEX
- `--sync-graph`：同步 Code Graph

> **注意**：其他平台會自動從 skills 目錄發現技能，不需要額外設定。

### 7.4 驗證安裝

```bash
# 根據你的平台調整路徑
python ~/.claude/skills/han-agents/scripts/doctor.py

# 預期輸出
✓ Database connection OK
✓ Type registry initialized
✓ SSOT files exist
✓ Server modules loaded
✓ Code extractor available
✓ Git hooks installed
```

### 7.5 專案初始化

為目標專案建立 Skill 目錄：

**macOS/Linux：**
```bash
python ~/.claude/skills/han-agents/scripts/init_project.py my-project /path/to/project
```

**Windows：**
```cmd
python "%USERPROFILE%\.claude\skills\han-agents\scripts\init_project.py" my-project C:\path\to\project
```

這會建立 `<project>/.claude/skills/<project-name>/SKILL.md` 模板，供 LLM 填寫專案文檔。

### 7.6 功能支援對照

| 功能 | Claude Code | 其他平台 |
|------|-------------|----------|
| 記憶與語義搜索 | ✅ 完整 | ✅ 完整 |
| Code Graph 與偏差偵測 | ✅ 完整 | ✅ 完整 |
| 任務生命週期管理 | ✅ 完整 | ✅ 完整 |
| 多代理協調 | ✅ 原生（Agent tool） | ⚠️ 循序執行 |

> **注意**：Claude Code 的 Agent tool 支援真正的並行代理執行與隔離 context。其他平台可使用所有 API，但代理會在共享 context 中循序執行。

---

## 8. 開發指南

### 8.1 新增節點類型

```python
from servers.registry import register_node_kind

register_node_kind(
    kind='component',
    display_name='元件',
    description='React/Vue 元件',
    icon='🧩',
    color='#42A5F5',
    source='ast'
)
```

### 8.2 新增邊類型

```python
from servers.registry import register_edge_kind

register_edge_kind(
    kind='renders',
    display_name='渲染',
    description='元件渲染關係',
    source_kinds=['component'],
    target_kinds=['component']
)
```

### 8.3 新增語言支援

1. 修改 `tools/code_graph_extractor/extractor.py`
2. 新增副檔名映射：

```python
SUPPORTED_EXTENSIONS = {
    '.ts': 'typescript',
    '.py': 'python',
    '.go': 'go',
    '.rs': 'rust',      # 新增
}
```

3. 實作解析方法：

```python
def extract_rust(self, content: str, file_path: str) -> List[Dict]:
    # 解析 Rust AST
    pass
```

### 8.4 新增代理

1. 在 `reference/agents/` 建立 `.md` 檔案
2. 使用標準格式：

```markdown
---
name: my-agent
description: 代理描述
---

# My Agent

## 職責
...

## 輸入
...

## 輸出
...

## 工作流程
...
```

3. 更新 `scripts/install.py` 的代理列表

### 8.5 測試

```bash
# 執行所有測試
pytest tests/

# 執行特定測試
pytest tests/test_facade.py -v

# 驗證 Stories 實作
python scripts/verify_stories.py
```

---

## 9. 常見問題

### Q1: 為什麼用 SQLite 而不是中央資料庫？

**A**:
- 零配置，開箱即用
- 離線可用
- 跨專案共享
- Git 同步 SSOT，本地建構 Code Graph，避免複雜同步

### Q2: Code Graph 和 SSOT Graph 為什麼分開？

**A**: 它們代表不同的真相層：
- SSOT = 意圖（應該怎樣）
- Code Graph = 現實（實際怎樣）

分開存放可以偵測「實作偏差」。

### Q3: 記憶會同步嗎？

**A**: 不會。`long_term_memory` 和 `working_memory` 是個人私有的：
- 保護隱私
- 避免衝突
- 簡化架構

### Q4: 如何處理大型專案？

**A**:
1. 使用增量更新（`incremental=True`）
2. 按模組拆分 SKILL.md
3. 定期執行 `sync` 而非每次全量

### Q5: Drift 偵測的準確度？

**A**: Drift 偵測分兩層：
1. **腳本層**（`servers/drift.py`）：提供結構化資料
2. **代理層**（Drift Detector）：做語義分析

語義分析的準確度取決於 LLM 能力。

---

## 附錄

### A. 設計決策記錄 (ADR)

| ID | 決策 | 理由 |
|----|------|------|
| ADR-001 | 記憶不同步 | 保護隱私、避免複雜合併邏輯 |
| ADR-002 | Code Graph 本地建構 | 確定性、無需共享基礎設施 |
| ADR-003 | SSOT 用 Git 同步 | 成熟方案、版本追溯、PR 審核 |
| ADR-004 | SQLite 而非中央 DB | 零配置、離線可用、跨專案共享 |
| ADR-005 | SKILL.md 動態分段 | 不硬編碼目錄分類，由 Heading 組織 |
| ADR-006 | Agent vs Script 分離 | 腳本提供資料、Agent 做語義判斷 |

### B. 版本歷史

| 版本 | 日期 | 說明 |
|------|------|------|
| 3.0.0 | 2026-01 | Skill 架構，SKILL.md 動態分段 |
| 2.1.0 | 2025-12 | 三層查詢、增強驗證、Drift 偵測 |
| 2.0.0 | 2025-12 | 基礎架構、Code Graph、Agents |
| 1.0.0 | 2025-11 | 初版：Attention Tree 概念驗證 |

### C. 相關文檔索引

| 文檔 | 路徑 | 說明 |
|------|------|------|
| 快速開始 | `README.md` | 安裝和基本使用 |
| Skill 定義 | `SKILL.md` | API 快速參考 |
| 架構設計 | `reference/ARCHITECTURE.md` | 深度架構說明 |
| API 參考 | `reference/API_REFERENCE.md` | 完整 API 文檔 |
| 工作流程 | `reference/WORKFLOW_GUIDE.md` | 詳細工作流 |
| 記憶操作 | `reference/MEMORY_GUIDE.md` | 記憶系統指南 |
| Graph 操作 | `reference/GRAPH_GUIDE.md` | Graph 使用指南 |
| 問題排解 | `reference/TROUBLESHOOTING.md` | 常見問題 |

### D. 聯絡資訊

- **專案維護者**：[維護者姓名]
- **內部文檔**：[內部 Wiki 連結]
- **問題回報**：[Issue Tracker 連結]

---

*本文檔最後更新於 2026-01-16*
