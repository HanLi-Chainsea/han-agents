# Graph Operations Guide

## Two Graph Layers

| Layer | Purpose | Tables |
|-------|---------|--------|
| **Skill Graph** | Intent (what SHOULD be) | project_nodes, project_edges |
| **Code Graph** | Reality (what IS) | code_nodes, code_edges |

## Import

```python
import sys, os
from servers import HAN_BASE_DIR
sys.path.insert(0, HAN_BASE_DIR)

from servers.facade import sync, sync_skill_graph, check_drift
from servers.graph import get_neighbors, get_impact, list_nodes
from servers.code_graph import get_code_nodes, get_code_edges
```

## Code Graph Sync

```python
result = sync('/path/to/project', 'my-project', incremental=True)
# {'files_processed': 10, 'nodes_added': 50, 'edges_added': 80}
```

### Node Types
`file`, `class`, `struct`, `function`, `interface`, `type`, `constant`, `module`

### Edge Types
`imports`, `calls`, `extends`, `implements`, `contains`, `defines`

### Supported Languages
TypeScript/JavaScript, Python, Java, Rust, Go, C, C++

### Parser Backends
- **Tree-sitter** (priority=10): Accurate AST parsing with call graph extraction, class method extraction, and class hierarchy. Available for all 8 supported languages; C and C++ use tree-sitter only (no regex fallback).
- **Regex** (priority=0, fallback): Pattern-based extraction when tree-sitter is unavailable. Supports TypeScript, JavaScript, Python, Java, and Rust.

```python
from tools.code_graph_extractor.backends import get_backend, list_backends

# List all registered backends and their capabilities
print(list_backends())
# Example output:
# [
#   {'name': 'tree_sitter', 'priority': 10, 'capabilities': ['functions', 'classes', 'imports', 'methods', 'calls']},
#   {'name': 'regex',       'priority': 0,  'capabilities': ['functions', 'classes', 'imports']}
# ]

# Get best backend for a language
backend = get_backend('typescript')
result = backend.extract(content, file_path)
```

## Cross-File Resolution (Phase 3)

After all files in a project are extracted, symbolic references (e.g. `class.Base`, `symbol.hello`)
are resolved to actual node IDs using a project-wide SymbolTable.

```python
from tools.code_graph_extractor.resolver import resolve_edges, SymbolTable

# nodes and edges come from the extraction pass
resolved_edges, stats = resolve_edges(nodes, edges)
print(stats)
# ResolveStats(total_edges=120, resolved=98, unresolved=18, ambiguous=4)
```

Resolution rules:
- **Already resolved** (contains `/` after kind prefix) — passed through unchanged
- **`module.name`** — matched against file nodes by path; external packages kept symbolic with `confidence=0.5`
- **`symbol.name`** — tries `function` then `class` lookup; ambiguous matches get `confidence=0.6`
- **`kind.Name`** (class, struct, function, …) — direct O(1) lookup via SymbolTable

## Skill Graph Sync

從專案的 SKILL.md 同步節點到 Graph。

```python
result = sync_skill_graph('/path/to/project', 'my-project')
# {'nodes_added': 15, 'edges_added': 20}
```

### SKILL.md Format

專案 SKILL.md 位於 `<project>/.claude/skills/<project-name>/SKILL.md`

使用 Markdown 連結格式定義 Flows、Domains、APIs：

```markdown
## 業務流程
- [認證流程](flows/auth.md) - 用戶登入認證流程
- [結帳流程](flows/checkout.md) - 購物車結帳流程

## 領域模型
- [用戶領域](domains/user.md) - 用戶相關資料結構
- [訂單領域](domains/order.md) - 訂單相關資料結構

## API 規格
- [用戶 API](apis/user.md) - 用戶相關 API 端點
```

## Graph Queries

```python
# Get neighbors (N-hop)
neighbors = get_neighbors('flow.auth', project='my-project', depth=2)

# Get impact (what depends on this)
impact = get_impact('api.login', project='my-project')

# List nodes
flows = list_nodes('my-project', kind='flow')
```

## Drift Detection

檢查意圖 vs Code（現實）的偏差。意圖來源優先取專案根的 `intent-manifest.json`
（doc-grounded，註冊 PRD/SA/SD）；無 manifest 時回退 legacy SSOT（SKILL.md flows/domains）。

```python
report = check_drift('/path/to/project', 'my-project', 'auth')
if report['has_drift']:
    for d in report['drifts']:
        print(f"[{d['type']}] {d['description']}")
```

### Drift Types

| Type | Description |
|------|-------------|
| `missing_implementation` | Skill defines, code missing |
| `missing_spec` | Code exists, Skill missing |
| `mismatch` | Both exist but differ |

### Conflict Resolution

| Scenario | Action |
|----------|--------|
| Skill says X, Code does Y | Human decision |
| Code has X, Skill missing | Add to Skill |
| Test fails | High priority fix |

## Type Registry

```python
from servers.registry import register_node_kind, register_edge_kind

register_node_kind('component', '元件', 'React/Vue component')
register_edge_kind('renders', '渲染', 'Component renders component')
```

## Best Practices

1. Sync after git pull
2. Use incremental sync for normal development
3. Check drift before major changes
4. Keep SKILL.md updated with implementation
