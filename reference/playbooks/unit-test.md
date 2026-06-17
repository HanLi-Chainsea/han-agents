---
name: unit_test
match: ["unit test", "單元測試", "write tests for", "寫測試", "撰寫測試"]
---

## Executor Principles
- 以 AAA 組織每個測試：先 Arrange 準備、再 Act 執行、最後 Assert 驗證
- 遵守 FIRST：Fast（快）、Independent（彼此不依賴、不共享狀態）、Repeatable（多次執行結果一致，隔離外部依賴）、Self-validating（純 pass/fail，不需人工判讀）、Timely（緊貼被測程式）
- 測「可觀察行為與契約」：透過 public API、驗 state 而非與協作者的互動細節，重構不應使測試破裂
- 涵蓋 happy path、邊界值、錯誤/例外路徑、空輸入（Beyoncé Rule：重要行為就要有測試）
- **明確涵蓋 null / None / 空狀態**：當**本次任務範圍內**的參數、回傳或物件欄位可能為 null/None（含 Optional 未設值、集合為空、Map 取不到 key）時，**即使原規格沒寫明，也要寫一個測試把「目前 null 時的實際行為」釘住**——null 往往有對應行為（回傳預設值、短路、拋特定例外），漏測等於放掉一整類迴歸（同事使用回饋）。先讀程式判定 null 走哪條路再對應斷言：會拋例外→斷言例外**型別**（錯誤訊息僅在屬公開契約時才一併斷言，避免綁死實作）；有預設值／短路→斷言該行為；**目前 null 會導致非預期崩潰（NPE 等）→ 仍要寫一個用 assertRaises 釘住該崩潰的測試，並在回報中標記為待修缺口**。重點：null 行為一定要有測試覆蓋，不可默默跳過（即使是崩潰也以 assertRaises 釘住現況）。
- 一個測試只驗一個行為，命名描述「行為與預期」
- 寫完必須用專案 test_tool 實際執行，並在輸出回報 pass/fail 與執行指令
- 不得寫空殼或恆真斷言（assert True）來騙過驗證
- **建置環境護欄（JDK / Gradle / 依賴 / CI）**：遇到 JDK、Gradle、依賴缺失、CI 設定問題時——
  1. 優先使用非侵入式方式處理（補測試依賴用 testImplementation、用既有版本、mock 外部依賴），不動建置設定；
  2. 若需改 root `build.gradle`（或 `build.gradle.kts`、`gradle.properties`、`settings.gradle`），**必須停止並標記人工確認**，不要自行修改；
  3. **不得為了讓測試通過而改變專案目標 JDK 版本**（`sourceCompatibility`/`targetCompatibility`/`toolchain`/`languageVersion`）。
  > 為什麼：上雲時 JDK/Gradle 版本是固定的；若為了本地測試過而改版本，等於把「上雲能不能跑」變成未知數——本地綠燈不代表雲端可跑。寧可回報受阻，也不要動版本。
- **分支全覆蓋（工具強制）**：本次 target 函式的每一條分支（含 if/else、null/None 路徑、early return、except）都必須被測試走到。上游會用 `coverage --branch` 量測，未覆蓋的分支會帶**具體行號**自動退件。
- **結構化回報測試檔**：完成後在回報中**獨立一行**列出本次新增/相關的測試檔路徑，格式固定：`TEST_TARGETS: tests/test_x.py, tests/test_y.py`（相對專案根、逗號分隔）。這是覆蓋率 gate 用來決定要跑哪些測試的依據。
- **不可達分支**：確認為真正不可達/防禦性的分支，用 `# pragma: no cover`（或 `# pragma: no branch`）標記，並在回報**說明理由**；gate 會尊重 pragma、不計入未覆蓋。

## Critic Checklist
- [ ] 測試有實際被執行且全數通過（executor 須附執行輸出，否則 REJECT）
- [ ] 測的是行為/契約（public API、驗 state），而非實作細節
- [ ] 涵蓋錯誤路徑與邊界，而非只有 happy path
- [ ] **本次範圍內可為 null / None 的輸入、回傳或欄位都有對應測試**（釘住目前的 null 行為：預設值／短路／例外，或用 assertRaises 釘住目前的崩潰並標記缺口）；可為 null 卻完全沒測 → REJECT
- [ ] 符合 FIRST：獨立、可重複、自我驗證
- [ ] 斷言有意義且每測只驗一件事；命名表達行為與預期
- [ ] **未擅自修改 `build.gradle`/`build.gradle.kts`/`gradle.properties`/`settings.gradle`**；若有 diff 必須是人工確認過的（否則 REJECT）
- [ ] **未變更專案目標 JDK 版本**（sourceCompatibility/targetCompatibility/toolchain）以求測試通過（違反即 REJECT，因破壞上雲版本一致性）
- [ ] **分支覆蓋（上游已工具強制）**：本次 target 的分支覆蓋已由 `coverage --branch` 在派你之前強制；你拿到此任務代表已全覆蓋或工具不可用。**若 prompt 開頭標記「分支覆蓋率工具未量到」，你必須手動逐分支核對**（含 null/None 路徑——一條 null 分支即一條分支），未覆蓋則 REJECT。
