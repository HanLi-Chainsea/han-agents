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

    def test_bad_json_raises_not_silent(self, tmp_path):
        """壞 manifest ≠ 無 manifest：必須 raise 讓上層帶警告，不可靜默走 legacy。"""
        import pytest
        from servers.intent import load_manifest, ManifestError
        (tmp_path / 'intent-manifest.json').write_text('{not json', encoding='utf-8')
        with pytest.raises(ManifestError):
            load_manifest(str(tmp_path))


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
        assert c['anchor'] == 'member'
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
    ('class.src/test/java/Helper.java:Helper', 'class',
     'Helper', 'src/test/java/Helper.java', 1),   # 真實 test path（無前置目錄）→ 必須排除
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
        r = self._link([self._claim('member', 'create', 'GoodsOrderService')],
                       tmp_path)[0]
        assert r['matched'] is True and r['tier'] == 'method_scoped'
        assert r['locations'][0]['file'] == 'a/GoodsOrderService.java'

    def test_scope_collision_not_matched(self, mock_db_path, tmp_path):
        """codex 抓包案的結構性防護：同名方法存在於他 scope，不得命中。"""
        _seed_code_nodes(mock_db_path, 'ip', SEED)
        r = self._link([self._claim('member', 'create', 'OrderServiceImpl')],
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
        assert 'Drift（normative 文件斷言存在、code 找不到）: 1' in rep
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
        assert 'Drift（normative 文件斷言存在、code 找不到）: 1' in base  # 基線只有 MissingThing
        conn = sqlite3.connect(mock_db_path)
        conn.execute("UPDATE code_nodes SET name='GoodsOrderSvc', "
                     "id='interface.a/GoodsOrderService.java:GoodsOrderSvc' "
                     "WHERE name='GoodsOrderService' AND project='rp'")
        conn.commit(); conn.close()
        after = intent_drift_report('rp', str(tmp_path))
        assert 'Drift（normative 文件斷言存在、code 找不到）: 2' in after   # TP：改名被偵測
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
        out = cv.drift_report('rp', str(tmp_path))
        assert 'LEGACY_SENTINEL' in out
        assert '⚠️' in out and 'intent 引擎異常' in out   # 不得靜默偽裝成 legacy 正常


# =============================================================================
# codex round-2 修正的鎖死測試
# =============================================================================

class TestUnmeasuredNotDrift:
    """無法量測的來源絕不可充當 missing（codex Critical 1 / Major 5）。"""

    def _claim(self, anchor, symbol, scope=None, status='current'):
        return {'id': 'x', 'doc': 'd.md', 'line': 1, 'quote': '',
                'anchor': anchor, 'symbol': symbol, 'scope': scope,
                'status': status}

    def test_empty_graph_class_unmeasured(self, mock_db_path, tmp_path):
        from servers.intent import link_claims
        r = link_claims([self._claim('class', 'Anything')], 'empty', str(tmp_path))[0]
        assert r['matched'] is None and r['tier'] == 'unavailable'

    def test_unscoped_only_graph_member_unmeasured(self, mock_db_path, tmp_path):
        """regex-fallback 環境：只有無 scope 的 function id → member 無法驗證。"""
        from servers.intent import link_claims
        _seed_code_nodes(mock_db_path, 'rf',
                         [('function.x.java:create', 'function', 'create', 'x.java', 1),
                          ('class.x.java:X', 'class', 'X', 'x.java', 1)])
        r = link_claims([self._claim('member', 'create', 'X')], 'rf', str(tmp_path))[0]
        assert r['matched'] is None and r['tier'] == 'unavailable'

    def test_no_java_route_source_unmeasured(self, mock_db_path, tmp_path):
        from servers.intent import link_claims
        _seed_code_nodes(mock_db_path, 'nr', SEED[:1])
        r = link_claims([self._claim('route', '/v1/x/y')], 'nr', str(tmp_path))[0]
        assert r['matched'] is None and r['tier'] == 'unavailable'


class TestMemberWeakFallback:
    """Class.member 的 field 案：scoped method 比對失敗 → member 弱搜尋承接
    （codex Critical 2；spike tenantPoints 案的正反兩面）。"""

    def test_existing_field_matched_weak(self, mock_db_path, tmp_path):
        from servers.intent import link_claims
        _seed_code_nodes(mock_db_path, 'fw', SEED)
        dto = tmp_path / 'a' / 'GoodsOrderSubmitDto.java'
        dto.parent.mkdir(parents=True)
        dto.write_text('public class GoodsOrderSubmitDto {\n'
                       '  private Integer districtPoints;\n'
                       '  // private Integer ghostField;\n'
                       '}\n', encoding='utf-8')
        claims = [{'id': 'x', 'doc': 'd.md', 'line': 1, 'quote': '',
                   'anchor': 'member', 'symbol': 'districtPoints',
                   'scope': 'GoodsOrderSubmitDto', 'status': 'current'},
                  {'id': 'y', 'doc': 'd.md', 'line': 2, 'quote': '',
                   'anchor': 'member', 'symbol': 'tenantPoints',
                   'scope': 'GoodsOrderSubmitDto', 'status': 'current'},
                  {'id': 'z', 'doc': 'd.md', 'line': 3, 'quote': '',
                   'anchor': 'member', 'symbol': 'ghostField',
                   'scope': 'GoodsOrderSubmitDto', 'status': 'current'}]
        got = link_claims(claims, 'fw', str(tmp_path))
        assert got[0]['matched'] is True and got[0]['tier'] == 'member_weak'
        assert got[0]['locations'][0]['file'].endswith('GoodsOrderSubmitDto.java')
        assert got[1]['matched'] is False    # tenantPoints 不存在 → 真 drift 保留
        assert got[2]['matched'] is False    # 註解內的字不算存在


class TestRouteBaseDetection:
    """class 宣告之後的 @RequestMapping 是 method-level，不得當 prefix（codex Major 2）。"""

    def test_method_level_request_mapping_not_prefix(self, mock_db_path, tmp_path):
        from servers.intent import _scan_routes
        ctrl = tmp_path / 'BarController.java'
        ctrl.write_text('public class BarController {\n'
                        '  @RequestMapping("/m1")\n'
                        '  public void m1() {}\n'
                        '  @PostMapping(path = "/m2")\n'
                        '  public void m2() {}\n'
                        '}\n', encoding='utf-8')
        routes, n_sources = _scan_routes(str(tmp_path))
        assert n_sources == 1
        assert '/m1' in routes and '/m2' in routes
        assert '/m1/m2' not in routes        # 不得誤組 prefix


class TestStatusUpgrade:
    """同 symbol 先『建議』後『既有』→ status 必須升為 current（codex Major 3）。"""

    def test_later_current_wins(self, tmp_path):
        from servers.intent import extract_claims
        doc = tmp_path / 'd.md'
        doc.write_text('## 規劃\n- 建議使用 `TicketStrategy`\n'
                       '## 現況\n- 既有 `TicketStrategy` 已上線\n', encoding='utf-8')
        r = extract_claims(str(doc), 'd.md')
        c = [c for c in r['claims'] if c['symbol'] == 'TicketStrategy'][0]
        assert c['status'] == 'current'
        assert c['line'] == 4               # line 跟著最強 status 那次


class TestAuthorityGate:
    """非 normative 文件的 current+missing → needs_review，不產生 drift（codex Major 8）。"""

    def test_draft_doc_missing_goes_needs_review(self, mock_db_path, tmp_path):
        from servers.intent import intent_drift_report
        _seed_code_nodes(mock_db_path, 'ag', SEED)
        (tmp_path / 'docs').mkdir()
        (tmp_path / 'docs' / 'draft.md').write_text(
            '## 現況\n- 既有 `NonExistentThing` 服務\n', encoding='utf-8')
        (tmp_path / 'intent-manifest.json').write_text(json.dumps({
            'docs': [{'path': 'docs/draft.md', 'type': 'design',
                      'authority': 'draft', 'status': 'active'}]}), encoding='utf-8')
        rep = intent_drift_report('ag', str(tmp_path))
        assert 'Drift（normative 文件斷言存在、code 找不到）: 0' in rep
        assert 'NonExistentThing' in rep    # 出現在 needs_review 而非 drift


class TestWatermarkSemantics:
    def test_archived_doc_not_counted_unregistered(self, mock_db_path, tmp_path):
        """archived 是『已登記未啟用』，不得計入未登記（codex Minor）。"""
        from servers.intent import intent_drift_report
        _seed_code_nodes(mock_db_path, 'wm', SEED)
        (tmp_path / 'docs').mkdir()
        (tmp_path / 'docs' / 'old.md').write_text('x', encoding='utf-8')
        (tmp_path / 'intent-manifest.json').write_text(json.dumps({
            'docs': [{'path': 'docs/old.md', 'status': 'archived'}]}), encoding='utf-8')
        rep = intent_drift_report('wm', str(tmp_path))
        assert '未登記 md 檔: 0' in rep
        assert '0 active / 1 total' in rep

    def test_path_traversal_rejected(self, mock_db_path, tmp_path):
        from servers.intent import intent_drift_report
        _seed_code_nodes(mock_db_path, 'pt', SEED)
        (tmp_path / 'intent-manifest.json').write_text(json.dumps({
            'docs': [{'path': '../outside.md', 'status': 'active'}]}), encoding='utf-8')
        rep = intent_drift_report('pt', str(tmp_path))
        assert '路徑拒絕: ../outside.md' in rep


# =============================================================================
# codex round-3 修正的鎖死測試
# =============================================================================

class TestRound3Locks:
    def _claim(self, anchor, symbol, scope=None, status='current'):
        return {'id': 'x', 'doc': 'd.md', 'line': 1, 'quote': '',
                'anchor': anchor, 'symbol': symbol, 'scope': scope,
                'status': status}

    def test_bad_manifest_warns_via_cli(self, mock_db_path, tmp_path, monkeypatch):
        """壞 manifest → legacy + 顯式警告（不可偽裝成正常 legacy）。"""
        import servers.cli_views as cv
        from servers import drift as legacy
        (tmp_path / 'intent-manifest.json').write_text('{broken', encoding='utf-8')
        monkeypatch.setattr(legacy, 'get_drift_summary',
                            lambda p, d=None: 'LEGACY_SENTINEL')
        out = cv.drift_report('rp', str(tmp_path))
        assert 'LEGACY_SENTINEL' in out and '⚠️' in out

    def test_member_weak_java_only(self, mock_db_path, tmp_path):
        """非 Java 檔（註解語法不明）不得參與弱搜尋 → 杜絕 # 註解 false clean。"""
        from servers.intent import link_claims
        _seed_code_nodes(mock_db_path, 'jo', SEED)
        (tmp_path / 'Ghost.py').write_text('# ghostField mentioned in comment\n',
                                           encoding='utf-8')
        r = link_claims([self._claim('member', 'ghostField', 'Ghost')],
                        'jo', str(tmp_path))[0]
        assert r['matched'] is False

    def test_member_weak_exact_filename(self, mock_db_path, tmp_path):
        """檔名必須 == Scope.java：`User.name` 不得命中 NotUserService.java。"""
        from servers.intent import link_claims
        _seed_code_nodes(mock_db_path, 'ef', SEED)
        (tmp_path / 'NotUserService.java').write_text(
            'public class NotUserService { String name; }\n', encoding='utf-8')
        r = link_claims([self._claim('member', 'name', 'User')],
                        'ef', str(tmp_path))[0]
        assert r['matched'] is False

    def test_src_test_direct_level_excluded_from_scanners(self, mock_db_path, tmp_path):
        """src/test 直層（無前置目錄）的 Controller / member 檔必須被排除。"""
        from servers.intent import _scan_routes, link_claims
        d = tmp_path / 'src' / 'test'
        d.mkdir(parents=True)
        (d / 'TFooController.java').write_text(
            '@PostMapping("/t/only")\n', encoding='utf-8')
        (d / 'Dto.java').write_text('String testOnlyField;\n', encoding='utf-8')
        routes, n = _scan_routes(str(tmp_path))
        assert '/t/only' not in routes and n == 0
        _seed_code_nodes(mock_db_path, 'st', SEED)
        r = link_claims([self._claim('member', 'testOnlyField', 'Dto')],
                        'st', str(tmp_path))[0]
        assert r['matched'] is False

    def test_route_real_miss_is_drift_able(self, mock_db_path, tmp_path):
        """有 Java route 來源時，查無 = 真缺失（matched False），非 unmeasured。"""
        from servers.intent import link_claims
        _seed_code_nodes(mock_db_path, 'rm', SEED)
        (tmp_path / 'AController.java').write_text(
            'public class AController {\n  @PostMapping("/a/exists")\n'
            '  public void a() {}\n}\n', encoding='utf-8')
        got = link_claims([self._claim('route', '/a/exists'),
                           self._claim('route', '/a/missing')],
                          'rm', str(tmp_path))
        assert got[0]['matched'] is True
        assert got[1]['matched'] is False and got[1]['tier'] == 'route_exact'

    def test_route_location_line_exact(self, mock_db_path, tmp_path):
        """block comment 不得讓 route 行號偏移（行數保留）。"""
        from servers.intent import _scan_routes
        (tmp_path / 'LController.java').write_text(
            '/*\n multi\n line\n*/\npublic class LController {\n'
            '  @PostMapping("/l/x")\n  public void x() {}\n}\n', encoding='utf-8')
        routes, _ = _scan_routes(str(tmp_path))
        assert routes['/l/x'][0]['line'] == 6

    def test_report_unmeasured_not_drift_on_empty_graph(self, mock_db_path, tmp_path):
        """report 層：空圖譜 → current claim 進 unmeasured，drift 必須為 0。"""
        from servers.intent import intent_drift_report
        (tmp_path / 'docs').mkdir()
        (tmp_path / 'docs' / 'd.md').write_text(
            '## 現況\n- 既有 `SomeService` 服務\n', encoding='utf-8')
        (tmp_path / 'intent-manifest.json').write_text(json.dumps({
            'docs': [{'path': 'docs/d.md', 'status': 'active'}]}), encoding='utf-8')
        rep = intent_drift_report('emptyproj', str(tmp_path))
        assert 'Drift（normative 文件斷言存在、code 找不到）: 0' in rep
        assert 'unmeasured: 1' in rep

    def test_maps_built_once_for_multiple_docs(self, mock_db_path, tmp_path, monkeypatch):
        """多 doc 共用 maps/routes，不得每 doc 重算（codex M7 鎖定）。"""
        import servers.intent as intent
        _seed_code_nodes(mock_db_path, 'mb', SEED)
        (tmp_path / 'docs').mkdir()
        for n in ('a.md', 'b.md'):
            (tmp_path / 'docs' / n).write_text('- 既有 `TicketStrategy`\n',
                                               encoding='utf-8')
        (tmp_path / 'intent-manifest.json').write_text(json.dumps({
            'docs': [{'path': 'docs/a.md'}, {'path': 'docs/b.md'}]}), encoding='utf-8')
        calls = {'maps': 0, 'routes': 0}
        real_maps, real_scan = intent._build_code_maps, intent._scan_routes
        monkeypatch.setattr(intent, '_build_code_maps',
                            lambda p: calls.__setitem__('maps', calls['maps'] + 1) or real_maps(p))
        monkeypatch.setattr(intent, '_scan_routes',
                            lambda d: calls.__setitem__('routes', calls['routes'] + 1) or real_scan(d))
        intent.intent_drift_report('mb', str(tmp_path))
        assert calls == {'maps': 1, 'routes': 1}

    def test_symlink_escape_rejected(self, tmp_path):
        from servers.intent import _safe_doc_path
        outside = tmp_path / 'outside'
        outside.mkdir()
        (outside / 'secret.md').write_text('x', encoding='utf-8')
        proj = tmp_path / 'proj'
        proj.mkdir()
        os.symlink(str(outside / 'secret.md'), str(proj / 'link.md'))
        assert _safe_doc_path('link.md', str(proj)) is False


class TestRound4Locks:
    def test_malformed_doc_entry_raises(self, tmp_path):
        """docs 內非 dict 或 path 非法 → ManifestError（不可靜默產出乾淨報告）。"""
        import pytest
        from servers.intent import load_manifest, ManifestError
        for bad in ([123], [{'type': 'prd'}], [{'path': ''}], ['docs/a.md']):
            (tmp_path / 'intent-manifest.json').write_text(
                json.dumps({'docs': bad}), encoding='utf-8')
            with pytest.raises(ManifestError):
                load_manifest(str(tmp_path))

    def test_malformed_entry_warns_via_cli(self, mock_db_path, tmp_path, monkeypatch):
        import servers.cli_views as cv
        from servers import drift as legacy
        (tmp_path / 'intent-manifest.json').write_text(
            json.dumps({'docs': [123]}), encoding='utf-8')
        monkeypatch.setattr(legacy, 'get_drift_summary',
                            lambda p, d=None: 'LEGACY_SENTINEL')
        out = cv.drift_report('rp', str(tmp_path))
        assert 'LEGACY_SENTINEL' in out and '⚠️' in out

    def test_route_prefix_segment_boundary(self, mock_db_path, tmp_path):
        """`/api/foo/*` 不得命中 /api/foobar（segment 邊界）。"""
        from servers.intent import link_claims
        _seed_code_nodes(mock_db_path, 'pb', SEED)
        (tmp_path / 'PController.java').write_text(
            'public class PController {\n'
            '  @PostMapping("/api/foobar")\n  public void a() {}\n'
            '  @PostMapping("/api/foo/baz")\n  public void b() {}\n'
            '}\n', encoding='utf-8')
        claim = {'id': 'x', 'doc': 'd.md', 'line': 1, 'quote': '',
                 'anchor': 'route_prefix', 'symbol': '/api/foo',
                 'scope': None, 'status': 'current'}
        r = link_claims([claim], 'pb', str(tmp_path))[0]
        assert r['matched'] is True
        assert r['prefix_matches'] == ['/api/foo/baz']   # foobar 不得入列


class TestRound5Locks:
    def test_status_typo_raises(self, tmp_path):
        """status typo（acitve）不得靜默停用文件（false-clean 向量）。"""
        import pytest
        from servers.intent import load_manifest, ManifestError
        (tmp_path / 'intent-manifest.json').write_text(json.dumps({
            'docs': [{'path': 'docs/a.md', 'status': 'acitve'}]}), encoding='utf-8')
        with pytest.raises(ManifestError):
            load_manifest(str(tmp_path))

    def test_authority_typo_raises(self, tmp_path):
        """authority typo 不得靜默把 drift 降級成 needs_review。"""
        import pytest
        from servers.intent import load_manifest, ManifestError
        (tmp_path / 'intent-manifest.json').write_text(json.dumps({
            'docs': [{'path': 'docs/a.md', 'authority': 'normativ'}]}), encoding='utf-8')
        with pytest.raises(ManifestError):
            load_manifest(str(tmp_path))

    def test_status_typo_warns_via_cli(self, mock_db_path, tmp_path, monkeypatch):
        import servers.cli_views as cv
        from servers import drift as legacy
        (tmp_path / 'intent-manifest.json').write_text(json.dumps({
            'docs': [{'path': 'docs/a.md', 'status': 'acitve'}]}), encoding='utf-8')
        monkeypatch.setattr(legacy, 'get_drift_summary',
                            lambda p, d=None: 'LEGACY_SENTINEL')
        out = cv.drift_report('rp', str(tmp_path))
        assert 'LEGACY_SENTINEL' in out and '⚠️' in out

    def test_valid_enums_still_load(self, tmp_path):
        from servers.intent import load_manifest
        (tmp_path / 'intent-manifest.json').write_text(json.dumps({
            'docs': [{'path': 'a.md', 'status': 'archived', 'authority': 'draft'},
                     {'path': 'b.md'}]}), encoding='utf-8')
        m = load_manifest(str(tmp_path))
        assert len(m['docs']) == 2


class TestStatusIntentAware:
    """quick_status 必須反映 intent layer 現況，不得再推銷過時的 Project Skill 生成
    （v1 已定案：manifest 取代 skill 生成；init_project.py 也根本不會生成 skill）。"""

    def _qs(self, tmp_path):
        from servers.facade import quick_status
        return quick_status(str(tmp_path))

    def test_manifest_present_shown_no_legacy_warning(self, mock_db_path, tmp_path):
        (tmp_path / 'intent-manifest.json').write_text(json.dumps({
            'docs': [{'path': 'docs/a.md'},
                     {'path': 'docs/b.md', 'status': 'archived'}]}), encoding='utf-8')
        out = self._qs(tmp_path)
        assert 'Intent:' in out
        assert '1 active / 2 docs' in out
        assert 'Project Skill not found' not in out
        assert 'init_project.py' not in out

    def test_no_manifest_suggests_manifest_not_init_project(self, mock_db_path, tmp_path):
        out = self._qs(tmp_path)
        assert 'intent-manifest.json' in out      # 新路徑的建議
        assert 'init_project.py' not in out        # 過時且錯誤的建議必須退場

    def test_bad_manifest_surfaces_warning_without_crash(self, mock_db_path, tmp_path):
        (tmp_path / 'intent-manifest.json').write_text('{broken', encoding='utf-8')
        out = self._qs(tmp_path)
        assert '無法使用' in out or '無法解析' in out
        assert not out.startswith('Error:')
        # 壞 manifest ≠ 未配置：不得同時叫使用者「去放一個 manifest」
        assert '未配置' not in out
