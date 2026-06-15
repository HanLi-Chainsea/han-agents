---
description: 'HAN：為指定範圍自動建立並執行整合測試任務（以模組分組，內建 integration_test 原則）'
---

# /han:integration-test — 自動整合測試

把 `$ARGUMENTS` 當作範圍，以**模組/目錄**為單位建立整合測試任務樹，驅動派工迴圈**實際寫出並跑過**測試。原則（跨邊界真實協作 / 序列化契約 / 本地依賴 / 必須實跑）由 integration_test playbook 自動注入。

## 範圍解讀（`$ARGUMENTS`）
- 路徑 → `target_path`；模組名 → 對應路徑；空白 → 整個專案。

## 執行步驟

> 安全準則：**所有專案/路徑/範圍值一律透過環境變數傳入 Python，絕不內插進 Python 程式碼字串**。`{{HAN_DIR}}` 由安裝程序替換為安全的字面量。

1. 設定環境變數：
```bash
export HAN_PROJECT_PATH="$(pwd)"
export HAN_PROJECT="$(basename "$HAN_PROJECT_PATH")"
export HAN_TARGET="servers/"   # ← 換成範圍路徑；整個專案則留空字串 ""
```

2. 建立任務樹：
```bash
python3 - <<'PY'
import os, sys
sys.path.insert(0, {{HAN_DIR}})
from servers.recipes import run_recipe
r = run_recipe('integration_tests',
               project_name=os.environ['HAN_PROJECT'],
               project_path=os.environ['HAN_PROJECT_PATH'],
               target_path=(os.environ.get('HAN_TARGET') or None))
print(r['message']); print('EPIC', r.get('epic_id'))
PY
```
- `task_count==0`／`EPIC` 為 None → 回報訊息後停止。

3. 派工迴圈（重複至 `action != 'dispatch'`），epic_id 放進 `HAN_EPIC`：
```bash
HAN_EPIC="<epic_id>" python3 - <<'PY'
import os, sys, json
sys.path.insert(0, {{HAN_DIR}})
from servers.facade import get_next_dispatch
inst = get_next_dispatch(os.environ['HAN_EPIC'], os.environ['HAN_PROJECT'], os.environ['HAN_PROJECT_PATH'])
print(json.dumps({k: inst.get(k) for k in ('action','subagent_type','task_id','progress','message')}, ensure_ascii=False))
print('PROMPT_START'); print(inst.get('prompt','')); print('PROMPT_END')
PY
```
- `dispatch` → 用 **Agent 工具**（Claude Code 派工工具，舊稱 Task）以回傳的 `subagent_type` 與 prompt 派發；完成後再 dispatch。
- `done` → 完成；`blocked`/`waiting` → 回報 message 並停止。

4. 收尾：建立的任務數、寫了哪些測試檔、pass/fail 摘要。

## 重要
- 必須跑完 dispatch 迴圈讓測試真的被寫出並執行，不可只建任務。
