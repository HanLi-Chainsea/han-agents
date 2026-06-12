---
description: 'HAN：列出所有 /han 斜線指令與各自用途'
---

# /han:help — 指令清單

列出目前可用的 `/han:*` 指令與說明（動態讀取，永遠與實際安裝同步）。

## 執行步驟

> `{{HAN_DIR}}` 由安裝程序替換為安全字面量。

```bash
python3 - <<'PY'
import os, sys, glob
sys.path.insert(0, {{HAN_DIR}})
base = os.path.join({{HAN_DIR}}, "commands", "han")
rows = []
for f in sorted(glob.glob(os.path.join(base, "*.md"))):
    name = os.path.basename(f)[:-3]
    desc = ""
    with open(f, encoding="utf-8") as fh:
        for line in fh:
            s = line.strip()
            if s.startswith("description:"):
                desc = s[len("description:"):].strip().strip("'\"")
                break
    rows.append((name, desc))
for name, desc in rows:
    print(f"/han:{name}\n    {desc}\n")
PY
```

把結果整理成清楚的清單呈現給使用者；可順帶分組：
- **寫測試（recipe）**：unit-test、integration-test、e2e
- **分析/讀取（單次）**：review、drift、impact、recall、status
- **維護**：sync、help

## 重要
- 單次讀取，**不寫檔、不派工**。
