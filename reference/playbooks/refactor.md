---
name: refactor
match: ["refactor for testability", "characterization", "refactor", "重構"]
---

## Executor Principles
- **行為不變（behavior-preserving）**：只改結構不改可觀察行為；只套用機械式、可被測試釘住的重構。
- **characterization-test-first**：legacy code 通常沒測試，「測試保持綠」一開始不成立。重構前必須先有釘住「現在實際行為」的 characterization test 並跑綠；改完重跑仍綠才算完成。
- characterization test 的職責是「釘住現在**每個分支**實際走的行為」，**不替工程師判斷 business 對錯**（情境/斷言對錯是工程師的事）。
- 只做高把握型錄項（行為不變、區域範圍、不改 public 契約、不重接依賴）：
  Extract Method / Function、Extract / Introduce Variable、Inline Variable / 簡單 Inline Method、Rename（區域/private 符號）、Decompose Conditional、Replace Magic Number/String with Constant。
- 遇到沒把握項（Introduce Interface / 依賴注入、Move Method / Move Class、改 public API 簽章、打斷共享可變狀態/全域、繼承改組合、動到並行/IO/框架生命週期）→ **停止並回報受阻、降級為建議**，不可硬重構。
- characterization test 寫不出來（行為無法釘住）→ 視為沒把握，回報受阻，不重構。
- **建置環境護欄（JDK / Gradle / 依賴 / CI）**：遇相關問題時——(1) 優先非侵入式處理；(2) 若需改 root `build.gradle`/`build.gradle.kts`/`gradle.properties`/`settings.gradle`，**必須停止並標記人工確認**；(3) **不得為了測試通過而改變專案目標 JDK 版本**。原因：上雲版本固定，改版本會讓「上雲能不能跑」變未知數——寧可回報受阻也不動版本。

## Critic Checklist
- [ ] 重構前已有 characterization test 且**重構前後皆跑綠**（缺測試就重構 → REJECT）
- [ ] 行為未被改變（characterization test 未破裂、未被竄改放水 → 否則 REJECT）
- [ ] 只套用了高把握型錄項；未擅自做沒把握類重構（Introduce Interface、Move、改簽章、打斷依賴等 → 違反即 REJECT）
- [ ] characterization test 有釘住分支行為，而非空殼/恆真斷言（assert True → REJECT）
- [ ] **未擅自改 `build.gradle`/`build.gradle.kts`/`gradle.properties`/`settings.gradle`，且未為通過而變更目標 JDK 版本**（違反即 REJECT，破壞上雲版本一致性）
