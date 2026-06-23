import os
import subprocess
import tempfile
from typing import Dict, List, Optional

# Use defusedxml to prevent XXE/billion-laughs attacks
try:
    from defusedxml.ElementTree import parse as ET_parse
except ImportError:
    # Fallback if defusedxml not available (though it should be)
    from xml.etree.ElementTree import parse as ET_parse

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_JDIR = os.path.join(_BASE, 'reference', 'tools', 'jacoco')
JACOCO_AGENT = os.path.join(_JDIR, 'jacocoagent.jar')
JACOCO_CLI = os.path.join(_JDIR, 'jacococli.jar')
JACOCO_INIT = os.path.join(_JDIR, 'jacoco-init.gradle')

_TIMEOUT_SEC = 600
_REPORT_TIMEOUT_SEC = 60


def _result(status: str, error: Optional[str] = None,
            per_target: Optional[List[Dict]] = None,
            fully_covered: bool = False) -> Dict:
    """Match the signature from servers/coverage.py."""
    return {'tool_status': status, 'fully_covered': fully_covered,
            'per_target': per_target or [], 'error': error}


def parse_jacoco_xml(xml_path: str, coverage_targets: List[Dict], source_root: str) -> Dict:
    """Parse JaCoCo XML report to per-target branch coverage.

    Args:
        xml_path: Path to JaCoCo XML report
        coverage_targets: List of {'file_path', 'name', 'line_start', 'line_end'}
        source_root: Source root directory (for resolving file paths)

    Returns:
        {'tool_status', 'fully_covered', 'per_target', 'error'}
        tool_status: 'ok' | 'schema_error' | 'no_targets' | 'invalid_targets' | 'test_run_error'
    """
    # Validate targets — mirror the Python backend guard (DRY, fail-closed)
    from servers.coverage import _invalid_targets
    bad = _invalid_targets(coverage_targets)
    if bad:
        return _result('invalid_targets', bad)

    # Validate targets
    if not coverage_targets:
        return _result('no_targets', 'No coverage targets provided')

    try:
        tree = ET_parse(xml_path)
        root = tree.getroot()
    except Exception as e:
        return _result('test_run_error', f'Failed to parse JaCoCo XML: {e}')

    # Build a map: source filename -> sourcefile element
    # <sourcefile name="Classify.java"> contains <line> elements
    sourcefile_map = {}
    for sf in root.findall('.//sourcefile'):
        name = sf.get('name')
        if name:
            sourcefile_map[name] = sf

    # Check if any target's class exists but has no sourcefile (schema_error)
    # Build class map: class name -> sourcefilename from class element
    class_sourcefilename_map = {}
    for cls in root.findall('.//class'):
        cls_name = cls.get('name')
        src_name = cls.get('sourcefilename')
        if cls_name:
            class_sourcefilename_map[cls_name] = src_name

    per_target = []

    for target in coverage_targets:
        file_path = target.get('file_path', '')
        name = target.get('name', '')
        line_start = target.get('line_start')
        line_end = target.get('line_end')

        # Extract source filename from file_path (e.g., "src/main/java/demo/Classify.java" -> "Classify.java")
        source_filename = os.path.basename(file_path)

        # Find the sourcefile element
        sourcefile = sourcefile_map.get(source_filename)
        if sourcefile is None:
            # Check if any class references this source file but it's not in sourcefile list
            # This is a schema error - class present but no sourcefile data
            for cls_name, src_name in class_sourcefilename_map.items():
                if src_name == source_filename:
                    # Class exists but no sourcefile element = schema_error
                    return _result('schema_error',
                                 f'Target {file_path}: class has no sourcefile data')

            # Target file/method entirely absent from XML
            return _result('no_targets', f'Target file not found in coverage: {file_path}')

        # Collect branch data from lines within range
        covered_branches = []
        missing_branches = []

        for line_elem in sourcefile.findall('line'):
            line_nr_str = line_elem.get('nr')
            if line_nr_str is None:
                continue

            try:
                line_nr = int(line_nr_str)
            except ValueError:
                continue

            # Only include lines within target's range
            if not (line_start <= line_nr <= line_end):
                continue

            # Get missed/covered branch counts
            mb_str = line_elem.get('mb')
            cb_str = line_elem.get('cb')

            # Both attributes must be present for valid branch data
            if mb_str is None or cb_str is None:
                continue

            try:
                mb = int(mb_str)
                cb = int(cb_str)
            except ValueError:
                continue

            # If no branches on this line, skip
            if mb == 0 and cb == 0:
                continue

            # Create entries for covered slots (0 to cb-1)
            for slot_idx in range(cb):
                covered_branches.append({'from': line_nr, 'to': slot_idx})

            # Create entries for missed slots (0 to mb-1)
            for slot_idx in range(mb):
                missing_branches.append({'from': line_nr, 'to': slot_idx})

        # If the sourcefile exists but has no BRANCH data for this target's range, schema_error
        if not covered_branches and not missing_branches:
            # Check if sourcefile has any lines at all
            lines_in_file = sourcefile.findall('line')
            if lines_in_file:
                # Has lines but none with branch data in this target's range = schema_error
                return _result('schema_error',
                             f'Target {name} ({file_path}): no branch data in coverage')
            else:
                # No lines in sourcefile at all
                return _result('schema_error',
                             f'Target {file_path}: sourcefile has no line data')

        n_total = len(covered_branches) + len(missing_branches)
        n_covered = len(covered_branches)

        per_target.append({
            'file_path': file_path,
            'name': name,
            'line_start': line_start,
            'line_end': line_end,
            'missing_branches': missing_branches,
            'covered_branches': covered_branches,
            'n_total': n_total,
            'n_covered': n_covered,
        })

    # Determine fully_covered
    fully_covered = all(not pt['missing_branches'] for pt in per_target)

    return _result('ok', None, per_target=per_target, fully_covered=fully_covered)


def measure_branch_coverage_java(
    project_path: str,
    test_targets: List[str],
    coverage_targets: List[Dict],
    *,
    gradle_module: Optional[str] = None,
    test_filters: Optional[List[str]] = None,
) -> Dict:
    """Run a scoped Gradle test with the JaCoCo agent attached via init-script,
    convert the exec file to XML, and parse branch coverage.

    Non-invasive: never modifies build.gradle / settings.gradle / gradle.properties.
    Uses -I (init-script) and -D (system properties) only.

    Args:
        project_path: Absolute path to the Gradle project root (contains gradlew).
        test_targets: Unused for Java (kept for API parity with Python backend).
        coverage_targets: List of {'file_path', 'name', 'line_start', 'line_end'}.
        gradle_module: Optional sub-module name (e.g. 'app' -> ':app:test').
        test_filters: Operative scoping parameter — list of test class/method filters
            passed via --tests to restrict which tests Gradle executes.

    Returns same contract as servers/coverage.py::measure_branch_coverage:
        {'tool_status': 'ok'|'tests_failed'|'no_targets'|'test_run_error'|
                        'schema_error'|'invalid_targets',
         'fully_covered': bool,
         'per_target': [...],
         'error': str|None}
    """
    # Validate targets up front (fail-closed, no subprocesses started on invalid input)
    from servers.coverage import _invalid_targets
    bad = _invalid_targets(coverage_targets)
    if bad:
        return _result('invalid_targets', bad)

    if not coverage_targets:
        return _result('no_targets', 'No coverage targets provided')

    project_path = os.path.realpath(project_path)
    gradlew = os.path.join(project_path, 'gradlew')
    if not os.path.isfile(gradlew):
        return _result('test_run_error', f'gradlew not found at {gradlew}')

    # Determine class/source dirs (per-module or root)
    if gradle_module:
        module_dir = os.path.join(project_path, gradle_module)
        class_dir = os.path.join(module_dir, 'build', 'classes', 'java', 'main')
        source_dir = os.path.join(module_dir, 'src', 'main', 'java')
    else:
        class_dir = os.path.join(project_path, 'build', 'classes', 'java', 'main')
        source_dir = os.path.join(project_path, 'src', 'main', 'java')

    # Build the Gradle task name (e.g. ':app:test' or just 'test')
    gradle_task = f':{gradle_module}:test' if gradle_module else 'test'

    with tempfile.TemporaryDirectory() as tmp:
        exec_file = os.path.join(tmp, 'jacoco.exec')
        xml_file = os.path.join(tmp, 'jacoco.xml')

        # Build the gradlew command
        cmd = [
            gradlew, gradle_task,
            '--no-daemon',
            '-I', JACOCO_INIT,
            f'-Dhan.jacoco.agent={JACOCO_AGENT}',
            f'-Dhan.jacoco.exec={exec_file}',
        ]

        # Add test filters (--tests <filter>)
        if test_filters:
            for f in test_filters:
                cmd += ['--tests', f]

        try:
            run = subprocess.run(
                cmd,
                cwd=project_path,
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_SEC,
            )
        except subprocess.TimeoutExpired:
            return _result('test_run_error',
                           f'Gradle test timed out (>{_TIMEOUT_SEC}s)')
        except Exception as e:
            return _result('test_run_error', f'Failed to launch Gradle: {e}')

        rc = run.returncode

        if rc != 0:
            combined = (run.stdout or '') + (run.stderr or '')
            exec_exists = os.path.isfile(exec_file)
            if exec_exists:
                # Tests ran but some failed — fail-closed: never green-light
                return _result('tests_failed',
                               f'Gradle test task failed (rc={rc}): '
                               + combined[-400:])
            else:
                # No exec produced — could be build error or test failure before exec write
                return _result('tests_failed',
                               f'Gradle test task failed (rc={rc}): '
                               + combined[-400:])

        # Check that exec file was produced
        if not os.path.isfile(exec_file):
            return _result('test_run_error',
                           'Gradle test ran but produced no JaCoCo exec file '
                           '(agent may not have attached)')

        # Check class dir exists (no_targets if absent)
        if not os.path.isdir(class_dir):
            return _result('no_targets',
                           f'Compiled class directory not found: {class_dir}')

        # Run jacococli to produce the XML report
        report_cmd = [
            'java', '-jar', JACOCO_CLI,
            'report', exec_file,
            '--classfiles', class_dir,
            '--sourcefiles', source_dir,
            '--xml', xml_file,
        ]

        try:
            rep = subprocess.run(
                report_cmd,
                cwd=project_path,
                capture_output=True,
                text=True,
                timeout=_REPORT_TIMEOUT_SEC,
            )
        except subprocess.TimeoutExpired:
            return _result('test_run_error',
                           f'jacococli report timed out (>{_REPORT_TIMEOUT_SEC}s)')
        except Exception as e:
            return _result('test_run_error', f'Failed to launch jacococli: {e}')

        if rep.returncode != 0 or not os.path.isfile(xml_file):
            err_tail = ((rep.stderr or '') + (rep.stdout or ''))[-300:]
            return _result('test_run_error',
                           f'jacococli report failed (rc={rep.returncode}): {err_tail}')

        # Parse the XML and return coverage result
        return parse_jacoco_xml(xml_file, coverage_targets, source_dir)
