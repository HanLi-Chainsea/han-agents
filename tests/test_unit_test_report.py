"""Tests for the unit-test run report builder (Part B + Part A coverage persistence)."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestReportHasSummaryAndCoverage:
    """build_unit_test_report returns markdown with project name, coverage table, file names."""

    def test_report_has_summary_and_coverage(self, mock_db_path, tmp_path):
        from servers.tasks import create_task, create_subtask, update_task_status
        from servers.memory import set_working_memory
        from servers.reporting import build_unit_test_report

        # Build a minimal epic with two done executor tasks
        epic_id = create_task(project='myproj', description='Unit Test Epic',
                              task_level='epic')
        story_id = create_task(project='myproj', description='Story for x.py',
                               task_level='story', epic_id=epic_id)
        t1 = create_subtask(parent_id=story_id, description='Write tests for src/x.py',
                            assigned_agent='executor', requires_validation=True,
                            epic_id=epic_id)
        t2 = create_subtask(parent_id=story_id, description='Write tests for src/y.py',
                            assigned_agent='executor', requires_validation=True,
                            epic_id=epic_id)
        update_task_status(t1, 'done', result='done\nTEST_TARGETS: tests/test_x.py')
        update_task_status(t2, 'done', result='done\nTEST_TARGETS: tests/test_y.py')

        # Persist coverage data on the epic (as if the gate stored it)
        coverage_data = [
            {'file_path': 'src/x.py', 'name': 'foo',
             'n_covered': 3, 'n_total': 4, 'missing_branches': [{'from': 2, 'to': 5}]},
            {'file_path': 'src/y.py', 'name': 'bar',
             'n_covered': 2, 'n_total': 2, 'missing_branches': []},
        ]
        set_working_memory(epic_id, 'coverage', json.dumps(coverage_data))

        md = build_unit_test_report(epic_id, 'myproj', str(tmp_path))

        # Summary section
        assert 'myproj' in md
        assert 'executor' in md.lower() or '2' in md  # 2 executor tasks

        # Coverage table must have file names and n/total
        assert 'src/x.py' in md
        assert 'src/y.py' in md
        assert '3/4' in md
        assert '2/2' in md


class TestReportListsUnresolvedCriticSuggestionsWithCount:
    """Tasks with critic_suggestions working-memory show up in the report with a count."""

    def test_report_lists_unresolved_critic_suggestions_with_count(
            self, mock_db_path, tmp_path):
        from servers.tasks import create_task, create_subtask, update_task_status
        from servers.memory import set_working_memory
        from servers.reporting import build_unit_test_report

        epic_id = create_task(project='myproj', description='Unit Test Epic',
                              task_level='epic')
        story_id = create_task(project='myproj', description='Story',
                               task_level='story', epic_id=epic_id)
        t1 = create_subtask(parent_id=story_id, description='Write tests for src/z.py',
                            assigned_agent='executor', requires_validation=True,
                            epic_id=epic_id)
        update_task_status(t1, 'done', result='done')

        # Critic left suggestions on this task (task is done but not yet approved)
        set_working_memory(t1, 'critic_suggestions',
                           'Missing edge case for None input\nAdd negative value test')

        md = build_unit_test_report(epic_id, 'myproj', str(tmp_path))

        assert 'Missing edge case' in md
        assert 'Add negative value test' in md
        # Must also show a count
        assert '2' in md  # 2 suggestions


class TestReportSourceTestMappingFromMarker:
    """A task whose result has TEST_TARGETS: line maps source file to test file."""

    def test_report_source_test_mapping_from_marker(self, mock_db_path, tmp_path):
        from servers.tasks import create_task, create_subtask, update_task_status
        from servers.reporting import build_unit_test_report

        epic_id = create_task(project='myproj', description='Unit Test Epic',
                              task_level='epic')
        story_id = create_task(project='myproj', description='Story for src/a.py',
                               task_level='story', epic_id=epic_id)
        t1 = create_subtask(parent_id=story_id, description='Write tests for src/a.py',
                            assigned_agent='executor', requires_validation=True,
                            epic_id=epic_id)
        update_task_status(t1, 'done',
                           result='Tests written.\nTEST_TARGETS: tests/test_a.py')

        md = build_unit_test_report(epic_id, 'myproj', str(tmp_path))

        # Source-to-test mapping section
        assert 'tests/test_a.py' in md
        assert 'src/a.py' in md


class TestWriteReportCreatesFile:
    """write_unit_test_report writes docs/han-unit-test-run-report.md under project_path."""

    def test_write_report_creates_file(self, mock_db_path, tmp_path):
        from servers.tasks import create_task, create_subtask, update_task_status
        from servers.reporting import write_unit_test_report

        epic_id = create_task(project='myproj', description='Unit Test Epic',
                              task_level='epic')
        story_id = create_task(project='myproj', description='Story',
                               task_level='story', epic_id=epic_id)
        t1 = create_subtask(parent_id=story_id, description='Write tests for src/b.py',
                            assigned_agent='executor', requires_validation=True,
                            epic_id=epic_id)
        update_task_status(t1, 'done', result='done')

        path = write_unit_test_report(epic_id, 'myproj', str(tmp_path))

        expected = os.path.join(str(tmp_path), 'docs', 'han-unit-test-run-report.md')
        assert path == expected
        assert os.path.isfile(expected)
        content = open(expected).read()
        assert 'myproj' in content


# =============================================================================
# R1 — Done/approved tasks must NOT contribute to unresolved suggestions
# =============================================================================

class TestR1ApprovedTaskSuggestionsExcluded:
    """R1: critic_suggestions on an approved/done task must NOT appear as unresolved."""

    def test_approved_task_suggestions_not_in_unresolved(self, mock_db_path, tmp_path):
        """R1: task validated as 'approved' → its critic_suggestions excluded."""
        from servers.tasks import (create_task, create_subtask, update_task_status,
                                   mark_validated)
        from servers.memory import set_working_memory
        from servers.reporting import build_unit_test_report

        epic_id = create_task(project='r1proj', description='Epic R1',
                              task_level='epic')
        story_id = create_task(project='r1proj', description='Story',
                               task_level='story', epic_id=epic_id)
        approved_task = create_subtask(
            parent_id=story_id, description='Write tests for src/approved.py',
            assigned_agent='executor', requires_validation=True,
            epic_id=epic_id
        )
        update_task_status(approved_task, 'done', result='done')
        mark_validated(approved_task, 'approved')

        # Leftover suggestions from a previous critic round — now resolved
        set_working_memory(approved_task, 'critic_suggestions',
                           'Old suggestion that was fixed\nAnother old suggestion')

        md = build_unit_test_report(epic_id, 'r1proj', str(tmp_path))

        # Approved task's suggestions must NOT appear as unresolved
        assert 'Old suggestion that was fixed' not in md
        assert 'Another old suggestion' not in md

    def test_rejected_task_suggestions_included(self, mock_db_path, tmp_path):
        """R1: task with validation_status='rejected' → its suggestions ARE included."""
        from servers.tasks import (create_task, create_subtask, update_task_status,
                                   mark_validated)
        from servers.memory import set_working_memory
        from servers.reporting import build_unit_test_report

        epic_id = create_task(project='r1proj2', description='Epic R1b',
                              task_level='epic')
        story_id = create_task(project='r1proj2', description='Story',
                               task_level='story', epic_id=epic_id)
        rejected_task = create_subtask(
            parent_id=story_id, description='Write tests for src/rejected.py',
            assigned_agent='executor', requires_validation=True,
            epic_id=epic_id
        )
        update_task_status(rejected_task, 'done', result='done')
        mark_validated(rejected_task, 'rejected')

        set_working_memory(rejected_task, 'critic_suggestions',
                           'Still unresolved: add null check\nCheck boundary condition')

        md = build_unit_test_report(epic_id, 'r1proj2', str(tmp_path))

        assert 'Still unresolved: add null check' in md
        assert 'Check boundary condition' in md

    def test_approved_and_rejected_mixed(self, mock_db_path, tmp_path):
        """R1: mix of approved + rejected tasks → only rejected suggestions shown."""
        from servers.tasks import (create_task, create_subtask, update_task_status,
                                   mark_validated)
        from servers.memory import set_working_memory
        from servers.reporting import build_unit_test_report

        epic_id = create_task(project='r1proj3', description='Epic R1c',
                              task_level='epic')
        story_id = create_task(project='r1proj3', description='Story',
                               task_level='story', epic_id=epic_id)

        approved_task = create_subtask(
            parent_id=story_id, description='Approved task',
            assigned_agent='executor', requires_validation=True,
            epic_id=epic_id
        )
        rejected_task = create_subtask(
            parent_id=story_id, description='Rejected task',
            assigned_agent='executor', requires_validation=True,
            epic_id=epic_id
        )
        update_task_status(approved_task, 'done', result='done')
        mark_validated(approved_task, 'approved')
        set_working_memory(approved_task, 'critic_suggestions',
                           'Resolved suggestion — should not appear')

        update_task_status(rejected_task, 'done', result='done')
        mark_validated(rejected_task, 'rejected')
        set_working_memory(rejected_task, 'critic_suggestions',
                           'Open issue: needs retry logic')

        md = build_unit_test_report(epic_id, 'r1proj3', str(tmp_path))

        assert 'Resolved suggestion' not in md
        assert 'Open issue: needs retry logic' in md


# =============================================================================
# R2 — Missing/invalid coverage data → 'unknown', NOT '0/0 ✓'
# =============================================================================

class TestR2MissingCoverageNotFalseGreen:
    """R2: entries missing n_total or n_total==0 must show 'unknown'/neutral, not ✓."""

    def test_missing_n_total_shows_unknown_not_checkmark(self, mock_db_path, tmp_path):
        """R2: coverage entry without n_total → report shows 'unknown', never '0/0 ✓'."""
        from servers.tasks import create_task, create_subtask, update_task_status
        from servers.memory import set_working_memory
        from servers.reporting import build_unit_test_report

        epic_id = create_task(project='r2proj', description='Epic R2',
                              task_level='epic')
        story_id = create_task(project='r2proj', description='Story',
                               task_level='story', epic_id=epic_id)
        t1 = create_subtask(parent_id=story_id, description='Write tests',
                            assigned_agent='executor', requires_validation=True,
                            epic_id=epic_id)
        update_task_status(t1, 'done', result='done')

        # Entry missing n_total key entirely
        coverage_data = [
            {'file_path': 'src/mystery.py', 'name': 'unknown_func'},
        ]
        set_working_memory(epic_id, 'coverage', json.dumps(coverage_data))

        md = build_unit_test_report(epic_id, 'r2proj', str(tmp_path))

        # Must NOT show '0/0' with a checkmark (false-green)
        assert '0/0' not in md or '✓' not in md.split('0/0')[1][:5] if '0/0' in md else True
        # Must show 'unknown' or a neutral marker
        assert 'unknown' in md.lower() or '—' in md or '⚠' in md

    def test_n_total_zero_shows_unknown(self, mock_db_path, tmp_path):
        """R2: n_total==0 → 'unknown'/neutral, not '0/0 ✓'."""
        from servers.tasks import create_task, create_subtask, update_task_status
        from servers.memory import set_working_memory
        from servers.reporting import build_unit_test_report

        epic_id = create_task(project='r2proj2', description='Epic R2b',
                              task_level='epic')
        story_id = create_task(project='r2proj2', description='Story',
                               task_level='story', epic_id=epic_id)
        t1 = create_subtask(parent_id=story_id, description='Write tests',
                            assigned_agent='executor', requires_validation=True,
                            epic_id=epic_id)
        update_task_status(t1, 'done', result='done')

        coverage_data = [
            {'file_path': 'src/empty.py', 'name': 'empty_func',
             'n_covered': 0, 'n_total': 0},
        ]
        set_working_memory(epic_id, 'coverage', json.dumps(coverage_data))

        md = build_unit_test_report(epic_id, 'r2proj2', str(tmp_path))

        # Must NOT show checkmark for 0/0
        assert '0/0 ✓' not in md and '0/0 | ✓' not in md
        # Must show 'unknown' or neutral marker
        assert 'unknown' in md.lower() or '—' in md or '⚠' in md

    def test_valid_coverage_still_shows_checkmark(self, mock_db_path, tmp_path):
        """R2: valid entry with n_covered==n_total > 0 still shows ✓."""
        from servers.tasks import create_task, create_subtask, update_task_status
        from servers.memory import set_working_memory
        from servers.reporting import build_unit_test_report

        epic_id = create_task(project='r2proj3', description='Epic R2c',
                              task_level='epic')
        story_id = create_task(project='r2proj3', description='Story',
                               task_level='story', epic_id=epic_id)
        t1 = create_subtask(parent_id=story_id, description='Write tests',
                            assigned_agent='executor', requires_validation=True,
                            epic_id=epic_id)
        update_task_status(t1, 'done', result='done')

        coverage_data = [
            {'file_path': 'src/good.py', 'name': 'good_func',
             'n_covered': 5, 'n_total': 5},
        ]
        set_working_memory(epic_id, 'coverage', json.dumps(coverage_data))

        md = build_unit_test_report(epic_id, 'r2proj3', str(tmp_path))

        assert '5/5' in md
        assert '✓' in md


# =============================================================================
# R3 — Non-dict coverage entries must not crash the report
# =============================================================================

class TestR3NonDictCoverageEntryRobust:
    """R3: non-dict in coverage list → report builds without crash; entry flagged/skipped."""

    def test_string_in_coverage_list_no_crash(self, mock_db_path, tmp_path):
        """R3: coverage list containing a string → report builds, doesn't raise."""
        from servers.tasks import create_task, create_subtask, update_task_status
        from servers.memory import set_working_memory
        from servers.reporting import build_unit_test_report

        epic_id = create_task(project='r3proj', description='Epic R3',
                              task_level='epic')
        story_id = create_task(project='r3proj', description='Story',
                               task_level='story', epic_id=epic_id)
        t1 = create_subtask(parent_id=story_id, description='Write tests',
                            assigned_agent='executor', requires_validation=True,
                            epic_id=epic_id)
        update_task_status(t1, 'done', result='done')

        # Malformed coverage: list contains a string (non-dict)
        coverage_data = [
            "this is not a dict",
            {'file_path': 'src/valid.py', 'name': 'valid_func',
             'n_covered': 3, 'n_total': 5},
        ]
        set_working_memory(epic_id, 'coverage', json.dumps(coverage_data))

        # Must NOT raise
        md = build_unit_test_report(epic_id, 'r3proj', str(tmp_path))

        # Report must build successfully
        assert 'r3proj' in md
        # Valid entry should still appear
        assert 'src/valid.py' in md

    def test_integer_in_coverage_list_no_crash(self, mock_db_path, tmp_path):
        """R3: coverage list containing an integer → report builds, invalid entry skipped."""
        from servers.tasks import create_task, create_subtask, update_task_status
        from servers.memory import set_working_memory
        from servers.reporting import build_unit_test_report

        epic_id = create_task(project='r3proj2', description='Epic R3b',
                              task_level='epic')
        story_id = create_task(project='r3proj2', description='Story',
                               task_level='story', epic_id=epic_id)
        t1 = create_subtask(parent_id=story_id, description='Write tests',
                            assigned_agent='executor', requires_validation=True,
                            epic_id=epic_id)
        update_task_status(t1, 'done', result='done')

        coverage_data = [42, None, {'file_path': 'src/ok.py', 'name': 'ok',
                                     'n_covered': 1, 'n_total': 1}]
        set_working_memory(epic_id, 'coverage', json.dumps(coverage_data))

        md = build_unit_test_report(epic_id, 'r3proj2', str(tmp_path))

        assert 'r3proj2' in md
        assert 'src/ok.py' in md

    def test_all_non_dict_coverage_list_no_crash(self, mock_db_path, tmp_path):
        """R3: coverage list with only non-dict entries → no section or empty section, no crash."""
        from servers.tasks import create_task, create_subtask, update_task_status
        from servers.memory import set_working_memory
        from servers.reporting import build_unit_test_report

        epic_id = create_task(project='r3proj3', description='Epic R3c',
                              task_level='epic')
        story_id = create_task(project='r3proj3', description='Story',
                               task_level='story', epic_id=epic_id)
        t1 = create_subtask(parent_id=story_id, description='Write tests',
                            assigned_agent='executor', requires_validation=True,
                            epic_id=epic_id)
        update_task_status(t1, 'done', result='done')

        coverage_data = ["bad1", "bad2", 99]
        set_working_memory(epic_id, 'coverage', json.dumps(coverage_data))

        # Must not raise
        md = build_unit_test_report(epic_id, 'r3proj3', str(tmp_path))
        assert 'r3proj3' in md
