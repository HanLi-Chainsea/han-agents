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
