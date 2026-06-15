---
description: 'HAN：通用執行器——消費任一規劃產出的任務樹（epic），驅動 executor→critic 派工迴圈把它做完。'
---

# /han:run — 通用執行任務樹

把 `$ARGUMENTS` 當作 `epic_id` 執行；省略則自動取本專案**最新 pending epic**（先印出選了哪個再跑）。
驅動 `get_next_dispatch` → `Agent` 派工迴圈直到完成。可接在 `/han:refactor`（或未來 plan/feat）規劃之後。

## 執行步驟

> `{{HAN_DIR}}` 由安裝程序替換為安全字面量。值一律走環境變數，勿內插。

1. 設定環境變數（在同一個 Bash 呼叫裡）：
```bash
export HAN_PROJECT_PATH="$(pwd)"
export HAN_PROJECT="$(basename "$HAN_PROJECT_PATH")"
export HAN_EPIC="$ARGUMENTS"   # ← 有指定 epic_id 就帶；空白＝步驟 2 自動選最新 pending
```
> `HAN_EPIC` 在此可能為空白（代表自動選）；**步驟 2 解析出具體 epic_id 後，步驟 3 一律改用該具體 id**（見下方交接說明），切勿讓步驟 3 在空白 `HAN_EPIC` 下執行。

2. 解析要執行的 epic：
```bash
python3 - <<'PY'
import os, sys
sys.path.insert(0, {{HAN_DIR}})
eid = os.environ.get('HAN_EPIC') or None
if not eid:
    from servers.facade import find_latest_pending_epic
    epic = find_latest_pending_epic(os.environ['HAN_PROJECT'])
    if not epic:
        print('NO_PENDING_EPIC'); sys.exit(0)
    eid = epic['id']
    print(f"自動選用最新 pending epic：{eid} — {epic.get('description','')}")
else:
    print(f"使用指定 epic：{eid}")
print('RESOLVED_EPIC', eid)
PY
```
- 輸出 `NO_PENDING_EPIC` → 回報「找不到可執行的 epic，請先 `/han:refactor` 或指定 epic_id」並**停止**。
- 否則記下 `RESOLVED_EPIC` 後面印出的 epic_id。

> **交接（步驟 2 → 步驟 3）**：把 `RESOLVED_EPIC` 後面印出的那個具體 epic_id，填入步驟 3 的 `HAN_EPIC="<resolved_epic_id>"` 前綴。派工迴圈會跑很多輪（每個任務一輪），**每一輪都用同一個 epic_id**——直到迴圈結束都別換、別省略。**絕對不要在 `HAN_EPIC` 為空白的情況下執行步驟 3**（空白 epic 會讓 `get_next_dispatch` 直接回 `action='done'`，整個迴圈零工作量靜默結束）。

3. 驅動派工迴圈（重複，直到 `action != 'dispatch'`）。每一輪都把步驟 2 解析出的同一個 epic_id 放進 `HAN_EPIC` 前綴再執行：
```bash
HAN_EPIC="<resolved_epic_id>" python3 - <<'PY'
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

4. 收尾回報：完成幾條鏈/任務、改了哪些檔、（若是 refactor epic）characterization test pass/fail 摘要。

## 重要
- **通用**：對任何 recipe/planner 產出的 epic 都能執行，不綁 refactor。
- 一定要跑完 dispatch 迴圈讓 executor 真的做、critic 真的驗——不要只解析 epic 就回報。
- playbook（含行為不變、characterization-first、build.gradle 護欄）由派工 prompt 依任務描述**自動注入**，無須手動帶。
