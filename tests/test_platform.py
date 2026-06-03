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

    def test_unsupported_platform_returns_minus_one(self):
        from servers import platform as plat
        # cursor 沒有 supports_commands → -1
        assert plat.setup_commands(platform_key="cursor", base_dir="/x") == -1
