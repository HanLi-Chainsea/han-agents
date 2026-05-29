# Task Playbooks — 任務原則強化設計

**Date**: 2026-05-29
**Status**: Approved (pending spec review)
**Scope**: 讓使用者能呼叫 han-agents 完整跑完 unit test / code review / integration test 流程，並透過「任務原則 playbook」拉高品質。

---

## 1. 問題

現況（`servers/facade.py`）下，所有任務共用通用 prompt：

- `_build_executor_prompt`（facade.py:1860）給每個任務的指令都是通用三句：「讀檔案 → 執行 → 輸出結果」，不知道這是「寫 unit test」還是「改 bug」。
- `_build_critic_prompt`（facade.py:1940）驗證標準也是通用三條，不會確認「測試是否真的跑過」。

結果：
1. 測試任務可能產出「空殼測試」（`assert True`、未執行）。
2. 只有 `recipe_unit_tests` 一個入口（recipes.py:181），code review / integration test 無法一句話完整跑完。
3. `detect_coverage_gaps`（drift.py:355）靜態啟發式有盲點，且有靜默截斷 bug。

---

## 2. 目標與非目標

**目標**
- 使用者說「幫 X 寫單元測試 / code review 這次改動 / 幫 Y 寫整合測試」→ 完整跑完 Epic→Story→Task→驗證閉環。
- 每種任務注入專屬「原則」（executor）與「驗收清單」（critic），品質底線：**測試必須實際執行，critic 驗證有跑過，否則 REJECT**。
- 小幅修正 coverage-gap 偵測（借鑒 understand-anything 的 test 關係處理）。
- 清理本任務碰到的過時內容。

**非目標（明確排除，避免膨脹）**
- 不做 understand-anything 式的大型 Code Graph enrichment（語意 summary、多 edge 類型、layer 分層）。若未來要做，另開 spec。
- 不新增 e2e / refactor 等其他 playbook（v1 只做三種）。
- 不改任務 schema（不加欄位、不做 migration）。

---

## 3. 核心設計決策

### 3.1 playbook 用「描述關鍵字」掛到任務（非 metadata / 非新欄位）

playbook 與「任務怎麼產生」正交。注入點只有兩個（executor / critic prompt builder），分類依據是 `task.description`。recipe 與 PFC 產生的任務描述都長得像「Write unit tests for X」，因此**一個分類器涵蓋兩條來源**，零 schema 改動。

理由：`get_next_task()` 不回傳 metadata（tasks.py:332 的 SELECT 僅取 id/description/assigned_agent/priority）；改 SQL 或加欄位都比關鍵字分類更重、收益更低。

### 3.2 品質底線 = 真的跑 + critic 驗證有跑

unit_test / integration_test 的 executor 原則要求「用專案 test_tool 實際執行並回報 pass/fail 與指令」；critic 清單第一條即「測試有實際被執行且通過，否則 REJECT」。這是根治空殼測試的關鍵。

### 3.3 fail-open

playbook 目錄缺失或解析失敗 → `resolve_playbook` 回 `None`，prompt 維持原樣，**絕不擋任務**（與 repo 既有 guardrail 風格一致）。

---

## 4. 元件設計

### 元件 1 — `reference/playbooks/*.md`（3 檔）

固定結構，可被使用者直接編輯（skill 感）。**原則內容取自業界經檢驗、大公司遵循的最佳實踐**（出處見 §10），不自創。

#### `unit-test.md`（FIRST + AAA + Test Behavior，Google / Bob Martin / Kent Beck）

```markdown
---
name: unit_test
match: ["unit test", "單元測試", "write tests for", "寫測試", "撰寫測試"]
---

## Executor Principles
# 結構：AAA（Arrange-Act-Assert）— 每個測試三段清楚分明
- 以 AAA 組織：先 Arrange 準備、再 Act 執行、最後 Assert 驗證
# FIRST（Fast, Independent, Repeatable, Self-validating, Timely）
- Fast：測試要快；Independent：彼此不依賴、不共享狀態；Repeatable：多次執行結果一致（隔離外部依賴）；Self-validating：純 pass/fail，不需人工判讀
# 行為導向（Google: Test Behavior, Not Implementation）
- 測「可觀察行為與契約」，透過 public API，驗 state 而非與協作者的互動細節，重構不應使測試破裂
- 涵蓋 happy path、邊界值、錯誤/例外路徑、空輸入（Beyoncé Rule：重要行為就要有測試）
- 一個測試只驗一個行為，命名描述「行為與預期」
- 寫完必須用專案 test_tool 實際執行，並在輸出回報 pass/fail 與執行指令
- 不得寫空殼或恆真斷言（assert True）來騙過驗證

## Critic Checklist
- [ ] 測試有實際被執行且全數通過（executor 須附執行輸出，否則 REJECT）
- [ ] 測的是行為/契約（public API、驗 state），而非實作細節
- [ ] 涵蓋錯誤路徑與邊界，而非只有 happy path
- [ ] 符合 FIRST：獨立、可重複、自我驗證
- [ ] 斷言有意義且每測只驗一件事；命名表達行為與預期
```

#### `code-review.md`（Google eng-practices: Standard + What to look for）

```markdown
---
name: code_review
match: ["code review", "程式碼審查", "審查", "review the diff", "review changes", "審 code"]
---

## Executor Principles
# 最高原則：讓整體程式碼健康度隨時間提升（Google）
- 目標是「改善整體 code health」，不是追求完美；達到「明確讓系統更健康」即可通過
- 依 Google 檢查序逐項看：Design（架構/整合是否合理）→ Functionality（含邊界、並行、像使用者一樣思考）→ Complexity（是否過度複雜、能否被快速理解）→ Tests（測試是否齊全且設計良好）→ Naming → Comments（解釋 why 而非 what）→ Style/Consistency（風格指南為準）→ Documentation
- 每行都看（Every Line），並理解周邊 Context 與影響範圍
- 也要肯定做得好的地方（Good Things）
- 每個發現給具體 `file:line`，並分級 Critical / Warning / Info（Nit），區分「必修」與「建議」

## Critic Checklist
- [ ] 是否涵蓋 Design / Functionality / Complexity / Tests 等核心面向
- [ ] 每個發現是否具體可定位（file:line）且分級
- [ ] 是否區分必修（阻擋合併）與建議（nit）
- [ ] 結論是否回答「此變更是否提升整體 code health」
```

#### `integration-test.md`（Test Pyramid: Martin Fowler / Manning）

```markdown
---
name: integration_test
match: ["integration test", "整合測試", "整合測", "e2e 之外的整合"]
---

## Executor Principles
# 邊界導向：測真實元件在交界處能否協作（Fowler / Manning）
- 測試焦點是「跨元件/跨模組在邊界上的真實協作」，而非孤立邏輯（孤立邏輯歸 unit test）
- 凡有序列化/反序列化、外部協作者（DB、API、queue）的讀寫，都要有整合測試驗證資料流與契約
- 外部依賴盡量在本地跑（容器/test double of infra），不打正式環境
- 採 narrow integration test：聚焦單一邊界，端到端資料流走完整路徑
- 清理副作用（測試後還原狀態），確保 Repeatable
- 寫完必須實際執行並回報 pass/fail 與指令

## Critic Checklist
- [ ] 測試是否真的跨越邊界（真實協作），而非偽裝成整合測試的 unit test
- [ ] 是否覆蓋序列化/反序列化、外部協作者的讀寫與 API 契約
- [ ] 是否實際被執行且通過（附輸出，否則 REJECT）
- [ ] 副作用是否清理、是否可重複執行
```

### 元件 2 — `servers/playbooks.py`（約 80 行）

```python
load_playbooks() -> Dict[str, Playbook]   # 讀 reference/playbooks/*.md，解析快取
resolve_playbook(description: str) -> Optional[Playbook]  # 依 match 關鍵字分類
executor_section(pb) -> str               # 格式化成 prompt 區塊
critic_section(pb) -> str
```

- frontmatter 解析沿用 repo 既有輕量風格（手寫，不引入 PyYAML）。
- `resolve_playbook` 用較具體的 pattern，降低誤判（例如要求含 test/review 意圖詞）。
- 快取載入結果（模組級），playbook 檔案少、不變動頻繁。

### 元件 3 — 注入點（改 `facade.py` 兩函式）

- `_build_executor_prompt`：在 `## Instructions` 前插入 `executor_section`（若 `resolve_playbook` 命中）。
- `_build_critic_prompt`：把 `critic_section` 併入 `## Validation Criteria`（若命中）。
- 未命中 → 完全維持現狀（向後相容）。

### 元件 4 — 擴充 `servers/recipes.py`

新增兩個 recipe，與 `recipe_unit_tests` 同形（回 `epic_id` 供 `get_next_dispatch` 消費）：

- `recipe_code_review(project, path, target_path=None, diff_base="HEAD")`
  - 目標來源：`git diff --name-only <diff_base>`（預設）或 `target_path`。
  - **第一次/邊界處理**：
    - 非 git repo 或無 commit → 不跑 diff，要求 `target_path`，否則回 `task_count=0` + 明確訊息。
    - 有 repo 但無 diff → 改用 `target_path`；若也沒給 → `task_count=0` + 提示「請指定 target_path 或先有改動」。
  - 一檔一 story，task 描述「Code review `<file>`」。
- `recipe_integration_tests(project, path, target_path=None)`
  - 以模組/目錄為單位分組（非單一 function）。
  - task 描述「Write integration tests for `<module>`」。

兩者 `requires_validation=True`，註冊進 `RECIPES`。
`recipe_unit_tests` 主流程不變（gap 偵測保留），品質提升靠 playbook。

### 元件 5 — 前門整合

- `reference/agents/pfc.md`：列出三個 recipe + 觸發語句對照，讓 PFC 路由使用者意圖。
- `SKILL.md`：更新 recipe 範例與清單。

---

## 5. Code Graph 小改（限 `detect_coverage_gaps`，drift.py:355）

借鑒 understand-anything 的 test 關係處理，限定一個函式範圍：

1. **修靜默截斷 bug**：目前 `get_code_nodes(limit=1000)` / `get_code_edges(kind='tests', limit=500)` 在大專案會靜默漏檔少報缺口。改為分批迴圈取完所有節點/邊（或在達到上限時於回傳摘要明確標示「結果不完整」）。
2. **test 關係更可靠**：understand 把測試當 first-class `tested_by` 邊；HAN 只認 `kind='tests'`。改為同時認 `tests` 與 `tested_by` 兩種 edge kind。
3. **納入 method 節點**：`important_kinds` 由 `{function, class, api}` 擴充為含 `method`（測試單位常是方法）。
4. 維持跳過 private、跳過測試檔（不擴大到 private —— 屬主觀範圍，避免膨脹）。

---

## 6. 清理（限本任務碰到範圍）

- `SKILL.md:163` `Available Recipes: unit_tests (more coming)` → 更新為三個 recipe。
- `recipes.py` 的 `SCHEMA` docstring（行 14-34）→ 補上 `recipe_code_review` / `recipe_integration_tests`，更新 `Available`。
- `recipes.py` 既有 SCHEMA 與實作若有不同步處一併校正。
- 不做無關重構（例如不動 regex backend）。

---

## 7. 錯誤處理

| 情境 | 行為 |
|------|------|
| playbook 目錄缺失/解析失敗 | `resolve_playbook` 回 None，prompt 原樣（fail-open） |
| code_review 無 git / 無 commit | 要求 target_path，否則 task_count=0 + 明確訊息 |
| recipe 找不到目標（無 diff / 無 gap） | 回 task_count=0 + 明確訊息（沿用既有模式） |
| coverage 節點達上限 | 分批取完；無法取完時摘要標示不完整（不靜默） |

---

## 8. 測試（pytest，鏡像現有 `mock_db_path` fixture）

新增 `tests/test_playbooks.py`：

1. **載入**：3 個 playbook 都載入成功，含必要欄位。
2. **分類**：`resolve_playbook("write unit tests for x") → unit_test`；`"code review the diff" → code_review`；`"write integration tests for auth" → integration_test`；`"fix bug in parser" → None`（防誤注入）。
3. **注入**：對測試任務 `_build_executor_prompt` 含「邊界/錯誤路徑」字樣、`_build_critic_prompt` 含「必須實際被執行」；對 `"fix bug"` 任務**不含**測試原則。
4. **fail-open**：monkeypatch playbook 目錄不存在 → prompt builder 不報錯、回原樣。
5. **recipe**：`recipe_code_review`（用 target_path 模式，免 git）與 `recipe_integration_tests` 在 mock project 上建出 Epic→Story→Task，且 task 描述能被 `resolve_playbook` 正確分類（閉環驗證）。

新增 `tests/test_drift.py` 案例（或既有檔擴充）：
6. **gap 偵測**：`tested_by` 邊也能算覆蓋；`method` 節點會被納入；節點數超過單批上限時不漏報。

驗收：`pytest tests/test_playbooks.py tests/test_drift.py` 全綠，且 `pytest tests/`（既有）無回歸。

---

## 9. 影響檔案總覽

| 檔案 | 動作 |
|------|------|
| `reference/playbooks/unit-test.md` | 新增 |
| `reference/playbooks/code-review.md` | 新增 |
| `reference/playbooks/integration-test.md` | 新增 |
| `servers/playbooks.py` | 新增 |
| `servers/facade.py` | 改 2 函式（注入） |
| `servers/recipes.py` | 加 2 recipe + 更新 docstring |
| `servers/drift.py` | `detect_coverage_gaps` 小改 |
| `reference/agents/pfc.md` | 更新路由說明 |
| `SKILL.md` | 更新 recipe 清單/範例 |
| `tests/test_playbooks.py` | 新增 |
| `tests/test_drift.py` | 擴充 |

設計原則：注入點集中、向後相容、fail-open、不動 schema、清理限本任務範圍。

---

## 10. Playbook 原則來源（經檢驗的業界最佳實踐）

playbook 內容非自創，取自下列權威來源：

**Unit test**
- Google Testing Blog — *Test Behavior, Not Implementation*：https://testing.googleblog.com/2013/08/testing-on-toilet-test-behavior-not.html
- *Software Engineering at Google*, ch.11 Testing Overview（測 public API、驗 state、Beyoncé Rule）：https://abseil.io/resources/swe-book/html/ch11.html
- FIRST principles（Fast/Independent/Repeatable/Self-validating/Timely，源自 R.C. Martin）：https://github.com/tekguard/Principles-of-Unit-Testing
- AAA pattern（Bill Wake 2001 / Kent Beck, *TDD by Example*）：https://semaphore.io/blog/aaa-pattern-test-automation

**Code review**
- Google Engineering Practices — *The Standard of Code Review*：https://google.github.io/eng-practices/review/reviewer/standard.html
- Google Engineering Practices — *What to look for in a code review*：https://google.github.io/eng-practices/review/reviewer/looking-for.html

**Integration test**
- Martin Fowler — *The Practical Test Pyramid*：https://martinfowler.com/articles/practical-test-pyramid.html
- Vladimir Khorikov — *Unit Testing Principles, Practices, and Patterns*, ch.8 Why integration testing：https://livebook.manning.com/book/unit-testing/chapter-8/
