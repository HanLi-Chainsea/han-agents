"""
Code Graph Server

提供 Code Graph 的查詢和更新 API。
整合 Code Graph Extractor，支援增量更新。

設計原則：
1. 與 SSOT Graph（project_nodes/edges）分開
2. 支援增量更新，只處理變更檔案
3. 提供友善的錯誤訊息
"""

import sqlite3
import json
from typing import List, Dict, Optional, Tuple
from datetime import datetime
from collections import defaultdict

# =============================================================================
# SCHEMA（供 Agent 參考）
# =============================================================================

SCHEMA = """
=== Code Graph API ===

sync_from_directory(project, directory, incremental=True) -> SyncResult
    從目錄同步 Code Graph（主要 API）
    - project: 專案名稱
    - directory: 目錄路徑
    - incremental: 是否增量更新（預設 True）
    Returns: {
        'nodes_added': int,
        'nodes_updated': int,
        'edges_added': int,
        'files_processed': int,
        'files_skipped': int,
        'errors': List[str]
    }

get_code_nodes(project, kind=None, file_path=None, limit=100) -> List[Dict]
    查詢 Code Nodes
    - kind: 過濾類型（可選）
    - file_path: 過濾檔案（可選）
    Returns: [{id, kind, name, file_path, line_start, line_end, ...}]

get_code_edges(project, from_id=None, to_id=None, kind=None, limit=100) -> List[Dict]
    查詢 Code Edges
    Returns: [{from_id, to_id, kind, line_number, confidence}]

get_code_dependencies(project, node_id, depth=1, direction='both') -> List[Dict]
    查詢節點的依賴關係
    - direction: 'incoming', 'outgoing', 'both'
    Returns: [{id, kind, name, relation, depth}]

get_file_structure(project, file_path) -> Dict
    取得檔案的結構摘要
    Returns: {
        'file': {...},
        'classes': [...],
        'functions': [...],
        'imports': [...]
    }

clear_code_graph(project) -> int
    清除專案的 Code Graph（重建前使用）
    Returns: 刪除的 node 數量

get_code_graph_stats(project) -> Dict
    取得 Code Graph 統計
    Returns: {
        'node_count': int,
        'edge_count': int,
        'file_count': int,
        'kinds': {kind: count},
        'last_sync': datetime
    }

get_class_dependencies_bfs(project, class_name, max_depth=2, include_edges=['imports', 'extends', 'implements', 'injects']) -> Dict
    BFS 遍歷類別依賴圖（用於 Unit Test context 收集）
    - class_name: 類別名稱（可以是簡單名稱或完整名稱）
    - max_depth: 最大遍歷深度（預設 2）
    - include_edges: 要遍歷的邊類型
    Returns: {
        'root': str,  # 根節點 ID
        'dependencies': [
            {'id': str, 'name': str, 'kind': str, 'file_path': str, 'depth': int, 'via': str}
        ],
        'interfaces_only': [...],  # 只返回介面簽名的節點
        'total': int
    }
"""

# =============================================================================
# Database Connection
# =============================================================================

from servers import managed_connection


def _get_conn():
    """Module-level connection context manager with Row factory."""
    return managed_connection(row_factory=True)

# =============================================================================
# Sync API
# =============================================================================

def sync_from_directory(
    project: str,
    directory: str,
    incremental: bool = True
) -> Dict:
    """
    從目錄同步 Code Graph

    Args:
        project: 專案名稱
        directory: 目錄路徑
        incremental: 是否增量更新

    Returns:
        同步結果統計
    """
    from tools.code_graph_extractor import extract_from_directory

    with _get_conn() as conn:
        # 1. 取得現有的 file hashes（用於增量比對）
        existing_hashes = {}
        if incremental:
            cursor = conn.execute(
                "SELECT file_path, hash FROM file_hashes WHERE project = ?",
                (project,)
            )
            existing_hashes = {row['file_path']: row['hash'] for row in cursor.fetchall()}

        # 2. 提取
        result = extract_from_directory(
            directory=directory,
            incremental=incremental,
            project=project,
            file_hashes=existing_hashes
        )

        if result['errors']:
            return {
                'nodes_added': 0,
                'nodes_updated': 0,
                'edges_added': 0,
                'files_processed': 0,
                'files_skipped': 0,
                'errors': result['errors']
            }

        # 3. 更新資料庫
        nodes_added = 0
        nodes_updated = 0
        edges_added = 0

        # Precompute per-file counts (Phase 0.3)
        node_count_by_file = defaultdict(int)
        edge_count_by_file = defaultdict(int)
        for node in result['nodes']:
            fp = node.get('file_path', '')
            if fp:
                node_count_by_file[fp] += 1
        for edge in result['edges']:
            from_id = edge.get('from_id', '')
            if '.' in from_id:
                source_path = from_id.split('.', 1)[1].split(':', 1)[0]
                edge_count_by_file[source_path] += 1

        # Batch upsert nodes (Phase 0.5)
        node_rows = [
            (
                node['id'], project, node['kind'], node['name'],
                node['file_path'], node.get('line_start', 0), node.get('line_end', 0),
                node.get('signature'), node.get('language'), node.get('visibility'), node.get('hash')
            )
            for node in result['nodes']
        ]
        if node_rows:
            conn.executemany(
                """
                INSERT INTO code_nodes
                (id, project, kind, name, file_path, line_start, line_end, signature, language, visibility, hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id, project) DO UPDATE SET
                    kind = excluded.kind,
                    name = excluded.name,
                    file_path = excluded.file_path,
                    line_start = excluded.line_start,
                    line_end = excluded.line_end,
                    signature = excluded.signature,
                    language = excluded.language,
                    visibility = excluded.visibility,
                    hash = excluded.hash,
                    last_updated = CURRENT_TIMESTAMP
                """,
                node_rows
            )
            nodes_added = len(node_rows)

        # Exact file-scoped edge deletion (Phase 0.2)
        processed_files = set(n['file_path'] for n in result['nodes'] if n['kind'] == 'file')
        for file_path in processed_files:
            conn.execute(
                """
                DELETE FROM code_edges
                WHERE project = ?
                  AND from_id IN (
                      SELECT id FROM code_nodes
                      WHERE project = ? AND file_path = ?
                  )
                """,
                (project, project, file_path)
            )

        # Batch insert edges (Phase 0.5)
        edge_rows = [
            (
                project, edge['from_id'], edge['to_id'], edge['kind'],
                edge.get('line_number'), edge.get('confidence', 1.0)
            )
            for edge in result['edges']
        ]
        if edge_rows:
            conn.executemany(
                """
                INSERT OR IGNORE INTO code_edges
                (project, from_id, to_id, kind, line_number, confidence)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                edge_rows
            )
            edges_added = len(edge_rows)

        # Batch update file hashes with precomputed counts (Phase 0.3)
        hash_rows = [
            (project, fp, hv, node_count_by_file[fp], edge_count_by_file[fp])
            for fp, hv in result['file_hashes'].items()
        ]
        if hash_rows:
            conn.executemany(
                """
                INSERT INTO file_hashes (project, file_path, hash, node_count, edge_count)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(project, file_path) DO UPDATE SET
                    hash = excluded.hash,
                    node_count = excluded.node_count,
                    edge_count = excluded.edge_count,
                    last_updated = CURRENT_TIMESTAMP
                """,
                hash_rows
            )

        conn.commit()

        return {
            'nodes_added': nodes_added,
            'nodes_updated': nodes_updated,
            'edges_added': edges_added,
            'files_processed': result['files_processed'],
            'files_skipped': result['files_skipped'],
            'errors': []
        }

# =============================================================================
# Query API
# =============================================================================

def get_code_nodes(
    project: str,
    kind: str = None,
    file_path: str = None,
    limit: int = 100,
    offset: int = 0
) -> List[Dict]:
    """查詢 Code Nodes"""
    if limit < 0 or offset < 0:
        raise ValueError(f"limit/offset must be >= 0 (got limit={limit}, offset={offset})")
    with _get_conn() as conn:
        query = "SELECT * FROM code_nodes WHERE project = ?"
        params = [project]

        if kind:
            query += " AND kind = ?"
            params.append(kind)

        if file_path:
            query += " AND file_path LIKE ?"
            params.append(f"%{file_path}%")

        query += " ORDER BY file_path, line_start, id LIMIT ? OFFSET ?"
        params.append(limit)
        params.append(offset)

        cursor = conn.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]
def get_code_edges(
    project: str,
    from_id: str = None,
    to_id: str = None,
    kind: str = None,
    limit: int = 100,
    offset: int = 0
) -> List[Dict]:
    """查詢 Code Edges"""
    if limit < 0 or offset < 0:
        raise ValueError(f"limit/offset must be >= 0 (got limit={limit}, offset={offset})")
    with _get_conn() as conn:
        query = "SELECT * FROM code_edges WHERE project = ?"
        params = [project]

        if from_id:
            query += " AND from_id = ?"
            params.append(from_id)

        if to_id:
            query += " AND to_id = ?"
            params.append(to_id)

        if kind:
            query += " AND kind = ?"
            params.append(kind)

        query += " ORDER BY id LIMIT ? OFFSET ?"
        params.append(limit)
        params.append(offset)

        cursor = conn.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]
def get_code_dependencies(
    project: str,
    node_id: str,
    depth: int = 1,
    direction: str = 'both'
) -> List[Dict]:
    """
    查詢節點的依賴關係

    Args:
        project: 專案名稱
        node_id: 節點 ID
        depth: 搜尋深度
        direction: 'incoming', 'outgoing', 'both'

    Returns:
        依賴節點列表，包含關係類型和深度
    """
    with _get_conn() as conn:
        results = []
        visited = set()

        def _traverse(current_id: str, current_depth: int, relation: str):
            if current_depth > depth or current_id in visited:
                return
            visited.add(current_id)

            if direction in ('outgoing', 'both'):
                cursor = conn.execute(
                    """
                    SELECT e.to_id, e.kind, n.kind as node_kind, n.name, n.file_path
                    FROM code_edges e
                    LEFT JOIN code_nodes n ON e.to_id = n.id AND e.project = n.project
                    WHERE e.project = ? AND e.from_id = ?
                    """,
                    (project, current_id)
                )
                for row in cursor.fetchall():
                    if row['to_id'] not in visited:
                        results.append({
                            'id': row['to_id'],
                            'kind': row['node_kind'],
                            'name': row['name'],
                            'file_path': row['file_path'],
                            'relation': row['kind'],
                            'direction': 'outgoing',
                            'depth': current_depth
                        })
                        if current_depth < depth:
                            _traverse(row['to_id'], current_depth + 1, row['kind'])

            if direction in ('incoming', 'both'):
                cursor = conn.execute(
                    """
                    SELECT e.from_id, e.kind, n.kind as node_kind, n.name, n.file_path
                    FROM code_edges e
                    LEFT JOIN code_nodes n ON e.from_id = n.id AND e.project = n.project
                    WHERE e.project = ? AND e.to_id = ?
                    """,
                    (project, current_id)
                )
                for row in cursor.fetchall():
                    if row['from_id'] not in visited:
                        results.append({
                            'id': row['from_id'],
                            'kind': row['node_kind'],
                            'name': row['name'],
                            'file_path': row['file_path'],
                            'relation': row['kind'],
                            'direction': 'incoming',
                            'depth': current_depth
                        })
                        if current_depth < depth:
                            _traverse(row['from_id'], current_depth + 1, row['kind'])

        _traverse(node_id, 1, '')
        return results
def get_file_structure(project: str, file_path: str) -> Dict:
    """
    取得檔案的結構摘要

    Returns:
        {
            'file': {...},
            'classes': [...],
            'functions': [...],
            'interfaces': [...],
            'imports': [...]
        }
    """
    with _get_conn() as conn:
        # 取得檔案節點
        cursor = conn.execute(
            "SELECT * FROM code_nodes WHERE project = ? AND file_path LIKE ? AND kind = 'file'",
            (project, f"%{file_path}%")
        )
        file_node = cursor.fetchone()

        if not file_node:
            return {'error': f'File not found: {file_path}'}

        file_node = dict(file_node)
        file_id = file_node['id']

        # 取得此檔案定義的所有節點
        cursor = conn.execute(
            """
            SELECT n.* FROM code_nodes n
            JOIN code_edges e ON n.id = e.to_id AND n.project = e.project
            WHERE e.project = ? AND e.from_id = ? AND e.kind = 'defines'
            ORDER BY n.kind, n.line_start
            """,
            (project, file_id)
        )
        defined_nodes = [dict(row) for row in cursor.fetchall()]

        # 取得 imports
        cursor = conn.execute(
            """
            SELECT e.to_id, e.line_number FROM code_edges e
            WHERE e.project = ? AND e.from_id = ? AND e.kind = 'imports'
            ORDER BY e.line_number
            """,
            (project, file_id)
        )
        imports = [{'target': row['to_id'], 'line': row['line_number']} for row in cursor.fetchall()]

        # 分類
        return {
            'file': file_node,
            'classes': [n for n in defined_nodes if n['kind'] == 'class'],
            'functions': [n for n in defined_nodes if n['kind'] == 'function'],
            'interfaces': [n for n in defined_nodes if n['kind'] == 'interface'],
            'types': [n for n in defined_nodes if n['kind'] == 'type'],
            'constants': [n for n in defined_nodes if n['kind'] == 'constant'],
            'imports': imports
        }
# =============================================================================
# Management API
# =============================================================================

def clear_code_graph(project: str) -> int:
    """清除專案的 Code Graph"""
    with _get_conn() as conn:
        cursor = conn.execute("SELECT COUNT(*) as cnt FROM code_nodes WHERE project = ?", (project,))
        count = cursor.fetchone()['cnt']

        conn.execute("DELETE FROM code_nodes WHERE project = ?", (project,))
        conn.execute("DELETE FROM code_edges WHERE project = ?", (project,))
        conn.execute("DELETE FROM file_hashes WHERE project = ?", (project,))
        conn.commit()

        return count
def get_code_graph_stats(project: str) -> Dict:
    """取得 Code Graph 統計"""
    with _get_conn() as conn:
        # Node 統計
        cursor = conn.execute(
            "SELECT COUNT(*) as cnt FROM code_nodes WHERE project = ?",
            (project,)
        )
        node_count = cursor.fetchone()['cnt']

        # Edge 統計
        cursor = conn.execute(
            "SELECT COUNT(*) as cnt FROM code_edges WHERE project = ?",
            (project,)
        )
        edge_count = cursor.fetchone()['cnt']

        # File 統計
        cursor = conn.execute(
            "SELECT COUNT(*) as cnt FROM code_nodes WHERE project = ? AND kind = 'file'",
            (project,)
        )
        file_count = cursor.fetchone()['cnt']

        # Kind 分佈
        cursor = conn.execute(
            "SELECT kind, COUNT(*) as cnt FROM code_nodes WHERE project = ? GROUP BY kind",
            (project,)
        )
        kinds = {row['kind']: row['cnt'] for row in cursor.fetchall()}

        # 最後同步時間
        cursor = conn.execute(
            "SELECT MAX(last_updated) as last_sync FROM file_hashes WHERE project = ?",
            (project,)
        )
        row = cursor.fetchone()
        last_sync = row['last_sync'] if row else None

        return {
            'node_count': node_count,
            'edge_count': edge_count,
            'file_count': file_count,
            'kinds': kinds,
            'last_sync': last_sync
        }
# =============================================================================
# 便利函數
# =============================================================================

def summarize_file(project: str, file_path: str) -> str:
    """產出檔案結構的 Markdown 摘要"""
    structure = get_file_structure(project, file_path)

    if 'error' in structure:
        return f"Error: {structure['error']}"

    lines = [f"## {structure['file']['name']}", ""]

    if structure['imports']:
        lines.append("### Imports")
        for imp in structure['imports']:
            lines.append(f"- `{imp['target']}`")
        lines.append("")

    if structure['classes']:
        lines.append("### Classes")
        for cls in structure['classes']:
            lines.append(f"- `{cls['name']}` (L{cls['line_start']}-{cls['line_end']})")
        lines.append("")

    if structure['functions']:
        lines.append("### Functions")
        for func in structure['functions']:
            vis = f"[{func['visibility']}] " if func.get('visibility') else ""
            lines.append(f"- {vis}`{func['name']}` (L{func['line_start']}-{func['line_end']})")
        lines.append("")

    if structure['interfaces']:
        lines.append("### Interfaces")
        for iface in structure['interfaces']:
            lines.append(f"- `{iface['name']}` (L{iface['line_start']}-{iface['line_end']})")
        lines.append("")

    return "\n".join(lines)


def get_class_dependencies_bfs(
    project: str,
    class_name: str,
    max_depth: int = 2,
    include_edges: List[str] = None
) -> Dict:
    """
    BFS 遍歷類別依賴圖（用於 Unit Test context 收集）

    Args:
        project: 專案名稱
        class_name: 類別名稱（簡單名稱或完整名稱）
        max_depth: 最大遍歷深度（預設 2）
        include_edges: 要遍歷的邊類型（預設 imports, extends, implements, injects）

    Returns:
        {
            'root': str,  # 根節點 ID
            'root_file': str,  # 根節點檔案路徑
            'dependencies': [
                {'id': str, 'name': str, 'kind': str, 'file_path': str, 'depth': int, 'via': str}
            ],
            'interfaces_only': [...],  # 介面類型的節點（建議只讀簽名）
            'total': int
        }
    """
    if include_edges is None:
        include_edges = ['imports', 'extends', 'implements', 'injects']

    with _get_conn() as conn:
        # 1. 找到根節點（類別）
        cursor = conn.execute(
            """
            SELECT id, name, kind, file_path FROM code_nodes
            WHERE project = ? AND (name = ? OR id LIKE ?)
            AND kind IN ('class', 'interface', 'enum')
            LIMIT 1
            """,
            (project, class_name, f"%{class_name}%")
        )
        root_row = cursor.fetchone()

        if not root_row:
            return {
                'root': None,
                'error': f"Class not found: {class_name}",
                'dependencies': [],
                'interfaces_only': [],
                'total': 0
            }

        root_id = root_row['id']
        root_file = root_row['file_path']

        # 2. BFS 遍歷
        from collections import deque

        visited = {root_id}
        queue = deque([(root_id, 0, '')])  # (node_id, depth, via_edge_kind)
        dependencies = []
        interfaces_only = []

        while queue:
            current_id, depth, via = queue.popleft()

            if depth >= max_depth:
                continue

            # 查詢此節點的 outgoing edges
            edge_kinds_placeholder = ','.join(['?' for _ in include_edges])
            cursor = conn.execute(
                f"""
                SELECT e.to_id, e.kind, n.name, n.kind as node_kind, n.file_path, n.signature
                FROM code_edges e
                LEFT JOIN code_nodes n ON e.to_id = n.id AND e.project = n.project
                WHERE e.project = ? AND e.from_id = ?
                AND e.kind IN ({edge_kinds_placeholder})
                """,
                [project, current_id] + include_edges
            )

            for row in cursor.fetchall():
                to_id = row['to_id']
                if to_id in visited:
                    continue

                visited.add(to_id)

                dep = {
                    'id': to_id,
                    'name': row['name'],
                    'kind': row['node_kind'],
                    'file_path': row['file_path'],
                    'depth': depth + 1,
                    'via': row['kind'],
                    'signature': row['signature']
                }
                dependencies.append(dep)

                # 標記介面類型（只需讀簽名）
                if row['node_kind'] == 'interface':
                    interfaces_only.append(dep)

                # 繼續 BFS
                queue.append((to_id, depth + 1, row['kind']))

        # 3. 按深度排序
        dependencies.sort(key=lambda x: (x['depth'], x['name'] or ''))

        return {
            'root': root_id,
            'root_file': root_file,
            'dependencies': dependencies,
            'interfaces_only': interfaces_only,
            'total': len(dependencies)
        }
