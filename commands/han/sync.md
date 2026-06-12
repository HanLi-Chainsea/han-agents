---
description: 'HAN：同步 Code Graph（增量解析當前專案，更新節點/邊）'
---

# /han:sync — 同步 Code Graph

重新解析當前專案、更新 HAN 的 Code Graph（增量；只重掃變更檔）。在 `/han:impact`、`/han:drift`、寫測試前先跑，讓變更檔反映到圖譜。

> 註：增量同步**更新/新增**變更檔的節點，但**不會自動刪除「已刪檔」遺留的舊節點/hash**。若有大量刪檔導致圖譜殘留，需做全量重建（非本指令範圍）。

## 執行步驟

> `{{HAN_DIR}}` 由安裝程序替換為安全字面量。

```bash
export HAN_PROJECT_PATH="$(pwd)"
export HAN_PROJECT="$(basename "$HAN_PROJECT_PATH")"
python3 - <<'PY'
import os, sys
sys.path.insert(0, {{HAN_DIR}})
from servers.facade import sync
r = sync(os.environ['HAN_PROJECT_PATH'], os.environ['HAN_PROJECT'], incremental=True)
stats = (r.get('stats') or r)
print("synced:", stats)
PY
```

回報同步統計（節點/邊數、變更檔數）。

## 重要
- 只更新 HAN 內部的 Code Graph（SQLite），**不修改你的原始碼**。
- 大專案首次同步較久屬正常。
