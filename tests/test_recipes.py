"""Recipe 任務樹建立測試"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _seed_files(db_path, project, files):
    import sqlite3
    conn = sqlite3.connect(db_path)
    for i, fp in enumerate(files):
        conn.execute("""INSERT INTO code_nodes
            (id, project, kind, name, file_path, line_start, line_end, language)
            VALUES (?,?,?,?,?,?,?,?)""",
            (f"file.{fp}", project, "file", os.path.basename(fp), fp, 1, 50, "python"))
    conn.commit()
    conn.close()


class TestRecipeCodeReview:
    def test_builds_tasks_from_target_path(self, mock_db_path, monkeypatch, tmp_path):
        _seed_files(mock_db_path, "cr",
                    ["servers/foo.py", "servers/bar.py", "tests/test_foo.py"])
        from servers import recipes
        monkeypatch.setattr(recipes, "_ensure_synced",
                            lambda p, path: {"test_tool": "pytest"})
        result = recipes.recipe_code_review(
            "cr", str(tmp_path), target_path="servers/")
        assert result["epic_id"] is not None
        assert result["task_count"] == 2  # 跳過 tests/ 下檔案

    def test_no_target_no_git_returns_zero(self, mock_db_path, monkeypatch, tmp_path):
        from servers import recipes
        monkeypatch.setattr(recipes, "_ensure_synced",
                            lambda p, path: {"test_tool": "pytest"})
        # tmp_path 非 git repo，且未給 target_path
        result = recipes.recipe_code_review("cr2", str(tmp_path))
        assert result["task_count"] == 0
        assert "target_path" in result["message"]

    def test_target_path_no_sibling_false_match(self, mock_db_path, monkeypatch, tmp_path):
        _seed_files(mock_db_path, "cr3",
                    ["servers/foo.py", "servers_other/bar.py"])
        from servers import recipes
        monkeypatch.setattr(recipes, "_ensure_synced",
                            lambda p, path: {"test_tool": "pytest"})
        result = recipes.recipe_code_review("cr3", str(tmp_path), target_path="servers/")
        assert result["task_count"] == 1  # 只 servers/，不含 servers_other/


class TestRecipeIntegrationTests:
    def test_groups_by_module(self, mock_db_path, monkeypatch, tmp_path):
        _seed_files(mock_db_path, "it",
                    ["servers/auth/login.py", "servers/auth/token.py",
                     "servers/user/profile.py"])
        from servers import recipes
        monkeypatch.setattr(recipes, "_ensure_synced",
                            lambda p, path: {"test_tool": "pytest"})
        result = recipes.recipe_integration_tests(
            "it", str(tmp_path), target_path="servers/")
        assert result["epic_id"] is not None
        # auth 與 user 兩個模組 → 2 個 story
        assert result["story_count"] == 2

    def test_story_count_respects_max_tasks(self, mock_db_path, monkeypatch, tmp_path):
        _seed_files(mock_db_path, "it3",
                    ["a/x.py", "b/y.py", "c/z.py"])
        from servers import recipes
        monkeypatch.setattr(recipes, "_ensure_synced",
                            lambda p, path: {"test_tool": "pytest"})
        result = recipes.recipe_integration_tests(
            "it3", str(tmp_path), target_path=None, max_tasks=2)
        assert result["task_count"] == 2
        assert result["story_count"] == 2  # 與實際建立的 story 數一致
        assert len(result["modules"]) == 2          # 只回報實際建立的模組
        assert "across 2 modules" in result["message"]

    def test_task_description_classifiable(self, mock_db_path, monkeypatch, tmp_path):
        _seed_files(mock_db_path, "it2", ["servers/auth/login.py"])
        from servers import recipes
        from servers.playbooks import resolve_playbook
        monkeypatch.setattr(recipes, "_ensure_synced",
                            lambda p, path: {"test_tool": "pytest"})
        recipes.recipe_integration_tests("it2", str(tmp_path), target_path="servers/")
        # 任務描述須能被 playbook 分類為 integration_test（閉環驗證）
        pb = resolve_playbook("Write integration tests for module servers/auth")
        assert pb is not None and pb.name == "integration_test"


class TestRecipeRegistry:
    def test_all_three_registered(self):
        from servers.recipes import RECIPES
        assert set(RECIPES.keys()) >= {"unit_tests", "code_review", "integration_tests"}

    def test_run_recipe_dispatches_code_review(self, mock_db_path, monkeypatch, tmp_path):
        from servers import recipes
        monkeypatch.setattr(recipes, "_ensure_synced",
                            lambda p, path: {"test_tool": "pytest"})
        result = recipes.run_recipe("code_review", project_name="rr",
                                    project_path=str(tmp_path), target_path="servers/")
        assert "task_count" in result


class TestRecipeHardening:
    def test_is_test_file_precision(self):
        from servers.recipes import is_test_file
        assert is_test_file("tests/test_foo.py")
        assert is_test_file("servers/test_bar.py")
        assert is_test_file("a/foo_test.py")
        assert is_test_file("a/foo.spec.ts")
        # 不應誤判：
        assert not is_test_file("servers/contest.py")
        assert not is_test_file("servers/latest_report.py")
        assert not is_test_file("servers/attestation.py")

    def test_git_diff_base_rejects_option(self, monkeypatch):
        from servers import recipes
        # 直接測 helper：以 '-' 開頭的 diff_base 必須被拒，且「不得呼叫 subprocess」
        called = {"ran": False}
        def fake_run(*a, **k):
            called["ran"] = True
            raise AssertionError("subprocess should NOT run for an option-like diff_base")
        monkeypatch.setattr(recipes.subprocess, "run", fake_run)
        assert recipes._git_changed_files("/some/repo", "--output=/tmp/x") is None
        assert recipes._git_changed_files("/some/repo", "-anything") is None
        assert called["ran"] is False
        # 對照組：正常 base 會實際呼叫 subprocess（用另一個 fake 確認有被呼叫）
        ran2 = {"ran": False}
        class R:
            returncode = 0
            stdout = "a.py\n"
        def fake_run2(*a, **k):
            ran2["ran"] = True
            return R()
        monkeypatch.setattr(recipes.subprocess, "run", fake_run2)
        assert recipes._git_changed_files("/some/repo", "HEAD") == ["a.py"]
        assert ran2["ran"] is True

    def test_single_file_target(self, mock_db_path, monkeypatch, tmp_path):
        # _seed_files defined at top of this test module
        _seed_files(mock_db_path, "h2", ["servers/foo.py", "servers/bar.py"])
        from servers import recipes
        monkeypatch.setattr(recipes, "_ensure_synced", lambda p, path: {"test_tool": "pytest"})
        result = recipes.recipe_code_review("h2", str(tmp_path), target_path="servers/foo.py")
        assert result["task_count"] == 1

    def test_max_tasks_zero_no_epic(self, mock_db_path, monkeypatch, tmp_path):
        _seed_files(mock_db_path, "h3", ["servers/foo.py"])
        from servers import recipes
        monkeypatch.setattr(recipes, "_ensure_synced", lambda p, path: {"test_tool": "pytest"})
        result = recipes.recipe_code_review("h3", str(tmp_path), target_path="servers/", max_tasks=0)
        assert result["epic_id"] is None
        assert result["task_count"] == 0

    def test_contest_file_not_excluded(self, mock_db_path, monkeypatch, tmp_path):
        _seed_files(mock_db_path, "h4", ["servers/contest.py"])
        from servers import recipes
        monkeypatch.setattr(recipes, "_ensure_synced", lambda p, path: {"test_tool": "pytest"})
        result = recipes.recipe_code_review("h4", str(tmp_path), target_path="servers/")
        assert result["task_count"] == 1  # contest.py 不是測試檔
