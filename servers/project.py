"""
HAN System - 專案初始化（Lazy）
冪等的專案初始化邏輯，首次使用時自動觸發。
專案理解存 DB + Code Graph，不在專案目錄建檔案。
"""

import os
import json
import re
from pathlib import Path
from typing import Optional

from servers import managed_connection


# 常見框架偵測規則：import name → (framework, test_tool)
# NOTE: framework-only imports (react, next, vue, etc.) carry test_tool=None.
# Only actual test-runner imports (jest, vitest, mocha, pytest, org.junit) set test_tool.
_FRAMEWORK_HINTS = {
    # Python
    'fastapi': ('FastAPI', 'pytest'),
    'flask': ('Flask', 'pytest'),
    'django': ('Django', 'pytest'),
    'pytest': (None, 'pytest'),
    'unittest': (None, 'pytest'),
    # TypeScript / JavaScript — framework hints carry NO test_tool (D1)
    'react': ('React', None),
    'next': ('Next.js', None),
    'vue': ('Vue', None),
    'nuxt': ('Nuxt', None),
    'express': ('Express', None),
    # Test-runner imports carry test_tool (these are the only JS test-tool signals)
    'jest': (None, 'jest'),
    'vitest': (None, 'vitest'),
    'mocha': (None, 'mocha'),
    # Java
    'org.springframework': ('Spring Boot', 'junit'),
    'org.junit': (None, 'junit'),
    'org.mockito': (None, 'junit'),
    # Rust
    'tokio': ('Tokio', 'cargo test'),
    'actix': ('Actix', 'cargo test'),
    # Go
    'net/http': ('net/http', 'go test'),
    'gin': ('Gin', 'go test'),
}

# 語言 → 預設測試工具
# L1 fix: removed typescript/javascript (fail-closed: JS/TS must not guess 'jest')
# Only deterministic language defaults (python, java, rust, go)
_DEFAULT_TEST_TOOLS = {
    'python': 'pytest',
    'java': 'junit',
    'rust': 'cargo test',
    'go': 'go test',
}

# Sentinel returned by _detect_test_tool_from_config when config signals conflict
# (e.g. both jest AND vitest in devDeps with no disambiguating config file).
# Distinct from None (= no config signal at all) so _detect_tech_stack can
# force test_tool=None (fail-closed) instead of falling back to import heuristic.
AMBIGUOUS = '__ambiguous__'

# Regex for vite.config 'test:' block detection — tolerates spaces and quoted keys (D4)
_VITE_TEST_RE = re.compile(r'["\']?test["\']?\s*:')


def _detect_test_tool_from_config(project_path) -> Optional[str]:
    """Read project config files to determine the test runner.

    Detection rules (first match wins, higher priority first):
    1. vitest.config.{ts,js,mts,mjs,cts,cjs}  → 'vitest'
    2. vite.config.* containing test block      → 'vitest'
    3. package.json devDeps/deps containing vitest OR scripts.test mentioning vitest → 'vitest'
    4. jest.config.{js,ts,cjs,mjs,json}        → 'jest'
    5. package.json top-level 'jest' key OR devDeps 'jest' OR scripts.test 'jest' → 'jest'
    6. pyproject.toml with [tool.pytest         → 'pytest'
    7. pytest.ini                               → 'pytest'
    8. tox.ini with [pytest]                    → 'pytest'
    9. setup.cfg with [tool:pytest]             → 'pytest'
    10. pom.xml                                 → 'maven'
    11. build.gradle / build.gradle.kts / settings.gradle / settings.gradle.kts → 'gradle'
    12. Cargo.toml                              → 'cargo test'
    13. go.mod                                  → 'go test'

    Vitest always beats Jest when both are present (explicit vitest config is
    stronger signal than a jest dependency).

    Returns:
        Tool name string, or None if no config matched.
        Never raises — malformed/missing files are silently skipped.
    """
    if project_path is None:
        return None

    root = Path(project_path)

    # ------------------------------------------------------------------
    # 1. vitest.config.* (explicit vitest config — strongest signal)
    # ------------------------------------------------------------------
    _vitest_config_exts = ('ts', 'js', 'mts', 'mjs', 'cts', 'cjs')
    for ext in _vitest_config_exts:
        if (root / f"vitest.config.{ext}").exists():
            return 'vitest'

    # ------------------------------------------------------------------
    # 2. vite.config.* containing a test block (D4: regex-based, tolerates spaces)
    # ------------------------------------------------------------------
    for vite_cfg in root.glob("vite.config.*"):
        try:
            if _VITE_TEST_RE.search(vite_cfg.read_text(encoding='utf-8', errors='replace')):
                return 'vitest'
        except OSError:
            pass

    # ------------------------------------------------------------------
    # 3 & 5. package.json — parse once, check vitest first then jest.
    # D3: if package.json is malformed/non-dict, fall through to config-file checks.
    # ------------------------------------------------------------------
    pkg_path = root / "package.json"
    pkg_parsed = False   # True only when package.json gave a usable result
    if pkg_path.exists():
        try:
            pkg = json.loads(pkg_path.read_text(encoding='utf-8', errors='replace'))
        except (json.JSONDecodeError, OSError, ValueError):
            pkg = None

        if pkg is not None and isinstance(pkg, dict):
            pkg_parsed = True
            # Collect all dependency keys (devDeps + deps)
            all_deps = set()
            for section in ('devDependencies', 'dependencies'):
                section_data = pkg.get(section)
                if isinstance(section_data, dict):
                    all_deps.update(section_data.keys())

            test_script = ''
            scripts = pkg.get('scripts')
            if isinstance(scripts, dict):
                test_script = scripts.get('test', '') or ''

            # K1b: detect mocha first (before vitest/jest checks) so it is
            # consistently returned rather than falling through.
            if (
                'mocha' in all_deps
                or 'mocha' in test_script
            ):
                return 'mocha'

            # K1b dual-runner guard: if BOTH jest and vitest appear in deps with
            # no disambiguating explicit signal, return None (ambiguous) rather
            # than silently picking vitest.
            _vitest_dep = 'vitest' in all_deps
            _jest_dep = 'jest' in all_deps or 'jest' in pkg
            _vitest_script = 'vitest' in test_script
            _jest_script = 'jest' in test_script

            if _vitest_dep and _jest_dep:
                # Both in deps: only disambiguate via explicit test_script signal
                # (vitest.config.* / vite.config.* were already handled above)
                if _vitest_script and not _jest_script:
                    return 'vitest'
                if _jest_script and not _vitest_script:
                    # also check jest.config.* as it beats a bare jest dep
                    _jest_config_exts = ('js', 'ts', 'cjs', 'mjs', 'json')
                    for ext in _jest_config_exts:
                        if (root / f"jest.config.{ext}").exists():
                            return 'jest'
                    return 'jest'
                # Neither script signal nor config file → truly ambiguous → AMBIGUOUS
                _jest_config_exts = ('js', 'ts', 'cjs', 'mjs', 'json')
                for ext in _jest_config_exts:
                    if (root / f"jest.config.{ext}").exists():
                        return 'jest'
                # Still ambiguous — return AMBIGUOUS (caller clears heuristic; M1)
                return AMBIGUOUS

            # Check vitest signals first (vitest beats jest when only one is present)
            if _vitest_dep or _vitest_script:
                return 'vitest'

            # jest.config.* (explicit config files for jest)
            _jest_config_exts = ('js', 'ts', 'cjs', 'mjs', 'json')
            for ext in _jest_config_exts:
                if (root / f"jest.config.{ext}").exists():
                    return 'jest'

            # Jest signals from package.json
            if (
                'jest' in pkg                   # top-level jest key
                or _jest_dep
                or _jest_script
            ):
                return 'jest'

            # package.json existed but gave no signal — fall through

    # D3: whether package.json was absent, malformed, or gave no signal,
    # always check jest.config.* files as a standalone fallback.
    if not pkg_parsed:
        _jest_config_exts = ('js', 'ts', 'cjs', 'mjs', 'json')
        for ext in _jest_config_exts:
            if (root / f"jest.config.{ext}").exists():
                return 'jest'

    # ------------------------------------------------------------------
    # 6. pyproject.toml containing [tool.pytest
    # ------------------------------------------------------------------
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        try:
            content = pyproject.read_text(encoding='utf-8', errors='replace')
            if '[tool.pytest' in content:
                return 'pytest'
        except OSError:
            pass

    # ------------------------------------------------------------------
    # 7. pytest.ini
    # ------------------------------------------------------------------
    if (root / "pytest.ini").exists():
        return 'pytest'

    # ------------------------------------------------------------------
    # 8. tox.ini with [pytest] section
    # ------------------------------------------------------------------
    tox_ini = root / "tox.ini"
    if tox_ini.exists():
        try:
            content = tox_ini.read_text(encoding='utf-8', errors='replace')
            if '[pytest]' in content:
                return 'pytest'
        except OSError:
            pass

    # ------------------------------------------------------------------
    # 9. setup.cfg with [tool:pytest] section
    # ------------------------------------------------------------------
    setup_cfg = root / "setup.cfg"
    if setup_cfg.exists():
        try:
            content = setup_cfg.read_text(encoding='utf-8', errors='replace')
            if '[tool:pytest]' in content:
                return 'pytest'
        except OSError:
            pass

    # ------------------------------------------------------------------
    # 10. pom.xml → maven
    # ------------------------------------------------------------------
    if (root / "pom.xml").exists():
        return 'maven'

    # ------------------------------------------------------------------
    # 11. Gradle → gradle (D4: includes settings.gradle.kts)
    # ------------------------------------------------------------------
    for gradle_file in ('build.gradle', 'build.gradle.kts',
                        'settings.gradle', 'settings.gradle.kts'):
        if (root / gradle_file).exists():
            return 'gradle'

    # ------------------------------------------------------------------
    # 12. Cargo.toml → cargo test
    # ------------------------------------------------------------------
    if (root / "Cargo.toml").exists():
        return 'cargo test'

    # ------------------------------------------------------------------
    # 13. go.mod → go test
    # ------------------------------------------------------------------
    if (root / "go.mod").exists():
        return 'go test'

    return None


def _detect_tech_stack(project_name, project_path=None):
    """從 Code Graph 偵測專案技術棧

    Args:
        project_name: 專案名稱（用於查詢 Code Graph）
        project_path: 專案根目錄（若提供則執行 config-file pass 並以其結果覆蓋
                      import heuristic；預設 None 表示跳過 config-file pass）

    Returns:
        {
            'languages': {'python': 42, 'typescript': 18, ...},
            'primary_language': 'python',
            'frameworks': ['FastAPI', 'React'],
            'test_tool': 'pytest',
        }
    """
    result = {
        'languages': {},
        'primary_language': None,
        'frameworks': [],
        'test_tool': None,
    }

    with managed_connection(row_factory=True) as conn:
        # 1. 語言分布
        cursor = conn.execute(
            "SELECT language, COUNT(*) as cnt FROM code_nodes "
            "WHERE project = ? AND language IS NOT NULL "
            "GROUP BY language ORDER BY cnt DESC",
            (project_name,)
        )
        for row in cursor.fetchall():
            result['languages'][row['language']] = row['cnt']

        if result['languages']:
            result['primary_language'] = next(iter(result['languages']))

        # 2. 從 import edges 偵測框架
        cursor = conn.execute(
            "SELECT DISTINCT cn.name FROM code_edges ce "
            "JOIN code_nodes cn ON ce.to_id = cn.id AND ce.project = cn.project "
            "WHERE ce.project = ? AND ce.kind = 'imports'",
            (project_name,)
        )
        import_names = {row['name'].lower() for row in cursor.fetchall()}

        frameworks = set()
        test_tools = set()
        for hint_key, (framework, test_tool) in _FRAMEWORK_HINTS.items():
            if any(hint_key in name for name in import_names):
                if framework:
                    frameworks.add(framework)
                if test_tool:
                    test_tools.add(test_tool)

        result['frameworks'] = sorted(frameworks)

        # 3. 決定測試工具：偵測到的 > 語言預設
        # D2: if import heuristic yields >1 distinct test_tool and no config, leave None
        # (ambiguous; better None than silently picking jest via sorted()[0])
        if len(test_tools) == 1:
            result['test_tool'] = next(iter(test_tools))
        elif len(test_tools) == 0 and result['primary_language']:
            result['test_tool'] = _DEFAULT_TEST_TOOLS.get(
                result['primary_language']
            )
        # else: >1 test_tools → leave test_tool = None (ambiguous)

    # 4. Config-file pass overrides import heuristic (if project_path given).
    # Three distinct outcomes (M1):
    #   concrete tool → use it (override heuristic)
    #   AMBIGUOUS     → force None (clear heuristic; ambiguous config = fail-closed)
    #   None          → no config signal; leave heuristic / language-default in place
    config_tool = _detect_test_tool_from_config(project_path)
    if config_tool == AMBIGUOUS:
        result['test_tool'] = None          # M1: ambiguous config clears import heuristic
    elif config_tool is not None:
        result['test_tool'] = config_tool

    return result


def ensure_project(project_name, project_path=None):
    """冪等的專案初始化：sync Code Graph + 偵測技術棧 + 存 DB

    首次對專案操作時自動呼叫，已初始化的專案直接返回快取。

    Args:
        project_name: 專案名稱
        project_path: 專案根目錄（預設 cwd）

    Returns:
        {
            'sync_result': {...},
            'tech_stack': {...},
            'already_initialized': bool,
        }
    """
    if project_path is None:
        project_path = os.getcwd()

    result = {
        'sync_result': None,
        'tech_stack': None,
        'already_initialized': False,
    }

    # 檢查是否已初始化
    with managed_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT content FROM long_term_memory "
            "WHERE project = ? AND title = 'Tech Stack' "
            "ORDER BY created_at DESC LIMIT 1",
            (project_name,)
        )
        row = cursor.fetchone()
        if row and row[0]:
            try:
                result['tech_stack'] = json.loads(row[0])
                result['already_initialized'] = True
            except (json.JSONDecodeError, TypeError):
                pass

    # 1. Sync Code Graph（incremental，已有的很快）
    from servers.facade import sync
    result['sync_result'] = sync(project_path, project_name, incremental=True)

    # 2. 偵測技術棧（thread project_path so config-file pass runs）
    tech_stack = _detect_tech_stack(project_name, project_path=project_path)
    result['tech_stack'] = tech_stack

    # 3. 存 DB（真正的 upsert：更新已有 Tech Stack 或建新的）
    tech_stack_json = json.dumps(tech_stack, ensure_ascii=False)
    with managed_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, content FROM long_term_memory "
            "WHERE project = ? AND category = 'knowledge' AND title = 'Tech Stack' "
            "AND status = 'active' "
            "ORDER BY created_at DESC",
            (project_name,)
        )
        rows = cursor.fetchall()
        if rows:
            current_id = rows[0][0]
            # 只在內容有變化時才更新
            if rows[0][1] != tech_stack_json:
                cursor.execute(
                    "UPDATE long_term_memory "
                    "SET content = ?, importance = 8, updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = ?",
                    (tech_stack_json, current_id)
                )
            # 清理重複記錄
            if len(rows) > 1:
                cursor.executemany(
                    "UPDATE long_term_memory "
                    "SET status = 'superseded', superseded_by = ? "
                    "WHERE id = ?",
                    [(current_id, row[0]) for row in rows[1:]]
                )
        else:
            cursor.execute(
                "INSERT INTO long_term_memory "
                "(category, project, title, content, importance) "
                "VALUES ('knowledge', ?, 'Tech Stack', ?, 8)",
                (project_name, tech_stack_json)
            )
        conn.commit()

    # 4. 寫初始化 episode（只寫一次）
    if not result['already_initialized']:
        with managed_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO episodes (project, event_type, summary) "
                "VALUES (?, 'milestone', ?)",
                (project_name, f'專案 {project_name} 初始化')
            )
            conn.commit()

    return result
