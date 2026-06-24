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
