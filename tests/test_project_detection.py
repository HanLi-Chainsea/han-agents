"""
Tests for _detect_test_tool_from_config and _detect_tech_stack —
config-file-based test runner detection + framework/test_tool decoupling.
TDD: write tests first, watch them fail, then implement.
"""

import json
import sqlite3
import pytest

from servers.project import _detect_test_tool_from_config


class TestVitestDetection:
    def test_vitest_config_overrides_react_jest_hint(self, tmp_path):
        """vitest.config.ts present + react in package.json → vitest wins."""
        (tmp_path / "vitest.config.ts").write_text(
            "import { defineConfig } from 'vitest/config';\nexport default defineConfig({});"
        )
        (tmp_path / "package.json").write_text(json.dumps({
            "dependencies": {"react": "^18.0.0"},
            "devDependencies": {"vite": "^5.0.0"},
        }))
        assert _detect_test_tool_from_config(tmp_path) == "vitest"

    def test_vitest_config_js(self, tmp_path):
        """vitest.config.js → vitest."""
        (tmp_path / "vitest.config.js").write_text("export default {};")
        assert _detect_test_tool_from_config(tmp_path) == "vitest"

    def test_vite_config_with_test_key(self, tmp_path):
        """vite.config.ts containing 'test:' → vitest."""
        (tmp_path / "vite.config.ts").write_text(
            "export default defineConfig({ test: { globals: true } });"
        )
        assert _detect_test_tool_from_config(tmp_path) == "vitest"

    def test_package_json_devdep_vitest(self, tmp_path):
        """package.json devDependencies containing vitest → vitest."""
        (tmp_path / "package.json").write_text(json.dumps({
            "devDependencies": {"vitest": "^1.0.0"},
        }))
        assert _detect_test_tool_from_config(tmp_path) == "vitest"

    def test_package_json_test_script_vitest(self, tmp_path):
        """package.json scripts.test mentioning vitest → vitest."""
        (tmp_path / "package.json").write_text(json.dumps({
            "scripts": {"test": "vitest run"},
        }))
        assert _detect_test_tool_from_config(tmp_path) == "vitest"

    def test_vitest_beats_jest_when_both_present(self, tmp_path):
        """Both vitest.config.ts and a jest devDep → vitest wins."""
        (tmp_path / "vitest.config.ts").write_text("export default {};")
        (tmp_path / "package.json").write_text(json.dumps({
            "devDependencies": {"jest": "^29.0.0"},
        }))
        assert _detect_test_tool_from_config(tmp_path) == "vitest"

    def test_package_json_dep_vitest(self, tmp_path):
        """vitest in dependencies (not just devDependencies) → vitest."""
        (tmp_path / "package.json").write_text(json.dumps({
            "dependencies": {"vitest": "^1.0.0"},
        }))
        assert _detect_test_tool_from_config(tmp_path) == "vitest"


class TestJestDetection:
    def test_jest_config_js(self, tmp_path):
        """jest.config.js present → jest."""
        (tmp_path / "jest.config.js").write_text("module.exports = {};")
        assert _detect_test_tool_from_config(tmp_path) == "jest"

    def test_jest_config_ts(self, tmp_path):
        """jest.config.ts present → jest."""
        (tmp_path / "jest.config.ts").write_text("export default {};")
        assert _detect_test_tool_from_config(tmp_path) == "jest"

    def test_jest_config_json(self, tmp_path):
        """jest.config.json present → jest."""
        (tmp_path / "jest.config.json").write_text("{}")
        assert _detect_test_tool_from_config(tmp_path) == "jest"

    def test_package_json_jest_key(self, tmp_path):
        """package.json with top-level 'jest' key → jest."""
        (tmp_path / "package.json").write_text(json.dumps({
            "jest": {"testEnvironment": "node"},
        }))
        assert _detect_test_tool_from_config(tmp_path) == "jest"

    def test_package_json_devdep_jest(self, tmp_path):
        """package.json devDependencies containing jest → jest."""
        (tmp_path / "package.json").write_text(json.dumps({
            "devDependencies": {"jest": "^29.0.0"},
        }))
        assert _detect_test_tool_from_config(tmp_path) == "jest"

    def test_package_json_test_script_jest(self, tmp_path):
        """package.json scripts.test mentioning jest → jest."""
        (tmp_path / "package.json").write_text(json.dumps({
            "scripts": {"test": "jest --coverage"},
        }))
        assert _detect_test_tool_from_config(tmp_path) == "jest"


class TestPytestDetection:
    def test_pyproject_pytest(self, tmp_path):
        """pyproject.toml containing [tool.pytest → pytest."""
        (tmp_path / "pyproject.toml").write_text(
            "[tool.pytest.ini_options]\ntestpaths = [\"tests\"]\n"
        )
        assert _detect_test_tool_from_config(tmp_path) == "pytest"

    def test_pytest_ini(self, tmp_path):
        """pytest.ini present → pytest."""
        (tmp_path / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n")
        assert _detect_test_tool_from_config(tmp_path) == "pytest"

    def test_tox_ini_with_pytest_section(self, tmp_path):
        """tox.ini with [pytest] section → pytest."""
        (tmp_path / "tox.ini").write_text("[pytest]\naddopts = -v\n")
        assert _detect_test_tool_from_config(tmp_path) == "pytest"

    def test_setup_cfg_with_tool_pytest(self, tmp_path):
        """setup.cfg with [tool:pytest] section → pytest."""
        (tmp_path / "setup.cfg").write_text("[tool:pytest]\naddopts = -v\n")
        assert _detect_test_tool_from_config(tmp_path) == "pytest"


class TestJVMDetection:
    def test_gradle(self, tmp_path):
        """build.gradle present → gradle."""
        (tmp_path / "build.gradle").write_text("apply plugin: 'java'")
        assert _detect_test_tool_from_config(tmp_path) == "gradle"

    def test_gradle_kts(self, tmp_path):
        """build.gradle.kts present → gradle."""
        (tmp_path / "build.gradle.kts").write_text("plugins { java }")
        assert _detect_test_tool_from_config(tmp_path) == "gradle"

    def test_settings_gradle(self, tmp_path):
        """settings.gradle present → gradle."""
        (tmp_path / "settings.gradle").write_text("rootProject.name = 'myapp'")
        assert _detect_test_tool_from_config(tmp_path) == "gradle"

    def test_maven(self, tmp_path):
        """pom.xml present → maven."""
        (tmp_path / "pom.xml").write_text("<project></project>")
        assert _detect_test_tool_from_config(tmp_path) == "maven"


class TestOtherLanguages:
    def test_cargo_toml(self, tmp_path):
        """Cargo.toml present → cargo test."""
        (tmp_path / "Cargo.toml").write_text("[package]\nname = \"mylib\"\n")
        assert _detect_test_tool_from_config(tmp_path) == "cargo test"

    def test_go_mod(self, tmp_path):
        """go.mod present → go test."""
        (tmp_path / "go.mod").write_text("module example.com/myapp\n\ngo 1.21\n")
        assert _detect_test_tool_from_config(tmp_path) == "go test"


class TestEdgeCases:
    def test_no_config_returns_none(self, tmp_path):
        """Empty directory → None (no crash)."""
        assert _detect_test_tool_from_config(tmp_path) is None

    def test_malformed_package_json_returns_none(self, tmp_path):
        """Malformed package.json → None (no crash)."""
        (tmp_path / "package.json").write_text("{ this is not json }")
        assert _detect_test_tool_from_config(tmp_path) is None

    def test_project_path_none_returns_none(self, tmp_path):
        """project_path=None → None (skip pass)."""
        assert _detect_test_tool_from_config(None) is None

    def test_vite_config_without_test_key(self, tmp_path):
        """vite.config.ts without 'test:' keyword → no vitest signal from this file."""
        (tmp_path / "vite.config.ts").write_text(
            "export default defineConfig({ server: { port: 3000 } });"
        )
        # No other signals → None
        assert _detect_test_tool_from_config(tmp_path) is None


# =============================================================================
# D1 — Framework imports must NOT set test_tool
# =============================================================================

class TestD1FrameworkDecoupled:
    """D1: Framework hint entries (react, next, express, vue, nuxt) must NOT inject
    test_tool into _detect_tech_stack result — only real test-runner imports should."""

    def _make_db_with_imports(self, db_path, project, imports):
        """Insert code_nodes + import edges into a real schema DB."""
        import os
        schema_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'brain', 'schema.sql'
        )
        conn = sqlite3.connect(db_path)
        with open(schema_path, encoding='utf-8') as f:
            conn.executescript(f.read())

        # Insert a source file node
        conn.execute(
            "INSERT OR IGNORE INTO code_nodes (id, project, kind, name, language) "
            "VALUES ('file.src', ?, 'file', 'src/index.ts', 'typescript')",
            (project,)
        )
        # Insert import target nodes and edges
        for imp in imports:
            node_id = f'import.{imp}'
            conn.execute(
                "INSERT OR IGNORE INTO code_nodes (id, project, kind, name) "
                "VALUES (?, ?, 'import', ?)",
                (node_id, project, imp)
            )
            conn.execute(
                "INSERT OR IGNORE INTO code_edges (project, from_id, to_id, kind) "
                "VALUES (?, 'file.src', ?, 'imports')",
                (project, node_id)
            )
        conn.commit()
        conn.close()

    def test_react_import_no_jest_config_no_jest_in_test_tool(
            self, tmp_path, mock_db_path, monkeypatch):
        """D1: importing 'react' with no jest/vitest config → test_tool != 'jest' from hint.

        Without any config files and without jest/vitest import nodes,
        the test_tool should come from the language default (jest for typescript)
        or be None — but NOT be forced to 'jest' by the react framework hint.

        Specifically: the react hint must NOT carry test_tool='jest'.
        After D1, react hint sets test_tool=None, so import heuristic yields
        only the react framework — no test_tool from hints → falls to language default.
        We verify that the react hint alone is not responsible for setting test_tool.
        """
        from servers.project import _detect_tech_stack, _FRAMEWORK_HINTS

        # D1 fix: the react hint must have test_tool=None
        framework_name, test_tool_from_hint = _FRAMEWORK_HINTS.get('react', (None, None))
        assert test_tool_from_hint is None, (
            "D1 violation: react hint carries test_tool='jest'; "
            "framework hints must not set test_tool"
        )

    def test_next_hint_has_no_test_tool(self):
        """D1: 'next' framework hint must not carry test_tool."""
        from servers.project import _FRAMEWORK_HINTS
        _, test_tool = _FRAMEWORK_HINTS.get('next', (None, None))
        assert test_tool is None, "D1 violation: next hint carries test_tool"

    def test_express_hint_has_no_test_tool(self):
        """D1: 'express' framework hint must not carry test_tool."""
        from servers.project import _FRAMEWORK_HINTS
        _, test_tool = _FRAMEWORK_HINTS.get('express', (None, None))
        assert test_tool is None, "D1 violation: express hint carries test_tool"

    def test_vue_hint_has_no_test_tool(self):
        """D1: 'vue' framework hint must not carry test_tool."""
        from servers.project import _FRAMEWORK_HINTS
        _, test_tool = _FRAMEWORK_HINTS.get('vue', (None, None))
        assert test_tool is None, "D1 violation: vue hint carries test_tool"

    def test_nuxt_hint_has_no_test_tool(self):
        """D1: 'nuxt' framework hint must not carry test_tool."""
        from servers.project import _FRAMEWORK_HINTS
        _, test_tool = _FRAMEWORK_HINTS.get('nuxt', (None, None))
        assert test_tool is None, "D1 violation: nuxt hint carries test_tool"

    def test_react_only_import_does_not_set_jest_via_hint(
            self, tmp_path, mock_db_path):
        """D1 integration: project with only 'react' import (no jest/vitest) and
        no config files → test_tool is NOT 'jest' sourced from react hint.

        After D1, with no config and no jest/vitest import nodes, test_tool may be
        the language default (typescript→jest) but NOT from the react hint itself.
        We confirm the react hint's test_tool field is None so it cannot pollute.
        """
        from servers.project import _FRAMEWORK_HINTS
        _, react_test_tool = _FRAMEWORK_HINTS['react']
        assert react_test_tool is None


# =============================================================================
# D2 — Ambiguous import heuristic → None when >1 test_tool and no config
# =============================================================================

class TestD2AmbiguousTestTool:
    """D2: If import heuristic sees both 'jest' and 'vitest' nodes but no config
    file → test_tool must be None (not silently 'jest' via sorted()[0])."""

    def _make_db_with_imports(self, db_path, project, imports):
        import os
        schema_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'brain', 'schema.sql'
        )
        conn = sqlite3.connect(db_path)
        with open(schema_path, encoding='utf-8') as f:
            conn.executescript(f.read())
        conn.execute(
            "INSERT OR IGNORE INTO code_nodes "
            "(id, project, kind, name, language) VALUES "
            "('file.src', ?, 'file', 'src/index.ts', 'typescript')",
            (project,)
        )
        for imp in imports:
            node_id = f'import.{imp}'
            conn.execute(
                "INSERT OR IGNORE INTO code_nodes (id, project, kind, name) "
                "VALUES (?, ?, 'import', ?)",
                (node_id, project, imp)
            )
            conn.execute(
                "INSERT OR IGNORE INTO code_edges "
                "(project, from_id, to_id, kind) "
                "VALUES (?, 'file.src', ?, 'imports')",
                (project, node_id)
            )
        conn.commit()
        conn.close()

    def test_both_jest_and_vitest_import_no_config_yields_none(
            self, tmp_path, mock_db_path):
        """D2: imports include both 'jest' and 'vitest', no config → test_tool is None."""
        import servers as s
        self._make_db_with_imports(
            s.BRAIN_DB, 'proj_d2', ['jest', 'vitest']
        )
        from servers.project import _detect_tech_stack
        result = _detect_tech_stack('proj_d2', project_path=None)
        assert result['test_tool'] is None, (
            f"D2 violation: expected None for ambiguous imports, "
            f"got {result['test_tool']!r}"
        )

    def test_jest_only_import_no_config_yields_jest(
            self, tmp_path, mock_db_path):
        """D2: only 'jest' imported, no config → test_tool is 'jest' (unambiguous)."""
        import servers as s
        self._make_db_with_imports(
            s.BRAIN_DB, 'proj_d2b', ['jest']
        )
        from servers.project import _detect_tech_stack
        result = _detect_tech_stack('proj_d2b', project_path=None)
        assert result['test_tool'] == 'jest'

    def test_vitest_only_import_no_config_yields_vitest(
            self, tmp_path, mock_db_path):
        """D2: only 'vitest' imported, no config → test_tool is 'vitest' (unambiguous)."""
        import servers as s
        self._make_db_with_imports(
            s.BRAIN_DB, 'proj_d2c', ['vitest']
        )
        from servers.project import _detect_tech_stack
        result = _detect_tech_stack('proj_d2c', project_path=None)
        assert result['test_tool'] == 'vitest'

    def test_config_wins_over_ambiguous_imports(
            self, tmp_path, mock_db_path):
        """D2: config present (vitest.config.ts) overrides ambiguous imports → vitest."""
        import servers as s
        self._make_db_with_imports(
            s.BRAIN_DB, 'proj_d2d', ['jest', 'vitest']
        )
        (tmp_path / "vitest.config.ts").write_text("export default {};")
        from servers.project import _detect_tech_stack
        result = _detect_tech_stack('proj_d2d', project_path=str(tmp_path))
        assert result['test_tool'] == 'vitest'


# =============================================================================
# D3 — Malformed package.json must not skip jest.config.* detection
# =============================================================================

class TestD3MalformedPackageJson:
    """D3: A malformed package.json must not short-circuit checking jest.config.*."""

    def test_malformed_package_json_with_jest_config_js(self, tmp_path):
        """D3: malformed package.json + jest.config.js → returns 'jest'."""
        (tmp_path / "package.json").write_text("{ this is broken json !!!")
        (tmp_path / "jest.config.js").write_text("module.exports = {};")
        assert _detect_test_tool_from_config(tmp_path) == "jest"

    def test_malformed_package_json_with_jest_config_ts(self, tmp_path):
        """D3: malformed package.json + jest.config.ts → returns 'jest'."""
        (tmp_path / "package.json").write_text("null")
        (tmp_path / "jest.config.ts").write_text("export default {};")
        assert _detect_test_tool_from_config(tmp_path) == "jest"

    def test_malformed_package_json_with_jest_config_json(self, tmp_path):
        """D3: malformed package.json (non-dict) + jest.config.json → returns 'jest'."""
        (tmp_path / "package.json").write_text("[1, 2, 3]")  # valid JSON but not dict
        (tmp_path / "jest.config.json").write_text("{}")
        assert _detect_test_tool_from_config(tmp_path) == "jest"

    def test_malformed_package_json_alone_returns_none(self, tmp_path):
        """D3: malformed package.json alone (no config files) → None (no crash)."""
        (tmp_path / "package.json").write_text("{ broken }")
        assert _detect_test_tool_from_config(tmp_path) is None


# =============================================================================
# D4 — settings.gradle.kts + vite.config 'test :' whitespace tolerance
# =============================================================================

class TestD4Robustness:
    """D4: settings.gradle.kts detection + vite.config tolerant 'test:' regex."""

    def test_settings_gradle_kts_detected(self, tmp_path):
        """D4: settings.gradle.kts present → gradle."""
        (tmp_path / "settings.gradle.kts").write_text(
            "rootProject.name = \"myapp\"\n"
        )
        assert _detect_test_tool_from_config(tmp_path) == "gradle"

    def test_vite_config_test_with_space_before_colon(self, tmp_path):
        """D4: vite.config.ts with 'test :' (space before colon) → vitest."""
        (tmp_path / "vite.config.ts").write_text(
            "export default defineConfig({ test : { globals: true } });"
        )
        assert _detect_test_tool_from_config(tmp_path) == "vitest"

    def test_vite_config_test_with_quoted_key(self, tmp_path):
        """D4: vite.config.ts with '\"test\":' (quoted JSON-style) → vitest."""
        (tmp_path / "vite.config.ts").write_text(
            'export default defineConfig({ "test": { globals: true } });'
        )
        assert _detect_test_tool_from_config(tmp_path) == "vitest"

    def test_vite_config_no_test_block(self, tmp_path):
        """D4: vite.config.ts without test block → None."""
        (tmp_path / "vite.config.ts").write_text(
            "export default defineConfig({ server: { port: 3000 } });"
        )
        assert _detect_test_tool_from_config(tmp_path) is None


# =============================================================================
# K1b — dual-runner ambiguity + mocha detection
# =============================================================================

class TestK1bDualRunnerAndMocha:
    """K1b: (a) both jest+vitest in devDeps with no disambiguating config → None
            (b) mocha in deps or scripts.test → 'mocha'
    """

    def test_dual_jest_vitest_devdeps_no_config_returns_ambiguous(self, tmp_path):
        """K1b-a / M1: package.json devDeps has BOTH jest and vitest, no config → AMBIGUOUS.

        The sentinel AMBIGUOUS (not None) lets _detect_tech_stack distinguish
        "no config found" (None → keep import heuristic) from "config is contradictory"
        (AMBIGUOUS → clear the heuristic → test_tool = None → fail-closed).
        """
        from servers.project import AMBIGUOUS
        (tmp_path / "package.json").write_text(json.dumps({
            "devDependencies": {
                "jest": "^29.0.0",
                "vitest": "^1.0.0",
            }
        }))
        # No vitest.config.*, no jest.config.* — truly ambiguous
        result = _detect_test_tool_from_config(tmp_path)
        assert result == AMBIGUOUS, (
            f'M1/K1b: both jest+vitest in devDeps with no config → AMBIGUOUS, got {result!r}')

    def test_dual_deps_with_vitest_script_returns_vitest(self, tmp_path):
        """K1b-a: both jest+vitest in deps, but scripts.test says 'vitest' → 'vitest'."""
        (tmp_path / "package.json").write_text(json.dumps({
            "devDependencies": {
                "jest": "^29.0.0",
                "vitest": "^1.0.0",
            },
            "scripts": {"test": "vitest run"},
        }))
        result = _detect_test_tool_from_config(tmp_path)
        assert result == 'vitest', (
            f'K1b: vitest in scripts.test disambiguates → vitest, got {result!r}')

    def test_dual_deps_with_jest_config_returns_jest(self, tmp_path):
        """K1b-a: both jest+vitest in deps, but jest.config.js present → 'jest'."""
        (tmp_path / "package.json").write_text(json.dumps({
            "devDependencies": {
                "jest": "^29.0.0",
                "vitest": "^1.0.0",
            }
        }))
        (tmp_path / "jest.config.js").write_text("module.exports = {};")
        result = _detect_test_tool_from_config(tmp_path)
        assert result == 'jest', (
            f'K1b: jest.config.js disambiguates dual deps → jest, got {result!r}')

    def test_only_jest_devdep_still_returns_jest(self, tmp_path):
        """K1b: only jest in devDeps (no vitest) → 'jest' unchanged."""
        (tmp_path / "package.json").write_text(json.dumps({
            "devDependencies": {"jest": "^29.0.0"},
        }))
        result = _detect_test_tool_from_config(tmp_path)
        assert result == 'jest', f'Single jest dep must still return jest, got {result!r}'

    def test_only_vitest_devdep_still_returns_vitest(self, tmp_path):
        """K1b: only vitest in devDeps (no jest) → 'vitest' unchanged."""
        (tmp_path / "package.json").write_text(json.dumps({
            "devDependencies": {"vitest": "^1.0.0"},
        }))
        result = _detect_test_tool_from_config(tmp_path)
        assert result == 'vitest', f'Single vitest dep must still return vitest, got {result!r}'

    def test_mocha_in_devdeps_returns_mocha(self, tmp_path):
        """K1b-b: mocha in devDependencies → 'mocha'."""
        (tmp_path / "package.json").write_text(json.dumps({
            "devDependencies": {"mocha": "^10.0.0"},
        }))
        result = _detect_test_tool_from_config(tmp_path)
        assert result == 'mocha', f'mocha in devDeps must return mocha, got {result!r}'

    def test_mocha_in_test_script_returns_mocha(self, tmp_path):
        """K1b-b: scripts.test mentions mocha → 'mocha'."""
        (tmp_path / "package.json").write_text(json.dumps({
            "scripts": {"test": "mocha --recursive"},
        }))
        result = _detect_test_tool_from_config(tmp_path)
        assert result == 'mocha', f'mocha in scripts.test must return mocha, got {result!r}'

    def test_mocha_in_dependencies_not_devdeps_returns_mocha(self, tmp_path):
        """K1b-b: mocha in dependencies (not devDependencies) → 'mocha'."""
        (tmp_path / "package.json").write_text(json.dumps({
            "dependencies": {"mocha": "^10.0.0"},
        }))
        result = _detect_test_tool_from_config(tmp_path)
        assert result == 'mocha', f'mocha in deps must return mocha, got {result!r}'


# =============================================================================
# L1 — JS/TS language default must not guess a runner (false-green fix)
# =============================================================================

class TestL1NoJSTypeScriptLanguageDefault:
    """L1: Remove typescript and javascript from _DEFAULT_TEST_TOOLS.
    
    Without explicit runner evidence (no jest/vitest import, no config),
    a JS/TS project should default to test_tool=None (fail-closed),
    not 'jest' (false-green).
    
    Tests the language-default lookup: assert typescript/javascript
    keys are removed from _DEFAULT_TEST_TOOLS.
    """

    def test_typescript_not_in_default_tools(self):
        """typescript language must not have a default test_tool."""
        from servers.project import _DEFAULT_TEST_TOOLS
        assert 'typescript' not in _DEFAULT_TEST_TOOLS, (
            "L1 violation: typescript in _DEFAULT_TEST_TOOLS — "
            "JS/TS must fail-closed (no guess runner)"
        )

    def test_javascript_not_in_default_tools(self):
        """javascript language must not have a default test_tool."""
        from servers.project import _DEFAULT_TEST_TOOLS
        assert 'javascript' not in _DEFAULT_TEST_TOOLS, (
            "L1 violation: javascript in _DEFAULT_TEST_TOOLS — "
            "JS/TS must fail-closed (no guess runner)"
        )

    def test_other_language_defaults_intact(self):
        """python, java, rust, go must still have defaults (deterministic)."""
        from servers.project import _DEFAULT_TEST_TOOLS
        assert _DEFAULT_TEST_TOOLS.get('python') == 'pytest'
        assert _DEFAULT_TEST_TOOLS.get('java') == 'junit'
        assert _DEFAULT_TEST_TOOLS.get('rust') == 'cargo test'
        assert _DEFAULT_TEST_TOOLS.get('go') == 'go test'


# =============================================================================
# M1 — config-detected AMBIGUITY must clear the import heuristic
# =============================================================================

class TestM1AmbiguityClearsHeuristic:
    """M1: AMBIGUOUS sentinel from _detect_test_tool_from_config forces test_tool=None
    in _detect_tech_stack, even if the import heuristic found a stray 'jest' import.

    This prevents the false-green where a TypeScript project with both jest+vitest
    in devDeps (ambiguous) kept test_tool='jest' because a code import node happened
    to mention 'jest'.
    """

    def _make_db_with_imports(self, db_path, project, imports):
        import os
        schema_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'brain', 'schema.sql'
        )
        import sqlite3
        conn = sqlite3.connect(db_path)
        with open(schema_path, encoding='utf-8') as f:
            conn.executescript(f.read())
        conn.execute(
            "INSERT OR IGNORE INTO code_nodes "
            "(id, project, kind, name, language) VALUES "
            "('file.src', ?, 'file', 'src/index.ts', 'typescript')",
            (project,)
        )
        for imp in imports:
            node_id = f'import.{imp}'
            conn.execute(
                "INSERT OR IGNORE INTO code_nodes (id, project, kind, name) "
                "VALUES (?, ?, 'import', ?)",
                (node_id, project, imp)
            )
            conn.execute(
                "INSERT OR IGNORE INTO code_edges "
                "(project, from_id, to_id, kind) "
                "VALUES (?, 'file.src', ?, 'imports')",
                (project, node_id)
            )
        conn.commit()
        conn.close()

    def test_ambiguous_config_returns_ambiguous_sentinel(self, tmp_path):
        """_detect_test_tool_from_config returns AMBIGUOUS (not None) for both-deps case."""
        from servers.project import AMBIGUOUS
        (tmp_path / "package.json").write_text(json.dumps({
            "devDependencies": {
                "jest": "^29.0.0",
                "vitest": "^1.0.0",
            }
        }))
        result = _detect_test_tool_from_config(tmp_path)
        assert result == AMBIGUOUS, (
            f'M1: ambiguous config must return AMBIGUOUS sentinel, got {result!r}')

    def test_ambiguous_config_clears_jest_import_heuristic(
            self, tmp_path, mock_db_path):
        """M1 end-to-end: config=AMBIGUOUS overrides stray jest import → test_tool=None.

        Scenario: project has a stray 'jest' import node in the Code Graph
        (import heuristic says 'jest'), but package.json has both jest+vitest deps
        with no disambiguating config → AMBIGUOUS → final test_tool must be None
        (fail-closed; gate will reject rather than run jest on an undetermined project).
        """
        import servers as s
        from servers.project import _detect_tech_stack
        # Insert a stray 'jest' import into the graph (heuristic would say jest)
        self._make_db_with_imports(s.BRAIN_DB, 'proj_m1a', ['jest'])
        # Ambiguous package.json: both jest+vitest, no disambiguating config
        (tmp_path / "package.json").write_text(json.dumps({
            "devDependencies": {
                "jest": "^29.0.0",
                "vitest": "^1.0.0",
            }
        }))
        result = _detect_tech_stack('proj_m1a', project_path=str(tmp_path))
        assert result['test_tool'] is None, (
            f'M1: AMBIGUOUS config must clear jest heuristic → test_tool=None, got {result["test_tool"]!r}')

    def test_no_config_preserves_jest_import_heuristic(
            self, tmp_path, mock_db_path):
        """M1 contrast: config=None (no config files) keeps the jest import heuristic.

        When there are NO config files (config_tool=None), the import heuristic
        is preserved — 'jest' import → test_tool='jest'. Only AMBIGUOUS clears it.
        """
        import servers as s
        from servers.project import _detect_tech_stack
        # Stray 'jest' import — heuristic says jest
        self._make_db_with_imports(s.BRAIN_DB, 'proj_m1b', ['jest'])
        # Empty tmp_path: no config files → config_tool=None → heuristic preserved
        result = _detect_tech_stack('proj_m1b', project_path=str(tmp_path))
        assert result['test_tool'] == 'jest', (
            f'M1: None config must NOT clear jest heuristic → test_tool=jest, got {result["test_tool"]!r}')

    def test_concrete_config_tool_still_overrides_heuristic(
            self, tmp_path, mock_db_path):
        """M1: concrete config result ('vitest') still overrides any import heuristic."""
        import servers as s
        from servers.project import _detect_tech_stack
        # Stray 'jest' import — heuristic says jest
        self._make_db_with_imports(s.BRAIN_DB, 'proj_m1c', ['jest'])
        # vitest.config.ts → concrete config → must override to vitest
        (tmp_path / "vitest.config.ts").write_text("export default {};")
        result = _detect_tech_stack('proj_m1c', project_path=str(tmp_path))
        assert result['test_tool'] == 'vitest', (
            f'Concrete config vitest must override jest heuristic, got {result["test_tool"]!r}')
