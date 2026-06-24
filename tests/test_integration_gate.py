"""Pure unit tests for servers/integration_gate.py — parse_junit_results.

TDD: these tests are written BEFORE the implementation.
They must fail until integration_gate.py exists and is correct.
"""

import os
import tempfile
import textwrap

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_xml(tmp_path, name, content):
    p = tmp_path / name
    p.write_text(textwrap.dedent(content))
    return str(p)


# ---------------------------------------------------------------------------
# parse_junit_results — fixture-driven unit tests
# ---------------------------------------------------------------------------

class TestParseJunitResults:
    """All tests call parse_junit_results(xml_paths) and inspect the dict."""

    def test_single_suite_with_failure(self, tmp_path):
        """testsuite tests=2 failures=1 → passed=False, total=2, failures=1."""
        p = _write_xml(tmp_path, "r.xml", """\
            <?xml version="1.0"?>
            <testsuite name="SomeTests" tests="2" failures="1" errors="0" skipped="0">
              <testcase classname="foo.Bar" name="test_a"/>
              <testcase classname="foo.Bar" name="test_b">
                <failure message="AssertionError">expected 1 got 2</failure>
              </testcase>
            </testsuite>
        """)

        from servers.integration_gate import parse_junit_results
        res = parse_junit_results([p])

        assert res["ran"] is True
        assert res["total"] == 2
        assert res["failures"] == 1
        assert res["errors"] == 0
        assert res["passed"] is False
        assert res["error"] is None

    def test_single_suite_all_pass(self, tmp_path):
        """testsuite tests=3 failures=0 errors=0 → passed=True."""
        p = _write_xml(tmp_path, "r.xml", """\
            <?xml version="1.0"?>
            <testsuite name="SomeTests" tests="3" failures="0" errors="0" skipped="0">
              <testcase classname="foo.Bar" name="test_a"/>
              <testcase classname="foo.Bar" name="test_b"/>
              <testcase classname="foo.Bar" name="test_c"/>
            </testsuite>
        """)

        from servers.integration_gate import parse_junit_results
        res = parse_junit_results([p])

        assert res["ran"] is True
        assert res["total"] == 3
        assert res["failures"] == 0
        assert res["errors"] == 0
        assert res["passed"] is True
        assert res["error"] is None

    def test_testsuites_wrapper_sums_two_suites(self, tmp_path):
        """<testsuites> containing two <testsuite> elements — counts are summed."""
        p = _write_xml(tmp_path, "r.xml", """\
            <?xml version="1.0"?>
            <testsuites>
              <testsuite name="Suite1" tests="2" failures="0" errors="0" skipped="0"/>
              <testsuite name="Suite2" tests="4" failures="1" errors="0" skipped="0"/>
            </testsuites>
        """)

        from servers.integration_gate import parse_junit_results
        res = parse_junit_results([p])

        assert res["ran"] is True
        assert res["total"] == 6       # 2 + 4
        assert res["failures"] == 1    # 0 + 1
        assert res["errors"] == 0
        assert res["passed"] is False  # has failure
        assert res["error"] is None

    def test_empty_file_returns_ran_false(self, tmp_path):
        """Empty/garbage file → ran=False, passed=False, error set."""
        p = _write_xml(tmp_path, "bad.xml", "this is not xml at all")

        from servers.integration_gate import parse_junit_results
        res = parse_junit_results([p])

        assert res["ran"] is False
        assert res["passed"] is False
        assert res["error"] is not None

    def test_no_xml_paths_returns_ran_false(self):
        """Empty list → ran=False, passed=False."""
        from servers.integration_gate import parse_junit_results
        res = parse_junit_results([])

        assert res["ran"] is False
        assert res["passed"] is False
        assert res["error"] is not None

    def test_multi_file_aggregation(self, tmp_path):
        """Two separate XML files are summed correctly."""
        p1 = _write_xml(tmp_path, "a.xml", """\
            <?xml version="1.0"?>
            <testsuite name="A" tests="2" failures="0" errors="0" skipped="0"/>
        """)
        p2 = _write_xml(tmp_path, "b.xml", """\
            <?xml version="1.0"?>
            <testsuite name="B" tests="3" failures="0" errors="0" skipped="0"/>
        """)

        from servers.integration_gate import parse_junit_results
        res = parse_junit_results([p1, p2])

        assert res["ran"] is True
        assert res["total"] == 5
        assert res["failures"] == 0
        assert res["errors"] == 0
        assert res["passed"] is True

    def test_errors_count_prevents_passed(self, tmp_path):
        """errors=1 (even with failures=0) → passed=False."""
        p = _write_xml(tmp_path, "r.xml", """\
            <?xml version="1.0"?>
            <testsuite name="T" tests="2" failures="0" errors="1" skipped="0"/>
        """)

        from servers.integration_gate import parse_junit_results
        res = parse_junit_results([p])

        assert res["ran"] is True
        assert res["errors"] == 1
        assert res["passed"] is False

    def test_zero_tests_prevents_passed(self, tmp_path):
        """tests=0 even with 0 failures → passed=False (no evidence tests ran)."""
        p = _write_xml(tmp_path, "r.xml", """\
            <?xml version="1.0"?>
            <testsuite name="T" tests="0" failures="0" errors="0" skipped="0"/>
        """)

        from servers.integration_gate import parse_junit_results
        res = parse_junit_results([p])

        assert res["ran"] is True   # we parsed the file, but…
        assert res["passed"] is False  # total==0 prevents passed

    def test_mixed_valid_and_corrupt_files_surfaces_warning(self, tmp_path):
        """C-d fix: Valid + corrupt file → passed=False (hard gate).

        Previously asserted passed=True when one valid suite coexisted with a bad
        file.  C-d mandates that ANY parse error makes the whole result passed=False
        (the corrupt file could have hidden real failures — cannot trust the batch).
        """
        # Valid XML with clean result
        p_valid = _write_xml(tmp_path, "valid.xml", """\
            <?xml version="1.0"?>
            <testsuite name="Clean" tests="2" failures="0" errors="0" skipped="0"/>
        """)
        # Non-existent file — a parse error
        p_bad = str(tmp_path / "nonexistent.xml")

        from servers.integration_gate import parse_junit_results
        res = parse_junit_results([p_valid, p_bad])

        # C-d hard gate: ANY parse error → passed=False even when a valid suite exists
        assert res["passed"] is False, (
            f"C-d: mixed valid+corrupt must be passed=False (hard gate), got: {res}")
        # Error message must mention the bad file
        assert res["error"] is not None
        assert "nonexistent.xml" in res["error"]


# ---------------------------------------------------------------------------
# detect_mocked_collaborators — L2 static mock-smell scanner
# ---------------------------------------------------------------------------

class TestDetectMockedCollaborators:
    """TDD tests written BEFORE implementation of detect_mocked_collaborators."""

    def test_java_mockbean_collaborator_detected(self):
        """@MockBean followed by field of type OrderRepository → flagged."""
        source = textwrap.dedent("""\
            @SpringBootTest
            class OrderServiceTest {

                @MockBean
                private OrderRepository repo;

                @Autowired
                private OrderService svc;
            }
        """)
        from servers.integration_gate import detect_mocked_collaborators
        result = detect_mocked_collaborators(
            source, ["OrderRepository", "OrderService"], "java"
        )
        assert result == ["OrderRepository"]

    def test_java_mockito_mock_call_detected(self):
        """mock(OrderRepository.class) → flagged."""
        source = textwrap.dedent("""\
            class OrderServiceTest {
                OrderRepository r = mock(OrderRepository.class);
                OrderService svc = new OrderService(r);
            }
        """)
        from servers.integration_gate import detect_mocked_collaborators
        result = detect_mocked_collaborators(
            source, ["OrderRepository", "OrderService"], "java"
        )
        assert result == ["OrderRepository"]

    def test_java_real_bean_not_flagged(self):
        """@Autowired and new() are real wiring — should not be flagged."""
        source = textwrap.dedent("""\
            @SpringBootTest
            class OrderServiceTest {
                @Autowired
                private OrderRepository repo;

                OrderService svc = new OrderService();
            }
        """)
        from servers.integration_gate import detect_mocked_collaborators
        result = detect_mocked_collaborators(
            source, ["OrderRepository", "OrderService"], "java"
        )
        assert result == []

    def test_java_injectmocks_is_not_a_mocked_collaborator(self):
        """@InjectMocks marks the SUT, not a mock — must NOT flag it."""
        source = textwrap.dedent("""\
            class OrderServiceTest {
                @InjectMocks
                private OrderService svc;
            }
        """)
        from servers.integration_gate import detect_mocked_collaborators
        result = detect_mocked_collaborators(
            source, ["OrderService"], "java"
        )
        assert result == []

    def test_python_patch_target_detected(self):
        """@patch('app.svc.OrderRepository') → flagged by full collaborator name."""
        source = textwrap.dedent("""\
            @patch('app.svc.OrderRepository')
            def test_something(mock_repo):
                svc = OrderService(mock_repo)
                svc.process()
        """)
        from servers.integration_gate import detect_mocked_collaborators
        result = detect_mocked_collaborators(
            source, ["app.svc.OrderRepository"], "python"
        )
        assert result == ["app.svc.OrderRepository"]

    def test_simple_name_matching(self):
        """Collaborator 'com.aile.OrderRepository' matches mock(OrderRepository.class)."""
        source = textwrap.dedent("""\
            class Test {
                OrderRepository r = mock(OrderRepository.class);
            }
        """)
        from servers.integration_gate import detect_mocked_collaborators
        result = detect_mocked_collaborators(
            source, ["com.aile.OrderRepository"], "java"
        )
        assert result == ["com.aile.OrderRepository"]

    def test_java_fqn_in_mock_call_detected(self):
        """mock(com.aile.OrderRepository.class) with FQN → flagged."""
        source = textwrap.dedent("""\
            class Test {
                OrderRepository r = mock(com.aile.OrderRepository.class);
            }
        """)
        from servers.integration_gate import detect_mocked_collaborators
        result = detect_mocked_collaborators(
            source, ["OrderRepository"], "java"
        )
        assert result == ["OrderRepository"]

    def test_java_mock_with_extra_args_detected(self):
        """Mockito.mock(OrderRepository.class, RETURNS_DEEP_STUBS) with extra args → flagged."""
        source = textwrap.dedent("""\
            class Test {
                var r = Mockito.mock(OrderRepository.class, RETURNS_DEEP_STUBS);
            }
        """)
        from servers.integration_gate import detect_mocked_collaborators
        result = detect_mocked_collaborators(
            source, ["OrderRepository"], "java"
        )
        assert result == ["OrderRepository"]

    def test_new_collaborator_not_flagged_after_fix(self):
        """new OrderRepository() is NOT flagged — only .class patterns are."""
        source = textwrap.dedent("""\
            class Test {
                OrderRepository r = new OrderRepository();
            }
        """)
        from servers.integration_gate import detect_mocked_collaborators
        result = detect_mocked_collaborators(
            source, ["OrderRepository"], "java"
        )
        assert result == []


# ---------------------------------------------------------------------------
# boundaries_for_target — B3: boundary extraction from Code Graph
# ---------------------------------------------------------------------------

class TestBoundariesForTarget:
    """TDD tests for boundaries_for_target — written BEFORE implementation."""

    def test_boundaries_keep_injects_drop_imports(self, monkeypatch):
        """injects edge kept; imports and extends edges dropped.

        Monkeypatch code_graph so no real DB is hit.
        """
        import servers.integration_gate as ig
        import servers.code_graph as cg

        caller_node = {
            "id": "node-1",
            "kind": "class",
            "name": "com.example.OrderService",
            "file_path": "src/main/java/OrderService.java",
        }
        callee_node = {
            "id": "node-2",
            "kind": "class",
            "name": "com.example.OrderRepository",
            "file_path": "src/main/java/OrderRepository.java",
        }

        monkeypatch.setattr(
            cg,
            "get_code_nodes",
            lambda project, kind=None, file_path=None, limit=100: (
                [caller_node] if file_path == "src/main/java/OrderService.java" else []
            ),
        )

        mixed_edges = [
            {"from_id": "node-1", "to_id": "node-2", "kind": "injects",
             "line_number": 10, "confidence": 1.0},
            {"from_id": "node-1", "to_id": "node-3", "kind": "imports",
             "line_number": 1,  "confidence": 1.0},
            {"from_id": "node-1", "to_id": "node-4", "kind": "extends",
             "line_number": 5,  "confidence": 1.0},
        ]
        callee_nodes_by_id = {"node-2": callee_node}

        def fake_get_edges(project, from_id=None, to_id=None, kind=None, limit=100):
            if from_id == "node-1":
                return mixed_edges
            return []

        def fake_get_nodes_by_id(project, kind=None, file_path=None, limit=100):
            # Called for callee lookup — file_path will be None, kind None
            # We use the callee_nodes_by_id dict instead via to_id
            return []

        monkeypatch.setattr(cg, "get_code_edges", fake_get_edges)

        # Patch the callee-node lookup: integration_gate calls get_code_nodes
        # filtered by file_path to find callee nodes; we need to intercept the
        # lookup of a single node by id. The implementation resolves callee via
        # another get_code_nodes call; patch at module level.
        original_get_nodes = cg.get_code_nodes

        def patched_get_nodes(project, kind=None, file_path=None, limit=100):
            if file_path == "src/main/java/OrderService.java":
                return [caller_node]
            # callee lookup: the implementation must resolve node-2
            # We'll intercept using the from_id logic below
            return []

        monkeypatch.setattr(cg, "get_code_nodes", patched_get_nodes)

        # We also need the callee resolution. Provide a helper that returns
        # callee_node when asked for node-2. The implementation does a second
        # get_code_nodes filtered differently — but if it uses get_code_edges
        # to_id resolution, we can supply via a side-channel.
        # Strategy: patch get_code_nodes to also handle the callee case.
        # Since real implementation may call get_code_nodes(project) and filter,
        # return all known nodes when no filter applied.
        def full_get_nodes(project, kind=None, file_path=None, limit=100):
            if file_path == "src/main/java/OrderService.java":
                return [caller_node]
            if file_path is None and kind is None:
                return [caller_node, callee_node]
            return []

        monkeypatch.setattr(cg, "get_code_nodes", full_get_nodes)

        from servers.integration_gate import boundaries_for_target
        result = boundaries_for_target(
            "test-project",
            ["src/main/java/OrderService.java"],
        )

        assert len(result) == 1, f"Expected 1 boundary, got {result}"
        b = result[0]
        assert b["caller"] == "com.example.OrderService"
        assert b["callee"] == "com.example.OrderRepository"
        assert b["callee_file"] == "src/main/java/OrderRepository.java"
        assert b["edge"] == "injects"

    def test_boundaries_calls_edge_mapped(self, monkeypatch):
        """calls edge → boundary dict with edge='calls'."""
        import servers.integration_gate as ig
        import servers.code_graph as cg

        caller_node = {
            "id": "node-A",
            "kind": "function",
            "name": "app.service.process",
            "file_path": "app/service.py",
        }
        callee_node = {
            "id": "node-B",
            "kind": "function",
            "name": "app.repo.find_all",
            "file_path": "app/repo.py",
        }

        def fake_get_nodes(project, kind=None, file_path=None, limit=100):
            if file_path == "app/service.py":
                return [caller_node]
            if file_path is None and kind is None:
                return [caller_node, callee_node]
            return []

        def fake_get_edges(project, from_id=None, to_id=None, kind=None, limit=100):
            if from_id == "node-A":
                return [{"from_id": "node-A", "to_id": "node-B", "kind": "calls",
                         "line_number": 20, "confidence": 1.0}]
            return []

        monkeypatch.setattr(cg, "get_code_nodes", fake_get_nodes)
        monkeypatch.setattr(cg, "get_code_edges", fake_get_edges)

        from servers.integration_gate import boundaries_for_target
        result = boundaries_for_target("test-project", ["app/service.py"])

        assert len(result) == 1
        b = result[0]
        assert b["caller"] == "app.service.process"
        assert b["callee"] == "app.repo.find_all"
        assert b["callee_file"] == "app/repo.py"
        assert b["edge"] == "calls"

    def test_no_nodes_returns_empty(self, monkeypatch):
        """No nodes in target files → empty list returned (not an error)."""
        import servers.integration_gate as ig
        import servers.code_graph as cg

        monkeypatch.setattr(
            cg,
            "get_code_nodes",
            lambda project, kind=None, file_path=None, limit=100: [],
        )
        monkeypatch.setattr(
            cg,
            "get_code_edges",
            lambda project, from_id=None, to_id=None, kind=None, limit=100: [],
        )

        from servers.integration_gate import boundaries_for_target
        result = boundaries_for_target("test-project", ["nonexistent/file.py"])

        assert result == []

    def test_boundaries_resolves_callee_beyond_default_limit(self, monkeypatch):
        """Callee nodes beyond the default 100-node limit are resolved.

        When get_code_nodes is called to fetch all nodes, it must pass a high
        limit (>1000) so callees beyond the default 100 are included in the
        boundary resolution.
        """
        import servers.integration_gate as ig
        import servers.code_graph as cg

        caller_node = {
            "id": "node-caller",
            "kind": "class",
            "name": "com.example.Controller",
            "file_path": "src/main/java/Controller.java",
        }
        # Callee is at position 150 (beyond the default limit of 100)
        callee_node = {
            "id": "node-callee-150",
            "kind": "class",
            "name": "com.example.ServiceAt150",
            "file_path": "src/main/java/ServiceAt150.java",
        }

        # Track the limit parameter passed to get_code_nodes
        get_code_nodes_calls = []

        def fake_get_nodes(project, kind=None, file_path=None, limit=100):
            # Record the call to inspect the limit parameter
            get_code_nodes_calls.append({
                'file_path': file_path,
                'kind': kind,
                'limit': limit
            })

            if file_path == "src/main/java/Controller.java":
                return [caller_node]
            # When called to fetch all nodes (no file_path filter), return a list
            # that includes the callee beyond position 100
            if file_path is None and kind is None:
                # Simulate a project with many nodes; callee is at position 150
                many_nodes = [{"id": f"node-{i}", "name": f"cls.Node{i}",
                               "kind": "class", "file_path": f"src/node{i}.java"}
                              for i in range(200)]
                many_nodes[0] = caller_node  # Caller at position 0
                many_nodes[150] = callee_node  # Callee at position 150
                return many_nodes
            return []

        def fake_get_edges(project, from_id=None, to_id=None, kind=None, limit=100):
            if from_id == "node-caller":
                return [{"from_id": "node-caller", "to_id": "node-callee-150",
                        "kind": "calls", "line_number": 50, "confidence": 1.0}]
            return []

        monkeypatch.setattr(cg, "get_code_nodes", fake_get_nodes)
        monkeypatch.setattr(cg, "get_code_edges", fake_get_edges)

        from servers.integration_gate import boundaries_for_target
        result = boundaries_for_target(
            "test-project",
            ["src/main/java/Controller.java"],
        )

        # The fix should make the boundary resolve (callee_node should be found)
        assert len(result) == 1, f"Expected 1 boundary, got {len(result)}"
        b = result[0]
        assert b["caller"] == "com.example.Controller"
        assert b["callee"] == "com.example.ServiceAt150"
        assert b["callee_file"] == "src/main/java/ServiceAt150.java"
        assert b["edge"] == "calls"

        # Verify that get_code_nodes was called with a high limit
        # (not just the default 100)
        all_nodes_call = [c for c in get_code_nodes_calls
                          if c['file_path'] is None and c['kind'] is None]
        assert all_nodes_call, "get_code_nodes must be called to fetch all nodes"
        assert all_nodes_call[0]['limit'] >= 1000, \
            f"Expected limit >= 1000, got {all_nodes_call[0]['limit']}"


# ---------------------------------------------------------------------------
# run_integration_gate + format_boundary_summary — B4 policy tests
# ---------------------------------------------------------------------------

class TestRunIntegrationGate:
    """TDD tests for run_integration_gate (written BEFORE implementation).

    Policy:
      L1 (run+pass) — hard gate: tests must run and pass.
      L2 (mock-smell) — hard gate: no boundary collaborator may be mocked.
      L3 (branch coverage) — advisory only; never blocks verdict.
    """

    def _setup_integration_task(self, metadata):
        """Create an epic→story→task hierarchy with given metadata, mark done,
        reserve a critic, and return (task_id, critic_id)."""
        from servers.tasks import (create_task, create_subtask,
                                   update_task_status, reserve_critic_task)
        epic = create_task(project='proj', description='epic', task_level='epic')
        story = create_subtask(parent_id=epic, description='story',
                               task_level='story', requires_validation=False)
        task = create_subtask(parent_id=story,
                              description='integration test write',
                              requires_validation=True,
                              metadata=metadata)
        update_task_status(task, 'done', result='done')
        critic = reserve_critic_task(task)
        return task, critic['id']

    # ------------------------------------------------------------------ L1 --

    def test_l1_failure_rejects(self, mock_db_path, tmp_path, monkeypatch):
        """L1: run_tests returns passed=False → verdict rejected (fail-closed)."""
        import servers.integration_gate as ig
        import servers.facade as facade

        boundaries = [{'caller': 'OrderService', 'callee': 'OrderRepository',
                       'callee_file': 'repo.java', 'edge': 'injects'}]
        task, critic_id = self._setup_integration_task(
            {'integration_boundaries': boundaries,
             'test_files': ['OrderServiceTest.java'],
             'stack': 'java'})

        monkeypatch.setattr(ig, 'run_tests', lambda *a, **kw: {
            'ran': True, 'passed': False, 'total': 3, 'failures': 1,
            'errors': 0, 'error': 'test_order_creates failed', 'evidence': {}})
        # L2 + coverage should not be reached
        monkeypatch.setattr(ig, 'detect_mocked_collaborators',
                            lambda *a, **kw: (_ for _ in ()).throw(AssertionError('L2 called when L1 failed')))

        verdict = facade.run_integration_gate(critic_id, task, 'proj', str(tmp_path))
        assert verdict['verdict'] in ('rejected', 'blocked'), \
            f"Expected rejected/blocked, got {verdict['verdict']}"

    # ------------------------------------------------------------------ L2 --

    def test_mocked_boundary_rejects(self, mock_db_path, tmp_path, monkeypatch):
        """L2: collaborator is mocked → verdict rejected, reason names collaborator."""
        import servers.integration_gate as ig
        import servers.facade as facade
        from servers.memory import get_working_memory

        boundaries = [{'caller': 'OrderService', 'callee': 'OrderRepository',
                       'callee_file': 'repo.java', 'edge': 'injects'}]
        # D5 test-quality: use proper src/test/java/ path so _derive_java_test_filters
        # includes it (reaching L2), rather than rejecting at empty-filter check.
        task, critic_id = self._setup_integration_task(
            {'integration_boundaries': boundaries,
             'test_files': ['src/test/java/com/example/OrderServiceTest.java'],
             'stack': 'java'})

        # L1 passes
        monkeypatch.setattr(ig, 'run_tests', lambda *a, **kw: {
            'ran': True, 'passed': True, 'total': 2,
            'failures': 0, 'errors': 0, 'error': None, 'evidence': {}})
        # L2 detects mock
        monkeypatch.setattr(ig, 'detect_mocked_collaborators',
                            lambda src, collabs, stack: ['OrderRepository'])
        # test file readable (must be at the path relative to tmp_path)
        test_file = tmp_path / 'src' / 'test' / 'java' / 'com' / 'example' / 'OrderServiceTest.java'
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text('@MockBean\nOrderRepository repo;\n')

        verdict = facade.run_integration_gate(critic_id, task, 'proj', str(tmp_path))
        assert verdict['verdict'] in ('rejected', 'blocked'), \
            f"Expected rejected/blocked, got {verdict['verdict']}"
        # Rejection reason must name the mocked collaborator
        wm = get_working_memory(task, 'critic_suggestions')
        assert wm and 'OrderRepository' in wm, \
            f"Rejection context should name OrderRepository, got: {wm!r}"

    # ------------------------------------------------------------------ L1+L2 pass --

    def test_real_passing_integration_proceeds(self, mock_db_path, tmp_path, monkeypatch):
        """L1 passes, no mocks detected → verdict proceed, coverage_summary present."""
        import servers.integration_gate as ig
        import servers.facade as facade

        boundaries = [{'caller': 'OrderService', 'callee': 'OrderRepository',
                       'callee_file': 'repo.java', 'edge': 'injects'}]
        task, critic_id = self._setup_integration_task(
            {'integration_boundaries': boundaries,
             'test_files': ['OrderServiceTest.py'],
             'stack': 'python'})

        monkeypatch.setattr(ig, 'run_tests', lambda *a, **kw: {
            'ran': True, 'passed': True, 'total': 5,
            'failures': 0, 'errors': 0, 'error': None, 'evidence': {}})
        monkeypatch.setattr(ig, 'detect_mocked_collaborators',
                            lambda src, collabs, stack: [])
        # L3 coverage — make it not-measurable (unavailable)
        import servers.coverage_java as cov_java
        import servers.project as proj_mod
        monkeypatch.setattr(proj_mod, 'ensure_project',
                            lambda *a, **kw: {'tech_stack': {'test_tool': 'pytest'}})
        import servers.coverage as cov
        monkeypatch.setattr(cov, '_coverage_available', lambda: False)

        test_file = tmp_path / 'OrderServiceTest.py'
        test_file.write_text('def test_it(): pass\n')

        verdict = facade.run_integration_gate(critic_id, task, 'proj', str(tmp_path))
        assert verdict['verdict'] == 'proceed', \
            f"Expected proceed, got {verdict['verdict']}"
        assert 'coverage_summary' in verdict
        assert isinstance(verdict['coverage_summary'], list)

    # ------------------------------------------------------------------ no boundaries --

    def test_no_boundaries_is_not_integration_task_proceeds(
            self, mock_db_path, tmp_path, monkeypatch):
        """metadata without integration_boundaries → proceed immediately, no gating."""
        import servers.integration_gate as ig
        import servers.facade as facade

        # Task with no integration_boundaries key
        task, critic_id = self._setup_integration_task(
            {'coverage_targets': [{'file_path': 'x.py', 'name': 'f',
                                   'line_start': 1, 'line_end': 5}]})

        # run_tests should NOT be called
        called = {'l1': False}
        def should_not_call(*a, **kw):
            called['l1'] = True
            return {'ran': True, 'passed': True, 'total': 1,
                    'failures': 0, 'errors': 0, 'error': None, 'evidence': {}}
        monkeypatch.setattr(ig, 'run_tests', should_not_call)

        verdict = facade.run_integration_gate(critic_id, task, 'proj', str(tmp_path))
        assert verdict['verdict'] == 'proceed'
        assert verdict.get('warn') is None
        assert not called['l1'], "run_tests should NOT be called for non-integration tasks"

    # ------------------------------------------------------------------ L3 --

    def test_l3_classification_four_labels(
            self, mock_db_path, tmp_path, monkeypatch):
        """L3 classifies boundaries into 4 labels; verdict stays proceed even when
        a boundary is not-measurable (advisory never blocks)."""
        import servers.integration_gate as ig
        import servers.facade as facade
        import servers.coverage as cov
        import servers.project as proj_mod

        # Three boundaries to exercise different L3 labels
        boundaries = [
            {'caller': 'OrderService', 'callee': 'OrderRepository',
             'callee_file': 'OrderRepository.java', 'edge': 'injects'},
            {'caller': 'OrderService', 'callee': 'EmailService',
             'callee_file': 'EmailService.java', 'edge': 'calls'},
            {'caller': 'OrderService', 'callee': 'AuditLog',
             'callee_file': 'AuditLog.java', 'edge': 'calls'},
        ]
        task, critic_id = self._setup_integration_task(
            {'integration_boundaries': boundaries,
             'test_files': ['OrderServiceTest.py'],
             'stack': 'python'})

        # L1 passes
        monkeypatch.setattr(ig, 'run_tests', lambda *a, **kw: {
            'ran': True, 'passed': True, 'total': 3,
            'failures': 0, 'errors': 0, 'error': None, 'evidence': {}})
        # L2 no mocks
        monkeypatch.setattr(ig, 'detect_mocked_collaborators',
                            lambda src, collabs, stack: [])

        # L3: use Python backend (simpler to monkeypatch)
        monkeypatch.setattr(proj_mod, 'ensure_project',
                            lambda *a, **kw: {'tech_stack': {'test_tool': 'pytest'}})
        # coverage available — will call measure_branch_coverage per boundary callee

        cov_call_count = {'n': 0}

        def fake_measure(project_path, test_targets, coverage_targets):
            cov_call_count['n'] += 1
            callee = coverage_targets[0].get('name', '') if coverage_targets else ''
            if callee == 'OrderRepository':
                # verified-real: coverage ran, callee branches covered
                return {
                    'tool_status': 'ok', 'fully_covered': True, 'error': None,
                    'per_target': [{'file_path': 'OrderRepository.java',
                                    'name': 'OrderRepository',
                                    'line_start': 1, 'line_end': 50,
                                    'covered_branches': [{'from': 2, 'to': 3}],
                                    'missing_branches': [],
                                    'n_total': 1, 'n_covered': 1}]
                }
            elif callee == 'EmailService':
                # not-observed: coverage ran but callee has 0 covered branches
                return {
                    'tool_status': 'ok', 'fully_covered': False, 'error': None,
                    'per_target': [{'file_path': 'EmailService.java',
                                    'name': 'EmailService',
                                    'line_start': 1, 'line_end': 30,
                                    'covered_branches': [],
                                    'missing_branches': [{'from': 2, 'to': 3}],
                                    'n_total': 1, 'n_covered': 0}]
                }
            else:
                # not-measurable: unavailable
                return {'tool_status': 'unavailable', 'error': 'no coverage', 'per_target': []}

        monkeypatch.setattr(cov, '_coverage_available', lambda: True)
        monkeypatch.setattr(cov, 'measure_branch_coverage', fake_measure)

        test_file = tmp_path / 'OrderServiceTest.py'
        test_file.write_text('def test_it(): pass\n')

        verdict = facade.run_integration_gate(critic_id, task, 'proj', str(tmp_path))

        # L3 advisory: verdict must still be proceed even with not-measurable
        assert verdict['verdict'] == 'proceed', \
            f"Expected proceed (advisory-only L3), got {verdict['verdict']}"

        # coverage_summary must reflect the 4-class labels
        summary = verdict.get('coverage_summary', [])
        assert summary, "coverage_summary should be present on proceed"
        summary_text = '\n'.join(summary)
        assert 'verified-real' in summary_text, \
            f"Expected verified-real in summary: {summary_text}"
        assert 'not-observed' in summary_text, \
            f"Expected not-observed in summary: {summary_text}"
        assert 'not-measurable' in summary_text, \
            f"Expected not-measurable in summary: {summary_text}"

    # ------------------------------------------------------------------ malformed boundaries --

    def test_malformed_boundaries_rejects(self, mock_db_path, tmp_path, monkeypatch):
        """integration_boundaries present but wrong type (string) → verdict rejected, not a crash.

        Mirrors run_coverage_gate's treatment of malformed coverage_targets:
        absent/empty → not an integration task (proceed);
        present-but-wrong-type → fail-closed reject (not a silent skip = 假綠).
        """
        import servers.integration_gate as ig
        import servers.facade as facade

        # integration_boundaries is a string (malformed — should be list[dict])
        task, critic_id = self._setup_integration_task(
            {'integration_boundaries': "yes", 'stack': 'python'})

        # run_tests must NOT be called — the gate should reject before L1
        called = {'l1': False}
        def should_not_call(*a, **kw):
            called['l1'] = True
            return {'ran': True, 'passed': True, 'total': 1,
                    'failures': 0, 'errors': 0, 'error': None, 'evidence': {}}
        monkeypatch.setattr(ig, 'run_tests', should_not_call)

        verdict = facade.run_integration_gate(critic_id, task, 'proj', str(tmp_path))
        assert verdict['verdict'] in ('rejected', 'blocked'), (
            f"Malformed boundaries must reject, got {verdict['verdict']}")
        assert not called['l1'], "run_tests must NOT be called for malformed boundaries"

    # ------------------------------------------------------------------ unreadable test sources --

    def test_unreadable_test_sources_rejects(self, mock_db_path, tmp_path, monkeypatch):
        """L2: test_files non-empty but no file is readable → verdict rejected (fail-closed).

        Cannot verify absence of mocks → reject (can't verify → reject),
        mirroring how run_coverage_gate rejects when it can't derive test targets.
        """
        import servers.integration_gate as ig
        import servers.facade as facade

        boundaries = [{'caller': 'OrderService', 'callee': 'OrderRepository',
                       'callee_file': 'repo.java', 'edge': 'injects'}]
        # test_files points to a nonexistent path — no file will be readable
        task, critic_id = self._setup_integration_task(
            {'integration_boundaries': boundaries,
             'test_files': ['/nonexistent/Foo.java'],
             'stack': 'java'})

        # L1 passes (monkeypatched to pass)
        monkeypatch.setattr(ig, 'run_tests', lambda *a, **kw: {
            'ran': True, 'passed': True, 'total': 2,
            'failures': 0, 'errors': 0, 'error': None, 'evidence': {}})

        verdict = facade.run_integration_gate(critic_id, task, 'proj', str(tmp_path))
        assert verdict['verdict'] in ('rejected', 'blocked'), (
            f"Unreadable test sources must reject, got {verdict['verdict']}")
        # Rejection reason must mention unreadable test sources
        from servers.memory import get_working_memory
        wm = get_working_memory(task, 'critic_suggestions')
        assert wm and ('讀取' in wm or 'unreadable' in wm or '不可讀' in wm or 'test_files' in wm), (
            f"Rejection context must mention unreadable test sources, got: {wm!r}")



class TestFormatBoundarySummary:
    """Unit tests for format_boundary_summary — pure function, no DB needed."""

    def test_verified_real_label(self):
        from servers.integration_gate import format_boundary_summary
        boundaries = [{'caller': 'A', 'callee': 'B', 'label': 'verified-real'}]
        lines = format_boundary_summary(boundaries)
        assert len(lines) >= 1
        joined = '\n'.join(lines)
        assert 'A' in joined and 'B' in joined
        assert 'verified-real' in joined

    def test_mocked_label(self):
        from servers.integration_gate import format_boundary_summary
        boundaries = [{'caller': 'A', 'callee': 'B', 'label': 'mocked'}]
        lines = format_boundary_summary(boundaries)
        joined = '\n'.join(lines)
        assert 'mocked' in joined

    def test_not_observed_label(self):
        from servers.integration_gate import format_boundary_summary
        boundaries = [{'caller': 'A', 'callee': 'B', 'label': 'not-observed'}]
        lines = format_boundary_summary(boundaries)
        joined = '\n'.join(lines)
        assert 'not-observed' in joined

    def test_not_measurable_label(self):
        from servers.integration_gate import format_boundary_summary
        boundaries = [{'caller': 'A', 'callee': 'B', 'label': 'not-measurable'}]
        lines = format_boundary_summary(boundaries)
        joined = '\n'.join(lines)
        assert 'not-measurable' in joined

    def test_multiple_boundaries_one_line_each(self):
        from servers.integration_gate import format_boundary_summary
        boundaries = [
            {'caller': 'Svc', 'callee': 'Repo', 'label': 'verified-real'},
            {'caller': 'Svc', 'callee': 'Cache', 'label': 'not-observed'},
        ]
        lines = format_boundary_summary(boundaries)
        # At least 2 lines (one per boundary, possibly a header line too)
        boundary_lines = [l for l in lines if 'Repo' in l or 'Cache' in l]
        assert len(boundary_lines) >= 2

    def test_l1_pass_line_included(self):
        from servers.integration_gate import format_boundary_summary
        boundaries = [{'caller': 'A', 'callee': 'B', 'label': 'verified-real'}]
        lines = format_boundary_summary(boundaries, l1_total=5)
        joined = '\n'.join(lines)
        # Should mention L1 tests passed
        assert '5' in joined


# ---------------------------------------------------------------------------
# B5 TDD: recipe_integration_tests attaches integration_boundaries metadata
# ---------------------------------------------------------------------------

class TestRecipeAttachesIntegrationBoundariesMetadata:
    """recipe_integration_tests should attach integration_boundaries to each
    module task's metadata.  Mirrors TestRecipePersistsCoverageTargets."""

    def test_recipe_attaches_integration_boundaries_metadata(
            self, mock_db_path, monkeypatch):
        """Integration recipe creates tasks whose metadata contains
        'integration_boundaries' key — populated by boundaries_for_target."""
        import servers.project as project_mod
        import servers.code_graph as cg
        import servers.integration_gate as ig

        # Fake code_graph: one file node so the recipe builds one module task.
        fake_node = {
            'id': 'n1', 'kind': 'file',
            'file_path': 'servers/foo.py',
            'name': 'foo.py',
        }
        monkeypatch.setattr(cg, 'get_code_nodes',
                            lambda project, kind=None, file_path=None,
                                   limit=100, offset=0: [fake_node])

        monkeypatch.setattr(project_mod, 'ensure_project',
                            lambda *a, **k: {'tech_stack': {'test_tool': 'pytest'}})

        # boundaries_for_target returns one boundary for the module's files.
        fake_boundary = {
            'caller': 'servers.foo.Foo',
            'callee': 'servers.bar.Bar',
            'callee_file': 'servers/bar.py',
            'edge': 'calls',
        }
        monkeypatch.setattr(ig, 'boundaries_for_target',
                            lambda project, files: [fake_boundary])

        # Intercept create_subtask to capture the metadata passed to the
        # executor task (the task that get_next_dispatch dispatches).
        import servers.tasks as tasks_mod
        captured = {}
        real_create_subtask = tasks_mod.create_subtask

        def capturing_create_subtask(parent_id, description, **kwargs):
            tid = real_create_subtask(parent_id, description, **kwargs)
            if kwargs.get('assigned_agent') == 'executor':
                captured['task_id'] = tid
                captured['metadata'] = kwargs.get('metadata')
            return tid

        monkeypatch.setattr(tasks_mod, 'create_subtask', capturing_create_subtask)

        from servers.recipes import recipe_integration_tests
        from servers.tasks import get_task

        res = recipe_integration_tests('proj', '/tmp/proj', max_tasks=1)
        assert res['task_count'] == 1, f"Expected 1 task, got {res}"
        assert captured, "create_subtask for executor task must have been called"

        meta = captured.get('metadata') or {}
        assert 'integration_boundaries' in meta, (
            f"Expected 'integration_boundaries' in metadata, got: {meta!r}")
        assert meta['integration_boundaries'] == [fake_boundary], (
            f"Expected fake_boundary, got: {meta['integration_boundaries']!r}")


# ---------------------------------------------------------------------------
# B5 TDD: get_next_dispatch_integration_gated rejects mocked boundary
# ---------------------------------------------------------------------------

class TestIntegrationGatedDispatch:
    """get_next_dispatch_integration_gated: mirrors TestGetNextDispatchGated
    but for the integration gate.

    Test: done integration task whose metadata has a mocked boundary →
    routes back to executor (rejected) with collaborator named in prompt.
    """

    def test_integration_gated_dispatch_rejects_mocked_boundary(
            self, mock_db_path, tmp_path, monkeypatch):
        """A done task with integration_boundaries where the collaborator is
        mocked → get_next_dispatch_integration_gated returns executor with
        the collaborator name in the prompt."""
        import servers.integration_gate as ig
        import servers.facade as facade
        from servers.tasks import create_task, create_subtask, update_task_status

        boundary = {
            'caller': 'OrderService',
            'callee': 'OrderRepository',
            'callee_file': 'repo/OrderRepository.java',
            'edge': 'injects',
        }

        epic = create_task(project='proj', description='epic', task_level='epic')
        story = create_subtask(parent_id=epic, description='story',
                               task_level='story', requires_validation=False)
        task = create_subtask(
            parent_id=story,
            description='write integration tests',
            requires_validation=True,
            metadata={
                'integration_boundaries': [boundary],
                # D5 test-quality: use proper src/test/java/ path so it passes
                # _derive_java_test_filters and reaches L2 (not empty-filter reject).
                'test_files': ['src/test/java/com/example/OrderServiceTest.java'],
                'stack': 'java',
            })
        update_task_status(task, 'done', result='done')

        # L1: tests pass
        monkeypatch.setattr(ig, 'run_tests', lambda *a, **kw: {
            'ran': True, 'passed': True, 'total': 3,
            'failures': 0, 'errors': 0, 'error': None, 'evidence': {}})

        # L2: collaborator is mocked
        monkeypatch.setattr(ig, 'detect_mocked_collaborators',
                            lambda src, collabs, stack: ['OrderRepository'])

        # make test file readable so L2 can fire (path relative to tmp_path)
        test_file = tmp_path / 'src' / 'test' / 'java' / 'com' / 'example' / 'OrderServiceTest.java'
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text('@MockBean\nOrderRepository repo;\n')

        inst = facade.get_next_dispatch_integration_gated(
            epic, 'proj', str(tmp_path))

        assert inst['subagent_type'] == 'executor', (
            f"Expected executor (rejection retry), got {inst.get('subagent_type')!r}")
        assert inst['task_id'] == task
        assert 'OrderRepository' in inst['prompt'], (
            f"Collaborator name should appear in retry prompt; "
            f"prompt={inst.get('prompt','')[:200]!r}")


# ---------------------------------------------------------------------------
# C7: parse_junit_results — malformed/all-skipped hardening (TDD)
# ---------------------------------------------------------------------------

class TestParseJunitC7Hardening:
    """C7: missing/non-numeric failures or errors attrs must NOT default to 0-and-pass.
    All-skipped suites must be passed=False.
    """

    def test_missing_failures_attr_is_parse_error(self, tmp_path):
        """<testsuite tests="2"/> — no failures/errors attrs → NOT a clean pass."""
        from servers.integration_gate import parse_junit_results
        p = _write_xml(tmp_path, "bad.xml", """\
            <?xml version="1.0"?>
            <testsuite name="BadSuite" tests="2"/>
        """)
        res = parse_junit_results([p])
        # Should not treat the missing attrs as 0 — suite is malformed
        assert res["passed"] is False, (
            f"Suite with no failures/errors attrs must not pass, got: {res}")

    def test_non_numeric_failures_attr_is_parse_error(self, tmp_path):
        """failures="oops" is non-numeric → NOT a clean pass (must not default to 0)."""
        from servers.integration_gate import parse_junit_results
        p = _write_xml(tmp_path, "bad.xml", """\
            <?xml version="1.0"?>
            <testsuite name="BadSuite" tests="2" failures="oops" errors="0"/>
        """)
        res = parse_junit_results([p])
        assert res["passed"] is False, (
            f"Non-numeric failures= must not pass, got: {res}")

    def test_all_skipped_suite_is_not_passed(self, tmp_path):
        """tests="2" skipped="2" failures="0" errors="0" → passed=False (no executed tests)."""
        from servers.integration_gate import parse_junit_results
        p = _write_xml(tmp_path, "skipped.xml", """\
            <?xml version="1.0"?>
            <testsuite name="AllSkipped" tests="2" failures="0" errors="0" skipped="2"/>
        """)
        res = parse_junit_results([p])
        assert res["ran"] is True, "Suite was parsed — ran should be True"
        assert res["passed"] is False, (
            f"All-skipped suite must be passed=False, got: {res}")

    def test_genuine_pass_still_passes(self, tmp_path):
        """tests="3" failures="0" errors="0" skipped="0" → passed=True (genuine pass)."""
        from servers.integration_gate import parse_junit_results
        p = _write_xml(tmp_path, "pass.xml", """\
            <?xml version="1.0"?>
            <testsuite name="GoodSuite" tests="3" failures="0" errors="0" skipped="0"/>
        """)
        res = parse_junit_results([p])
        assert res["ran"] is True
        assert res["passed"] is True, f"Clean pass suite must be passed=True, got: {res}"

    def test_no_clean_suites_means_ran_false(self, tmp_path):
        """Only a malformed suite (no failures/errors) → ran=False, passed=False."""
        from servers.integration_gate import parse_junit_results
        p = _write_xml(tmp_path, "only_bad.xml", """\
            <?xml version="1.0"?>
            <testsuite name="NaughtyOnly" tests="2"/>
        """)
        res = parse_junit_results([p])
        assert res["ran"] is False, (
            f"No clean suites must produce ran=False, got: {res}")
        assert res["passed"] is False
        assert res["error"] is not None


# ---------------------------------------------------------------------------
# C7b: Java runner must not score off stale XML (TDD)
# ---------------------------------------------------------------------------

class TestJavaRunnerStaleXml:
    """C7b: Stale test-results/test/*.xml must be deleted before Gradle runs.
    If no fresh XML is produced after the run → not passed.
    """

    def test_stale_xml_is_cleared_before_run(self, tmp_path, monkeypatch):
        """Pre-seeded old XML is removed before Gradle runs; if no fresh XML after
        the (no-op) run → result is not passed (ran=False or passed=False)."""
        import servers.integration_gate as ig

        # Create fake gradlew
        gradlew = tmp_path / "gradlew"
        gradlew.write_text("#!/bin/sh\n")
        gradlew.chmod(0o755)

        # Seed a stale XML file in the expected output directory
        xml_dir = tmp_path / "build" / "test-results" / "test"
        xml_dir.mkdir(parents=True)
        stale_xml = xml_dir / "STALE_TEST-SomeTests.xml"
        stale_xml.write_text(
            '<?xml version="1.0"?>\n'
            '<testsuite name="Stale" tests="3" failures="0" errors="0" skipped="0"/>\n'
        )
        assert stale_xml.exists(), "Stale XML must exist before the test"

        # Monkeypatch subprocess.run to be a no-op (returns rc=0, writes NO new XML)
        import subprocess

        class FakeCompleted:
            returncode = 0
            stdout = ""
            stderr = ""

        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeCompleted())

        # Run the Java backend
        result = ig._run_java(str(tmp_path))

        # The stale XML dir should have been cleared before run.
        # Since subprocess is no-op (no fresh XML written), the result must be
        # not passed (either ran=False because no XML, or passed=False).
        assert not result.get("passed"), (
            f"Stale XML must not produce a passing result; got: {result}")

    def test_no_fresh_xml_means_not_passed(self, tmp_path, monkeypatch):
        """After a successful Gradle run that produces no XML → ran=False, passed=False."""
        import servers.integration_gate as ig
        import subprocess

        gradlew = tmp_path / "gradlew"
        gradlew.write_text("#!/bin/sh\n")
        gradlew.chmod(0o755)

        class FakeCompleted:
            returncode = 0
            stdout = ""
            stderr = ""

        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeCompleted())

        # No XML directory at all
        result = ig._run_java(str(tmp_path))
        assert result.get("passed") is False, (
            f"No XML dir should yield passed=False, got: {result}")
        assert result.get("ran") is False, (
            f"No XML dir should yield ran=False, got: {result}")


# ---------------------------------------------------------------------------
# C8: boundaries_for_target pagination limit (TDD)
# ---------------------------------------------------------------------------

class TestBoundariesPaginationLimit:
    """C8: get_code_nodes (caller fetch) and get_code_edges (per-caller edge fetch)
    must both be called with limit >= 1000 — NOT the default 100.
    """

    def test_caller_node_fetch_uses_high_limit(self, monkeypatch):
        """get_code_nodes for caller-side file_path lookup must pass limit >= 1000."""
        import servers.code_graph as cg

        recorded_limits = []

        def recording_get_nodes(project, kind=None, file_path=None, limit=100, **kw):
            recorded_limits.append({'file_path': file_path, 'limit': limit})
            return []

        monkeypatch.setattr(cg, "get_code_nodes", recording_get_nodes)
        monkeypatch.setattr(cg, "get_code_edges",
                            lambda *a, **kw: [])

        from servers.integration_gate import boundaries_for_target
        boundaries_for_target("proj", ["src/Foo.java"])

        caller_fetches = [c for c in recorded_limits if c['file_path'] == "src/Foo.java"]
        assert caller_fetches, "get_code_nodes must be called for the target file"
        for call in caller_fetches:
            assert call['limit'] >= 1000, (
                f"Caller-node fetch limit must be >= 1000, got {call['limit']}")

    def test_edge_fetch_uses_high_limit(self, monkeypatch):
        """get_code_edges per-caller call must pass limit >= 1000."""
        import servers.code_graph as cg

        caller_node = {
            "id": "node-c", "kind": "class",
            "name": "com.example.Ctrl", "file_path": "src/Ctrl.java",
        }

        edge_limits = []

        def get_nodes(project, kind=None, file_path=None, limit=100, **kw):
            if file_path == "src/Ctrl.java":
                return [caller_node]
            if file_path is None and kind is None:
                return [caller_node]
            return []

        def get_edges(project, from_id=None, to_id=None, kind=None, limit=100, **kw):
            edge_limits.append(limit)
            return []

        monkeypatch.setattr(cg, "get_code_nodes", get_nodes)
        monkeypatch.setattr(cg, "get_code_edges", get_edges)

        from servers.integration_gate import boundaries_for_target
        boundaries_for_target("proj", ["src/Ctrl.java"])

        assert edge_limits, "get_code_edges must be called at least once"
        for lim in edge_limits:
            assert lim >= 1000, (
                f"Edge fetch limit must be >= 1000, got {lim}")


# ---------------------------------------------------------------------------
# C2: Empty integration_boundaries — L1 must still run (TDD)
# ---------------------------------------------------------------------------

class TestC2EmptyBoundariesPolicy:
    """C2: Key present + empty list → L1 must run; failing L1 → rejected.
    Key absent → pass-through without calling run_tests.
    """

    def _make_task(self, metadata):
        from servers.tasks import (create_task, create_subtask,
                                   update_task_status, reserve_critic_task)
        epic = create_task(project='proj', description='epic', task_level='epic')
        story = create_subtask(parent_id=epic, description='story',
                               task_level='story', requires_validation=False)
        task = create_subtask(parent_id=story, description='integration task',
                              requires_validation=True, metadata=metadata)
        update_task_status(task, 'done', result='done')
        critic = reserve_critic_task(task)
        return task, critic['id']

    def test_absent_key_does_not_call_run_tests(self, mock_db_path, tmp_path, monkeypatch):
        """integration_boundaries KEY ABSENT → proceed without calling run_tests."""
        import servers.integration_gate as ig
        import servers.facade as facade

        task, critic_id = self._make_task({'other_key': 'irrelevant'})

        called = {'n': 0}
        def should_not_call(*a, **kw):
            called['n'] += 1
            return {'ran': True, 'passed': True, 'total': 1,
                    'failures': 0, 'errors': 0, 'error': None, 'evidence': {}}
        monkeypatch.setattr(ig, 'run_tests', should_not_call)

        verdict = facade.run_integration_gate(critic_id, task, 'proj', str(tmp_path))
        assert verdict['verdict'] == 'proceed', (
            f"Absent key → proceed, got {verdict['verdict']}")
        assert called['n'] == 0, "run_tests must NOT be called when key is absent"

    def test_present_empty_list_l1_pass_proceeds(self, mock_db_path, tmp_path, monkeypatch):
        """integration_boundaries KEY PRESENT + empty list + L1 pass → proceed.

        C-a fix: test files must now be provided (TEST_TARGETS marker in result).
        Empty-boundary tasks also require test files to scope L1.
        """
        import servers.integration_gate as ig
        import servers.facade as facade
        from servers.tasks import update_task_status

        # Create a test file on disk so derive_integration_test_files can find it
        test_file = tmp_path / "tests" / "test_integration.py"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text("def test_it(): pass\n")

        task, critic_id = self._make_task(
            {'integration_boundaries': [], 'stack': 'python'})
        # Provide TEST_TARGETS marker in executor result so gate can derive test files
        update_task_status(task, 'done', result='TEST_TARGETS: tests/test_integration.py\nDone.')

        monkeypatch.setattr(ig, 'run_tests', lambda *a, **kw: {
            'ran': True, 'passed': True, 'total': 2,
            'failures': 0, 'errors': 0, 'error': None, 'evidence': {}})

        verdict = facade.run_integration_gate(critic_id, task, 'proj', str(tmp_path))
        assert verdict['verdict'] == 'proceed', (
            f"Empty boundaries + L1 pass + test files → proceed, got {verdict['verdict']}")

    def test_present_empty_list_l1_fail_rejects(self, mock_db_path, tmp_path, monkeypatch):
        """C2 core fix: integration_boundaries KEY PRESENT + empty list + L1 FAIL → rejected.

        Previously this proceeded (false-green hole).  Now L1 must be enforced.
        C-a fix: test files must also be present (integration task requires TEST_TARGETS).
        """
        import servers.integration_gate as ig
        import servers.facade as facade
        from servers.tasks import update_task_status

        # Create a test file on disk so derive_integration_test_files can find it
        test_file = tmp_path / "tests" / "test_integration.py"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text("def test_it(): pass\n")

        task, critic_id = self._make_task(
            {'integration_boundaries': [], 'stack': 'python'})
        # Provide TEST_TARGETS marker in executor result so gate can derive test files
        update_task_status(task, 'done', result='TEST_TARGETS: tests/test_integration.py\nDone.')

        # L1 fails — tests did not pass
        monkeypatch.setattr(ig, 'run_tests', lambda *a, **kw: {
            'ran': True, 'passed': False, 'total': 3, 'failures': 2,
            'errors': 0, 'error': 'test_something failed', 'evidence': {}})

        verdict = facade.run_integration_gate(critic_id, task, 'proj', str(tmp_path))
        assert verdict['verdict'] in ('rejected', 'blocked'), (
            f"C2 fix: empty boundaries + test files + L1 fail must reject, got {verdict['verdict']}")


# ---------------------------------------------------------------------------
# L2 Majors: additional mock false-negative patterns (TDD)
# ---------------------------------------------------------------------------

class TestL2AdditionalMockPatterns:
    """L2 new patterns: MockitoSpyBean, spy(new C(), mockConstruction, mockStatic (Java);
    patch.object, create_autospec, MagicMock(spec=C), Mock(spec=C), monkeypatch.setattr (Python).
    """

    # ---- Java new patterns ----

    def test_java_mockito_spy_bean_annotation(self):
        """@MockitoSpyBean OrderRepository repo → detected."""
        source = (
            "@SpringBootTest\nclass T {\n"
            "    @MockitoSpyBean\n"
            "    private OrderRepository repo;\n"
            "}\n"
        )
        from servers.integration_gate import detect_mocked_collaborators
        result = detect_mocked_collaborators(source, ["OrderRepository"], "java")
        assert result == ["OrderRepository"], f"@MockitoSpyBean not detected: {result}"

    def test_java_spy_new_instance(self):
        """spy(new OrderRepository()) → detected."""
        source = (
            "class T {\n"
            "    OrderRepository repo = spy(new OrderRepository());\n"
            "}\n"
        )
        from servers.integration_gate import detect_mocked_collaborators
        result = detect_mocked_collaborators(source, ["OrderRepository"], "java")
        assert result == ["OrderRepository"], f"spy(new C() not detected: {result}"

    def test_java_mock_construction(self):
        """mockConstruction(OrderRepository.class → detected."""
        source = (
            "class T {\n"
            "    MockedConstruction<OrderRepository> m = "
            "mockConstruction(OrderRepository.class);\n"
            "}\n"
        )
        from servers.integration_gate import detect_mocked_collaborators
        result = detect_mocked_collaborators(source, ["OrderRepository"], "java")
        assert result == ["OrderRepository"], f"mockConstruction not detected: {result}"

    def test_java_mock_static(self):
        """Mockito.mockStatic(OrderRepository.class → detected."""
        source = (
            "class T {\n"
            "    try (MockedStatic<OrderRepository> ms = "
            "Mockito.mockStatic(OrderRepository.class)) {}\n"
            "}\n"
        )
        from servers.integration_gate import detect_mocked_collaborators
        result = detect_mocked_collaborators(source, ["OrderRepository"], "java")
        assert result == ["OrderRepository"], f"mockStatic not detected: {result}"

    def test_java_real_new_not_flagged(self):
        """new OrderRepository() without spy/mock wrapper is NOT flagged."""
        source = (
            "class T {\n"
            "    OrderRepository repo = new OrderRepository();\n"
            "}\n"
        )
        from servers.integration_gate import detect_mocked_collaborators
        result = detect_mocked_collaborators(source, ["OrderRepository"], "java")
        assert result == [], f"Plain new() must not be flagged: {result}"

    # ---- Python new patterns ----

    def test_python_patch_object(self):
        """patch.object(obj, 'OrderRepository') → detected."""
        source = (
            "def test_it(monkeypatch):\n"
            "    with patch.object(module, 'OrderRepository') as m:\n"
            "        pass\n"
        )
        from servers.integration_gate import detect_mocked_collaborators
        result = detect_mocked_collaborators(source, ["OrderRepository"], "python")
        assert result == ["OrderRepository"], f"patch.object not detected: {result}"

    def test_python_create_autospec(self):
        """create_autospec(OrderRepository → detected."""
        source = (
            "def test_it():\n"
            "    repo = create_autospec(OrderRepository, instance=True)\n"
        )
        from servers.integration_gate import detect_mocked_collaborators
        result = detect_mocked_collaborators(source, ["OrderRepository"], "python")
        assert result == ["OrderRepository"], f"create_autospec not detected: {result}"

    def test_python_magic_mock_spec(self):
        """MagicMock(spec=OrderRepository → detected."""
        source = (
            "def test_it():\n"
            "    repo = MagicMock(spec=OrderRepository)\n"
        )
        from servers.integration_gate import detect_mocked_collaborators
        result = detect_mocked_collaborators(source, ["OrderRepository"], "python")
        assert result == ["OrderRepository"], f"MagicMock(spec=C) not detected: {result}"

    def test_python_mock_spec(self):
        """Mock(spec=OrderRepository → detected."""
        source = (
            "def test_it():\n"
            "    repo = Mock(spec=OrderRepository)\n"
        )
        from servers.integration_gate import detect_mocked_collaborators
        result = detect_mocked_collaborators(source, ["OrderRepository"], "python")
        assert result == ["OrderRepository"], f"Mock(spec=C) not detected: {result}"

    def test_python_real_usage_not_flagged(self):
        """Plain import and direct use of OrderRepository is NOT flagged."""
        source = (
            "from app.repo import OrderRepository\n"
            "def test_it():\n"
            "    repo = OrderRepository()\n"
            "    assert repo.find_all() == []\n"
        )
        from servers.integration_gate import detect_mocked_collaborators
        result = detect_mocked_collaborators(source, ["OrderRepository"], "python")
        assert result == [], f"Real usage must not be flagged: {result}"


# ---------------------------------------------------------------------------
# D3: derive_integration_test_files — marker-based test file derivation (TDD)
# ---------------------------------------------------------------------------

class TestDeriveIntegrationTestFiles:
    """TDD tests for derive_integration_test_files (D3 C1 fix)."""

    def test_derive_from_marker_existing_file(self, tmp_path):
        """Executor result containing TEST_TARGETS: tests/FooIT.java
        (file exists under tmp project) → returns that relative path."""
        # Create the test file so it exists under project root
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        test_file = test_dir / "FooIT.java"
        test_file.write_text("@SpringBootTest\npublic class FooIT {}\n")

        executor_result = "All tests passed.\nTEST_TARGETS: tests/FooIT.java\nDone."

        from servers.integration_gate import derive_integration_test_files
        result = derive_integration_test_files(str(tmp_path), executor_result)

        assert result == ["tests/FooIT.java"], (
            f"Expected ['tests/FooIT.java'], got {result!r}")

    def test_no_marker_returns_empty(self, tmp_path):
        """Executor result with no TEST_TARGETS: marker → empty list."""
        from servers.integration_gate import derive_integration_test_files
        result = derive_integration_test_files(str(tmp_path), "All done, tests pass.")
        assert result == [], f"Expected [], got {result!r}"

    def test_nonexistent_file_in_marker_is_skipped(self, tmp_path):
        """TEST_TARGETS: path that does not exist → filtered out → empty."""
        from servers.integration_gate import derive_integration_test_files
        result = derive_integration_test_files(
            str(tmp_path), "TEST_TARGETS: tests/NonExistent.java")
        assert result == [], f"Non-existent file must not be returned, got {result!r}"

    def test_comma_separated_multiple_files(self, tmp_path):
        """Multiple files separated by comma are all returned when they exist."""
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        (test_dir / "FooIT.java").write_text("class FooIT {}")
        (test_dir / "BarIT.java").write_text("class BarIT {}")

        from servers.integration_gate import derive_integration_test_files
        result = derive_integration_test_files(
            str(tmp_path),
            "TEST_TARGETS: tests/FooIT.java, tests/BarIT.java")

        assert "tests/FooIT.java" in result
        assert "tests/BarIT.java" in result
        assert len(result) == 2


# ---------------------------------------------------------------------------
# D3: C1 regression test — no test files → rejected (fail-closed)
# ---------------------------------------------------------------------------

class TestIntegrationTaskWithoutTestFilesRejects:
    """C1 false-green regression test: integration task (boundaries present)
    with no TEST_TARGETS: marker AND no metadata.test_files → must reject,
    NOT proceed.  Previously this was a silent false-green.
    """

    def _make_integration_task_no_files(self, result_text='done'):
        """Create an integration task with boundaries but no test files."""
        from servers.tasks import (create_task, create_subtask,
                                   update_task_status, reserve_critic_task)
        epic = create_task(project='proj', description='epic', task_level='epic')
        story = create_subtask(parent_id=epic, description='story',
                               task_level='story', requires_validation=False)
        # metadata has boundaries but NO test_files key at all
        boundaries = [{'caller': 'OrderService', 'callee': 'OrderRepository',
                        'callee_file': 'repo.java', 'edge': 'injects'}]
        task = create_subtask(parent_id=story,
                              description='write integration tests',
                              requires_validation=True,
                              metadata={'integration_boundaries': boundaries,
                                        'stack': 'python'})
        # result has no TEST_TARGETS: marker
        update_task_status(task, 'done', result=result_text)
        critic = reserve_critic_task(task)
        return task, critic['id']

    def test_integration_task_without_test_files_rejects(
            self, mock_db_path, tmp_path, monkeypatch):
        """C1 regression: boundaries present + result has no TEST_TARGETS marker
        + no metadata.test_files → verdict MUST be 'rejected', not 'proceed'."""
        import servers.integration_gate as ig
        import servers.facade as facade

        task, critic_id = self._make_integration_task_no_files(
            result_text='All integration tests done.')

        # run_tests must NOT be called (gate should reject before L1)
        called = {'l1': False}
        def should_not_reach(*a, **kw):
            called['l1'] = True
            return {'ran': True, 'passed': True, 'total': 1,
                    'failures': 0, 'errors': 0, 'error': None, 'evidence': {}}
        monkeypatch.setattr(ig, 'run_tests', should_not_reach)

        verdict = facade.run_integration_gate(critic_id, task, 'proj', str(tmp_path))

        assert verdict['verdict'] in ('rejected', 'blocked'), (
            f"C1 regression: no test files must reject, got {verdict['verdict']!r}")
        assert not called['l1'], (
            "run_tests must NOT be called when no test files can be derived")

        # Rejection message must guide executor to use TEST_TARGETS:
        from servers.memory import get_working_memory
        wm = get_working_memory(task, 'critic_suggestions')
        assert wm and 'TEST_TARGETS' in wm, (
            f"Rejection context must mention TEST_TARGETS, got: {wm!r}")


# ---------------------------------------------------------------------------
# D3: L2 now actually scans derived test files (TDD)
# ---------------------------------------------------------------------------

class TestL2ScancesDerivedTestFiles:
    """L2 now reads the executor's actual test files (derived from marker),
    not the old metadata.test_files=[].  Previously L2 scanned ZERO files
    and passed silently (false-green).
    """

    def _make_task_with_marker_result(self, tmp_path, test_file_content,
                                      test_file_rel):
        """Create an integration task whose executor result has a TEST_TARGETS:
        marker pointing to a file we create in tmp_path."""
        from servers.tasks import (create_task, create_subtask,
                                   update_task_status, reserve_critic_task)
        # Create the test file on disk
        test_path = tmp_path / test_file_rel
        test_path.parent.mkdir(parents=True, exist_ok=True)
        test_path.write_text(test_file_content)

        epic = create_task(project='proj', description='epic', task_level='epic')
        story = create_subtask(parent_id=epic, description='story',
                               task_level='story', requires_validation=False)
        boundaries = [{'caller': 'OrderService', 'callee': 'OrderRepository',
                        'callee_file': 'repo.java', 'edge': 'injects'}]
        task = create_subtask(
            parent_id=story,
            description='write integration tests',
            requires_validation=True,
            metadata={
                'integration_boundaries': boundaries,
                'stack': 'java',
                # Note: test_files is intentionally NOT set (empty/absent)
                # so we rely purely on TEST_TARGETS: marker
            })
        # Executor result contains the TEST_TARGETS: marker
        result_text = (
            f"Integration tests written.\n"
            f"TEST_TARGETS: {test_file_rel}\n"
            f"All tests pass."
        )
        update_task_status(task, 'done', result=result_text)
        critic = reserve_critic_task(task)
        return task, critic['id']

    def test_l2_scans_derived_test_files(self, mock_db_path, tmp_path, monkeypatch):
        """L2 reads the file pointed to by the TEST_TARGETS: marker and rejects
        when it detects a mocked boundary collaborator.

        Previously: L2 scanned ZERO files (test_files=[]) → always passed (false-green).
        Now: L2 reads the actual test file from the marker → detects mock → rejects.
        """
        import servers.integration_gate as ig
        import servers.facade as facade

        # Test file content that MOCKS the boundary collaborator
        mocked_test_source = (
            "@SpringBootTest\n"
            "class OrderServiceIT {\n"
            "    @MockBean\n"
            "    private OrderRepository repo;\n"
            "\n"
            "    @Autowired\n"
            "    private OrderService svc;\n"
            "}\n"
        )
        # D5 test-quality: use src/test/java/ path so _derive_java_test_filters includes
        # it and the test reaches L2 (mock detection), not the empty-filter reject.
        test_file_rel = "src/test/java/com/example/OrderServiceIT.java"

        task, critic_id = self._make_task_with_marker_result(
            tmp_path, mocked_test_source, test_file_rel)

        # L1: monkeypatch to pass — only L2 should decide verdict
        monkeypatch.setattr(ig, 'run_tests', lambda *a, **kw: {
            'ran': True, 'passed': True, 'total': 1,
            'failures': 0, 'errors': 0, 'error': None, 'evidence': {}})

        verdict = facade.run_integration_gate(critic_id, task, 'proj', str(tmp_path))

        assert verdict['verdict'] in ('rejected', 'blocked'), (
            f"L2 should have detected mocked OrderRepository and rejected, "
            f"got {verdict['verdict']!r}")

        # Rejection reason must name the collaborator
        from servers.memory import get_working_memory
        wm = get_working_memory(task, 'critic_suggestions')
        assert wm and 'OrderRepository' in wm, (
            f"Rejection must name the mocked collaborator, got: {wm!r}")


# ---------------------------------------------------------------------------
# C-a: Empty boundaries + no TEST_TARGETS → rejected (new TDD test)
# ---------------------------------------------------------------------------

class TestCaEmptyBoundariesRequiresTestFiles:
    """C-a: ANY integration task (key present) must have derived test files.
    Even with empty boundaries, gate must reject if no TEST_TARGETS marker.
    """

    def _make_empty_boundary_task(self, result_text='done (no marker)'):
        from servers.tasks import (create_task, create_subtask,
                                   update_task_status, reserve_critic_task)
        epic = create_task(project='proj', description='epic', task_level='epic')
        story = create_subtask(parent_id=epic, description='story',
                               task_level='story', requires_validation=False)
        task = create_subtask(parent_id=story,
                              description='integration test write',
                              requires_validation=True,
                              metadata={'integration_boundaries': [], 'stack': 'python'})
        update_task_status(task, 'done', result=result_text)
        critic = reserve_critic_task(task)
        return task, critic['id']

    def test_empty_boundaries_no_test_targets_marker_rejects(
            self, mock_db_path, tmp_path, monkeypatch):
        """C-a regression: empty boundaries + result has no TEST_TARGETS marker
        + no metadata.test_files → verdict MUST be 'rejected', not 'proceed'.

        Currently (before fix) this would wrongly proceed.
        """
        import servers.integration_gate as ig
        import servers.facade as facade

        task, critic_id = self._make_empty_boundary_task(
            result_text='All integration tests done (no marker).')

        # run_tests must NOT be called — gate should reject before L1
        called = {'l1': False}
        def should_not_reach(*a, **kw):
            called['l1'] = True
            return {'ran': True, 'passed': True, 'total': 1,
                    'failures': 0, 'errors': 0, 'error': None, 'evidence': {}}
        monkeypatch.setattr(ig, 'run_tests', should_not_reach)

        verdict = facade.run_integration_gate(critic_id, task, 'proj', str(tmp_path))

        assert verdict['verdict'] in ('rejected', 'blocked'), (
            f"C-a: empty boundaries + no TEST_TARGETS must reject, "
            f"got {verdict['verdict']!r}")
        assert not called['l1'], (
            "run_tests must NOT be called when no test files can be derived")

        # Rejection message must mention TEST_TARGETS
        from servers.memory import get_working_memory
        wm = get_working_memory(task, 'critic_suggestions')
        assert wm and 'TEST_TARGETS' in wm, (
            f"Rejection context must mention TEST_TARGETS, got: {wm!r}")


# ---------------------------------------------------------------------------
# C-b: boundaries_error flag → rejected (TDD)
# ---------------------------------------------------------------------------

class TestCbBoundariesExtractionError:
    """C-b: boundaries_error=True in metadata → fail-closed reject.
    Clean empty boundaries (no flag) + L1 pass → proceed.
    """

    def _make_task_with_meta(self, metadata, result_text='done'):
        from servers.tasks import (create_task, create_subtask,
                                   update_task_status, reserve_critic_task)
        epic = create_task(project='proj', description='epic', task_level='epic')
        story = create_subtask(parent_id=epic, description='story',
                               task_level='story', requires_validation=False)
        task = create_subtask(parent_id=story, description='int test',
                              requires_validation=True, metadata=metadata)
        update_task_status(task, 'done', result=result_text)
        critic = reserve_critic_task(task)
        return task, critic['id']

    def test_boundaries_error_flag_rejects(self, mock_db_path, tmp_path, monkeypatch):
        """C-b: metadata with boundaries_error=True → verdict rejected."""
        import servers.integration_gate as ig
        import servers.facade as facade

        task, critic_id = self._make_task_with_meta(
            {'integration_boundaries': [], 'stack': 'python', 'boundaries_error': True})

        # run_tests should NOT be called (gate should reject before L1)
        called = {'n': 0}
        def should_not_call(*a, **kw):
            called['n'] += 1
            return {'ran': True, 'passed': True, 'total': 1,
                    'failures': 0, 'errors': 0, 'error': None, 'evidence': {}}
        monkeypatch.setattr(ig, 'run_tests', should_not_call)

        verdict = facade.run_integration_gate(critic_id, task, 'proj', str(tmp_path))
        assert verdict['verdict'] in ('rejected', 'blocked'), (
            f"C-b: boundaries_error=True must reject, got {verdict['verdict']!r}")
        assert called['n'] == 0, "run_tests must not be called when boundaries_error=True"

    def test_clean_empty_boundaries_no_error_flag_l1_pass_proceeds(
            self, mock_db_path, tmp_path, monkeypatch):
        """C-b: empty boundaries WITHOUT boundaries_error flag + L1 pass → proceed."""
        import servers.integration_gate as ig
        import servers.facade as facade
        from servers.tasks import update_task_status

        # Create a test file on disk so C-a test-file check passes
        test_file = tmp_path / "tests" / "test_service.py"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text("def test_it(): pass\n")

        task, critic_id = self._make_task_with_meta(
            {'integration_boundaries': [], 'stack': 'python'},
            result_text='TEST_TARGETS: tests/test_service.py\nDone.')

        monkeypatch.setattr(ig, 'run_tests', lambda *a, **kw: {
            'ran': True, 'passed': True, 'total': 1,
            'failures': 0, 'errors': 0, 'error': None, 'evidence': {}})

        verdict = facade.run_integration_gate(critic_id, task, 'proj', str(tmp_path))
        assert verdict['verdict'] == 'proceed', (
            f"C-b: clean empty boundaries (no error flag) + L1 pass → proceed, "
            f"got {verdict['verdict']!r}")

    def test_recipe_records_boundaries_error_on_exception(self, mock_db_path, monkeypatch):
        """C-b: recipe_integration_tests records boundaries_error=True in metadata
        when boundaries_for_target raises an exception."""
        import servers.project as project_mod
        import servers.code_graph as cg
        import servers.integration_gate as ig

        fake_node = {
            'id': 'n1', 'kind': 'file',
            'file_path': 'servers/foo.py',
            'name': 'foo.py',
        }
        monkeypatch.setattr(cg, 'get_code_nodes',
                            lambda project, kind=None, file_path=None,
                                   limit=100, offset=0: [fake_node])
        monkeypatch.setattr(project_mod, 'ensure_project',
                            lambda *a, **k: {'tech_stack': {'test_tool': 'pytest'}})

        # boundaries_for_target raises — simulating Code Graph failure
        monkeypatch.setattr(ig, 'boundaries_for_target',
                            lambda project, files: (_ for _ in ()).throw(
                                RuntimeError('Code Graph unavailable')))

        import servers.tasks as tasks_mod
        captured = {}
        real_create_subtask = tasks_mod.create_subtask

        def capturing_create_subtask(parent_id, description, **kwargs):
            tid = real_create_subtask(parent_id, description, **kwargs)
            if kwargs.get('assigned_agent') == 'executor':
                captured['metadata'] = kwargs.get('metadata')
            return tid

        monkeypatch.setattr(tasks_mod, 'create_subtask', capturing_create_subtask)

        from servers.recipes import recipe_integration_tests
        res = recipe_integration_tests('proj', '/tmp/proj', max_tasks=1)
        assert res['task_count'] == 1

        meta = captured.get('metadata') or {}
        assert meta.get('boundaries_error') is True, (
            f"C-b: boundaries_error must be True when extraction raises, got: {meta!r}")
        assert meta.get('integration_boundaries') == [], (
            f"C-b: boundaries should still be [] on error, got: {meta!r}")


# ---------------------------------------------------------------------------
# C-c: junit stack normalization (TDD)
# ---------------------------------------------------------------------------

class TestCcJunitStackNormalization:
    """C-c: stack='junit' must normalize to 'java' before run_tests is called."""

    def _make_task_with_junit_stack(self, tmp_path):
        from servers.tasks import (create_task, create_subtask,
                                   update_task_status, reserve_critic_task)
        # Create a test file with proper Java path so C-a + Major pass
        test_file = tmp_path / 'src' / 'test' / 'java' / 'com' / 'example' / 'FooIT.java'
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text('@SpringBootTest\npublic class FooIT {}\n')

        boundaries = [{'caller': 'Svc', 'callee': 'Repo',
                       'callee_file': 'Repo.java', 'edge': 'injects'}]
        epic = create_task(project='proj', description='epic', task_level='epic')
        story = create_subtask(parent_id=epic, description='story',
                               task_level='story', requires_validation=False)
        task = create_subtask(parent_id=story, description='junit integration test',
                              requires_validation=True,
                              metadata={
                                  'integration_boundaries': boundaries,
                                  'stack': 'junit',  # <-- key: non-standard stack name
                              })
        # Executor result with TEST_TARGETS pointing to the created file
        result_text = (
            'Integration tests done.\n'
            'TEST_TARGETS: src/test/java/com/example/FooIT.java\n'
        )
        update_task_status(task, 'done', result=result_text)
        critic = reserve_critic_task(task)
        return task, critic['id']

    def test_junit_stack_routes_to_java_path(self, mock_db_path, tmp_path, monkeypatch):
        """C-c: stack='junit' must be normalized to 'java' by select_backend,
        so run_tests receives 'java' (not 'junit' which would cause unknown-stack error)."""
        import servers.integration_gate as ig
        import servers.facade as facade

        task, critic_id = self._make_task_with_junit_stack(tmp_path)

        # Capture the stack argument passed to run_tests
        received_stack = {}

        def capturing_run_tests(project_path, stack, **kwargs):
            received_stack['stack'] = stack
            return {'ran': True, 'passed': True, 'total': 1,
                    'failures': 0, 'errors': 0, 'error': None, 'evidence': {}}

        monkeypatch.setattr(ig, 'run_tests', capturing_run_tests)
        monkeypatch.setattr(ig, 'detect_mocked_collaborators',
                            lambda src, collabs, stack: [])

        # make test file readable for L2
        test_file = tmp_path / 'src' / 'test' / 'java' / 'com' / 'example' / 'FooIT.java'
        # already created in _make_task_with_junit_stack

        verdict = facade.run_integration_gate(critic_id, task, 'proj', str(tmp_path))

        assert received_stack.get('stack') == 'java', (
            f"C-c: stack='junit' must normalize to 'java' before run_tests, "
            f"got {received_stack.get('stack')!r}")


# ---------------------------------------------------------------------------
# C-d: one valid + one garbage file → passed=False (new explicit test)
# ---------------------------------------------------------------------------

class TestCdMixedValidCorruptFailed:
    """C-d: explicit test for the hard-gate mixed case."""

    def test_one_valid_one_garbage_file_passed_false(self, tmp_path):
        """C-d: one valid <testsuite failures=0> + one garbage file → passed=False."""
        from servers.integration_gate import parse_junit_results

        # Write a clean valid testsuite
        p_valid = tmp_path / "valid.xml"
        p_valid.write_text(
            '<?xml version="1.0"?>\n'
            '<testsuite name="Clean" tests="3" failures="0" errors="0" skipped="0"/>\n'
        )
        # Write a garbage (unparseable) file
        p_garbage = tmp_path / "garbage.xml"
        p_garbage.write_text("not xml at all!!!")

        res = parse_junit_results([str(p_valid), str(p_garbage)])

        assert res["passed"] is False, (
            f"C-d: valid+garbage must be passed=False (hard gate), got: {res}")
        assert res["error"] is not None, "C-d: error must describe the garbage file"


# ---------------------------------------------------------------------------
# Major: stale-XML delete failure → fail closed (TDD)
# ---------------------------------------------------------------------------

class TestMajorStaleXmlDeleteFailure:
    """Major: if deleting the stale test-results dir raises, return ran=False, passed=False."""

    def test_stale_xml_delete_failure_fails_closed(self, tmp_path, monkeypatch):
        """If shutil.rmtree raises when clearing stale XML, the runner must
        return ran=False, passed=False (not continue and score off stale XML)."""
        import shutil
        import servers.integration_gate as ig

        # Create fake gradlew
        gradlew = tmp_path / "gradlew"
        gradlew.write_text("#!/bin/sh\n")
        gradlew.chmod(0o755)

        # Create a stale XML directory so the delete path is hit
        xml_dir = tmp_path / "build" / "test-results" / "test"
        xml_dir.mkdir(parents=True)
        (xml_dir / "STALE.xml").write_text(
            '<?xml version="1.0"?>\n'
            '<testsuite name="Stale" tests="3" failures="0" errors="0" skipped="0"/>\n'
        )

        # Monkeypatch shutil.rmtree to raise OSError
        original_rmtree = shutil.rmtree

        def failing_rmtree(path, *args, **kwargs):
            if str(xml_dir) in str(path):
                raise OSError(f"Permission denied: cannot delete {path}")
            return original_rmtree(path, *args, **kwargs)

        monkeypatch.setattr(shutil, 'rmtree', failing_rmtree)

        result = ig._run_java(str(tmp_path))

        assert result.get('ran') is False, (
            f"Major: delete failure must set ran=False, got: {result}")
        assert result.get('passed') is False, (
            f"Major: delete failure must set passed=False, got: {result}")
        assert result.get('error') and ('stale' in result['error'].lower()
                                         or 'delete' in result['error'].lower()
                                         or 'Cannot delete' in result['error']), (
            f"Major: error must mention delete failure, got: {result.get('error')!r}")


# ---------------------------------------------------------------------------
# Major: empty Java test_filters → rejected (TDD)
# ---------------------------------------------------------------------------

class TestMajorEmptyJavaTestFilters:
    """Major: java task whose derived test files yield zero FQ class filters → rejected."""

    def _make_java_task_bad_paths(self):
        """Create a java integration task with test_files that won't produce FQ names."""
        from servers.tasks import (create_task, create_subtask,
                                   update_task_status, reserve_critic_task)
        boundaries = [{'caller': 'Svc', 'callee': 'Repo',
                       'callee_file': 'Repo.java', 'edge': 'calls'}]
        epic = create_task(project='proj', description='epic', task_level='epic')
        story = create_subtask(parent_id=epic, description='story',
                               task_level='story', requires_validation=False)
        task = create_subtask(parent_id=story, description='java integration test',
                              requires_validation=True,
                              metadata={
                                  'integration_boundaries': boundaries,
                                  # test_files path lacks src/test/java/ — no FQ name derivable
                                  'test_files': ['FooTest.java'],
                                  'stack': 'java',
                              })
        # result has no TEST_TARGETS marker either
        update_task_status(task, 'done', result='done')
        critic = reserve_critic_task(task)
        return task, critic['id']

    def test_java_task_no_fq_filters_rejects(self, mock_db_path, tmp_path, monkeypatch):
        """Major: java integration task whose test files yield empty FQ class filters
        must be rejected (not run the full Gradle suite).

        D5 test-quality fix: the test now genuinely exercises the empty-filter branch.
        test_files=['FooTest.java'] lacks src/test/java/ so _derive_java_test_filters
        returns [] → gate rejects BEFORE calling run_tests (empty filter = cannot scope).
        The assertion that run_tests is NOT called (called['n']==0) is now enforced.
        """
        import servers.integration_gate as ig
        import servers.facade as facade

        task, critic_id = self._make_java_task_bad_paths()

        # run_tests must NOT be called — gate should reject before L1 (empty java_filters)
        called = {'n': 0}
        def should_not_call(*a, **kw):
            called['n'] += 1
            return {'ran': True, 'passed': True, 'total': 1,
                    'failures': 0, 'errors': 0, 'error': None, 'evidence': {}}
        monkeypatch.setattr(ig, 'run_tests', should_not_call)

        verdict = facade.run_integration_gate(critic_id, task, 'proj', str(tmp_path))
        assert verdict['verdict'] in ('rejected', 'blocked'), (
            f"Major: empty java FQ filters must reject, got {verdict['verdict']!r}")
        # D5 test-quality fix: assert run_tests was NOT called (rejects before L1)
        assert called['n'] == 0, (
            f"run_tests must NOT be called when java_filters is empty (got {called['n']} calls)")


# ---------------------------------------------------------------------------
# D5 F1: Python L2 — method-level and object-target mocking (TDD)
# ---------------------------------------------------------------------------

class TestD5F1PythonMethodLevelMocking:
    """D5 F1: Python L2 must detect method-level / object-target mocking.

    These patterns currently return [] but represent real fake-integration:
      patch("app.repo.OrderRepository.find_all")      # method of collaborator
      patch.object(OrderRepository, "find_all")        # object form (first arg is class)
      monkeypatch.setattr(OrderRepository, "find_all", ...)
    """

    def test_patch_method_of_collaborator_detected(self):
        """patch("app.repo.OrderRepository.find_all") → C=OrderRepository detected.

        The collaborator simple name appears as a DOTTED PATH SEGMENT in the
        middle of the patch target string, not as the final segment.
        """
        source = (
            "@patch('app.repo.OrderRepository.find_all')\n"
            "def test_it(mock_find):\n"
            "    svc = OrderService()\n"
            "    result = svc.process()\n"
        )
        from servers.integration_gate import detect_mocked_collaborators
        result = detect_mocked_collaborators(source, ['OrderRepository'], 'python')
        assert result == ['OrderRepository'], (
            f"patch of method on collaborator must flag collaborator, got {result!r}")

    def test_patch_object_first_arg_is_collaborator(self):
        """patch.object(OrderRepository, "find_all") → C=OrderRepository detected.

        The FIRST argument to patch.object is the collaborator class itself.
        """
        source = (
            "def test_it():\n"
            "    with patch.object(OrderRepository, 'find_all') as m:\n"
            "        svc = OrderService()\n"
            "        svc.process()\n"
        )
        from servers.integration_gate import detect_mocked_collaborators
        result = detect_mocked_collaborators(source, ['OrderRepository'], 'python')
        assert result == ['OrderRepository'], (
            f"patch.object first-arg class must flag collaborator, got {result!r}")

    def test_monkeypatch_setattr_first_arg_is_collaborator(self):
        """monkeypatch.setattr(OrderRepository, "find_all", mock_fn) → detected."""
        source = (
            "def test_it(monkeypatch):\n"
            "    monkeypatch.setattr(OrderRepository, 'find_all', lambda: [])\n"
            "    svc = OrderService()\n"
            "    svc.process()\n"
        )
        from servers.integration_gate import detect_mocked_collaborators
        result = detect_mocked_collaborators(source, ['OrderRepository'], 'python')
        assert result == ['OrderRepository'], (
            f"monkeypatch.setattr with collaborator as first arg must flag it, got {result!r}")

    def test_patch_object_with_module_prefix_first_arg(self):
        """patch.object(pkg.OrderRepository, "find_all") → detected (ends with .C)."""
        source = (
            "def test_it():\n"
            "    with patch.object(pkg.OrderRepository, 'find_all') as m:\n"
            "        pass\n"
        )
        from servers.integration_gate import detect_mocked_collaborators
        result = detect_mocked_collaborators(source, ['OrderRepository'], 'python')
        assert result == ['OrderRepository'], (
            f"patch.object pkg.C first arg must flag collaborator, got {result!r}")

    def test_plain_import_not_flagged(self):
        """from app.repo import OrderRepository is NOT flagged (no patch/mock)."""
        source = (
            "from app.repo import OrderRepository\n"
            "def test_it():\n"
            "    repo = OrderRepository()\n"
            "    assert repo.find_all() == []\n"
        )
        from servers.integration_gate import detect_mocked_collaborators
        result = detect_mocked_collaborators(source, ['OrderRepository'], 'python')
        assert result == [], f"Plain import must not be flagged: {result!r}"

    def test_constructor_call_not_flagged(self):
        """OrderRepository() constructor call is NOT flagged."""
        source = (
            "def test_it():\n"
            "    repo = OrderRepository()\n"
            "    svc = OrderService(repo)\n"
            "    svc.process()\n"
        )
        from servers.integration_gate import detect_mocked_collaborators
        result = detect_mocked_collaborators(source, ['OrderRepository'], 'python')
        assert result == [], f"Constructor call must not be flagged: {result!r}"


# ---------------------------------------------------------------------------
# D5 F2: Non-numeric skipped attr → passed=False (TDD)
# ---------------------------------------------------------------------------

class TestD5F2NonNumericSkipped:
    """D5 F2: skipped="oops" must be treated as malformed → passed=False.

    Currently _int_attr silently defaults invalid values to 0, so
    <testsuite tests="2" failures="0" errors="0" skipped="oops"/> passes.
    Fix: treat present-but-non-numeric skipped (or any numeric attr) as a
    parse error → passed=False.
    """

    def test_non_numeric_skipped_makes_passed_false(self, tmp_path):
        """skipped="oops" on an otherwise-valid suite → passed=False."""
        p = _write_xml(tmp_path, "bad_skip.xml", """\
            <?xml version="1.0"?>
            <testsuite name="OopsSuite" tests="2" failures="0" errors="0" skipped="oops"/>
        """)
        from servers.integration_gate import parse_junit_results
        res = parse_junit_results([p])
        assert res['passed'] is False, (
            f"D5 F2: non-numeric skipped= must make passed=False, got: {res}")

    def test_non_numeric_skipped_is_parse_error(self, tmp_path):
        """skipped="oops" should result in a parse error (not a clean suite)."""
        p = _write_xml(tmp_path, "bad_skip2.xml", """\
            <?xml version="1.0"?>
            <testsuite name="OopsSuite" tests="2" failures="0" errors="0" skipped="oops"/>
        """)
        from servers.integration_gate import parse_junit_results
        res = parse_junit_results([p])
        # Either ran=False (no clean suite) or error is set
        assert res['error'] is not None or res['ran'] is False, (
            f"D5 F2: non-numeric skipped= must produce error or ran=False, got: {res}")

    def test_numeric_skipped_still_works(self, tmp_path):
        """skipped="1" still works correctly (numeric value fine)."""
        p = _write_xml(tmp_path, "ok_skip.xml", """\
            <?xml version="1.0"?>
            <testsuite name="Suite" tests="3" failures="0" errors="0" skipped="1"/>
        """)
        from servers.integration_gate import parse_junit_results
        res = parse_junit_results([p])
        assert res['ran'] is True
        assert res['skipped'] == 1
        # 2 executed (3 total - 1 skipped), no failures → should pass
        assert res['passed'] is True, (
            f"Numeric skipped=1 with 2 executed must pass, got: {res}")

    def test_zero_skipped_still_passes(self, tmp_path):
        """skipped="0" → passed=True (no regression for valid suites)."""
        p = _write_xml(tmp_path, "zero_skip.xml", """\
            <?xml version="1.0"?>
            <testsuite name="Suite" tests="2" failures="0" errors="0" skipped="0"/>
        """)
        from servers.integration_gate import parse_junit_results
        res = parse_junit_results([p])
        assert res['passed'] is True, (
            f"Zero skipped with passing tests must still pass, got: {res}")


# ---------------------------------------------------------------------------
# D5 F3: Malformed boundary dict (missing required keys) → rejected (TDD)
# ---------------------------------------------------------------------------

class TestD5F3MalformedBoundaryDict:
    """D5 F3: integration_boundaries=[{}] bypasses L2.

    A dict missing callee/caller/callee_file/edge makes collaborator list empty,
    so L2 checks nothing. Fix: validate required keys; reject if any boundary
    is missing callee or caller with non-empty values.
    """

    def _make_task_with_boundaries(self, boundaries, result_text=None):
        from servers.tasks import (create_task, create_subtask,
                                   update_task_status, reserve_critic_task)
        epic = create_task(project='proj', description='epic', task_level='epic')
        story = create_subtask(parent_id=epic, description='story',
                               task_level='story', requires_validation=False)
        task = create_subtask(parent_id=story, description='int test',
                              requires_validation=True,
                              metadata={'integration_boundaries': boundaries,
                                        'stack': 'python'})
        update_task_status(task, 'done',
                           result=result_text or 'done')
        critic = reserve_critic_task(task)
        return task, critic['id']

    def test_empty_dict_boundary_rejects(self, mock_db_path, tmp_path, monkeypatch):
        """integration_boundaries=[{}] → verdict 'rejected' (missing callee/caller)."""
        import servers.integration_gate as ig
        import servers.facade as facade
        from servers.tasks import update_task_status

        # Need a test file so test-file check passes
        test_file = tmp_path / "tests" / "test_svc.py"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text("def test_it(): pass\n")

        task, critic_id = self._make_task_with_boundaries(
            [{}],
            result_text='TEST_TARGETS: tests/test_svc.py\ndone')

        # run_tests must NOT be called (reject before L1)
        called = {'n': 0}
        def should_not_call(*a, **kw):
            called['n'] += 1
            return {'ran': True, 'passed': True, 'total': 1,
                    'failures': 0, 'errors': 0, 'error': None, 'evidence': {}}
        monkeypatch.setattr(ig, 'run_tests', should_not_call)

        verdict = facade.run_integration_gate(critic_id, task, 'proj', str(tmp_path))
        assert verdict['verdict'] in ('rejected', 'blocked'), (
            f"D5 F3: [{{}}] boundaries must reject, got {verdict['verdict']!r}")
        assert called['n'] == 0, "run_tests must NOT be called for malformed boundary dicts"

    def test_boundary_missing_callee_rejects(self, mock_db_path, tmp_path, monkeypatch):
        """boundary with caller but no callee → rejected."""
        import servers.integration_gate as ig
        import servers.facade as facade
        from servers.tasks import update_task_status

        test_file = tmp_path / "tests" / "test_svc.py"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text("def test_it(): pass\n")

        task, critic_id = self._make_task_with_boundaries(
            [{'caller': 'Svc', 'edge': 'calls'}],  # missing callee
            result_text='TEST_TARGETS: tests/test_svc.py\ndone')

        called = {'n': 0}
        def should_not_call(*a, **kw):
            called['n'] += 1
            return {'ran': True, 'passed': True, 'total': 1,
                    'failures': 0, 'errors': 0, 'error': None, 'evidence': {}}
        monkeypatch.setattr(ig, 'run_tests', should_not_call)

        verdict = facade.run_integration_gate(critic_id, task, 'proj', str(tmp_path))
        assert verdict['verdict'] in ('rejected', 'blocked'), (
            f"D5 F3: boundary missing callee must reject, got {verdict['verdict']!r}")

    def test_boundary_missing_caller_rejects(self, mock_db_path, tmp_path, monkeypatch):
        """boundary with callee but no caller → rejected."""
        import servers.integration_gate as ig
        import servers.facade as facade
        from servers.tasks import update_task_status

        test_file = tmp_path / "tests" / "test_svc.py"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text("def test_it(): pass\n")

        task, critic_id = self._make_task_with_boundaries(
            [{'callee': 'Repo', 'callee_file': 'repo.py', 'edge': 'calls'}],  # missing caller
            result_text='TEST_TARGETS: tests/test_svc.py\ndone')

        called = {'n': 0}
        def should_not_call(*a, **kw):
            called['n'] += 1
            return {'ran': True, 'passed': True, 'total': 1,
                    'failures': 0, 'errors': 0, 'error': None, 'evidence': {}}
        monkeypatch.setattr(ig, 'run_tests', should_not_call)

        verdict = facade.run_integration_gate(critic_id, task, 'proj', str(tmp_path))
        assert verdict['verdict'] in ('rejected', 'blocked'), (
            f"D5 F3: boundary missing caller must reject, got {verdict['verdict']!r}")

    def test_complete_boundary_dict_proceeds(self, mock_db_path, tmp_path, monkeypatch):
        """Well-formed boundary dict passes the validation (no false positive)."""
        import servers.integration_gate as ig
        import servers.facade as facade

        test_file = tmp_path / "tests" / "test_svc.py"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text("def test_it(): pass\n")

        task, critic_id = self._make_task_with_boundaries(
            [{'caller': 'Svc', 'callee': 'Repo',
              'callee_file': 'repo.py', 'edge': 'calls'}],
            result_text='TEST_TARGETS: tests/test_svc.py\ndone')

        monkeypatch.setattr(ig, 'run_tests', lambda *a, **kw: {
            'ran': True, 'passed': True, 'total': 2,
            'failures': 0, 'errors': 0, 'error': None, 'evidence': {}})
        monkeypatch.setattr(ig, 'detect_mocked_collaborators',
                            lambda src, collabs, stack: [])

        import servers.coverage as cov
        import servers.project as proj_mod
        monkeypatch.setattr(proj_mod, 'ensure_project',
                            lambda *a, **kw: {'tech_stack': {'test_tool': 'pytest'}})
        monkeypatch.setattr(cov, '_coverage_available', lambda: False)

        verdict = facade.run_integration_gate(critic_id, task, 'proj', str(tmp_path))
        assert verdict['verdict'] == 'proceed', (
            f"Well-formed boundary must proceed, got {verdict['verdict']!r}")


# ---------------------------------------------------------------------------
# D5 test-quality fix: Java empty-filter reject branch — correct test
# ---------------------------------------------------------------------------

class TestD5JavaEmptyFilterRejectBranch:
    """D5 test-quality fix: the existing test exercises wrong branch.

    TestMajorEmptyJavaTestFilters._make_java_task_bad_paths uses test_files
    pointing to 'FooTest.java' which lacks src/test/java/ — but the C-a check
    rejects BEFORE we reach the java_filters check (FooTest.java doesn't exist
    under tmp_path, so derive_integration_test_files returns [] and metadata
    fallback to ['FooTest.java'] which is not a real file so no test source is
    readable; but importantly we need to reach the java_filters empty check
    with test_files present but no FQ mapping).

    The corrected test: arrange test_files present (file exists so C-a passes)
    but path lacks src/test/java/ so FQ derivation yields '' (empty string)
    which must trigger reject. run_tests must NOT be called.

    Also add a unit test for _java_test_file_to_fq_classname with
    unmappable / empty inputs.
    """

    def test_java_fq_classname_helper_empty_on_unmappable(self):
        """_java_test_file_to_fq_classname returns '' for paths lacking src/test/java/."""
        from servers.facade import _java_test_file_to_fq_classname
        # Bare filename, no directory segments
        assert _java_test_file_to_fq_classname('FooTest.java') == 'FooTest', (
            "bare filename without path: extension stripped, no dots")
        # But lacks src/test/java — still returns something (just classname without pkg)
        # The key insight: this becomes 'FooTest' not 'com.example.FooTest'
        # The filter check in facade: java_filters excludes entries that don't contain
        # the /test/ path check — let us test what actually becomes empty
        assert _java_test_file_to_fq_classname('') == '', "empty string → empty"

    def test_java_fq_classname_helper_normal(self):
        """_java_test_file_to_fq_classname works for standard paths."""
        from servers.facade import _java_test_file_to_fq_classname
        result = _java_test_file_to_fq_classname(
            'src/test/java/com/example/OrderServiceIT.java')
        assert result == 'com.example.OrderServiceIT', (
            f"Standard path must map to FQ class, got {result!r}")

    def test_java_empty_filter_reject_genuine(self, mock_db_path, tmp_path, monkeypatch):
        """Correctly exercises the java_filters empty reject branch.

        Arrange: java task with test file that EXISTS on disk (so C-a check passes)
        but whose path does NOT satisfy the _derive_java_test_filters condition
        (lacks src/test/java/ OR /test/ in path), so java_filters == [].
        The gate must reject BEFORE calling run_tests.
        """
        import servers.integration_gate as ig
        import servers.facade as facade
        from servers.tasks import (create_task, create_subtask,
                                   update_task_status, reserve_critic_task)

        # Create a test file that EXISTS but lacks src/test/java/ — FQ derivation
        # will produce a bare name but _derive_java_test_filters requires '/test/' in path
        test_file = tmp_path / 'FooIT.java'
        test_file.write_text('@SpringBootTest\npublic class FooIT {}\n')

        boundaries = [{'caller': 'Svc', 'callee': 'Repo',
                       'callee_file': 'Repo.java', 'edge': 'calls'}]
        epic = create_task(project='proj', description='epic', task_level='epic')
        story = create_subtask(parent_id=epic, description='story',
                               task_level='story', requires_validation=False)
        task = create_subtask(parent_id=story, description='java integration test',
                              requires_validation=True,
                              metadata={
                                  'integration_boundaries': boundaries,
                                  'stack': 'java',
                                  # NO test_files metadata — rely on TEST_TARGETS marker
                              })
        # TEST_TARGETS marker pointing to the file that exists but lacks /test/ in path
        update_task_status(task, 'done',
                           result='TEST_TARGETS: FooIT.java\nIntegration tests done.')
        critic = reserve_critic_task(task)

        # run_tests must NOT be called
        run_tests_called = {'n': 0}
        def should_not_call(*a, **kw):
            run_tests_called['n'] += 1
            return {'ran': True, 'passed': True, 'total': 1,
                    'failures': 0, 'errors': 0, 'error': None, 'evidence': {}}
        monkeypatch.setattr(ig, 'run_tests', should_not_call)

        verdict = facade.run_integration_gate(critic['id'], task, 'proj', str(tmp_path))
        assert verdict['verdict'] in ('rejected', 'blocked'), (
            f"Java task with unmappable test file must reject (no FQ filter), "
            f"got {verdict['verdict']!r}")
        assert run_tests_called['n'] == 0, (
            "run_tests must NOT be called when java_filters is empty")
