# Stack-Adaptive 分支覆蓋量測 + 整合測試把關 — 設計

> 狀態：設計審查中（codex 設計審查已做一輪：51/100 → 本 spec 為修正版）。
> 本次任務必須**全部做完並實跑驗證可行**（含 Java JaCoCo 分支覆蓋 spike），最後跑 CCG review。

## 目標（一句話）
讓 `/han:unit-test` 與 `/han:integration-test` 的「測試夠不夠」從人工判讀變成**工具可驗證、不准假綠**，且**同時支援 Python 與 Java**，依專案技術棧自動分派。

## 核心需求（使用者原話彙整）
1. **覆蓋率 = 邏輯分支覆蓋率（branch），不是行覆蓋率（line）**。Python=`coverage.py --branch`；Java=JaCoCo **`BRANCH` counter**。
2. **不准假綠**：工具無法確認就 fail-closed，絕不放行。
3. **stack-adaptive**：看技術棧決定 backend，Python 不放掉，Java 要補（同事都在用 Java）。
4. **不爆複雜度**：能簡單可靠就不要花俏。
5. **裸呼叫即用**：使用者只打 `/han:integration-test [scope]`，不宣告意圖；指令用 Claude 原生選項當場問情境。
6. **預設嚴謹**。

## 架構：一個共用量測層，兩個分開的 policy engine
codex 提醒：**共用 runner/report-parser 可以，但 policy 要分開**（unit branch 覆蓋 vs integration 邊界把關語義不同）。

```
servers/coverage/
  runner.py      # stack-adaptive：跑 scoped 測試、回傳原生結果 + 覆蓋資料位置
  jacoco.py      # Java backend：非侵入量測 + 解析 BRANCH counter（排除生成碼）
  pycov.py       # Python backend：coverage.py --branch（沿用既有 servers/coverage.py 邏輯）
  model.py       # 共用資料型別：TestRunResult / BranchCoverage / Boundary / Evidence
servers/gates/
  unit_branch.py        # policy：target 方法分支全覆蓋才過（沿用既有 unit-test gate）
  integration_gate.py   # policy：L1+L2 硬 gate、L3 advisory
```

> 既有 `servers/coverage.py`（PR #15）會被重構進 `servers/coverage/pycov.py` + `servers/gates/unit_branch.py`，行為不變、測試不退。

## 技術棧偵測
沿用 `_ensure_synced(...)` 回傳的 `tech`（含 `test_tool`）。
- `gradle` / `maven` / java 檔佔比高 → Java backend
- `pytest` / python → Python backend
- 偵測不到 backend → **fail-closed 回報**「此技術棧覆蓋未配置」，不放行。

---

## 整合測試把關（重設計，採 codex 修正）

### 邊界定義（收斂）
**只取 runtime 協作邊**：Code Graph 的 `injects`（Spring DI）+ 呼叫邊。
**移除** `imports/extends/implements`（codex Critical：import 非 runtime 協作；繼承非「呼叫協作者」）。
邊界 = 「受測類別 → 它注入/呼叫的協作者」。

### 三層
| 層 | 角色 | 判定 | 阻擋 |
|---|---|---|---|
| **L1** | 真跑真過 | 跑 scoped 測試（`gradlew :mod:test --tests …` / `pytest <files>`），解析**原生結果 XML**（JUnit/pytest）→ 確定性 pass/fail | **硬 gate** |
| **L2** | 抓假整合 | **static mock-smell**：掃測試碼，邊界協作者是否被 `@MockBean`/`@MockitoBean`/`@Mock`/`@InjectMocks`/`Mockito.mock`/`when(...).thenReturn`（Java）或 `unittest.mock`/`patch`（Python）替換 | **硬 gate** |
| **L3** | 覆蓋證據 | JaCoCo/coverage 的 **BRANCH** 覆蓋，當證據，**四分類** | **advisory（只報不擋）** |

### 為什麼 L2 用 static mock-smell 而非覆蓋率（codex Critical，本設計承重修正）
覆蓋率**證明不了「邊界是由目標測試、透過真 bean 走到的」**：同輪其他測試、Spring context 初始化、`@BeforeEach`、event listener、scheduled task、AOP advice、repository bootstrap 都會覆蓋被呼叫端 → 假綠。
→ 抓「假裝成整合的單元測試」最可靠的訊號是**測試碼把協作者 mock 掉了沒有**，這是 static、無污染、簡單。覆蓋率退為 advisory。

### L3 四分類（不可把「量不到」誤報成「假整合」）
- `verified-real`：邊界協作者未被 mock，且其分支在本測試集有被覆蓋。
- `mocked`：協作者被 mock-smell 命中（L2 已擋）。
- `not-observed`：未 mock，但覆蓋資料沒看到（可能污染/未走到）。
- `not-measurable`：proxy / interface-only / 生成碼 / 無對應 bytecode，覆蓋無法歸屬。

### fail-closed（消除自相矛盾）
- L1、L2 = **硬 gate**，不過即退件。
- L3 = **永不阻擋**（advisory）。
- 覆蓋 backend 量不到 → L3 標 `not-measurable` 並回報，**不影響 L1/L2 判決**，也絕不當 ✓。

### Evidence 輸出（codex Minor：不要只有 ✓/✗）
每條邊界輸出：test task、matched test classes、boundary source（誰→誰）、expected callee symbol、observed covered branches、coverage XML path、判定與**理由**。

---

## Java 非侵入覆蓋量測（硬約束：不改 build.gradle/settings.gradle/gradle.properties、不改 JDK）
優先序（codex 建議：先用現成，再 fallback）：
1. **若專案已有 JaCoCo report** → 直接讀（最穩）。
2. **否則**用 Gradle **init script**（`-I /han/.../jacoco-init.gradle`，外部檔）把 JaCoCo agent 掛進 test JVM 產 `.exec`，再用獨立 `jacococli.jar` 出 **XML**（含 BRANCH counter）。
3. 量測時**排除生成碼**：Lombok、MapStruct(`*Impl`)、`*MapperImpl`、annotation processor output；processs `synthetic`/`switch`/`lambda` 分支歸屬。
4. classpath/sourcepath 要精準（`build/classes/java/main` 等），拿錯寧可標 `not-measurable` 不可出錯誤覆蓋。

### 成本控制（codex Major：多模組整包很慢）
- 只跑**相關模組**的**指定測試**（`:mod:test --tests Pattern`），不跑 full build。
- L3 覆蓋為 advisory，可限制頻率 / 快取 report。

---

## unit-test 補 Java（Phase 2，本任務內完成）
- 沿用 `unit_branch.py` policy（target 方法分支全覆蓋），backend 換 JaCoCo BRANCH。
- 同樣 scoped 跑 target 測試類，降低同輪污染；排除生成碼。
- Python 行為不變。

---

## 互動（指令開場，Claude 原生選項）
`/han:integration-test [scope]` 一執行先問：
- **Q1 情境**：補現有系統整合測（預設）／驗收剛改的部分（範圍縮到 `git diff` 檔 + 對外邊界）／只看邊界清單不寫碼。
- 嚴格度不問，預設 L1+L2 硬 gate（最嚴）。

收尾報告：範圍內每條邊界 ✓ `verified-real` / ✗ `mocked` / ⚠️ `not-observed` / ➖ `not-measurable`，附 evidence。工具實測、非 LLM 宣稱。

---

## 驗證計畫（本任務必做，不可只宣稱）
### Fixture matrix（codex 指定，每個都要有預期 ✓/✗）
single-module、多模組、Spring Data repository、interface service + impl、CGLIB/JDK proxy、MapStruct、`@MockBean`、`@SpyBean`(partial)、Feign/WebClient、async/event listener。
另含 Python 對照 fixture（真協作 vs `patch` mock）。

### 必過驗證
1. Java JaCoCo init-script spike 在**真實多模組 Spring build**（aipoolserver 唯讀，不改檔；用獨立 fixture 專案驗 init-script 機制）實跑產出含 BRANCH 的 XML。
2. L1 對「貼假輸出」確定性退件。
3. L2 對 `@MockBean` 掉邊界協作者 → 退件；真 bean → 過。
4. L3 四分類在 proxy/repo/generated fixture 上不誤報 `mocked`。
5. 既有 419+ Python 測試不退、unit-test Python 行為不變。

---

## 階段（本任務內的建置順序，非延期）
- **Phase 1**：共用 runner + L1 + L2（兩棧）。Python+Java 的 run+pass 與 mock-smell。
- **Phase 2**：JaCoCo BRANCH advisory（L3）+ unit-test Java 分支覆蓋回補。先 spike 驗證再接上。

## 不做（YAGNI）
- 不做 runtime AOP/BeanPostProcessor probe（codex 提的更重方案，超出本次）。
- 不做 `@HanIntegrationBoundary` 顯式註解契約（保留為未來選項；先用 DI graph + mock-smell）。
- 不改任何建置/JDK 設定。

## 已知限制（codex 審查 11 輪後，PASS 等價於「結構性假綠全關 + 主流 mock 全覆蓋」，餘為非阻擋尾巴）
- **L2 static mock-smell 抓不到 bare 無型別 mock 注入**：Python `repo = Mock()`（無 spec、變數名 != 協作者）傳入 SUT 建構子無法靠 regex 連結到邊界——需 data-flow/AST。L1（測試真跑真過）仍把關此類測試；具名/帶 spec 的 mock 全數攔截。升級路徑：AST 追蹤 mock 變數流入 SUT。
- **L3 為 advisory**：逐 boundary 重跑 coverage（成本 O(boundaries×run)）、Java 未傳 test_filters、callee 用整檔範圍——L3 永不改變 verdict，僅作證據參考。
- **多模組 Gradle 未自動推導 `gradle_module`**：目前跑 root module；多模組專案需後續接線。
- 罕見 mock 語法尾巴（`patch(target=...)`、positional `Mock(C)`、`mockConstructionWithAnswer` 等）為非阻擋 limitation，可後續補強。

## 風險與緩解
| 風險 | 緩解 |
|---|---|
| init-script 在多模組踩坑（custom test task/parallel/configuration cache/既有 jacoco） | 先讀現成 report；spike 驗證；坑點逐一在 fixture matrix 覆蓋；失敗 → `not-measurable` 不假綠 |
| 覆蓋歸屬錯（classpath/generated/proxy） | 嚴格 classpath + 排除生成碼；不確定即 `not-measurable` |
| 成本爆 | 只跑相關模組+指定測試；L3 advisory 可降頻 |
| 複雜度 | runner/parser 共用、policy 分離；L3 先 advisory 不進硬 gate |
