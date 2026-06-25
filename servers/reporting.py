"""
HAN Unit-Test Run Report

Builds a human-readable markdown report from the Han DB after a /han:unit-test run.
No template engine — just f-strings.  Keep it lean.
"""

import json
import os
import re
from typing import List, Optional

# Marker regex (mirrors servers/coverage.py derive_test_targets)
_TEST_TARGETS_RE = re.compile(
    r'^TEST_TARGETS:\s*(.+)$', re.MULTILINE | re.IGNORECASE
)


def _parse_test_targets_from_result(result_text: str) -> List[str]:
    """Extract TEST_TARGETS: paths from an executor result string."""
    m = _TEST_TARGETS_RE.search(result_text or '')
    if not m:
        return []
    return [p.strip() for p in m.group(1).split(',') if p.strip()]


def _get_executor_tasks_for_epic(epic_id: str) -> List[dict]:
    """Return all executor-role tasks whose epic_id matches (direct or via parent chain)."""
    from servers import managed_connection
    with managed_connection() as db:
        cursor = db.cursor()
        # Tasks where epic_id == epic_id AND assigned_agent == 'executor'
        cursor.execute(
            '''SELECT id, description, status, result, validation_status,
                      assigned_agent, parent_id
               FROM tasks
               WHERE epic_id = ? AND assigned_agent = 'executor'
               ORDER BY created_at''',
            (epic_id,)
        )
        rows = cursor.fetchall()
    return [
        {'id': r[0], 'description': r[1], 'status': r[2],
         'result': r[3], 'validation_status': r[4],
         'assigned_agent': r[5], 'parent_id': r[6]}
        for r in rows
    ]


def build_unit_test_report(epic_id: str, project_name: str, project_path: str) -> str:
    """Build a markdown run report for a /han:unit-test epic.

    Sections:
      1. Summary
      2. Per-file coverage (from persisted 'coverage' working-memory on executor tasks,
         or the epic itself if stored there)
      3. Source <-> test mapping (from TEST_TARGETS: marker in executor results)
      4. Critic verdicts
      5. Unresolved critic suggestions (with count)
      6. Completeness caveat
    """
    from servers.memory import get_working_memory

    executor_tasks = _get_executor_tasks_for_epic(epic_id)
    n_total = len(executor_tasks)
    n_done = sum(1 for t in executor_tasks if t['status'] == 'done')
    n_failed = sum(1 for t in executor_tasks if t['status'] == 'failed')

    lines = []

    # ── 1. Summary ──────────────────────────────────────────────────────────────
    lines.append(f'# HAN Unit-Test Run Report')
    lines.append('')
    lines.append(f'**Project**: {project_name}')
    lines.append(f'**Epic ID**: {epic_id}')
    lines.append(f'**Executor tasks**: {n_total}')
    lines.append(f'**Passed / Failed**: {n_done} / {n_failed}')
    lines.append('')

    # ── 2. Per-file coverage ─────────────────────────────────────────────────────
    # Collect coverage data from working_memory: check executor tasks first,
    # then the epic itself (in case the gate stored it there).
    all_coverage: List[dict] = []
    for t in executor_tasks:
        raw = get_working_memory(t['id'], 'coverage')
        if raw:
            try:
                data = json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(data, list):
                    all_coverage.extend(data)
            except (json.JSONDecodeError, TypeError):
                pass

    # Also check the epic-level coverage key (set by tests directly)
    epic_cov_raw = get_working_memory(epic_id, 'coverage')
    if epic_cov_raw:
        try:
            data = json.loads(epic_cov_raw) if isinstance(epic_cov_raw, str) else epic_cov_raw
            if isinstance(data, list):
                all_coverage.extend(data)
        except (json.JSONDecodeError, TypeError):
            pass

    if all_coverage:
        lines.append('## Per-file Coverage')
        lines.append('')
        lines.append('| File | Function | Covered/Total | Status |')
        lines.append('|------|----------|---------------|--------|')
        for entry in all_coverage:
            fp = entry.get('file_path', '?')
            name = entry.get('name', '?')
            nc = entry.get('n_covered', 0)
            nt = entry.get('n_total', 0)
            mark = '✓' if nc == nt else '⚠️'
            lines.append(f'| {fp} | {name} | {nc}/{nt} | {mark} |')
        lines.append('')

    # ── 3. Source <-> test mapping ───────────────────────────────────────────────
    mapping_rows = []
    for t in executor_tasks:
        desc = t.get('description', '')
        result = t.get('result', '') or ''
        test_targets = _parse_test_targets_from_result(result)
        if test_targets:
            mapping_rows.append((desc, test_targets))

    if mapping_rows:
        lines.append('## Source to Test Mapping')
        lines.append('')
        lines.append('| Task Description | Test Files |')
        lines.append('|-----------------|------------|')
        for desc, targets in mapping_rows:
            short_desc = desc[:60] + '...' if len(desc) > 60 else desc
            targets_str = ', '.join(targets)
            lines.append(f'| {short_desc} | {targets_str} |')
        lines.append('')

    # ── 4. Critic verdicts ───────────────────────────────────────────────────────
    verdicts = [(t['id'], t['validation_status']) for t in executor_tasks
                if t.get('validation_status')]
    if verdicts:
        lines.append('## Critic Verdicts')
        lines.append('')
        for task_id, verdict in verdicts:
            icon = {'approved': '✓', 'rejected': '✗', 'skipped': '—'}.get(verdict or '', '?')
            lines.append(f'- {icon} Task `{task_id}`: {verdict}')
        lines.append('')

    # ── 5. Unresolved critic suggestions ─────────────────────────────────────────
    all_suggestions = []
    for t in executor_tasks:
        raw = get_working_memory(t['id'], 'critic_suggestions')
        if raw:
            suggestions = [s.strip() for s in str(raw).split('\n') if s.strip()]
            all_suggestions.extend(suggestions)

    if all_suggestions:
        lines.append('## Unresolved Critic Suggestions')
        lines.append('')
        lines.append(f'**{len(all_suggestions)} unresolved suggestion(s) to address:**')
        lines.append('')
        for s in all_suggestions:
            lines.append(f'- [ ] {s}')
        lines.append('')

    # ── 6. Completeness caveat ───────────────────────────────────────────────────
    lines.append('---')
    lines.append('')
    lines.append(
        '_Coverage = branch reachability, not assertion quality; '
        'see critic verdicts above for qualitative review._'
    )

    return '\n'.join(lines)


def write_unit_test_report(epic_id: str, project_name: str, project_path: str) -> str:
    """Write the unit-test run report to <project_path>/docs/han-unit-test-run-report.md.

    Creates docs/ if it does not exist.  Returns the absolute path of the written file.
    """
    md = build_unit_test_report(epic_id, project_name, project_path)
    docs_dir = os.path.join(project_path, 'docs')
    os.makedirs(docs_dir, exist_ok=True)
    out_path = os.path.join(docs_dir, 'han-unit-test-run-report.md')
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(md)
    return out_path
