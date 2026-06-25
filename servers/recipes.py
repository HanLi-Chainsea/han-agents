"""
HAN System - 場景 Recipe

預定義的工作流程，自動建立任務樹。
每個 recipe 接受最少參數，用現有 building blocks 組合出完整 Epic→Story→Task 結構。
返回 epic_id 供 get_next_dispatch() 消費。
"""

import os
import re
import subprocess
from typing import Dict, List, Optional
from collections import defaultdict


_TEST_FILE_RE = re.compile(r'(^|/)(tests?|__tests__)/|(^|/)test_[^/]+$|[^/]+_test\.[^/]+$|[^/]+\.(test|spec)\.[^/]+$')


def is_test_file(path: str) -> bool:
    """判斷是否為測試檔（路徑段 tests/test/__tests__，或 test_*/*_test/*.test.*/*.spec.* 命名）。"""
    return bool(_TEST_FILE_RE.search(path or ''))


SCHEMA = """
=== Recipes ===

recipe_unit_tests(project_name, project_path, target_path=None, max_tasks=20) -> Dict
    為未測試的程式碼建立 unit test 任務樹。
    自動：sync Code Graph → 偵測覆蓋缺口 → 建立 Epic/Story/Task

recipe_code_review(project_name, project_path, target_path=None, diff_base="HEAD", max_tasks=20) -> Dict
    為待審查檔案建立 code review 任務樹。
    目標來源：target_path（指定路徑）或 git diff（預設 HEAD）。
    第一次/無 git/無 diff 且未給 target_path → task_count=0 + 明確訊息。

recipe_integration_tests(project_name, project_path, target_path=None, max_tasks=20) -> Dict
    為各模組建立整合測試任務樹（以目錄為模組分組）。

recipe_e2e_tests(project_name, project_path, target_path=None, max_tasks=5) -> Dict
    為各模組建立 E2E 任務樹（聚焦關鍵使用者旅程；上限刻意較小）。

所有 recipe 回傳含 'epic_id' 供 get_next_dispatch() 消費。

run_recipe(name, **kwargs) -> Dict
    按名稱執行 recipe。
    Available: 'unit_tests', 'code_review', 'integration_tests', 'e2e_tests'
"""


def _gaps_to_coverage_targets(file_gaps: List[Dict]) -> List[Dict]:
    """把同一檔案的 coverage gaps 轉成 gate 用的結構化 target 清單。"""
    return [{
        'file_path': g.get('file_path'),
        'name': g.get('name'),
        'line_start': g.get('line_start'),
        'line_end': g.get('line_end'),
    } for g in file_gaps]


def recipe_unit_tests(
    project_name: str,
    project_path: str,
    target_path: str = None,
    max_tasks: int = 20
) -> Dict:
    """為未測試的程式碼建立 unit test 任務樹

    流程（全部 baked in code）：
    1. sync Code Graph
    2. detect_coverage_gaps → 找未測試的 function/class
    3. 按檔案分組
    4. 建立 Epic → Story（每檔案）→ Task（每組 gaps）

    Args:
        project_name: 專案名稱
        project_path: 專案根目錄
        target_path: 只處理此路徑下的檔案（可選）
        max_tasks: 任務數上限

    Returns:
        dict with epic_id, ready for get_next_dispatch()
    """
    from servers.facade import sync
    from servers.drift import detect_coverage_gaps
    from servers.tasks import create_task, create_subtask
    from servers.project import ensure_project

    # 0. 確保專案已初始化（冪等，sync 包含在內）
    proj = ensure_project(project_name, project_path)
    tech_stack = proj.get('tech_stack', {})
    test_tool = tech_stack.get('test_tool', 'unknown')

    # 1. 偵測覆蓋缺口
    gaps = detect_coverage_gaps(project_name)
    total_gaps = len(gaps)

    # 2. 過濾
    if target_path:
        # 正規化 target_path（支援 'servers/' 和 './servers/' 兩種格式）
        target_path = target_path.rstrip('/')
        target_variants = [target_path, './' + target_path, target_path + '/']
        gaps = [g for g in gaps
                if any(g.get('file_path', '').startswith(v)
                       for v in target_variants)]

    if not gaps:
        return {
            'epic_id': None,
            'task_count': 0,
            'story_count': 0,
            'gaps_found': total_gaps,
            'stories': [],
            'message': f'No coverage gaps found'
                       f'{" under " + target_path if target_path else ""}. '
                       f'Total gaps in project: {total_gaps}',
        }

    # 3. 按檔案分組
    by_file = defaultdict(list)
    for gap in gaps:
        fp = gap.get('file_path') or 'unknown'
        by_file[fp].append(gap)

    # 4. 建立 Epic
    epic_desc = (
        f"Unit Test Coverage: {len(gaps)} untested items "
        f"across {len(by_file)} files"
    )
    epic_id = create_task(
        project=project_name,
        description=epic_desc,
        priority=7,
        task_level='epic'
    )

    # 5. 建立 Stories + Tasks
    stories_info = []
    task_count = 0

    for file_path in sorted(by_file.keys()):
        if task_count >= max_tasks:
            break

        file_gaps = by_file[file_path]
        gap_names = [g.get('name', '?') for g in file_gaps]

        # Story: 一個檔案一個 story
        story_id = create_task(
            project=project_name,
            description=f"Write tests for {file_path}",
            task_level='story',
            epic_id=epic_id,
            priority=7
        )

        story_info = {
            'story_id': story_id,
            'file_path': file_path,
            'task_ids': [],
            'gap_count': len(file_gaps),
        }

        # Task: 每個檔案一個 executor task（batch 該檔案所有 gaps）
        # 注意：per-file gaps 不可被 remaining(任務數預算) 切片——
        # remaining 是 task-count 預算，迴圈頂的 `task_count >= max_tasks: break`
        # 已負責任務數上限；切 per-file gaps 會漏掉同檔案的覆蓋目標。
        task_desc = (
            f"Write unit tests for {file_path}. "
            f"Test targets: {', '.join(gap_names[:5])}"
        )
        if len(gap_names) > 5:
            task_desc += f" and {len(gap_names) - 5} more"
        task_desc += f". Test tool: {test_tool}"

        task_id = create_subtask(
            parent_id=story_id,
            description=task_desc,
            assigned_agent='executor',
            requires_validation=True,
            task_level='task',
            epic_id=epic_id,
            story_id=story_id,
            metadata={'coverage_targets': _gaps_to_coverage_targets(file_gaps), 'task_type': 'unit_test'},
        )

        story_info['task_ids'].append(task_id)
        task_count += 1
        stories_info.append(story_info)

    return {
        'epic_id': epic_id,
        'task_count': task_count,
        'story_count': len(stories_info),
        'gaps_found': total_gaps,
        'stories': stories_info,
        'message': (
            f"Created {task_count} test tasks across "
            f"{len(stories_info)} files. "
            f"Use get_next_dispatch('{epic_id}', ...) to start execution."
        ),
    }


def _ensure_synced(project_name: str, project_path: str) -> Dict:
    """確保專案已初始化並同步 Code Graph，回 tech_stack。"""
    from servers.project import ensure_project
    proj = ensure_project(project_name, project_path)
    return proj.get('tech_stack', {})


def _list_source_files(project_name: str, target_path: str = None) -> List[str]:
    """從 Code Graph 取 file 節點，過濾 target_path、跳過測試檔。"""
    from servers.code_graph import get_code_nodes
    files = []
    offset = 0
    while True:
        page = get_code_nodes(project_name, kind='file', limit=500, offset=offset)
        files.extend(page)
        if len(page) < 500:
            break
        offset += 500
    result = []
    for n in files:
        fp = n.get('file_path') or ''
        if is_test_file(fp):
            continue
        if target_path:
            tp = target_path.rstrip('/')
            if not (fp == tp or fp == './' + tp
                    or fp.startswith(tp + '/') or fp.startswith('./' + tp + '/')):
                continue
        result.append(fp)
    return sorted(set(result))


def _git_changed_files(project_path: str, diff_base: str) -> Optional[List[str]]:
    """回傳 git diff 變更檔（排除已刪除檔）；非 git repo、失敗或不合法 diff_base 回 None。"""
    if not diff_base or diff_base.startswith("-"):
        return None
    try:
        out = subprocess.run(
            ["git", "-C", project_path, "diff", "--name-only",
             "--no-ext-diff", "--no-textconv", "--diff-filter=d", diff_base, "--"],
            capture_output=True, text=True, timeout=10)
        if out.returncode != 0:
            return None
        return [f for f in out.stdout.splitlines() if f.strip()]
    except Exception:
        return None


def recipe_code_review(
    project_name: str,
    project_path: str,
    target_path: str = None,
    diff_base: str = "HEAD",
    max_tasks: int = 20
) -> Dict:
    """為待審查的檔案建立 code review 任務樹。

    目標來源：
    - 有 target_path → 取該路徑下的原始碼檔（跳過測試檔）
    - 否則 → git diff --name-only <diff_base> 的變更檔
    - 第一次/無 git/無 diff 且未給 target_path → task_count=0 + 明確訊息

    預設 diff_base="HEAD" 審「未提交的工作區變更」；若要審「分支 vs main」
    已提交的變更，傳 diff_base="main"（或 merge-base range 由呼叫端先算好）。
    """
    from servers.tasks import create_task, create_subtask

    _ensure_synced(project_name, project_path)

    if max_tasks <= 0:
        return {'epic_id': None, 'task_count': 0, 'story_count': 0,
                'message': 'max_tasks 必須 > 0。'}

    if target_path:
        files = _list_source_files(project_name, target_path)
    else:
        changed = _git_changed_files(project_path, diff_base)
        if changed is None:
            return {'epic_id': None, 'task_count': 0, 'story_count': 0,
                    'message': '非 git repo 或無法取得 diff。請指定 target_path。'}
        files = [f for f in changed if not is_test_file(f)]

    if not files:
        return {'epic_id': None, 'task_count': 0, 'story_count': 0,
                'message': '沒有可審查的檔案。請指定 target_path 或先有改動。'}

    epic_id = create_task(
        project=project_name,
        description=f"Code Review: {min(len(files), max_tasks)} files",
        priority=7, task_level='epic')

    task_count = 0
    for fp in files:
        if task_count >= max_tasks:
            break
        story_id = create_task(
            project=project_name,
            description=f"Code review {fp}",
            task_level='story', epic_id=epic_id, priority=7)
        create_subtask(
            parent_id=story_id,
            description=f"Code review {fp}. 依 playbook 原則逐項審查並分級回報。",
            assigned_agent='executor', requires_validation=True,
            task_level='task', epic_id=epic_id, story_id=story_id,
            metadata={'task_type': 'code_review'})
        task_count += 1

    return {
        'epic_id': epic_id, 'task_count': task_count, 'story_count': task_count,
        'message': (f"Created {task_count} code review tasks. "
                    f"Use get_next_dispatch('{epic_id}', ...) to start."),
    }


def recipe_integration_tests(
    project_name: str,
    project_path: str,
    target_path: str = None,
    max_tasks: int = 20
) -> Dict:
    """為各模組建立整合測試任務樹（以模組/目錄為單位，非單一 function）。"""
    from servers.tasks import create_task, create_subtask
    from servers.integration_gate import boundaries_for_target

    tech = _ensure_synced(project_name, project_path)
    test_tool = tech.get('test_tool', 'unknown')

    if max_tasks <= 0:
        return {'epic_id': None, 'task_count': 0, 'story_count': 0,
                'message': 'max_tasks 必須 > 0。'}

    files = _list_source_files(project_name, target_path)
    if not files:
        return {'epic_id': None, 'task_count': 0, 'story_count': 0,
                'message': '沒有可建立整合測試的檔案。請指定 target_path 或先 sync。'}

    # 以「目錄」為模組分組（取檔案所在目錄）
    by_module = defaultdict(list)
    for fp in files:
        module = os.path.dirname(fp) or fp
        by_module[module].append(fp)

    epic_id = create_task(
        project=project_name,
        description=f"Integration Tests: {min(len(by_module), max_tasks)} modules",
        priority=7, task_level='epic')

    task_count = 0
    built_modules = []
    for module in sorted(by_module.keys()):
        if task_count >= max_tasks:
            break
        module_files = by_module[module]
        boundaries_error = False
        try:
            boundaries = boundaries_for_target(project_name, module_files)
        except Exception:
            # C-b fix: record error distinctly so the gate knows extraction failed
            # (vs. genuinely zero boundaries), preventing silent L2 disable.
            boundaries = []
            boundaries_error = True
        story_id = create_task(
            project=project_name,
            description=f"Integration tests for module {module}",
            task_level='story', epic_id=epic_id, priority=7)
        create_subtask(
            parent_id=story_id,
            description=(f"Write integration tests for module {module}. "
                        f"涵蓋跨檔案協作與邊界。Test tool: {test_tool}"),
            assigned_agent='executor', requires_validation=True,
            task_level='task', epic_id=epic_id, story_id=story_id,
            metadata={
                'integration_boundaries': boundaries,
                'test_files': [],
                'stack': test_tool,
                'task_type': 'integration_test',
                # C-b: flag extraction failure so the gate can reject
                **({'boundaries_error': True} if boundaries_error else {}),
            })
        task_count += 1
        built_modules.append(module)

    return {
        'epic_id': epic_id, 'task_count': task_count,
        'story_count': task_count, 'modules': built_modules,
        'message': (f"Created {task_count} integration test tasks across "
                    f"{len(built_modules)} modules. "
                    f"Use get_next_dispatch('{epic_id}', ...) to start."),
    }


def recipe_e2e_tests(
    project_name: str,
    project_path: str,
    target_path: str = None,
    max_tasks: int = 5
) -> Dict:
    """為各模組建立 E2E 任務樹（聚焦關鍵使用者旅程，非逐函式）。

    以模組分組，但**預設上限刻意較小（5）**以呼應 Test Pyramid「E2E 少而精」；
    任務描述框定為「end-to-end 使用者旅程」，實際「只測關鍵旅程」的 granularity
    由 e2e playbook 注入 executor/critic 把關。
    注意：以模組為分組是粗略代理；無使用者旅程的後端/內部模組宜改用 integration。
    """
    from servers.tasks import create_task, create_subtask

    tech = _ensure_synced(project_name, project_path)
    test_tool = tech.get('test_tool', 'unknown')

    if max_tasks <= 0:
        return {'epic_id': None, 'task_count': 0, 'story_count': 0,
                'message': 'max_tasks 必須 > 0。'}

    files = _list_source_files(project_name, target_path)
    if not files:
        return {'epic_id': None, 'task_count': 0, 'story_count': 0,
                'message': '沒有可建立 E2E 測試的檔案。請指定 target_path 或先 sync。'}

    by_module = defaultdict(list)
    for fp in files:
        module = os.path.dirname(fp) or fp
        by_module[module].append(fp)

    epic_id = create_task(
        project=project_name,
        description=f"E2E Tests: {min(len(by_module), max_tasks)} modules",
        priority=7, task_level='epic')

    task_count = 0
    built_modules = []
    for module in sorted(by_module.keys()):
        if task_count >= max_tasks:
            break
        story_id = create_task(
            project=project_name,
            description=f"E2E tests for module {module}",
            task_level='story', epic_id=epic_id, priority=7)
        create_subtask(
            parent_id=story_id,
            description=(f"Write end-to-end (E2E) tests for the critical user "
                        f"journeys through module {module}. Test tool: {test_tool}"),
            assigned_agent='executor', requires_validation=True,
            task_level='task', epic_id=epic_id, story_id=story_id,
            metadata={'task_type': 'e2e_test'})
        task_count += 1
        built_modules.append(module)

    return {
        'epic_id': epic_id, 'task_count': task_count,
        'story_count': task_count, 'modules': built_modules,
        'message': (f"Created {task_count} E2E test tasks across "
                    f"{len(built_modules)} modules. "
                    f"Use get_next_dispatch('{epic_id}', ...) to start."),
    }


# === 為可測試性重構：掃描（確定性，不建 epic、不改碼）===

LONG_METHOD_LINES = 40
HIGH_FANOUT = 8

# 高把握重構型錄（與 reference/playbooks/refactor.md 一致）；只有這些型別可建成可執行任務
HIGH_CONFIDENCE_REFACTORS = {
    'Extract Method', 'Extract Function',
    'Extract Variable', 'Introduce Variable',
    'Inline Variable', 'Inline Method',
    'Rename', 'Decompose Conditional',
    'Replace Magic Number', 'Replace Magic Number with Constant',
    'Replace Magic String with Constant',
}


def _call_fanout(project: str) -> Dict[str, int]:
    """回傳每個來源節點的 'calls' 出邊數（一次 group-by 查詢）。"""
    from servers import managed_connection
    with managed_connection() as db:
        cur = db.cursor()
        cur.execute(
            "SELECT from_id, COUNT(*) FROM code_edges "
            "WHERE project = ? AND kind = 'calls' GROUP BY from_id",
            (project,))
        return {row[0]: row[1] for row in cur.fetchall()}


def _in_target(file_path: str, target_path: Optional[str]) -> bool:
    if not target_path:
        return True
    tp = target_path.rstrip('/')
    return (file_path == tp or file_path == './' + tp
            or file_path.startswith(tp + '/')
            or file_path.startswith('./' + tp + '/'))


def _detect_hotspots(project: str, target_path: Optional[str]) -> List[Dict]:
    """掃描可測試性熱點（過長方法或高 fan-out）。只讀 Code Graph，純確定性。

    回傳依 score 由高至低排序的熱點清單；每項：
      {id, file_path, name, line_start, line_end, length, fan_out, score}
    """
    from servers.code_graph import get_code_nodes

    fanout = _call_fanout(project)
    nodes: List[Dict] = []
    for kind in ('function', 'method'):
        offset = 0
        while True:
            page = get_code_nodes(project, kind=kind, limit=500, offset=offset)
            nodes.extend(page)
            if len(page) < 500:
                break
            offset += 500

    spots: List[Dict] = []
    for n in nodes:
        fp = n.get('file_path') or ''
        if is_test_file(fp) or not _in_target(fp, target_path):
            continue
        ls = n.get('line_start') or 0
        le = n.get('line_end') or 0
        length = max(0, le - ls)
        fan_out = fanout.get(n.get('id'), 0)
        if length >= LONG_METHOD_LINES or fan_out >= HIGH_FANOUT:
            spots.append({
                'id': n.get('id'),
                'file_path': fp,
                'name': n.get('name') or '?',
                'line_start': ls,
                'line_end': le,
                'length': length,
                'fan_out': fan_out,
                'score': length + fan_out * 5,
            })
    spots.sort(key=lambda s: s['score'], reverse=True)
    return spots


def _find_pending_refactor_epic(project: str) -> Optional[str]:
    """同專案是否已有 pending 的 refactor epic（被動提示用）。"""
    from servers.tasks import get_epic_tasks
    for epic in get_epic_tasks(project):  # created_at DESC
        if (epic.get('status') == 'pending'
                and (epic.get('description') or '').startswith(
                    'Refactor for Testability')):
            return epic.get('id')
    return None


def scan_refactor_candidates(
    project_name: str,
    project_path: str,
    target_path: str = None,
    max_candidates: int = 20,
) -> Dict:
    """掃可測試性熱點候選。**不分類、不建 epic、不改原始碼。**

    分類（高/低把握）由指令層主代理依 refactor playbook 型錄進行。
    """
    if max_candidates < 1:
        max_candidates = 1
    _ensure_synced(project_name, project_path)
    hotspots = _detect_hotspots(project_name, target_path)
    truncated = len(hotspots) > max_candidates
    candidates = hotspots[:max_candidates]
    existing = _find_pending_refactor_epic(project_name)

    scope = f" under {target_path}" if target_path else ""
    if not candidates:
        msg = f"No testability hotspots found{scope}."
    else:
        msg = (f"Found {len(hotspots)} testability hotspot(s){scope}; "
               f"returning top {len(candidates)}.")
        if truncated:
            msg += f" Truncated {len(hotspots) - len(candidates)} (raise max_candidates to see more)."
    if existing:
        msg += f" NOTE: a pending refactor epic already exists: {existing}."

    return {
        'candidates': candidates,
        'total_hotspots': len(hotspots),
        'truncated': truncated,
        'existing_pending_epic': existing,
        'message': msg,
    }


def _delete_epic_tree(project: str, epic_id: str) -> None:
    """Best-effort 補償刪除：移除某 epic 及其 story/task 與相依，避免建樹中途失敗遺留 partial tree。"""
    from servers import managed_connection
    with managed_connection() as db:
        cur = db.cursor()
        cur.execute(
            "SELECT id FROM tasks WHERE project = ? AND (id = ? OR epic_id = ?)",
            (project, epic_id, epic_id))
        ids = [r[0] for r in cur.fetchall()]
        if ids:
            qs = ','.join('?' * len(ids))
            cur.execute(
                f"DELETE FROM task_dependencies "
                f"WHERE task_id IN ({qs}) OR depends_on_task_id IN ({qs})",
                ids + ids)
            cur.execute(f"DELETE FROM tasks WHERE id IN ({qs})", ids)
        db.commit()


def build_refactor_epic(project_name: str, items: List[Dict]) -> Dict:
    """為高把握重構項建任務樹。

    items: 每項 {file_path, name, refactor_type, line_start, line_end}
    每項 -> 1 story + 3 相依 task：characterization-test -> refactor -> verify。
    只接受 refactor_type ∈ HIGH_CONFIDENCE_REFACTORS 且含 file_path/name 的項；
    其餘列入 rejected、不建任務。無有效項 -> 不建 epic。
    回傳 {'epic_id', 'story_count', 'task_count', 'rejected'}。
    """
    from servers.tasks import create_task, create_subtask

    valid: List[Dict] = []
    rejected: List[Dict] = []
    for it in items or []:
        fp = (it.get('file_path') or '').strip()
        sym = (it.get('name') or '').strip()
        rtype = (it.get('refactor_type') or '').strip()
        if not fp or not sym:
            rejected.append({'item': it, 'reason': 'missing file_path or name'})
            continue
        if rtype not in HIGH_CONFIDENCE_REFACTORS:
            rejected.append({'item': it,
                             'reason': f'refactor_type not in high-confidence catalog: {rtype!r}'})
            continue
        valid.append(it)

    if not valid:
        return {'epic_id': None, 'story_count': 0, 'task_count': 0, 'rejected': rejected}

    epic_id = create_task(
        project=project_name,
        description=f"Refactor for Testability: {len(valid)} units",
        priority=7, task_level='epic')

    task_count = 0
    try:
        for it in valid:
            sym = it['name'].strip()
            fp = it['file_path'].strip()
            rtype = it['refactor_type'].strip()
            ls = it.get('line_start')
            le = it.get('line_end')
            loc = fp
            if isinstance(ls, int) and isinstance(le, int) and ls > 0 and le >= ls:
                loc = f"{fp} (lines {ls}-{le})"

            story_id = create_task(
                project=project_name,
                description=f"Refactor for testability: {sym} in {loc}",
                task_level='story', epic_id=epic_id, priority=7)

            t1 = create_subtask(
                parent_id=story_id,
                description=(
                    f"Write characterization tests pinning current behavior of "
                    f"{sym} in {loc} (refactor-for-testability safety net). "
                    f"Do not judge correctness; pin every branch's current behavior."),
                assigned_agent='executor', requires_validation=True,
                task_level='task', epic_id=epic_id, story_id=story_id,
                metadata={'task_type': 'refactor'})
            t2 = create_subtask(
                parent_id=story_id,
                description=(
                    f"Refactor for testability: apply {rtype} to {sym} in {loc}. "
                    f"Behavior-preserving, mechanical."),
                assigned_agent='executor', depends_on=[t1],
                requires_validation=True,
                task_level='task', epic_id=epic_id, story_id=story_id,
                metadata={'task_type': 'refactor'})
            create_subtask(
                parent_id=story_id,
                description=(
                    f"Verify refactor of {sym} in {loc}: rerun characterization "
                    f"tests, must stay green."),
                assigned_agent='executor', depends_on=[t2],
                requires_validation=True,
                task_level='task', epic_id=epic_id, story_id=story_id,
                metadata={'task_type': 'refactor'})
            task_count += 3
    except Exception:
        _delete_epic_tree(project_name, epic_id)  # 補償：不留 partial tree
        raise

    return {'epic_id': epic_id, 'story_count': len(valid),
            'task_count': task_count, 'rejected': rejected}


# Recipe registry
RECIPES = {
    'unit_tests': recipe_unit_tests,
    'code_review': recipe_code_review,
    'integration_tests': recipe_integration_tests,
    'e2e_tests': recipe_e2e_tests,
}


def run_recipe(name: str, **kwargs) -> Dict:
    """按名稱執行 recipe

    Args:
        name: Recipe 名稱（見 RECIPES）
        **kwargs: 傳給 recipe 函式的參數

    Returns:
        Recipe 返回值（包含 epic_id）

    Raises:
        KeyError: recipe 名稱不存在
    """
    if name not in RECIPES:
        available = ', '.join(sorted(RECIPES.keys()))
        raise KeyError(
            f"Unknown recipe: '{name}'. Available: {available}"
        )
    return RECIPES[name](**kwargs)
