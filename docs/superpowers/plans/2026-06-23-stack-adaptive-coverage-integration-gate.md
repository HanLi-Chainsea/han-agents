# Stack-Adaptive Branch Coverage + Integration-Test Gate — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `/han:unit-test` (branch coverage) and `/han:integration-test` (real-collaboration gate) tool-verifiable and fail-closed on **both Python and Java**, branch coverage measured as logical BRANCH (never line).

**Architecture:** Keep the shipped `servers/coverage.py` (Python `coverage.py --branch`) untouched as the Python backend. Add a **Java JaCoCo backend** that returns the *identical* result dict contract (`tool_status`/`fully_covered`/`per_target`), measured non-invasively via a Gradle `-I` init-script (proven by spike) + standalone `jacococli`. A thin stack dispatcher routes by `tech_stack.test_tool`. The integration gate is a separate policy: L1 run+pass (hard) + L2 static mock-smell (hard) + L3 branch-coverage advisory.

**Tech Stack:** Python 3 / pytest / coverage.py; Java 17+ / Gradle (wrapper) / JUnit5 / JaCoCo 0.8.12 (agent + cli jars bundled, no build.gradle change).

## Global Constraints
- Coverage = **logical BRANCH** only. Python `coverage.py --branch`; Java JaCoCo `<counter type="BRANCH">`. Never line coverage as a gate.
- **Never** modify `build.gradle`/`build.gradle.kts`/`gradle.properties`/`settings.gradle` or change JDK/toolchain version. Java coverage is attached only via external `-I` init-script + env/sysprops.
- **Fail-closed**: tool cannot confirm → deterministic reject (or advisory `not-measurable` for L3); never green-light unverified coverage (no 假綠).
- Shipped Python unit-test gate behavior + its 427 tests must not regress.
- Integration boundaries = `injects`/call edges only (NOT imports/extends/implements).
- Integration hard gate = L1 (ran+passed) + L2 (collaborator not mocked). Branch coverage there is **advisory** (4-class), never blocking.
- Java backend must return the SAME dict shape as `measure_branch_coverage` so `run_coverage_gate` stays backend-agnostic.

---

## PART A — Stack-adaptive branch coverage + Java unit-test (proven feasible by spike)

### Task A1: Bundle JaCoCo tooling + init-script into the repo

**Files:**
- Create: `reference/tools/jacoco/jacocoagent.jar` (0.8.12 runtime), `reference/tools/jacoco/jacococli.jar` (0.8.12 nodeps)
- Create: `reference/tools/jacoco/jacoco-init.gradle`
- Create: `reference/tools/jacoco/README.md` (provenance: Maven Central org.jacoco 0.8.12, why non-invasive)
- Test: `tests/test_java_coverage.py` (existence + jar sanity)

**Interfaces:**
- Produces: constant paths `servers.coverage_java.JACOCO_AGENT`, `JACOCO_CLI`, `JACOCO_INIT` resolving to these files.

- [ ] **Step 1: Write the failing test**
```python
# tests/test_java_coverage.py
import os, zipfile
def test_jacoco_tooling_bundled():
    from servers import coverage_java as cj
    for p in (cj.JACOCO_AGENT, cj.JACOCO_CLI, cj.JACOCO_INIT):
        assert os.path.isfile(p), p
    assert zipfile.is_zipfile(cj.JACOCO_AGENT)   # real jar
    assert zipfile.is_zipfile(cj.JACOCO_CLI)
    assert 'javaagent' in open(cj.JACOCO_INIT).read()
```
- [ ] **Step 2: Run → FAIL** (`servers.coverage_java` missing). `pytest tests/test_java_coverage.py::test_jacoco_tooling_bundled -q`
- [ ] **Step 3: Copy the spike's verified artifacts in**
```bash
mkdir -p reference/tools/jacoco
cp /tmp/jacoco-spike/tools/jacocoagent.jar reference/tools/jacoco/
cp /tmp/jacoco-spike/tools/jacococli.jar  reference/tools/jacoco/
cp /tmp/jacoco-spike/jacoco-init.gradle   reference/tools/jacoco/
```
  Create `servers/coverage_java.py` head with the path constants:
```python
import os
_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_JDIR = os.path.join(_BASE, 'reference', 'tools', 'jacoco')
JACOCO_AGENT = os.path.join(_JDIR, 'jacocoagent.jar')
JACOCO_CLI   = os.path.join(_JDIR, 'jacococli.jar')
JACOCO_INIT  = os.path.join(_JDIR, 'jacoco-init.gradle')
```
- [ ] **Step 4: Run → PASS**
- [ ] **Step 5: Commit** `git add reference/tools/jacoco servers/coverage_java.py tests/test_java_coverage.py && git commit -m "feat(coverage-java): bundle non-invasive JaCoCo tooling (spike-verified)"`

### Task A2: Parse JaCoCo BRANCH XML → per-target branch coverage (pure, no Gradle)

**Files:**
- Modify: `servers/coverage_java.py`
- Test: `tests/test_java_coverage.py`

**Interfaces:**
- Produces: `parse_jacoco_xml(xml_path: str, coverage_targets: list[dict], source_root: str) -> dict` returning the SAME shape as `coverage.measure_branch_coverage`: `{'tool_status','fully_covered','per_target':[{file_path,name,line_start,line_end,missing_branches,covered_branches,n_total,n_covered}], 'error'}`. For Java a "branch" is a per-line decision slot; `missing_branches`/`covered_branches` entries are `{'from': <line>, 'to': <slot_index>}` so the existing `format_coverage_summary` renders `L<line>→<slot>`.
- Consumes: JaCoCo XML `<line nr= mb= cb=>` (missed/covered branch counts per line) within each target method's `[line_start,line_end]`.

- [ ] **Step 1: Write the failing test** (uses a checked-in fixture XML produced by the spike)
```python
def test_parse_jacoco_branch_partial(tmp_path):
    from servers import coverage_java as cj
    xml = tmp_path / "r.xml"
    xml.write_text('''<?xml version="1.0"?><report name="t">
      <package name="demo"><class name="demo/Classify" sourcefilename="Classify.java">
        <method name="of" desc="(I)Ljava/lang/String;" line="4">
          <counter type="BRANCH" missed="3" covered="1"/></method>
      </class>
      <sourcefile name="Classify.java">
        <line nr="4" mi="0" ci="3" mb="1" cb="1"/>
        <line nr="7" mi="3" ci="0" mb="2" cb="0"/>
      </sourcefile></package></report>''')
    targets = [{'file_path':'src/main/java/demo/Classify.java','name':'of','line_start':4,'line_end':9}]
    res = cj.parse_jacoco_xml(str(xml), targets, 'src/main/java')
    assert res['tool_status'] == 'ok'
    pt = res['per_target'][0]
    assert pt['n_total'] == 4 and pt['n_covered'] == 1     # 1 covered + 3 missed branch slots
    assert res['fully_covered'] is False
    # line 7 has 2 missed branch slots → 2 ✗ entries anchored at line 7
    assert sum(1 for a in pt['missing_branches'] if a['from']==7) == 2
```
- [ ] **Step 2: Run → FAIL** (`parse_jacoco_xml` missing)
- [ ] **Step 3: Implement `parse_jacoco_xml`** — walk `<sourcefile>` lines; for lines within a target's range build `covered_branches` (cb slots) + `missing_branches` (mb slots) as `{'from':nr,'to':i}`; `n_total=cb+mb`, `n_covered=cb`; `fully_covered = all(no missing across targets)`. Schema-guard: if no `<sourcefile>`/no BRANCH data for a present class → `tool_status='schema_error'` (fail-closed). If a target file/method absent from XML → `'no_targets'`.
- [ ] **Step 4: Run → PASS**
- [ ] **Step 5: Commit** `feat(coverage-java): parse JaCoCo BRANCH XML to per-target branch coverage`

### Task A3: Run scoped Gradle test with non-invasive agent → measure (integration with real Gradle)

**Files:**
- Modify: `servers/coverage_java.py`
- Test: `tests/test_java_coverage_live.py` (marked `@pytest.mark.slow`, skipped if no `gradlew`)

**Interfaces:**
- Produces: `measure_branch_coverage_java(project_path, test_targets, coverage_targets, *, gradle_module=None, test_filters=None) -> dict` — same contract as Python backend. Runs `./gradlew [:module:]test --tests <filter> --no-daemon -I <JACOCO_INIT> -Dhan.jacoco.agent=… -Dhan.jacoco.exec=…`; on test failure → `tool_status='tests_failed'`; rc≠0 non-test → `'test_run_error'`; then `jacococli report … --xml` → `parse_jacoco_xml`. Class/source dirs resolved from `build/classes/java/main` + `src/main/java` (per-module); missing → `'no_targets'`.

- [ ] **Step 1: Write the failing live test** against the proven spike fixture (copy `/tmp/jacoco-spike` into tmp_path)
```python
import pytest, shutil, os
pytestmark = pytest.mark.slow
def test_live_java_branch_measure(tmp_path):
    if not shutil.which('java'): pytest.skip("no java")
    src = "/tmp/jacoco-spike"
    if not os.path.isdir(src): pytest.skip("spike fixture absent")
    proj = tmp_path / "p"; shutil.copytree(src, proj)
    from servers import coverage_java as cj
    targets = [{'file_path':'src/main/java/demo/Classify.java','name':'of','line_start':4,'line_end':12}]
    res = cj.measure_branch_coverage_java(str(proj), ['ClassifyTest'], targets, test_filters=['demo.ClassifyTest'])
    assert res['tool_status'] == 'ok'
    assert res['fully_covered'] is False
    assert res['per_target'][0]['n_covered'] >= 1
```
- [ ] **Step 2: Run → FAIL**
- [ ] **Step 3: Implement `measure_branch_coverage_java`** (subprocess to gradlew with the init-script + sysprops, then jacococli, then parse). Timeout 600s. Use a tempdir for exec/xml. Honour the no-build-change constraint (only `-I`/`-D`).
- [ ] **Step 4: Run → PASS** `pytest tests/test_java_coverage_live.py -q -m slow`
- [ ] **Step 5: Commit** `feat(coverage-java): non-invasive scoped Gradle+JaCoCo branch measurement`

### Task A4: Stack dispatcher + wire Java into the unit-test gate

**Files:**
- Modify: `servers/coverage_java.py` (add `select_backend`), `servers/facade.py` (`run_coverage_gate` routes by stack)
- Test: `tests/test_coverage_gate.py` (new `TestStackDispatch`)

**Interfaces:**
- Produces: `select_backend(tech_stack: dict) -> str` returning `'python'|'java'|'unknown'`.
- Modifies: `run_coverage_gate` — after metadata guard, read `tech_stack` (via the original task's project), pick backend; `java` → `measure_branch_coverage_java`, `python` → existing `coverage.measure_branch_coverage`, `unknown`/tool-absent → existing fail-open (manual checklist). Everything downstream (`format_coverage_summary`, `_gate_reject`) unchanged since the dict shape is identical.

- [ ] **Step 1: Write failing test** — monkeypatch `measure_branch_coverage_java`, assert a java-stack task routes to it and a python-stack task routes to `coverage.measure_branch_coverage`.
- [ ] **Step 2: Run → FAIL**
- [ ] **Step 3: Implement `select_backend`** (gradle/maven/`*.java` test_tool → java; pytest/unittest → python) + route in `run_coverage_gate`.
- [ ] **Step 4: Run → PASS**, then full `pytest -q` (427 Python tests still green).
- [ ] **Step 5: Commit** `feat(coverage-gate): stack-adaptive backend routing (python+java branch coverage)`

---

## PART B — Integration-test gate (L1 hard + L2 hard + L3 advisory)

### Task B1: L1 — run scoped tests, parse native result XML → deterministic pass/fail

**Files:**
- Create: `servers/integration_gate.py`
- Test: `tests/test_integration_gate.py`

**Interfaces:**
- Produces: `run_tests(project_path, stack, *, gradle_module=None, test_filters=None, py_test_files=None) -> dict` = `{'ran': bool, 'passed': bool, 'total': int, 'failures': int, 'errors': int, 'error': str|None, 'evidence': {...}}`. Java: parse `build/test-results/test/*.xml` (`<testsuite tests= failures= errors=>`). Python: `pytest --junitxml`. `ran=False`/non-zero infra → `passed=False`.

- [ ] **Step 1: Write failing test** — feed a JUnit `<testsuite tests="2" failures="1">` XML fixture to a pure `parse_junit_results(paths)` → `passed False`. And a `failures="0"` → `passed True`.
- [ ] **Step 2: Run → FAIL**
- [ ] **Step 3: Implement `parse_junit_results` + `run_tests`** (subprocess; deterministic, no LLM).
- [ ] **Step 4: Run → PASS** (+ a `@pytest.mark.slow` live run against the spike fixture asserting `passed True`).
- [ ] **Step 5: Commit** `feat(integration-gate): L1 deterministic run+pass via native result XML`

### Task B2: L2 — static mock-smell scanner on boundary collaborators

**Files:**
- Modify: `servers/integration_gate.py`
- Test: `tests/test_integration_gate.py`

**Interfaces:**
- Produces: `detect_mocked_collaborators(test_source: str, collaborators: list[str], stack: str) -> list[str]` — returns the subset of `collaborators` that the test mocks. Java patterns: `@MockBean`, `@MockitoBean`, `@Mock`, `@InjectMocks`, `Mockito.mock(<Type>`, `mock(<Type>.class)`. Python: `unittest.mock`/`patch('<dotted>'` / `MagicMock` bound to the collaborator name. Match by simple type name (last path segment).

- [ ] **Step 1: Write failing test** — Java source with `@MockBean private OrderRepository repo;` and collaborators `['OrderRepository','OrderService']` → returns `['OrderRepository']`. A clean source → `[]`.
- [ ] **Step 2: Run → FAIL**
- [ ] **Step 3: Implement** regex/AST scan (Java: regex on annotations+Mockito calls; Python: `ast` walk for `patch`/`mock.patch` targets).
- [ ] **Step 4: Run → PASS**
- [ ] **Step 5: Commit** `feat(integration-gate): L2 static mock-smell detection (java+python)`

### Task B3: Boundary extraction from Code Graph (injects/call edges only)

**Files:**
- Modify: `servers/integration_gate.py`
- Test: `tests/test_integration_gate.py`

**Interfaces:**
- Produces: `boundaries_for_target(project_name, target_files: list[str]) -> list[dict]` = `[{'caller': fqn, 'callee': fqn, 'callee_file': path, 'edge': 'injects'|'calls'}]`. Uses `servers.code_graph` edges filtered to `injects`/call kinds; drops `imports/extends/implements`.

- [ ] **Step 1: Write failing test** — monkeypatch `code_graph.get_code_edges` to return mixed edges; assert only injects/call boundaries returned, imports/extends excluded.
- [ ] **Step 2: Run → FAIL**
- [ ] **Step 3: Implement** edge filter + shaping.
- [ ] **Step 4: Run → PASS**
- [ ] **Step 5: Commit** `feat(integration-gate): boundary extraction from Code Graph (injects/call only)`

### Task B4: Integration gate policy + L3 advisory 4-class + report

**Files:**
- Modify: `servers/integration_gate.py`
- Test: `tests/test_integration_gate.py`

**Interfaces:**
- Produces: `run_integration_gate(critic_task_id, original_task_id, project_name, project_path) -> dict` mirroring `run_coverage_gate`'s verdict contract (`{'verdict':'proceed'|'rejected'|'blocked', 'warn', 'coverage_summary'?}`). Policy: L1 fail → `_gate_reject`; L2 any boundary mocked → `_gate_reject` (lists which); both pass → proceed. L3 classifies each boundary `verified-real|mocked|not-observed|not-measurable` using the Part-A coverage backend on the callee (advisory; never changes verdict). `format_boundary_summary(boundaries) -> list[str]` for the human report.

- [ ] **Step 1: Write failing tests** — (a) mocked boundary → verdict `rejected` + reason names the collaborator; (b) real+passing → `proceed`; (c) L3 classification maps coverage states to the 4 labels; (d) coverage unavailable → boundary `not-measurable`, verdict still `proceed` (advisory never blocks).
- [ ] **Step 2: Run → FAIL**
- [ ] **Step 3: Implement** policy + 4-class + summary, reusing `facade._gate_reject`.
- [ ] **Step 4: Run → PASS**
- [ ] **Step 5: Commit** `feat(integration-gate): policy (L1+L2 hard, L3 advisory 4-class) + human report`

### Task B5: Recipe metadata + gated dispatch + command interaction

**Files:**
- Modify: `servers/recipes.py` (`recipe_integration_tests` attaches `metadata={'integration_boundaries': …}`), `servers/facade.py` (`get_next_dispatch_integration_gated` or extend gating), `commands/han/integration-test.md` (Q1 scenario via native options; print `coverage_summary`/boundary report; gated dispatch)
- Test: `tests/test_integration_gate.py`

**Interfaces:**
- Consumes: B3 `boundaries_for_target`, B4 `run_integration_gate`.
- Produces: integration recipe tasks carry boundary metadata; dispatch loop runs the integration gate before critic (same pattern as `get_next_dispatch_gated`).

- [ ] **Step 1: Write failing test** — a done integration task with a mocked boundary, routed through the gated dispatch, returns executor-retry with the collaborator named in the prompt (mirrors `test_gated_rejects_then_executor_prompt_carries_missing_arc`).
- [ ] **Step 2: Run → FAIL**
- [ ] **Step 3: Implement** recipe metadata + gated dispatch wiring + command Q1 options & report section.
- [ ] **Step 4: Run → PASS**, full `pytest -q`.
- [ ] **Step 5: Commit** `feat(integration-test): gated dispatch + scenario prompt + boundary report`

---

## Verification (must actually run, not claim)
- [ ] `pytest -q` all green (Python unchanged + new).
- [ ] `pytest -q -m slow` live: Java branch coverage on spike fixture (partial), L1 pass/fail on spike fixture.
- [ ] Fixture-matrix spot checks for L3 4-class: interface+impl, MapStruct `*Impl`, `@MockBean` (→ mocked), real bean (→ verified-real or not-measurable, never false ✓).
- [ ] CCG review (codex authoritative) on the full diff; close Critical/Major before done.

## Notes / deviations from spec
- Leaner than spec's `servers/coverage/` package: keep `servers/coverage.py`, add `servers/coverage_java.py` + `servers/integration_gate.py`. Reuses the shipped gate; 427 tests don't move. (Honors 不爆複雜度 + codex "don't restructure unilaterally".)
- Java "branch" granularity is per-line decision slots (JaCoCo gives counts, not arc dest); rendered as `L<line>→<slot>` so the shipped `format_coverage_summary` is reused unchanged.
