"""Integration-gate: L1 deterministic run + pass via native result XML,
and L2 static mock-smell detection on boundary collaborators.

Public entry points:
  parse_junit_results(xml_paths)                          — pure parser, no subprocess
  run_tests(project_path, stack, ...)                     — runs tests, parses XML, fail-closed
  detect_mocked_collaborators(test_source, collaborators, stack)  — L2 static scanner

Policy is intentionally separate from the branch-coverage gate
(servers/coverage_java.py) so each gate can evolve independently.
"""

import glob
import re
import sys
import os
import subprocess
import tempfile
from typing import Dict, List, Optional

# Use defusedxml to prevent XXE/billion-laughs attacks; fall back to stdlib.
try:
    from defusedxml.ElementTree import parse as ET_parse
except ImportError:
    from xml.etree.ElementTree import parse as ET_parse  # type: ignore[assignment]

_GRADLE_TIMEOUT_SEC = 600
_PYTEST_TIMEOUT_SEC = 300


# ---------------------------------------------------------------------------
# Pure parser
# ---------------------------------------------------------------------------

def parse_junit_results(xml_paths: List[str]) -> Dict:
    """Parse one or more JUnit XML result files and return aggregated counts.

    Handles both:
      - bare ``<testsuite ...>`` documents
      - ``<testsuites>`` wrapper documents containing multiple ``<testsuite>``

    Returns:
        {
          'ran':      bool   — True if at least one parseable testsuite found
          'total':    int    — sum of tests= across all suites
          'failures': int    — sum of failures=
          'errors':   int    — sum of errors=
          'passed':   bool   — True iff ran and total>0 and failures==0 and errors==0
          'error':    str|None — human-readable error description (None on success)
        }

    Fail-closed: any parse error → ran=False, passed=False.
    """
    if not xml_paths:
        return _ran_false("No XML result files provided")

    total = 0
    failures = 0
    errors = 0
    suite_count = 0
    parse_errors = []

    for path in xml_paths:
        if not os.path.isfile(path):
            parse_errors.append(f"File not found: {path}")
            continue
        try:
            tree = ET_parse(path)
            root = tree.getroot()
        except Exception as exc:
            parse_errors.append(f"{os.path.basename(path)}: {exc}")
            continue

        # Collect all <testsuite> elements (regardless of wrapper)
        if root.tag == "testsuite":
            suites = [root]
        elif root.tag == "testsuites":
            suites = list(root.iter("testsuite"))
        else:
            # Unexpected root tag — try iterating for any testsuite
            suites = list(root.iter("testsuite"))

        for suite in suites:
            t = _int_attr(suite, "tests")
            f = _int_attr(suite, "failures")
            e = _int_attr(suite, "errors")
            total += t
            failures += f
            errors += e
            suite_count += 1

    if suite_count == 0:
        if parse_errors:
            return _ran_false("; ".join(parse_errors))
        return _ran_false("No parseable <testsuite> elements found in XML files")

    passed = (failures == 0) and (errors == 0) and (total > 0)
    error_msg = None
    if parse_errors:
        error_msg = "Parse warnings: " + "; ".join(parse_errors)
    return {
        "ran": True,
        "total": total,
        "failures": failures,
        "errors": errors,
        "passed": passed,
        "error": error_msg,
    }


def _int_attr(elem, attr: str, default: int = 0) -> int:
    """Parse an integer XML attribute; return default on missing/invalid."""
    val = elem.get(attr)
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _ran_false(error: str) -> Dict:
    return {
        "ran": False,
        "total": 0,
        "failures": 0,
        "errors": 0,
        "passed": False,
        "error": error,
    }


# ---------------------------------------------------------------------------
# L2: Static mock-smell detector
# ---------------------------------------------------------------------------

def detect_mocked_collaborators(
    test_source: str,
    collaborators: List[str],
    stack: str,
) -> List[str]:
    """Return the subset of *collaborators* that *test_source* mocks out.

    Detection is purely static (regex on source text) — no execution required.
    Fail-closed: when a collaborator is clearly mocked, flag it even if the
    intent is ambiguous.  Do NOT flag real wiring (@Autowired, new C(), import).

    Args:
        test_source:    Full text of the test source file.
        collaborators:  List of collaborator identifiers (fully-qualified or
                        simple type names, e.g. 'com.aile.OrderRepository' or
                        'OrderRepository').
        stack:          Technology stack string; 'java'/'gradle'/'maven' routes
                        to Java patterns; 'python'/'pytest' to Python patterns.

    Returns:
        Ordered list of collaborators (preserving input order) that were found
        to be mocked in *test_source*.
    """
    stack_lower = stack.lower()
    if any(s in stack_lower for s in ("java", "gradle", "maven")):
        return _detect_java(test_source, collaborators)
    elif any(s in stack_lower for s in ("python", "pytest")):
        return _detect_python(test_source, collaborators)
    else:
        # Unknown stack — conservative: scan both patterns
        java_hits = set(_detect_java(test_source, collaborators))
        python_hits = set(_detect_python(test_source, collaborators))
        combined = java_hits | python_hits
        return [c for c in collaborators if c in combined]


def _simple_name(collaborator: str) -> str:
    """Return the simple type name: last segment after '.' or '/'."""
    return collaborator.replace("/", ".").rsplit(".", 1)[-1]


def _detect_java(test_source: str, collaborators: List[str]) -> List[str]:
    """Detect Java mock constructs for the given collaborators.

    Patterns checked (fail-closed — @SpyBean/spy counts as mocked):
      1. @MockBean / @MockitoBean / @Mock / @SpyBean annotation followed
         within ~2 lines by a field declaration whose declared type is C.
         @InjectMocks is explicitly excluded (marks the SUT, not a mock).
      2. Mockito.mock(C.class) or mock(C.class) call.
      3. Mockito.spy(C.class) or spy(C.class) call.
    """
    mocked: set = set()

    for collab in collaborators:
        simple = _simple_name(collab)

        # ---- Pattern 1: annotation + field declaration ----
        # Match @MockBean / @MockitoBean / @Mock / @SpyBean (but NOT @InjectMocks)
        # followed by optional whitespace/lines then a field whose type is the
        # simple name.  We allow up to ~2 lines between annotation and field.
        #
        # Strategy: find each mock annotation block, then check if `simple`
        # appears as a type in the next ~2 lines of text.

        # Regex: annotation keyword, then up to 200 chars (non-greedy) including
        # newlines, then the type name as a word boundary.
        annotation_pattern = re.compile(
            r"@(?:MockBean|MockitoBean|Mock|SpyBean)\b"   # annotation
            r"(?:[^@\n]*\n){0,3}"                         # up to 3 intervening lines
            r"[^\n]*\b" + re.escape(simple) + r"\b",     # type name in field line
            re.MULTILINE,
        )
        if annotation_pattern.search(test_source):
            mocked.add(collab)
            continue

        # ---- Pattern 2 & 3: Mockito.mock/spy call or bare mock/spy call ----
        # Matches: mock(OrderRepository.class) or Mockito.mock(OrderRepository.class)
        # Also mock(com.aile.OrderRepository.class) (FQN) and mock(..., extra args)
        # Allows optional fully-qualified prefix and drops closing ) anchor.
        call_pattern = re.compile(
            r"\b(?:Mockito\.)?(?:mock|spy)\s*\(\s*(?:[A-Za-z0-9_]+\.)*"
            + re.escape(simple) + r"\s*\.class",
        )
        if call_pattern.search(test_source):
            mocked.add(collab)

    return [c for c in collaborators if c in mocked]


def _detect_python(test_source: str, collaborators: List[str]) -> List[str]:
    """Detect Python mock constructs for the given collaborators.

    Patterns checked:
      1. patch('...C') / patch("...C") where C is the simple name or full name
         appearing as the last segment of the dotted path inside the patch string.
         This covers @patch(...), mock.patch(...), unittest.mock.patch(...).
      2. C = MagicMock() / C = Mock() where C is the simple name.
    """
    mocked: set = set()

    for collab in collaborators:
        simple = _simple_name(collab)

        # ---- Pattern 1: patch('...C') where the string ends with .C or is C ----
        # Matches both full dotted path and simple name at end of path.
        # We look for the collaborator name (full or simple) as the last
        # segment inside a patch string literal.
        # e.g.  patch('app.svc.OrderRepository')   → simple name at end
        #        patch('OrderRepository')            → simple name
        #        @patch('a.b.OrderRepository')

        # Build pattern that matches the collaborator appearing as the last
        # segment (or whole path) in a patch()/patch.object() string.
        patch_pattern = re.compile(
            r"""(?:@|\b)(?:unittest\.mock\.|mock\.)?patch\s*\(\s*['"]"""
            r"""(?:[A-Za-z0-9_.]*\.)?"""   # optional dotted prefix
            + re.escape(simple)             # the simple name
            + r"""['"]""",                  # closing quote
        )
        if patch_pattern.search(test_source):
            mocked.add(collab)
            continue

        # Also match the full collaborator path literally inside patch string
        if collab != simple:
            full_patch_pattern = re.compile(
                r"""(?:@|\b)(?:unittest\.mock\.|mock\.)?patch\s*\(\s*['"]"""
                + re.escape(collab)
                + r"""['"]""",
            )
            if full_patch_pattern.search(test_source):
                mocked.add(collab)
                continue

        # ---- Pattern 2: C = MagicMock() or C = Mock() ----
        assign_pattern = re.compile(
            r"\b" + re.escape(simple) + r"\s*=\s*(?:MagicMock|Mock)\s*\(",
        )
        if assign_pattern.search(test_source):
            mocked.add(collab)

    return [c for c in collaborators if c in mocked]


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

def run_tests(
    project_path: str,
    stack: str,
    *,
    gradle_module: Optional[str] = None,
    test_filters: Optional[List[str]] = None,
    py_test_files: Optional[List[str]] = None,
) -> Dict:
    """Run integration tests and parse native XML results.

    Fail-closed: never returns passed=True without parsed XML evidence.

    Args:
        project_path:   Absolute path to the project root.
        stack:          'java' or 'python'.
        gradle_module:  Optional Gradle sub-module name (Java only).
        test_filters:   List of --tests filters (Java) or extra args (Python).
        py_test_files:  Python test file paths (Python only).

    Returns:
        {
          'ran':      bool
          'passed':   bool
          'total':    int
          'failures': int
          'errors':   int
          'error':    str|None
          'evidence': dict   — command run + result-xml location
        }
    """
    project_path = os.path.realpath(project_path)

    if stack == "java":
        return _run_java(project_path, gradle_module=gradle_module,
                         test_filters=test_filters)
    elif stack == "python":
        return _run_python(project_path, py_test_files=py_test_files,
                           test_filters=test_filters)
    else:
        return _failed(f"Unknown stack: {stack!r}", evidence={"stack": stack})


# ---------------------------------------------------------------------------
# Java backend
# ---------------------------------------------------------------------------

def _run_java(
    project_path: str,
    *,
    gradle_module: Optional[str] = None,
    test_filters: Optional[List[str]] = None,
) -> Dict:
    gradlew = os.path.join(project_path, "gradlew")
    if not os.path.isfile(gradlew):
        return _failed(f"gradlew not found at {gradlew}",
                       evidence={"project_path": project_path})

    # Build task name: ':module:test' or 'test'
    gradle_task = f":{gradle_module}:test" if gradle_module else "test"

    cmd = [gradlew, gradle_task, "--no-daemon"]
    if test_filters:
        for f in test_filters:
            cmd += ["--tests", f]

    evidence = {
        "command": " ".join(cmd),
        "project_path": project_path,
        "gradle_module": gradle_module,
    }

    try:
        run = subprocess.run(
            cmd,
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=_GRADLE_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        return _failed(
            f"Gradle test timed out (>{_GRADLE_TIMEOUT_SEC}s)",
            evidence=evidence,
        )
    except Exception as exc:
        return _failed(f"Failed to launch Gradle: {exc}", evidence=evidence)

    # Locate result XML directory
    if gradle_module:
        # Module path may contain slashes (e.g. 'app/core') — keep as-is
        xml_dir = os.path.join(project_path, gradle_module,
                               "build", "test-results", "test")
    else:
        xml_dir = os.path.join(project_path, "build", "test-results", "test")

    evidence["result_xml_dir"] = xml_dir

    if run.returncode != 0:
        combined = (run.stdout or "") + (run.stderr or "")
        evidence["gradle_rc"] = run.returncode
        evidence["gradle_output_tail"] = combined[-400:]
        return _failed(
            f"Gradle test task failed (rc={run.returncode}): {combined[-200:]}",
            evidence=evidence,
        )

    # Find result XMLs
    xml_paths = glob.glob(os.path.join(xml_dir, "*.xml"))
    if not xml_paths:
        return _failed(
            f"Gradle test passed but no result XML found in {xml_dir}",
            evidence=evidence,
        )

    parsed = parse_junit_results(xml_paths)
    result = dict(parsed)
    result["evidence"] = evidence
    return result


# ---------------------------------------------------------------------------
# Python backend
# ---------------------------------------------------------------------------

def _run_python(
    project_path: str,
    *,
    py_test_files: Optional[List[str]] = None,
    test_filters: Optional[List[str]] = None,
) -> Dict:
    files = py_test_files or []

    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tmp:
        xml_out = tmp.name

    cmd = [sys.executable, "-m", "pytest"] + files + [f"--junitxml={xml_out}", "-q"]
    if test_filters:
        cmd += list(test_filters)

    evidence = {
        "command": " ".join(cmd),
        "project_path": project_path,
        "result_xml": xml_out,
    }

    try:
        run = subprocess.run(
            cmd,
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=_PYTEST_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        os.unlink(xml_out)
        return _failed(
            f"pytest timed out (>{_PYTEST_TIMEOUT_SEC}s)",
            evidence=evidence,
        )
    except Exception as exc:
        os.unlink(xml_out)
        return _failed(f"Failed to launch pytest: {exc}", evidence=evidence)

    # Parse XML regardless of rc — pytest writes XML even on test failure
    if not os.path.isfile(xml_out):
        evidence["pytest_rc"] = run.returncode
        return _failed(
            f"pytest did not produce JUnit XML at {xml_out}",
            evidence=evidence,
        )

    try:
        parsed = parse_junit_results([xml_out])
    finally:
        try:
            os.unlink(xml_out)
        except OSError:
            pass

    result = dict(parsed)
    result["evidence"] = evidence

    # If pytest returned non-zero AND we have no failures/errors recorded in
    # XML, treat as infra error (fail-closed).
    if run.returncode != 0 and result["passed"]:
        combined = (run.stdout or "") + (run.stderr or "")
        evidence["pytest_rc"] = run.returncode
        evidence["pytest_output_tail"] = combined[-300:]
        result["passed"] = False
        result["error"] = (
            f"pytest exited rc={run.returncode} but XML showed 0 failures "
            f"— treating as infra error (fail-closed)"
        )

    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _failed(error: str, *, evidence: Optional[Dict] = None) -> Dict:
    return {
        "ran": False,
        "passed": False,
        "total": 0,
        "failures": 0,
        "errors": 0,
        "error": error,
        "evidence": evidence or {},
    }
