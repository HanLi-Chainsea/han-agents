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

        # Critic left suggestions on this task
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
