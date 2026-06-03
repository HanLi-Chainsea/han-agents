"""
HAN System - 平台偵測與自動設定
集中化管理跨平台相容性邏輯
"""

import os
import sys
import json
import shutil
import glob as globmod

from servers import HAN_BASE_DIR, BRAIN_DB, ensure_db


# =============================================================================
# 平台設定
# =============================================================================

PLATFORMS = {
    'claude': {
        'name': 'Claude Code',
        'skills_dir': '~/.claude/skills',
        'agents_dir': '~/.claude/agents',
        'commands_dir': '~/.claude/commands',
        'settings_path': '~/.claude/settings.json',
        'supports_agents': True,
        'supports_hooks': True,
        'supports_commands': True,
    },
    'cursor': {
        'name': 'Cursor',
        'skills_dir': '~/.cursor/skills',
        'agents_dir': '.cursor/agents',       # workspace-level
        'supports_agents': True,
        'supports_hooks': False,
    },
    'windsurf': {
        'name': 'Windsurf',
        'skills_dir': '~/.codeium/windsurf/skills',
        'supports_agents': False,
        'supports_hooks': False,
    },
    'cline': {
        'name': 'Cline',
        'skills_dir': '~/.cline/skills',
        'supports_agents': False,
        'supports_hooks': False,
    },
    'codex': {
        'name': 'Codex CLI',
        'skills_dir': '~/.codex/skills',
        'supports_agents': False,
        'supports_hooks': False,
    },
    'gemini': {
        'name': 'Gemini CLI',
        'skills_dir': '~/.gemini/skills',
        'supports_agents': False,
        'supports_hooks': False,
    },
    'antigravity': {
        'name': 'Antigravity',
        'skills_dir': '~/.gemini/antigravity/skills',
        'supports_agents': False,
        'supports_hooks': False,
    },
}

# Workspace-level 路徑模式（用於偵測非 global 安裝）
_WORKSPACE_PATTERNS = [
    ('.cursor/skills', 'cursor'),
    ('.windsurf/skills', 'windsurf'),
    ('.cline/skills', 'cline'),
    ('.gemini/skills', 'gemini'),
    ('.codex/skills', 'codex'),
    ('.agent/skills', 'antigravity'),
]


# =============================================================================
# 平台偵測
# =============================================================================

def detect_platform(base_dir=None):
    """根據 han-agents 安裝位置偵測平台

    Args:
        base_dir: han-agents 根目錄（預設用 HAN_BASE_DIR）

    Returns:
        tuple: (platform_key, base_dir)。無法識別時 platform_key 為 'claude'（預設）
    """
    if base_dir is None:
        base_dir = HAN_BASE_DIR

    normalized = os.path.normpath(base_dir).replace('\\', '/')

    # 1. 比對 global skills 目錄
    for key, config in PLATFORMS.items():
        skills_dir = os.path.normpath(
            os.path.expanduser(config['skills_dir'])
        ).replace('\\', '/')
        if normalized.startswith(skills_dir):
            return key, base_dir

    # 2. 比對 workspace-level 模式
    for pattern, key in _WORKSPACE_PATTERNS:
        if pattern in normalized:
            return key, base_dir

    # 3. 預設 claude
    return 'claude', base_dir


def get_agents_dir(platform_key=None, base_dir=None):
    """取得平台對應的 agents 目錄路徑

    Args:
        platform_key: 平台 key（預設自動偵測）
        base_dir: han-agents 根目錄

    Returns:
        str 或 None（平台不支援 agents 時）
    """
    if platform_key is None:
        platform_key, base_dir = detect_platform(base_dir)

    config = PLATFORMS.get(platform_key)
    if not config or not config.get('supports_agents'):
        return None

    agents_dir = config.get('agents_dir')
    if not agents_dir:
        return None

    # workspace-level（以 . 開頭，如 .cursor/agents）
    if agents_dir.startswith('.'):
        if base_dir is None:
            base_dir = HAN_BASE_DIR
        # base_dir = ~/.cursor/skills/han-agents → 上 2 層到 ~/.cursor
        workspace_root = os.path.dirname(os.path.dirname(base_dir))
        if os.path.basename(workspace_root).startswith('.'):
            workspace_root = os.path.dirname(workspace_root)
        return os.path.join(workspace_root, agents_dir)

    # global-level
    return os.path.normpath(os.path.expanduser(agents_dir))


def get_settings_path(platform_key=None):
    """取得平台的 settings.json 路徑

    Returns:
        str 或 None（平台不支援 hooks/settings 時）
    """
    if platform_key is None:
        platform_key, _ = detect_platform()

    config = PLATFORMS.get(platform_key, {})
    path = config.get('settings_path')
    if path:
        return os.path.normpath(os.path.expanduser(path))
    return None


# =============================================================================
# 自動設定
# =============================================================================

def setup_agents(platform_key=None, base_dir=None):
    """複製 agent 定義到平台的 agents 目錄（冪等）

    Returns:
        int: 複製的檔案數量，-1 表示平台不支援
    """
    if platform_key is None:
        platform_key, base_dir = detect_platform(base_dir)
    if base_dir is None:
        base_dir = HAN_BASE_DIR

    agents_dir = get_agents_dir(platform_key, base_dir)
    if agents_dir is None:
        return -1

    os.makedirs(agents_dir, exist_ok=True)

    source_dir = os.path.join(base_dir, 'reference', 'agents')
    if not os.path.exists(source_dir):
        return 0

    copied = 0
    for f in globmod.glob(os.path.join(source_dir, '*.md')):
        dst = os.path.join(agents_dir, os.path.basename(f))
        if not os.path.exists(dst):
            shutil.copy2(f, dst)
            copied += 1
    return copied


def get_commands_dir(platform_key=None, base_dir=None):
    """取得平台的 slash-command 目錄（None 表示不支援）。"""
    if platform_key is None:
        platform_key, base_dir = detect_platform(base_dir)
    config = PLATFORMS.get(platform_key)
    if not config or not config.get('supports_commands'):
        return None
    commands_dir = config.get('commands_dir')
    if not commands_dir:
        return None
    return os.path.normpath(os.path.expanduser(commands_dir))


def setup_commands(platform_key=None, base_dir=None):
    """安裝 HAN slash 指令到平台的 commands/han/ 目錄（冪等）。

    來源 commands/han/*.md 內含 {{HAN_DIR}} 佔位符，安裝時替換為實際
    han-agents 根目錄，讓指令在任何專案目錄下都能正確 import han。

    Returns:
        int: 安裝/更新的檔案數量，-1 表示平台不支援
    """
    if platform_key is None:
        platform_key, base_dir = detect_platform(base_dir)
    if base_dir is None:
        base_dir = HAN_BASE_DIR

    commands_dir = get_commands_dir(platform_key, base_dir)
    if commands_dir is None:
        return -1

    source_dir = os.path.join(base_dir, 'commands', 'han')
    if not os.path.exists(source_dir):
        return 0

    dest_dir = os.path.join(commands_dir, 'han')
    os.makedirs(dest_dir, exist_ok=True)

    written = 0
    for f in globmod.glob(os.path.join(source_dir, '*.md')):
        with open(f, 'r', encoding='utf-8') as src:
            rendered = src.read().replace('{{HAN_DIR}}', base_dir)
        dst = os.path.join(dest_dir, os.path.basename(f))
        existing = None
        if os.path.exists(dst):
            with open(dst, 'r', encoding='utf-8') as d:
                existing = d.read()
        if existing != rendered:  # 只在缺檔或內容變更時寫入（冪等）
            with open(dst, 'w', encoding='utf-8') as out:
                out.write(rendered)
            written += 1
    return written


def _is_han_hook(entry, script_name):
    for hook in entry.get('hooks', []):
        command = hook.get('command', '')
        if script_name in command:
            return True
    return False


def _upsert_hook(settings, event_name, hook_cmd, matcher=""):
    hooks_list = settings.setdefault('hooks', {}).setdefault(event_name, [])
    script_name = os.path.basename(hook_cmd.split()[-1])
    existing = [
        h for h in hooks_list
        if h.get('matcher') in (matcher, 'Task') and _is_han_hook(h, script_name)
    ]
    hook_entry = [{"type": "command", "command": hook_cmd, "timeout": 30}]
    if existing:
        for h in existing:
            h['matcher'] = matcher
            h['hooks'] = hook_entry
    else:
        hooks_list.append({
            "matcher": matcher,
            "hooks": hook_entry,
        })


def setup_hooks(platform_key=None, base_dir=None):
    """註冊 HAN hooks 到 settings.json（冪等）

    Returns:
        bool: True 表示已設定/已存在，False 表示平台不支援
    """
    if platform_key is None:
        platform_key, base_dir = detect_platform(base_dir)
    if base_dir is None:
        base_dir = HAN_BASE_DIR

    config = PLATFORMS.get(platform_key, {})
    if not config.get('supports_hooks'):
        return False

    settings_path = get_settings_path(platform_key)
    if not settings_path:
        return False

    post_hook_cmd = f"python3 {os.path.join(base_dir, 'hooks', 'post_task.py')}"
    pre_hook_cmd = f"python3 {os.path.join(base_dir, 'hooks', 'pre_tool.py')}"

    settings = {}
    if os.path.exists(settings_path):
        try:
            with open(settings_path, 'r', encoding='utf-8') as f:
                settings = json.load(f)
        except (json.JSONDecodeError, IOError):
            settings = {}

    # Use empty matchers so hooks can observe Task lifecycle and guardrail-able
    # tool events in hosts that provide them.
    _upsert_hook(settings, 'PreToolUse', pre_hook_cmd, matcher="")
    _upsert_hook(settings, 'PostToolUse', post_hook_cmd, matcher="")

    with open(settings_path, 'w', encoding='utf-8') as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)

    return True


def auto_setup(base_dir=None):
    """一鍵自動設定：DB + agents + hooks（冪等，跨平台）

    Returns:
        dict: {platform, agents_copied, hooks_set}
    """
    if base_dir is None:
        base_dir = HAN_BASE_DIR

    platform_key, base_dir = detect_platform(base_dir)
    platform_name = PLATFORMS.get(platform_key, {}).get('name', platform_key)

    # 1. 確保 DB
    ensure_db()

    # 2. 複製 agents
    agents_copied = setup_agents(platform_key, base_dir)

    # 3. 註冊 hooks
    hooks_set = setup_hooks(platform_key, base_dir)

    # 4. 安裝 slash 指令
    commands_installed = setup_commands(platform_key, base_dir)

    return {
        'platform': platform_name,
        'platform_key': platform_key,
        'agents_copied': agents_copied,
        'hooks_set': hooks_set,
        'commands_installed': commands_installed,
    }
