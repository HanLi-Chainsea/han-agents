"""
Tasks Server - 任務管理工具
"""

import sqlite3
import json
import uuid
import os
from typing import Optional, List, Dict, Any
from datetime import datetime

from servers import managed_connection

SCHEMA = """
=== Tasks Server ===

create_task(project, description, priority=5, parent_id=None, branch=None, task_level=None, epic_id=None, story_id=None) -> str
    建立新任務，回傳 task_id

    Parameters:
        project: 專案名稱
        description: 任務描述
        priority: 優先級 (1-10)
        parent_id: 父任務 ID（可選）
        branch: Branch 信息（可選）
            {
                'flow_id': 'flow.auth',
                'domain_ids': ['domain.user']
            }
        task_level: 任務層級（可選）'epic' | 'story' | 'task' | 'bug'
        epic_id: 所屬 Epic ID（可選）
        story_id: 所屬 Story ID（可選）

create_subtask(parent_id, description, assigned_agent='executor', depends_on=None, requires_validation=True) -> str
    建立子任務（可指定是否需要驗證）

get_task(task_id) -> Dict
    取得任務詳情（包含 metadata, branch, executor_agent_id, rejection_count）

update_task(task_id, **kwargs) -> None
    更新任務的任意欄位（用於生命週期管理）

    Allowed fields:
        - executor_agent_id: 執行者的 agentId（用於 resume）
        - rejection_count: 被 Critic reject 的次數
        - status, result, error_message, phase, validation_status, validator_task_id

    Example:
        update_task(task_id, executor_agent_id='abc123', rejection_count=1)

update_task_status(task_id, status, result=None, error=None) -> None
    更新任務狀態 ('pending', 'running', 'done', 'failed', 'blocked')

get_next_task(parent_id) -> Optional[Dict]
    取得下一個可執行的任務（依賴已完成）

get_task_progress(parent_id) -> Dict
    取得任務進度統計

log_agent_action(agent, task_id, action, message, duration_ms=None) -> None
    記錄 agent 執行日誌

get_unvalidated_tasks(parent_id) -> List[Dict]
    取得待驗證任務（已完成執行但未驗證）

mark_validated(task_id, status, validator_task_id=None) -> None
    標記任務驗證狀態 ('approved', 'rejected', 'skipped')

advance_task_phase(task_id, phase) -> None
    推進任務階段 ('execution', 'validation', 'documentation', 'completed')

get_task_branch(task_id) -> Optional[Dict]
    取得任務的 Branch 信息

    Returns: {'flow_id': 'flow.auth', 'domain_ids': [...]} 或 None

set_task_branch(task_id, branch) -> None
    設定任務的 Branch 信息

    Parameters:
        task_id: 任務 ID
        branch: {'flow_id': 'flow.auth', 'domain_ids': ['domain.user']}

load_branch_context(branch, project_dir=None) -> str
    加載 Branch 完整 context（整合 SSOT + Memory）

    Parameters:
        branch: {'flow_id': 'flow.auth', 'domain_ids': ['domain.user']}
        project_dir: 專案目錄（可選）

    Returns: 組合的 context 字符串（doctrine + flow_spec + 相關 memory）
"""


def create_task(project: str, description: str, priority: int = 5,
                parent_id: str = None, branch: Dict = None,
                task_level: str = None, epic_id: str = None,
                story_id: str = None) -> str:
    """建立新任務

    Args:
        project: 專案名稱
        description: 任務描述
        priority: 優先級 (1-10)
        parent_id: 父任務 ID（可選）
        branch: Branch 信息（可選）
            {
                'flow_id': 'flow.auth',
                'domain_ids': ['domain.user']
            }
        task_level: 任務層級（可選）'epic' | 'story' | 'task' | 'bug'
        epic_id: 所屬 Epic ID（可選）
        story_id: 所屬 Story ID（可選）

    Returns:
        task_id
    """
    valid_levels = ['epic', 'story', 'task', 'bug', None]
    if task_level not in valid_levels:
        raise ValueError(f"Invalid task_level: {task_level}. Must be one of {valid_levels[:-1]}")

    with managed_connection() as db:
        cursor = db.cursor()

        task_id = str(uuid.uuid4())[:8]

        metadata = None
        if branch:
            metadata = json.dumps({'branch': branch})

        cursor.execute('''
            INSERT INTO tasks (id, parent_id, project, description, priority, metadata,
                              task_level, epic_id, story_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (task_id, parent_id, project, description, priority, metadata,
              task_level, epic_id, story_id))

        db.commit()
        return task_id

def create_subtask(parent_id: str, description: str,
                   assigned_agent: str = 'executor',
                   depends_on: List[str] = None,
                   priority: int = 5,
                   requires_validation: bool = True,
                   task_level: str = 'task',
                   epic_id: str = None,
                   story_id: str = None) -> str:
    """建立子任務

    Args:
        parent_id: 父任務 ID
        description: 任務描述
        assigned_agent: 指派的 agent (executor, critic, memory, researcher)
        depends_on: 依賴的任務 ID 列表
        priority: 優先級 (1-10)
        requires_validation: 是否需要 Critic 驗證 (預設 True)
        task_level: 任務層級 (epic/story/task/bug)
        epic_id: 所屬 Epic ID（None 時自動從 parent 繼承）
        story_id: 所屬 Story ID（None 時自動從 parent 繼承）
    """
    with managed_connection() as db:
        cursor = db.cursor()

        cursor.execute(
            'SELECT project, task_level, epic_id, story_id FROM tasks WHERE id = ?',
            (parent_id,)
        )
        row = cursor.fetchone()
        project = row[0] if row else None

        # Auto-inherit hierarchy from parent
        if row:
            parent_level = row[1]
            if epic_id is None:
                epic_id = parent_id if parent_level == 'epic' else row[2]
            if story_id is None:
                story_id = parent_id if parent_level == 'story' else row[3]

        task_id = str(uuid.uuid4())[:8]
        cursor.execute('''
            INSERT INTO tasks (id, parent_id, project, description, assigned_agent,
                             priority, requires_validation, task_level, epic_id, story_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (task_id, parent_id, project, description, assigned_agent, priority,
              1 if requires_validation else 0, task_level, epic_id, story_id))

        if depends_on:
            for dep_id in depends_on:
                cursor.execute('''
                    INSERT INTO task_dependencies (task_id, depends_on_task_id)
                    VALUES (?, ?)
                ''', (task_id, dep_id))

        db.commit()
        return task_id

def get_task(task_id: str) -> Optional[Dict]:
    """取得任務詳情（包含 metadata、branch 和層級資訊）"""
    with managed_connection() as db:
        cursor = db.cursor()

        cursor.execute('''
            SELECT id, parent_id, project, description, status, priority,
                   assigned_agent, result, error_message, retry_count,
                   created_at, started_at, completed_at,
                   phase, requires_validation, validation_status, validator_task_id,
                   metadata, executor_agent_id, rejection_count,
                   task_level, epic_id, story_id
            FROM tasks WHERE id = ?
        ''', (task_id,))

        row = cursor.fetchone()

        if row:
            metadata = None
            branch = None
            if row[17]:
                try:
                    metadata = json.loads(row[17])
                    branch = metadata.get('branch')
                except json.JSONDecodeError:
                    pass

            return {
                'id': row[0],
                'parent_id': row[1],
                'project': row[2],
                'description': row[3],
                'status': row[4],
                'priority': row[5],
                'assigned_agent': row[6],
                'result': row[7],
                'error_message': row[8],
                'retry_count': row[9],
                'created_at': row[10],
                'started_at': row[11],
                'completed_at': row[12],
                'phase': row[13],
                'requires_validation': bool(row[14]) if row[14] is not None else True,
                'validation_status': row[15],
                'validator_task_id': row[16],
                'metadata': metadata,
                'branch': branch,
                'executor_agent_id': row[18],
                'rejection_count': row[19] or 0,
                'task_level': row[20],
                'epic_id': row[21],
                'story_id': row[22]
            }
        return None


def update_task(task_id: str, **kwargs) -> None:
    """更新任務的任意欄位

    Args:
        task_id: 任務 ID
        **kwargs: 要更新的欄位，例如：
            - executor_agent_id: 執行者的 agentId
            - rejection_count: 被 reject 的次數
            - status: 任務狀態
            - result: 執行結果
            - phase: 任務階段

    Example:
        update_task(task_id, executor_agent_id='abc123', rejection_count=1)
    """
    if not kwargs:
        return

    allowed_fields = {
        'executor_agent_id', 'rejection_count', 'status', 'result',
        'error_message', 'phase', 'validation_status', 'validator_task_id'
    }

    updates = {k: v for k, v in kwargs.items() if k in allowed_fields}
    if not updates:
        return

    with managed_connection() as db:
        cursor = db.cursor()

        set_clause = ', '.join([f'{k} = ?' for k in updates.keys()])
        values = list(updates.values()) + [task_id]

        cursor.execute(f'''
            UPDATE tasks SET {set_clause} WHERE id = ?
        ''', values)

        db.commit()

def update_task_status(task_id: str, status: str,
                       result: str = None, error: str = None) -> None:
    """更新任務狀態"""
    with managed_connection() as db:
        cursor = db.cursor()

        if status == 'running':
            cursor.execute('''
                UPDATE tasks
                SET status = ?, started_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (status, task_id))
        elif status in ('done', 'failed'):
            cursor.execute('''
                UPDATE tasks
                SET status = ?, result = ?, error_message = ?,
                    completed_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (status, result, error, task_id))

            if status == 'failed':
                cursor.execute('''
                    UPDATE tasks
                    SET retry_count = retry_count + 1
                    WHERE id = ?
                ''', (task_id,))
        else:
            cursor.execute('''
                UPDATE tasks SET status = ? WHERE id = ?
            ''', (status, task_id))

        db.commit()

def get_next_task(parent_id: str) -> Optional[Dict]:
    """取得下一個可執行的任務"""
    with managed_connection() as db:
        cursor = db.cursor()

        cursor.execute('''
            SELECT t.id, t.description, t.assigned_agent, t.priority
            FROM tasks t
            WHERE t.status = 'pending'
            AND t.parent_id = ?
            AND NOT EXISTS (
                SELECT 1 FROM task_dependencies td
                JOIN tasks dep ON td.depends_on_task_id = dep.id
                WHERE td.task_id = t.id AND dep.status != 'done'
            )
            ORDER BY t.priority DESC
            LIMIT 1
        ''', (parent_id,))

        row = cursor.fetchone()

        if row:
            return {
                'id': row[0],
                'description': row[1],
                'assigned_agent': row[2],
                'priority': row[3]
            }
        return None

def get_task_progress(parent_id: str) -> Dict:
    """取得任務進度統計"""
    with managed_connection() as db:
        cursor = db.cursor()

        cursor.execute('''
            SELECT status, COUNT(*) FROM tasks
            WHERE parent_id = ?
            GROUP BY status
        ''', (parent_id,))

        stats = {row[0]: row[1] for row in cursor.fetchall()}

        cursor.execute('''
            SELECT id, description, status, result
            FROM tasks WHERE parent_id = ?
            ORDER BY created_at
        ''', (parent_id,))

        subtasks = []
        for row in cursor.fetchall():
            subtasks.append({
                'id': row[0],
                'description': row[1],
                'status': row[2],
                'result': row[3]
            })

    total = sum(stats.values())
    done = stats.get('done', 0)

    return {
        'total': total,
        'done': done,
        'pending': stats.get('pending', 0),
        'running': stats.get('running', 0),
        'failed': stats.get('failed', 0),
        'progress': f"{done}/{total}",
        'percentage': round(done / total * 100, 1) if total > 0 else 0,
        'subtasks': subtasks
    }

def log_agent_action(agent: str, task_id: str, action: str,
                     message: str, duration_ms: int = None,
                     tokens_used: int = None) -> None:
    """記錄 agent 執行日誌"""
    with managed_connection() as db:
        cursor = db.cursor()

        cursor.execute('''
            INSERT INTO agent_logs (agent, task_id, action, message, duration_ms, tokens_used)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (agent, task_id, action, message, duration_ms, tokens_used))

        db.commit()

def get_all_subtasks(parent_id: str) -> List[Dict]:
    """取得所有子任務"""
    with managed_connection() as db:
        cursor = db.cursor()

        cursor.execute('''
            SELECT id, description, status, assigned_agent, priority, result
            FROM tasks
            WHERE parent_id = ?
            ORDER BY priority DESC, created_at
        ''', (parent_id,))

        subtasks = []
        for row in cursor.fetchall():
            subtasks.append({
                'id': row[0],
                'description': row[1],
                'status': row[2],
                'assigned_agent': row[3],
                'priority': row[4],
                'result': row[5]
            })

        return subtasks


def get_unvalidated_tasks(parent_id: str) -> List[Dict]:
    """取得待驗證任務（已完成執行但未驗證）

    回傳所有 status='done' 且 requires_validation=1 且尚無 active critic 的任務。
    已有 pending/running critic（validator_task_id 指向的任務）的不會重複回傳。
    """
    with managed_connection() as db:
        cursor = db.cursor()

        cursor.execute('''
            SELECT t.id, t.parent_id, t.description, t.status,
                   t.assigned_agent, t.result, t.phase, t.validator_task_id,
                   t.epic_id, t.story_id
            FROM tasks t
            WHERE t.parent_id = ?
            AND t.status = 'done'
            AND t.requires_validation = 1
            AND (t.validation_status IS NULL OR t.validation_status = 'pending')
            ORDER BY t.created_at
        ''', (parent_id,))

        tasks = []
        for row in cursor.fetchall():
            # 若已有 validator_task_id，檢查其是否仍在 pending/running
            validator_id = row[7]
            if validator_id:
                cursor.execute(
                    "SELECT status FROM tasks WHERE id = ?",
                    (validator_id,)
                )
                v_row = cursor.fetchone()
                if v_row and v_row[0] in ('pending', 'running'):
                    continue  # 已有 active critic，跳過

            tasks.append({
                'id': row[0],
                'parent_id': row[1],
                'description': row[2],
                'status': row[3],
                'assigned_agent': row[4],
                'result': row[5],
                'phase': row[6],
                'validator_task_id': validator_id,
                'epic_id': row[8],
                'story_id': row[9],
            })

        return tasks


def reserve_critic_task(original_task_id: str) -> Optional[Dict]:
    """原子性地為已完成任務保留或重用 critic 驗證任務。

    使用 BEGIN IMMEDIATE 確保同一任務不會重複建立 critic。
    若已有 pending/running critic，直接返回該 critic 資訊。

    Returns:
        dict with critic task info, or None if task not eligible
    """
    with managed_connection() as db:
        cursor = db.cursor()
        cursor.execute('BEGIN IMMEDIATE')

        cursor.execute('''
            SELECT id, parent_id, project, description, result,
                   validator_task_id, epic_id, story_id
            FROM tasks
            WHERE id = ?
              AND status = 'done'
              AND requires_validation = 1
              AND (validation_status IS NULL OR validation_status = 'pending')
        ''', (original_task_id,))
        row = cursor.fetchone()
        if not row:
            db.commit()
            return None

        validator_task_id = row[5]

        # 若已有 critic，檢查是否仍 active
        if validator_task_id:
            cursor.execute(
                "SELECT id, status FROM tasks WHERE id = ?",
                (validator_task_id,)
            )
            existing = cursor.fetchone()
            if existing and existing[1] in ('pending', 'running'):
                db.commit()
                return {
                    'id': existing[0],
                    'original_task_id': row[0],
                    'original_description': row[3],
                    'result': row[4],
                }

        # 建立新 critic subtask
        critic_task_id = str(uuid.uuid4())[:8]
        cursor.execute('''
            INSERT INTO tasks (
                id, parent_id, project, description, assigned_agent, priority,
                requires_validation, task_level, epic_id, story_id, status
            ) VALUES (?, ?, ?, ?, 'critic', 5, 0, 'task', ?, ?, 'pending')
        ''', (
            critic_task_id, row[1], row[2], f"Validate: {row[3][:80]}",
            row[6], row[7]
        ))

        # 標記原任務的 validator_task_id + phase
        cursor.execute('''
            UPDATE tasks
            SET validation_status = 'pending', validator_task_id = ?, phase = 'validation'
            WHERE id = ?
        ''', (critic_task_id, original_task_id))

        db.commit()
        return {
            'id': critic_task_id,
            'original_task_id': row[0],
            'original_description': row[3],
            'result': row[4],
        }


def mark_validated(task_id: str, status: str, validator_task_id: str = None) -> None:
    """標記任務驗證狀態

    Args:
        task_id: 被驗證的任務 ID
        status: 驗證結果 ('approved', 'rejected', 'skipped')
        validator_task_id: 執行驗證的 Critic 任務 ID
    """
    with managed_connection() as db:
        cursor = db.cursor()

        cursor.execute('''
            UPDATE tasks
            SET validation_status = ?,
                validator_task_id = ?,
                phase = CASE
                    WHEN ? = 'approved' THEN 'documentation'
                    WHEN ? = 'rejected' THEN 'execution'
                    ELSE phase
                END
            WHERE id = ?
        ''', (status, validator_task_id, status, status, task_id))

        db.commit()


def advance_task_phase(task_id: str, phase: str) -> None:
    """推進任務階段

    Args:
        task_id: 任務 ID
        phase: 目標階段 ('execution', 'validation', 'documentation', 'completed')
    """
    valid_phases = ['execution', 'validation', 'documentation', 'completed']
    if phase not in valid_phases:
        raise ValueError(f"Invalid phase: {phase}. Must be one of {valid_phases}")

    with managed_connection() as db:
        cursor = db.cursor()

        cursor.execute('''
            UPDATE tasks SET phase = ? WHERE id = ?
        ''', (phase, task_id))

        db.commit()


def get_active_tasks_for_project(project: str) -> List[Dict]:
    """取得專案中所有進行中的主任務（用於斷點重連）

    Args:
        project: 專案名稱

    Returns:
        進行中的主任務列表
    """
    with managed_connection() as db:
        cursor = db.cursor()

        cursor.execute('''
            SELECT t.id, t.description, t.status, t.phase, t.created_at,
                   (SELECT COUNT(*) FROM tasks WHERE parent_id = t.id) as subtask_count,
                   (SELECT COUNT(*) FROM tasks WHERE parent_id = t.id AND status = 'done') as done_count
            FROM tasks t
            WHERE t.project = ?
            AND t.parent_id IS NULL
            AND t.status NOT IN ('done', 'failed')
            ORDER BY t.created_at DESC
        ''', (project,))

        tasks = []
        for row in cursor.fetchall():
            total = row[5]
            done = row[6]
            tasks.append({
                'id': row[0],
                'description': row[1],
                'status': row[2],
                'phase': row[3],
                'created_at': row[4],
                'progress': f"{done}/{total}" if total > 0 else "0/0",
                'percentage': round(done / total * 100, 1) if total > 0 else 0
            })

        return tasks


def get_validation_summary(parent_id: str) -> Dict:
    """取得驗證統計摘要

    回傳任務的驗證狀態統計
    """
    with managed_connection() as db:
        cursor = db.cursor()

        cursor.execute('''
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN validation_status = 'approved' THEN 1 ELSE 0 END) as approved,
                SUM(CASE WHEN validation_status = 'rejected' THEN 1 ELSE 0 END) as rejected,
                SUM(CASE WHEN validation_status = 'skipped' THEN 1 ELSE 0 END) as skipped,
                SUM(CASE WHEN validation_status IS NULL OR validation_status = 'pending' THEN 1 ELSE 0 END) as pending
            FROM tasks
            WHERE parent_id = ?
            AND requires_validation = 1
        ''', (parent_id,))

        row = cursor.fetchone()

        return {
            'total': row[0] or 0,
            'approved': row[1] or 0,
            'rejected': row[2] or 0,
            'skipped': row[3] or 0,
            'pending': row[4] or 0,
            'validation_rate': f"{row[1] or 0}/{row[0] or 0}"
        }


def get_task_branch(task_id: str) -> Optional[Dict]:
    """取得任務的 Branch 信息

    Args:
        task_id: 任務 ID

    Returns:
        {'flow_id': 'flow.auth', 'domain_ids': [...]} 或 None
    """
    task = get_task(task_id)
    if task:
        return task.get('branch')
    return None


def set_task_branch(task_id: str, branch: Dict) -> None:
    """設定任務的 Branch 信息

    Args:
        task_id: 任務 ID
        branch: {'flow_id': 'flow.auth', 'domain_ids': ['domain.user']}
    """
    with managed_connection() as db:
        cursor = db.cursor()

        cursor.execute('SELECT metadata FROM tasks WHERE id = ?', (task_id,))
        row = cursor.fetchone()

        if row:
            try:
                metadata = json.loads(row[0]) if row[0] else {}
            except json.JSONDecodeError:
                metadata = {}

            metadata['branch'] = branch

            cursor.execute('''
                UPDATE tasks SET metadata = ? WHERE id = ?
            ''', (json.dumps(metadata), task_id))

            db.commit()


def load_branch_context(branch: Dict, project_dir: str = None) -> str:
    """加載 Branch 完整 context（整合 SSOT + Memory）

    Args:
        branch: {'flow_id': 'flow.auth', 'domain_ids': ['domain.user']}
        project_dir: 專案目錄（可選）

    Returns:
        組合的 context 字符串（doctrine + flow_spec + 相關 memory）
    """
    # 延遲 import 避免循環依賴
    from servers.ssot import load_ssot_for_branch
    from servers.memory import search_memory

    sections = []

    ssot_context = load_ssot_for_branch(branch, project_dir)
    if ssot_context:
        sections.append(ssot_context)

    flow_id = branch.get('flow_id')
    domain_ids = branch.get('domain_ids', [])

    if flow_id:
        memories = search_memory(
            query=flow_id.replace('flow.', ''),
            branch_flow=flow_id,
            limit=5
        )

        if memories:
            memory_section = "# 相關記憶\n\n"
            for m in memories:
                title = m.get('title', '無標題')
                content = m.get('content', '')[:300]
                memory_section += f"## {title}\n{content}\n\n"

            sections.append(memory_section)

    return "\n\n---\n\n".join(sections) if sections else ""


def _ensure_columns(table: str, columns: dict):
    """確保資料表有指定欄位（冪等 schema migration）

    Args:
        table: 資料表名稱
        columns: {欄位名: 欄位定義} e.g. {'metadata': 'TEXT', 'count': 'INTEGER DEFAULT 0'}
    """
    with managed_connection() as db:
        cursor = db.cursor()
        cursor.execute(f"PRAGMA table_info({table})")
        existing = {row[1] for row in cursor.fetchall()}
        for col_name, col_def in columns.items():
            if col_name not in existing:
                cursor.execute(f'ALTER TABLE {table} ADD COLUMN {col_name} {col_def}')
        db.commit()


# 初始化時確保欄位存在（單次 DB 連線）
_ensure_columns('tasks', {
    'metadata': 'TEXT',
    'executor_agent_id': 'TEXT',
    'rejection_count': 'INTEGER DEFAULT 0',
    'task_level': 'TEXT',
    'epic_id': 'TEXT',
    'story_id': 'TEXT',
})


def get_epic_tasks(project: str, epic_id: str = None) -> List[Dict]:
    """取得專案的 Epic 任務

    Args:
        project: 專案名稱
        epic_id: 特定 Epic ID（可選，若不指定則返回所有 Epic）

    Returns:
        Epic 任務列表，每個包含 stories 子列表
    """
    with managed_connection() as db:
        cursor = db.cursor()

        if epic_id:
            cursor.execute('''
                SELECT id, description, status, phase, created_at
                FROM tasks
                WHERE project = ? AND id = ? AND task_level = 'epic'
            ''', (project, epic_id))
        else:
            cursor.execute('''
                SELECT id, description, status, phase, created_at
                FROM tasks
                WHERE project = ? AND task_level = 'epic'
                ORDER BY created_at DESC, rowid DESC
            ''', (project,))

        epics = []
        for row in cursor.fetchall():
            epic = {
                'id': row[0],
                'description': row[1],
                'status': row[2],
                'phase': row[3],
                'created_at': row[4],
                'stories': []
            }

            cursor.execute('''
                SELECT id, description, status, phase
                FROM tasks
                WHERE epic_id = ? AND task_level = 'story'
                ORDER BY priority DESC, created_at
            ''', (row[0],))

            for story_row in cursor.fetchall():
                epic['stories'].append({
                    'id': story_row[0],
                    'description': story_row[1],
                    'status': story_row[2],
                    'phase': story_row[3]
                })

            epics.append(epic)

        return epics


def get_story_tasks(story_id: str) -> List[Dict]:
    """取得 Story 下的所有任務

    Args:
        story_id: Story ID

    Returns:
        任務列表（包含 task 和 bug 類型）
    """
    with managed_connection() as db:
        cursor = db.cursor()

        cursor.execute('''
            SELECT id, description, status, phase, task_level, assigned_agent
            FROM tasks
            WHERE story_id = ?
            ORDER BY
                CASE task_level
                    WHEN 'task' THEN 1
                    WHEN 'bug' THEN 2
                    ELSE 3
                END,
                priority DESC, created_at
        ''', (story_id,))

        tasks = []
        for row in cursor.fetchall():
            tasks.append({
                'id': row[0],
                'description': row[1],
                'status': row[2],
                'phase': row[3],
                'task_level': row[4],
                'assigned_agent': row[5]
            })

        return tasks


def get_hierarchy_summary(project: str) -> Dict:
    """取得專案的任務層級摘要

    Returns:
        {
            'epics': int,
            'stories': int,
            'tasks': int,
            'bugs': int,
            'by_status': {...}
        }
    """
    with managed_connection() as db:
        cursor = db.cursor()

        cursor.execute('''
            SELECT task_level, status, COUNT(*) as cnt
            FROM tasks
            WHERE project = ? AND task_level IS NOT NULL
            GROUP BY task_level, status
        ''', (project,))

        levels = {'epic': 0, 'story': 0, 'task': 0, 'bug': 0}
        by_status = {}

        for row in cursor.fetchall():
            level, status, count = row[0], row[1], row[2]
            if level:
                levels[level] = levels.get(level, 0) + count
            if status not in by_status:
                by_status[status] = {}
            by_status[status][level] = count

    return {
        'epics': levels.get('epic', 0),
        'stories': levels.get('story', 0),
        'tasks': levels.get('task', 0),
        'bugs': levels.get('bug', 0),
        'by_status': by_status
    }


__all__ = [
    'SCHEMA',
    'create_task',
    'create_subtask',
    'get_task',
    'update_task',
    'update_task_status',
    'get_next_task',
    'get_task_progress',
    'log_agent_action',
    'get_all_subtasks',
    'get_unvalidated_tasks',
    'reserve_critic_task',
    'mark_validated',
    'advance_task_phase',
    'get_validation_summary',
    'get_active_tasks_for_project',
    'get_task_branch',
    'set_task_branch',
    'load_branch_context',
    # 層級任務相關
    'get_epic_tasks',
    'get_story_tasks',
    'get_hierarchy_summary'
]
