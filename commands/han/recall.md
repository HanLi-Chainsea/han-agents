---
description: 'HAN：從長期記憶撈出與主題相關的過往決策/教訓（單次讀取，不改原始碼）'
---

# /han:recall — 喚起過往經驗

對 `$ARGUMENTS`（主題/關鍵詞）查 HAN 的長期記憶，回答「我們以前在這件事上學到什麼、做過什麼決定」。單次讀取。

## 執行步驟

> 安全準則：值一律透過環境變數傳入 Python（Python 內讀 `os.environ`，不內插）。`{{HAN_DIR}}` 由安裝程序替換為安全字面量。設環境變數時用**單引號**；若關鍵詞含 shell 特殊字元（`$` `` ` `` `'` `;` `|` `&`）先過濾。

1. 設定環境變數：
```bash
export HAN_PROJECT_PATH="$(pwd)"
export HAN_PROJECT="$(basename "$HAN_PROJECT_PATH")"
export HAN_QUERY='<取自 $ARGUMENTS 的主題/關鍵詞>'
```

2. 查記憶（查詢+格式化在 `servers.cli_views`，已單元測試）：
```bash
python3 - <<'PY'
import os, sys
sys.path.insert(0, {{HAN_DIR}})
from servers.cli_views import recall_report
print(recall_report(os.environ['HAN_PROJECT'], os.environ['HAN_QUERY']))
PY
```

3. 整理呈現：依相關度列出，點出可直接套用的教訓 / 過往決策；若無結果明確說明。

## 重要
- **不改你的原始碼、不派工**。（註：查詢會更新 HAN 記憶內部的存取統計 access_count，屬 han 自身 DB 的 metadata，不動你的檔案。）
- 想反過來「存」一條經驗，請走 memory agent / 任務流程，不在此指令範圍。
