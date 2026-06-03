---
description: 'HAN：帶專案脈絡的審查。吃 code 或想法/設計，對著 Code Graph + SSOT + 記憶批判並產出分級報告'
---

# /han:review — 帶脈絡的審查（code 或想法）

對 `$ARGUMENTS` 做**一次性批判並直接產出報告**。與 recipe 不同：**不建任務樹、不派工、不寫檔**——你（模型）讀取 HAN 的 Code Graph / SSOT / 記憶當脈絡後，直接寫出分級 review。

> 與 ccg:review 的差異：ccg 是通用雙模型；`/han:review` 的價值是**專案脈絡**——對著「實際架構、相依關係、過往決策」審。不要做成多模型 fan-out。

## 模式判定
- `$ARGUMENTS` 是路徑 / 含程式碼 / 空白 → **CODE 模式**
- `$ARGUMENTS` 是一段想法/設計/提案的敘述 → **IDEA 模式**

先 Bash：`PROJECT_PATH=$(pwd)`、`PROJECT=$(basename "$PROJECT_PATH")`。

---

## CODE 模式

1. 取得變更：`git diff HEAD`（或使用者指定的 base / 路徑；空白就審當前工作區 diff）。
2. 拉「影響半徑」脈絡——對變更檔的節點查相依/被呼叫：
```bash
python3 - <<'PY'
import sys; sys.path.insert(0, "{{HAN_DIR}}")
from servers.code_graph import get_code_nodes, get_code_dependencies
# 對每個變更檔取其節點，再查 1-hop 相依，評估 blast radius
nodes = get_code_nodes("<PROJECT>", file_path="<CHANGED_FILE>", limit=50)
for n in nodes[:20]:
    print(n['kind'], n['id'])
PY
```
3. 讀審查原則：`reference/playbooks/code-review.md`（在 `{{HAN_DIR}}`）。
4. 依 Google 檢查序（Design→Functionality→Complexity→Tests→Naming→Comments→Style→Docs）逐項審，**並結合影響半徑**指出下游風險。
5. 輸出**分級報告**：
   - `Critical`（必修才能合併）/ `Major` / `Minor` / `Suggestion`
   - 每項附 `file:line` 與具體修正；附「影響範圍」（哪些相依節點會被波及）
   - 結論回答：此變更是否提升整體 code health。

---

## IDEA 模式

1. 撈專案脈絡與相關過往經驗：
```bash
python3 - <<'PY'
import sys; sys.path.insert(0, "{{HAN_DIR}}")
from servers.memory import search_memory
for m in search_memory("<關鍵詞，取自想法>", project="<PROJECT>", limit=8) or []:
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

## 重要
- **一定要產出實際的 review 報告**（不是「我會去審」）；單次完成、不繞 dispatch。
- CODE 模式至少要納入一項 Code Graph 影響半徑觀察，IDEA 模式至少要撈一次記憶/SSOT——否則就退化成通用 review，失去 HAN 的脈絡價值。
- 只讀不寫：不修改任何檔案。
