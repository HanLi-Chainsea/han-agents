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

2. 查目標節點的相依。`get_code_dependencies` 回傳每筆含 `id`/`kind`/`name`/`relation`/`direction`/`depth`。**分開呼叫 incoming 與 outgoing**（在 `direction='both'` 下，第二階的 `direction` 是相對當下節點、非相對目標，會混淆；分開呼叫才語義正確）：
```bash
python3 - <<'PY'
import os, sys
sys.path.insert(0, {{HAN_DIR}})
from servers.code_graph import get_code_nodes, get_code_dependencies
proj = os.environ['HAN_PROJECT']; tgt = os.environ['HAN_TARGET']
# 先當路徑找；找不到再當符號名比對
nodes = get_code_nodes(proj, file_path=tgt, limit=200)
if not nodes:
    nodes = [n for n in get_code_nodes(proj, limit=2000) if n.get('name') == tgt or tgt in n['id']]
if not nodes:
    print("（找不到目標節點，請先 /han:sync 或確認路徑/名稱）")
for n in nodes:
    incoming = get_code_dependencies(proj, n['id'], depth=2, direction='incoming') or []  # 誰依賴/呼叫我（扇入）
    outgoing = get_code_dependencies(proj, n['id'], depth=2, direction='outgoing') or []  # 我依賴誰（扇出）
    print(f"\n### {n['kind']} {n['id']}  影響半徑={len(incoming)+len(outgoing)}（扇入 {len(incoming)} / 扇出 {len(outgoing)}）")
    print("  呼叫者/依賴我者（改動會波及）：")
    for d in incoming[:20]:
        print(f"    - {d.get('name') or d.get('id')} ({d.get('kind','?')}) via {d.get('relation','?')} [深度 {d.get('depth')}]")
    print("  我依賴的：")
    for d in outgoing[:20]:
        print(f"    - {d.get('name') or d.get('id')} ({d.get('kind','?')}) via {d.get('relation','?')}")
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
