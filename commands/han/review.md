---
description: 'HAN：帶專案脈絡的審查。吃 code 或想法/設計，對著 Code Graph + SSOT + 記憶批判並產出分級報告'
---

# /han:review — 帶脈絡的審查（code 或想法）

對 `$ARGUMENTS` 做**一次性批判並直接產出報告**。與 recipe 不同：**不建任務樹、不派工、不改原始碼**——你（模型）讀取 HAN 的 Code Graph / SSOT / 記憶當脈絡後，直接寫出分級 review（預設顯示在對話；`--out`/`--post` 才落地）。

> 與 ccg:review 的差異：ccg 是通用雙模型；`/han:review` 的價值是**專案脈絡**——對著「實際架構、相依關係、過往決策」審。不要做成多模型 fan-out。

> 安全準則：**所有值（專案名、關鍵詞、檔名）一律透過環境變數傳入 Python，Python 內讀 `os.environ`、絕不內插**。設環境變數時用**單引號**；若值含 shell 特殊字元（`$` `` ` `` `"` `'` `;` `|` `&`）先過濾或拒絕。`{{HAN_DIR}}` 由安裝程序替換為安全的字面量。

## 模式判定
- `$ARGUMENTS` 是路徑 / 含程式碼 / 空白 → **CODE 模式**
- `$ARGUMENTS` 是一段想法/設計/提案的敘述 → **IDEA 模式**

先設環境變數：
```bash
export HAN_PROJECT_PATH="$(pwd)"
export HAN_PROJECT="$(basename "$HAN_PROJECT_PATH")"
```

---

## CODE 模式

1. 取得變更檔清單：`git diff --name-only HEAD`（或使用者指定的 base / 路徑；空白就審當前工作區 diff），以及 `git diff HEAD` 看實際變更。
2. **對每個變更檔**查節點與 1-hop 相依，評估影響半徑（檔名逐一放進 `HAN_FILE` 重複執行）：
```bash
HAN_FILE='servers/foo.py' python3 - <<'PY'
import os, sys
sys.path.insert(0, {{HAN_DIR}})
from servers.cli_views import blast_radius
print(blast_radius(os.environ['HAN_PROJECT'], os.environ['HAN_FILE']))
PY
```
   （對每個變更檔重複；這份相依清單就是要納入 review 的「blast radius」。）
3. 讀審查原則：`reference/playbooks/code-review.md`（在 han 安裝目錄下）。
4. 依 Google 檢查序（Design→Functionality→Complexity→Tests→Naming→Comments→Style→Docs）逐項審，**並結合上面查到的影響半徑**指出下游風險。
5. 輸出**分級報告**：
   - `Critical`（必修才能合併）/ `Major` / `Minor` / `Suggestion`
   - 每項附 `file:line` 與具體修正；附「影響範圍」（哪些相依節點會被波及）
   - 結論回答：此變更是否提升整體 code health。

---

## IDEA 模式

1. 撈專案脈絡與相關過往經驗（關鍵詞透過環境變數傳入）：
```bash
HAN_QUERY='<取自想法的關鍵詞>' python3 - <<'PY'
import os, sys
sys.path.insert(0, {{HAN_DIR}})
from servers.cli_views import recall_report
print(recall_report(os.environ['HAN_PROJECT'], os.environ['HAN_QUERY']))
PY
```
   （如專案有定義 SSOT flows/domains，可一併比對想法會動到哪些 flow。）
2. **對著專案現實批判**這個想法：
   - 與**現有架構**衝突嗎？會動到哪些 flow / 模組？
   - 是否**違反記憶裡的某個過往決策**（撈到就點名）？
   - 風險、隱含假設、更簡單的替代方案。
3. 輸出**分級報告**：`Critical`（致命缺陷/與既有設計衝突）/ `Major` / `Minor` / `Suggestion`，每項給理由與具體建議，最後給「採納 / 修正後採納 / 不建議」結論。

---

## 輸出（落地位置）

**預設：在對話顯示完整報告。** 要落地到檔案或 PR/MR，依使用者指示：

> ⚠️ 寫檔/貼留言時，**一律用 Write 工具把完整 markdown 報告寫到檔案**（不要用 `printf`/`echo`/`"$(...)"` 把報告內容塞進 shell——報告可能含 `"`、`$()`、backtick，會造成 shell 注入）。再用「以檔案為輸入」的旗標帶入。

> 寫檔一律用 **Write 工具搭配「字面路徑」**（不要用 shell 變數如 `$F`——它不跨 Bash 呼叫保留，Write 也只吃字面路徑）。

1. **`--out <path>`** → 用 Write 工具把報告寫到該可見路徑（如 `docs/review-<branch>.md`），並在對話顯示。

2. **`--post`（明確要求才發佈；這是對外公開動作）**：報告會公開在 PR/MR，**必須使用者明確帶 `--post` 或明說「貼到 PR/MR」才執行**：
   - 偵測目標：GitHub `gh pr view --json url -q .url`；GitLab `glab mr view`
   - 選一個**唯一**字面暫存路徑（避免競態/覆寫，例如 `/tmp/han-review-<你產生的隨機字串>.md`），用 Write 工具把完整報告寫到該路徑，且**後續發佈指令用同一個字面路徑**：
     - GitHub：`gh pr comment --body-file /tmp/han-review-XXXX.md`
     - GitLab（當前分支的 MR，從 stdin 讀；`--resolvable=false` 避免建立可阻擋合併的 discussion）：`glab mr note create --resolvable=false < /tmp/han-review-XXXX.md`
   - 貼完回報 comment 連結，並 `rm -f` 該暫存檔。

3. **沒給旗標 → 只在對話顯示**，不自動建檔、不自動發佈。

## 重要
- **一定要產出實際的 review 報告**（不是「我會去審」）；單次完成、不繞 dispatch。
- CODE 模式至少要納入一項由 `get_code_dependencies` 查到的影響半徑觀察，IDEA 模式至少要撈一次記憶/SSOT——否則就退化成通用 review，失去 HAN 的脈絡價值。
- **不修改原始碼**；只有 `--out`/`--post` 才產生報告檔（用 Write 工具寫，非 shell）。發佈到 PR/MR 一律 opt-in。
