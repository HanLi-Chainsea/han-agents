"""
Tests for _detect_test_tool_from_config — config-file-based test runner detection.
TDD: write tests first, watch them fail, then implement.
"""

import json
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
