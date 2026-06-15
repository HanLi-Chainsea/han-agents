**English** | [繁體中文](README.zh-TW.md)

# HAN-Agents

**H**ierarchical **A**pproached **N**euromorphic Agents — a brain-inspired multi-agent task system for AI coding assistants.

![Python 3.8+](https://img.shields.io/badge/Python-3.8%2B-blue?logo=python&logoColor=white)
![SQLite FTS5](https://img.shields.io/badge/SQLite-FTS5-lightgrey?logo=sqlite)
![License: MIT](https://img.shields.io/badge/License-MIT-green)
![Platforms](https://img.shields.io/badge/Platforms-Claude%20%7C%20Cursor%20%7C%20Windsurf%20%7C%206%20more-purple)

---

> **Clone once. Start immediately. No configuration required.**
>
> HAN auto-creates its database, registers hooks, and installs grammars the first time you use it.

---

## What Is HAN?

HAN is a task orchestration layer that sits inside your AI coding agent. It gives your agent persistent memory, a live code map, and a structured workflow — so complex, multi-step projects don't fall apart between conversations.

Three layers work together:

```
┌──────────────────────────────────────────────────────────────────┐
│                          HAN System                              │
├─────────────────┬─────────────────┬─────────────────┬───────────┤
│   Skill Layer   │   Code Graph    │     Memory      │   Tasks   │
│    (Intent)     │   (Reality)     │  (Experience)   │(Execution)│
├─────────────────┼─────────────────┼─────────────────┼───────────┤
│  SKILL.md       │  code_nodes     │ long_term_      │  tasks    │
│  flows/*.md     │  code_edges     │   memory        │ checkpts  │
│  domains/*      │  file_hashes    │ working_        │ agent_    │
│                 │                 │   memory        │   logs    │
└─────────────────┴─────────────────┴─────────────────┴───────────┘
```

- **Skill Layer** — intent definitions that guide agent behavior
- **Code Graph** — live AST snapshot of your codebase (8 languages)
- **Memory** — semantic search over past lessons; resumable checkpoints
- **Tasks** — Jira-like hierarchy (Epic → Story → Task → Bug) with automated dispatch

---

## Why HAN?

| Challenge | What HAN does |
|-----------|---------------|
| Long tasks lose context between conversations | Micro-Nap checkpoints save and restore mid-task state |
| Agents repeat the same mistakes | Semantic memory surfaces relevant lessons before each run |
| Hard to know if code matches the design | Drift detection compares Skill definitions against actual code |
| Complex workflows require manual hand-holding | Automated dispatch loop runs Executor → Critic → Memory without intervention |
| Setting up a new project takes too much boilerplate | `ensure_project()` auto-detects languages, frameworks, and test tools |

---

## Features

- **Zero-Config Start** — database and hooks auto-initialize on first use
- **Automated Dispatch Loop** — `get_next_dispatch()` orchestrates Executor → Critic → Memory pipeline end-to-end
- **Recipe System** — pre-built workflows (e.g. `recipe_unit_tests()`) that generate full task trees from a single call
- **Code Graph** — Tree-sitter AST with regex fallback; call graph and method extraction across 8 languages
- **Drift Detection** — compare Skill definitions against actual code implementation
- **Semantic Memory** — FTS5 + embedding search with LLM reranking
- **Task Lifecycle** — full hierarchy with create, execute, validate, and document phases
- **Harness Tracing & Evals** — local trace/span storage plus deterministic trajectory scoring
- **Auto Tech Stack Detection** — `ensure_project()` detects languages, frameworks, and test tools
- **Micro-Nap Checkpoints** — save and resume long-running tasks across conversations

---

## Installation

### Step 1: Clone

Choose your platform and clone to its skills directory:

<details>
<summary><b>Claude Code</b></summary>

```bash
# Global (recommended)
git clone https://github.com/Stanley-1013/han-agents.git ~/.claude/skills/han-agents

# Project-level
git clone https://github.com/Stanley-1013/han-agents.git .claude/skills/han-agents
```

Windows (PowerShell): replace `~` with `$env:USERPROFILE`

</details>

<details>
<summary><b>Cursor</b></summary>

```bash
# Global
git clone https://github.com/Stanley-1013/han-agents.git ~/.cursor/skills/han-agents

# Project-level
git clone https://github.com/Stanley-1013/han-agents.git .cursor/skills/han-agents
```

> Enable Skills in Cline-based Cursor: Settings → Features → Enable Skills

</details>

<details>
<summary><b>Windsurf / Cline / Codex CLI / Gemini CLI / Antigravity</b></summary>

Replace the target path with your platform's skills directory:

| Platform | Directory |
|----------|-----------|
| Windsurf | `.windsurf/skills/han-agents` |
| Cline | `~/.cline/skills/han-agents` |
| Codex CLI | `~/.codex/skills/han-agents` |
| Gemini CLI | `.gemini/skills/han-agents` |
| Antigravity | `~/.antigravity/skills/han-agents` |

</details>

<details>
<summary><b>Kiro (AWS)</b></summary>

Uses the **Powers** system — search for "han-agents" in Kiro's Powers panel or install from the GitHub URL.

</details>

### Step 2: Just Start

That's it. No install script, no config file. On first use, HAN automatically:

- Creates the database (`brain/brain.db`)
- Copies agent definitions to your platform's agents directory
- Registers hooks (Claude Code only)
- Installs Tree-sitter grammars on demand

> Set `HAN_NO_INSTALL=1` to disable auto-initialization (useful in CI or air-gapped environments).

<details>
<summary><b>Optional: manual install / advanced setup</b></summary>

```bash
cd <path-to-han-agents>
python scripts/install.py --skip-prompts
```

| Flag | Description |
|------|-------------|
| `--skip-prompts` | Non-interactive mode |
| `--all` | Run all optional setup steps |
| `--add-claude-md` | Add config to project's CLAUDE.md |
| `--init-ssot` | Initialize project SSOT INDEX |
| `--sync-graph` | Sync Code Graph |
| `--reset` | Reset database |

Pre-install Tree-sitter grammars (optional — auto-installed on first use):

```bash
pip install -r requirements-ast.txt
```

</details>

### Verify Installation

```bash
python scripts/doctor.py
python cli/main.py eval
```

---

## Quick Start

### Import

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

### Initialize a Project

```python
result = ensure_project('my-project', '/path/to/project')
# result['tech_stack'] → {'primary_language': 'python', 'test_tool': 'pytest', ...}
```

Or via CLI:

```bash
python cli/main.py init my-project /path/to/project
```

### Recipe + Dispatch Loop

One call to generate a full task tree, then a loop to run it:

```python
# 1. Recipe auto-analyzes the project and builds the task tree
result = recipe_unit_tests('my-project', '/path/to/project')
trace_id = start_trace('unit_tests recipe', project='my-project')

# 2. Dispatch loop — repeat until all tasks are done
while True:
    inst = get_next_dispatch(result['epic_id'], 'my-project', '/path/to/project', trace_id=trace_id)
    if inst['action'] != 'dispatch':
        break
    # Claude Code: dispatch via Agent tool
    Agent(subagent_type=inst['subagent_type'], prompt=inst['prompt'])

trace = finish_trace(trace_id)
```

`get_next_dispatch()` handles the full lifecycle automatically:

- Executor → Critic validation (idempotent, no duplicate critics)
- Rejected tasks → retry with feedback context
- All done → Memory agent stores lessons learned
- Returns `model_tier` for platform-specific model selection
- With `trace_id`, records redacted dispatch/status spans for replay and evals

---

## Agents

| Agent | Type | Model Tier | Role |
|-------|------|------------|------|
| PFC | `pfc` | `planner` | Planning and task decomposition |
| Executor | `executor` | `worker` | Task execution |
| Critic | `critic` | `worker` | Validation and quality checks |
| Memory | `memory` | `fast` | Knowledge storage and retrieval |
| Researcher | `researcher` | `worker` | Information gathering |
| Drift Detector | `drift-detector` | `fast` | Skill-Code drift detection |

> **Model Tier** maps to semantic capability levels: `planner` (strongest reasoning), `worker` (balanced), `fast` (low-cost). Each platform maps these tiers to its own models.

---

## API Reference

### Facade — Unified Entry Point

The main interface for most workflows:

```python
from servers.facade import (
    sync,              # Sync Code Graph from source files
    status,            # Project status overview
    check_drift,       # Detect Skill vs Code divergence
    get_full_context,  # Retrieve all three-layer context
    finish_task,       # Complete task lifecycle
    get_next_dispatch, # Auto-dispatch next agent in pipeline
)
```

### Recipes

Pre-built workflows that generate full task trees:

```python
from servers.recipes import (
    recipe_unit_tests, # Auto-generate unit test task tree
    run_recipe,        # Run recipe by name
)
```

### Tasks

Full Jira-like task hierarchy management:

```python
from servers.tasks import (
    create_task,           # Create task (epic / story / task / bug)
    create_subtask,        # Create child task (auto-inherits hierarchy)
    reserve_critic_task,   # Atomic critic reservation (idempotent)
    get_task_progress,     # Completion statistics
    get_epic_tasks,        # Epics with nested stories
    get_story_tasks,       # Tasks under a story
    get_hierarchy_summary, # Count by level: {epics, stories, tasks, bugs}
)
```

### Project

```python
from servers.project import (
    ensure_project,    # Auto-init: sync Code Graph + detect tech stack + store to DB
)
```

### Code Graph

Live AST snapshot and traversal:

```python
from servers.code_graph import (
    sync_from_directory,        # Sync a folder into the Code Graph
    get_class_dependencies_bfs, # BFS dependency traversal
    get_file_structure,         # Code structure of a single file
)
```

### Memory

Persistent knowledge across conversations:

```python
from servers.memory import (
    search_memory_semantic,  # Semantic search with LLM reranking
    store_memory,            # Store to long-term memory
    save_checkpoint,         # Create a Micro-Nap checkpoint
    load_checkpoint,         # Resume from checkpoint
)
```

---

## Code Graph: Supported Languages

Tree-sitter provides full AST accuracy. A regex fallback is available for the languages marked below — HAN installs grammars automatically on first use.

| Language | Extensions | Backend | Call Graph | Methods |
|----------|------------|---------|:----------:|:-------:|
| TypeScript | `.ts`, `.tsx` | Tree-sitter (regex fallback) | Yes | Yes |
| JavaScript | `.js`, `.jsx` | Tree-sitter (regex fallback) | Yes | Yes |
| Python | `.py` | Tree-sitter (regex fallback) | Yes | Yes |
| Java | `.java` | Tree-sitter (regex fallback) | Yes | Yes |
| Rust | `.rs` | Tree-sitter (regex fallback) | Yes | Yes |
| Go | `.go` | Tree-sitter only | Yes | Yes |
| C | `.c`, `.h` | Tree-sitter only | Yes | — |
| C++ | `.cpp`, `.cc`, `.cxx`, `.hpp`, `.hh`, `.hxx` | Tree-sitter only | Yes | Yes |

To pre-install all grammars at once:

```bash
pip install tree-sitter tree-sitter-python tree-sitter-javascript tree-sitter-typescript tree-sitter-java tree-sitter-rust tree-sitter-go tree-sitter-c tree-sitter-cpp
```

---

## CLI Reference

```bash
python cli/main.py <command>
```

| Command | Description |
|---------|-------------|
| `doctor` | Diagnose system status |
| `init` | Initialize a project for HAN |
| `sync` | Sync Code Graph from source files |
| `status` | Show project status overview |
| `drift` | Check SSOT vs Code drift |
| `graph` | Query and explore the SSOT Graph |
| `dashboard` | Show full system dashboard |
| `ssot-sync` | Sync SSOT Index to Graph |
| `install-hooks` | Install Git hooks for auto-sync |
| `eval` | Run deterministic harness trajectory evals |
| `traces` | Inspect local traces and guardrail events |
| `reviews` | Inspect and resolve human review queue items |
| `migrate` | Apply numbered schema migrations |
| `guard` | Check guardrail policy for commands and paths |

Examples:

```bash
python cli/main.py guard --agent executor --command "rm -rf /tmp/project"
python cli/main.py guard --agent executor --mode warn --command "rm -rf /tmp/project"
python cli/main.py eval --trace trace_abc --expected executor,critic,memory
python cli/main.py traces --export-jsonl /tmp/han-traces.jsonl
python cli/main.py traces --export-otel-jsonl /tmp/han-otel.jsonl
python cli/main.py reviews
python cli/main.py migrate --history
```

### Utility Scripts

```bash
cd <path-to-han-agents>

python scripts/install.py --skip-prompts           # Install / update
python scripts/doctor.py                           # Verify installation
python scripts/sync.py /path/to/project            # Sync Code Graph
python scripts/init_project.py my-project /path/  # Init a project
```

---

## Platform Compatibility

### Supported Platforms

| Platform | Skills Directory | Scope |
|----------|-----------------|-------|
| [Claude Code](https://claude.ai/code) | `~/.claude/skills/` or `.claude/skills/` | Global / Project |
| [Cursor](https://cursor.com) | `~/.cursor/skills/` or `.cursor/skills/` | Global / Project |
| [Windsurf](https://windsurf.com) | `.windsurf/skills/` | Project |
| [Cline](https://cline.bot) | `~/.cline/skills/` or `.cline/skills/` | Global / Project |
| [Codex CLI](https://developers.openai.com/codex) | `~/.codex/skills/` or `.codex/skills/` | Global / Project |
| [Gemini CLI](https://github.com/google-gemini/gemini-cli) | `.gemini/skills/` | Project |
| [Antigravity](https://antigravity.google) | `~/.antigravity/skills/` or `.antigravity/skills/` | Global / Project |
| [Kiro](https://kiro.dev) | Powers system (one-click install) | — |

### Feature Support

| Feature | Claude Code | Other Platforms |
|---------|:-----------:|:---------------:|
| Memory & Semantic Search | Full | Full |
| Code Graph & Drift Detection | Full | Full |
| Task Lifecycle Management | Full | Full |
| Multi-Agent Coordination | Native (parallel) | Sequential |

> Claude Code's Agent tool enables parallel agent execution with isolated contexts. Other platforms run agents sequentially in a shared context.

---

## Database

HAN uses SQLite at `<han-agents>/brain/brain.db`, auto-created on first use.

- Schema: [brain/schema.sql](brain/schema.sql)
- Reference DB for testing: `brain/brain.example.db`

**Requirements:** Python 3.8+, SQLite 3.9+ (FTS5 required)

---

## Documentation

| Document | Description |
|----------|-------------|
| [SKILL.md](SKILL.md) | Main skill definition |
| [reference/API_REFERENCE.md](reference/API_REFERENCE.md) | Complete API documentation |
| [reference/WORKFLOW_GUIDE.md](reference/WORKFLOW_GUIDE.md) | Workflow patterns |
| [reference/GRAPH_GUIDE.md](reference/GRAPH_GUIDE.md) | Graph operations |
| [reference/TROUBLESHOOTING.md](reference/TROUBLESHOOTING.md) | Common issues and fixes |
| [docs/HARNESS_ROADMAP.md](docs/HARNESS_ROADMAP.md) | Trace/eval/guardrail maturity roadmap |
| [docs/QUICKSTART_HANDOVER.md](docs/QUICKSTART_HANDOVER.md) | Quick start handover guide |
| [docs/WHITEPAPER.md](docs/WHITEPAPER.md) | Architecture whitepaper |

---

## License

MIT
