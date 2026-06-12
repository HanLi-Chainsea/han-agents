---
description: 'HAN：偵測 SSOT(意圖) 與 Code(現實) 的偏差，產出 drift 報告（單次讀取，不寫檔不派工）'
---

# /han:drift — 設計與實作偏差偵測

比對 HAN 的 **SSOT（應該怎樣）** 與 **Code Graph（實際怎樣）**，找出 missing implementation / missing spec / mismatch / stale spec。單次讀取，直接產出報告。

## 執行步驟

> 安全準則：值一律透過環境變數傳入 Python。`{{HAN_DIR}}` 由安裝程序替換為安全字面量。

1. 設定環境變數：
```bash
export HAN_PROJECT_PATH="$(pwd)"
export HAN_PROJECT="$(basename "$HAN_PROJECT_PATH")"
```

2. 取得 drift 報告（必要時先 sync 確保 Code Graph 最新）：
```bash
python3 - <<'PY'
import os, sys
sys.path.insert(0, {{HAN_DIR}})
from servers.drift import get_drift_summary
print(get_drift_summary(os.environ['HAN_PROJECT'], os.environ['HAN_PROJECT_PATH']))
PY
```

3. 把報告整理呈現：
   - 依嚴重度（🔴 critical / 🟠 high / 🟡 medium / 🟢 low）列出每筆 drift
   - 每筆標明型別（missing_implementation / missing_spec / mismatch / stale_spec）、SSOT 項、Code 項、建議
   - 若 `✅ In sync` 則明確回報無偏差

## 重要
- 單次讀取，**不寫檔、不派工、不改任何程式碼**。
- 若報告為空或專案無 SSOT，明確告知（可能尚未定義 SKILL.md flows/domains）。
