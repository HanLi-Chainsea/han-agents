---
description: 'HAN：為當前專案初始化 HAN（建 DB 專案、偵測技術棧、首次同步 Code Graph）'
---

# /han:init — 專案初始化

把當前目錄登錄為 HAN 專案：偵測技術棧（語言/框架/test_tool）、首次解析並同步 Code Graph。這是用其他 `/han:*` 指令前的**第一步**（HAN 本身 zero-config，首次使用也會自動觸發；本指令讓你明確初始化並看到結果）。

## 執行步驟

> `{{HAN_DIR}}` 由安裝程序替換為安全字面量；值走環境變數，不內插。

```bash
export HAN_PROJECT_PATH="$(pwd)"
export HAN_PROJECT="$(basename "$HAN_PROJECT_PATH")"
python3 - <<'PY'
import os, sys
sys.path.insert(0, {{HAN_DIR}})
from servers.project import ensure_project
r = ensure_project(os.environ['HAN_PROJECT'], os.environ['HAN_PROJECT_PATH'])
ts = r.get('tech_stack') or {}
sr = r.get('sync_result') or {}
stats = (sr.get('stats') or sr)
print("already_initialized:", r.get('already_initialized'))
print("tech_stack:", {k: ts.get(k) for k in ('primary_language','framework','test_tool')})
print("code_graph:", stats)
PY
```

整理呈現：
- 專案是否已初始化（`already_initialized`）
- 偵測到的 **語言 / 框架 / 測試工具**（`test_tool` 之後 `/han:unit-test` 等會用到）
- Code Graph 同步統計（節點/邊/變更檔數）
- 收尾建議下一步：`/han:status` 看狀態、`/han:sync` 之後再同步、`/han:unit-test <範圍>` 開始補測試

## 重要
- 這會建立/更新 HAN 內部的 SQLite 資料（專案紀錄 + Code Graph），**不修改你的原始碼**。
- 大專案首次同步較久屬正常（之後增量很快）。
