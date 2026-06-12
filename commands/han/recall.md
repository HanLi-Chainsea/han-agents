---
description: 'HAN：從長期記憶撈出與主題相關的過往決策/教訓（單次讀取，不寫檔不派工）'
---

# /han:recall — 喚起過往經驗

對 `$ARGUMENTS`（主題/關鍵詞）查 HAN 的長期記憶，回答「我們以前在這件事上學到什麼、做過什麼決定」。單次讀取。

## 執行步驟

> 安全準則：值一律透過環境變數傳入 Python。`{{HAN_DIR}}` 由安裝程序替換為安全字面量。

1. 設定環境變數：
```bash
export HAN_PROJECT_PATH="$(pwd)"
export HAN_PROJECT="$(basename "$HAN_PROJECT_PATH")"
export HAN_QUERY="<取自 $ARGUMENTS 的主題/關鍵詞>"
```

2. 查記憶：
```bash
python3 - <<'PY'
import os, sys
sys.path.insert(0, {{HAN_DIR}})
from servers.memory import search_memory
proj = os.environ['HAN_PROJECT']
rows = search_memory(os.environ['HAN_QUERY'], project=proj, limit=10) or []
if not rows:
    print("（無相關記憶）")
for m in rows:
    print(f"- [{m.get('category')}] {m.get('title')}\n    {(m.get('content') or '')[:200]}")
PY
```

3. 整理呈現：依相關度列出，點出可直接套用的教訓 / 過往決策；若無結果明確說明。

## 重要
- 單次讀取，**不寫檔、不派工**。
- 想反過來「存」一條經驗，請走 memory agent / 任務流程，不在此指令範圍。
