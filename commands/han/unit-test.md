---
description: 'HAN：為指定範圍自動建立並執行單元測試任務（recipe + dispatch 迴圈，內建 unit_test 原則）'
---

# /han:unit-test — 自動單元測試

把 `$ARGUMENTS` 當作測試範圍，透過 HAN recipe 找出未覆蓋的程式碼、建立任務樹，並驅動 executor→critic 派工迴圈**實際把測試寫出來並跑過**。品質原則（AAA / FIRST / 測行為不測實作 / 必須實跑）由 unit_test playbook 自動注入。

## 範圍解讀（`$ARGUMENTS`）
- 是路徑（如 `servers/` 或 `servers/memory.py`）→ 當 `target_path`
- 是模組名 / 自然語言範圍 → 先對應到路徑；對不到就用整個專案
- 空白 → 整個專案

> **安全**：`HAN_TARGET` 必須是單純的相對路徑（如 `servers/` 或 `servers/x.py`），**不得**含 shell 特殊字元或命令替換（`$( )`、反引號、`;`、`"` 等）。若使用者範圍無法化為這樣的乾淨路徑，就用整個專案（空字串 `""`）。主代理在嵌入指令前自行確保此點。

## 執行步驟

> 安全準則：**所有專案/路徑/範圍值一律透過環境變數傳入 Python，絕不內插進 Python 程式碼字串**。`{{HAN_DIR}}` 由安裝程序替換為安全的字面量。

1. 確認測試範圍（人類參考用；**Bash 工具的 shell state 不會跨呼叫保留**，所以下面每個 python 區塊都各自在自己的命令列 inline 重算這些值，不依賴此處的 export）：
```bash
# 僅供閱讀：每個 python 區塊會在自己的命令列上 inline 重算這些值。
# HAN_PROJECT_PATH = $(pwd)
# HAN_PROJECT      = $(basename "$(pwd)")
# HAN_TARGET       = 解讀出的範圍路徑；整個專案則留空字串 ""
```

2. 建立任務樹（值全部透過 inline 環境變數讀入；勿內插）：
```bash
HAN_PROJECT_PATH="$(pwd)" HAN_PROJECT="$(basename "$(pwd)")" HAN_TARGET="servers/" python3 - <<'PY'
# HAN_TARGET：← 換成解讀出的範圍路徑；整個專案則留空字串 ""
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

3. 驅動派工迴圈（重複，直到 `action != 'dispatch'`）。每一輪都把步驟 2 印出的同一個 epic_id 放進 `HAN_EPIC` 前綴，並 inline 重算其餘環境變數再執行：
```bash
HAN_EPIC="<epic_id>" HAN_PROJECT="$(basename "$(pwd)")" HAN_PROJECT_PATH="$(pwd)" python3 - <<'PY'
import os, sys, json
sys.path.insert(0, {{HAN_DIR}})
from servers.facade import get_next_dispatch
inst = get_next_dispatch(os.environ['HAN_EPIC'], os.environ['HAN_PROJECT'], os.environ['HAN_PROJECT_PATH'])
print(json.dumps({k: inst.get(k) for k in ('action','subagent_type','task_id','progress','message')}, ensure_ascii=False))
print('PROMPT_START'); print(inst.get('prompt','')); print('PROMPT_END')
PY
```
- `action == 'dispatch'`：用 **Agent 工具**（Claude Code 派工工具，舊稱 Task）派發，`subagent_type` 用回傳值、`prompt` 用 `PROMPT_START`…`PROMPT_END` 之間的內容。子代理完成後再次 dispatch。
- `action == 'done'`：完成；`blocked`/`waiting`：回報 `message` 並停止。

4. 收尾回報：建立了幾個任務、寫了哪些測試檔、執行 pass/fail 摘要。

## 重要
- 一定要**跑完 dispatch 迴圈**讓 executor 真的寫測試、critic 真的驗證——不要只建任務就回報。
- 不要自己手寫測試繞過 HAN；要走 recipe + dispatch，原則注入才會生效。
