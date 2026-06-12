---
description: 'HAN：快速顯示專案狀態（Code Graph 節點/邊/檔數 + Skill 健康度）（單次讀取）'
---

# /han:status — 專案狀態速覽

顯示 HAN 對當前專案的掌握：專案名/路徑、健康度、Code Graph 節點/邊/檔數、Skill 資訊（`quick_status()` 的內容）。

## 執行步驟

> `{{HAN_DIR}}` 由安裝程序替換為安全字面量。

```bash
export HAN_PROJECT_PATH="$(pwd)"
python3 - <<'PY'
import os, sys
sys.path.insert(0, {{HAN_DIR}})
from servers.cli_views import status_report
print(status_report(os.environ['HAN_PROJECT_PATH']))
PY
```

把輸出整理呈現。若顯示尚未初始化，提示先 `/han:sync`。

## 重要
- **不改你的原始碼、不派工**；首次查詢可能初始化 han DB。
