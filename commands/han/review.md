---
description: 'HAN：帶專案脈絡的審查。吃 code 或想法/設計，對著 Code Graph + SSOT + 記憶批判並產出分級報告'
---

# /han:review — 帶脈絡的審查（code 或想法）

對 `$ARGUMENTS` 做**一次性批判並直接產出報告**。與 recipe 不同：**不建任務樹、不派工、不寫檔**——你（模型）讀取 HAN 的 Code Graph / SSOT / 記憶當脈絡後，直接寫出分級 review。

> 與 ccg:review 的差異：ccg 是通用雙模型；`/han:review` 的價值是**專案脈絡**——對著「實際架構、相依關係、過往決策」審。不要做成多模型 fan-out。

> 安全準則：**所有值（專案名、關鍵詞、檔名）一律透過環境變數傳入 Python，絕不內插進 Python 程式碼字串**。`{{HAN_DIR}}` 由安裝程序替換為安全的字面量。

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
HAN_FILE="servers/foo.py" python3 - <<'PY'
import os, sys
sys.path.insert(0, {{HAN_DIR}})
from servers.code_graph import get_code_nodes, get_code_dependencies
proj = os.environ['HAN_PROJECT']; fp = os.environ['HAN_FILE']
nodes = get_code_nodes(proj, file_path=fp, limit=50)
for n in nodes:
    deps = get_code_dependencies(proj, n['id'], depth=1, direction='both') or []
    names = [d.get('name') or d.get('id') for d in deps][:10]
    print(f"{n['kind']} {n['id']} -> 影響/相依({len(deps)}): {names}")
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
HAN_QUERY="<取自想法的關鍵詞>" python3 - <<'PY'
import os, sys
sys.path.insert(0, {{HAN_DIR}})
from servers.memory import search_memory
proj = os.environ['HAN_PROJECT']
for m in (search_memory(os.environ['HAN_QUERY'], project=proj, limit=8) or []):
    print("MEM:", m.get('title'), "->", (m.get('content') or '')[:160])
PY
```
   （如有 SSOT flows/domains，亦可用 `servers.facade.get_full_context` 取相關 flow 比對。）
2. **對著專案現實批判**這個想法：
   - 與**現有架構**衝突嗎？會動到哪些 flow / 模組？
   - 是否**違反記憶裡的某個過往決策**（撈到就點名）？
   - 風險、隱含假設、更簡單的替代方案。
3. 輸出**分級報告**：`Critical`（致命缺陷/與既有設計衝突）/ `Major` / `Minor` / `Suggestion`，每項給理由與具體建議，最後給「採納 / 修正後採納 / 不建議」結論。

---

## 輸出（落地位置）

一份 review 屬於它所審的「變更」，所以**有 PR/MR 時，它的家就是那個 PR/MR**（好找、有組織、跟變更綁在一起）；沒有才退回對話/指定檔。**絕不倒進隱藏目錄或時間戳檔堆。**

依序判斷：

1. **偵測當前分支是否有開啟的 PR/MR**：
   - GitHub：`gh pr view --json number,url -q .url`（成功即有 PR）
   - GitLab：`glab mr view`（成功即有 MR）

2. **有 PR/MR → 預設貼成 comment**（這是對外發佈：先回報「將貼到 <url>」，使用者帶 `--no-post` 則略過只顯示）：
   - 先把完整 markdown 報告寫到暫存檔，例如 `T=$(mktemp); printf '%s\n' "<報告>" > "$T"`
   - GitHub：`gh pr comment --body-file "$T"`（當前分支的 PR）
   - GitLab：`glab mr note "$(cat "$T")"`（或 API）
   - 貼完回報 comment 連結。

3. **沒有 PR/MR → 預設只在對話顯示完整報告**。要存檔再用 `--out <path>`（可見、git 可追蹤的路徑，如 `docs/review-<branch>.md`），不要自動建檔。

4. **使用者明確 `--out <path>` → 一律寫該路徑**（並仍在對話顯示）。

## 重要
- **一定要產出實際的 review 報告**（不是「我會去審」）；單次完成、不繞 dispatch。
- CODE 模式至少要納入一項由 `get_code_dependencies` 查到的影響半徑觀察，IDEA 模式至少要撈一次記憶/SSOT——否則就退化成通用 review，失去 HAN 的脈絡價值。
- **不修改原始碼**。落地位置：PR/MR 留言（有則優先）或對話；`--out` 才寫檔。不製造隱藏檔堆。
