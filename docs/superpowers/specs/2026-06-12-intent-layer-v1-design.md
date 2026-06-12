# Intent Layer v1 — doc-grounded 意圖層（取代手寫 flows/domains）

**Date**: 2026-06-12
**Status**: Approved direction（使用者全權委託）；經兩輪雙模型設計審查 + 真實專案 spike 驗證
**Spike 證據**: `/home/agent/han-intent-spike/`（aipoolserver, 1323 Java files, 62-claim census）

---

## 1. 問題與目標

現況 SSOT 意圖層要求人手寫 SKILL.md 的 flows/domains——實務上**沒人維護，形同虛設**。
目標：意圖改紮根於**團隊本來就會產的開發文件（PRD/SA/SD，in-repo markdown）**；
HAN 只維護一份薄 **intent-manifest** 註冊來源，意圖**自動抽取**並對 Code Graph 比對產生 drift。

## 2. Spike 已驗證的設計事實（v1 直接建立在這些之上）

| 事實 | 證據 |
|---|---|
| 確定性多型錨點（class/method/route/const/field）可解析符號密集設計文件的多數 claim | doc1 census 62 claims：41 anchored（66%）、15 residual（24%）、3 unclear、3 proposed |
| method 必須以 `Class.method` scope 比對，否則同名 collision 製造假象 | `createGoodsOrder` 案：存在於 `GoodsOrderController` ≠ 提案的 `OrderServiceImpl` |
| **文件自評 status 不可信**；git 訊號可解歧 | const 進 code 2025-12、doc 最後改 2026-04 → 寫時已存在；3/3 unclear 由 git/code-state 解掉 |
| 端到端能抓真 drift | `GoodsOrderSubmitDto.tenantPoints`：文件宣稱、code 全文無 tenant 欄位 |
| Code Graph 覆蓋缺陷會汙染一切下游 | Java interface 未抽取 → 16/17 假 drift；已修（fix/java-interface-extraction, 284 tests） |
| 未抽取 ≠ 無 drift | coverage watermark 必須是報告的一級公民 |

## 3. v1 範圍（砍刀）

**做**：
- `intent-manifest.json`（專案根；唯一要人維護的薄檔）：每份 doc 的 path / type(prd|sa|sd|design) / authority(normative|draft|non-normative) / status(active|archived)
- `servers/intent.py`：
  - `load_manifest(project_dir)`（缺檔 → fail-open 回 None，drift 走 legacy SSOT 路徑）
  - `extract_claims(doc_path)`：**確定性 census**（backtick 符號、`Class.method`、route 路徑、UPPER_SNAKE const、欄位清單；status 由措辭啟發式初標：建議/需新增/必做→proposed、若存在→unclear、既有/已在/目前→current）+ **residual 計數**（非錨點句段數，量化 LLM 區但 v1 不處理）
  - `link_claims(claims, project, project_dir)`：對 **code_nodes（Code Graph）** 比對——class/interface 精確、method 以 scoped id 後綴精確、route 用 comment-stripped 掃描（Code Graph 尚無 route）、const/field 用 comment-stripped word-search（標 weak tier）。**abstain 一級公民**：unscoped 同名多 scope → ambiguous 不判 matched
  - `resolve_status(claim, match, project_dir)`：unclear 或 proposed+matched 時用 git 訊號（symbol 進 code 時間 vs doc 最後修改）解歧；解不了就維持 unclear（誠實）
  - `intent_drift_report(project, project_dir) -> str`：markdown 報告含 ①drift（current+missing，附 per-symbol 證據與 file:line）②doc_stale 通道（proposed 但已存在 → 提醒更新文件，**不是 drift**）③unclear+git 解歧結果 ④**coverage watermark**（manifest 登記數/已抽取數/未登記 md 數/各 doc residual 數）
- `/han:drift` 升級：manifest 存在 → intent 引擎；否則 legacy（向後相容，零破壞）
- 信賴度分層由**建構**決定：exact-scoped=high、exact-unscoped/route=med、weak word-search=low——不是 LLM 自評

**不做（明確 defer v2）**：
- LLM 抽取殘餘語意 claim（需 staging 表 + 人工確認狀態機 → v2）
- DB 持久化 intent 表（v1 抽取是確定性+毫秒級，**每次 drift 即時重算，零 schema 遷移**；LLM staging 進來才需要表）
- manifest 自動 bootstrap（v1 手寫薄 JSON（han 零 yaml 依賴，沿用 stdlib-only 慣例）；`/han:status` 提示未登記的 md 檔）
- 移除 legacy SSOT/flows（v1 共存，之後另案棄用）

## 4. 與既有系統的關係
- 讀取面只依賴 `servers.code_graph`（get_code_nodes 等）與檔案系統；不動 project_nodes/edges schema
- `branch_flow` 等既有 ID 不受影響（v1 不生成 flow，迴避 identity-stability 風險——審查指出的 Major）
- git 呼叫一律 argv list、無 shell 內插（沿用 recipes 的安全模式）

## 5. 錯誤處理
- 無 manifest / 解析失敗 → fail-open 回 legacy 路徑
- doc 路徑不存在 → 該 doc 記為 extract_failed，計入 watermark，不中斷
- 非 git repo → status resolver 跳過 git 訊號，unclear 保持 unclear

## 6. 測試（TDD；契約鎖實際值，沿用 cli_views 教訓）
- manifest 載入/缺檔 fail-open
- extract_claims：固定樣本 doc → 斷言具體 claim（symbol/scope/status/line 實際值）；residual 計數
- link：seeded code_nodes（含 interface scoped method）→ matched 實際 file:line；scope collision 案（同名異 scope 不得命中）；ambiguous abstain
- status resolver：tmp git repo 造「code 先於 doc」案 → unclear→current
- drift report：current+missing → drift 含 per-symbol；proposed+exists → doc_stale 非 drift；watermark 數字正確
- 負向：把 seeded 節點改名 → drift 出現（TP）、控制組不誤報（FP=0）
