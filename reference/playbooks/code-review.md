---
name: code_review
match: ["code review", "程式碼審查", "審查", "review the diff", "review changes", "審 code"]
---

## Executor Principles
- 最高原則：讓整體程式碼健康度隨時間提升；不追求完美，達到「明確讓系統更健康」即可通過
- 依序逐項看：Design（架構/整合是否合理）→ Functionality（含邊界、並行、像使用者一樣思考）→ Complexity（是否過度複雜、能否被快速理解）→ Tests（測試是否齊全且設計良好）→ Naming → Comments（解釋 why 而非 what）→ Style/Consistency（風格指南為準）→ Documentation
- 每行都看（Every Line），並理解周邊 Context 與影響範圍
- 也要肯定做得好的地方（Good Things）
- 每個發現給具體 file:line，分級 Critical / Warning / Info（Nit），並區分「必修」與「建議」

## Critic Checklist
- [ ] 是否涵蓋 Design / Functionality / Complexity / Tests 等核心面向
- [ ] 每個發現是否具體可定位（file:line）且分級
- [ ] 是否區分必修（阻擋合併）與建議（nit）
- [ ] 結論是否回答「此變更是否提升整體 code health」
