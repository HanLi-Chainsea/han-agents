---
name: unit_test
match: ["unit test", "單元測試", "write tests for", "寫測試", "撰寫測試"]
---

## Executor Principles
- 以 AAA 組織每個測試：先 Arrange 準備、再 Act 執行、最後 Assert 驗證
- 遵守 FIRST：Fast（快）、Independent（彼此不依賴、不共享狀態）、Repeatable（多次執行結果一致，隔離外部依賴）、Self-validating（純 pass/fail，不需人工判讀）、Timely（緊貼被測程式）
- 測「可觀察行為與契約」：透過 public API、驗 state 而非與協作者的互動細節，重構不應使測試破裂
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
