---
description: 'HAN：影響半徑分析。改動某檔案/符號會波及哪些相依節點（單次讀取，不寫檔不派工）'
---

# /han:impact — 改動影響半徑

用 Code Graph 算出「動 `$ARGUMENTS`（檔案或符號）會炸到哪裡」：誰呼叫它、它依賴誰、變更的下游風險。單次讀取，直接產出報告。

## 範圍解讀（`$ARGUMENTS`）
- 路徑（如 `servers/memory.py`）→ 取該檔所有節點
- 符號名（如 `search_memory` 或 `MyClass`）→ 比對節點名

## 執行步驟

> 安全準則：值一律透過環境變數傳入 Python。`{{HAN_DIR}}` 由安裝程序替換為安全字面量。

1. 設定環境變數：
```bash
export HAN_PROJECT_PATH="$(pwd)"
export HAN_PROJECT="$(basename "$HAN_PROJECT_PATH")"
export HAN_TARGET="servers/memory.py"   # ← 換成 $ARGUMENTS 解讀出的檔案或符號
```

2. 查目標節點與雙向相依（depth=2）：
```bash
python3 - <<'PY'
import os, sys
sys.path.insert(0, {{HAN_DIR}})
from servers.code_graph import get_code_nodes, get_code_dependencies
proj = os.environ['HAN_PROJECT']; tgt = os.environ['HAN_TARGET']
# 先當路徑找；找不到再當符號名比對
nodes = get_code_nodes(proj, file_path=tgt, limit=100)
if not nodes:
    nodes = [n for n in get_code_nodes(proj, limit=1000) if n.get('name') == tgt or tgt in n['id']]
for n in nodes:
    deps = get_code_dependencies(proj, n['id'], depth=2, direction='both') or []
    inc = [d for d in deps if d.get('edge_kind') in ('calls','imports') and d.get('to_id')==n['id']]
    print(f"\n### {n['kind']} {n['id']}  影響半徑={len(deps)}")
    for d in deps[:25]:
        print(f"  - {d.get('name') or d.get('id')} ({d.get('kind','?')}) via {d.get('edge_kind','?')}")
PY
```

3. 整理成報告：
   - 受影響/相依的節點清單（依關係分組：呼叫者 / 被依賴）
   - 風險評估：高扇入（很多呼叫者）= 改動風險高、需更多測試
   - 建議：應一併檢查/補測試的下游節點

## 重要
- 單次讀取，**不寫檔、不派工、不改任何程式碼**。
- 找不到目標節點時，提示先 `/han:sync` 或確認名稱/路徑。
