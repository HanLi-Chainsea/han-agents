"""
HAN Facade

統一入口，黑箱化系統複雜度。
使用者/Agent 只需要這個模組。

設計原則：
1. 極簡 API，一個函數做一件事
2. 錯誤訊息可行動（告訴使用者怎麼修）
3. 整合多個低階模組
"""

import os
import subprocess
from typing import Dict, List, Optional
from datetime import datetime

# =============================================================================
# SCHEMA（供 Agent 參考）
# =============================================================================

SCHEMA = """
=== HAN Facade ===
統一入口，使用者/Agent 只需要這些 API。

## 基本操作

sync(project_path=None, project_name=None, incremental=True) -> SyncResult
    同步專案 Code Graph（主要 API）
    - 自動偵測變更檔案
    - 增量更新 Code Graph（或完整重建）
    - 回傳同步結果

    Example:
        result = sync('/path/to/project', 'my-project')
        # {'files_processed': 10, 'nodes_added': 50, ...}

status(project_path=None, project_name=None) -> StatusReport
    取得專案狀態總覽
    - Code Graph 統計
    - Skill 狀態（專案 SKILL.md）
    - 最後同步時間

init(project_path, project_name=None) -> InitResult
    初始化專案（首次使用時呼叫）

## PFC 三層查詢

get_full_context(branch, project_path=None, project_name=None) -> Dict
    取得 Branch 完整三層 context（結構化版本）
    - Skill 層（意圖）- SKILL.md, flow_spec, related_nodes
    - Code Graph 層（現實）- related_files, dependencies
    - Memory 層（經驗）- 相關記憶
    - Drift: 偏差檢測

    Args:
        branch: {'flow_id': 'flow.auth', 'domain_ids': ['domain.user']}
        project_path: 專案目錄路徑（用於讀取專案 Skill）

    Example:
        ctx = get_full_context({'flow_id': 'flow.auth'}, '/path/to/project')
        # {'branch': {...}, 'ssot': {...}, 'code': {...}, 'memory': [...], 'drift': {...}}

format_context_for_agent(context) -> str
    將 get_full_context 結果格式化為 Agent 可讀的 Markdown

## Critic 增強驗證

validate_with_graph(modified_files, branch, project_path=None, project_name=None) -> Dict
    使用 Graph 做增強驗證
    - 修改影響分析
    - Skill 符合性檢查
    - 測試覆蓋檢查

    Args:
        modified_files: ['src/api/auth.py', ...]
        branch: {'flow_id': 'flow.auth'}
        project_path: 專案目錄路徑

    Returns: {
        'impact_analysis': {...},
        'ssot_compliance': {...},
        'test_coverage': {...},
        'recommendations': [...]
    }

format_validation_report(validation) -> str
    將 validate_with_graph 結果格式化為 Markdown 報告

## 任務生命週期管理（強制機制）

finish_task(task_id, success, result=None, error=None, skip_validation=False) -> Dict
    Executor 結束任務時必須呼叫
    - 自動更新 status, phase
    - 回傳 next_action 建議
    - 注意：executor_agent_id 由主對話在派發後記錄

    Example:
        result = finish_task(task_id, success=True, result='完成')
        # {'status': 'done', 'phase': 'validation', 'next_action': 'await_validation'}

finish_validation(task_id, original_task_id, approved, issues=None, suggestions=None) -> Dict
    Critic 結束驗證時必須呼叫
    - 自動更新驗證狀態
    - rejected 時回傳 resume 指令

    Example:
        result = finish_validation(critic_id, task_id, approved=False, issues=['覆蓋率不足'])
        # {'status': 'rejected', 'next_action': 'resume_executor', 'resume_agent_id': 'xxx'}

run_validation_cycle(parent_id, mode='normal', sample_count=3) -> Dict
    PFC 執行驗證循環
    - mode: 'normal' | 'batch_approve' | 'batch_skip' | 'sample'
    - 回傳待驗證任務列表

    Example:
        result = run_validation_cycle(parent_id, mode='sample', sample_count=3)
        # {'pending_validation': ['task1', 'task2'], 'approved': 5}

manual_validate(task_id, status, reviewer) -> Dict
    人類手動驗證（繞過 Critic）

## Drift 偵測

check_drift(project_path, project_name=None, flow_name=None) -> DriftReport
    檢查 Skill vs Code 偏差

    Args:
        project_path: 專案目錄路徑（必要）
        project_name: 專案名稱（預設使用目錄名）
        flow_name: 特定 Flow 名稱（可選）

    Example:
        report = check_drift('/path/to/project', 'my-project', 'auth')
        # {'has_drift': True, 'drifts': [...]}

## Skill Graph 同步

sync_skill_graph(project_path=None, project_name=None) -> SyncResult
    同步專案 SKILL.md 到 project_nodes/project_edges
    - 從 SKILL.md 解析所有連結
    - 建立節點和關係到 Graph
    - 動態支援任何類型

    Example:
        result = sync_skill_graph('/path/to/project', 'my-project')
        # {'nodes_added': 15, 'edges_added': 20, 'types_found': ['flows', ...]}
"""

# =============================================================================
# Errors
# =============================================================================

class FacadeError(Exception):
    """Facade 層錯誤基類"""
    pass

class ProjectNotFoundError(FacadeError):
    """專案不存在"""
    def __init__(self, path: str):
        self.path = path
        super().__init__(
            f"Project path not found: {path}\n\n"
            f"Please check:\n"
            f"  1. The path exists\n"
            f"  2. You have read permissions\n"
        )

class NotInitializedError(FacadeError):
    """系統未初始化"""
    def __init__(self):
        super().__init__(
            f"HAN system not initialized.\n\n"
            f"Please run:\n"
            f"  from servers.facade import init\n"
            f"  init('/path/to/your/project', 'project-name')\n"
        )

class CodeGraphEmptyError(FacadeError):
    """Code Graph 為空"""
    def __init__(self, project: str):
        self.project = project
        super().__init__(
            f"Code Graph is empty for project '{project}'.\n\n"
            f"Please run:\n"
            f"  from servers.facade import sync\n"
            f"  sync('/path/to/project', '{project}')\n"
        )

# =============================================================================
# Main API
# =============================================================================

def init(project_path: str, project_name: str = None) -> Dict:
    """
    初始化專案

    Args:
        project_path: 專案目錄路徑
        project_name: 專案名稱（預設使用目錄名）

    Returns:
        {
            'project_name': str,
            'project_path': str,
            'schema_initialized': bool,
            'types_initialized': (int, int),
            'code_graph_synced': bool,
            'sync_result': {...}
        }
    """
    from servers.registry import init_registry
    from servers.code_graph import sync_from_directory

    # 驗證路徑
    if not os.path.isdir(project_path):
        raise ProjectNotFoundError(project_path)

    project_name = project_name or os.path.basename(os.path.abspath(project_path))

    # 初始化 Schema 和預設類型
    node_count, edge_count = init_registry()

    # 同步 Code Graph
    sync_result = sync_from_directory(project_name, project_path, incremental=False)

    return {
        'project_name': project_name,
        'project_path': project_path,
        'schema_initialized': True,
        'types_initialized': (node_count, edge_count),
        'code_graph_synced': len(sync_result.get('errors', [])) == 0,
        'sync_result': sync_result
    }


def sync(project_path: str = None, project_name: str = None, incremental: bool = True) -> Dict:
    """
    同步專案 Code Graph

    Args:
        project_path: 專案目錄路徑
        project_name: 專案名稱
        incremental: 是否增量更新（預設 True）

    Returns:
        {
            'files_processed': int,
            'files_skipped': int,
            'nodes_added': int,
            'nodes_updated': int,
            'edges_added': int,
            'duration_ms': int,
            'errors': List[str]
        }
    """
    from servers.code_graph import sync_from_directory
    import time

    # 預設使用當前目錄
    project_path = project_path or os.getcwd()
    project_name = project_name or os.path.basename(os.path.abspath(project_path))

    if not os.path.isdir(project_path):
        raise ProjectNotFoundError(project_path)

    start_time = time.time()
    result = sync_from_directory(project_name, project_path, incremental=incremental)
    duration_ms = int((time.time() - start_time) * 1000)

    result['duration_ms'] = duration_ms
    return result


def status(project_path: str = None, project_name: str = None) -> Dict:
    """
    取得專案狀態總覽

    Args:
        project_path: 專案目錄路徑
        project_name: 專案名稱（預設使用目錄名）

    Returns:
        {
            'project_name': str,
            'project_path': str,
            'code_graph': {
                'node_count': int,
                'edge_count': int,
                'file_count': int,
                'kinds': {...},
                'last_sync': datetime
            },
            'skill': {
                'has_skill': bool,
                'skill_path': str,
                'flow_count': int,
                'domain_count': int,
                'api_count': int
            },
            'registry': {
                'node_kinds': int,
                'edge_kinds': int
            },
            'intent': {                       # doc-grounded 意圖層（v1）
                'has_manifest': bool,
                'active_docs': int,
                'total_docs': int
            },
            'health': 'ok' | 'warning' | 'error',
            'messages': List[str]
        }
    """
    from servers.code_graph import get_code_graph_stats
    from servers.registry import diagnose as registry_diagnose
    from servers.ssot import find_skill_dir, load_skill, parse_skill_links

    project_path = project_path or os.getcwd()
    project_name = project_name or os.path.basename(os.path.abspath(project_path))
    messages = []
    health = 'ok'

    # Code Graph 狀態
    code_graph = get_code_graph_stats(project_name)
    if code_graph['node_count'] == 0:
        health = 'warning'
        messages.append(f"Code Graph is empty. Run sync('{project_path}', '{project_name}') to populate.")

    # Registry 狀態
    registry_status = registry_diagnose()
    registry = {
        'node_kinds': registry_status.get('node_kinds_count', 0),
        'edge_kinds': registry_status.get('edge_kinds_count', 0)
    }
    if registry_status['status'] != 'ok':
        health = 'warning' if health == 'ok' else health
        messages.extend(registry_status.get('messages', []))

    # Skill 狀態（專案層級）
    skill = {
        'has_skill': False,
        'skill_path': None,
        'flow_count': 0,
        'domain_count': 0,
        'api_count': 0
    }

    skill_dir = find_skill_dir(project_path)
    if skill_dir:
        skill['has_skill'] = True
        skill['skill_path'] = skill_dir
        try:
            skill_content = load_skill(project_path)
            links = parse_skill_links(skill_content)
            # 新格式：links 是 flat list，按 section 分組
            skill['link_count'] = len(links.get('links', []))
            skill['section_count'] = len(links.get('sections', {}))
        except:
            pass
    # Intent Layer（doc-grounded，v1）：manifest 即意圖註冊表，取代 skill 生成
    intent_info = {'has_manifest': False, 'active_docs': 0, 'total_docs': 0}
    manifest_broken = False  # manifest 存在但壞掉 ≠ 未配置：別誤報「未配置」
    if project_path:
        try:
            from servers.intent import load_manifest
            mf = load_manifest(project_path)
            if mf:
                docs = [d for d in mf['docs']]
                intent_info = {
                    'has_manifest': True,
                    'total_docs': len(docs),
                    'active_docs': sum(1 for d in docs
                                       if d.get('status', 'active') == 'active'),
                }
        except Exception as e:
            manifest_broken = True
            messages.append(f"intent-manifest.json 無法使用：{e}")

    if not skill_dir and not intent_info['has_manifest'] and not manifest_broken:
        messages.append(
            "意圖層未配置（選用）：在專案根放 intent-manifest.json 註冊 PRD/SA/SD "
            "即可啟用 doc-grounded drift（/han:drift）；不需生成 Project Skill")

    return {
        'project_name': project_name,
        'project_path': project_path,
        'code_graph': code_graph,
        'skill': skill,
        'intent': intent_info,
        'registry': registry,
        'health': health,
        'messages': messages
    }


def get_context(branch: Dict, project_path: str = None, project_name: str = None) -> str:
    """
    取得 Branch 完整 context

    整合 Skill + Memory + Graph 資訊，供 Agent 使用。

    Args:
        branch: {'flow_id': 'flow.auth', 'domain_ids': ['domain.user']}
        project_path: 專案目錄路徑
        project_name: 專案名稱

    Returns:
        格式化的 context 字串
    """
    from servers.ssot import load_skill, load_flow_spec
    from servers.memory import search_memory
    from servers.graph import get_neighbors
    from servers.code_graph import get_code_nodes

    project_path = project_path or os.getcwd()
    project_name = project_name or os.path.basename(os.path.abspath(project_path))
    lines = []

    # 1. Skill 內容（核心原則）
    try:
        skill_content = load_skill(project_path)
        if skill_content:
            lines.append("## Project Skill (核心原則)")
            lines.append(skill_content[:1000] + "..." if len(skill_content) > 1000 else skill_content)
            lines.append("")
    except:
        pass

    # 2. Flow Spec
    flow_id = branch.get('flow_id')
    if flow_id:
        try:
            flow_spec = load_flow_spec(flow_id, project_path)
            if flow_spec:
                lines.append(f"## Flow Spec: {flow_id}")
                lines.append(flow_spec[:1500] + "..." if len(flow_spec) > 1500 else flow_spec)
                lines.append("")
        except:
            pass

        # 3. Graph Neighbors（SSOT 層）
        try:
            neighbors = get_neighbors(flow_id, project_name, depth=1)
            if neighbors:
                lines.append(f"## 相關節點 (SSOT Graph)")
                for n in neighbors[:10]:
                    lines.append(f"- {n['id']} ({n['kind']})")
                lines.append("")
        except:
            pass

        # 4. Code Graph（Code 層）
        try:
            # 找與此 flow 相關的程式碼
            code_nodes = get_code_nodes(project_name, limit=20)
            if code_nodes:
                lines.append(f"## Code Structure (Top Files)")
                seen_files = set()
                for n in code_nodes:
                    if n['kind'] == 'file' and n['file_path'] not in seen_files:
                        seen_files.add(n['file_path'])
                        lines.append(f"- {n['file_path']}")
                        if len(seen_files) >= 10:
                            break
                lines.append("")
        except:
            pass

    # 5. Related Memory
    try:
        query = flow_id.replace('flow.', '') if flow_id else 'general'
        memories = search_memory(query, project=project_name, limit=3)
        if memories:
            lines.append("## 相關記憶")
            for m in memories:
                lines.append(f"- **{m.get('title', 'Untitled')}**: {m.get('content', '')[:100]}...")
            lines.append("")
    except:
        pass

    return "\n".join(lines) if lines else f"No context available for branch: {branch}"


def check_drift(project_path: str = None, project_name: str = None, flow_name: str = None) -> Dict:
    """
    檢查 Skill vs Code 偏差

    Args:
        project_path: 專案目錄路徑（必要）
        project_name: 專案名稱（預設使用目錄名）
        flow_name: 特定 Flow 名稱（可選）

    Returns:
        {
            'has_drift': bool,
            'drift_count': int,
            'drifts': [
                {
                    'type': 'missing_implementation' | 'missing_spec' | 'mismatch',
                    'ssot_item': str,
                    'code_item': str,
                    'description': str,
                    'severity': str,
                    'suggestion': str
                }
            ],
            'summary': str
        }
    """
    from servers.drift import detect_all_drifts, detect_flow_drift

    project_path = project_path or os.getcwd()
    project_name = project_name or os.path.basename(os.path.abspath(project_path))

    # 使用 drift.py 的完整偵測
    if flow_name:
        # 單一 Flow 偵測
        report = detect_flow_drift(project_name, flow_name, project_path)
    else:
        # 全專案偵測
        report = detect_all_drifts(project_name, project_path)

    # 轉換為 Dict 格式
    return {
        'has_drift': report.has_drift,
        'drift_count': report.drift_count,
        'drifts': [
            {
                'id': d.id,
                'type': d.type,
                'severity': d.severity,
                'ssot_item': d.ssot_item,
                'code_item': d.code_item,
                'description': d.description,
                'suggestion': d.suggestion
            }
            for d in report.drifts
        ],
        'summary': report.summary,
        'checked_at': report.checked_at.isoformat() if report.checked_at else None
    }


# =============================================================================
# Story 15: PFC Three-Layer Query
# =============================================================================

def get_full_context(branch: Dict, project_path: str = None, project_name: str = None) -> Dict:
    """
    取得 Branch 完整三層 context（結構化版本）

    供 PFC 規劃任務時使用，整合：
    - Skill 層（意圖）- SKILL.md, flow_spec
    - Code Graph 層（現實）- related_files, dependencies
    - Memory 層（經驗）- 相關記憶
    - Drift: 偏差檢測

    Args:
        branch: {'flow_id': 'flow.auth', 'domain_ids': ['domain.user']}
        project_path: 專案目錄路徑
        project_name: 專案名稱

    Returns:
        {
            'branch': {...},
            'skill': {
                'content': str,
                'flow_spec': str,
                'related_nodes': [...]
            },
            'code': {
                'related_files': [...],
                'dependencies': [...]
            },
            'memory': [...],
            'drift': {
                'has_drift': bool,
                'drifts': [...]
            }
        }
    """
    from servers.ssot import load_skill, load_flow_spec
    from servers.memory import search_memory
    from servers.graph import get_neighbors, get_node
    from servers.code_graph import get_code_nodes, get_code_edges

    project_path = project_path or os.getcwd()
    project_name = project_name or os.path.basename(os.path.abspath(project_path))
    flow_id = branch.get('flow_id')
    domain_ids = branch.get('domain_ids', [])

    result = {
        'branch': branch,
        'project_name': project_name,
        'project_path': project_path,
        'skill': {
            'content': None,
            'flow_spec': None,
            'related_nodes': []
        },
        'code': {
            'related_files': [],
            'dependencies': []
        },
        'memory': [],
        'drift': {
            'has_drift': False,
            'drifts': []
        }
    }
    # Backward-compatible alias for older callers/tests that still name the
    # intent layer "ssot". Keep it as the same object so mutations stay in sync.
    result['ssot'] = result['skill']

    # 1. Skill 層
    try:
        result['skill']['content'] = load_skill(project_path)
    except:
        pass

    if flow_id:
        try:
            result['skill']['flow_spec'] = load_flow_spec(flow_id, project_path)
        except:
            pass

        try:
            neighbors = get_neighbors(flow_id, project_name, depth=2)
            result['skill']['related_nodes'] = neighbors
        except:
            pass

    # 2. Code Graph 層
    try:
        # 取得相關檔案
        code_nodes = get_code_nodes(project_name, limit=50)

        # 如果有 flow_id，過濾相關的檔案
        if flow_id:
            flow_name = flow_id.replace('flow.', '').replace('-', '_')
            related = [n for n in code_nodes
                      if flow_name.lower() in n.get('file_path', '').lower()
                      or flow_name.lower() in n.get('name', '').lower()]
            result['code']['related_files'] = related[:20]
        else:
            result['code']['related_files'] = [n for n in code_nodes if n['kind'] == 'file'][:10]

        # 取得依賴關係
        code_edges = get_code_edges(project_name, limit=50)
        result['code']['dependencies'] = code_edges

    except:
        pass

    # 3. Memory 層
    try:
        query = flow_id.replace('flow.', '') if flow_id else 'general'
        result['memory'] = search_memory(query, project=project_name, limit=5)
    except:
        pass

    # 4. Drift 檢測
    try:
        flow_name = flow_id.replace('flow.', '') if flow_id else None
        drift_result = check_drift(project_path, project_name, flow_name)
        result['drift'] = drift_result
    except:
        pass

    return result


def format_context_for_agent(context: Dict) -> str:
    """
    將結構化 context 格式化為 Agent 可讀的 Markdown

    Args:
        context: get_full_context() 的返回值

    Returns:
        格式化的 Markdown 字串
    """
    lines = []
    branch = context.get('branch', {})

    lines.append(f"# Context for Branch: {branch.get('flow_id', 'general')}")
    lines.append("")

    # Skill 層
    skill = context.get('skill', {})
    if skill.get('content'):
        lines.append("## 📜 Project Skill (核心原則)")
        content = skill['content']
        lines.append(content[:800] + "..." if len(content) > 800 else content)
        lines.append("")

    if skill.get('flow_spec'):
        lines.append(f"## 📋 Flow Spec: {branch.get('flow_id')}")
        spec = skill['flow_spec']
        lines.append(spec[:1200] + "..." if len(spec) > 1200 else spec)
        lines.append("")

    if skill.get('related_nodes'):
        lines.append("## 🔗 Related Skill Nodes")
        for n in skill['related_nodes'][:10]:
            direction = "→" if n.get('direction') == 'outgoing' else "←"
            lines.append(f"- {direction} [{n.get('edge_kind', '?')}] {n['id']} ({n.get('kind', '?')})")
        lines.append("")

    # Code 層
    code = context.get('code', {})
    if code.get('related_files'):
        lines.append("## 💻 Related Code Files")
        for f in code['related_files'][:10]:
            lines.append(f"- [{f['kind']}] {f.get('file_path', f['name'])}")
        lines.append("")

    # Memory 層
    memories = context.get('memory', [])
    if memories:
        lines.append("## 🧠 Related Memory")
        for m in memories:
            title = m.get('title', 'Untitled')
            content = m.get('content', '')[:100]
            lines.append(f"- **{title}**: {content}...")
        lines.append("")

    # Drift 警告
    drift = context.get('drift', {})
    if drift.get('has_drift'):
        lines.append("## ⚠️ Drift Warning")
        lines.append(f"**{drift.get('summary', 'Drift detected')}**")
        for d in drift.get('drifts', [])[:5]:
            lines.append(f"- [{d.get('type', '?')}] {d.get('description', '')}")
        lines.append("")

    return "\n".join(lines)


# =============================================================================
# Story 16: Critic Graph-Enhanced Validation
# =============================================================================

def validate_with_graph(
    modified_files: List[str],
    branch: Dict,
    project_name: str = None
) -> Dict:
    """
    使用 Graph 做增強驗證

    供 Critic 驗證時使用，檢查：
    1. 修改的影響範圍
    2. SSOT 符合性
    3. 測試覆蓋

    Args:
        modified_files: 被修改的檔案列表
        branch: {'flow_id': 'flow.auth', ...}
        project_name: 專案名稱

    Returns:
        {
            'impact_analysis': {
                'affected_nodes': [...],
                'cross_module_impact': bool,
                'api_affected': bool
            },
            'ssot_compliance': {
                'status': 'ok' | 'warning' | 'violation',
                'checks': [...]
            },
            'test_coverage': {
                'covered': [...],
                'missing': [...]
            },
            'recommendations': [...]
        }
    """
    from servers.graph import get_impact, get_neighbors, list_nodes
    from servers.code_graph import get_code_nodes, get_code_edges

    project_name = project_name or os.path.basename(os.getcwd())
    flow_id = branch.get('flow_id')

    result = {
        'impact_analysis': {
            'affected_nodes': [],
            'cross_module_impact': False,
            'api_affected': False
        },
        'ssot_compliance': {
            'status': 'ok',
            'checks': []
        },
        'test_coverage': {
            'covered': [],
            'missing': []
        },
        'recommendations': []
    }

    # 1. 影響分析
    try:
        all_nodes = list_nodes(project_name)
        node_ids_affected = set()

        # 找出修改的檔案對應的 SSOT nodes
        for f in modified_files:
            for node in all_nodes:
                ref = node.get('ref', '')
                if ref and f in ref:
                    node_ids_affected.add(node['id'])

                    # 找出誰依賴這個 node
                    impact = get_impact(node['id'], project_name)
                    for i in impact:
                        node_ids_affected.add(i['id'])
                        result['impact_analysis']['affected_nodes'].append({
                            'id': i['id'],
                            'reason': f"depends on {node['id']} via {i.get('edge_kind', '?')}"
                        })

        # 檢查是否有 API 受影響
        result['impact_analysis']['api_affected'] = any(
            n['id'].startswith('api.') for n in result['impact_analysis']['affected_nodes']
        )

        # 檢查是否跨模組
        affected_domains = set()
        for node in all_nodes:
            if node['id'] in node_ids_affected and node['kind'] == 'domain':
                affected_domains.add(node['id'])
        result['impact_analysis']['cross_module_impact'] = len(affected_domains) > 1

    except Exception as e:
        result['recommendations'].append(f"Impact analysis failed: {str(e)}")

    # 2. SSOT 符合性
    try:
        if flow_id:
            # 檢查 flow 是否有 SSOT 定義
            flow_node = None
            for node in all_nodes:
                if node['id'] == flow_id:
                    flow_node = node
                    break

            if flow_node:
                result['ssot_compliance']['checks'].append({
                    'check': f"Flow '{flow_id}' defined in SSOT",
                    'status': 'pass'
                })
            else:
                result['ssot_compliance']['checks'].append({
                    'check': f"Flow '{flow_id}' defined in SSOT",
                    'status': 'fail',
                    'message': 'Flow not found in SSOT Index'
                })
                result['ssot_compliance']['status'] = 'warning'

            # 檢查 flow 的鄰居是否完整
            neighbors = get_neighbors(flow_id, project_name, depth=1)
            has_api = any(n['id'].startswith('api.') for n in neighbors)
            has_domain = any(n['id'].startswith('domain.') for n in neighbors)

            if not has_api:
                result['ssot_compliance']['checks'].append({
                    'check': f"Flow '{flow_id}' has implementing APIs",
                    'status': 'warning',
                    'message': 'No API implementations found'
                })

    except Exception as e:
        result['recommendations'].append(f"SSOT compliance check failed: {str(e)}")

    # 3. 測試覆蓋
    try:
        test_nodes = [n for n in all_nodes if n['kind'] == 'test']

        if flow_id:
            # 找出覆蓋這個 flow 的測試
            for test in test_nodes:
                neighbors = get_neighbors(test['id'], project_name, depth=1, direction='outgoing')
                for n in neighbors:
                    if n['id'] == flow_id and n.get('edge_kind') == 'covers':
                        result['test_coverage']['covered'].append({
                            'test': test['id'],
                            'covers': flow_id
                        })

            if not result['test_coverage']['covered']:
                result['test_coverage']['missing'].append({
                    'target': flow_id,
                    'type': 'flow',
                    'message': f"No tests found covering '{flow_id}'"
                })
                result['recommendations'].append(f"Add test coverage for flow '{flow_id}'")

    except Exception as e:
        result['recommendations'].append(f"Test coverage check failed: {str(e)}")

    # 4. 生成建議
    if result['impact_analysis']['api_affected']:
        result['recommendations'].append("⚠️ API affected - consider backward compatibility")

    if result['impact_analysis']['cross_module_impact']:
        result['recommendations'].append("⚠️ Cross-module impact - coordinate with other teams")

    if result['ssot_compliance']['status'] != 'ok':
        result['recommendations'].append("📝 Update SSOT Index to match implementation")

    return result


def format_validation_report(validation: Dict) -> str:
    """
    將驗證結果格式化為 Markdown 報告

    Args:
        validation: validate_with_graph() 的返回值

    Returns:
        格式化的 Markdown 字串
    """
    lines = []
    lines.append("# 🔍 Critic Validation Report")
    lines.append("")

    # 影響分析
    impact = validation.get('impact_analysis', {})
    lines.append("## Impact Analysis")
    lines.append(f"- API Affected: {'⚠️ Yes' if impact.get('api_affected') else '✅ No'}")
    lines.append(f"- Cross-Module: {'⚠️ Yes' if impact.get('cross_module_impact') else '✅ No'}")

    affected = impact.get('affected_nodes', [])
    if affected:
        lines.append(f"- Affected Nodes: {len(affected)}")
        for n in affected[:5]:
            lines.append(f"  - {n['id']}: {n.get('reason', '')}")
    lines.append("")

    # SSOT 符合性
    ssot = validation.get('ssot_compliance', {})
    status_emoji = {'ok': '✅', 'warning': '⚠️', 'violation': '❌'}.get(ssot.get('status', 'ok'), '?')
    lines.append(f"## SSOT Compliance: {status_emoji} {ssot.get('status', 'unknown').upper()}")
    for check in ssot.get('checks', []):
        check_emoji = {'pass': '✅', 'fail': '❌', 'warning': '⚠️'}.get(check.get('status', '?'), '?')
        lines.append(f"- {check_emoji} {check.get('check', '')}")
        if check.get('message'):
            lines.append(f"  {check['message']}")
    lines.append("")

    # 測試覆蓋
    tests = validation.get('test_coverage', {})
    lines.append("## Test Coverage")
    covered = tests.get('covered', [])
    missing = tests.get('missing', [])
    lines.append(f"- Covered: {len(covered)}")
    for c in covered:
        lines.append(f"  - ✅ {c['test']} covers {c['covers']}")
    lines.append(f"- Missing: {len(missing)}")
    for m in missing:
        lines.append(f"  - ❌ {m['message']}")
    lines.append("")

    # 建議
    recommendations = validation.get('recommendations', [])
    if recommendations:
        lines.append("## Recommendations")
        for r in recommendations:
            lines.append(f"- {r}")
        lines.append("")

    return "\n".join(lines)


# =============================================================================
# Skill Graph 同步
# =============================================================================

def sync_skill_graph(project_path: str = None, project_name: str = None) -> Dict:
    """
    同步專案 SKILL.md 到 project_nodes/project_edges

    從 SKILL.md 解析所有連結，同步到 Graph。
    動態支援任何類型（不寫死在程式碼中）。

    Args:
        project_path: 專案目錄路徑
        project_name: 專案名稱（預設使用目錄名）

    Returns:
        {
            'project_name': str,
            'project_path': str,
            'nodes_added': int,
            'edges_added': int,
            'types_found': List[str],
            'total_nodes': int,
            'total_edges': int
        }
    """
    from servers.ssot import load_skill, parse_skill_links, find_skill_dir
    from servers.graph import sync_from_index, get_graph_stats

    project_path = project_path or os.getcwd()
    project_name = project_name or os.path.basename(os.path.abspath(project_path))

    # 檢查專案 Skill 是否存在
    skill_dir = find_skill_dir(project_path)
    if not skill_dir:
        return {
            'project_name': project_name,
            'project_path': project_path,
            'nodes_added': 0,
            'edges_added': 0,
            'types_found': [],
            'total_nodes': 0,
            'total_edges': 0,
            'message': f'No legacy Skill found for {project_name}. 注意：doc-grounded 意圖請改用 intent-manifest.json（/han:drift 會自動使用），不需生成 Skill'
        }

    # 解析 SKILL.md 連結
    skill_content = load_skill(project_path)
    parsed = parse_skill_links(skill_content)
    links = parsed.get('links', [])
    sections = parsed.get('sections', {})

    if not links:
        return {
            'project_name': project_name,
            'project_path': project_path,
            'nodes_added': 0,
            'edges_added': 0,
            'types_found': [],
            'total_nodes': 0,
            'total_edges': 0,
            'message': 'SKILL.md has no links defined'
        }

    # 轉換為 sync_from_index 期望的格式
    # 所有連結都當作 'doc' 類型
    index_data = {
        'docs': [
            {
                'id': f"doc.{link['name'].lower().replace(' ', '_').replace('.', '_')}",
                'name': link['name'],
                'path': link['path'],
                'section': link.get('section', '')
            }
            for link in links
        ]
    }

    # 同步到 Graph
    result = sync_from_index(project_name, index_data)

    # 取得最終統計
    stats = get_graph_stats(project_name)

    return {
        'project_name': project_name,
        'project_path': project_path,
        'nodes_added': result['nodes_added'],
        'edges_added': result['edges_added'],
        'types_found': list(sections.keys()),  # 返回 section 名稱
        'total_nodes': stats['node_count'],
        'total_edges': stats['edge_count']
    }


# 向下相容別名
sync_ssot_graph = sync_skill_graph


# =============================================================================
# 便利函數
# =============================================================================

def quick_status(project_path: str = None) -> str:
    """快速狀態報告（供 CLI 使用）"""
    try:
        s = status(project_path)
        lines = [
            f"Project: {s['project_name']}",
            f"Path: {s['project_path']}",
            f"Health: {s['health']}",
            f"",
            f"Code Graph:",
            f"  Nodes: {s['code_graph']['node_count']}",
            f"  Edges: {s['code_graph']['edge_count']}",
            f"  Files: {s['code_graph']['file_count']}",
            f"",
            f"Skill (legacy):",
            f"  Has Skill: {'✅' if s['skill']['has_skill'] else '❌'}",
            f"  Path: {s['skill']['skill_path'] or 'N/A'}",
            f"  Flows: {s['skill']['flow_count']}",
            f"  Domains: {s['skill']['domain_count']}",
            f"  APIs: {s['skill']['api_count']}",
            f"",
            f"Intent:",
            (f"  Manifest: ✅ ({s['intent']['active_docs']} active / "
             f"{s['intent']['total_docs']} docs)"
             if s.get('intent', {}).get('has_manifest')
             else "  Manifest: ❌（選用：放 intent-manifest.json 啟用 doc-grounded drift）"),
        ]
        if s['messages']:
            lines.append("")
            lines.append("Messages:")
            for msg in s['messages']:
                lines.append(f"  ⚠️ {msg}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error: {str(e)}"


# =============================================================================
# Task Lifecycle Management（任務生命週期強制機制）
# =============================================================================

# 最大重試次數
MAX_RETRIES = 3


def finish_task(
    task_id: str,
    success: bool,
    result: str = None,
    error: str = None,
    skip_validation: bool = False
) -> Dict:
    """
    任務結束的標準流程（強制執行）

    Executor 完成任務時必須呼叫此函數，自動處理：
    1. 更新 task.status
    2. 記錄 agent_log
    3. 推進 task.phase

    注意：executor_agent_id 由主對話在派發後記錄，不由 Executor 自己記錄。

    Args:
        task_id: 任務 ID
        success: 是否成功
        result: 成功結果描述
        error: 失敗原因
        skip_validation: Opt-out 開關 - 跳過驗證階段

    Returns:
        {
            'status': 'done' | 'failed',
            'phase': 'validation' | 'documentation' | 'execution',
            'next_action': 'await_validation' | 'proceed' | 'retry',
            'message': str
        }

    Example:
        # Executor 結束時
        result = finish_task(
            task_id='abc123',
            success=True,
            result='完成測試撰寫，新增 5 個測試案例'
        )
    """
    from servers.tasks import (
        get_task, update_task_status,
        advance_task_phase, log_agent_action
    )

    # 取得任務
    task = get_task(task_id)
    if not task:
        return {
            'status': 'error',
            'phase': None,
            'next_action': 'check_task_id',
            'message': f"Task not found: {task_id}"
        }

    # 1. 更新狀態
    if success:
        update_task_status(task_id, 'done', result=result)
        status = 'done'
    else:
        update_task_status(task_id, 'failed', error=error)
        status = 'failed'

    # 2. 記錄 log
    action = 'complete' if success else 'failed'
    message = result if success else error
    log_agent_action('executor', task_id, action, message or '')

    # 4. 決定下一步
    if not success:
        return {
            'status': status,
            'phase': task.get('phase', 'execution'),
            'next_action': 'retry',
            'message': f"Task failed: {error}"
        }

    # 5. 推進 phase
    requires_validation = task.get('requires_validation', True)

    if skip_validation or not requires_validation:
        # 跳過驗證，直接到 documentation
        advance_task_phase(task_id, 'documentation')
        log_agent_action('system', task_id, 'skip_validation',
                        f"skip_validation={skip_validation}, requires_validation={requires_validation}")
        return {
            'status': status,
            'phase': 'documentation',
            'next_action': 'proceed',
            'message': 'Task completed, validation skipped'
        }
    else:
        # 需要驗證
        advance_task_phase(task_id, 'validation')
        return {
            'status': status,
            'phase': 'validation',
            'next_action': 'await_validation',
            'message': 'Task completed, awaiting validation'
        }


def finish_validation(
    task_id: str,
    original_task_id: str,
    approved: bool,
    issues: List[str] = None,
    suggestions: List[str] = None
) -> Dict:
    """
    Critic 驗證結束的標準流程（強制執行）

    Critic 完成驗證時必須呼叫此函數，自動處理：
    1. 更新 Critic 任務狀態為 done
    2. 呼叫 mark_validated() 更新原任務
    3. 如果 rejected，回傳 resume 指令

    Args:
        task_id: Critic 任務 ID
        original_task_id: 被驗證的原任務 ID
        approved: 是否通過
        issues: 發現的問題列表
        suggestions: 改進建議

    Returns:
        若 approved:
            {
                'status': 'approved',
                'original_task_phase': 'documentation',
                'next_action': 'proceed',
                'message': str
            }
        若 rejected:
            {
                'status': 'rejected',
                'original_task_phase': 'execution',
                'next_action': 'resume_executor',
                'resume_agent_id': str,  # 原 Executor 的 agentId
                'rejection_context': {
                    'issues': [...],
                    'suggestions': [...],
                    'retry_count': int
                },
                'message': str
            }
        若超過重試次數:
            {
                'status': 'blocked',
                'next_action': 'human_review',
                'message': str
            }

    Example:
        # Critic 驗證通過
        result = finish_validation(
            task_id='critic-001',
            original_task_id='task-abc',
            approved=True
        )

        # Critic 驗證不通過
        result = finish_validation(
            task_id='critic-002',
            original_task_id='task-xyz',
            approved=False,
            issues=['測試覆蓋率只有 60%', '缺少邊界測試'],
            suggestions=['新增 edge case 測試', '提高覆蓋率到 80%']
        )

        # PFC 根據 result['next_action'] 決定下一步
        if result['next_action'] == 'resume_executor':
            # Resume 原 Executor
            Task(resume=result['resume_agent_id'], prompt=f"修復問題: {result['rejection_context']}")
    """
    import json
    from servers.tasks import (
        get_task, update_task, update_task_status,
        advance_task_phase, mark_validated, log_agent_action
    )

    # 取得原任務
    original_task = get_task(original_task_id)
    if not original_task:
        return {
            'status': 'error',
            'next_action': 'check_task_id',
            'message': f"Original task not found: {original_task_id}"
        }

    # 1. 更新 Critic 任務狀態
    update_task_status(task_id, 'done', result=f"Validation: {'approved' if approved else 'rejected'}")

    # 2. 處理驗證結果
    if approved:
        # 標記通過
        mark_validated(original_task_id, 'approved', validator_task_id=task_id)
        advance_task_phase(original_task_id, 'documentation')

        log_agent_action('critic', original_task_id, 'approved', 'Validation passed')

        return {
            'status': 'approved',
            'original_task_phase': 'documentation',
            'next_action': 'proceed',
            'message': f"Task {original_task_id} validation passed"
        }

    else:
        # 標記 rejected
        executor_agent_id = original_task.get('executor_agent_id')
        retry_count = (original_task.get('rejection_count') or 0) + 1

        # 檢查重試次數
        if retry_count >= MAX_RETRIES:
            update_task_status(original_task_id, 'blocked',
                              error=f'Exceeded {MAX_RETRIES} validation retries')
            mark_validated(original_task_id, 'rejected', validator_task_id=task_id)

            log_agent_action('critic', original_task_id, 'blocked',
                            f'Exceeded {MAX_RETRIES} retries, needs human review')
            from servers.reviews import create_review_item
            review_id = create_review_item(
                kind='critic_rejection',
                reason=f'Exceeded {MAX_RETRIES} validation retries',
                project=original_task.get('project'),
                severity='high',
                task_id=original_task_id,
                payload={
                    'critic_task_id': task_id,
                    'issues': issues or [],
                    'suggestions': suggestions or [],
                    'retry_count': retry_count,
                },
            )

            return {
                'status': 'blocked',
                'next_action': 'human_review',
                'review_id': review_id,
                'message': f"Task {original_task_id} exceeded {MAX_RETRIES} retries, needs human review"
            }

        # 更新任務狀態
        update_task(original_task_id, rejection_count=retry_count)
        update_task_status(original_task_id, 'pending')
        advance_task_phase(original_task_id, 'execution')
        mark_validated(original_task_id, 'rejected', validator_task_id=task_id)

        # 記錄 log
        log_agent_action('critic', original_task_id, 'rejected',
                        json.dumps({'issues': issues or [], 'suggestions': suggestions or []}))

        return {
            'status': 'rejected',
            'original_task_phase': 'execution',
            'next_action': 'resume_executor',
            'resume_agent_id': executor_agent_id,
            'rejection_context': {
                'issues': issues or [],
                'suggestions': suggestions or [],
                'retry_count': retry_count
            },
            'message': f"Task {original_task_id} rejected (attempt {retry_count}/{MAX_RETRIES})"
        }


def run_validation_cycle(
    parent_id: str,
    mode: str = 'normal',
    sample_count: int = 3
) -> Dict:
    """
    執行一輪驗證循環

    PFC 在階段完成後呼叫，自動處理：
    1. 抓取待驗證任務
    2. 根據 mode 決定如何驗證
    3. 回傳驗證狀態

    Args:
        parent_id: 父任務 ID
        mode: 驗證模式
            - 'normal': 對每個任務派發 Critic（預設）
            - 'batch_approve': 批量自動通過（緊急情況）
            - 'batch_skip': 批量跳過驗證
            - 'sample': 只抽樣驗證前 N 個，其餘 auto-approve
        sample_count: sample 模式時驗證的數量

    Returns:
        {
            'total': int,
            'validated': int,
            'approved': int,
            'rejected': int,
            'skipped': int,
            'pending_validation': [task_id, ...],  # 需要派發 Critic 的任務
            'message': str
        }

    Example:
        # 標準驗證
        result = run_validation_cycle(parent_id='task-main')

        # 緊急情況批量通過
        result = run_validation_cycle(parent_id='task-main', mode='batch_approve')

        # 抽樣驗證
        result = run_validation_cycle(parent_id='task-main', mode='sample', sample_count=5)
    """
    from servers.tasks import (
        get_unvalidated_tasks, get_validation_summary,
        mark_validated, log_agent_action
    )

    # 取得待驗證任務
    unvalidated = get_unvalidated_tasks(parent_id)

    result = {
        'total': len(unvalidated),
        'validated': 0,
        'approved': 0,
        'rejected': 0,
        'skipped': 0,
        'pending_validation': [],
        'message': ''
    }

    if not unvalidated:
        result['message'] = 'No tasks pending validation'
        return result

    # 根據 mode 處理
    if mode == 'batch_approve':
        # 批量自動通過
        for task in unvalidated:
            mark_validated(task['id'], 'approved', validator_task_id='system:batch_approve')
            log_agent_action('system', task['id'], 'batch_approve', 'Auto-approved in batch mode')
            result['approved'] += 1
            result['validated'] += 1
        result['message'] = f"Batch approved {result['approved']} tasks"

    elif mode == 'batch_skip':
        # 批量跳過
        for task in unvalidated:
            mark_validated(task['id'], 'skipped', validator_task_id='system:batch_skip')
            log_agent_action('system', task['id'], 'batch_skip', 'Skipped in batch mode')
            result['skipped'] += 1
            result['validated'] += 1
        result['message'] = f"Batch skipped {result['skipped']} tasks"

    elif mode == 'sample':
        # 抽樣驗證
        to_validate = unvalidated[:sample_count]
        to_auto_approve = unvalidated[sample_count:]

        # 標記需要驗證的
        for task in to_validate:
            result['pending_validation'].append(task['id'])

        # 自動通過其餘的
        for task in to_auto_approve:
            mark_validated(task['id'], 'approved', validator_task_id='system:sample_auto')
            log_agent_action('system', task['id'], 'sample_auto',
                            f'Auto-approved (sampled {sample_count} for manual review)')
            result['approved'] += 1
            result['validated'] += 1

        result['message'] = f"Sampled {len(to_validate)} for review, auto-approved {len(to_auto_approve)}"

    else:  # normal
        # 標準模式：所有任務都需要 Critic
        for task in unvalidated:
            result['pending_validation'].append(task['id'])

        result['message'] = f"{len(unvalidated)} tasks pending Critic review"

    return result


def manual_validate(task_id: str, status: str, reviewer: str) -> Dict:
    """
    人類手動驗證（繞過 Critic）

    用於人類已經 review 過程式碼的情況。

    Args:
        task_id: 任務 ID
        status: 'approved' | 'rejected' | 'skipped'
        reviewer: 審核者名稱（記錄用）

    Returns:
        {
            'status': str,
            'phase': str,
            'message': str
        }
    """
    from servers.tasks import (
        get_task, mark_validated, advance_task_phase, log_agent_action
    )

    task = get_task(task_id)
    if not task:
        return {
            'status': 'error',
            'phase': None,
            'message': f"Task not found: {task_id}"
        }

    # 標記驗證結果
    mark_validated(task_id, status, validator_task_id=f'human:{reviewer}')
    log_agent_action(f'human:{reviewer}', task_id, f'manual_{status}', f'Manual review by {reviewer}')

    try:
        from servers.reviews import list_review_items, resolve_review_item
        for item in list_review_items(status='open', limit=100):
            if item.get('task_id') == task_id:
                resolve_review_item(
                    item['id'],
                    reviewer=reviewer,
                    resolution=f'manual_{status}',
                    notes=f"Task {task_id} manually {status}",
                )
    except Exception:
        pass

    # 推進 phase
    if status == 'approved':
        advance_task_phase(task_id, 'documentation')
        phase = 'documentation'
    elif status == 'rejected':
        advance_task_phase(task_id, 'execution')
        phase = 'execution'
    else:  # skipped
        advance_task_phase(task_id, 'documentation')
        phase = 'documentation'

    return {
        'status': status,
        'phase': phase,
        'message': f"Task {task_id} manually {status} by {reviewer}"
    }


# =============================================================================
# Dispatch Loop — 自動化 agent 派發
# =============================================================================

# Agent → model tier mapping
_AGENT_TIERS = {
    'pfc': 'planner',
    'executor': 'worker',
    'critic': 'worker',
    'researcher': 'worker',
    'memory': 'fast',
    'drift-detector': 'fast',
}


def get_next_dispatch(
    parent_id: str,
    project_name: str,
    project_path: str,
    trace_id: str = None
) -> Dict:
    """取得下一個要派發的 agent 指令

    讀取 DB 狀態，返回結構化的派發指令。主對話拿到指令後用 Task tool 執行。
    重複呼叫直到 action='done'。

    Args:
        parent_id: 根任務 ID（epic 或 parent task）
        project_name: 專案名稱
        project_path: 專案目錄路徑
        trace_id: 可選，本地 trace id。提供時會記錄 dispatch/status span。

    Returns:
        {
            'action': 'dispatch' | 'done' | 'blocked' | 'waiting',
            'subagent_type': str,      # action=dispatch 時
            'model_tier': str,         # 'planner' | 'worker' | 'fast'
            'prompt': str,             # 完整 prompt
            'task_id': str,            # 追蹤用
            'progress': str,           # e.g. '3/7 tasks complete'
            'message': str,            # 人類可讀狀態
        }
    """
    from servers.tasks import (
        get_task, get_next_task, get_unvalidated_tasks,
        get_task_progress, get_epic_tasks, get_story_tasks,
        reserve_critic_task,
    )

    # 取得根任務，判斷是否為 epic
    root_task = get_task(parent_id)
    if not root_task:
        return _record_dispatch_decision(trace_id, parent_id, project_name, project_path, {
            'action': 'done',
            'progress': '0/0',
            'message': f'Task not found: {parent_id}',
        })

    is_epic = root_task.get('task_level') == 'epic'

    # 收集所有要處理的 parent_ids（epic → stories, 否則就是 parent_id 本身）
    story_ids = []
    if is_epic:
        stories = get_epic_tasks(root_task.get('project', project_name), parent_id)
        for epic in stories:
            for story in epic.get('stories', []):
                story_ids.append(story['id'])
        if not story_ids:
            story_ids = [parent_id]
    else:
        story_ids = [parent_id]

    # 彙整所有子任務的進度
    total_done = 0
    total_all = 0

    # 1. 檢查待驗證任務（優先處理）
    for sid in story_ids:
        unvalidated = get_unvalidated_tasks(sid)
        if unvalidated:
            task = unvalidated[0]
            # 原子性保留 critic 任務（幂等：重複呼叫返回同一 critic）
            critic_task = reserve_critic_task(task['id'])
            if not critic_task:
                continue

            progress = get_task_progress(sid)
            total_all += progress['total']
            total_done += progress['done']

            critic_prompt = _build_critic_prompt(
                critic_task, project_name, project_path
            )
            return _record_dispatch_decision(trace_id, parent_id, project_name, project_path, {
                'action': 'dispatch',
                'subagent_type': 'critic',
                'model_tier': _AGENT_TIERS['critic'],
                'prompt': critic_prompt,
                'task_id': critic_task['id'],
                'original_task_id': critic_task['original_task_id'],
                'progress': f'{total_done}/{total_all} tasks complete',
                'message': f"Validating: {task['description'][:60]}",
            })

    # 2. 檢查被 reject 需重做的任務
    for sid in story_ids:
        rejected = _get_rejected_tasks(sid)
        if rejected:
            task = rejected[0]
            progress = get_task_progress(sid)
            prompt = _build_executor_prompt(
                task, project_name, project_path,
                rejection_context=task.get('_rejection_context')
            )
            return _record_dispatch_decision(trace_id, parent_id, project_name, project_path, {
                'action': 'dispatch',
                'subagent_type': 'executor',
                'model_tier': _AGENT_TIERS['executor'],
                'prompt': prompt,
                'task_id': task['id'],
                'progress': f"{progress['done']}/{progress['total']} tasks complete",
                'message': f"Retrying: {task['description'][:60]}",
            })

    # 3. 檢查下一個 pending 任務
    for sid in story_ids:
        next_task = get_next_task(sid)
        if next_task:
            progress = get_task_progress(sid)
            prompt = _build_executor_prompt(
                next_task, project_name, project_path
            )
            return _record_dispatch_decision(trace_id, parent_id, project_name, project_path, {
                'action': 'dispatch',
                'subagent_type': next_task.get('assigned_agent', 'executor'),
                'model_tier': _AGENT_TIERS.get(
                    next_task.get('assigned_agent', 'executor'), 'worker'
                ),
                'prompt': prompt,
                'task_id': next_task['id'],
                'progress': f"{progress['done']}/{progress['total']} tasks complete",
                'message': f"Executing: {next_task['description'][:60]}",
            })

    # 4. 統計總進度
    for sid in story_ids:
        progress = get_task_progress(sid)
        total_all += progress['total']
        total_done += progress['done']

    # 5. 全部完成？→ 派 memory agent（依狀態決定）
    if total_done >= total_all and total_all > 0:
        memory_task = _get_memory_task(parent_id)
        if not memory_task:
            # 尚未建立 memory task，建立並派發
            mem_task_id, mem_prompt = _build_memory_prompt(
                parent_id, project_name
            )
            return _record_dispatch_decision(trace_id, parent_id, project_name, project_path, {
                'action': 'dispatch',
                'subagent_type': 'memory',
                'model_tier': _AGENT_TIERS['memory'],
                'prompt': mem_prompt,
                'task_id': mem_task_id,
                'progress': f'{total_done}/{total_all} tasks complete',
                'message': 'All tasks done. Storing lessons learned.',
            })
        if memory_task['status'] in ('pending', 'running'):
            return _record_dispatch_decision(trace_id, parent_id, project_name, project_path, {
                'action': 'waiting',
                'progress': f'{total_done}/{total_all} tasks complete',
                'message': 'All tasks done. Waiting for memory task to finish.',
            })
        if memory_task['status'] == 'done':
            return _record_dispatch_decision(trace_id, parent_id, project_name, project_path, {
                'action': 'done',
                'progress': f'{total_done}/{total_all} tasks complete',
                'message': 'All tasks completed and validated.',
            })
        # failed/blocked
        return _record_dispatch_decision(trace_id, parent_id, project_name, project_path, {
            'action': 'blocked',
            'progress': f'{total_done}/{total_all} tasks complete',
            'message': f"Memory task is {memory_task['status']}.",
        })

    # 6. 有 blocked 的？
    blocked = [sid for sid in story_ids
               if get_task_progress(sid).get('failed', 0) > 0]
    if blocked:
        return _record_dispatch_decision(trace_id, parent_id, project_name, project_path, {
            'action': 'blocked',
            'progress': f'{total_done}/{total_all} tasks complete',
            'message': f'{len(blocked)} stories have blocked/failed tasks.',
        })

    # 7. 都在跑中
    return _record_dispatch_decision(trace_id, parent_id, project_name, project_path, {
        'action': 'waiting',
        'progress': f'{total_done}/{total_all} tasks complete',
        'message': 'Tasks are running. Call again after agent completes.',
    })


def _gate_reject(critic_task_id: str, original_task_id: str,
                 issues: List[str]) -> Dict:
    """確定性退件。

    關鍵：executor 重試 prompt 的 rejection context 來自 working_memory['critic_suggestions']
    （見 _get_rejected_tasks），而 finish_validation **不寫**此鍵。故 gate 必須先自己寫，
    行號才會出現在 executor 重試 prompt 裡。值為字串（直接被注入 prompt）。

    重試上限：達 MAX_RETRIES 時 finish_validation 會把原任務設為 'blocked' 並回
    status='blocked'/next_action='human_review'（servers/facade.py finish_validation 的
    rejected 分支）。但 get_next_dispatch 的 blocked 偵測只看 get_task_progress(...).failed>0，
    **抓不到 'blocked' 狀態** → 會誤判成 'waiting'（任務卡死、無人察覺）。故 gate 必須自己把
    blocked 往上帶，讓 get_next_dispatch_gated 直接回 action='blocked'（指向 human_review）。
    """
    from servers.memory import set_working_memory
    set_working_memory(original_task_id, 'critic_suggestions', '\n'.join(issues))
    fv = finish_validation(critic_task_id, original_task_id, approved=False, issues=issues) or {}
    if fv.get('status') == 'blocked':
        return {'verdict': 'blocked', 'issues': issues,
                'message': fv.get('message'), 'review_id': fv.get('review_id')}
    return {'verdict': 'rejected', 'issues': issues}


def _java_test_file_to_fq_classname(test_file: str) -> str:
    """Convert a Java/Kotlin test file path to a fully-qualified class name.

    C4 fix: strips leading src/test/java (or src/test/kotlin) prefix,
    removes the .java/.kt extension, and replaces '/' with '.'.

    Examples:
        'src/test/java/demo/FooTest.java'          -> 'demo.FooTest'
        'src/test/kotlin/com/example/BazSpec.kt'   -> 'com.example.BazSpec'
        '**/src/test/java/com/example/BarTest.java' -> 'com.example.BarTest'
    """
    import re
    path = test_file.replace('\\', '/').lstrip('*').lstrip('/')
    # Strip leading src/test/{java,kotlin}/ or test/{java,kotlin}/
    path = re.sub(
        r'^(?:.*?/)?src/test/(?:java|kotlin)/',
        '',
        path,
    )
    # Also handle bare test/java/ or test/kotlin/ prefixes
    path = re.sub(r'^(?:.*?/)?test/(?:java|kotlin)/', '', path)
    # Drop extension
    for ext in ('.java', '.kt'):
        if path.endswith(ext):
            path = path[:-len(ext)]
            break
    return path.replace('/', '.')


def _derive_java_test_filters(test_targets: List[str]) -> List[str]:
    """Return FQ class names for any Java/Kotlin test file paths in test_targets.

    C4: Used to scope the Gradle test run to only the tests written for the
    target, preventing unrelated tests from covering the branch (false green).
    Non-Java paths are silently ignored.
    """
    filters = []
    for tf in (test_targets or []):
        tf_norm = tf.replace('\\', '/')
        if (('src/test/java/' in tf_norm or 'src/test/kotlin/' in tf_norm
             or tf_norm.endswith('.java') or tf_norm.endswith('.kt'))
                and '/test/' in tf_norm):
            fq = _java_test_file_to_fq_classname(tf_norm)
            if fq:
                filters.append(fq)
    return filters


def run_coverage_gate(critic_task_id: str,
                      original_task_id: str,
                      project_name: str,
                      project_path: str) -> Dict:
    """對一個待派 critic 的任務跑分支覆蓋率 gate。

    fail-state 分流（對齊「工具確認、非 LLM 宣稱」）：
      - 有未覆蓋分支 / 測試失敗(tests_failed) / 沒測到(no_targets) / 推不出測試檔
        → **確定性退件**（_gate_reject：寫 critic_suggestions + finish_validation），
        回 {'verdict':'rejected'}；若該退件令原任務達 MAX_RETRIES，_gate_reject 改回
        {'verdict':'blocked', ...}（上游轉 action='blocked' → human_review）。
      - 全覆蓋 → {'verdict':'proceed', 'warn': None}。
      - 真正 infra（coverage 未安裝 / unavailable）→ fail-open，回
        {'verdict':'proceed', 'warn': <警示字串>}（上游把警示前置到 critic prompt）。

    C5 fix: backend selection happens FIRST, then per-backend availability is
    checked — Java availability = gradlew exists; Python availability =
    _coverage_available(). This prevents a Java project from fail-opening just
    because the Python 'coverage' package is not installed.

    C4 fix: for Java backend, test file paths are converted to FQ class names
    and passed as test_filters= to scope the Gradle run to the relevant tests
    only (prevents unrelated suite tests from covering the target → false green).
    """
    import sys
    from servers.tasks import get_task
    from servers import coverage as cov

    original = get_task(original_task_id)
    metadata = (original or {}).get('metadata')
    if not isinstance(metadata, dict):
        metadata = {}
    coverage_targets = metadata.get('coverage_targets')
    result_text = (original or {}).get('result') or ''

    # key 不存在 / 空 → 非 unit_test recipe 任務或無 target → 不攔，照常派 critic
    if coverage_targets is None or coverage_targets == []:
        return {'verdict': 'proceed', 'warn': None}

    # key 存在但型別壞掉（非 list[dict]）→ 這是 coverage 任務但 metadata 損毀，
    # 不可被 `or []` 靜默當成非 coverage 任務而跳過 gate（隱性繞過＝假綠）→ 確定性退件。
    if (not isinstance(coverage_targets, list)
            or not all(isinstance(t, dict) for t in coverage_targets)):
        return _gate_reject(critic_task_id, original_task_id, [
            f'分支覆蓋率 gate：coverage_targets metadata 型別異常'
            f'（預期 list[dict]，得到 {type(coverage_targets).__name__}）。'])

    # ── C5: Select backend FIRST, then check per-backend availability ──────────
    # Resolve tech_stack from the project record (same approach as recipes.py).
    # select_backend: 'java' → JaCoCo/Gradle backend; 'python' or 'unknown' → pytest/coverage.
    from servers import coverage_java as cov_java
    from servers import project as _proj
    _ts = (_proj.ensure_project(project_name, project_path).get('tech_stack') or {})
    _backend = cov_java.select_backend(_ts)

    if _backend != 'java':
        # Python / unknown stack: check python-coverage availability (infra check).
        # Java stack does NOT go through this check — gradlew absence is handled
        # fail-closed by measure_branch_coverage_java itself (returns test_run_error).
        if not cov._coverage_available():
            return {'verdict': 'proceed',
                    'warn': '⚠️ coverage 套件未安裝，本任務回退人工逐分支核對。'}

    sys.stderr.write('… 量測分支覆蓋率中 …\n')  # UX：pytest 較久時別讓使用者以為當機

    test_targets = cov.derive_test_targets(project_path, result_text, coverage_targets)
    if not test_targets:
        # 推不出測試檔 → 確定性退件，要求 executor 用 marker 回報
        return _gate_reject(critic_task_id, original_task_id, [
            '未能確認你寫的測試檔。請在回報中以**獨立一行** '
            '`TEST_TARGETS: <相對專案根路徑>, ...` 列出本次測試檔，gate 才能量測分支覆蓋。'])

    if _backend == 'java':
        # C4: Derive FQ class-name filters so Gradle runs ONLY the tests written
        # for this target (not the full suite) — prevents false-green coverage
        # from unrelated tests that happen to exercise the same branches.
        java_test_filters = _derive_java_test_filters(test_targets)
        res = cov_java.measure_branch_coverage_java(
            project_path, test_targets, coverage_targets,
            test_filters=java_test_filters if java_test_filters else None)
    else:
        # 'python' or 'unknown' → existing Python backend (safe default)
        res = cov.measure_branch_coverage(project_path, test_targets, coverage_targets)
    status = res['tool_status']

    if status == 'ok':
        # 人類可見：每個 target 的實際覆蓋數字＋逐條分支都印到 stderr（通過/缺口都印），
        # 讓跑 /han:unit-test 的人「看得到覆蓋率」，不再靜默通過。
        summary = cov.format_coverage_summary(res['per_target'])
        for line in summary:
            sys.stderr.write(line + '\n')
        if res['fully_covered']:
            # 同時把摘要回傳：stderr 是即時的、迴圈結束後就消失；回傳值讓 dispatch
            # 迴圈攔得到，最後能寫進收尾的人類報告（不只 stderr）。
            return {'verdict': 'proceed', 'warn': None, 'coverage_summary': summary}
        issues = cov.format_missing_issues(res['per_target'])
        issues.append('若為真正不可達/防禦性分支，請用 `# pragma: no cover` 並在回報說明理由。')
        return _gate_reject(critic_task_id, original_task_id, issues)

    # 唯一 fail-open：coverage 套件缺失（真正 infra；measure 回 'unavailable'）。
    # 此時回退人工逐分支核對（playbook critic checklist 已涵蓋）。
    if status == 'unavailable':
        return {'verdict': 'proceed',
                'warn': f"⚠️ 分支覆蓋率工具未量到（{res.get('error')}），本任務回退人工逐分支核對。"}

    # 其餘任何非 ok（tests_failed / no_targets / invalid_targets / test_run_error /
    # schema_error）＝「工具無法確認全覆蓋」→ 一律確定性退件（fail-closed），
    # 絕不 fail-open 把未驗證的覆蓋交給 LLM 宣稱。
    return _gate_reject(critic_task_id, original_task_id,
                        [f'分支覆蓋率 gate（{status}）：{res.get("error")}'])



def run_integration_gate(critic_task_id: str,
                         original_task_id: str,
                         project_name: str,
                         project_path: str) -> Dict:
    """Integration gate: L1 (run+pass) and L2 (mock-smell) are HARD gates;
    L3 (branch coverage per boundary) is ADVISORY — never changes verdict.

    Returns the same verdict contract as run_coverage_gate:
        {'verdict': 'proceed' | 'rejected' | 'blocked',
         'warn':    str | None,
         'coverage_summary': list[str]}   # present when verdict='proceed'

    Policy:
      - metadata.integration_boundaries absent/empty → not an integration task → proceed.
      - L1: run_tests(...) passes=False → _gate_reject (fail-closed).
      - L2: any boundary collaborator mocked → _gate_reject naming the collaborator.
      - L3: classify each boundary (verified-real / not-observed / not-measurable);
            result is advisory, never modifies verdict.
    """
    from servers.tasks import get_task
    import servers.integration_gate as ig
    import servers.project as proj_mod

    original = get_task(original_task_id)
    metadata = (original or {}).get('metadata')
    if not isinstance(metadata, dict):
        metadata = {}

    boundaries = metadata.get('integration_boundaries')

    # Not an integration task (absent/empty boundaries) → pass-through, no gating.
    if not boundaries:
        return {'verdict': 'proceed', 'warn': None}

    # Present but malformed (not list[dict]) → fail-closed reject (not a silent skip = 假綠).
    # Mirror run_coverage_gate's treatment of malformed coverage_targets.
    if (not isinstance(boundaries, list)
            or not all(isinstance(b, dict) for b in boundaries)):
        return _gate_reject(critic_task_id, original_task_id, [
            '整合測試 gate：integration_boundaries metadata 型別異常（預期 list[dict]）'])

    test_files = metadata.get('test_files') or []
    stack = metadata.get('stack') or 'python'

    # ── L1: Run tests — hard gate ──────────────────────────────────────────────
    l1 = ig.run_tests(project_path, stack, py_test_files=test_files)
    if not l1.get('passed'):
        error_detail = l1.get('error') or f"failures={l1.get('failures')}, errors={l1.get('errors')}"
        return _gate_reject(critic_task_id, original_task_id, [
            f'Integration gate L1: tests did not pass — {error_detail}'])

    l1_total = l1.get('total', 0)

    # ── L2: Mock-smell detection — hard gate ───────────────────────────────────
    # Read each test file and check whether boundary collaborators are mocked.
    # Fail-closed: if test_files is non-empty but zero files are readable,
    # we cannot verify absence of mocks → reject (can't verify → reject).
    all_mocked: List[str] = []
    collaborators = [b.get('callee', '') for b in boundaries if b.get('callee')]
    files_read = 0

    for tf in test_files:
        # test_files may be relative or absolute; try project_path-relative first
        tf_path = tf if os.path.isabs(tf) else os.path.join(project_path, tf)
        if not os.path.isfile(tf_path):
            continue
        try:
            with open(tf_path, 'r', encoding='utf-8', errors='replace') as fh:
                test_source = fh.read()
        except OSError:
            continue
        files_read += 1
        mocked = ig.detect_mocked_collaborators(test_source, collaborators, stack)
        for m in mocked:
            if m not in all_mocked:
                all_mocked.append(m)

    # Fail-closed: non-empty test_files but zero readable → cannot verify → reject
    if test_files and files_read == 0:
        return _gate_reject(critic_task_id, original_task_id, [
            '整合測試 gate：無法讀取任何測試原始碼以驗證未 mock 邊界（test_files 路徑不可讀）'])

    if all_mocked:
        issues = [f'fake integration: {m} is mocked' for m in all_mocked]
        return _gate_reject(critic_task_id, original_task_id, issues)

    # ── L3: Branch coverage per boundary — advisory only ──────────────────────
    # Enrich each boundary with a label; verdict is never changed by L3.
    boundaries_with_labels = []

    for b in boundaries:
        b_annotated = dict(b)
        b_annotated['_project_name'] = project_name
        label = ig._classify_boundary_l3(b_annotated, project_path, test_files)
        b_annotated['label'] = label
        boundaries_with_labels.append(b_annotated)

    summary = ig.format_boundary_summary(boundaries_with_labels, l1_total=l1_total)
    return {'verdict': 'proceed', 'warn': None, 'coverage_summary': summary}


def get_next_dispatch_gated(parent_id: str,
                            project_name: str,
                            project_path: str,
                            trace_id: str = None) -> Dict:
    """get_next_dispatch + 分支覆蓋率 gate。

    底層派 critic 時先跑 gate：確定性退件 → finish_validation 已處理，重取下一個 dispatch
    （會變成 executor 重試）；其餘照常回傳 critic（fail-open 時把警示前置到 prompt）。
    其他 action（executor/done/blocked）原樣回傳。
    防護：記住本次已跑過 gate 的 critic id，若同一 critic 又被派回（理應不會——
    finish_validation 會把任務推進到 pending/execution）→ 直接回傳避免無限迴圈。
    """
    seen_critics = set()
    while True:
        inst = get_next_dispatch(parent_id, project_name, project_path, trace_id)
        if inst.get('action') != 'dispatch' or inst.get('subagent_type') != 'critic':
            return inst
        critic_id = inst.get('task_id')
        if critic_id in seen_critics:
            return inst  # 防護：避免狀態未推進時的無限迴圈
        seen_critics.add(critic_id)
        verdict = run_coverage_gate(
            critic_id, inst['original_task_id'], project_name, project_path)
        if verdict['verdict'] == 'blocked':
            # 達 MAX_RETRIES：finish_validation 已開 human_review。直接回 blocked，
            # 不再 continue（否則 get_next_dispatch 的 blocked 偵測抓不到 'blocked' 狀態 → 誤回 waiting）。
            return {
                'action': 'blocked',
                'progress': inst.get('progress'),
                'message': verdict.get('message') or '任務已達最大驗證重試次數，需人工審查。',
                'review_id': verdict.get('review_id'),
            }
        if verdict['verdict'] == 'rejected':
            continue  # 退件已處理，迴圈重取 → executor 重試
        if verdict.get('warn'):
            inst['prompt'] = verdict['warn'] + '\n\n' + inst['prompt']
        if verdict.get('coverage_summary'):
            # 掛到回傳的 critic dispatch 上，讓 /han:unit-test 迴圈每輪攔得到，收尾寫進報告。
            inst['coverage_summary'] = verdict['coverage_summary']
        return inst


def get_next_dispatch_integration_gated(parent_id: str,
                                        project_name: str,
                                        project_path: str,
                                        trace_id: str = None) -> Dict:
    """get_next_dispatch + integration gate (L1 run+pass, L2 mock-smell).

    Mirrors get_next_dispatch_gated but calls run_integration_gate instead of
    run_coverage_gate.  When the next dispatch is a critic, the integration gate
    runs first:
      - rejected  → finish_validation already handled; continue to get next dispatch
                   (will become executor retry with collaborator name in prompt)
      - blocked   → return blocked action immediately
      - proceed   → attach coverage_summary (boundary report) and return critic dispatch
    Other actions (executor / done / blocked) are returned unchanged.
    """
    seen_critics: set = set()
    while True:
        inst = get_next_dispatch(parent_id, project_name, project_path, trace_id)
        if inst.get('action') != 'dispatch' or inst.get('subagent_type') != 'critic':
            return inst
        critic_id = inst.get('task_id')
        if critic_id in seen_critics:
            return inst  # safety: avoid infinite loop if state did not advance
        seen_critics.add(critic_id)
        verdict = run_integration_gate(
            critic_id, inst['original_task_id'], project_name, project_path)
        if verdict['verdict'] == 'blocked':
            return {
                'action': 'blocked',
                'progress': inst.get('progress'),
                'message': verdict.get('message') or '任務已達最大驗證重試次數，需人工審查。',
                'review_id': verdict.get('review_id'),
            }
        if verdict['verdict'] == 'rejected':
            continue  # retry: gate already called finish_validation
        if verdict.get('warn'):
            inst['prompt'] = verdict['warn'] + '\n\n' + inst['prompt']
        if verdict.get('coverage_summary'):
            inst['coverage_summary'] = verdict['coverage_summary']
        return inst


def _record_dispatch_decision(
    trace_id: str,
    parent_id: str,
    project_name: str,
    project_path: str,
    decision: Dict,
) -> Dict:
    """Record a dispatch/status decision without storing full prompts."""
    if not trace_id:
        return decision

    traced_decision = dict(decision)
    traced_decision['trace_id'] = trace_id

    safe_output = {
        key: value
        for key, value in traced_decision.items()
        if key != 'prompt'
    }
    if 'prompt' in traced_decision:
        safe_output['prompt_length'] = len(traced_decision.get('prompt') or '')
        safe_output['prompt_redacted'] = True

    action = traced_decision.get('action', 'unknown')
    subagent_type = traced_decision.get('subagent_type')
    span_type = 'dispatch' if action == 'dispatch' else 'workflow_status'
    span_name = f"dispatch:{subagent_type}" if subagent_type else f"dispatch:{action}"
    metadata = {
        'action': action,
        'parent_id': parent_id,
        'project': project_name,
    }
    if subagent_type:
        metadata['subagent_type'] = subagent_type
    if traced_decision.get('model_tier'):
        metadata['model_tier'] = traced_decision['model_tier']

    try:
        from servers.tracing import finish_span, start_span
        span_id = start_span(
            trace_id,
            span_name,
            span_type,
            task_id=traced_decision.get('task_id'),
            input={
                'parent_id': parent_id,
                'project_name': project_name,
                'project_path': project_path,
            },
            metadata=metadata,
        )
        finish_span(span_id, output=safe_output)
    except Exception as exc:
        traced_decision['_trace_error'] = f"{type(exc).__name__}: {exc}"

    return traced_decision


def _get_rejected_tasks(parent_id: str) -> List[Dict]:
    """取得被 reject 需重做的任務"""
    from servers import managed_connection
    with managed_connection() as db:
        cursor = db.cursor()
        cursor.execute('''
            SELECT id, description, assigned_agent, rejection_count,
                   executor_agent_id
            FROM tasks
            WHERE parent_id = ?
            AND status = 'pending'
            AND phase = 'execution'
            AND rejection_count > 0
            ORDER BY priority DESC
            LIMIT 1
        ''', (parent_id,))
        row = cursor.fetchone()
        if not row:
            return []

        task = {
            'id': row[0],
            'description': row[1],
            'assigned_agent': row[2] or 'executor',
            'rejection_count': row[3],
            'executor_agent_id': row[4],
        }

        # 取得 rejection context from working_memory
        cursor.execute(
            "SELECT value FROM working_memory "
            "WHERE task_id = ? AND key = 'critic_suggestions'",
            (row[0],)
        )
        wm_row = cursor.fetchone()
        if wm_row:
            task['_rejection_context'] = wm_row[0]

        return [task]


def _get_memory_task(parent_id: str) -> Optional[Dict]:
    """取得 memory task 的最新狀態

    Returns:
        {'id': str, 'status': str} or None
    """
    from servers import managed_connection
    with managed_connection() as db:
        cursor = db.cursor()
        cursor.execute(
            "SELECT id, status FROM tasks "
            "WHERE parent_id = ? AND assigned_agent = 'memory' "
            "ORDER BY created_at DESC LIMIT 1",
            (parent_id,)
        )
        row = cursor.fetchone()
        if not row:
            return None
        return {'id': row[0], 'status': row[1]}


def _build_executor_prompt(
    task: Dict,
    project_name: str,
    project_path: str,
    rejection_context: str = None
) -> str:
    """建構 Executor agent 的完整 prompt"""
    task_id = task['id']
    description = task.get('description', '')
    policy_section = _build_guardrail_policy_section(task.get('assigned_agent', 'executor'))

    # 嘗試從 description 提取檔案路徑
    context_section = ""
    file_path = _extract_file_path(description)
    if file_path:
        try:
            from servers.code_graph import get_file_structure
            structure = get_file_structure(project_name, file_path)
            if structure and 'error' not in structure:
                items = []
                for kind in ['classes', 'functions', 'interfaces']:
                    for item in structure.get(kind, []):
                        sig = item.get('signature', item.get('name', ''))
                        items.append(f"  - [{item.get('kind', kind)}] {sig}")
                if items:
                    context_section += f"\n## File Structure: {file_path}\n"
                    context_section += "\n".join(items[:20])
        except Exception:
            pass

        # 嘗試取得 class dependencies
        class_name = _extract_class_name(description)
        if class_name:
            try:
                from servers.code_graph import get_class_dependencies_bfs
                deps = get_class_dependencies_bfs(
                    project_name, class_name, max_depth=1
                )
                if deps and deps.get('dependencies'):
                    dep_lines = []
                    for d in deps['dependencies'][:10]:
                        dep_lines.append(
                            f"  - {d['name']} ({d.get('kind', '?')}) "
                            f"via {d.get('edge_kind', '?')}"
                        )
                    context_section += f"\n\n## Dependencies of {class_name}\n"
                    context_section += "\n".join(dep_lines)
            except Exception:
                pass

    rejection_section = ""
    if rejection_context:
        rejection_section = f"""

## Previous Rejection Feedback

{rejection_context}

Please address the issues above in this retry.
"""

    # Playbook 原則注入（依描述分類；未命中則為空，fail-open）
    playbook_section = ""
    try:
        from servers.playbooks import resolve_playbook, executor_section
        pb = resolve_playbook(description)
        if pb:
            playbook_section = "\n" + executor_section(pb)
    except Exception:
        playbook_section = ""

    prompt = f'''TASK_ID = "{task_id}"
PROJECT = "{project_name}"
PROJECT_PATH = "{project_path}"

## Task

{description}
{context_section}
{rejection_section}
{playbook_section}
{policy_section}
## Instructions

1. Read relevant source files
2. Execute the task as described
3. Output results clearly
'''
    return prompt.strip()


def _build_critic_prompt(
    critic_task: Dict,
    project_name: str,
    project_path: str
) -> str:
    """建構 Critic agent 的完整 prompt

    Args:
        critic_task: reserve_critic_task() 返回的 dict
            {'id', 'original_task_id', 'original_description', 'result'}

    Returns:
        完整的 critic prompt 字串
    """
    critic_task_id = critic_task['id']
    original_task_id = critic_task['original_task_id']
    description = critic_task.get('original_description', '')
    policy_section = _build_guardrail_policy_section('critic')

    # Playbook 驗收清單注入（依原始任務描述分類；fail-open）
    playbook_section = ""
    try:
        from servers.playbooks import resolve_playbook, critic_section
        pb = resolve_playbook(description)
        if pb:
            playbook_section = "\n" + critic_section(pb)
    except Exception:
        playbook_section = ""

    prompt = f'''TASK_ID = "{critic_task_id}"
ORIGINAL_TASK_ID = "{original_task_id}"
PROJECT = "{project_name}"
PROJECT_PATH = "{project_path}"

## Validation Target

Task: {description}
Result: {critic_task.get('result', 'See code changes')}

## Validation Criteria

1. Does the output match the task description?
2. Are there obvious errors or missing edge cases?
3. Is the code quality acceptable?
{playbook_section}
{policy_section}

## Output Format

You MUST output one of:
- `## 驗證結果: APPROVED` — if the task is done correctly
- `## 驗證結果: CONDITIONAL` — if acceptable with minor suggestions
- `## 驗證結果: REJECTED` — if significant issues need fixing
'''
    return prompt.strip()


def _build_memory_prompt(
    parent_id: str,
    project_name: str
) -> tuple:
    """建構 Memory agent 的完整 prompt

    Returns:
        (memory_task_id, prompt)
    """
    from servers.tasks import create_subtask, get_task_progress
    policy_section = _build_guardrail_policy_section('memory')

    # 建立 memory subtask
    memory_task_id = create_subtask(
        parent_id=parent_id,
        description="Store lessons learned from completed tasks",
        assigned_agent='memory',
        requires_validation=False
    )

    # 彙整已完成任務
    progress = get_task_progress(parent_id)
    completed_summaries = []
    for st in progress.get('subtasks', []):
        if st['status'] == 'done':
            completed_summaries.append(
                f"- {st['description']}: {st.get('result', 'done')}"
            )

    prompt = f'''TASK_ID = "{memory_task_id}"
PROJECT = "{project_name}"

## Task

Store lessons learned from the following completed tasks.

## Completed Tasks

{chr(10).join(completed_summaries) if completed_summaries else '(no details)'}

{policy_section}

## Instructions

1. Identify patterns, lessons, or reusable knowledge
2. Store important findings using store_memory()
3. Use category='lesson' for lessons learned, 'pattern' for patterns discovered
'''
    return memory_task_id, prompt.strip()


def _build_guardrail_policy_section(agent: str) -> str:
    """Return a prompt policy block. Fail open if guardrails cannot load."""
    try:
        from servers.guardrails import format_policy_for_prompt
        return "\n" + format_policy_for_prompt(agent)
    except Exception:
        return ""


def _extract_file_path(description: str) -> Optional[str]:
    """從任務描述中提取檔案路徑"""
    import re
    # 匹配常見檔案路徑 pattern
    match = re.search(
        r'(?:in |for |path[: ]+)([a-zA-Z0-9_./-]+\.[a-zA-Z]{1,5})',
        description
    )
    if match:
        return match.group(1)
    # 匹配獨立的檔案路徑
    match = re.search(
        r'\b((?:src|lib|app|servers|tests)/[a-zA-Z0-9_./-]+\.[a-zA-Z]{1,5})\b',
        description
    )
    if match:
        return match.group(1)
    return None


def _extract_class_name(description: str) -> Optional[str]:
    """從任務描述中提取 class 名稱"""
    import re
    match = re.search(r"(?:class|Class)\s*['\"]?(\w+)['\"]?", description)
    if match:
        return match.group(1)
    # CamelCase word that looks like a class name
    match = re.search(r'\b([A-Z][a-z]+(?:[A-Z][a-z]+)+)\b', description)
    if match:
        return match.group(1)
    return None


def find_latest_pending_epic(project_name: str) -> Optional[Dict]:
    """回傳該專案最新、且仍有未完成任務的 pending epic；無則 None。

    供 /han:run 在未指定 epic_id 時選預設 epic。通用：不限 refactor。
    只選「還有 status != 'done' 的子任務」的 epic，避免重複選中已全部完成
    （但 epic row 仍停在 pending）的 epic，而 shadow 較舊但仍可執行的 epic。
    回傳 {'id', 'description', 'status'} 或 None。
    """
    from servers import managed_connection
    with managed_connection() as db:
        cur = db.cursor()
        cur.execute('''
            SELECT e.id, e.description, e.status
            FROM tasks e
            WHERE e.project = ? AND e.task_level = 'epic' AND e.status = 'pending'
              AND EXISTS (
                  SELECT 1 FROM tasks t
                  WHERE t.epic_id = e.id AND t.task_level = 'task'
                    AND t.status != 'done'
              )
            ORDER BY e.created_at DESC, e.rowid DESC
            LIMIT 1
        ''', (project_name,))
        row = cur.fetchone()
    if not row:
        return None
    return {'id': row[0], 'description': row[1], 'status': row[2]}
