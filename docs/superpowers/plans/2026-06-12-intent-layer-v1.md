# Intent Layer v1 Implementation Plan

> Spec: docs/superpowers/specs/2026-06-12-intent-layer-v1-design.md
> 執行模式：inline TDD（執行者持有完整 spike 脈絡）；merge 前過雙模型 /ccg:review gate。

**Goal:** doc-grounded 意圖層 v1 — manifest 註冊 PRD/SA/SD，確定性抽取 claims，
對 Code Graph scoped 比對，status-aware drift 報告（含 doc_stale 通道與 coverage watermark）。

**Architecture:** 單一新模組 `servers/intent.py`（無新 DB 表、無新依賴、即時重算）；
`cli_views.drift_report` 路由：有 manifest → intent 引擎，無 → legacy SSOT。

---

## File Structure

| 檔案 | 動作 | 責任 |
|---|---|---|
| `servers/intent.py` | 新增 | manifest / extract / link / status-resolve / report（單一職責：意圖層讀取與比對） |
| `tests/test_intent.py` | 新增 | 契約測試（實際值斷言）+ 負向測試 |
| `servers/cli_views.py` | 修改 | `drift_report` 加 manifest 路由（fail-open 回 legacy） |
| `commands/han/drift.md` | 修改 | 說明兩種模式 |
| `SKILL.md` | 修改 | intent-manifest 一節 |

## 核心資料形狀（無 DB，純 dict）

```python
Claim = {
  'id': str, 'doc': str, 'line': int, 'quote': str,        # 溯源
  'anchor': 'class|method|const|route|route_prefix',
  'symbol': str, 'scope': str|None,                         # method 才有 scope
  'status': 'current|proposed|unclear|unlabeled',           # 措辭啟發式；unlabeled 為保守預設
}
LinkResult = Claim + {
  'matched': bool|None, 'tier': str, 'locations': [...],
  'ambiguous': bool,                                        # unscoped 多 scope → abstain
  'verdict': 'ok|drift|doc_stale|needs_review|unmeasured|ambiguous|cross_check',
}
```

**Verdict 規則（誠實車道，spike 驗證）**：
- `current` + missing → **drift**（唯一報 drift 的車道）
- `current` + matched → ok
- `proposed` + matched(strong tier) → **doc_stale**（提醒更新文件，非 drift）
- `proposed` + missing → ok（預期）
- `unclear`/`unlabeled` + matched → git 訊號解歧（code 先於 doc → 視同 current/ok 或 doc_stale）
- `unclear`/`unlabeled` + missing → **needs_review**（不是 drift，不是 clean）
- const 無 code_nodes 來源 → **unmeasured**（watermark 揭露）

## Tasks（TDD 順序）

1. **manifest**：`load_manifest(project_dir)` — 讀 `intent-manifest.json`；缺檔/壞 JSON → None（fail-open）。
   測試：正常載入欄位、缺檔 None、壞 JSON None。
2. **extract**：`extract_claims(doc_abs, doc_rel)` — 逐行掃 backtick spans：
   `Class.method`→method+scope；CamelCase→class；UPPER_SNAKE(≥5)→const；`/path`→route（尾 `*`→prefix）。
   status：行/節標題含 建議|需新增|需補|必做|提案|可另闢→proposed；若存在→unclear；
   既有|已在|目前|現行|舊：→current；否則 unlabeled。residual = 無錨點之非空 prose 行數。
   測試：固定樣本 doc → 斷言每個 claim 的 symbol/scope/status/line 實際值；residual 數。
3. **link**：`link_claims(claims, project, project_dir)` — 從 code_nodes 建 name→loc 與
   scoped-suffix→loc 映射（_fetch_all 分頁，排除 path 含 /src/test/）；route 用 comment-stripped
   掃描 `*Controller*.java`/`*Feign*.java`。
   測試：seeded code_nodes（interface + scoped method，模擬 extractor id 格式
   `function.<path>:<Class>.<method>`）→ matched 與 locations 實際值；
   **scope collision**：`A.foo` 存在、claim 要 `B.foo` → 不得命中且列 same-name scopes；
   unscoped 多 scope → ambiguous=True。
4. **status resolver**：`_git_resolve(claim, match, project_dir)` — argv-list git；
   symbol 進 code 時間（`git log --format=%ct -S symbol -- file` 最早）vs doc 最後修改
   （`git log -1 --format=%ct -- doc`）。code 先於 doc → resolved。非 git → skip。
   測試：tmp git repo 兩段 commit（先 code 後 doc）→ unclear→resolved。
5. **report**：`intent_drift_report(project, project_dir) -> str` — 串 1-4，markdown 輸出
   四節：Drift / Doc-stale / Needs-review / Coverage watermark（登記數、抽取成敗、未登記
   md 數、各 doc residual、unmeasured 數）。
   測試：端到端 fixture → drift 含 per-symbol file:line；doc_stale 不在 drift 節；
   watermark 數字正確。**負向**：改名 seeded 節點 → drift 出現（TP）、控制組仍 ok（FP=0）。
6. **路由**：`cli_views.drift_report` 加 manifest 判斷（測：有 manifest 走 intent、無走 legacy、
   intent 內部炸掉 fail-open 回 legacy）。
7. **文件**：drift.md + SKILL.md。
8. **驗收**：全套件 → /ccg:review（codex gate）→ 修 → merge。
