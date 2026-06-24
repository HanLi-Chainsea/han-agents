"""Integration-gate: L1 deterministic run + pass via native result XML,
and L2 static mock-smell detection on boundary collaborators.

Public entry points:
  parse_junit_results(xml_paths)                          — pure parser, no subprocess
  run_tests(project_path, stack, ...)                     — runs tests, parses XML, fail-closed
  detect_mocked_collaborators(test_source, collaborators, stack)  — L2 static scanner
  boundaries_for_target(project_name, target_files)       — B3: boundary extraction from Code Graph
  derive_integration_test_files(project_path, executor_result)   — parse TEST_TARGETS: marker

Policy is intentionally separate from the branch-coverage gate
(servers/coverage_java.py) so each gate can evolve independently.
"""

import glob
import re
import shutil
import sys
import os
import subprocess
import tempfile
from typing import Dict, List, Optional

import servers.code_graph as cg

# Use defusedxml to prevent XXE/billion-laughs attacks; fall back to stdlib.
try:
    from defusedxml.ElementTree import parse as ET_parse
except ImportError:
    from xml.etree.ElementTree import parse as ET_parse  # type: ignore[assignment]

_GRADLE_TIMEOUT_SEC = 600
_PYTEST_TIMEOUT_SEC = 300


# ---------------------------------------------------------------------------
# Marker-based test file derivation
# ---------------------------------------------------------------------------

def derive_integration_test_files(
    project_path: str,
    executor_result: Optional[str],
) -> List[str]:
    """Parse the ``TEST_TARGETS:`` marker from the executor's result text and
    return a list of existing test file paths relative to the project root.

    Mirrors ``coverage.derive_test_targets`` in structure: reuses
    ``coverage._MARKER_RE`` and the same validation logic (path must exist
    under project root AND look like a test file).  No fallback heuristic —
    integration gate is marker-only (fail-closed if no marker).

    Returns:
        Sorted list of relative paths (may be empty if no valid marker found).
    """
    from servers import coverage as _cov

    root = os.path.realpath(project_path)
    found: List[str] = []

    for m in _cov._MARKER_RE.findall(executor_result or ''):
        for raw in re.split(r'[,\s]+', m.strip()):
            if not raw:
                continue
            # Resolve to absolute, confirm it lives under project root
            cand = raw if os.path.isabs(raw) else os.path.join(root, raw)
            cand = os.path.realpath(cand)
            if not (cand == root or cand.startswith(root + os.sep)):
                continue  # path escape — skip
            if not os.path.isfile(cand):
                continue
            # Must look like a test file (path segment / naming convention)
            from servers.recipes import is_test_file
            if not is_test_file(raw):
                continue
            rel = os.path.relpath(cand, root)
            if rel not in found:
                found.append(rel)

    return sorted(found)


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
          'ran':      bool   — True if at least one cleanly-parsed testsuite found
          'total':    int    — sum of tests= across all clean suites
          'failures': int    — sum of failures=
          'errors':   int    — sum of errors=
          'skipped':  int    — sum of skipped= across all clean suites
          'passed':   bool   — True iff ran and (total-skipped)>0 and failures==0 and errors==0
          'error':    str|None — human-readable error description (None on clean success)
        }

    Fail-closed:
      - Missing or non-numeric failures/errors attribute → that suite is a parse
        error; it does NOT count toward clean_suite_count.
      - If NO suite parses cleanly → ran=False, passed=False.
      - All-skipped suite (total==skipped) → passed=False even with 0 failures.
    """
    if not xml_paths:
        return _ran_false("No XML result files provided")

    total = 0
    failures = 0
    errors = 0
    skipped = 0
    clean_suite_count = 0
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
            # G2b: tests attribute must also be strictly numeric (not lenient default to 0)
            t = _strict_int_attr(suite, "tests")
            # C7: failures and errors must be present and numeric; if not → parse error
            f = _strict_int_attr(suite, "failures")
            e = _strict_int_attr(suite, "errors")
            if t is None or f is None or e is None:
                name = suite.get("name", "<unnamed>")
                missing = []
                if t is None:
                    missing.append("tests")
                if f is None:
                    missing.append("failures")
                if e is None:
                    missing.append("errors")
                parse_errors.append(
                    f"Suite '{name}': missing/non-numeric attribute(s): {', '.join(missing)}"
                )
                continue  # This suite does not count as clean
            # D5 F2: skipped= must also be strictly numeric; non-numeric is a parse error.
            sk = _strict_int_attr(suite, "skipped")
            if sk is None:
                name = suite.get("name", "<unnamed>")
                parse_errors.append(
                    f"Suite '{name}': missing/non-numeric attribute(s): skipped"
                )
                continue  # This suite does not count as clean
            # G2: counts must be non-negative and consistent
            # Fail-closed: any violation → suite is malformed
            if f < 0 or e < 0 or sk < 0 or t < 0:
                name = suite.get("name", "<unnamed>")
                parse_errors.append(
                    f"Suite '{name}': negative count(s) — tests={t}, failures={f}, errors={e}, skipped={sk}"
                )
                continue  # This suite does not count as clean
            # G2: skipped must not exceed tests (consistency check)
            if sk > t:
                name = suite.get("name", "<unnamed>")
                parse_errors.append(
                    f"Suite '{name}': skipped ({sk}) > tests ({t}) — inconsistent"
                )
                continue  # This suite does not count as clean
            
            total += t
            failures += f
            errors += e
            skipped += sk
            clean_suite_count += 1

    if clean_suite_count == 0:
        if parse_errors:
            return _ran_false("; ".join(parse_errors))
        return _ran_false("No parseable <testsuite> elements found in XML files")

    # C7: passed requires executed-and-passed tests (total - skipped > 0)
    executed = total - skipped
    # C-d hard gate: ANY parse error (unreadable file OR malformed suite) makes
    # the entire result passed=False.  A mixed valid+corrupt batch is never trusted
    # because the corrupt file might have hidden the real failures.
    clean_run = (failures == 0) and (errors == 0) and (executed > 0) and (not parse_errors)
    passed = clean_run
    error_msg = None
    if parse_errors:
        error_msg = "Parse errors (hard gate): " + "; ".join(parse_errors)
    return {
        "ran": True,
        "total": total,
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
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


def _strict_int_attr(elem, attr: str) -> Optional[int]:
    """Parse an integer XML attribute strictly; return None on missing/invalid.

    Used for failure-critical attributes (failures, errors) where a missing
    or non-numeric value must NOT be treated as zero — it is a parse failure.
    """
    val = elem.get(attr)
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _ran_false(error: str) -> Dict:
    return {
        "ran": False,
        "total": 0,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
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
      1. @MockBean / @MockitoBean / @Mock / @SpyBean / @MockitoSpyBean annotation
         followed within ~3 lines by a field declaration whose declared type is C.
         @InjectMocks is explicitly excluded (marks the SUT, not a mock).
      2. Mockito.mock(C.class) or mock(C.class) call.
      3. Mockito.spy(C.class) or spy(C.class) call.
      4. Mockito.spy(new C( or spy(new C( — spy-on-real-instance pattern.
      5. mockConstruction(C.class — Mockito static construction mock.
      6. Mockito.mockStatic(C.class — static method mocking.
    """
    mocked: set = set()

    for collab in collaborators:
        simple = _simple_name(collab)

        # ---- Pattern 1: annotation + field declaration ----
        # Match @MockBean / @MockitoBean / @Mock / @SpyBean / @MockitoSpyBean
        # (but NOT @InjectMocks) followed by optional whitespace/lines then a
        # field whose type is the simple name.  We allow up to ~3 lines between
        # annotation and field.
        annotation_pattern = re.compile(
            r"@(?:MockBean|MockitoBean|Mock|SpyBean|MockitoSpyBean)\b"  # annotation
            r"(?:[^@\n]*\n){0,3}"                                       # up to 3 intervening lines
            r"[^\n]*\b" + re.escape(simple) + r"\b",                   # type name in field line
            re.MULTILINE,
        )
        if annotation_pattern.search(test_source):
            mocked.add(collab)
            continue

        # ---- Pattern 2 & 3: Mockito.mock/spy(C.class) or bare mock/spy(C.class) ----
        # Matches: mock(OrderRepository.class) or Mockito.mock(OrderRepository.class)
        # Also mock(com.aile.OrderRepository.class) (FQN) and mock(..., extra args)
        call_pattern = re.compile(
            r"\b(?:Mockito\.)?(?:mock|spy)\s*\(\s*(?:[A-Za-z0-9_]+\.)*"
            + re.escape(simple) + r"\s*\.class",
        )
        if call_pattern.search(test_source):
            mocked.add(collab)
            continue

        # ---- Pattern 4: spy(new C( or Mockito.spy(new C( ----
        spy_new_pattern = re.compile(
            r"\b(?:Mockito\.)?spy\s*\(\s*new\s+"
            + re.escape(simple) + r"\s*\(",
        )
        if spy_new_pattern.search(test_source):
            mocked.add(collab)
            continue

        # ---- Pattern 5: mockConstruction(C.class ----
        mock_construction_pattern = re.compile(
            r"\bmockConstruction\s*\(\s*(?:[A-Za-z0-9_]+\.)*"
            + re.escape(simple) + r"\s*\.class",
        )
        if mock_construction_pattern.search(test_source):
            mocked.add(collab)
            continue

        # ---- Pattern 6: Mockito.mockStatic(C.class or mockStatic(C.class ----
        mock_static_pattern = re.compile(
            r"\b(?:Mockito\.)?mockStatic\s*\(\s*(?:[A-Za-z0-9_]+\.)*"
            + re.escape(simple) + r"\s*\.class",
        )
        if mock_static_pattern.search(test_source):
            mocked.add(collab)

    return [c for c in collaborators if c in mocked]


def _detect_python(test_source: str, collaborators: List[str]) -> List[str]:
    """Detect Python mock constructs for the given collaborators.

    Patterns checked:
      1a. patch('...C...') / @patch("...") / mocker.patch("..."):
          C's simple name appears as a DOTTED PATH SEGMENT anywhere in the quoted
          target string: (^|.)C(.|$) within the string.  This catches both
          patch('app.svc.OrderRepository') (final segment) AND
          patch('app.repo.OrderRepository.find_all') (middle segment — method mock).
      1b. Full collaborator path literal inside patch string (for FQN collaborators).
      2.  C = MagicMock() / C = Mock() where C is the simple name.
      3a. patch.object(C, ...) or mocker.patch.object(C, ...) where C is the
          FIRST argument (the class/object being patched).  Matches bare name or
          module-qualified name (pkg.C).
      3b. patch.object(<anything>, 'C') — original pattern: second arg is string 'C'.
      4.  create_autospec(C where C is the simple name.
      5.  MagicMock(spec=C) or Mock(spec=C) where C is the simple name.
      6a. monkeypatch.setattr(C, ...) / setattr(C, ...) where C is the FIRST arg
          (the target object/class) — simple name or ends with .C.
      6b. monkeypatch.setattr(<target>, <attr>, C) — original: third arg is C.
    """
    mocked: set = set()

    for collab in collaborators:
        simple = _simple_name(collab)

        # ---- Pattern 1a: patch('...') where C appears as a DOTTED PATH SEGMENT ----
        # C's simple name must appear as (^|.)C(.|$) within the quoted string.
        # This catches: patch('app.repo.OrderRepository.find_all') where C=OrderRepository
        # as well as the final-segment case patch('app.svc.OrderRepository').
        # mocker.patch / @patch / patch / mock.patch / unittest.mock.patch all covered.
        patch_segment_pattern = re.compile(
            r"""(?:@|\b)(?:unittest\.mock\.|mock\.)?(?:mocker\.)?patch\s*\(\s*['"]"""
            r"""[A-Za-z0-9_.]*"""             # any dotted prefix (possibly empty)
            r"""(?:^|(?<=\.))"""             # C starts at string start or after a dot
            + re.escape(simple)               # the simple name
            + r"""(?=\.|['"])""",            # followed by a dot or closing quote
            re.VERBOSE,
        )
        # Use a simpler, reliable approach: look for the segment boundary inline
        patch_segment_pattern2 = re.compile(
            r"""(?:@|\b)(?:unittest\.mock\.|mock\.)?(?:mocker\.)?patch\s*\(\s*['"]"""
            r"""(?:[A-Za-z0-9_]+\.)*"""     # zero or more preceding segments
            + re.escape(simple)               # the simple name
            + r"""(?:\.[A-Za-z0-9_]+)*"""   # zero or more following segments
            + r"""['"]""",                    # closing quote
        )
        if patch_segment_pattern2.search(test_source):
            mocked.add(collab)
            continue

        # Also match the full collaborator path literally inside patch string
        if collab != simple:
            full_patch_pattern = re.compile(
                r"""(?:@|\b)(?:unittest\.mock\.|mock\.)?(?:mocker\.)?patch\s*\(\s*['"]"""
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
            continue

        # ---- Pattern 3a: patch.object(C, ...) — first arg is the collaborator class ----
        # Matches: patch.object(OrderRepository, ...) or patch.object(pkg.OrderRepository, ...)
        # mocker.patch.object also covered.
        patch_object_first_arg_pattern = re.compile(
            r"""\b(?:mocker\.)?patch\.object\s*\(\s*"""
            r"""(?:[A-Za-z0-9_]+\.)*"""     # optional module prefix (pkg.)
            + re.escape(simple)               # the collaborator simple name
            + r"""\s*,""",                   # followed by a comma (more args follow)
        )
        if patch_object_first_arg_pattern.search(test_source):
            mocked.add(collab)
            continue

        # ---- Pattern 3b: patch.object(<obj>, 'C') — second arg is string 'C' ----
        # Original pattern: matches patch.object(anything, 'OrderRepository')
        patch_object_second_arg_pattern = re.compile(
            r"""\b(?:mocker\.)?patch\.object\s*\([^)]*['"]"""
            + re.escape(simple)
            + r"""['"]""",
        )
        if patch_object_second_arg_pattern.search(test_source):
            mocked.add(collab)
            continue

        # ---- Pattern 4: create_autospec(C ----
        create_autospec_pattern = re.compile(
            r"""\bcreate_autospec\s*\(\s*""" + re.escape(simple) + r"""\b""",
        )
        if create_autospec_pattern.search(test_source):
            mocked.add(collab)
            continue

        # ---- Pattern 5: MagicMock(spec=C) or Mock(spec=C) ----
        spec_pattern = re.compile(
            r"""\b(?:MagicMock|Mock)\s*\([^)]*spec\s*=\s*"""
            + re.escape(simple) + r"""\b""",
        )
        if spec_pattern.search(test_source):
            mocked.add(collab)
            continue

        # ---- Pattern 6a: monkeypatch.setattr(C, ...) / setattr(C, ...) ----
        # First argument is C (the target class/object being patched).
        # Matches: monkeypatch.setattr(OrderRepository, 'attr', val)
        #          monkeypatch.setattr(pkg.OrderRepository, 'attr', val)
        monkeypatch_first_arg_pattern = re.compile(
            r"""\b(?:monkeypatch\.setattr|setattr)\s*\(\s*"""
            r"""(?:[A-Za-z0-9_]+\.)*"""     # optional module prefix
            + re.escape(simple)               # the collaborator simple name
            + r"""\s*,""",                   # followed by a comma
        )
        if monkeypatch_first_arg_pattern.search(test_source):
            mocked.add(collab)
            continue

        # ---- Pattern 6b: monkeypatch.setattr(<target>, <attr>, C) ----
        # The simple name appears as the third positional argument (the replacement).
        monkeypatch_third_arg_pattern = re.compile(
            r"""\bmonkeypatch\.setattr\s*\([^)]*,\s*"""
            + re.escape(simple) + r"""\s*[,)]""",
        )
        if monkeypatch_third_arg_pattern.search(test_source):
            mocked.add(collab)

    return [c for c in collaborators if c in mocked]


# ---------------------------------------------------------------------------
# B3: Boundary extraction from Code Graph
# ---------------------------------------------------------------------------

# Edge kinds that represent runtime collaboration (injects/call family).
# imports, extends, implements are structural-only; drop them (design hard req).
_INJECT_KINDS = frozenset({"injects"})
_CALL_KINDS = frozenset({"calls", "call", "invokes"})
_KEEP_KINDS = _INJECT_KINDS | _CALL_KINDS


def boundaries_for_target(
    project_name: str,
    target_files: List[str],
) -> List[Dict]:
    """Extract integration boundaries for the given target source files.

    An integration boundary is an outgoing injects/call edge from a node
    defined in one of *target_files* to a node defined in a different file.

    Args:
        project_name:  The project name used to look up the Code Graph.
        target_files:  List of file paths (as stored in code_graph) that
                       define the caller side of the boundary.

    Returns:
        Deduplicated list of boundary dicts:
            {
              'caller':      str  — FQN of the calling node
              'callee':      str  — FQN of the called/injected node
              'callee_file': str  — file_path of the callee node
              'edge':        str  — 'injects' or 'calls'
            }
        Returns [] when the project has no graph or no nodes match.
    """
    if not target_files:
        return []

    # Step 1: collect all nodes whose file_path is in target_files.
    # Build a lookup: node_id -> node dict for caller-side nodes.
    # C8: pass limit=1_000_000 to avoid silent truncation of high-fan-out modules.
    # ponytail: 1e6 ceiling
    caller_nodes: Dict[str, Dict] = {}
    for file_path in target_files:
        nodes = cg.get_code_nodes(project_name, file_path=file_path, limit=1_000_000)  # ponytail: 1e6 ceiling
        for node in nodes:
            caller_nodes[node["id"]] = node

    if not caller_nodes:
        return []

    # Step 2: fetch all nodes in the project to allow callee resolution by id.
    # get_code_nodes has a limit; for boundary extraction we only need the
    # callee nodes that appear in edges, so build a lazy id->node index.
    all_nodes_list = cg.get_code_nodes(project_name, limit=1_000_000)  # ponytail: hard ceiling 1e6 nodes; switch to by-id lookup if a project exceeds it
    all_nodes_by_id: Dict[str, Dict] = {n["id"]: n for n in all_nodes_list}

    # Step 3+4: for each caller node, get outgoing edges, keep injects/call
    # kinds, resolve callee, build boundary dict.
    # C8: pass limit=1_000_000 to avoid silent truncation at 100 edges.
    # ponytail: 1e6 ceiling
    seen: set = set()
    boundaries: List[Dict] = []

    for node_id, caller_node in caller_nodes.items():
        edges = cg.get_code_edges(project_name, from_id=node_id, limit=1_000_000)  # ponytail: 1e6 ceiling
        for edge in edges:
            edge_kind_lower = edge.get("kind", "").lower()
            if edge_kind_lower not in _KEEP_KINDS:
                continue  # drop imports, extends, implements, etc.

            callee_id = edge.get("to_id")
            if not callee_id:
                continue

            callee_node = all_nodes_by_id.get(callee_id)
            if not callee_node:
                # callee not in our index (external / outside limit) — skip
                continue

            caller_fqn = caller_node.get("name", caller_node["id"])
            callee_fqn = callee_node.get("name", callee_node["id"])
            callee_file = callee_node.get("file_path", "")

            # Map edge kind to canonical label
            edge_label = "injects" if edge_kind_lower in _INJECT_KINDS else "calls"

            key = (caller_fqn, callee_fqn, edge_label)
            if key in seen:
                continue
            seen.add(key)

            boundaries.append({
                "caller": caller_fqn,
                "callee": callee_fqn,
                "callee_file": callee_file,
                "edge": edge_label,
            })

    return boundaries


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

    # Locate result XML directory
    if gradle_module:
        # Module path may contain slashes (e.g. 'app/core') — keep as-is
        xml_dir = os.path.join(project_path, gradle_module,
                               "build", "test-results", "test")
    else:
        xml_dir = os.path.join(project_path, "build", "test-results", "test")

    # C7b: Delete stale test-results before running Gradle so an UP-TO-DATE
    # or NO-SOURCE Gradle run cannot be scored off a previous run's XML.
    # Major fix: delete failure is fail-closed — stale XML may hide real failures.
    if os.path.isdir(xml_dir):
        try:
            shutil.rmtree(xml_dir)
        except OSError as _del_err:
            return _failed(
                f"Cannot delete stale test-results dir {xml_dir}: {_del_err} "
                f"— aborting to prevent scoring off stale XML (fail-closed)",
                evidence={"project_path": project_path, "xml_dir": xml_dir},
            )

    evidence = {
        "command": " ".join(cmd),
        "project_path": project_path,
        "gradle_module": gradle_module,
        "result_xml_dir": xml_dir,
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

    if run.returncode != 0:
        combined = (run.stdout or "") + (run.stderr or "")
        evidence["gradle_rc"] = run.returncode
        evidence["gradle_output_tail"] = combined[-400:]
        return _failed(
            f"Gradle test task failed (rc={run.returncode}): {combined[-200:]}",
            evidence=evidence,
        )

    # Find result XMLs — must be FRESH (xml_dir was cleared before the run)
    xml_paths = glob.glob(os.path.join(xml_dir, "*.xml"))
    if not xml_paths:
        return _failed(
            f"Gradle test passed but no fresh result XML found in {xml_dir}",
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


# ---------------------------------------------------------------------------
# B4: Integration gate policy — L1 (hard) + L2 (hard) + L3 (advisory)
# ---------------------------------------------------------------------------

_LABEL_VERIFIED_REAL = 'verified-real'
_LABEL_MOCKED = 'mocked'
_LABEL_NOT_OBSERVED = 'not-observed'
_LABEL_NOT_MEASURABLE = 'not-measurable'

# Symbol map for format_boundary_summary
_LABEL_SYMBOL = {
    _LABEL_VERIFIED_REAL: '✓',
    _LABEL_MOCKED: '✗',
    _LABEL_NOT_OBSERVED: '⚠️',
    _LABEL_NOT_MEASURABLE: '➖',
}


def format_boundary_summary(
    boundaries_with_labels: List[Dict],
    *,
    l1_total: Optional[int] = None,
) -> List[str]:
    """Return a human-readable list of strings for the integration boundary report.

    Each boundary produces one line:
        🔗 Caller→Callee ✓ verified-real
        🔗 Caller→Callee ✗ mocked
        🔗 Caller→Callee ⚠️ not-observed
        🔗 Caller→Callee ➖ not-measurable

    Optionally prepends an L1 test-pass line when *l1_total* is given.

    Mirrors the spirit of coverage.format_coverage_summary.
    """
    lines = []
    if l1_total is not None:
        lines.append(f"✅ L1 integration tests passed ({l1_total} tests)")
    for b in boundaries_with_labels:
        label = b.get('label', _LABEL_NOT_MEASURABLE)
        symbol = _LABEL_SYMBOL.get(label, '?')
        caller = b.get('caller', '?')
        callee = b.get('callee', '?')
        lines.append(f"🔗 {caller}→{callee} {symbol} {label}")
    return lines


def _classify_boundary_l3(
    boundary: Dict,
    project_path: str,
    test_files: List[str],
) -> str:
    """Classify a single boundary into one of the 4 L3 labels using coverage.

    Backend is selected internally via select_backend(tech_stack); caller need
    not supply the stack.

    This is advisory-only: any exception or unavailability → 'not-measurable'.
    Never raises; always returns a label string.
    """
    try:
        from servers import coverage as cov
        from servers import coverage_java as cov_java
        from servers import project as proj_mod

        # Determine backend
        _ts = (proj_mod.ensure_project(
            boundary.get('_project_name', ''),
            project_path,
        ).get('tech_stack') or {})
        backend = cov_java.select_backend(_ts)

        callee_file = boundary.get('callee_file', '')
        callee_name = boundary.get('callee', '?')

        # We measure coverage of the callee file/class.
        # Build a minimal coverage_target for the callee.
        # line_start/end are required; since we don't know exact range,
        # use a wide range (1-9999) — the backend will filter by what's in
        # the file; any coverage hit counts as "observed".
        coverage_target = [{
            'file_path': callee_file,
            'name': callee_name,
            'line_start': 1,
            'line_end': 9999,
        }]

        if backend == 'java':
            # ponytail: L3 re-runs coverage per boundary without test_filters
            # (L3 is ADVISORY only — never changes the verdict).
            res = cov_java.measure_branch_coverage_java(
                project_path, test_files, coverage_target)
        else:
            # Python or unknown — use pytest/coverage backend
            if not cov._coverage_available():
                return _LABEL_NOT_MEASURABLE
            res = cov.measure_branch_coverage(
                project_path, test_files, coverage_target)

        status = res.get('tool_status', 'unavailable')
        if status == 'unavailable':
            return _LABEL_NOT_MEASURABLE
        if status != 'ok':
            return _LABEL_NOT_MEASURABLE

        per_target = res.get('per_target', [])
        if not per_target:
            return _LABEL_NOT_MEASURABLE

        pt = per_target[0]
        covered = pt.get('n_covered', 0)
        total = pt.get('n_total', 0)
        if total == 0:
            # No measurable branches in callee → not-measurable
            return _LABEL_NOT_MEASURABLE
        if covered > 0:
            return _LABEL_VERIFIED_REAL
        return _LABEL_NOT_OBSERVED

    except Exception:
        return _LABEL_NOT_MEASURABLE
