"""
HAN System - Intent Layer v1（doc-grounded 意圖層）

意圖來源 = 團隊真實開發文件（PRD/SA/SD，in-repo markdown），由專案根的
`intent-manifest.json` 註冊。本模組做四件事：

  load_manifest   讀 manifest（缺檔/壞檔 → None，呼叫端 fail-open 回 legacy SSOT）
  extract_claims  確定性 census：從 doc 抽「可驗證 claim」（backtick 符號錨點），
                  status 由措辭啟發式保守初標（無標記 → unlabeled，絕不臆測）
  link_claims     對 Code Graph（code_nodes）做 scoped 精確比對；route 用目標檔掃描；
                  abstain（ambiguous/unmeasured）是一級公民
  intent_drift_report  status-aware 車道報告：
                  drift（僅 current+missing）/ doc_stale（proposed 但已存在）
                  / needs_review（unlabeled|unclear+missing）/ coverage watermark

設計依據：兩輪雙模型設計審查 + aipoolserver spike（62-claim census）。
關鍵教訓內建：
  - method 必須 `Class.method` scope 比對（同名 collision 會製造假象）
  - 文件自評 status 不可信 → git 訊號（symbol 進 code 時間 vs doc 修改時間）解歧
  - 未抽取/未登記 ≠ 無 drift → watermark 必須出現在報告
  - 機率性結果（LLM）不入 v1；residual 區只計數揭露（v2 staging）

零新依賴、零 DB 遷移（確定性抽取毫秒級，每次即時重算）。
git 一律 argv list（無 shell 內插）。route 掃描 v1 僅支援 Java
(*Controller*/*Feign* 檔的 @*Mapping)，其他框架計入 unmeasured。
"""

import json
import os
import re
import subprocess
from typing import Dict, List, Optional

MANIFEST_NAME = 'intent-manifest.json'

# --- 措辭啟發式（保守：寧可 unlabeled 不可臆測 current）---
_UNCLEAR_MARKERS = ('若存在', '或拆出', '（示例）', '(示例)')
_CURRENT_MARKERS = ('既有', '已在', '已有', '目前', '現行', '舊：', '現況')
_PROPOSED_MARKERS = ('建議', '需新增', '需補', '必做', '提案', '可另闢',
                     '待補', '應新增', '需在', '拆出', '規劃')

_BACKTICK_RE = re.compile(r'`([^`]+)`')
_SCOPED_METHOD_RE = re.compile(r'^([A-Z][A-Za-z0-9_]*)\.([a-z][A-Za-z0-9_]*)$')
_CONST_RE = re.compile(r'^[A-Z][A-Z0-9_]{4,}$')
_CLASS_RE = re.compile(r'^[A-Z][A-Za-z0-9]*$')
_ROUTE_RE = re.compile(r'^/[A-Za-z0-9_/{}.\-*]+$')
_HEADING_RE = re.compile(r'^#{1,6}\s+(.*)$')

_ROUTE_ANNOT_RE = re.compile(
    r'@(?:Get|Post|Put|Delete|Patch|Request)Mapping\s*\(\s*(?:value\s*=\s*)?[\{]?\s*"([^"]+)"')
_CLASS_LEVEL_ROUTE_RE = re.compile(
    r'@RequestMapping\s*\(\s*(?:value\s*=\s*)?[\{]?\s*"([^"]+)"')


# =============================================================================
# Task 1: manifest
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


# =============================================================================
# Task 2: extract（確定性 census）
# =============================================================================

_HTTP_VERB_RE = re.compile(r'^(?:GET|POST|PUT|DELETE|PATCH)\s+', re.I)


def _classify_span(span: str):
    """backtick span → (anchor, symbol, scope) 或 None（非錨點）。"""
    span = _HTTP_VERB_RE.sub('', span.strip())  # `POST /x/y` → `/x/y`（文件常見寫法）
    m = _SCOPED_METHOD_RE.match(span)
    if m:
        return ('method', m.group(2), m.group(1))
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
    """status 判定優先序：unclear 標記 > 行內 current > 行內 proposed > 節標 > unlabeled。"""
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
    """確定性抽取一份 doc 的 claims。回 {claims, residual_lines, extract_failed}。"""
    try:
        text = open(doc_abs, encoding='utf-8', errors='replace').read()
    except Exception:
        return {'doc': doc_rel, 'claims': [], 'residual_lines': 0, 'extract_failed': True}

    claims, seen = [], set()
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
        if set(stripped) <= set('|-: '):  # 表格分隔線
            continue

        status = _line_status(stripped, section)
        line_claims = 0
        for span in _BACKTICK_RE.findall(stripped):
            cls = _classify_span(span)
            if not cls:
                continue
            anchor, symbol, scope = cls
            key = (anchor, symbol, scope)
            if key in seen:
                continue
            seen.add(key)
            claims.append({
                'id': f'{doc_rel}:{lineno}:{len(claims)}',
                'doc': doc_rel, 'line': lineno,
                'quote': stripped[:120],
                'anchor': anchor, 'symbol': symbol, 'scope': scope,
                'status': status,
            })
            line_claims += 1
        if line_claims == 0:
            residual += 1

    return {'doc': doc_rel, 'claims': claims, 'residual_lines': residual,
            'extract_failed': False}


# =============================================================================
# Task 3: link（對 Code Graph scoped 比對）
# =============================================================================

def _build_code_maps(project: str) -> Dict:
    """從 code_nodes 建查詢映射（分頁取完；排除 /src/test/）。"""
    from servers.code_graph import get_code_nodes
    classes, scoped, method_scopes, consts = {}, {}, {}, {}
    offset = 0
    while True:
        page = get_code_nodes(project, limit=500, offset=offset)
        for n in page:
            fp = n.get('file_path') or ''
            if '/src/test/' in fp:
                continue
            loc = {'file': fp, 'line': n.get('line_start')}
            kind = n.get('kind')
            if kind in ('class', 'interface'):
                classes.setdefault(n['name'], []).append(loc)
            elif kind in ('function', 'method'):
                suffix = (n.get('id') or '').rsplit(':', 1)[-1]
                if '.' in suffix:
                    scoped.setdefault(suffix, []).append(loc)
                    method_scopes.setdefault(n['name'], set()).add(
                        suffix.rsplit('.', 1)[0])
                else:
                    method_scopes.setdefault(n['name'], set())
            elif kind in ('constant', 'variable'):
                consts.setdefault(n['name'], []).append(loc)
        if len(page) < 500:
            break
        offset += 500
    return {'classes': classes, 'scoped': scoped,
            'method_scopes': method_scopes, 'consts': consts}


def _strip_java_comments(txt: str) -> List[str]:
    out, in_block = [], False
    for line in txt.splitlines():
        s = line
        if in_block:
            if '*/' in s:
                s = s.split('*/', 1)[1]
                in_block = False
            else:
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
    """掃 *Controller*/*Feign* Java 檔的 active @*Mapping（comment-stripped）。"""
    routes = {}
    for root, dirs, files in os.walk(project_dir):
        dirs[:] = [d for d in dirs if d not in ('.git', 'node_modules')]
        if '/src/test/' in root:
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


def link_claims(claims: List[Dict], project: str, project_dir: str) -> List[Dict]:
    """比對 claims 與 Code Graph / route 掃描。回 LinkResult list（含 verdict）。"""
    maps = _build_code_maps(project)
    routes = None  # lazy：有 route claim 才掃
    results = []
    for c in claims:
        r = dict(c)
        a = c['anchor']
        if a == 'class':
            locs = maps['classes'].get(c['symbol'], [])
            r.update(matched=bool(locs), tier='class_exact', locations=locs[:3])
        elif a == 'method':
            key = f"{c['scope']}.{c['symbol']}"
            locs = maps['scoped'].get(key, [])
            r.update(matched=bool(locs), tier='method_scoped', locations=locs[:3])
            if not locs:
                r['same_name_other_scopes'] = sorted(
                    maps['method_scopes'].get(c['symbol'], set()))[:5]
        elif a == 'const':
            if not maps['consts']:
                r.update(matched=None, tier='unavailable', locations=[])
            else:
                locs = maps['consts'].get(c['symbol'], [])
                r.update(matched=bool(locs), tier='const_exact', locations=locs[:3])
        elif a in ('route', 'route_prefix'):
            if routes is None:
                routes = _scan_routes(project_dir)
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
# Task 4: status resolver（git 訊號；argv list、無 shell）
# =============================================================================

def _git(args: List[str], cwd: str) -> Optional[str]:
    try:
        out = subprocess.run(['git', '-C', cwd] + args,
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip() if out.returncode == 0 else None
    except Exception:
        return None


def _git_resolve(result: Dict, project_dir: str) -> Optional[str]:
    """matched 的 unclear/unlabeled/proposed claim：symbol 進 code 是否早於 doc 最後修改。
    回 'existed_before_doc' / 'added_after_doc' / None（無法判定）。"""
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
# Task 5: report（status-aware 車道 + watermark）
# =============================================================================

_STRONG_TIERS = ('class_exact', 'method_scoped', 'const_exact', 'route_exact')


def _verdict(r: Dict, project_dir: str) -> str:
    st, m, tier = r['status'], r.get('matched'), r.get('tier')
    if tier == 'unavailable':
        return 'unmeasured'
    if st == 'current':
        return 'ok' if m else 'drift'
    if st == 'proposed':
        if m and tier in _STRONG_TIERS:
            r['git_resolution'] = _git_resolve(r, project_dir)
            return 'doc_stale'
        return 'ok_proposed'
    # unclear / unlabeled
    if m:
        r['git_resolution'] = _git_resolve(r, project_dir)
        return 'ok' if r.get('git_resolution') == 'existed_before_doc' else 'cross_check'
    return 'needs_review'


def intent_drift_report(project: str, project_dir: str) -> Optional[str]:
    """manifest 驅動的 intent drift 報告。無 manifest → None（呼叫端走 legacy）。"""
    manifest = load_manifest(project_dir)
    if manifest is None:
        return None

    docs = [d for d in manifest['docs']
            if isinstance(d, dict) and d.get('status', 'active') == 'active']
    all_results, extract_failed, residual_total = [], [], 0
    for d in docs:
        rel = d.get('path', '')
        ext = extract_claims(os.path.join(project_dir, rel), rel)
        if ext['extract_failed']:
            extract_failed.append(rel)
            continue
        residual_total += ext['residual_lines']
        linked = link_claims(ext['claims'], project, project_dir)
        for r in linked:
            r['authority'] = d.get('authority', 'normative')
            r['verdict'] = _verdict(r, project_dir)
        all_results.extend(linked)

    lanes = {}
    for r in all_results:
        lanes.setdefault(r['verdict'], []).append(r)

    # 未登記的 md 檔（watermark）
    registered = {d.get('path') for d in docs}
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
        scope = f"{r['scope']}." if r.get('scope') else ''
        return (f"- [{r['anchor']}] `{scope}{r['symbol']}` "
                f"({r['doc']}:{r['line']}){where}{extra}")

    lines = [f"# Intent Drift Report — {project}", '']
    lines.append(f"## 🔴 Drift（文件斷言存在、code 找不到）: {len(lanes.get('drift', []))}")
    lines += [fmt(r) for r in lanes.get('drift', [])] or []
    lines.append('')
    lines.append(f"## 📝 Doc-stale（文件標『提案』但 code 已存在 → 請更新文件）: "
                 f"{len(lanes.get('doc_stale', []))}")
    lines += [fmt(r) for r in lanes.get('doc_stale', [])]
    lines.append('')
    lines.append(f"## ❓ Needs-review（status 不明且 code 找不到；非 drift 非 clean）: "
                 f"{len(lanes.get('needs_review', []))}")
    lines += [fmt(r) for r in lanes.get('needs_review', [])]
    lines.append('')
    cc = lanes.get('cross_check', [])
    if cc:
        lines.append(f"## 🔍 Cross-check（已存在但無法以 git 確認時序）: {len(cc)}")
        lines += [fmt(r) for r in cc]
        lines.append('')
    lines.append('## 📊 Coverage Watermark')
    lines.append(f"- manifest 登記 docs: {len(docs)}（抽取失敗: {len(extract_failed)}"
                 f"{'：' + ', '.join(extract_failed) if extract_failed else ''}）")
    lines.append(f"- 未登記 md 檔: {unregistered}（未登記 = 未檢查 ≠ 無 drift）")
    lines.append(f"- claims: {len(all_results)}"
                 f"（ok: {len(lanes.get('ok', []))}, ok_proposed: {len(lanes.get('ok_proposed', []))}, "
                 f"unmeasured: {len(lanes.get('unmeasured', []))}）")
    lines.append(f"- residual 非錨點行（v1 未處理、待 v2 LLM staging）: {residual_total}")
    return '\n'.join(lines)
