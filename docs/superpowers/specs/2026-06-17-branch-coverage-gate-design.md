# 分支覆蓋率硬關（Branch-Coverage Gate）設計

> /han:unit-test 強化：把「本次 target 的分支有沒有測到」從 LLM 判讀，變成 `coverage.py` 工具量測的確定性 gate。

**Goal:** 讓 unit_test 派工迴圈在 executor 寫完測試後，用工具實際量測「本次任務 target 範圍內」的分支覆蓋率；有未覆蓋分支就以**具體行號**自動退回 executor 補測，不依賴 LLM 判讀。殺掉「覆蓋足夠與否靠人眼/LLM 判斷」的瓶頸。

**Architecture:** 新增單一職責模組 `servers/coverage.py` 跑 `coverage run --branch` 並把缺漏分支過濾到 target 行範圍；`/han:unit-test` 的 dispatch 迴圈在派 LLM critic 之前插入一道確定性前置關；playbook 補對應 executor 原則與 critic checklist；對應測試。

**Tech Stack:** Python、`coverage.py`（`--branch`，純 CLI、零專案設定侵入）、pytest、HAN recipe+playbook+executor/critic 派工。

**Scope:** 單一語言（Python）。Java/JaCoCo 為 v2，本 spec 不含。

---

## 背景與現況

- 現有 `detect_coverage_gaps`（[servers/drift.py](../../../servers/drift.py)）是**靜態/圖譜式**：找「完全沒有測試」的 function/flow，不量測 runtime 分支是否走到。
- 現有 critic 驗「測試有跑、有過」是**讀 executor 的文字輸出**（LLM 判讀），不是工具數字。
- `finish_validation(approved, issues=[...])`（[servers/facade.py](../../../servers/facade.py)）已是裁決入口，docstring 範例甚至已用 `issues=['覆蓋率不足']`——coverage 當退件理由是被預期的。

本功能補的就是中間缺的那塊：**runtime 分支量測 + 工具可驗證的硬關**。

## 決策（已與使用者確認）

1. **工具鏈：Python 優先**，`coverage.py --branch`。純 CLI、非侵入。Java/JaCoCo 留 v2、本版抽象層不預建（YAGNI）。
2. **Gate 政策：target-scoped 全覆蓋**。不設全檔百分比門檻；只針對**本次任務 target 的函式/檔行範圍**——其分支若有未覆蓋 → REJECT。與 HAN 既有 `blast_radius`/`target_path` 範圍一致，不被範圍外 legacy 舊債拖累。
3. **Gate 位置：指令迴圈內的確定性前置關**。新增 `measure_branch_coverage()`；dispatch 迴圈在派 critic 前先跑；未覆蓋直接 resume executor 附具體行號，不勞駕 LLM。facade 控制流不動（最小侵入）。

## 元件（4 個改動單元）

### 1. `servers/coverage.py`（新模組，單一職責）

```
measure_branch_coverage(project_path, test_targets, source_file, line_start, line_end)
  -> {
       'tool_available': bool,      # coverage 可跑且產出報告
       'fully_covered': bool,       # target 行範圍內無未覆蓋分支
       'missing_branches': [{'line': int, 'to': int}],  # 過濾到 target 範圍
       'n_total': int,              # target 範圍內分支總數
       'n_covered': int,
       'error': str | None,         # 不可用時的原因（給報告大聲標記）
     }
```

**機制：**
1. 在 `project_path` 下跑 `python -m coverage run --branch -m pytest <test_targets>`。
2. `python -m coverage json -o <唯一暫存檔>`（用 `mktemp`，避免競態；用後即刪）。
3. 讀 `files[source_file]['missing_branches']`（coverage.py 原生格式：`[[src_line, dest_line], ...]`）。
4. **只留 `src_line ∈ [line_start, line_end]` 的對**——即「本次 target 未覆蓋的分支」。
5. `n_total` 由 `summary.num_branches` 配合 `executed_branches`/`missing_branches` 在 target 範圍內計數；`fully_covered = (target 範圍內 missing_branches 為空)`。

**非侵入：** 全程 `python -m coverage` CLI，不寫 `setup.cfg`/`pyproject`/`.coveragerc` 等專案設定（若專案已有 `.coveragerc` 則沿用、不覆寫）。

### 2. `/han:unit-test` dispatch 迴圈：確定性前置關

executor 跑完該任務後、**派 LLM critic 之前**，迴圈跑一個 coverage bash 步驟（沿用 inline env 慣例：`HAN_PROJECT_PATH="$(pwd)" ... python3 - <<'PY'`）：

- `tool_available && !fully_covered` → **不派 critic**，直接 resume executor，issues 帶**具體未覆蓋行號**（如 `servers/x.py:42,57 分支未覆蓋`）。
- `fully_covered` → 照常派 LLM critic 做質性檢查（AAA / 測行為非實作 / 命名）→ `finish_validation`。
- `!tool_available` → **fail-open**：跳過硬關、照常派 LLM critic，但在收尾報告**大聲標記** `⚠️ 分支覆蓋率工具不可用，本任務回退人工判讀`。

target 的 `source_file` + `line_start`/`line_end` 來自 recipe 候選（`recipe_unit_tests` 已帶這些欄位）；實作計畫須確認此 metadata 有被帶到迴圈可讀處（任務記錄）。

### 3. `reference/playbooks/unit-test.md`

- **Executor 原則（新增）：** 用 `coverage --branch` 自測本次 target 的分支；未覆蓋的要補測；**確認為真正不可達/防禦性**的分支用 `# pragma: no cover` 或 `# pragma: no branch` 標記，並在回報說明理由（gate 尊重 pragma，不會逼測不可達碼）。
- **Critic checklist（新增）：** 註明「本次 target 分支覆蓋已由工具在上游強制」——與 PR #14 的 null/None 原則天然協同（null 路徑就是一條分支，工具會直接抓到漏測）。

### 4. `tests/`

- `measure_branch_coverage` 對 fixture（含 if/else 與 null 分支的小 source + 只覆蓋部分分支的測試）：
  - 斷言能抓到 target 範圍內的 missing_branches；
  - 斷言 `# pragma: no cover` 被尊重（標記行不計入 missing）；
  - 斷言 coverage 不可用時 `tool_available=False` 且不拋例外（fail-open）；
  - 斷言行範圍過濾正確（範圍外的 missing 不算進來）。
- 指令 markdown 回歸測試：coverage 步驟存在、且在 critic dispatch 之前。

## 資料流

```
recipe 建任務(target file + line range)
  → executor 寫測試、跑
  → [coverage 硬關] measure_branch_coverage(target)
       未覆蓋 → resume executor + 具體行號（確定性、無 LLM）
       全覆蓋 → LLM critic（質性）→ finish_validation(approved)
       工具不可用 → fail-open → LLM critic（報告大聲標記）
```

## 錯誤與邊界

| 情境 | 行為 |
|---|---|
| coverage 套件缺失 / pytest 跑不起來 / 報告無此 source | `tool_available=False`，fail-open 回退 LLM 判讀，報告大聲標記 |
| 防禦性 / 不可達分支 | 尊重 `# pragma: no cover`/`no branch`；executor 須在回報說明 |
| target 零分支 | `fully_covered=True`（trivial pass） |
| 專案已有 `.coveragerc` | 沿用、不覆寫（非侵入） |

## 明確排除（YAGNI）

- Java / JaCoCo（v2；屆時才引入 coverage adapter 抽象層）。
- 全檔百分比門檻（只做 target-scoped 全覆蓋）。
- 覆蓋率歷史趨勢 / 持久化 DB。
- line / statement 覆蓋（只做 branch）。

## 護欄沿用

- **非侵入建置設定**（不改 `build.gradle`/`.coveragerc`/`pyproject` 等）——與既有 build.gradle 護欄同精神。
- **值走環境變數、勿內插進 Python 字串**；暫存檔用 `mktemp`、用後即刪。
