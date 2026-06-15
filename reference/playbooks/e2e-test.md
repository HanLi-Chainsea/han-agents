---
name: e2e_test
match: ["e2e", "end-to-end", "端對端", "端到端", "e2e test", "端對端測試"]
---

## Executor Principles
- 只測「關鍵使用者旅程」端到端（登入→操作→結果），數量少而精；瑣碎路徑歸 unit/integration（Test Pyramid 頂端）
- 真正跨越完整堆疊（UI → 後端 → DB），驗證使用者可見的行為與結果，而非實作細節
- 用穩定選擇器：優先 `data-test` / `data-testid` 這類專用屬性，不要綁易變的 CSS class / XPath / 文字（避免 flaky）
- 測試彼此獨立：每個測試自備初始狀態、不依賴其他測試的執行結果或順序
- 用自動等待 / web-first assertion，不要寫固定 `sleep`（固定延遲是 flaky 主因）
- 外部第三方服務以 stub/mock 取得決定性結果；專案內部堆疊用真實（接近正式）環境
- 清理副作用：測試後還原資料/狀態，確保可重複執行（Repeatable）
- 寫完必須實際執行並回報 pass/fail 與指令；失敗時保留 screenshot/trace 以便除錯
- **建置環境護欄（JDK / Gradle / 依賴 / CI）**：遇到相關問題時——(1) 優先非侵入式處理（補測試依賴、用既有版本、stub/mock）；(2) 若需改 root `build.gradle`/`build.gradle.kts`/`gradle.properties`/`settings.gradle`，**必須停止並標記人工確認**；(3) **不得為了測試通過而改變專案目標 JDK 版本**。原因：上雲版本固定，改版本會讓「上雲能不能跑」變未知數——寧可回報受阻也不動版本。

## Critic Checklist
- [ ] 是否真的端到端跨越堆疊（UI→後端→DB），而非偽裝成 E2E 的整合/單元測試
- [ ] 是否聚焦關鍵旅程、數量克制（非把所有路徑都塞 E2E）
- [ ] 選擇器是否穩定（data-test 類），避免易變的 CSS/XPath/文字
- [ ] 測試是否獨立、自備狀態、清理副作用、可重複
- [ ] 是否避免固定 sleep，改用自動等待 / web-first assertion
- [ ] 是否實際被執行且通過（附輸出，否則 REJECT）
- [ ] **未擅自改 build.gradle/gradle.properties/settings.gradle，且未為通過而變更目標 JDK 版本**（違反即 REJECT，破壞上雲版本一致性）
