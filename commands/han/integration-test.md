---
description: 'HAN：為指定範圍自動建立並執行整合測試任務（以模組分組，內建 integration_test 原則）'
---

# /han:integration-test — 自動整合測試

把 `$ARGUMENTS` 當作範圍，以**模組/目錄**為單位建立整合測試任務樹，驅動派工迴圈**實際寫出並跑過**測試。原則（跨邊界真實協作 / 序列化契約 / 本地依賴 / 必須實跑）由 integration_test playbook 自動注入。

## 範圍解讀（`$ARGUMENTS`）
- 路徑 → `target_path`；模組名 → 對應路徑；空白 → 整個專案。

## 執行步驟

1. Bash：`PROJECT_PATH=$(pwd)`、`PROJECT=$(basename "$PROJECT_PATH")`。

2. 建立任務樹：
```bash
python3 - <<'PY'
import sys; sys.path.insert(0, "{{HAN_DIR}}")
from servers.recipes import run_recipe
r = run_recipe('integration_tests', project_name="<PROJECT>", project_path="<PROJECT_PATH>", target_path=<TARGET_OR_None>)
print(r['message']); print("EPIC", r.get('epic_id'))
PY
```
- `task_count==0`／`epic_id` 為 None → 回報訊息後停止。

3. 派工迴圈（重複至 `action != 'dispatch'`）：
```bash
python3 - <<'PY'
import sys; sys.path.insert(0, "{{HAN_DIR}}")
from servers.facade import get_next_dispatch
import json
inst = get_next_dispatch("<EPIC_ID>", "<PROJECT>", "<PROJECT_PATH>")
print(json.dumps({k: inst.get(k) for k in ('action','subagent_type','task_id','progress','message')}, ensure_ascii=False))
print("PROMPT_START"); print(inst.get('prompt','')); print("PROMPT_END")
PY
```
- `dispatch` → 用 **Task 工具** 以回傳的 `subagent_type` 與 prompt 派發；完成後再 dispatch。
- `done` → 完成；`blocked`/`waiting` → 回報 message 並停止。

4. 收尾：建立的任務數、寫了哪些測試檔、pass/fail 摘要。

## 重要
- 必須跑完 dispatch 迴圈讓測試真的被寫出並執行，不可只建任務。
