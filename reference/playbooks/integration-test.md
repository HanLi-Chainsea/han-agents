---
name: integration_test
match: ["integration test", "整合測試", "整合測", "整合測程式"]
---

## Executor Principles
- 測試焦點是「跨元件/跨模組在邊界上的真實協作」，而非孤立邏輯（孤立邏輯歸 unit test）
- 凡有序列化/反序列化、外部協作者（DB、API、queue）的讀寫，都要有整合測試驗證資料流與契約
- 外部依賴盡量在本地跑（容器 / infra 的 test double），不打正式環境
- 採 narrow integration test：聚焦單一邊界，端到端資料流走完整路徑
- 清理副作用（測試後還原狀態），確保 Repeatable
- 寫完必須實際執行並回報 pass/fail 與指令
- **建置環境護欄（JDK / Gradle / 依賴 / CI）**：遇到相關問題時——(1) 優先非侵入式處理（補測試依賴、用既有版本、test double）；(2) 若需改 root `build.gradle`/`build.gradle.kts`/`gradle.properties`/`settings.gradle`，**必須停止並標記人工確認**；(3) **不得為了測試通過而改變專案目標 JDK 版本**。原因：上雲版本固定，改版本會讓「上雲能不能跑」變未知數——寧可回報受阻也不動版本。

## Critic Checklist
- [ ] 測試是否真的跨越邊界（真實協作），而非偽裝成整合測試的 unit test
- [ ] 是否覆蓋序列化/反序列化、外部協作者的讀寫與 API 契約
- [ ] 是否實際被執行且通過（附輸出，否則 REJECT）
- [ ] 副作用是否清理、是否可重複執行
- [ ] **未擅自改 build.gradle/gradle.properties/settings.gradle，且未為通過而變更目標 JDK 版本**（違反即 REJECT，破壞上雲版本一致性）
