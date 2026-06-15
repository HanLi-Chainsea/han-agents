[English](README.md) | **繁體中文**

# HAN-Agents

**HAN** = **H**ierarchical **A**pproached **N**euromorphic Agents

一套三層架構的多 Agent 任務系統：**Intent**（意圖；doc-grounded `intent-manifest.json`，回退 legacy Skill）+ **Code Graph**（現實）+ **Memory**（經驗）。設計靈感源自神經科學，讓 PFC 協調多個專屬 Agent，透過自動化派遣迴圈完成任務。

核心 Python 能力可在多數相容 [Agent Skills](https://agentskills.io) 的 AI 編程工具中使用；Claude Code 目前具備最完整的 Task tool 與 hook 整合。支援平台包含 Claude Code、Cursor、Windsurf、Cline、Codex CLI、Gemini CLI、Antigravity 以及 Kiro。

## 特色功能

- **自動派遣迴圈** — `get_next_dispatch()` 自動協調 Executor → Critic → Memory 整條流水線
- **Recipe 系統** — 內建工作流程（如 `recipe_unit_tests()`），一鍵產生完整任務樹
- **任務生命週期** — 仿 Jira 階層（Epic → Story → Task → Bug），涵蓋建立、執行、驗證與文件化
- **Code Graph** — Tree-sitter AST 搭配 regex 備援，支援呼叫圖與方法萃取（8 種語言）
- **Drift Detection** — 比對 Skill 定義與實際程式碼，自動偵測落差
- **語義記憶** — FTS5 全文檢索，支援 Claude rerank prompt 與 optional 本地 embedding 重排
- **Harness Tracing & Evals** — 本地 trace/span 儲存、deterministic trajectory evals、JSONL / OTel-style 匯出
- **Guardrails & Review Queue** — 指令/路徑政策檢查、PreToolUse 阻擋、人工 review queue
- **Schema Migrations** — 版本化 SQLite migration 與 doctor 檢查
- **自動技術堆疊偵測** — `ensure_project()` 自動識別語言、框架與測試工具
- **Micro-Nap 檢查點** — 跨對話儲存與恢復長時間執行的任務
- **Zero-Config** — Clone 完即可用，資料庫首次使用時自動建立

## 安裝

### 第一步：Clone

將專案 Clone 到你的 AI 工具的 skills 目錄：

<details>
<summary><b>Claude Code</b></summary>

```bash
# 全域安裝（推薦）
git clone https://github.com/Stanley-1013/han-agents.git ~/.claude/skills/han-agents

# 專案層級安裝
git clone https://github.com/Stanley-1013/han-agents.git .claude/skills/han-agents
```

Windows（PowerShell）：將 `~` 替換為 `$env:USERPROFILE`

</details>

<details>
<summary><b>Cursor</b></summary>

```bash
# 全域安裝
git clone https://github.com/Stanley-1013/han-agents.git ~/.cursor/skills/han-agents

# 專案層級安裝
git clone https://github.com/Stanley-1013/han-agents.git .cursor/skills/han-agents
```

> 若使用 Cline-based Cursor，需在設定中啟用 Skills：Settings → Features → Enable Skills

</details>

<details>
<summary><b>Windsurf / Cline / Codex CLI / Gemini CLI / Antigravity</b></summary>

將 Clone 目標替換成對應平台的 skills 目錄：

| 平台 | 目錄 |
|------|------|
| Windsurf | `.windsurf/skills/han-agents` |
| Cline | `~/.cline/skills/han-agents` |
| Codex CLI | `~/.codex/skills/han-agents` |
| Gemini CLI | `.gemini/skills/han-agents` |
| Antigravity | `~/.antigravity/skills/han-agents` |

</details>

<details>
<summary><b>Kiro (AWS)</b></summary>

使用 **Powers** 系統 — 在 Kiro 的 Powers 面板中搜尋「han-agents」，或直接貼上 GitHub URL 安裝。

</details>

### 第二步：直接使用

就這樣，不需要執行任何安裝腳本。首次使用時，han-agents 會自動完成以下事項：
- 建立資料庫（`brain/brain.db`）
- 將 Agent 定義複製到對應平台的 agents 目錄
- 註冊 hooks（僅限 Claude Code）
- 按需安裝 tree-sitter grammars（用於增強程式碼萃取）

若在 CI 或離線環境下使用，可設定 `HAN_NO_INSTALL=1` 停用自動安裝。

<details>
<summary><b>選用：手動安裝 / 進階設定</b></summary>

```bash
cd <path-to-han-agents>
python scripts/install.py --skip-prompts
```

| 參數 | 說明 |
|------|------|
| `--skip-prompts` | 非互動式模式（適合自動化環境） |
| `--all` | 執行所有可選安裝步驟 |
| `--add-claude-md` | 將設定加入專案的 CLAUDE.md |
| `--init-ssot` | 初始化專案 SSOT INDEX |
| `--sync-graph` | 同步 Code Graph |
| `--reset` | 重置資料庫 |

預先安裝 tree-sitter grammars（可選，首次使用時也會自動安裝）：
```bash
pip install -r requirements-ast.txt
```

</details>

### 驗證安裝（可選）

```bash
python scripts/doctor.py
python cli/main.py eval
```

## 快速開始

```python
import sys, os
from servers import HAN_BASE_DIR
sys.path.insert(0, HAN_BASE_DIR)

from servers.facade import sync, status, check_drift, get_next_dispatch
from servers.tasks import create_task, create_subtask, get_task_progress
from servers.recipes import recipe_unit_tests, run_recipe
from servers.memory import search_memory_semantic, store_memory
from servers.project import ensure_project
from servers.tracing import start_trace, finish_trace
from servers.evals import evaluate_trajectory, evaluate_trace, extract_agents_from_trace
```

### 初始化專案

```python
result = ensure_project('my-project', '/path/to/project')
# result['tech_stack'] → {'primary_language': 'python', 'test_tool': 'pytest', ...}
```

或透過 CLI：

```bash
python cli/main.py init my-project /path/to/project
```

### Recipe + 派遣迴圈

一個指令產生任務樹，自動派遣 Agent 執行：

```python
# 1. Recipe 自動分析專案，建立任務樹
result = recipe_unit_tests('my-project', '/path/to/project')
trace_id = start_trace('unit_tests recipe', project='my-project')

# 2. 派遣迴圈 — 持續執行直到完成
while True:
    inst = get_next_dispatch(result['epic_id'], 'my-project', '/path/to/project', trace_id=trace_id)
    if inst['action'] != 'dispatch':
        break
    # Claude Code：透過 Task tool 派遣
    Task(subagent_type=inst['subagent_type'], prompt=inst['prompt'])

trace = finish_trace(trace_id)
```

`get_next_dispatch()` 自動處理以下所有事項：
- Executor → Critic 驗證迴圈（冪等，不會重複派遣 Critic）
- 被退回的任務 → 附帶回饋 context 重新執行
- 全部完成 → Memory Agent 儲存經驗教訓
- 回傳 `model_tier`，供各平台選擇對應模型
- 傳入 `trace_id` 時，會記錄已遮蔽 prompt 的 dispatch/status spans，供 replay 與 eval 使用

## 架構

```
┌──────────────────────────────────────────────────────────────┐
│                        HAN System                            │
├───────────────┬───────────────┬───────────────┬──────────────┤
│  Skill Layer  │  Code Graph   │    Memory     │    Tasks     │
│   (Intent)    │  (Reality)    │ (Experience)  │ (Execution)  │
├───────────────┼───────────────┼───────────────┼──────────────┤
│  SKILL.md     │  code_nodes   │ long_term_    │   tasks      │
│  flows/*.md   │  code_edges   │   memory      │ checkpoints  │
│  domains/*    │  file_hashes  │ working_      │ agent_logs   │
│               │               │   memory      │              │
└───────────────┴───────────────┴───────────────┴──────────────┘
```

## Agent 角色

| Agent | 類型 | 模型等級 | 職責 |
|-------|------|----------|------|
| PFC | `pfc` | `planner` | 規劃與任務拆解 |
| Executor | `executor` | `worker` | 執行任務 |
| Critic | `critic` | `worker` | 驗證與品質審查 |
| Memory | `memory` | `fast` | 知識儲存與檢索 |
| Researcher | `researcher` | `worker` | 資訊蒐集 |
| Drift Detector | `drift-detector` | `fast` | 偵測 Skill 與程式碼的落差 |

> **模型等級**代表語意能力層級：`planner`（最強推理）、`worker`（均衡）、`fast`（低成本）。各平台會將等級對應到自己的模型。

## API 參考

### Facade（統一入口）

```python
from servers.facade import (
    sync,              # 同步 Code Graph
    status,            # 查看專案狀態
    check_drift,       # 偵測 Skill 與程式碼的落差
    get_full_context,  # 取得三層 context
    finish_task,       # 完成任務生命週期
    get_next_dispatch, # 自動派遣下一個 Agent
)
```

### Recipes

```python
from servers.recipes import (
    recipe_unit_tests, # 自動產生單元測試任務樹
    run_recipe,        # 依名稱執行 recipe
)
```

### Tasks

```python
from servers.tasks import (
    create_task,           # 建立任務（epic/story/task/bug）
    create_subtask,        # 建立子任務（自動繼承階層）
    reserve_critic_task,   # 原子性 Critic 預約（冪等）
    get_task_progress,     # 取得完成進度統計
    get_epic_tasks,        # 取得 Epic 及巢狀 Story
    get_story_tasks,       # 取得 Story 下的任務
    get_hierarchy_summary, # 各層級數量統計 {epics, stories, tasks, bugs}
)
```

### Project

```python
from servers.project import (
    ensure_project,    # 自動初始化：同步 Code Graph + 偵測技術堆疊 + 存入資料庫
)
```

### Code Graph

```python
from servers.code_graph import (
    sync_from_directory,        # 將目錄同步至 Code Graph
    get_class_dependencies_bfs, # BFS 依賴關係遍歷
    get_file_structure,         # 取得檔案的程式碼結構
)
```

支援語言：

| 語言 | 副檔名 | 解析後端 | 呼叫圖 | 方法萃取 |
|------|--------|----------|--------|----------|
| TypeScript | `.ts`, `.tsx` | Tree-sitter（regex 備援） | 是 | 是 |
| JavaScript | `.js`, `.jsx` | Tree-sitter（regex 備援） | 是 | 是 |
| Python | `.py` | Tree-sitter（regex 備援） | 是 | 是 |
| Java | `.java` | Tree-sitter（regex 備援） | 是 | 是 |
| Rust | `.rs` | Tree-sitter（regex 備援） | 是 | 是 |
| Go | `.go` | 僅 Tree-sitter | 是 | 是 |
| C | `.c`, `.h` | 僅 Tree-sitter | 是 | — |
| C++ | `.cpp`, `.cc`, `.cxx`, `.hpp`, `.hh`, `.hxx` | 僅 Tree-sitter | 是 | 是 |

安裝 Tree-sitter 以獲得更完整的萃取能力（可選，TS/JS/Python/Java/Rust 有 regex 備援）：
```bash
pip install tree-sitter tree-sitter-python tree-sitter-javascript tree-sitter-typescript tree-sitter-java tree-sitter-rust tree-sitter-go tree-sitter-c tree-sitter-cpp
```

### Memory

```python
from servers.memory import (
    search_memory_semantic,  # 語義搜尋（支援重排）
    store_memory,            # 儲存至長期記憶
    save_checkpoint,         # 儲存 Micro-Nap 檢查點
    load_checkpoint,         # 從檢查點恢復
)
```

> `search_memory_semantic(..., rerank_mode='claude')` 會回傳候選記憶與 rerank prompt，由呼叫方交給模型排序；`rerank_mode='embedding'` 需要額外安裝 `sentence-transformers` 與 `numpy`。

### Harness Trace & Eval

```python
from servers.tracing import (
    start_trace, start_span, finish_span, finish_trace,
    get_trace, summarize_trace, export_traces_jsonl, export_traces_otel_jsonl,
)
from servers.evals import (
    evaluate_trajectory, evaluate_trace, evaluate_trace_jsonl,
    extract_agents_from_trace,
)
```

### Guardrails / Reviews / Migrations

```python
from servers.guardrails import get_agent_policy, check_command, check_path, enforce_result
from servers.reviews import create_review_item, list_review_items, resolve_review_item
from servers.migrations import apply_pending_migrations, get_migration_history
```

## CLI 指令

```bash
python cli/main.py <command>
```

| 指令 | 說明 |
|------|------|
| `doctor` | 診斷系統狀態 |
| `sync` | 從原始碼同步 Code Graph |
| `status` | 顯示專案狀態概覽 |
| `init` | 初始化專案 |
| `drift` | 檢查 SSOT 與程式碼的落差 |
| `install-hooks` | 安裝 Git hooks 以自動同步 |
| `eval` | 執行 deterministic harness trajectory evals |
| `traces` | 檢視本地 traces、guardrail events，或匯出 JSONL |
| `reviews` | 檢視與處理人工 review queue |
| `migrate` | 套用版本化 schema migrations |
| `guard` | 檢查 Agent 指令/路徑 guardrail policy |
| `ssot-sync` | 將 SSOT Index 同步至 Graph |
| `graph` | 查詢與瀏覽 SSOT Graph |
| `dashboard` | 顯示系統完整儀表板 |

常用範例：

```bash
python cli/main.py eval
python cli/main.py eval --trace trace_abc --expected executor,critic,memory
python cli/main.py traces
python cli/main.py traces --guardrails
python cli/main.py traces --export-jsonl /tmp/han-traces.jsonl
python cli/main.py traces --export-otel-jsonl /tmp/han-otel.jsonl
python cli/main.py guard --agent executor --command "rm -rf /tmp/project"
python cli/main.py guard --agent executor --mode warn --command "rm -rf /tmp/project"
python cli/main.py reviews
python cli/main.py reviews --enqueue-trace trace_abc
python cli/main.py reviews --resolve review_abc --reviewer alice --resolution approved
python cli/main.py migrate --history
```

## 腳本

```bash
cd <path-to-han-agents>

python scripts/install.py --skip-prompts  # 安裝 / 更新
python scripts/doctor.py                  # 驗證安裝
python scripts/sync.py /path/to/project   # 同步 Code Graph
python scripts/init_project.py my-project /path/to/project  # 初始化專案
```

## 平台相容性

| 平台 | Skills 目錄 | 範圍 |
|------|-------------|------|
| [Claude Code](https://claude.ai/code) | `~/.claude/skills/` 或 `.claude/skills/` | 全域 / 專案 |
| [Cursor](https://cursor.com) | `~/.cursor/skills/` 或 `.cursor/skills/` | 全域 / 專案 |
| [Windsurf](https://windsurf.com) | `.windsurf/skills/` | 專案 |
| [Cline](https://cline.bot) | `~/.cline/skills/` 或 `.cline/skills/` | 全域 / 專案 |
| [Codex CLI](https://developers.openai.com/codex) | `~/.codex/skills/` 或 `.codex/skills/` | 全域 / 專案 |
| [Gemini CLI](https://github.com/google-gemini/gemini-cli) | `.gemini/skills/` | 專案 |
| [Antigravity](https://antigravity.google) | `~/.antigravity/skills/` 或 `.antigravity/skills/` | 全域 / 專案 |
| [Kiro](https://kiro.dev) | Powers 系統（一鍵安裝） | — |

| 功能 | Claude Code | 其他平台 |
|------|-------------|----------|
| 記憶與語義搜尋 | ✅ 完整支援 | ✅ 完整支援 |
| Code Graph 與 Drift Detection | ✅ 完整支援 | ✅ 完整支援 |
| 任務生命週期管理 | ✅ 完整支援 | ✅ 完整支援 |
| Harness traces / evals / reviews | ✅ 完整支援 | ✅ 完整支援 |
| Guardrail CLI | ✅ 完整支援 | ✅ 完整支援 |
| PreToolUse / PostToolUse hooks | ✅ 原生支援 | ⚠️ 依平台能力而定 |
| 多 Agent 協作 | ✅ 原生支援（Task tool） | ⚠️ 通常需循序執行 |

> Claude Code 的 Task tool 支援在獨立 context 中執行多個 Agent，並可透過 PreToolUse/PostToolUse hooks 做 lifecycle automation 與 guardrail enforcement。其他平台可使用 HAN 的 Python/CLI 核心能力，但 hook 與多 Agent 派發深度依平台而定。

## 文件

- [SKILL.md](SKILL.md) — 主要 Skill 定義
- [reference/API_REFERENCE.md](reference/API_REFERENCE.md) — 完整 API 文件
- [reference/WORKFLOW_GUIDE.md](reference/WORKFLOW_GUIDE.md) — 工作流程模式指南
- [reference/GRAPH_GUIDE.md](reference/GRAPH_GUIDE.md) — Graph 操作指南
- [reference/TROUBLESHOOTING.md](reference/TROUBLESHOOTING.md) — 常見問題排除
- [docs/HARNESS_ROADMAP.md](docs/HARNESS_ROADMAP.md) — Trace / eval / guardrail 成熟度 roadmap
- [docs/QUICKSTART_HANDOVER.md](docs/QUICKSTART_HANDOVER.md) — 快速上手移交指南
- [docs/WHITEPAPER.md](docs/WHITEPAPER.md) — 架構白皮書

## 資料庫

SQLite 資料庫位於 `<han-agents>/brain/brain.db`（首次使用時自動建立）。

- Schema 定義：[brain/schema.sql](brain/schema.sql)
- 範例資料庫：`brain/brain.example.db` — 供測試用的參考資料庫

## 系統需求

- Python 3.8+
- SQLite 3.9+（需支援 FTS5）

## 授權

MIT
