# 為可測試性重構：`/han:refactor`（規劃）+ `/han:run`（通用執行）

- 日期：2026-06-15
- 狀態：設計已確認，待寫實作計畫
- 範圍：v1 = 新增兩個指令（`refactor`、`run`）+ 一個 recipe + 一個 playbook。**現有指令完全不動。**

## 1. 動機

資深工程師回饋：「以單元測試來說他不管架構，重點在沒做好單元拆分的程式鏈路拆乾淨，所以重構是沒錯的。你有把握的話其實可以寫直接重構，沒把握就先給出建議吧。」

也就是：有些既有程式把多個職責糾纏在一條呼叫鏈裡，導致**分支無法被獨立測試**。要先把「該拆而沒拆」的鏈路拆乾淨，讓每個單元（與其分支）可被獨立覆蓋。這是 `/han:unit-test` 的**前置步驟**，但屬於**不同職責**——`/han:unit-test` 保持純粹（只寫測試、不碰架構、不重構、不給重構建議）。

## 2. 核心設計原則

1. **每個功能拆簡單、職責分開**（使用者鐵律）。`unit-test` 不管架構；重構與重構建議是另一件事。
2. **規劃與執行分離**。規劃指令（refactor，未來 plan/feat）只產出任務樹；單一通用執行指令消費任務樹。對應 HAN 既有結構：recipe = 規劃、`get_next_dispatch` 迴圈 = 執行。
3. **被動自動浮現 > 選用指令**。HAN 的價值在自動／被動注入（playbook 自動注入派工 prompt、drift 自動路由），不靠人記得下指令。持久化只靠「會被自動讀到」的東西。
4. **信心閘控**。有把握（機械式、行為不變）→ 進可執行計畫直接改；沒把握（需語義判斷）→ 只出建議交人決定。
5. **安全網先行**。legacy code 通常沒測試，「測試保持綠」一開始不成立 → 先寫 characterization test 釘住現有行為，再重構（Michael Feathers legacy code 算法）。
6. **沿用既有護欄**：blast_radius 範圍限定、build.gradle / JDK 護欄。

## 3. 架構

```
/han:refactor <path>   ──產出──▶  ① 持久化任務樹 (epic, DB)   ──┐
  [純分析・不動原始碼]            ② 人類可讀計畫報告 (.han/...)  │
                                                                ▼
/han:run [epic_id]     ──消費任務樹──▶  executor→critic 派工迴圈（實際改碼）
  [通用・可接任何規劃之後]
```

復用 HAN 既有機制，新增物極少：

| 既有機制 | 復用方式 |
|---|---|
| `servers/tasks.py`（`tasks` + `task_dependencies`） | 任務樹持久化 + 自動稽核軌跡 + 三步相依鏈排序 |
| `servers/recipes.py` / `servers/tasks.py`（`create_task`） | 新增 `scan_refactor_candidates`（確定性掃描）；epic 由指令層用 `create_task` 建 |
| `servers/facade.py`（`get_next_dispatch`） | `/han:run` 的派工迴圈，**直接沿用、不改** |
| `servers/playbooks.py`（關鍵字注入） | 新增 `refactor.md`，依任務描述自動注入原則/checklist |
| Code Graph（`servers/code_graph.py`/`graph.py`） | 掃測試性熱點（方法長度、呼叫鏈深度、fan-out、巢狀） |
| blast_radius | 限定分析與重構範圍 |
| working_memory / checkpoints | run 期間自動共享、可續跑（內建，無須加工） |

## 4. 元件

> **規劃分兩層**：熱點偵測是**確定性**的（Python 可算）→ 放 recipe；型錄分類是**判斷**（需讀程式語義）→ 放指令層由主代理做。兩者不可混在一個 Python 函式裡。

### 4.1 recipe：`scan_refactor_candidates`（新增於 `servers/recipes.py`，純掃描、不建 epic）

- 輸入：`project_name`、`project_path`、`target_path`（可 None = 整個專案）。
- 流程（**全確定性，無 LLM 判斷**）：
  1. 用 Code Graph 在 `target_path` 範圍掃「可測試性熱點」候選：過長方法、呼叫鏈過深、fan-out 過高、巢狀過深。
  2. 取 **top-N** 熱點（預設 N 由實作決定，例如 20）；**若超過 N 必須在回傳訊息明講截斷數量**（不靜默截斷）。
  3. 被動提示：若同 `target_path` 已有 pending 的 refactor epic，回傳訊息提示既有 epic 狀態（查既有任務紀錄，不需新表）。
- 回傳：`{candidates: [{file, symbol, metrics, ...}], truncated, existing_pending_epic, message}`。
- **不分類、不建 epic、不修改原始碼。** 把判斷留給指令層。

### 4.1b epic 建立（在指令層、§4.3 內完成，非 recipe）

主代理拿到候選後，依注入的型錄（§4.2）逐一分類，再用 `servers/tasks.py` 的 `create_task`/`create_subtask` 建樹：
- **高把握** → epic 下建一個 story，story 下建三個相依 task：`characterization-test` → `refactor` → `verify`（用 `task_dependencies` 串成有序鏈）。
- **沒把握** → **不進 epic**，僅寫入報告（§4.4）的「建議／需人工決定」區，附理由。
- 若無候選或無高把握項，不建 epic（`epic_id` 為 None），仍輸出報告。

### 4.2 playbook：`reference/playbooks/refactor.md`（新增）

front-matter `match:` 關鍵字需與 recipe 產生的任務描述對齊，使 `get_next_dispatch` 自動注入。

**重構型錄（信心閘）：**

| 高把握（→ 可執行 epic，行為不變） | 沒把握（→ 只進報告建議） |
|---|---|
| Extract Method / Function | Introduce Interface / 依賴注入 |
| Extract / Introduce Variable | Move Method / Move Class（跨模組） |
| Inline Variable / 簡單 Inline Method | 改 public API 簽章 |
| Rename（區域 / private 符號） | 打斷共享可變狀態 / 全域 |
| Decompose Conditional | 繼承改組合 |
| Replace Magic Number/String with Constant | 動到並行 / IO 邊界 / 框架生命週期 |

共同判準——**高把握**：區域範圍、不改 public 契約、不重接依賴、行為可被 characterization test 釘住。**沒把握**：需語義判斷、跨模組 blast radius 大、無法安全釘住行為。

**Executor Principles（節錄要點）：**
- 重構必須**行為不變**（機械式轉換），只改結構不改可觀察行為。
- **characterization-test-first**：重構前先有釘住現有行為的測試並跑綠；改完重跑仍綠才算完成。
- characterization test 的職責是「釘住現在每個分支實際走的行為」，**不替工程師判斷 business 對錯**。
- 沿用 build.gradle / JDK 護欄（見 §6）。

**Critic Checklist（REJECT 條件）：**
- [ ] 行為被改變（characterization test 破裂或被竄改）→ REJECT
- [ ] 缺 characterization test 就直接重構 → REJECT
- [ ] 套用了型錄外 / 沒把握類重構 → REJECT
- [ ] 為通過而動 build.gradle / 改目標 JDK 版本 → REJECT（沿用既有護欄）

### 4.3 指令：`commands/han/refactor.md`（新增）

- 範圍解讀沿用 `unit-test.md`（路徑／模組名／自然語言／空白=全專案）。
- 步驟：
  1. 透過環境變數傳值（不內插進 Python 字串），呼叫 `scan_refactor_candidates(...)` 取候選 + 截斷資訊 + 既有 pending epic 提示。
  2. 讀 `reference/playbooks/refactor.md` 的型錄（單一事實來源），逐一分類候選為高/低把握。
  3. 高把握 → 用 `create_task`/`create_subtask` 建三步相依鏈；低把握 → 收進建議清單。
  4. 寫報告檔（§4.4）；輸出 `epic_id`、報告路徑、高把握任務數、沒把握建議數。
- **明確聲明：本指令只規劃、不改碼、不派工。** 要執行請接 `/han:run`。

### 4.4 指令：`commands/han/run.md`（新增，通用執行）

- `$ARGUMENTS` = `epic_id`；省略則查本專案**最新 pending epic**，並**先印出選了哪個 epic 與其描述**再執行（不靜默）。
- 迴圈：`get_next_dispatch(epic_id, ...)` → `action=='dispatch'` 用 **Agent 工具**派 `subagent_type`/`prompt` → 子代理完成後再 dispatch → 直到 `action != 'dispatch'`。
- playbook 由派工 prompt 依任務描述自動注入（refactor 任務→refactor playbook）。
- 收尾：完成幾條鏈、改了哪些檔、characterization test pass/fail 摘要。
- **通用性**：不綁定 refactor，對任何 recipe 產出的 epic 都能執行（未來 plan/feat 共用）。

### 4.5 報告格式 `.han/refactor-plan-<ts>.md`

- 標頭：target、掃描熱點數、截斷與否、epic_id。
- 區段 A「已排入計畫（高把握）」：每條鏈列出檔案/方法、重構型錄項、三步任務。
- 區段 B「建議／需人工決定（沒把握）」：列出位置、判定為沒把握的型錄項與**理由**。

## 5. 資料流（端到端）

1. `/han:refactor servers/foo` → recipe 掃熱點（確定性）→ 指令層主代理依型錄分類 → 用 `create_task` 寫 epic（高把握三步鏈）+ 報告（含建議）→ 印 `epic_id` + 報告路徑。
2. 工程師讀報告，確認要執行。
3. `/han:run <epic_id>` → 派工迴圈：每條鏈依序 `characterization-test`（executor 寫、critic 驗跑綠）→ `refactor`（executor 機械式改、critic 驗行為不變）→ `verify`（重跑 characterization test 仍綠）。
4. 任務狀態全程寫入 DB；`/han:status` 可見；working_memory/checkpoints 自動支援續跑。
5. 完成後工程師可接 `/han:unit-test` 對已拆乾淨的單元補正式測試（另一指令、另一職責）。

## 6. 既有護欄沿用（build.gradle / JDK）

refactor playbook 的 Executor Principles + Critic Checklist 納入既有護欄：遇 JDK/Gradle/依賴/CI 問題優先非侵入式處理；需改 root `build.gradle`/`gradle.properties`/`settings.gradle` 必須停止並標記人工確認；不得為通過而改目標 JDK 版本。理由：上雲版本固定，改版本會讓「上雲能不能跑」變未知數。

## 7. 錯誤處理

- 無候選 / 分類後無高把握項：指令層不建 epic（`epic_id=None`），仍輸出報告（可能只有建議區或空），訊息說明。
- `/han:run` 找不到 pending epic：回報並停止，不亂跑。
- 派工迴圈 `action` 為 `blocked`/`waiting`：回報 message 並停止。
- characterization test 寫不出來（行為無法釘住）：該條鏈視為**沒把握**，executor 應回報受阻、降級為建議，**不可硬重構**。
- 護欄觸發（需動 build.gradle）：停止該任務、標記人工確認。

## 8. 測試策略

- recipe（確定性、易單元測）：給含已知熱點的小型 fixture，驗證熱點偵測、top-N 截斷有明講、同路徑既有 pending epic 被動提示。
- epic 建立輔助：驗證高把握項建出 `characterization-test → refactor → verify` 三步相依鏈（`task_dependencies` 正確）、低把握不進 epic。
- playbook：`resolve_playbook` 能以 refactor 任務描述命中；`executor_section`/`critic_section` 含護欄與 characterization 條目。
- `/han:run`：epic_id 省略時選最新 pending 並印出；派工迴圈正確終止（done/blocked/waiting）。
- 不動現有指令測試。

## 9. 明確不做（YAGNI / 範圍外）

- **不改**現有 `unit-test`/`integration-test`/`e2e`/`review` 指令（「合併嵌入迴圈的重複」留待後續小改）。
- **不做**分支覆蓋率（另一獨立 unit-test 增強，已記於 `project-branch-coverage-unit-test`）。
- **不做**靠手動 `/han:recall` 才讀得到的 long_term_memory 寫入（手動記憶指令當未來靈活工具，另議）。
- **不做** plan / feat / 記憶 指令（同家族未來成員，本次只確立可容納它們的「規劃/執行分離」模式）。

## 10. 未來延伸（不在 v1）

- 把現有測試指令重構為 plan + 共用 run，消除嵌入迴圈重複。
- plan / feat / 記憶 指令，沿用同一規劃/執行分離模式。
- 分支覆蓋率（工具量測、非侵入式）作為 unit-test 成功指標。
- 再次分析同路徑時，自動帶出上次重構決策（自動注入，非手動 recall）。
