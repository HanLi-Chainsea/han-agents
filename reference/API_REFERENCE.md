# HAN API Reference

## Import

```python
import sys, os
from servers import HAN_BASE_DIR
sys.path.insert(0, HAN_BASE_DIR)

# Facade (recommended)
from servers.facade import (
    init, sync, status, get_full_context, check_drift, sync_skill_graph,
    finish_task, finish_validation, run_validation_cycle, validate_with_graph,
    get_next_dispatch,  # Dispatch loop orchestration
)

# Tasks
from servers.tasks import (
    create_task, create_subtask, get_task, update_task, update_task_status,
    get_next_task, get_task_progress, get_unvalidated_tasks, mark_validated,
    advance_task_phase, get_task_branch, set_task_branch,
    reserve_critic_task,  # Atomic critic reservation
    get_epic_tasks, get_story_tasks, get_hierarchy_summary,
)

# Recipes (automated workflows)
from servers.recipes import recipe_unit_tests, run_recipe

# Project
from servers.project import ensure_project

# Memory
from servers.memory import (
    search_memory, search_memory_semantic, store_memory, store_memory_smart,
    get_working_memory, set_working_memory, save_checkpoint, load_checkpoint,
    get_project_context, add_episode, get_recent_episodes
)

# Harness tracing and evals
from servers.tracing import start_trace, start_span, finish_span, finish_trace, get_trace
from servers.evals import (
    evaluate_trajectory, evaluate_trace, evaluate_trace_jsonl,
    extract_agents_from_trace,
)
from servers.guardrails import get_agent_policy, check_command, check_path
```

---

## Facade API

### init(project_path, project_name=None) -> Dict
Initialize project (first-time use).

### sync(project_path, project_name=None, incremental=True) -> Dict
Sync Code Graph. Returns `{files_processed, nodes_added, edges_added, duration_ms}`.

### status(project_path=None, project_name=None) -> Dict
Get project status overview (includes Skill status).

### get_full_context(branch, project_path=None, project_name=None) -> Dict
Get three-layer context (Skill + Code + Memory + Drift).
```python
ctx = get_full_context({'flow_id': 'flow.auth'}, '/path/to/project', 'my-project')
# {skill: {...}, code: {...}, memory: [...], drift: {...}}
```

### check_drift(project_path, project_name=None, flow_name=None) -> Dict
Check Skill vs Code drift. Returns `{has_drift, drift_count, drifts: [{type, description, severity}]}`.
```python
report = check_drift('/path/to/project', 'my-project', 'auth')
```

### sync_skill_graph(project_path=None, project_name=None) -> Dict
Sync project SKILL.md to project_nodes/edges.

### finish_task(task_id, success, result=None, error=None) -> Dict
Executor must call when done. Returns `{status, phase, next_action}`.

### finish_validation(task_id, original_task_id, approved, issues=None) -> Dict
Critic must call when done. Returns `{status, next_action, resume_agent_id}`.

### validate_with_graph(modified_files, branch, project_path=None, project_name=None) -> Dict
Graph-enhanced validation (impact analysis, Skill compliance, test coverage).

### get_next_dispatch(parent_id, project_name, project_path, trace_id=None) -> Dict
Auto-dispatch next agent in the Executor → Critic → Memory pipeline.
Returns structured instruction for the main conversation to execute.
When `trace_id` is provided, the dispatch/status decision is recorded as a local
span with the prompt redacted and only `prompt_length` stored.

```python
inst = get_next_dispatch(epic_id, 'my-project', '/path/to/project')
# {
#     'action': 'dispatch' | 'done' | 'blocked' | 'waiting',
#     'subagent_type': 'executor' | 'critic' | 'memory',
#     'model_tier': 'planner' | 'worker' | 'fast',
#     'prompt': str,        # Complete prompt for Agent tool
#     'task_id': str,       # For tracking
#     'progress': '3/7 tasks complete',
#     'message': str,       # Human-readable status
# }
```

**Idempotent**: repeated calls return the same critic task (no duplicates).
**Memory-aware**: waits for memory task to complete before returning `done`.

---

## Recipes API

### recipe_unit_tests(project_name, project_path, target_path=None, max_tasks=20) -> Dict
Auto-generate unit test task tree from coverage gaps.

```python
result = recipe_unit_tests('my-project', '/path/to/project', target_path='src/')
# {
#     'epic_id': str,      # Feed to get_next_dispatch()
#     'task_count': int,
#     'story_count': int,
#     'gaps_found': int,
#     'stories': [...],
#     'message': str,
# }
```

### run_recipe(name, **kwargs) -> Dict
Run recipe by name. Available: `'unit_tests'`.

---

## Harness Trace & Eval API

### start_trace(workflow_name, project=None, group_id=None, metadata=None) -> str
Create a local trace for one end-to-end agent workflow.

### start_span(trace_id, name, span_type, parent_span_id=None, task_id=None, input=None, metadata=None) -> str
Create a span for a dispatch, tool call, validation, memory write, or other operation.

### finish_span(span_id, status='completed', output=None, error=None, metadata=None) -> Dict
Finish a span and store output or error details.

### finish_trace(trace_id, status='completed', metadata=None) -> Dict
Finish a trace and return the trace with ordered spans.

### summarize_trace(trace_id) -> Dict
Return span counts by type/status and guardrail violation count.

### list_guardrail_events(project=None, limit=20, only_violations=True) -> List[Dict]
List recent guardrail spans across traces.

### export_traces_jsonl(path, trace_id=None, project=None, limit=100) -> int
Export trace/span events as newline-delimited JSON for external processors.

### export_traces_otel_jsonl(path, trace_id=None, project=None, limit=100) -> int
Export OpenTelemetry-style span records as JSONL. The adapter follows the
current OpenTelemetry GenAI semantic-convention shape where applicable, while
keeping HAN-specific fields under `han.*`.

### evaluate_trajectory(actual, expected, mode='exact') -> Dict
Run deterministic exact or subsequence trajectory scoring.

### run_trajectory_dataset(path=None, dataset=None) -> Dict
Run all golden trajectory cases and return aggregate pass/fail metrics.

### evaluate_trace(trace_id, expected, mode='exact') -> Dict
Score a stored local trace against an expected dispatch sequence.

### evaluate_trace_jsonl(path, expected, mode='exact', trace_id=None) -> Dict
Score an exported trace JSONL file against an expected dispatch sequence.

```python
trace_id = start_trace('unit-test recipe', project='my-project')
span_id = start_span(trace_id, 'dispatch executor', 'dispatch',
                     metadata={'subagent_type': 'executor'})
finish_span(span_id, output={'subagent_type': 'executor'})
trace = finish_trace(trace_id)

agents = extract_agents_from_trace(trace)
score = evaluate_trajectory(agents, ['executor'], mode='exact')
```

CLI:

```bash
python cli/main.py eval
python cli/main.py eval --dataset evals/trajectories.json
python cli/main.py eval --trace trace_abc --expected executor,critic,memory
python cli/main.py eval --trace-jsonl /tmp/han-traces.jsonl --expected executor --mode subsequence
python cli/main.py traces
python cli/main.py traces --guardrails
python cli/main.py traces --export-jsonl /tmp/han-traces.jsonl
python cli/main.py traces --export-otel-jsonl /tmp/han-otel.jsonl
python cli/main.py guard --agent executor --command "rm -rf /tmp/project"
python cli/main.py guard --agent executor --mode warn --command "rm -rf /tmp/project"
python cli/main.py guard --agent critic --path servers/facade.py --operation write
python cli/main.py reviews
python cli/main.py reviews --enqueue-trace trace_abc
python cli/main.py reviews --resolve review_abc --reviewer alice --resolution approved
python cli/main.py migrate --history
```

---

## Guardrails API

### get_agent_policy(agent) -> Dict
Return the default provider-neutral policy for an agent.

### check_command(command, agent='executor') -> Dict
Classify shell command risk against denied patterns and network approval policy.

### check_path(path, agent='executor', operation='read') -> Dict
Classify read/write path access against allow/deny patterns.

### enforce_result(check_result, mode=None) -> Dict
Convert a guardrail check into `allow`, `warn`, or `block` enforcement. The
default mode comes from `HAN_GUARDRAIL_MODE` and falls back to `warn`.

Dispatch prompts include a concise guardrail policy block for executor, critic,
and memory agents. `hooks/pre_tool.py` can deny or ask on risky Bash/Write/Edit
operations before execution in hosts that support PreToolUse decisions.
`hooks/post_task.py` still records lifecycle events and adds post-tool guardrail
context where available.

---

## Human Review Queue API

### create_review_item(kind, reason, project=None, severity='medium', task_id=None, trace_id=None, span_id=None, payload=None) -> str
Create or reuse an open review item.

### list_review_items(status='open', project=None, limit=20) -> List[Dict]
List open, resolved, or all review items.

### resolve_review_item(item_id, reviewer, resolution, notes=None) -> Dict
Close a review item with reviewer metadata.

### enqueue_trace_reviews(trace_id) -> Dict
Create review items from warning/error spans in a trace.

Blocked Critic retries create high-severity review items automatically. Guardrail
PreToolUse violations create review items when a task or trace id is available.

---

## Migrations API

### apply_pending_migrations() -> Dict
Apply numbered SQL migrations from `migrations/` and return applied versions.

### get_migration_history() -> List[Dict]
Return applied schema versions.

---

## Project API

### ensure_project(project_name, project_path=None) -> Dict
Idempotent project initialization: sync Code Graph + detect tech stack + store in DB.

```python
result = ensure_project('my-project', '/path/to/project')
# {
#     'sync_result': {...},
#     'tech_stack': {'primary_language': 'python', 'test_tool': 'pytest', 'frameworks': [...]},
#     'already_initialized': bool,
# }
```

Tech stack is auto-detected from Code Graph (language distribution + import edges).
Upserts Tech Stack memory (no duplicate records on repeated calls).

---

## Tasks API

### create_task(project, description, priority=5, parent_id=None, task_level=None, epic_id=None, story_id=None, branch=None) -> str
Create task, returns task_id.
- `task_level`: 'epic', 'story', 'task', 'bug'
- `branch`: `{'flow_id': 'flow.auth', 'domain_ids': ['domain.user']}`

### create_subtask(parent_id, description, assigned_agent='executor', depends_on=None, requires_validation=True, task_level='task', epic_id=None, story_id=None) -> str
Create subtask with optional dependencies. Auto-inherits `epic_id`/`story_id` from parent when not specified.
- `assigned_agent`: 'executor', 'critic', 'memory', 'researcher'
- `depends_on`: list of task_ids

### get_task(task_id) -> Dict
Get task details (includes metadata, branch, executor_agent_id, rejection_count).

### update_task(task_id, **kwargs) -> None
Update task fields: executor_agent_id, rejection_count, status, phase, validation_status.

### update_task_status(task_id, status, result=None, error=None) -> None
Update status: 'pending', 'running', 'done', 'failed', 'blocked'.

### get_next_task(parent_id) -> Optional[Dict]
Get next executable task (dependencies completed).

### get_task_progress(parent_id) -> Dict
Get progress stats: `{total, completed, pending, percentage}`.

### get_unvalidated_tasks(parent_id) -> List[Dict]
Get tasks pending validation. Skips tasks that already have an active (pending/running) critic.

### reserve_critic_task(original_task_id) -> Optional[Dict]
Atomically reserve or reuse a critic task for a completed task. Uses `BEGIN IMMEDIATE` for concurrency safety.
Returns `{'id', 'original_task_id', 'original_description', 'result'}` or `None`.
**Idempotent**: repeated calls return the same critic task.

### mark_validated(task_id, status, validator_task_id=None) -> None
Mark validation: 'approved', 'rejected', 'skipped'.

### advance_task_phase(task_id, phase) -> None
Advance phase: 'execution', 'validation', 'documentation', 'completed'.

### get_task_branch(task_id) -> Optional[Dict]
Get task's branch info.

### set_task_branch(task_id, branch) -> None
Set task's branch info.

---

## Memory API

### search_memory_semantic(query, project=None, limit=5, rerank_mode='claude', **kwargs) -> Dict
Semantic search with reranking.
- `rerank_mode`: 'claude' (recommended), 'embedding', 'none'
```python
result = search_memory_semantic("auth pattern", rerank_mode='claude')
if result['mode'] == 'claude_rerank':
    print(result['rerank_prompt'])  # Agent selects best matches
```

### search_memory(query, project=None, category=None, limit=5, branch_flow=None) -> List[Dict]
Full-text search.
- `category`: 'sop', 'knowledge', 'error', 'preference', 'pattern', 'lesson'

### store_memory(category, content, title=None, project=None, importance=5, branch_flow=None) -> int
Store to long-term memory. Returns memory_id.
- `importance`: 1-10 (8-10 critical, 5-7 useful, 1-4 reference)

### store_memory_smart(category, content, title=None, project=None, importance=5, auto_supersede=True) -> Dict
Smart store: checks for similar memories first.
Returns `{id, action: 'created'|'superseded', superseded_ids}`.

### get_working_memory(task_id, key=None) -> Dict | Any
Read working memory (session-scoped key-value).

### set_working_memory(task_id, key, value, project=None) -> None
Set working memory.

### save_checkpoint(project, task_id, agent, state, summary) -> int
Save Micro-Nap checkpoint.
```python
save_checkpoint('proj', task_id, 'pfc',
    state={'completed': [...], 'pending': [...]},
    summary='Phase 1 complete')
```

### load_checkpoint(task_id) -> Optional[Dict]
Load latest checkpoint. Returns `{state, summary, created_at}`.

### get_project_context(project) -> Dict
Get project context for reconnection.
Returns `{active_tasks, last_phase, recent_activity, suggestion}`.

### add_episode(project, event_type, summary, details=None, session_id=None) -> int
Record event to episodic memory.
- `event_type`: 'task_completed', 'error_encountered', 'phase_complete', etc.

### get_recent_episodes(project, limit=5) -> List[Dict]
Get recent episodes.

---

## Memory Lifecycle

### challenge_memory(memory_id, reason, challenger='system') -> Dict
Mark memory as challenged. Returns `{success, memory_id, previous_status}`.

### resolve_challenge(memory_id, resolution, new_content=None) -> Dict
Resolve challenged memory.
- `resolution`: 'keep', 'update', 'deprecate'

### deprecate_memory(memory_id, reason=None) -> Dict
Deprecate memory directly.

### validate_memory(memory_id) -> Dict
Update last_validated timestamp.

### find_similar_memories(content, category=None, threshold=0.7, limit=5) -> List[Dict]
Find similar existing memories.

---

## Code Graph Backends

### ExtractorBackend Protocol

All backends implement the `ExtractorBackend` protocol from `tools.code_graph_extractor.backends`.

```python
from tools.code_graph_extractor.backends import (
    ExtractorBackend,   # Protocol
    get_backend,        # Get best backend for a language
    list_backends,      # List registered backends
    register_backend,   # Register a custom backend
    LANGUAGE_CONFIGS,   # Extension → backend config mapping
)
```

### get_backend(language) -> Optional[ExtractorBackend]
Returns the highest-priority registered backend that supports `language`, or `None`.

### list_backends() -> List[dict]
Returns all registered backends sorted by priority (descending).
```python
print(list_backends())
# [
#   {'name': 'tree_sitter', 'priority': 10, 'capabilities': ['functions', 'classes', 'imports', 'methods', 'calls']},
#   {'name': 'regex',       'priority': 0,  'capabilities': ['functions', 'classes', 'imports']}
# ]
```

### register_backend(backend, priority=0) -> None
Register a custom backend. Tree-sitter registers at priority=10; regex at priority=0.

### LANGUAGE_CONFIGS
Dict mapping language names to `{extensions, preferred_backend, fallback_backend}`.
Supported languages: `typescript`, `javascript`, `python`, `java`, `rust`, `go`, `c`, `cpp`.

---

## Cross-File Resolver

```python
from tools.code_graph_extractor.resolver import resolve_edges, SymbolTable, ResolveStats
```

### resolve_edges(nodes, edges) -> Tuple[List[CodeEdge], ResolveStats]
Resolve symbolic `to_id` references in edges using a project-wide node list.
Does not mutate the original edge list — returns a new list.

```python
resolved_edges, stats = resolve_edges(nodes, edges)
# stats.total_edges, stats.resolved, stats.unresolved, stats.ambiguous
```

### SymbolTable(nodes)
In-memory index for O(1) symbol lookup.

#### lookup(kind, name) -> List[CodeNode]
Return all nodes matching `(kind, name)`. Multiple results indicate ambiguity.

#### lookup_module(module_name) -> Optional[CodeNode]
Resolve a module/import path to a file node. Returns `None` for external packages.

---

---

## Error Classes

```python
from servers.facade import (
    FacadeError, ProjectNotFoundError, NotInitializedError, CodeGraphEmptyError
)
```
All errors include actionable fix messages.
