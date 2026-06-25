---
description: 'HAN：為指定範圍自動建立並執行單元測試任務（recipe + dispatch 迴圈，內建 unit_test 原則）'
---

# /han:unit-test — 自動單元測試

把 `$ARGUMENTS` 當作測試範圍，透過 HAN recipe 找出未覆蓋的程式碼、建立任務樹，並驅動 executor→critic 派工迴圈**實際把測試寫出來並跑過**。品質原則（AAA / FIRST / 測行為不測實作 / 必須實跑）由 unit_test playbook 自動注入。

## 範圍解讀（`$ARGUMENTS`）
- 是路徑（如 `servers/` 或 `servers/memory.py`）→ 當 `target_path`
- 是模組名 / 自然語言範圍 → 先對應到路徑；對不到就用整個專案
- 空白 → 整個專案

> **安全（HAN_TARGET 是唯一來自使用者、可能被注入的值）**：主代理在嵌入指令前必須確保它同時滿足以下**全部**條件，否則 → 改用整個專案（空字串）：
>   1. 字元集符合 allowlist `^[A-Za-z0-9._/-]+$`（無空白、無 shell 特殊字元 `$ \` ; " ' ( ) & | < >`）；
>   2. **不得以 `/` 開頭**（必須是相對路徑，非絕對路徑）；
>   3. 以 `/` 切開後，**任一段都不得等於 `..`**（不得向上跳出專案）。
> **嵌入時一律用單引號包住此值**（`HAN_TARGET='servers/'`），讓 shell 不對它做任何展開；只有 `$(pwd)`、`$(basename ...)` 這類受信任值才用雙引號。
>   - Good：`HAN_TARGET='servers/utils/'`、`HAN_TARGET=''`（整個專案）
>   - Bad：`HAN_TARGET="$(rm -rf x)"`、含 `..`、絕對路徑、含空白或引號

## 執行步驟

> 安全準則：**所有專案/路徑/範圍值一律透過環境變數傳入 Python，絕不內插進 Python 程式碼字串**。`{{HAN_DIR}}` 由安裝程序替換為安全的字面量。

1. 確認測試範圍（人類參考用；**Bash 工具的 shell state 不會跨呼叫保留**，所以下面每個 python 區塊都各自在自己的命令列 inline 重算這些值，不依賴此處的 export）：
```bash
# 僅供閱讀：每個 python 區塊會在自己的命令列上 inline 重算這些值。
# HAN_PROJECT_PATH = $(pwd)
# HAN_PROJECT      = $(basename "$(pwd)")
# HAN_TARGET       = 解讀出的乾淨範圍路徑（單引號包住）；整個專案則留空字串 ''
```

2. 建立任務樹（值全部透過 inline 環境變數讀入；勿內插）：
```bash
HAN_PROJECT_PATH="$(pwd)" HAN_PROJECT="$(basename "$(pwd)")" HAN_TARGET='servers/' python3 - <<'PY'
# HAN_TARGET：← 換成解讀出的乾淨範圍路徑（單引號包住、符合 allowlist）；整個專案則留空字串 ''
import os, sys
sys.path.insert(0, {{HAN_DIR}})
from servers.recipes import run_recipe
r = run_recipe('unit_tests',
               project_name=os.environ['HAN_PROJECT'],
               project_path=os.environ['HAN_PROJECT_PATH'],
               target_path=(os.environ.get('HAN_TARGET') or None))
print(r['message']); print('EPIC', r.get('epic_id'))
PY
```
- 若 `task_count==0`／`EPIC` 為 None → 回報訊息後**停止**（沒有缺口或沒指定範圍）。

3. 驅動派工迴圈（重複，直到 `action != 'dispatch'`）。每一輪都把步驟 2 印出的同一個 epic_id 放進 `HAN_EPIC` 前綴，並 inline 重算其餘環境變數再執行。**用 `get_next_dispatch_gated`**：當下一步是 critic 驗證時，它會先用 `coverage --branch` 量測本次 target 的分支覆蓋——有未覆蓋分支就直接走 `finish_validation` 退回 executor 補測（帶具體行號、不派 critic、不費 token）；全覆蓋或工具不可用才照常派 critic：
```bash
HAN_EPIC="<epic_id>" HAN_PROJECT="$(basename "$(pwd)")" HAN_PROJECT_PATH="$(pwd)" python3 - <<'PY'
import os, sys, json
sys.path.insert(0, {{HAN_DIR}})
from servers.facade import get_next_dispatch_gated
inst = get_next_dispatch_gated(os.environ['HAN_EPIC'], os.environ['HAN_PROJECT'], os.environ['HAN_PROJECT_PATH'])
print(json.dumps({k: inst.get(k) for k in ('action','subagent_type','task_id','progress','message','coverage_summary')}, ensure_ascii=False))
print('PROMPT_START'); print(inst.get('prompt','')); print('PROMPT_END')
PY
```
- `action == 'dispatch'`：用 **Agent 工具**（Claude Code 派工工具，舊稱 Task）派發，`subagent_type` 用回傳值、`prompt` 用 `PROMPT_START`…`PROMPT_END` 之間的內容。子代理完成後再次 dispatch。
- `action == 'done'`：完成後執行步驟 4 產生報告；`blocked`/`waiting`：回報 `message` 並停止。
- `coverage_summary`（若非 null）：某個 target 通過分支覆蓋率 gate 時帶回的逐條分支摘要（每條分支 ✓ 已覆蓋／✗ 未覆蓋、含「共 N 條分支」）。**逐輪收集起來**，收尾報告要原樣列出，讓人核對邏輯。

4. 收尾回報：當迴圈 `action == 'done'` 時，執行以下程式碼產生持久化 run report（記錄測試覆蓋、critic 驗證結果、未解決建議），並告知使用者報告路徑：
```bash
HAN_EPIC="<epic_id>" HAN_PROJECT="$(basename "$(pwd)")" HAN_PROJECT_PATH="$(pwd)" python3 - <<'PY'
import os, sys
sys.path.insert(0, {{HAN_DIR}})
from servers.reporting import write_unit_test_report
path = write_unit_test_report(
    os.environ['HAN_EPIC'],
    os.environ['HAN_PROJECT'],
    os.environ['HAN_PROJECT_PATH'],
)
print(f'Run report written to: {path}')
PY
```
並附上「分支覆蓋率」區塊：把迴圈中收集到的所有 `coverage_summary` 行原樣列出（每個 target 的 n/總數 與逐條分支 ✓/✗），這是工具實測值、非宣稱，供人逐條核對。

## 重要
- 一定要**跑完 dispatch 迴圈**讓 executor 真的寫測試、critic 真的驗證——不要只建任務就回報。
- 不要自己手寫測試繞過 HAN；要走 recipe + dispatch，原則注入才會生效。
