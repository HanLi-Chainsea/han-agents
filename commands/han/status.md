---
description: 'HAN：快速顯示專案狀態（Code Graph 統計、任務、記憶）（單次讀取）'
---

# /han:status — 專案狀態速覽

顯示 HAN 對當前專案的掌握：Code Graph 節點/邊數、任務進度、記憶筆數等。

## 執行步驟

> `{{HAN_DIR}}` 由安裝程序替換為安全字面量。

```bash
export HAN_PROJECT_PATH="$(pwd)"
python3 - <<'PY'
import os, sys
sys.path.insert(0, {{HAN_DIR}})
from servers.facade import quick_status
print(quick_status(os.environ['HAN_PROJECT_PATH']))
PY
```

把輸出整理呈現。若顯示尚未初始化，提示先 `/han:sync`。

## 重要
- 單次讀取，**不寫檔、不派工、不改程式碼**。
