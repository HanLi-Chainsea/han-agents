---
description: 'HAN：分析可測試性熱點並產出重構規劃（高把握→可執行任務樹；沒把握→建議報告）。只規劃、不改碼、不派工。'
---

# /han:refactor — 為可測試性重構（規劃）

把 `$ARGUMENTS` 當作分析範圍，掃出「測不動的糾纏鏈路」熱點，依**重構型錄**分類：
高把握（機械式、行為不變）建成可執行任務樹（含 characterization-test 安全網）；沒把握的只列為建議交人決定。
**本指令只規劃：不修改任何原始碼、不派工。** 要實際執行請接 `/han:run`。

## 範圍解讀（`$ARGUMENTS`）
- 路徑（如 `servers/`）→ 當 `target_path`
- 模組名 / 自然語言 → 對應到路徑；對不到就用整個專案
- 空白 → 整個專案

> **安全（HAN_TARGET 是唯一來自使用者、可能被注入的值）**：主代理在嵌入指令前必須確保它同時滿足以下**全部**條件，否則 → 改用整個專案（空字串）：
>   1. 字元集符合 allowlist `^[A-Za-z0-9._/-]+$`（無空白、無 shell 特殊字元 `$ \` ; " ' ( ) & | < >`）；
>   2. **不得以 `/` 開頭**（必須是相對路徑，非絕對路徑）；
>   3. 以 `/` 切開後，**任一段都不得等於 `..`**（不得向上跳出專案）。
> **嵌入時一律用單引號包住此值**（`HAN_TARGET='servers/'`），讓 shell 不對它做任何展開；只有 `$(pwd)`、`$(basename ...)` 這類受信任值才用雙引號。

## 執行步驟

> 安全準則：**所有專案/路徑/範圍值一律透過環境變數傳入 Python，絕不內插進 Python 程式碼字串**。`{{HAN_DIR}}` 由安裝程序替換為安全字面量。

1. 確認目標範圍（人類參考用；**Bash 工具的 shell state 不會跨呼叫保留**，下面每個 python 區塊都各自 inline 重算這些值，不依賴此處的 export）：
```bash
# 僅供閱讀：每個 python 區塊會在自己的命令列上 inline 重算這些值。
# HAN_PROJECT_PATH = $(pwd)
# HAN_PROJECT      = $(basename "$(pwd)")
# HAN_TARGET       = 解讀出的範圍路徑；整個專案則留空字串 ""
```

2. 掃描候選（不建 epic、不改碼）：
```bash
HAN_PROJECT_PATH="$(pwd)" HAN_PROJECT="$(basename "$(pwd)")" HAN_TARGET="servers/" python3 - <<'PY'
# HAN_TARGET：← 換成解讀出的範圍路徑；整個專案則留空字串 ""
import os, sys, json
sys.path.insert(0, {{HAN_DIR}})
from servers.recipes import scan_refactor_candidates
r = scan_refactor_candidates(
    project_name=os.environ['HAN_PROJECT'],
    project_path=os.environ['HAN_PROJECT_PATH'],
    target_path=(os.environ.get('HAN_TARGET') or None))
print(r['message'])
print('TRUNCATED', r.get('truncated'))
print('EXISTING_PENDING_EPIC', r.get('existing_pending_epic'))
print('CANDIDATES_JSON_START')
print(json.dumps(r['candidates'], ensure_ascii=False))
print('CANDIDATES_JSON_END')
PY
```
- 候選為空 → 回報訊息後**停止**（沒有熱點）。

3. **分類（主代理判斷）**：讀 `reference/playbooks/refactor.md` 的型錄表，對每個候選讀其原始碼（`file_path` 的 `line_start`–`line_end`），判定需要的重構型錄項屬「高把握」或「沒把握」：
   - **高把握**（Extract Method/Variable、Inline、Rename、Decompose Conditional、Replace Magic Number 等，且區域範圍、不改 public 契約、可被 characterization test 釘住）→ 收進 `high` 清單，每項記 `{file_path, name, refactor_type, line_start, line_end}`。
   - **沒把握**（Introduce Interface/DI、Move、改簽章、打斷共享狀態、繼承改組合、動到並行/IO/框架，或無法寫 characterization test）→ 收進 `low` 清單，記位置 + 型錄項 + 理由。

4. 建可執行任務樹（只放高把握；值用環境變數/檔案傳，勿內插）：先選一個**唯一**字面暫存路徑（避免競態/覆寫，例如用 `$RANDOM` 或時間戳），把 `high` 清單寫進該暫存 JSON，**後續讀檔用同一條路徑**——
```bash
# 用 mktemp 取唯一暫存檔（避免低熵 $RANDOM 與 symlink 競態）；寫、讀、清理都在同一個 Bash 區塊
HAN_HIGH_JSON="$(mktemp "${TMPDIR:-/tmp}/han_refactor_high.XXXXXX")"
trap 'rm -f "$HAN_HIGH_JSON"' EXIT
cat > "$HAN_HIGH_JSON" <<'JSON'
[
  {"file_path": "servers/x.py", "name": "foo", "refactor_type": "Extract Method", "line_start": 1, "line_end": 80}
]
JSON
# ↑ 上面是格式範例；換成你判定出的真正「高把握」清單（每項 {file_path, name, refactor_type, line_start, line_end}）。
HAN_PROJECT="$(basename "$(pwd)")" HAN_HIGH_JSON="$HAN_HIGH_JSON" python3 - <<'PY'
import os, sys, json
sys.path.insert(0, {{HAN_DIR}})
from servers.recipes import build_refactor_epic
items = json.load(open(os.environ['HAN_HIGH_JSON'], encoding='utf-8'))
r = build_refactor_epic(os.environ['HAN_PROJECT'], items)
print('EPIC', r.get('epic_id'), 'stories', r.get('story_count'), 'tasks', r.get('task_count'))
print('REJECTED_JSON_START')
print(json.dumps(r.get('rejected', []), ensure_ascii=False))
print('REJECTED_JSON_END')
PY
```
- `high` 為空 → 不建 epic（`EPIC None`）。

5. 寫計畫報告（用 Write 工具）至 `.han/refactor-plan-<ts>.md`（先 `mkdir -p .han`，`ts` 用 `date +%Y%m%d-%H%M%S`）：
   - 標頭：target、掃描熱點數、是否截斷、`epic_id`。
   - 區段 A「已排入計畫（高把握）」：逐項列 檔案/方法、重構型錄項、三步任務。
   - 區段 B「建議／需人工決定（沒把握）」：逐項列 位置、型錄項、**判定為沒把握的理由**。
     - 解析步驟 4 輸出 `REJECTED_JSON_START`…`REJECTED_JSON_END` 之間的 JSON（被 `build_refactor_epic` 退回的項），把每一項的**位置 + 理由**也列在此區段。

6. 收尾回報：`epic_id`、報告路徑、高把握任務數、沒把握建議數。提示「要執行請跑 `/han:run <epic_id>`」。

## 重要
- **絕不修改原始碼、絕不派工**——這是純規劃指令。
- 分類務必依 `reference/playbooks/refactor.md` 型錄；拿不準的一律歸「沒把握／建議」（寧可保守）。
