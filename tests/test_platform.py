"""platform.setup_commands —— slash 指令自動安裝測試"""
import os
import sys

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BASE)


class TestSetupCommands:
    def test_installs_and_substitutes_han_dir(self, tmp_path, monkeypatch):
        from servers import platform as plat
        cmds = tmp_path / "commands"
        monkeypatch.setattr(plat, "get_commands_dir", lambda *a, **k: str(cmds))

        written = plat.setup_commands(platform_key="claude", base_dir=_BASE)
        han_dir = cmds / "han"
        assert written >= 4
        for name in ("unit-test.md", "integration-test.md", "e2e.md", "review.md"):
            assert (han_dir / name).exists(), f"missing {name}"

        content = (han_dir / "unit-test.md").read_text(encoding="utf-8")
        assert "{{HAN_DIR}}" not in content      # 佔位符已替換
        assert _BASE in content                  # 換成真實 han 路徑

    def test_all_command_files_render_safely(self, tmp_path, monkeypatch):
        """所有指令檔 render 後不得殘留 {{HAN_DIR}}，且每個 sys.path.insert 行可被 compile。"""
        from servers import platform as plat
        cmds = tmp_path / "commands"
        monkeypatch.setattr(plat, "get_commands_dir", lambda *a, **k: str(cmds))
        plat.setup_commands(platform_key="claude", base_dir=_BASE)

        han_dir = cmds / "han"
        md_files = list(han_dir.glob("*.md"))
        assert len(md_files) >= 5  # 至少含本批新指令
        for md in md_files:
            content = md.read_text(encoding="utf-8")
            assert "{{HAN_DIR}}" not in content, f"{md.name} 仍有未替換的佔位符"
            for line in content.splitlines():
                if "sys.path.insert" in line:
                    compile(line.strip(), f"<{md.name}>", "exec")

    def test_idempotent(self, tmp_path, monkeypatch):
        from servers import platform as plat
        cmds = tmp_path / "commands"
        monkeypatch.setattr(plat, "get_commands_dir", lambda *a, **k: str(cmds))
        plat.setup_commands(platform_key="claude", base_dir=_BASE)
        # 第二次不應再寫入（內容相同）
        assert plat.setup_commands(platform_key="claude", base_dir=_BASE) == 0

    def test_overwrites_stale_content(self, tmp_path, monkeypatch):
        from servers import platform as plat
        cmds = tmp_path / "commands"
        monkeypatch.setattr(plat, "get_commands_dir", lambda *a, **k: str(cmds))
        han_dir = cmds / "han"
        han_dir.mkdir(parents=True)
        (han_dir / "unit-test.md").write_text("STALE", encoding="utf-8")
        # 既有檔內容過時 → 應被重新渲染覆蓋
        written = plat.setup_commands(platform_key="claude", base_dir=_BASE)
        assert written >= 1
        content = (han_dir / "unit-test.md").read_text(encoding="utf-8")
        assert "STALE" not in content and _BASE in content

    def test_impact_command_uses_real_dep_fields(self):
        """impact.md 必須用 get_code_dependencies 實際欄位（relation/direction），非錯誤的 edge_kind/to_id。"""
        content = open(os.path.join(_BASE, "commands", "han", "impact.md"), encoding="utf-8").read()
        assert "direction" in content and "relation" in content
        assert "edge_kind" not in content and "to_id" not in content

    def test_get_code_dependencies_contract(self, sample_code_graph):
        """鎖定 get_code_dependencies 的回傳鍵，避免指令本文與 API 漂移。"""
        from servers.code_graph import get_code_dependencies
        deps = get_code_dependencies(
            "test", "func.src/auth/login.py:authenticate", depth=1, direction="both")
        assert deps, "expected at least one dependency edge from sample graph"
        for d in deps:
            assert {"id", "relation", "direction"}.issubset(d.keys())

    def test_unsupported_platform_returns_minus_one(self):
        from servers import platform as plat
        # cursor 沒有 supports_commands → -1
        assert plat.setup_commands(platform_key="cursor", base_dir="/x") == -1

    def test_han_dir_rendered_as_safe_json_literal(self, tmp_path, monkeypatch):
        """Critical: 安裝路徑含空白/引號時，{{HAN_DIR}} 必須以安全 JSON 字面量嵌入。"""
        import json
        from servers import platform as plat
        cmds = tmp_path / "commands"
        monkeypatch.setattr(plat, "get_commands_dir", lambda *a, **k: str(cmds))

        tricky = str(tmp_path / 'we"ird dir')
        os.makedirs(os.path.join(tricky, "commands", "han"), exist_ok=True)
        with open(os.path.join(tricky, "commands", "han", "x.md"), "w", encoding="utf-8") as f:
            f.write("sys.path.insert(0, {{HAN_DIR}})\n")

        plat.setup_commands(platform_key="claude", base_dir=tricky)
        content = (cmds / "han" / "x.md").read_text(encoding="utf-8")
        assert "{{HAN_DIR}}" not in content
        assert json.dumps(os.path.abspath(tricky)) in content
        # 嵌入的字面量可被 Python 安全解析（不會因引號破壞語法）
        line = next(l for l in content.splitlines() if "sys.path.insert" in l)
        compile(line, "<rendered>", "exec")
