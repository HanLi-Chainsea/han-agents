---
description: 'HAN：偵測 SSOT(意圖) 與 Code(現實) 的偏差，產出 drift 報告（單次讀取，不改原始碼）'
---

# /han:drift — 設計與實作偏差偵測

比對「意圖」與 **Code Graph（實際怎樣）**，找出偏差。單次讀取，直接產出報告。

**兩種模式（自動路由）**：
- 專案根有 `intent-manifest.json` → **doc-grounded 模式**：意圖來自 manifest 註冊的
  PRD/SA/SD（確定性錨點抽取 + scoped 比對 + git 訊號解 status），報告含四車道：
  Drift / Doc-stale（文件過期，非 drift）/ Needs-review / Coverage watermark。
- 無 manifest → legacy SSOT 模式（SKILL.md flows/domains 連結檢查）。

> 範圍說明（誠實）：目前 `get_drift_summary()` 主要做 **SSOT 連結有效性 / 缺檔（missing_file / broken link）** 檢查。**若專案尚未定義 SSOT（SKILL.md flows/domains）或 Code Graph 為空，底層可能回 `✅ In sync`——這是「沒東西可比」而非「真的一致」**，呈現時務必點明這個前提，不要報成健康。

## 執行步驟

> 安全準則：值一律透過環境變數傳入 Python。`{{HAN_DIR}}` 由安裝程序替換為安全字面量。

1. 設定環境變數：
```bash
export HAN_PROJECT_PATH="$(pwd)"
export HAN_PROJECT="$(basename "$HAN_PROJECT_PATH")"
```

2. 取得 drift 報告（必要時先 `/han:sync` 把變更檔更新進圖譜；註：sync 不會移除已刪檔的舊節點）：
```bash
python3 - <<'PY'
import os, sys
sys.path.insert(0, {{HAN_DIR}})
from servers.cli_views import drift_report
print(drift_report(os.environ['HAN_PROJECT'], os.environ['HAN_PROJECT_PATH']))
PY
```

3. 把報告整理呈現：
   - 依嚴重度（🔴 critical / 🟠 high / 🟡 medium / 🟢 low）列出每筆 drift，標明型別（如 missing_file）、SSOT 項、Code 項、建議
   - **若 `✅ In sync`：先確認專案有定義 SSOT 且 Code Graph 非空**（可參考 `/han:status`）。兩者皆空時要報「無可比對基準」而非「健康」。

## 重要
- **不改你的原始碼、不派工**；僅讀 HAN 內部資料（首次查詢可能初始化 han DB）。
- 別把空 SSOT/空圖譜的 `In sync` 誤報成「設計與實作一致」。
