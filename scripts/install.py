#!/usr/bin/env python3
"""
HAN System - 安裝腳本（Optional）

Zero-config 模式下不需要手動執行此腳本。
資料庫、agents、hooks 會在首次使用時自動設定。

此腳本適用於：
- CI/CD 環境預先安裝
- 非互動式批次設定（--skip-prompts）
- 資料庫重置（--reset）
- 可選的 CLAUDE.md / SSOT / Code Graph 設定

支援平台：
- Claude Code: ~/.claude/skills/, ~/.claude/agents/
- Cursor: ~/.cursor/skills/, .cursor/agents/
- 其他平台: 只安裝 Skills，無 agents 目錄
"""

import os
import sqlite3
import sys

# Windows console encoding fix
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import json

# 讓 scripts/ 能 import servers/
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BASE_DIR)

from servers import HAN_BASE_DIR, BRAIN_DB, SCHEMA_PATH, ensure_db
from servers.platform import (
    PLATFORMS, detect_platform, get_agents_dir,
    setup_agents, setup_hooks, auto_setup,
)


def check_dependencies(base_dir):
    """檢查系統依賴"""
    errors = []
    warnings = []

    if sys.version_info < (3, 8):
        errors.append(f"Python 3.8+ 必須，目前版本: {sys.version}")

    try:
        conn = sqlite3.connect(':memory:')
        conn.execute('SELECT 1')
        conn.close()
    except Exception as e:
        errors.append(f"sqlite3 模組無法使用: {e}")

    skills_dir = os.path.dirname(base_dir)
    if os.path.exists(skills_dir):
        if not os.access(skills_dir, os.W_OK):
            errors.append(f"無寫入權限: {skills_dir}")
    else:
        try:
            os.makedirs(skills_dir, exist_ok=True)
        except Exception as e:
            errors.append(f"無法建立目錄 {skills_dir}: {e}")

    if errors:
        print("❌ 依賴檢查失敗:")
        for e in errors:
            print(f"   - {e}")
        print("\n請先解決上述問題再重新執行安裝。")
        sys.exit(1)

    if warnings:
        print("⚠️  警告:")
        for w in warnings:
            print(f"   - {w}")

    print("✅ 依賴檢查通過")
    return True


def install():
    """安裝 HAN-Agents（委派給 servers.platform.auto_setup）"""
    platform_key, base_dir = detect_platform()
    platform_config = PLATFORMS.get(platform_key, {})
    platform_name = platform_config.get('name', '未知平台')

    print("🧠 安裝 HAN-Agents")
    print("=" * 50)
    print(f"📍 偵測到平台: {platform_name}")
    print(f"📁 安裝路徑: {base_dir}")

    # 0. 依賴檢查
    check_dependencies(base_dir)

    # 1. 一鍵設定（DB + agents + hooks）
    result = auto_setup(base_dir)

    agents_copied = result['agents_copied']
    if agents_copied >= 0:
        agents_dir = get_agents_dir(platform_key, base_dir)
        print(f"✅ 安裝 {agents_copied} 個 agent 定義到 {agents_dir}")
    else:
        print(f"ℹ️  {platform_name} 不支援獨立 agents 目錄，跳過 agent 複製")

    print(f"✅ 資料庫已就緒: {BRAIN_DB}")

    if result['hooks_set']:
        print(f"✅ Claude Code Hook 設定完成")
    else:
        print(f"ℹ️  {platform_name} 不支援 Hooks，跳過 Hook 設定")

    commands_installed = result.get('commands_installed', -1)
    if commands_installed >= 0:
        print(f"✅ 安裝 {commands_installed} 個 slash 指令到 ~/.claude/commands/han/"
              f"（/han:unit-test、/han:integration-test、/han:e2e、/han:review）")
    else:
        print(f"ℹ️  {platform_name} 不支援 slash 指令，跳過")

    # 2. 完成
    print("\n" + "=" * 50)
    print("🎉 安裝完成！")
    print(f"\n平台: {platform_name}")
    print("\n可用 Agents:")
    print("  pfc            - 任務規劃、分解子任務")
    print("  executor       - 執行單一任務")
    print("  critic         - 驗證結果品質")
    print("  memory         - 記憶管理")
    print("  researcher     - 資訊收集")
    print("  drift-detector - 檢測 SSOT 與 Code 偏差")

    if platform_key == 'claude':
        print("\n使用方式:")
        print("  對 Claude Code 說：「使用 pfc agent 規劃 [任務描述]」")
    elif platform_key == 'cursor':
        print("\n使用方式:")
        print("  使用 Cursor 的 subagent 功能調用 agent")
    else:
        print("\n使用方式:")
        print("  透過 SKILL.md 中定義的 API 呼叫各項功能")

    return base_dir, platform_key


def ask_add_to_claude_md(base_dir, auto_confirm=False):
    """詢問是否將 PFC 系統設定加入專案的 CLAUDE.md"""
    print("\n" + "=" * 50)

    cwd = os.getcwd()
    claude_md_path = os.path.join(cwd, 'CLAUDE.md')

    if not auto_confirm:
        response = input("是否要將 PFC 系統設定加到當前專案的 CLAUDE.md？(y/n): ").strip().lower()
        if response != 'y':
            print(f"跳過。如需手動加入，請參考：{os.path.join(base_dir, 'README.md')}")
            return
    else:
        print("自動加入 CLAUDE.md 設定...")

    pfc_config = '''
## HAN Multi-Agent 系統

> **本專案使用 HAN Multi-Agent 系統進行任務管理**
>
> 完整協作指南：`~/.claude/skills/han-agents/SYSTEM_GUIDE.md`

### ⚠️ 使用規則

**一般任務**：Claude Code 可直接執行，不需派發 agent。

**使用 PFC 系統時**（複雜多步驟任務、用戶明確要求）：

1. **必須透過 Task tool 派發 agent** - Claude Code 是「調度者」，不是「執行者」
2. **完整執行循環**：
   - 派發 `pfc` agent 規劃任務
   - 派發 `executor` agent 執行子任務
   - 派發 `critic` agent 驗證結果
   - 派發 `memory` agent 存經驗
3. **auto-compact 後必須檢查任務進度** - 讀取 DB 恢復狀態

**禁止行為（使用 PFC 時）：**
- ❌ 直接用 Bash 執行本應由 Executor 做的檔案操作/程式碼修改
- ❌ 自己扮演 PFC 規劃而不派發 Task tool
- ❌ 跳過 Critic 驗證直接完成任務

**Agent 限制：**
- ❌ Executor 禁止執行 `git commit` / `git push` - 由 Claude Code 主體審核後提交
- ❌ Agent 不得覆蓋人工編排的文檔，除非明確指示

### 可用 Agents

| Agent | subagent_type | 用途 |
|-------|---------------|------|
| PFC | `pfc` | 任務規劃、協調 |
| Executor | `executor` | 執行單一任務 |
| Critic | `critic` | 驗證結果 |
| Memory | `memory` | 知識管理 |
| Researcher | `researcher` | 資訊收集 |
| Drift Detector | `drift-detector` | 檢測 SSOT 與 Code 偏差 |

### 系統入口（供 Agent 使用）

```python
import sys, os
from servers import HAN_BASE_DIR
sys.path.insert(0, HAN_BASE_DIR)
from servers.tasks import get_task_progress, create_task
from servers.memory import search_memory, load_checkpoint
```

### 使用方式

對 Claude Code 說：「使用 pfc agent 規劃 [任務描述]」
'''

    try:
        if os.path.exists(claude_md_path):
            with open(claude_md_path, 'r', encoding='utf-8') as f:
                content = f.read()
            if 'HAN Multi-Agent' in content:
                print("⚠️  CLAUDE.md 已包含 PFC 系統設定，跳過")
                return
            with open(claude_md_path, 'a', encoding='utf-8') as f:
                f.write('\n' + pfc_config)
            print(f"✅ 已加入 {claude_md_path}")
        else:
            with open(claude_md_path, 'w', encoding='utf-8') as f:
                f.write(f"# {os.path.basename(cwd)} - 專案指令\n" + pfc_config)
            print(f"✅ 已建立 {claude_md_path}")
    except Exception as e:
        print(f"❌ 無法寫入 CLAUDE.md: {e}")
        print(f"   請手動加入，參考：{os.path.join(base_dir, 'README.md')}")


def ask_init_project_ssot(base_dir, auto_confirm=False):
    """詢問是否為當前專案初始化 SSOT INDEX"""
    print("\n" + "=" * 50)

    cwd = os.getcwd()
    pfc_dir = os.path.join(cwd, '.claude', 'pfc')
    index_path = os.path.join(pfc_dir, 'INDEX.md')

    if os.path.exists(index_path):
        print(f"✅ 專案 SSOT 已存在: {index_path}")
        return

    if not auto_confirm:
        response = input("是否要為當前專案初始化 SSOT INDEX？(y/n): ").strip().lower()
        if response != 'y':
            print("跳過。之後可執行 `python install.py --init-ssot` 初始化")
            return
    else:
        print("自動初始化 SSOT INDEX...")

    os.makedirs(pfc_dir, exist_ok=True)

    project_name = os.path.basename(cwd)
    index_template = f'''# {project_name} - SSOT Index

> **請 Claude 掃描專案後填入此檔案**
>
> 對 Claude 說：「請掃描專案，找出技術文件並更新 .claude/pfc/INDEX.md」

## 格式說明

用 `ref` 指向專案內的技術文件（相對路徑），Agent 會自動載入對應內容。

```yaml
docs:
  - id: doc.xxx        # 唯一識別碼
    name: 文件名稱      # 顯示名稱
    ref: path/to/file  # 相對路徑
    required: true     # 可選：標記為必讀規範
```

## 必讀規範（每次任務開始前載入）

```yaml
rules:
  # 指向專案必須遵守的規範文檔
  # Agent 開始任務前會先讀取這些文檔
  # - id: rule.coding-standards
  #   ref: docs/coding-standards.md
  #   required: true
```

## 技術文件

```yaml
docs:
  # TODO: 請 Claude 掃描專案後填入
```

## 主要程式碼

```yaml
code:
  # TODO: 請 Claude 掃描專案後填入
```
'''

    try:
        with open(index_path, 'w', encoding='utf-8') as f:
            f.write(index_template)
        print(f"✅ 已建立專案 SSOT: {index_path}")
        print("   請編輯此檔案，用 ref 指向專案內的文檔")
    except Exception as e:
        print(f"❌ 無法建立 INDEX.md: {e}")


def ask_sync_code_graph(auto_confirm=False):
    """詢問是否同步當前專案的 Code Graph"""
    print("\n" + "=" * 50)

    cwd = os.getcwd()

    if not auto_confirm:
        response = input("是否要同步當前專案的 Code Graph？(y/n): ").strip().lower()
        if response != 'y':
            print("跳過。之後可執行 `han sync` 同步")
            return
    else:
        print("自動同步 Code Graph...")

    print("📊 同步 Code Graph...")
    try:
        from servers.facade import sync
        result = sync(cwd)
        if result.get('status') == 'success':
            stats = result.get('stats', {})
            print(f"✅ Code Graph 同步完成")
            print(f"   節點: {stats.get('nodes', 0)}, 邊: {stats.get('edges', 0)}")
        else:
            print(f"⚠️  同步完成但有警告: {result.get('message', '')}")
    except Exception as e:
        print(f"❌ 同步失敗: {e}")
        print("   請確認專案結構正確，之後可執行 `han sync` 重試")


def reset_database():
    """強制重置資料庫（謹慎使用）"""
    print("⚠️  警告：這會清空所有跨專案記憶！")
    response = input("確定要重置嗎？輸入 'RESET' 確認: ")
    if response == 'RESET':
        if os.path.exists(BRAIN_DB):
            os.remove(BRAIN_DB)
        # ensure_db 會重建
        ensure_db().close()
        print("✅ 資料庫已重置")
    else:
        print("取消重置")


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='HAN System 安裝腳本（Optional — zero-config 模式下不需要）')
    parser.add_argument('--reset', action='store_true', help='重置資料庫（需手動確認）')
    parser.add_argument('--add-claude-md', action='store_true', help='自動加入 CLAUDE.md 設定')
    parser.add_argument('--init-ssot', action='store_true', help='自動初始化專案 SSOT INDEX')
    parser.add_argument('--sync-graph', action='store_true', help='自動同步 Code Graph')
    parser.add_argument('--all', action='store_true', help='執行所有可選設定（不含 reset）')
    parser.add_argument('--skip-prompts', action='store_true', help='跳過所有互動詢問（僅執行核心安裝）')

    args = parser.parse_args()

    if args.reset:
        reset_database()
    else:
        base_dir, platform_key = install()

        if args.skip_prompts:
            print("\n（使用 --skip-prompts，跳過可選設定）")
        elif args.all or args.add_claude_md or args.init_ssot or args.sync_graph:
            if args.all:
                args.add_claude_md = args.init_ssot = args.sync_graph = True
            if args.add_claude_md:
                ask_add_to_claude_md(base_dir, auto_confirm=True)
            if args.init_ssot:
                ask_init_project_ssot(base_dir, auto_confirm=True)
            if args.sync_graph:
                ask_sync_code_graph(auto_confirm=True)
        else:
            ask_add_to_claude_md(base_dir)
            ask_init_project_ssot(base_dir)
            ask_sync_code_graph()
