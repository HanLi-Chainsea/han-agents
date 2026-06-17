# 分支覆蓋率硬關（Branch-Coverage Gate）設計

> /han:unit-test 強化：把「本次 target 的分支有沒有測到」從 LLM 判讀，變成 `coverage.py` 工具量測的確定性 gate。

**Goal:** 讓 unit_test 派工迴圈在 executor 寫完測試後，用工具實際量測「本次任務 target 範圍內」的分支覆蓋率；有未覆蓋分支就以**具體行號**自動退回 executor 補測，不依賴 LLM 判讀。殺掉「覆蓋足夠與否靠人眼/LLM 判斷」的瓶頸。

**Architecture:** 新增單一職責模組 `servers/coverage.py`（跑 `coverage run --branch`、用**行範圍**把分支歸因到 target 函式）；`/han:unit-test` 的 dispatch 迴圈在 `get_next_dispatch` 回傳 critic dispatch 後、實際派 LLM critic 之前插入一道確定性 gate，未覆蓋時**改走既有 `finish_validation` 退件路由**（不另闢控制流）；資料層補 target 行範圍 metadata；playbook 補對應原則；對應測試。

**Tech Stack:** Python、`coverage.py`（`--branch`）、pytest、HAN recipe+playbook+executor/critic 派工。

**Scope:** 單一語言（Python）。Java/JaCoCo 為 v2，本 spec 不含、抽象層不預建（YAGNI）。

**審查狀態：** 經 dual-model gate（codex 後端權威 + gemini）審查後修訂，再依使用者「不過度工程」指示精簡：保留載重的 C1（資料流）/C2（finish_validation 路由），砍掉 AST scope 歸因（改行範圍過濾）與零分支 executed 檢查。**fail-state 分流（後續 codex 計畫審查修正）：** 早先曾把 fail-state 收斂成「ok / measure_failed(fail-open)」兩條，但那會把「pytest 失敗」「target 沒被測到」洗進 LLM critic 判讀——違反使用者「工具確認、非 LLM 宣稱」的核心意圖。故改回四態 `ok / tests_failed / no_targets / unavailable`：**只有 `unavailable`（coverage 未安裝等真正 infra）才 fail-open**；測試失敗與沒測到一律**確定性退件**。核心三件事不變：補 target 行範圍、`--branch` 量測、未覆蓋／失敗走 `finish_validation`。

---

## 背景與現況（已讀碼確認）

- `detect_coverage_gaps`（[servers/drift.py:355](../../../servers/drift.py)）是**靜態/圖譜式**：找「完全沒有測試」的 function，gap 帶 `file_path` + `line_start`，**無 `line_end`**。
- `recipe_unit_tests`（[servers/recipes.py:50](../../../servers/recipes.py)）建 task 時**只把 `file_path` + gap names 拼進 description**，丟掉結構化行範圍；且一個檔案的所有 gaps **batch 成單一 task**。
- `get_next_dispatch`（[servers/facade.py:1585](../../../servers/facade.py)）step 1：只要有 unvalidated task，**立刻 `reserve_critic_task()` 並回傳 critic dispatch**——迴圈拿回的就是 critic 指令，沒有「critic 之前」的天然空檔。
- `finish_validation(approved=False, issues=[...])`（[servers/facade.py:1333](../../../servers/facade.py)）已是完整退件機制：累加 `rejection_count`、達 `MAX_RETRIES` 轉 blocked + 開 human-review item、否則設 task `pending`/phase=`execution` 並回 `resume_executor`，`issues` 透過 `rejection_context` 進到 executor 重試 prompt。docstring 範例已用 `issues=['覆蓋率不足']`。

## 決策（已與使用者確認 + 審查修訂）

1. **工具鏈：Python 優先**，`coverage.py --branch`。純 CLI、非侵入。Java/JaCoCo 留 v2。
2. **Gate 政策：target-scoped 全覆蓋**。不設全檔百分比門檻；只針對**本次任務 target 函式 scope** 的分支——有未覆蓋 → REJECT。與 `blast_radius`/`target_path` 一致，不被範圍外 legacy 拖累。
3. **Gate 位置（C2 修訂）：迴圈驅動、走 `finish_validation` 路由**。dispatch 迴圈在 `get_next_dispatch` 回 critic dispatch 後、真正派 LLM critic **之前**先量測；未覆蓋 → 直接用那個已 reserve 的 `critic_task_id` 呼叫 `finish_validation(approved=False, issues=[行號])`（既有 bookkeeping 全部正確、不動 facade 控制流、不費 LLM token）；全覆蓋 → 照常派 LLM critic 做質性檢查。

## 元件（改動單元）

### 0. 資料層：補 target 行範圍 metadata（C1）

- `detect_coverage_gaps`：gap 補 `line_end`（由 Code Graph node 既有欄位取得；node 已存 `line_start`，確認/補 `line_end`）。輸出每 target：`{node_id, name, kind, file_path, line_start, line_end}`。
- `recipe_unit_tests`：建 task 時把該 task 的 target 清單**結構化存進 task metadata**（`coverage_targets` JSON 欄位），不再只靠 description 字串。一個 task 可含多個 target（沿用現有 batch-一檔一 task），gate 逐 target 量測與回報。

### 1. `servers/coverage.py`（新模組，單一職責）

```
measure_branch_coverage(project_path, test_targets, coverage_targets) -> {
    'tool_status': 'ok' | 'tests_failed' | 'no_targets' | 'unavailable',
    #   ok          測試全綠且 target 有被執行
    #   tests_failed pytest 有測試失敗（rc==1）           → 上游確定性退件
    #   no_targets   無測試被收集（rc==5）或 target 不在報告 → 上游確定性退件
    #   unavailable  coverage 未安裝 / 空 test_targets / pytest 中斷/內部錯(rc 2,3,4)/逾時 / json 失敗 → 上游 fail-open
    'fully_covered': bool,            # 所有 coverage_targets 行範圍內無未覆蓋分支
    'per_target': [ {                 # 逐 target（tool_status=='ok' 時）
        'file_path', 'name', 'line_start', 'line_end',
        'missing_branches': [{'from': int, 'to': int}],   # src_line 落在此 target 行範圍內
        'n_total': int, 'n_covered': int,
    } ],
    'error': str | None,              # 非 ok 時帶原因供退件 issue / 報告標記
}
```

**機制：**
1. **隔離資料檔（M5）：** 用 `tempfile.TemporaryDirectory()`；設 `COVERAGE_FILE=<tmp>/.coverage`（或 `coverage --data-file`），避免污染專案根與並行競態。
2. **執行（M6）：** `subprocess.run([sys.executable, '-m', 'coverage', 'run', '--branch', '--data-file', DATA, '-m', 'pytest', *test_targets], cwd=project_path, env=..., timeout=...)`——**list argv、不走 shell**；`test_targets`/`project_path` 先 canonicalize、限制在 project root 下且須實際存在；輸出截斷。**依 pytest 退出碼分流**：`rc==1`（有測試失敗）→ `tests_failed`；`rc==5`（未收集到測試）→ `no_targets`；`rc∈{2,3,4}` 或逾時 → `unavailable`；coverage 未安裝 / 空 test_targets → `unavailable`。
3. `coverage json --data-file DATA -o <tmp>/cov.json`；產製或解析失敗 → `unavailable`。json 須為 `dict` 且含 `files` dict（isinstance 防護未來格式異動），arc 只接受 `[int,int]` 形狀。
4. **路徑正規化（Minor）：** 對 `files` 的 key 建 canonical map（resolve 相對/絕對、`./` 前綴），對應 target `file_path`；對應不到（target 沒被 import/執行＝沒測到）→ `no_targets`（確定性退件，非 fail-open）。
5. **行範圍歸因（精簡，取代 AST）：** 把 coverage 的 branch arc `[src_line, dest]`，凡 `src_line ∈ [line_start, line_end]` 即歸入此 target。**已知限制：** target 行範圍內的巢狀 function/lambda 分支也會被算入——這是**偏保守的過度涵蓋**（要求多測，不會漏算），對 v1 gate 可接受；行範圍內過度糾纏的檔案本就該先走 `/han:refactor`。AST 精確 scope 留待 v2 視需要再加。
6. `fully_covered = (所有 target 的 missing_branches 皆空)`。

**非侵入：** 全程 `python -m coverage` CLI + 隔離 data file，不寫 `.coveragerc`/`pyproject`/`setup.cfg`；專案已有 `.coveragerc` 則沿用、不覆寫。

### 2. `/han:unit-test` dispatch 迴圈：確定性 gate（走 finish_validation）

迴圈每輪 `get_next_dispatch` 後判斷回傳。`subagent_type == 'critic'` 時**先別派 critic**，讀該原任務的 `coverage_targets` + executor 回報的 `test_targets`，呼叫 `measure_branch_coverage`，依結果分流（**確定性退件**為主，僅一類 fail-open）：

- **確定性退件**（走 `finish_validation(critic_task_id, original_task_id, approved=False, issues=[...])`，executor 自動 resume 帶具體行號，**不派 LLM critic、不費 token**）：
  - 量到且有未覆蓋分支（`ok && !fully_covered`）→ issues 帶 `servers/x.py 函式 f (L1-9)：分支未覆蓋 2→4 …`。
  - `tests_failed`（pytest 有測試失敗）→ issues 帶失敗摘要。**測試失敗由工具判定退件，不交給 LLM 宣稱。**
  - `no_targets`（沒收集到測試 / target 沒被執行）→ issues 要求補測或回報測試。
  - 推不出 `test_targets`（executor 沒用 `TEST_TARGETS:` marker 且 stem 後備找不到）→ issues 要求用 marker 明確回報測試檔。
  - **關鍵實作細節：** executor 重試 prompt 的 rejection context 來自 `working_memory['critic_suggestions']`（見 `_get_rejected_tasks`），而 `finish_validation` **不寫**此鍵。故 gate 退件時必須先 `set_working_memory(original_task_id, 'critic_suggestions', <issues 字串>)`，行號才會進到 executor 重試 prompt。
- **全覆蓋**（`ok && fully_covered`）→ 照常派 LLM critic 做質性檢查（AAA/測行為/命名）。
- **唯一 fail-open**（`unavailable`：coverage 未安裝 / pytest 中斷/內部錯 / json 失敗）→ 派 LLM critic，但 critic prompt 與收尾報告**大聲標記** `⚠️ 分支覆蓋率工具未量到（<error>），本任務回退人工逐分支核對`；LLM critic 既有 checklist 仍要求驗證測試實際執行。
- **無限退件防護：** 確定性退件走 `finish_validation` → 自動計入 `rejection_count`，達 `MAX_RETRIES` 轉 blocked + human-review（既有機制，免新增）。gate 迴圈另記已處理的 critic id，避免狀態未推進時的無限迴圈。未覆蓋退件的 issues 附帶提示：「若為真正不可達/防禦性分支，請用 `# pragma: no cover` 並註明理由」。

### 3. `reference/playbooks/unit-test.md`

- **Executor 原則（新增）：** 本次 target 的每條分支都要被測到；**確認為真正不可達/防禦性**的分支用 `# pragma: no cover`/`no branch` 標記並在回報**說明理由**（gate 尊重 pragma）。回報時**結構化列出本次新增/相關的 test 檔路徑**（供 gate 當 `test_targets`）。
- **Critic checklist（新增）：** 註明「本次 target 分支覆蓋已由工具在上游強制；若報告標記工具不可用，critic 須手動逐分支核對」。與 PR #14 null/None 原則協同（null 路徑即一條分支）。

### 4. `tests/`

- `measure_branch_coverage` 對 fixtures：
  - 基本：含 if/else + null 分支的 source + 只覆蓋部分分支的測試 → 抓到 target 行範圍內 missing_branches；
  - **行範圍過濾**：範圍外（其他函式）的 missing 分支**不**算進此 target；
  - **行範圍保守涵蓋**：target 範圍內巢狀 function 的未覆蓋分支**會**被算入（記錄此為已知/刻意行為，非 bug）；
  - pragma 被尊重（標記分支不計 missing）；
  - **fail-state 四分**：pytest 測試失敗 → `tests_failed`；target 不在報告/未收集到測試 → `no_targets`；coverage 未安裝 → `unavailable`；量到資料 → `ok`，皆不拋例外；
  - **退件資料流**：gate 退件時 `working_memory['critic_suggestions']` 被寫入，且未覆蓋行號真的出現在 executor 重試 prompt（端到端 assert，非只 assert subagent_type）；
  - **非侵入**：跑完專案根**無 `.coverage*` 殘留**。
- `test_targets` 歸因（M1）：既有測試已覆蓋的分支不被誤判未覆蓋（gate 的 `test_targets` 含該 source 既有測試 + 新測試）。
- 資料流（C1）：`detect_coverage_gaps` 回傳含 `line_end`；`recipe_unit_tests` 把 `coverage_targets` 寫進 task metadata 並可讀回。
- 指令 markdown 回歸測試：迴圈改用 `get_next_dispatch_gated`；critic 分支先跑 coverage、未覆蓋／失敗走 `finish_validation(approved=False)`、僅全覆蓋或 `unavailable` 才派 critic。

## 資料流（修訂）

```
recipe 建任務 → task.metadata.coverage_targets = [{file,name,line_start,line_end}]
  → executor 寫測試、跑、結構化回報 test_targets
  → get_next_dispatch 回 critic dispatch（已 reserve critic_task_id）
  → [coverage gate] measure_branch_coverage(test_targets, coverage_targets)
       ok & 未覆蓋 / tests_failed / no_targets / 推不出測試檔
            → set_working_memory(critic_suggestions=[行號/原因])  ← 行號進 executor 重試 prompt 的關鍵
            → finish_validation(critic_task_id, approved=False, issues=[...])  ← 確定性退件
            → 既有 bookkeeping：rejection_count++ / MAX_RETRIES→blocked / 否則 resume_executor(帶行號)
       ok & 全覆蓋        → 派 LLM critic 做質性檢查
       unavailable        → fail-open，派 LLM critic（報告大聲標記 error）
```

## 錯誤與邊界

| 情境 | 行為 |
|---|---|
| 量到資料 + target 有未覆蓋分支 | **確定性退件** `set_working_memory(critic_suggestions)` + `finish_validation(approved=False, issues=[行號])` |
| 量到資料 + 全覆蓋 | 派 LLM critic 做質性檢查 |
| pytest 測試失敗（`tests_failed`） | **確定性退件**（工具判定，非 LLM 宣稱）；issues 帶失敗摘要 |
| target 不在報告 / 未收集到測試 / 推不出測試檔（`no_targets`） | **確定性退件**；issues 要求補測或用 `TEST_TARGETS:` marker 回報測試檔 |
| coverage 未安裝 / json 產製失敗 / pytest 中斷·內部錯 / 逾時（`unavailable`） | **fail-open** 派 LLM critic（報告大聲標記 `error`）——唯一 fail-open 類別 |
| 防禦性/不可達分支 | 尊重 `# pragma: no cover`/`no branch`；executor 須說明理由 |
| 多任務並行 | 隔離 `COVERAGE_FILE`，無 `.coverage` 競態 |
| 退件無限迴圈 | 走 `finish_validation` → `MAX_RETRIES` 自動 blocked + human-review |

## 安全（明文化，M6）

- 在 project cwd 跑 pytest＝執行專案碼——**沿用 HAN executor 既有「已允許執行測試」的信任模型**；本 gate 不擴大信任邊界。
- `subprocess.run([...])` list argv、**不走 shell**（無 command injection）；`project_path`/`test_targets` canonicalize 並限制在 project root 下；加 `timeout` 與輸出截斷。

## 明確排除（YAGNI）

- Java / JaCoCo（v2；屆時才引入 coverage adapter 抽象層）。
- 全檔百分比門檻（只做 target-scoped 全覆蓋）。
- 覆蓋率歷史趨勢 / 持久化 DB。
- line / statement 覆蓋（只做 branch）。
- AST 精確 scope 歸因（v1 用行範圍；巢狀誤含為可接受的保守過度涵蓋，v2 視需要再加）。
- 零分支 target 的 executed 檢查（屬 statement 覆蓋範疇；無斷言測試由 critic 既有「不得空殼/恆真斷言」checklist 擋）。

## 護欄沿用

- **非侵入建置/工具設定**（不改 `build.gradle`/`.coveragerc`/`pyproject`）——與既有 build.gradle 護欄同精神。
- **值走環境變數、勿內插進 Python 字串**；coverage data 與 json 皆用隔離暫存、用後即刪。
