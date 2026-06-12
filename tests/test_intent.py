"""Intent Layer v1 —— manifest / extract / link / status / report 契約測試。

紀律（沿用 cli_views 教訓）：斷言實際值（非標籤）；負向測試證明 drift 真的會紅。
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# =============================================================================
# Task 1: manifest（fail-open）
# =============================================================================

class TestLoadManifest:
    def test_loads_docs_with_fields(self, tmp_path):
        from servers.intent import load_manifest
        (tmp_path / 'intent-manifest.json').write_text(json.dumps({
            'docs': [{'path': 'docs/PRD.md', 'type': 'prd',
                      'authority': 'normative', 'status': 'active'}]
        }), encoding='utf-8')
        m = load_manifest(str(tmp_path))
        assert m is not None
        assert m['docs'][0]['path'] == 'docs/PRD.md'
        assert m['docs'][0]['authority'] == 'normative'

    def test_missing_file_returns_none(self, tmp_path):
        from servers.intent import load_manifest
        assert load_manifest(str(tmp_path)) is None

    def test_bad_json_returns_none(self, tmp_path):
        from servers.intent import load_manifest
        (tmp_path / 'intent-manifest.json').write_text('{not json', encoding='utf-8')
        assert load_manifest(str(tmp_path)) is None


# =============================================================================
# Task 2: extract_claims（確定性 census）
# =============================================================================

SAMPLE_DOC = """# 訂單遷移

## 現況
- 既有 `OrderServiceImpl.payOrder` 處理支付
- 目前介面 `GoodsOrderService` 定義訂單生命週期
- 逾時走 `ACTION_DELAY_CLEAR_GOODS_ORDER_TIMEOUT`
- 租戶端點 `/v1/tenant/order/list`

## 建議新增
- 拆出 `OrderServiceImpl.createGoodsOrder` 流程
- 新增 API `POST /internal/v1/tickets/from-order`

## 其他
- （若存在）`GoodsOrderAPIController` 一併停用
- `TicketStrategy` 沿用
- 這一行沒有任何錨點只是說明文字
"""


class TestExtractClaims:
    def _claims(self, tmp_path):
        from servers.intent import extract_claims
        p = tmp_path / 'doc.md'
        p.write_text(SAMPLE_DOC, encoding='utf-8')
        return extract_claims(str(p), 'doc.md')

    def _by_symbol(self, result, symbol):
        found = [c for c in result['claims'] if c['symbol'] == symbol]
        assert found, f"{symbol} 未被抽取；有的: {[c['symbol'] for c in result['claims']]}"
        return found[0]

    def test_scoped_method_current(self, tmp_path):
        c = self._by_symbol(self._claims(tmp_path), 'payOrder')
        assert c['anchor'] == 'method'
        assert c['scope'] == 'OrderServiceImpl'
        assert c['status'] == 'current'          # 行首「既有」
        assert c['line'] == 4

    def test_class_current(self, tmp_path):
        c = self._by_symbol(self._claims(tmp_path), 'GoodsOrderService')
        assert c['anchor'] == 'class'
        assert c['status'] == 'current'          # 「目前介面」

    def test_const_current(self, tmp_path):
        c = self._by_symbol(self._claims(tmp_path), 'ACTION_DELAY_CLEAR_GOODS_ORDER_TIMEOUT')
        assert c['anchor'] == 'const'
        assert c['status'] == 'current'          # section「現況」

    def test_route_current(self, tmp_path):
        c = self._by_symbol(self._claims(tmp_path), '/v1/tenant/order/list')
        assert c['anchor'] == 'route'

    def test_proposed_by_section(self, tmp_path):
        r = self._claims(tmp_path)
        c = self._by_symbol(r, 'createGoodsOrder')
        assert c['scope'] == 'OrderServiceImpl'
        assert c['status'] == 'proposed'         # section「建議新增」
        c2 = self._by_symbol(r, '/internal/v1/tickets/from-order')
        assert c2['status'] == 'proposed'

    def test_unclear_marker(self, tmp_path):
        c = self._by_symbol(self._claims(tmp_path), 'GoodsOrderAPIController')
        assert c['status'] == 'unclear'          # 「若存在」

    def test_unlabeled_default(self, tmp_path):
        c = self._by_symbol(self._claims(tmp_path), 'TicketStrategy')
        assert c['status'] == 'unlabeled'        # 無任何標記 → 保守

    def test_residual_counted(self, tmp_path):
        r = self._claims(tmp_path)
        assert r['residual_lines'] >= 1          # 「沒有任何錨點」那行

    def test_missing_doc_marks_failed(self, tmp_path):
        from servers.intent import extract_claims
        r = extract_claims(str(tmp_path / 'nope.md'), 'nope.md')
        assert r['extract_failed'] is True
        assert r['claims'] == []


# =============================================================================
# Task 3: link_claims（scoped 比對；碰撞不命中；abstain）
# =============================================================================

def _seed_code_nodes(db_path, project, rows):
    """rows: (id, kind, name, file_path, line)"""
    import sqlite3
    conn = sqlite3.connect(db_path)
    for nid, kind, name, fp, line in rows:
        conn.execute(
            """INSERT INTO code_nodes
               (id, project, kind, name, file_path, line_start, line_end, language)
               VALUES (?,?,?,?,?,?,?,?)""",
            (nid, project, kind, name, fp, line, line + 5, 'java'))
    conn.commit()
    conn.close()


SEED = [
    ('interface.a/GoodsOrderService.java:GoodsOrderService', 'interface',
     'GoodsOrderService', 'a/GoodsOrderService.java', 10),
    ('function.a/GoodsOrderService.java:GoodsOrderService.create', 'function',
     'create', 'a/GoodsOrderService.java', 12),
    ('function.b/Other.java:Other.create', 'function',
     'create', 'b/Other.java', 30),
    ('class.c/TicketStrategy.java:TicketStrategy', 'class',
     'TicketStrategy', 'c/TicketStrategy.java', 5),
    ('class.t/src/test/java/Helper.java:Helper', 'class',
     'Helper', 't/src/test/java/Helper.java', 1),   # test path → 必須排除
]


class TestLinkClaims:
    def _link(self, claims, tmp_path):
        from servers.intent import link_claims
        return link_claims(claims, 'ip', str(tmp_path))

    def _claim(self, anchor, symbol, scope=None, status='current'):
        return {'id': 'x', 'doc': 'd.md', 'line': 1, 'quote': '',
                'anchor': anchor, 'symbol': symbol, 'scope': scope,
                'status': status}

    def test_interface_class_matched_with_location(self, mock_db_path, tmp_path):
        _seed_code_nodes(mock_db_path, 'ip', SEED)
        r = self._link([self._claim('class', 'GoodsOrderService')], tmp_path)[0]
        assert r['matched'] is True and r['tier'] == 'class_exact'
        assert r['locations'][0] == {'file': 'a/GoodsOrderService.java', 'line': 10}

    def test_scoped_method_exact(self, mock_db_path, tmp_path):
        _seed_code_nodes(mock_db_path, 'ip', SEED)
        r = self._link([self._claim('method', 'create', 'GoodsOrderService')],
                       tmp_path)[0]
        assert r['matched'] is True and r['tier'] == 'method_scoped'
        assert r['locations'][0]['file'] == 'a/GoodsOrderService.java'

    def test_scope_collision_not_matched(self, mock_db_path, tmp_path):
        """codex 抓包案的結構性防護：同名方法存在於他 scope，不得命中。"""
        _seed_code_nodes(mock_db_path, 'ip', SEED)
        r = self._link([self._claim('method', 'create', 'OrderServiceImpl')],
                       tmp_path)[0]
        assert r['matched'] is False
        assert 'GoodsOrderService' in r['same_name_other_scopes']

    def test_test_path_excluded(self, mock_db_path, tmp_path):
        _seed_code_nodes(mock_db_path, 'ip', SEED)
        r = self._link([self._claim('class', 'Helper')], tmp_path)[0]
        assert r['matched'] is False    # 只存在於 /src/test/ → 不算 prod 存在

    def test_const_unavailable_when_no_const_nodes(self, mock_db_path, tmp_path):
        _seed_code_nodes(mock_db_path, 'ip', SEED)
        r = self._link([self._claim('const', 'SOME_CONST_NAME')], tmp_path)[0]
        assert r['matched'] is None and r['tier'] == 'unavailable'

    def test_route_scan_excludes_comments(self, mock_db_path, tmp_path):
        _seed_code_nodes(mock_db_path, 'ip', SEED)
        ctrl = tmp_path / 'src' / 'FooController.java'
        ctrl.parent.mkdir(parents=True)
        ctrl.write_text(
            '@RequestMapping("/v1/foo")\n'
            'public class FooController {\n'
            '  @PostMapping("/create")\n'
            '  public void create() {}\n'
            '  // @PostMapping("/dead")\n'
            '}\n', encoding='utf-8')
        got = self._link([self._claim('route', '/v1/foo/create'),
                          self._claim('route', '/v1/foo/dead')], tmp_path)
        assert got[0]['matched'] is True
        assert got[0]['locations'][0]['file'].endswith('FooController.java')
        assert got[1]['matched'] is False   # 註解掉的路由不得命中


# =============================================================================
# Task 4: git status resolver
# =============================================================================

class TestGitResolve:
    def test_symbol_existed_before_doc(self, tmp_path):
        from servers.intent import _git_resolve
        repo = tmp_path
        def git(*args):
            subprocess.run(['git', '-C', str(repo)] + list(args),
                           capture_output=True, check=True)
        git('init', '-q')
        git('config', 'user.email', 't@t'); git('config', 'user.name', 't')
        (repo / 'A.java').write_text('class A { void timeOutOrder() {} }',
                                     encoding='utf-8')
        git('add', 'A.java'); git('commit', '-q', '-m', 'code first')
        (repo / 'doc.md').write_text('`timeOutOrder` 已在 service 介面',
                                     encoding='utf-8')
        git('add', 'doc.md'); git('commit', '-q', '-m', 'doc later')
        result = {'matched': True, 'doc': 'doc.md', 'symbol': 'timeOutOrder',
                  'locations': [{'file': 'A.java', 'line': 1}]}
        assert _git_resolve(result, str(repo)) == 'existed_before_doc'

    def test_non_git_dir_returns_none(self, tmp_path):
        from servers.intent import _git_resolve
        result = {'matched': True, 'doc': 'doc.md', 'symbol': 'x',
                  'locations': [{'file': 'A.java', 'line': 1}]}
        assert _git_resolve(result, str(tmp_path)) is None


# =============================================================================
# Task 5: intent_drift_report（車道 + watermark）+ 負向測試
# =============================================================================

REPORT_DOC = """# 設計

## 現況
- 既有 `GoodsOrderService` 介面
- 既有 `MissingThing` 服務

## 建議新增
- 拆出 `GoodsOrderService.create` 流程

## 其他
- `UnknownGadget` 元件
"""


def _setup_report_project(tmp_path, mock_db_path):
    _seed_code_nodes(mock_db_path, 'rp', SEED)
    (tmp_path / 'docs').mkdir()
    (tmp_path / 'docs' / 'd.md').write_text(REPORT_DOC, encoding='utf-8')
    (tmp_path / 'stray.md').write_text('未登記', encoding='utf-8')
    (tmp_path / 'intent-manifest.json').write_text(json.dumps({
        'docs': [{'path': 'docs/d.md', 'type': 'design',
                  'authority': 'normative', 'status': 'active'}]
    }), encoding='utf-8')


class TestIntentDriftReport:
    def test_lanes_and_watermark(self, mock_db_path, tmp_path):
        from servers.intent import intent_drift_report
        _setup_report_project(tmp_path, mock_db_path)
        rep = intent_drift_report('rp', str(tmp_path))
        assert rep is not None
        # drift：current+missing（MissingThing）；且只有它
        assert 'Drift（文件斷言存在、code 找不到）: 1' in rep
        assert 'MissingThing' in rep
        # doc_stale：proposed 但 scoped method 已存在 → 非 drift
        assert 'Doc-stale' in rep and 'GoodsOrderService.create' in rep
        drift_sec = rep.split('Doc-stale')[0]
        assert 'create' not in drift_sec
        # needs_review：unlabeled+missing（UnknownGadget）→ 非 drift 非 clean
        assert 'Needs-review' in rep and 'UnknownGadget' in rep
        # watermark：未登記 md（stray.md）=1
        assert '未登記 md 檔: 1' in rep

    def test_no_manifest_returns_none(self, mock_db_path, tmp_path):
        from servers.intent import intent_drift_report
        assert intent_drift_report('rp', str(tmp_path)) is None

    def test_negative_rename_creates_drift(self, mock_db_path, tmp_path):
        """負向：把 code 節點改名 → 對應 current claim 必須翻成 drift（TP），
        其餘 ok claim 不得誤翻（FP=0）。"""
        import sqlite3
        from servers.intent import intent_drift_report
        _setup_report_project(tmp_path, mock_db_path)
        base = intent_drift_report('rp', str(tmp_path))
        assert 'Drift（文件斷言存在、code 找不到）: 1' in base  # 基線只有 MissingThing
        conn = sqlite3.connect(mock_db_path)
        conn.execute("UPDATE code_nodes SET name='GoodsOrderSvc', "
                     "id='interface.a/GoodsOrderService.java:GoodsOrderSvc' "
                     "WHERE name='GoodsOrderService' AND project='rp'")
        conn.commit(); conn.close()
        after = intent_drift_report('rp', str(tmp_path))
        assert 'Drift（文件斷言存在、code 找不到）: 2' in after   # TP：改名被偵測
        assert 'GoodsOrderService' in after.split('Doc-stale')[0]
        assert 'TicketStrategy' not in after.split('Doc-stale')[0]  # FP=0（控制組）


# =============================================================================
# Task 6: cli_views 路由（manifest → intent；否則 legacy；炸掉 fail-open）
# =============================================================================

class TestDriftRouting:
    def test_manifest_routes_to_intent(self, mock_db_path, tmp_path):
        from servers.cli_views import drift_report
        _setup_report_project(tmp_path, mock_db_path)
        out = drift_report('rp', str(tmp_path))
        assert 'Intent Drift Report' in out

    def test_no_manifest_falls_back_to_legacy(self, mock_db_path, tmp_path, monkeypatch):
        import servers.cli_views as cv
        from servers import drift as legacy
        monkeypatch.setattr(legacy, 'get_drift_summary',
                            lambda p, d=None: 'LEGACY_SENTINEL')
        assert 'LEGACY_SENTINEL' in cv.drift_report('rp', str(tmp_path))

    def test_intent_crash_fails_open_to_legacy(self, mock_db_path, tmp_path, monkeypatch):
        import servers.cli_views as cv
        import servers.intent as intent
        from servers import drift as legacy
        _setup_report_project(tmp_path, mock_db_path)
        def boom(p, d):
            raise RuntimeError('boom')
        monkeypatch.setattr(intent, 'intent_drift_report', boom)
        monkeypatch.setattr(legacy, 'get_drift_summary',
                            lambda p, d=None: 'LEGACY_SENTINEL')
        assert 'LEGACY_SENTINEL' in cv.drift_report('rp', str(tmp_path))
