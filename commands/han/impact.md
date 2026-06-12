---
description: 'HAN：影響半徑分析。改動某檔案/符號會波及哪些相依節點（單次讀取，不改原始碼）'
---

# /han:impact — 改動影響半徑

用 Code Graph 算出「動 `$ARGUMENTS`（檔案或符號）會炸到哪裡」：誰呼叫它、它依賴誰、變更的下游風險。單次讀取，直接產出報告。

## 範圍解讀（`$ARGUMENTS`）
- 路徑（如 `servers/memory.py`）→ 取該檔所有節點
- 符號名（如 `search_memory` 或 `MyClass`）→ 比對節點名

## 執行步驟

> 安全準則：值一律透過環境變數傳入 Python（Python 內讀 `os.environ`，不內插）。`{{HAN_DIR}}` 由安裝程序替換為安全字面量。設環境變數時用**單引號**；若 `$ARGUMENTS` 含 shell 特殊字元（`$` `` ` `` `"` `'` `;` `|` `&`）先過濾或拒絕（影響半徑目標只該是路徑/符號名）。

1. 設定環境變數：
```bash
export HAN_PROJECT_PATH="$(pwd)"
export HAN_PROJECT="$(basename "$HAN_PROJECT_PATH")"
export HAN_TARGET='servers/memory.py'   # ← 換成 $ARGUMENTS 解讀出的檔案或符號（單引號）
```

2. 產出影響半徑報告（查詢+格式化在 `servers.cli_views`，已單元測試鎖欄位契約；分扇入/扇出）：
```bash
python3 - <<'PY'
import os, sys
sys.path.insert(0, {{HAN_DIR}})
from servers.cli_views import impact_report
print(impact_report(os.environ['HAN_PROJECT'], os.environ['HAN_TARGET']))
PY
```

3. 整理成報告：
   - 依方向分組：**呼叫者/依賴我者（扇入，改動會波及）** vs **我依賴的（扇出）**
   - 風險評估：高扇入 = 改動風險高、需更多回歸測試
   - 建議：應一併檢查/補測試的扇入節點
   - 若節點數達上限（200/2000）提示結果可能不完整

## 重要
- **不改你的原始碼、不派工**；僅讀 HAN 內部圖譜（首次查詢可能初始化 han DB）。
- 找不到目標節點時，提示先 `/han:sync` 或確認名稱/路徑。
