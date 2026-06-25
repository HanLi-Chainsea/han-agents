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


# Common source-root prefixes stripped when normalising a target file_path
# to find its JaCoCo package-qualified name (e.g. "src/main/java/", "src/main/kotlin/").
_SRC_PREFIXES = (
    'src/main/java/',
    'src/main/kotlin/',
    'main/java/',
    'main/kotlin/',
)


def _pkg_qualified_key(pkg_name: str, sf_name: str) -> str:
    """Return the lookup key used in the package-qualified sourcefile map.

    JaCoCo nests <sourcefile name="Foo.java"> inside <package name="a/b">,
    so the unique key is 'a/b/Foo.java'.
    """
    return f'{pkg_name}/{sf_name}' if pkg_name else sf_name


def _target_matches_pkg_key(file_path: str, pkg_key: str) -> bool:
    """Return True when *file_path* refers to the same source file as *pkg_key*.

    Strategy (in order):
    1. Exact suffix match:  file_path.endswith(pkg_key)
       covers   "src/main/java/a/b/Foo.java"  →  "a/b/Foo.java"
    2. Strip a known src-root prefix from file_path, then compare:
       "src/main/java/a/b/Foo.java" → "a/b/Foo.java" == pkg_key
    """
    # Normalise slashes just in case
    fp = file_path.replace('\\', '/')
    pk = pkg_key.replace('\\', '/')

    if fp.endswith('/' + pk) or fp == pk:
        return True

    # Strip known source-root prefixes and compare directly
    for prefix in _SRC_PREFIXES:
        if fp.startswith(prefix):
            stripped = fp[len(prefix):]
            if stripped == pk:
                return True

    return False


def parse_jacoco_xml(xml_path: str, coverage_targets: List[Dict], source_root: str) -> Dict:
    """Parse JaCoCo XML report to per-target branch coverage.

    Args:
        xml_path: Path to JaCoCo XML report
        coverage_targets: List of {'file_path', 'name', 'line_start', 'line_end'}
        source_root: Source root directory (for resolving file paths)

    Returns:
        {'tool_status', 'fully_covered', 'per_target', 'error'}
        tool_status: 'ok' | 'schema_error' | 'no_targets' | 'invalid_targets' | 'test_run_error'

    C6 fix: keyed by package-qualified path (<package>/<sourcefile>) to prevent
    basename collisions when multiple packages contain a file with the same name
    (e.g. a/Foo.java and b/Foo.java would previously collide in the basename map,
    letting the later entry silently produce a false-green for the earlier target).
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

    # C6: Build a package-qualified map: "<package>/<sourcefile>" -> sourcefile element.
    # JaCoCo XML nests <sourcefile name="Foo.java"> inside <package name="a/b">.
    # Keying by basename alone causes collision when two packages have identically-named
    # files; using the package-qualified key prevents that false-green.
    sourcefile_map: Dict[str, object] = {}
    for pkg in root.findall('.//package'):
        pkg_name = pkg.get('name') or ''
        for sf in pkg.findall('sourcefile'):
            sf_name = sf.get('name')
            if sf_name:
                key = _pkg_qualified_key(pkg_name, sf_name)
                sourcefile_map[key] = sf

    # Build class map: package-qualified class name -> sourcefilename (for schema_error check).
    # JaCoCo <class name="a/b/Foo" sourcefilename="Foo.java"> is itself a child of <package>.
    class_sourcefilename_map: Dict[str, str] = {}
    for pkg in root.findall('.//package'):
        pkg_name = pkg.get('name') or ''
        for cls in pkg.findall('class'):
            cls_name = cls.get('name')
            src_name = cls.get('sourcefilename')
            if cls_name and src_name:
                # Store with package-qualified source key for lookup
                pkg_key = _pkg_qualified_key(pkg_name, src_name)
                class_sourcefilename_map[cls_name] = pkg_key

    per_target = []

    for target in coverage_targets:
        file_path = target.get('file_path', '')
        name = target.get('name', '')
        line_start = target.get('line_start')
        line_end = target.get('line_end')

        # C6: Find the sourcefile element by package-qualified path match.
        # Iterate all keys in the map and find the one whose pkg-qualified path
        # corresponds to this target's file_path.
        sourcefile = None
        matched_pkg_key = None
        for pkg_key, sf_elem in sourcefile_map.items():
            if _target_matches_pkg_key(file_path, pkg_key):
                sourcefile = sf_elem
                matched_pkg_key = pkg_key
                break

        if sourcefile is None:
            # Check if any class references this source file but it's not in sourcefile list
            # This is a schema error - class present but no sourcefile data
            for cls_name, pkg_key in class_sourcefilename_map.items():
                if _target_matches_pkg_key(file_path, pkg_key):
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


# ── Stack-adaptive backend selection ──────────────────────────────────────────

# C3 fix: added 'junit' so that real Java projects reporting test_tool='junit'
# (as returned by servers/project.py) correctly route to the Java backend
# instead of falling through to 'unknown' and bypassing the JaCoCo gate.
_JAVA_TOOLS = frozenset({'gradle', 'maven', 'junit'})
_JAVA_LANGS = frozenset({'java', 'kotlin'})
_PYTHON_TOOLS = frozenset({'pytest', 'unittest'})
_PYTHON_LANGS = frozenset({'python'})
_JS_TOOLS = frozenset({'vitest', 'jest', 'mocha'})
_JS_LANGS = frozenset({'javascript', 'typescript'})


def select_backend(tech_stack: dict) -> str:
    """Choose coverage backend based on the project's tech_stack.

    Args:
        tech_stack: Dict from ensure_project(...)['tech_stack'], e.g.
                    {'test_tool': 'gradle', 'primary_language': 'java', ...}

    Returns:
        'java'    — gradle/maven/junit test_tool, or java/kotlin primary_language
        'python'  — pytest/unittest test_tool, or python primary_language
        'js'      — vitest/jest/mocha test_tool, or javascript/typescript language
        'unknown' — no recognisable indicator found

    Precedence: test_tool is checked first; primary_language is the tiebreaker.
    The check is case-insensitive.
    """
    if not isinstance(tech_stack, dict):
        return 'unknown'

    tool = (tech_stack.get('test_tool') or '').lower().strip()
    lang = (tech_stack.get('primary_language') or '').lower().strip()

    # test_tool wins — check it first
    if tool in _JAVA_TOOLS:
        return 'java'
    if tool in _PYTHON_TOOLS:
        return 'python'
    if tool in _JS_TOOLS:
        return 'js'

    # Fall back to primary language
    if lang in _JAVA_LANGS:
        return 'java'
    if lang in _PYTHON_LANGS:
        return 'python'
    if lang in _JS_LANGS:
        return 'js'

    return 'unknown'


# ── Java branch-coverage measurement ──────────────────────────────────────────

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
