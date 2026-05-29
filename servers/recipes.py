"""
HAN System - 場景 Recipe

預定義的工作流程，自動建立任務樹。
每個 recipe 接受最少參數，用現有 building blocks 組合出完整 Epic→Story→Task 結構。
返回 epic_id 供 get_next_dispatch() 消費。
"""

import os
import subprocess
from typing import Dict, List, Optional
from collections import defaultdict


SCHEMA = """
=== Recipes ===

recipe_unit_tests(project_name, project_path, target_path=None, max_tasks=20) -> Dict
    為未測試的程式碼建立 unit test 任務樹。
    自動：sync Code Graph → 偵測覆蓋缺口 → 建立 Epic/Story/Task

    Returns:
        {
            'epic_id': str,
            'task_count': int,
            'story_count': int,
            'gaps_found': int,
            'stories': [...],
            'message': str,
        }

run_recipe(name, **kwargs) -> Dict
    按名稱執行 recipe。
    Available: 'unit_tests'
"""


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

        # Task: 每個檔案一個 executor task（batch 所有 gaps）
        remaining = max_tasks - task_count
        batch_names = gap_names[:remaining]

        task_desc = (
            f"Write unit tests for {file_path}. "
            f"Test targets: {', '.join(batch_names[:5])}"
        )
        if len(batch_names) > 5:
            task_desc += f" and {len(batch_names) - 5} more"
        task_desc += f". Test tool: {test_tool}"

        task_id = create_subtask(
            parent_id=story_id,
            description=task_desc,
            assigned_agent='executor',
            requires_validation=True,
            task_level='task',
            epic_id=epic_id,
            story_id=story_id,
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
        if 'test' in fp.lower():
            continue
        if target_path:
            tp = target_path.rstrip('/')
            if not (fp.startswith(tp + '/') or fp.startswith('./' + tp + '/')):
                continue
        result.append(fp)
    return sorted(set(result))


def _git_changed_files(project_path: str, diff_base: str) -> Optional[List[str]]:
    """回傳 git diff 變更檔；非 git repo 或失敗回 None。"""
    try:
        out = subprocess.run(
            ["git", "-C", project_path, "diff", "--name-only", diff_base],
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
    """
    from servers.tasks import create_task, create_subtask

    _ensure_synced(project_name, project_path)

    if target_path:
        files = _list_source_files(project_name, target_path)
    else:
        changed = _git_changed_files(project_path, diff_base)
        if changed is None:
            return {'epic_id': None, 'task_count': 0, 'story_count': 0,
                    'message': '非 git repo 或無法取得 diff。請指定 target_path。'}
        files = [f for f in changed if 'test' not in f.lower()]

    if not files:
        return {'epic_id': None, 'task_count': 0, 'story_count': 0,
                'message': '沒有可審查的檔案。請指定 target_path 或先有改動。'}

    epic_id = create_task(
        project=project_name,
        description=f"Code Review: {len(files)} files",
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
            task_level='task', epic_id=epic_id, story_id=story_id)
        task_count += 1

    return {
        'epic_id': epic_id, 'task_count': task_count, 'story_count': task_count,
        'message': (f"Created {task_count} code review tasks. "
                    f"Use get_next_dispatch('{epic_id}', ...) to start."),
    }


# Recipe registry
RECIPES = {
    'unit_tests': recipe_unit_tests,
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
