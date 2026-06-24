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
        """Valid + corrupt file: passed=True (valid suite clean) but error warns of bad file."""
        # Valid XML with clean result
        p_valid = _write_xml(tmp_path, "valid.xml", """\
            <?xml version="1.0"?>
            <testsuite name="Clean" tests="2" failures="0" errors="0" skipped="0"/>
        """)
        # Non-existent file
        p_bad = str(tmp_path / "nonexistent.xml")

        from servers.integration_gate import parse_junit_results
        res = parse_junit_results([p_valid, p_bad])

        # Valid suite is clean, so passed should be True
        assert res["passed"] is True
        # But we should have a warning about the bad file
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
        task, critic_id = self._setup_integration_task(
            {'integration_boundaries': boundaries,
             'test_files': ['OrderServiceTest.java'],
             'stack': 'java'})

        # L1 passes
        monkeypatch.setattr(ig, 'run_tests', lambda *a, **kw: {
            'ran': True, 'passed': True, 'total': 2,
            'failures': 0, 'errors': 0, 'error': None, 'evidence': {}})
        # L2 detects mock
        monkeypatch.setattr(ig, 'detect_mocked_collaborators',
                            lambda src, collabs, stack: ['OrderRepository'])
        # test file readable
        test_file = tmp_path / 'OrderServiceTest.java'
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
             'test_files': ['OrderServiceTest.java'],
             'stack': 'java'})

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

        test_file = tmp_path / 'OrderServiceTest.java'
        test_file.write_text('@Autowired\nOrderRepository repo;\n')

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
             'test_files': ['OrderServiceTest.java'],
             'stack': 'java'})

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

        test_file = tmp_path / 'OrderServiceTest.java'
        test_file.write_text('@Autowired\nOrderRepository repo;\n')

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
