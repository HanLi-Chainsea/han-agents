"""
HAN System - Intent Layer v1（doc-grounded 意圖層）

意圖來源 = 團隊真實開發文件（PRD/SA/SD，in-repo markdown），由專案根的
`intent-manifest.json` 註冊。本模組做四件事：

  load_manifest   讀 manifest（缺檔/壞檔 → None，呼叫端 fail-open 回 legacy SSOT）
  extract_claims  確定性 census：從 doc 抽「可驗證 claim」（backtick 符號錨點），
                  status 由措辭啟發式保守初標（無標記 → unlabeled，絕不臆測）
  link_claims     對 Code Graph（code_nodes）做 scoped 精確比對；route 用目標檔掃描；
                  abstain（unmeasured）是一級公民
  intent_drift_report  status-aware 車道報告：
                  drift（僅 normative 文件的 current+missing）/ doc_stale（proposed 但已存在）
                  / needs_review / unmeasured / coverage watermark

核心誠實原則（spike + 三輪 codex 審查內建）：
  - **無法量測 ≠ missing**：空圖譜、無 scoped method（regex fallback 環境）、
    非 Java route、const 無來源 → 一律 unmeasured，絕不報 drift
  - method 必須 `Class.method` scope 比對；scope 比對失敗時退 member 弱搜尋
    （`Order.items` 這類 field 與 method 在 backtick 中語法不可分）
  - 文件自評 status 不可信 → git 訊號解歧（注意：-S 是「文字首次出現」啟發式，
    非宣告點；僅作 advisory，只能把 unclear 升為 ok，不能製造 drift）
  - 同一 symbol 多處提及 → status 取最強（current > proposed > unclear > unlabeled）
  - 已知限制（v1）：status 是行粒度——「既有 A、建議新增 B」同行時兩者同 status；
    route 掃描僅支援 Java 單行 annotation（value=/path=）

零新依賴、零 DB 遷移。git 一律 argv list。manifest path 拒絕絕對路徑與 `..`。
"""

import json
import os
import re
import subprocess
from typing import Dict, List, Optional

MANIFEST_NAME = 'intent-manifest.json'

_UNCLEAR_MARKERS = ('若存在', '或拆出', '（示例）', '(示例)')
_CURRENT_MARKERS = ('既有', '已在', '已有', '目前', '現行', '舊：', '現況')
_PROPOSED_MARKERS = ('建議', '需新增', '需補', '必做', '提案', '可另闢',
                     '待補', '應新增', '需在', '拆出', '規劃')
_STATUS_RANK = {'current': 3, 'proposed': 2, 'unclear': 1, 'unlabeled': 0}

_BACKTICK_RE = re.compile(r'`([^`]+)`')
_HTTP_VERB_RE = re.compile(r'^(?:GET|POST|PUT|DELETE|PATCH)\s+', re.I)
_SCOPED_MEMBER_RE = re.compile(r'^([A-Z][A-Za-z0-9_]*)\.([a-z][A-Za-z0-9_]*)$')
_CONST_RE = re.compile(r'^[A-Z][A-Z0-9_]{4,}$')
_CLASS_RE = re.compile(r'^[A-Z][A-Za-z0-9]*$')
_ROUTE_RE = re.compile(r'^/[A-Za-z0-9_/{}.\-*]+$')
_HEADING_RE = re.compile(r'^#{1,6}\s+(.*)$')
_TEST_PATH_RE = re.compile(r'(^|/)src/test/')
_JAVA_CLASS_DECL_RE = re.compile(r'\b(?:class|interface|enum|record)\s+[A-Z]')

_ROUTE_ANNOT_RE = re.compile(
    r'@(?:Get|Post|Put|Delete|Patch|Request)Mapping\s*\(\s*(?:(?:value|path)\s*=\s*)?[\{]?\s*"([^"]+)"')
_CLASS_LEVEL_ROUTE_RE = re.compile(
    r'@RequestMapping\s*\(\s*(?:(?:value|path)\s*=\s*)?[\{]?\s*"([^"]+)"')


# =============================================================================
# manifest
# =============================================================================

def load_manifest(project_dir: str) -> Optional[Dict]:
    """讀專案根的 intent-manifest.json。缺檔/壞 JSON/形狀錯 → None（fail-open）。"""
    path = os.path.join(project_dir, MANIFEST_NAME)
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get('docs'), list):
            return None
        return data
    except Exception:
        return None


def _safe_doc_path(rel: str) -> bool:
    """manifest path 必須是專案內相對路徑（拒絕絕對路徑與 .. 跳脫）。"""
    if not rel or os.path.isabs(rel):
        return False
    parts = rel.replace('\\', '/').split('/')
    return '..' not in parts


# =============================================================================
# extract（確定性 census）
# =============================================================================

def _classify_span(span: str):
    span = _HTTP_VERB_RE.sub('', span.strip())  # `POST /x` → `/x`（文件常見寫法）
    m = _SCOPED_MEMBER_RE.match(span)
    if m:
        # 注意：Class.member 在 backtick 中無法區分 method 與 field，
        # link 階段先試 scoped method、再退 member 弱搜尋
        return ('member', m.group(2), m.group(1))
    if _CONST_RE.match(span) and '_' in span:
        return ('const', span, None)
    if _CLASS_RE.match(span) and any(c.islower() for c in span):
        return ('class', span, None)
    if _ROUTE_RE.match(span) and len(span) > 4:
        if span.rstrip('/').endswith('*'):
            return ('route_prefix', span.rstrip('*/'), None)
        return ('route', span, None)
    return None


def _line_status(line: str, section_status: str) -> str:
    if any(k in line for k in _UNCLEAR_MARKERS):
        return 'unclear'
    if any(k in line for k in _CURRENT_MARKERS):
        return 'current'
    if any(k in line for k in _PROPOSED_MARKERS):
        return 'proposed'
    return section_status or 'unlabeled'


def _section_status(heading: str) -> str:
    if any(k in heading for k in _PROPOSED_MARKERS):
        return 'proposed'
    if any(k in heading for k in _CURRENT_MARKERS):
        return 'current'
    return ''


def extract_claims(doc_abs: str, doc_rel: str) -> Dict:
    """確定性抽取。同一 (anchor,symbol,scope) 多處提及 → status 取最強
    （current>proposed>unclear>unlabeled），line/quote 跟著最強那次。"""
    try:
        text = open(doc_abs, encoding='utf-8', errors='replace').read()
    except Exception:
        return {'doc': doc_rel, 'claims': [], 'residual_lines': 0, 'extract_failed': True}

    by_key: Dict = {}
    order: List = []
    residual = 0
    section = ''
    in_fence = False
    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith('```'):
            in_fence = not in_fence
            continue
        if in_fence or not stripped:
            continue
        h = _HEADING_RE.match(stripped)
        if h:
            section = _section_status(h.group(1))
            continue
        if set(stripped) <= set('|-: '):
            continue

        status = _line_status(stripped, section)
        line_claims = 0
        for span in _BACKTICK_RE.findall(stripped):
            cls = _classify_span(span)
            if not cls:
                continue
            anchor, symbol, scope = cls
            key = (anchor, symbol, scope)
            if key in by_key:
                # status 升級（codex Major：首見不可永久決定 status）
                if _STATUS_RANK[status] > _STATUS_RANK[by_key[key]['status']]:
                    by_key[key].update(status=status, line=lineno,
                                       quote=stripped[:120])
            else:
                by_key[key] = {
                    'id': f'{doc_rel}:{lineno}:{len(order)}',
                    'doc': doc_rel, 'line': lineno, 'quote': stripped[:120],
                    'anchor': anchor, 'symbol': symbol, 'scope': scope,
                    'status': status,
                }
                order.append(key)
            line_claims += 1
        if line_claims == 0:
            residual += 1

    return {'doc': doc_rel, 'claims': [by_key[k] for k in order],
            'residual_lines': residual, 'extract_failed': False}


# =============================================================================
# link（對 Code Graph scoped 比對；無法量測 → unmeasured，絕不充當 missing）
# =============================================================================

def _build_code_maps(project: str) -> Dict:
    from servers.code_graph import get_code_nodes
    classes, scoped, member_scopes, consts = {}, {}, {}, {}
    offset = 0
    while True:
        page = get_code_nodes(project, limit=500, offset=offset)
        for n in page:
            fp = n.get('file_path') or ''
            if _TEST_PATH_RE.search(fp):   # codex Major：src/test/ 開頭也要排除
                continue
            loc = {'file': fp, 'line': n.get('line_start')}
            kind = n.get('kind')
            if kind in ('class', 'interface'):
                classes.setdefault(n['name'], []).append(loc)
            elif kind in ('function', 'method'):
                suffix = (n.get('id') or '').rsplit(':', 1)[-1]
                if '.' in suffix:
                    scoped.setdefault(suffix, []).append(loc)
                    member_scopes.setdefault(n['name'], set()).add(
                        suffix.rsplit('.', 1)[0])
            elif kind in ('constant', 'variable'):
                consts.setdefault(n['name'], []).append(loc)
        if len(page) < 500:
            break
        offset += 500
    return {'classes': classes, 'scoped': scoped,
            'member_scopes': member_scopes, 'consts': consts}


def _strip_java_comments(txt: str) -> List[str]:
    """保留行數（被吞的行以空字串佔位），route 行號才不會偏移。"""
    out, in_block = [], False
    for line in txt.splitlines():
        s = line
        if in_block:
            if '*/' in s:
                s = s.split('*/', 1)[1]
                in_block = False
            else:
                out.append('')
                continue
        while '/*' in s:
            pre, rest = s.split('/*', 1)
            if '*/' in rest:
                s = pre + rest.split('*/', 1)[1]
            else:
                s = pre
                in_block = True
                break
        out.append(s.split('//', 1)[0])
    return out


def _scan_routes(project_dir: str) -> Dict[str, List[Dict]]:
    """掃 *Controller*/*Feign* Java 檔的 active @*Mapping。
    class-level base 只認「class 宣告之前」的 @RequestMapping（codex Major：
    method-level @RequestMapping 不得被誤當 prefix）。"""
    routes = {}
    for root, dirs, files in os.walk(project_dir):
        dirs[:] = [d for d in dirs if d not in ('.git', 'node_modules')]
        if _TEST_PATH_RE.search(root.replace(project_dir, '', 1)):
            continue
        for fn in files:
            if not fn.endswith('.java') or ('Controller' not in fn and 'Feign' not in fn):
                continue
            p = os.path.join(root, fn)
            rel = os.path.relpath(p, project_dir)
            try:
                lines = _strip_java_comments(
                    open(p, encoding='utf-8', errors='replace').read())
            except Exception:
                continue
            base = ''
            for s in lines:
                if _JAVA_CLASS_DECL_RE.search(s):
                    break  # class 宣告後的 @RequestMapping 是 method-level
                m = _CLASS_LEVEL_ROUTE_RE.search(s)
                if m:
                    base = m.group(1).rstrip('/')
                    break
            for i, s in enumerate(lines, 1):
                for m in _ROUTE_ANNOT_RE.finditer(s):
                    sub = m.group(1)
                    full = (base + '/' + sub.lstrip('/')) if base and not sub.startswith(base) else sub
                    norm = re.sub(r'\{[^}]+\}', '{}', full.replace('//', '/').rstrip('/'))
                    routes.setdefault(norm, []).append({'file': rel, 'line': i})
    return routes


def _member_weak_search(symbol: str, scope: str, project_dir: str) -> List[Dict]:
    """member 弱搜尋：在檔名含 scope 的原始碼檔中（comment-stripped）找 symbol
    整字出現（field/方法皆可）。Class.member 的 field 案（如 Dto 欄位）由此承接。"""
    hits = []
    pat = re.compile(r'(?<![A-Za-z0-9_.])' + re.escape(symbol) + r'(?![A-Za-z0-9_])')
    for root, dirs, files in os.walk(project_dir):
        dirs[:] = [d for d in dirs if d not in ('.git', 'node_modules')]
        if _TEST_PATH_RE.search(root.replace(project_dir, '', 1)):
            continue
        for fn in files:
            if scope not in fn or not fn.rsplit('.', 1)[-1] in (
                    'java', 'py', 'ts', 'js', 'go', 'rs', 'kt'):
                continue
            p = os.path.join(root, fn)
            try:
                lines = _strip_java_comments(
                    open(p, encoding='utf-8', errors='replace').read())
            except Exception:
                continue
            for i, s in enumerate(lines, 1):
                if pat.search(s):
                    hits.append({'file': os.path.relpath(p, project_dir), 'line': i})
                    if len(hits) >= 5:
                        return hits
    return hits


def link_claims(claims: List[Dict], project: str, project_dir: str,
                _maps: Dict = None, _routes: Dict = None) -> List[Dict]:
    """比對 claims。_maps/_routes 可由呼叫端預建（report 對多 doc 共用，避免重算）。
    來源不可量測（空圖譜/無 scoped id/無 Java route 檔）→ tier='unavailable'。"""
    maps = _maps if _maps is not None else _build_code_maps(project)
    routes = _routes
    classes_available = bool(maps['classes'])
    scoped_available = bool(maps['scoped'])

    results = []
    for c in claims:
        r = dict(c)
        a = c['anchor']
        if a == 'class':
            if not classes_available:
                r.update(matched=None, tier='unavailable', locations=[])
            else:
                locs = maps['classes'].get(c['symbol'], [])
                r.update(matched=bool(locs), tier='class_exact', locations=locs[:3])
        elif a == 'member':
            if not scoped_available:
                # regex-fallback 環境（無 scoped id）：scope 無法驗證 → unmeasured
                r.update(matched=None, tier='unavailable', locations=[])
            else:
                key = f"{c['scope']}.{c['symbol']}"
                locs = maps['scoped'].get(key, [])
                if locs:
                    r.update(matched=True, tier='method_scoped', locations=locs[:3])
                else:
                    weak = _member_weak_search(c['symbol'], c['scope'], project_dir)
                    if weak:
                        r.update(matched=True, tier='member_weak', locations=weak[:3])
                    else:
                        r.update(matched=False, tier='method_scoped', locations=[],
                                 same_name_other_scopes=sorted(
                                     maps['member_scopes'].get(c['symbol'], set()))[:5])
        elif a == 'const':
            if not maps['consts']:
                r.update(matched=None, tier='unavailable', locations=[])
            else:
                locs = maps['consts'].get(c['symbol'], [])
                r.update(matched=bool(locs), tier='const_exact', locations=locs[:3])
        elif a in ('route', 'route_prefix'):
            if routes is None:
                routes = _scan_routes(project_dir)
            if not routes:
                r.update(matched=None, tier='unavailable', locations=[])
            else:
                norm = re.sub(r'\{[^}]+\}', '{}', c['symbol'].rstrip('/'))
                if a == 'route':
                    locs = routes.get(norm, [])
                    r.update(matched=bool(locs), tier='route_exact', locations=locs[:3])
                else:
                    hit = sorted(k for k in routes if k.startswith(norm))
                    r.update(matched=bool(hit), tier='route_prefix',
                             locations=[], prefix_matches=hit[:5])
        else:
            r.update(matched=None, tier='unknown', locations=[])
        results.append(r)
    return results


# =============================================================================
# status resolver（git 訊號；advisory only）
# =============================================================================

def _git(args: List[str], cwd: str) -> Optional[str]:
    try:
        out = subprocess.run(['git', '-C', cwd] + args,
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip() if out.returncode == 0 else None
    except Exception:
        return None


def _git_resolve(result: Dict, project_dir: str) -> Optional[str]:
    """symbol 首次出現於 code（git -S 文字啟發式，非宣告點）是否早於 doc 最後修改。
    僅 advisory：把 unclear/unlabeled 升為 ok；絕不據此產生 drift。"""
    if not result.get('matched') or not result.get('locations'):
        return None
    doc_ts = _git(['log', '-1', '--format=%ct', '--', result['doc']], project_dir)
    if not doc_ts:
        return None
    code_file = result['locations'][0]['file']
    log = _git(['log', '--format=%ct', '-S', result['symbol'], '--', code_file],
               project_dir)
    if not log:
        return None
    earliest = log.splitlines()[-1]
    try:
        return ('existed_before_doc' if int(earliest) <= int(doc_ts)
                else 'added_after_doc')
    except ValueError:
        return None


# =============================================================================
# report（status-aware 車道 + authority gate + watermark）
# =============================================================================

_STRONG_TIERS = ('class_exact', 'method_scoped', 'const_exact', 'route_exact')


def _verdict(r: Dict, project_dir: str) -> str:
    st, m, tier = r['status'], r.get('matched'), r.get('tier')
    if tier in ('unavailable', 'unknown'):
        return 'unmeasured'
    if st == 'current':
        if m:
            return 'ok'
        # authority gate（codex Major）：非 normative 文件不產生 drift
        if r.get('authority', 'normative') != 'normative':
            return 'needs_review'
        return 'drift'
    if st == 'proposed':
        if m and tier in _STRONG_TIERS:
            r['git_resolution'] = _git_resolve(r, project_dir)
            return 'doc_stale'
        return 'ok_proposed'
    if m:
        r['git_resolution'] = _git_resolve(r, project_dir)
        return 'ok' if r.get('git_resolution') == 'existed_before_doc' else 'cross_check'
    return 'needs_review'


def intent_drift_report(project: str, project_dir: str) -> Optional[str]:
    """manifest 驅動的 intent drift 報告。無 manifest → None（呼叫端走 legacy）。"""
    manifest = load_manifest(project_dir)
    if manifest is None:
        return None

    all_docs = [d for d in manifest['docs'] if isinstance(d, dict)]
    active_docs = [d for d in all_docs if d.get('status', 'active') == 'active']

    maps = _build_code_maps(project)            # 多 doc 共用（codex Major：勿重算）
    routes_cache = _scan_routes(project_dir)    # 同上；空 dict = 無 Java route 來源

    all_results, extract_failed, rejected_paths = [], [], []
    residual_total = 0
    for d in active_docs:
        rel = d.get('path', '')
        if not _safe_doc_path(rel):
            rejected_paths.append(rel)
            continue
        ext = extract_claims(os.path.join(project_dir, rel), rel)
        if ext['extract_failed']:
            extract_failed.append(rel)
            continue
        residual_total += ext['residual_lines']
        linked = link_claims(ext['claims'], project, project_dir,
                             _maps=maps, _routes=routes_cache)
        for r in linked:
            r['authority'] = d.get('authority', 'normative')
            r['verdict'] = _verdict(r, project_dir)
        all_results.extend(linked)

    lanes = {}
    for r in all_results:
        lanes.setdefault(r['verdict'], []).append(r)

    # watermark：未登記 md（registered 含 archived——已登記只是未啟用，非未登記）
    registered = {d.get('path') for d in all_docs}
    unregistered = 0
    for root, dirs, files in os.walk(project_dir):
        dirs[:] = [d2 for d2 in dirs if d2 not in ('.git', 'node_modules')]
        for fn in files:
            if fn.endswith('.md'):
                rel = os.path.relpath(os.path.join(root, fn), project_dir)
                if rel not in registered:
                    unregistered += 1

    def fmt(r):
        loc = r['locations'][0] if r.get('locations') else None
        where = f" → {loc['file']}:{loc['line']}" if loc else ''
        extra = ''
        if r.get('same_name_other_scopes'):
            extra = f"（同名見於: {', '.join(r['same_name_other_scopes'])}）"
        if r.get('git_resolution'):
            extra += f"（git: {r['git_resolution']}）"
        if r.get('tier') == 'member_weak':
            extra += "（弱比對：member 文字出現）"
        scope = f"{r['scope']}." if r.get('scope') else ''
        return (f"- [{r['anchor']}] `{scope}{r['symbol']}` "
                f"({r['doc']}:{r['line']}){where}{extra}")

    lines = [f"# Intent Drift Report — {project}", '']
    lines.append(f"## 🔴 Drift（normative 文件斷言存在、code 找不到）: "
                 f"{len(lanes.get('drift', []))}")
    lines += [fmt(r) for r in lanes.get('drift', [])]
    lines.append('')
    lines.append(f"## 📝 Doc-stale（文件標『提案』但 code 已存在 → 請更新文件）: "
                 f"{len(lanes.get('doc_stale', []))}")
    lines += [fmt(r) for r in lanes.get('doc_stale', [])]
    lines.append('')
    lines.append(f"## ❓ Needs-review（status 不明或非 normative 來源且 code 找不到）: "
                 f"{len(lanes.get('needs_review', []))}")
    lines += [fmt(r) for r in lanes.get('needs_review', [])]
    lines.append('')
    cc = lanes.get('cross_check', [])
    if cc:
        lines.append(f"## 🔍 Cross-check（已存在但無法以 git 確認時序）: {len(cc)}")
        lines += [fmt(r) for r in cc]
        lines.append('')
    lines.append('## 📊 Coverage Watermark')
    lines.append(f"- manifest 登記 docs: {len(active_docs)} active / {len(all_docs)} total"
                 f"（抽取失敗: {len(extract_failed)}"
                 f"{'：' + ', '.join(extract_failed) if extract_failed else ''}"
                 f"{'；路徑拒絕: ' + ', '.join(rejected_paths) if rejected_paths else ''}）")
    lines.append(f"- 未登記 md 檔: {unregistered}（未登記 = 未檢查 ≠ 無 drift）")
    lines.append(f"- claims: {len(all_results)}"
                 f"（ok: {len(lanes.get('ok', []))}, ok_proposed: {len(lanes.get('ok_proposed', []))}, "
                 f"unmeasured: {len(lanes.get('unmeasured', []))}——"
                 f"unmeasured = 來源不可量測（空圖譜/非 Java route/無 const 來源），非 clean）")
    lines.append(f"- residual 非錨點行（v1 未處理、待 v2 LLM staging）: {residual_total}")
    return '\n'.join(lines)
