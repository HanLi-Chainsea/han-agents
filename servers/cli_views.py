"""
HAN System - CLI / Slash-command Views

把 `/han:*` 唯讀指令用到的「查詢 + 格式化」集中在這裡，並由 tests/test_cli_views.py
單元測試鎖住對底層 API 的欄位契約。指令本文只呼叫這些函式、不再硬寫欄位名——
這樣 API 欄位漂移會被測試擋下，而非靠人肉 review（先前 node_kind/edge_kind/
framework 之類的低級錯誤就是因為在 prose 裡硬寫欄位）。

每個函式回傳「可直接 print 的字串」。**不寫使用者原始碼/檔案**；但部分函式會更新
HAN 內部 SQLite（`sync_report`/`init_report` 同步圖譜、`recall_report` 更新
search_memory 的 access_count）——這些是 HAN 自身狀態，非使用者檔案。
"""

from typing import Optional


def _fmt_sync(sr: Optional[dict]) -> str:
    """格式化 facade.sync() 的回傳（鍵：files_processed/files_skipped/
    nodes_added/nodes_updated/edges_added/duration_ms/errors）。"""
    sr = sr or {}
    return (
        f"files_processed={sr.get('files_processed')} "
        f"nodes(+{sr.get('nodes_added')}/~{sr.get('nodes_updated')}) "
        f"edges(+{sr.get('edges_added')}) "
        f"skipped={sr.get('files_skipped')} "
        f"{sr.get('duration_ms')}ms"
        + (f" errors={len(sr.get('errors') or [])}" if sr.get('errors') else "")
    )


def status_report(project_path: str = None) -> str:
    from servers.facade import quick_status
    return quick_status(project_path)


def drift_report(project: str, project_dir: str = None) -> str:
    from servers.drift import get_drift_summary
    return get_drift_summary(project, project_dir)


def sync_report(project_path: str, project: str) -> str:
    from servers.facade import sync
    return "synced: " + _fmt_sync(sync(project_path, project, incremental=True))


def init_report(project: str, project_path: str) -> str:
    from servers.project import ensure_project
    r = ensure_project(project, project_path)
    ts = r.get('tech_stack') or {}
    return "\n".join([
        f"previously_initialized: {r.get('already_initialized')}",  # 本次執行「前」是否已初始化
        f"language: {ts.get('primary_language')}",
        f"frameworks: {ts.get('frameworks')}",     # 注意：複數陣列鍵
        f"test_tool: {ts.get('test_tool')}",
        "code_graph: " + _fmt_sync(r.get('sync_result')),
    ])


def recall_report(project: str, query: str, limit: int = 10) -> str:
    from servers.memory import search_memory
    rows = search_memory(query, project=project, limit=limit) or []
    if not rows:
        return "（無相關記憶）"
    return "\n".join(
        f"- [{m.get('category')}] {m.get('title')}\n    {(m.get('content') or '')[:200]}"
        for m in rows
    )


_NODE_LIMIT = 2000


def _resolve_nodes(project: str, target: str):
    """先當路徑找；找不到再當符號名比對。回 (nodes, truncated)。"""
    from servers.code_graph import get_code_nodes
    nodes = get_code_nodes(project, file_path=target, limit=_NODE_LIMIT)
    if not nodes:
        scanned = get_code_nodes(project, limit=_NODE_LIMIT)
        nodes = [n for n in scanned if n.get('name') == target or target in n['id']]
        truncated = len(scanned) >= _NODE_LIMIT  # 全庫掃描可能漏節點
    else:
        truncated = len(nodes) >= _NODE_LIMIT
    return nodes, truncated


def impact_report(project: str, target: str) -> str:
    """改動影響半徑：分開查 incoming(扇入)/outgoing(扇出)，方向語義相對目標正確。"""
    from servers.code_graph import get_code_dependencies
    nodes, truncated = _resolve_nodes(project, target)
    if not nodes:
        return "（找不到目標節點，請先 /han:sync 或確認路徑/名稱）"
    out = []
    if truncated:
        out.append(f"（注意：節點查詢達上限 {_NODE_LIMIT}，目標解析可能不完整）")
    for n in nodes:
        inc = get_code_dependencies(project, n['id'], depth=2, direction='incoming') or []
        outg = get_code_dependencies(project, n['id'], depth=2, direction='outgoing') or []
        out.append(
            f"### {n['kind']} {n['id']}  影響半徑={len(inc) + len(outg)}"
            f"（扇入 {len(inc)} / 扇出 {len(outg)}）"
        )
        out.append("  呼叫者/依賴我者（改動會波及）：")
        out += [f"    - {d.get('name') or d['id']} ({d['kind']}) "
                f"via {d['relation']} [深度 {d['depth']}]" for d in inc[:20]]
        out.append("  我依賴的：")
        out += [f"    - {d.get('name') or d['id']} ({d['kind']}) "
                f"via {d['relation']}" for d in outg[:20]]
    return "\n".join(out)


def blast_radius(project: str, file_path: str) -> str:
    """review CODE 模式用：變更檔節點的 1-hop 影響半徑摘要。"""
    from servers.code_graph import get_code_nodes, get_code_dependencies
    nodes = get_code_nodes(project, file_path=file_path, limit=50)
    if not nodes:
        return f"（{file_path} 無對應節點，可先 /han:sync）"
    out = []
    for n in nodes:
        deps = get_code_dependencies(project, n['id'], depth=1, direction='both') or []
        names = [d.get('name') or d['id'] for d in deps][:10]
        out.append(f"{n['kind']} {n['id']} -> 影響/相依({len(deps)}): {names}")
    return "\n".join(out)
