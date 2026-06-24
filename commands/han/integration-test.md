---
description: 'HAN：為指定範圍自動建立並執行整合測試任務（以模組分組，內建 integration_test 原則）'
---

# /han:integration-test — 自動整合測試

把 `$ARGUMENTS` 當作範圍，以**模組/目錄**為單位建立整合測試任務樹，驅動派工迴圈**實際寫出並跑過**測試。原則（跨邊界真實協作 / 序列化契約 / 本地依賴 / 必須實跑）由 integration_test playbook 自動注入。

## 範圍解讀（`$ARGUMENTS`）
- 路徑 → `target_path`；模組名 → 對應路徑；空白 → 整個專案。

## 執行步驟

> 安全準則：**所有專案/路徑/範圍值一律透過環境變數傳入 Python，絕不內插進 Python 程式碼字串**。`{{HAN_DIR}}` 由安裝程序替換為安全的字面量。

**步驟 0 — 確認場景（原生選項，必問）**

向用戶呈現以下三個選項，並等待回答再繼續：

> 請選擇整合測試場景：
> 1. **補現有系統整合測**（預設）— 對給定範圍全面建立整合測試任務
> 2. **驗收剛改的部分** — 範圍縮窄至 git diff 的變更檔案（适合 PR review 前驗收）
> 3. **只看邊界清單不寫碼** — 呼叫 boundaries_for_target 印出模組邊界清單後停止，不建任務

各場景對應行為：
- 選 **1（預設）**：`target_path` = `$ARGUMENTS` 所解讀的路徑（整個專案或指定子目錄），正常走 recipe + dispatch 迴圈。
- 選 **2（驗收剛改的部分）**：改用 `git diff --name-only HEAD` 縮窄 target 到已變更的來源檔，其餘流程不變。
- 選 **3（只看邊界清單）**：執行下方「邊界清單」程式碼區塊，印出 `boundaries_for_target` 結果後停止，不建任務、不派工。

**選 3 — 邊界清單（列印後停止）**：
```bash
HAN_PROJECT_PATH="$(pwd)" HAN_PROJECT="$(basename "$(pwd)")" HAN_TARGET="servers/"   python3 - <<'PY'
# HAN_TARGET：← 換成範圍路徑
import os, sys, json
sys.path.insert(0, {{HAN_DIR}})
from servers.integration_gate import boundaries_for_target
# 取範圍內的來源檔（與 recipe 同邏輯）
from servers.recipes import _list_source_files
files = _list_source_files(os.environ['HAN_PROJECT'], os.environ.get('HAN_TARGET') or None)
boundaries = boundaries_for_target(os.environ['HAN_PROJECT'], files)
print(json.dumps(boundaries, ensure_ascii=False, indent=2))
print(f'--- 共 {len(boundaries)} 條邊界 ---')
PY
```

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

3. 派工迴圈（重複至 `action != 'dispatch'`）。**使用 `get_next_dispatch_integration_gated`**：當下一步是 critic 驗證時，它會先跑整合 gate（L1 測試必須 run+pass；L2 mock-smell 偵測邊界合作者不可 mock）——有違規就直接 `finish_validation` 退回 executor 補測（帶具體合作者名稱、不費 token）；通過才照常派 critic：
```bash
HAN_EPIC="<epic_id>" HAN_PROJECT="$(basename "$(pwd)")" HAN_PROJECT_PATH="$(pwd)" python3 - <<'PY'
import os, sys, json
sys.path.insert(0, {{HAN_DIR}})
from servers.facade import get_next_dispatch_integration_gated
inst = get_next_dispatch_integration_gated(os.environ['HAN_EPIC'], os.environ['HAN_PROJECT'], os.environ['HAN_PROJECT_PATH'])
print(json.dumps({k: inst.get(k) for k in ('action','subagent_type','task_id','progress','message','coverage_summary')}, ensure_ascii=False))
print('PROMPT_START'); print(inst.get('prompt','')); print('PROMPT_END')
PY
```
- `action == 'dispatch'`：用 **Agent 工具**（Claude Code 派工工具，舊稱 Task）以回傳的 `subagent_type` 與 prompt 派發；完成後再 dispatch。
- `action == 'done'`：完成；`blocked`/`waiting`：回報 `message` 並停止。
- `coverage_summary`（若非 null）：gate 通過時帶回的邊界驗證摘要（4-class 標籤：verified-real / not-observed / not-measurable / mocked）。**逐輪收集起來**，收尾報告要原樣列出。

4. 收尾回報：建立的任務數、寫了哪些測試檔、pass/fail 摘要。**並附上「邊界驗證」區塊**：把迴圈中收集到的所有 `coverage_summary` 行原樣列出（每條邊界的 4-class 標籤與 L1 tests run 數），這是工具實測值、非宣稱，供人核對整合邊界是否真實被測試：
  - ✅ `verified-real` — 邊界協作者的程式碼確實在測試中被執行（非 mock）
  - ⚠️ `not-observed` — 測試跑了但邊界協作者未被實際呼叫到
  - ❓ `not-measurable` — coverage 工具無法量測（可能是 Java/外部服務）
  - ❌ `mocked` — 協作者被 mock，不算真實整合測試（gate L2 應已擋下）

## 重要
- 必須跑完 dispatch 迴圈讓測試真的被寫出並執行，不可只建任務。
- 不要自己手寫測試繞過 HAN；要走 recipe + dispatch，原則注入才會生效。
