# HAN-Agents 快速交接指南

> **閱讀時間**：15 分鐘
> **適用對象**：接手工程師

---

## 一句話說明

HAN-Agents 是一套 **AI 多代理任務協調系統**，讓支援 [Agent Skills](https://agentskills.io) 標準的 AI 編碼工具能夠處理複雜的多步驟開發任務。

**支援平台**：Claude Code、Cursor、Windsurf、Cline、Codex CLI、Gemini CLI、Antigravity、Kiro

---

## 核心概念（3 分鐘）

### 三層架構

```
SSOT Layer   → 文檔說「應該怎樣」  → SKILL.md
Code Graph   → 程式碼「實際怎樣」  → AST 解析
Memory Layer → AI「記住怎樣」     → 經驗累積
```

### 六個代理

| 代理 | 一句話職責 |
|------|-----------|
| **PFC** | 規劃任務、分解步驟 |
| **Executor** | 執行具體任務 |
| **Critic** | 驗證品質 |
| **Memory** | 儲存/檢索知識 |
| **Researcher** | 收集資訊 |
| **Drift Detector** | 偵測文檔與程式碼不一致 |

### 工作流程

```
PFC (規劃) → Executor (執行) → Critic (驗證) → Memory (記錄)
```

---

## 關鍵檔案（2 分鐘）

```
~/.claude/skills/han-agents/
├── SKILL.md                 # ⭐ 入口，先看這個
├── servers/facade.py        # ⭐ 主要 API，所有操作從這裡
├── servers/tracing.py       # Harness traces / spans / exports
├── servers/evals.py         # Deterministic trajectory evals
├── servers/guardrails.py    # Agent command/path policy
├── servers/reviews.py       # Human review queue
├── hooks/pre_tool.py        # PreToolUse guardrail blocking
├── hooks/post_task.py       # Task lifecycle + post-tool observation
├── migrations/              # Numbered schema migrations
├── brain/brain.db           # SQLite 資料庫
├── docs/HARNESS_ROADMAP.md  # Harness maturity state and next steps
└── scripts/install.py       # 安裝腳本
```

---

## 快速上手（5 分鐘）

### 0. 安裝

**Claude Code（推薦）：**
```bash
# macOS/Linux
git clone https://github.com/Stanley-1013/han-agents.git ~/.claude/skills/han-agents
python ~/.claude/skills/han-agents/scripts/install.py --skip-prompts

# Windows (PowerShell)
git clone https://github.com/Stanley-1013/han-agents.git "$env:USERPROFILE\.claude\skills\han-agents"
python "$env:USERPROFILE\.claude\skills\han-agents\scripts\install.py" --skip-prompts
```

然後在 `~/.claude/settings.json` 新增：
```json
{ "skills": ["~/.claude/skills/han-agents"] }
```

**其他平台**：Clone 到對應的 skills 目錄即可自動發現，詳見 [README.md](../README.md)。

### 1. 驗證安裝

```bash
# 根據你的平台調整路徑
python ~/.claude/skills/han-agents/scripts/doctor.py
python ~/.claude/skills/han-agents/cli/main.py migrate --history
python ~/.claude/skills/han-agents/cli/main.py eval
```

上線前最小檢查：

```bash
pytest -q
python scripts/doctor.py
python cli/main.py eval
python cli/main.py guard --agent executor --command "pytest -q"
python cli/main.py traces --export-jsonl /tmp/han-traces.jsonl
python cli/main.py traces --export-otel-jsonl /tmp/han-otel.jsonl
```

### 2. 常用 API

```python
# 初始化
import sys, os
sys.path.insert(0, os.path.expanduser('~/.claude/skills/han-agents'))

from servers.facade import sync, get_full_context, check_drift
from servers.tasks import create_task, get_task_progress
from servers.memory import search_memory_semantic, store_memory
from servers.tracing import start_trace, finish_trace
from servers.evals import evaluate_trace
from servers.reviews import list_review_items

# 同步 Code Graph
sync('/path/to/project', 'project-name')

# 取得三層 context
context = get_full_context({'flow_id': 'auth'}, '/path/to/project', 'project-name')

# 建立任務
task_id = create_task(project='my-project', description='實作功能 X', priority=8)

# 搜索記憶
results = search_memory_semantic('認證模式', limit=5)
```

### 3. 代理派發

```python
# 透過 Claude Code Agent tool
Agent(
    subagent_type='pfc',      # 或 executor, critic, memory, researcher
    prompt='任務描述...'
)
```

### 4. Harness 工作流範例

```python
from servers.facade import get_next_dispatch
from servers.tracing import start_trace, finish_trace
from servers.evals import evaluate_trace

trace_id = start_trace('unit_tests', project='my-project')

while True:
    inst = get_next_dispatch(epic_id, 'my-project', '/path/to/project', trace_id=trace_id)
    if inst['action'] != 'dispatch':
        break
    Agent(subagent_type=inst['subagent_type'], prompt=inst['prompt'])

finish_trace(trace_id)
result = evaluate_trace(trace_id, ['executor', 'critic', 'memory'], mode='subsequence')
```

---

## 資料庫表格速查（2 分鐘）

| 表 | 用途 |
|---|------|
| `tasks` | 任務記錄 |
| `code_nodes` | 程式碼節點（類別、函式等） |
| `code_edges` | 程式碼關係（呼叫、繼承等） |
| `project_nodes` | SKILL.md 解析的節點 |
| `long_term_memory` | 長期記憶 |
| `working_memory` | 工作記憶 |
| `agent_traces` | Workflow trace |
| `agent_spans` | Dispatch/tool/guardrail spans |
| `human_review_queue` | 需要人工處理的 blocked/failed/warning 項目 |
| `schema_migrations` | 已套用的 schema 版本 |

---

## 常見操作（3 分鐘）

### 同步專案 Code Graph

```bash
python ~/.claude/skills/han-agents/scripts/sync.py /path/to/project
```

### 初始化新專案

```bash
# macOS/Linux
python ~/.claude/skills/han-agents/scripts/init_project.py my-app ~/projects/my-app

# Windows
python "%USERPROFILE%\.claude\skills\han-agents\scripts\init_project.py" my-app C:\projects\my-app
```

### 檢查偏差

```python
from servers.facade import check_drift
report = check_drift('/path/to/project', 'project-name', 'module-name')
if report['has_drift']:
    for d in report['drifts']:
        print(f"[{d['type']}] {d['description']}")
```

### 查看任務進度

```python
from servers.tasks import get_task_progress
progress = get_task_progress(project='my-project')
```

### 查看 harness traces

```bash
python cli/main.py traces
python cli/main.py traces trace_abc
python cli/main.py traces --guardrails
python cli/main.py eval --trace trace_abc --expected executor,critic,memory
```

### 處理人工 review queue

```bash
python cli/main.py reviews
python cli/main.py reviews --show review_abc
python cli/main.py reviews --resolve review_abc --reviewer alice --resolution approved --notes "Looks good"
python cli/main.py reviews --enqueue-trace trace_abc
```

### 套用 migration

```bash
python cli/main.py migrate --history
```

新增 schema 時：

1. 在 `migrations/` 新增下一個編號 SQL。
2. 更新 `servers/migrations.py` 的 `CURRENT_SCHEMA_VERSION` 與 `MIGRATIONS`。
3. 同步更新 `brain/schema.sql` 與 `assets/schema.sql`，讓新安裝不用重放歷史才能完整。
4. 加上 `tests/test_migrations.py` 或相關 API 測試。

---

## 遇到問題？

1. **先跑診斷**：`python scripts/doctor.py`
2. **跑 harness eval**：`python cli/main.py eval`
3. **查 review queue**：`python cli/main.py reviews`
4. **查文檔**：`reference/TROUBLESHOOTING.md`
5. **看 roadmap**：`docs/HARNESS_ROADMAP.md`

---

## 深入閱讀

| 想了解... | 看這份文檔 |
|----------|-----------|
| 完整架構 | `reference/ARCHITECTURE.md` |
| 所有 API | `reference/API_REFERENCE.md` |
| 代理定義 | `reference/agents/*.md` |
| 記憶操作 | `reference/MEMORY_GUIDE.md` |
| Harness 狀態 | `docs/HARNESS_ROADMAP.md` |
| 完整白皮書 | `docs/WHITEPAPER.md` |

---

*有問題歡迎詢問！*
