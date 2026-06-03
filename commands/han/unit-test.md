---
description: 'HAN：為指定範圍自動建立並執行單元測試任務（recipe + dispatch 迴圈，內建 unit_test 原則）'
---

# /han:unit-test — 自動單元測試

把 `$ARGUMENTS` 當作測試範圍，透過 HAN recipe 找出未覆蓋的程式碼、建立任務樹，並驅動 executor→critic 派工迴圈**實際把測試寫出來並跑過**。品質原則（AAA / FIRST / 測行為不測實作 / 必須實跑）由 unit_test playbook 自動注入。

## 範圍解讀（`$ARGUMENTS`）
- 是路徑（如 `servers/` 或 `servers/memory.py`）→ 當 `target_path`
- 是模組名 / 自然語言範圍 → 先用它對應到路徑；對不到就用整個專案
- 空白 → 整個專案

## 執行步驟

1. 用 Bash 取得工作目錄與專案名：`PROJECT_PATH=$(pwd)`、`PROJECT=$(basename "$PROJECT_PATH")`。

2. 建立任務樹（Bash 執行 Python；`HAN_DIR` 已由安裝程序填入）：
```bash
python3 - <<'PY'
import sys; sys.path.insert(0, "{{HAN_DIR}}")
from servers.recipes import run_recipe
r = run_recipe('unit_tests', project_name="<PROJECT>", project_path="<PROJECT_PATH>", target_path=<TARGET_OR_None>)
print(r['message']); print("EPIC", r.get('epic_id'))
PY
```
- 若 `task_count==0`／`epic_id` 為 None → 把訊息回報給使用者後**停止**（沒有缺口或沒指定範圍）。

3. 驅動派工迴圈（重複，直到 `action != 'dispatch'`）：
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
- 當 `action == 'dispatch'`：用 **Task 工具**派發，`subagent_type` 用回傳值、`prompt` 用 `PROMPT_START`…`PROMPT_END` 之間的內容。子代理完成後再次呼叫上面的 dispatch。
- 當 `action == 'done'`：完成；`blocked`/`waiting`：把 `message` 回報並停止。

4. 收尾回報：建立了幾個任務、寫了哪些測試檔、執行 pass/fail 摘要。

## 重要
- 一定要**跑完 dispatch 迴圈**讓 executor 真的寫測試、critic 真的驗證——不要只建任務就回報。
- 不要自己手寫測試繞過 HAN；要走 recipe + dispatch，原則注入才會生效。
