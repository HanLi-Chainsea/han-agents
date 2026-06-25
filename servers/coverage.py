"""分支覆蓋率量測（單一職責：純量測，不碰 DB / facade）。

用 `python -m coverage run --branch` 跑指定測試，產 json，再用「行範圍」把
branch arc 歸因到本次 target 函式。非侵入：隔離 data file、不寫專案設定檔。

已知限制（v1，刻意）：行範圍會把 target 範圍內的巢狀 function/lambda 分支也算入，
屬偏保守的過度涵蓋（要求多測、不會漏算）。AST 精確 scope 留待 v2。
"""

import ast
import json
import os
import re
import subprocess
import sys
import tempfile
from typing import Dict, List, Optional

_TIMEOUT_SEC = 300

# 終端機跳脫序列（ANSI/CSI）。pytest 輸出可能含色碼；落進退件 prompt 前先剝除。
_ANSI_RE = re.compile(r'\x1b\[[0-9;?]*[ -/]*[@-~]')


def _sanitize(text: Optional[str], limit: int = 400) -> str:
    """清掉 ANSI 跳脫與控制字元（保留 \\n\\t），截長度。

    為什麼：pytest stdout/stderr 會被當成退件理由寫進 working_memory['critic_suggestions']，
    再注入 executor 重試 prompt。剝除控制字元可縮小亂碼/注入面，且不影響可讀性。
    """
    if not text:
        return ''
    text = _ANSI_RE.sub('', text)
    text = ''.join(c for c in text
                   if c in ('\n', '\t') or (ord(c) >= 32 and ord(c) != 127))
    return text.strip()[-limit:]


def _invalid_targets(coverage_targets: List[Dict]) -> Optional[str]:
    """回傳第一個不合法 target 的描述；全合法回 None。

    每個 target 必須有正整數 line_start、line_end 且 line_end >= line_start
    （bool 不算合法行號）。不合法 → 上游確定性退件，**絕不**讓 `None`/`0` 退化成
    單行範圍而把整段未覆蓋分支假裝成全覆蓋（codex 審查的假綠破口）。
    """
    for t in coverage_targets:
        ls = t.get('line_start')
        le = t.get('line_end')
        fp = t.get('file_path') or '?'
        for label, v in (('line_start', ls), ('line_end', le)):
            if not isinstance(v, int) or isinstance(v, bool) or v <= 0:
                return f"{fp}: {label} 必須為正整數（得到 {ls!r}/{le!r}）"
        if le < ls:
            return f"{fp}: line_end({le}) < line_start({ls})"
    return None


def _coverage_available() -> bool:
    try:
        import importlib.util
        return importlib.util.find_spec('coverage') is not None
    except Exception:
        return False


def _canonical_in_root(root: str, path: str) -> Optional[str]:
    """把 path 解析為絕對路徑並確認落在 root 下；否則回 None。"""
    root_abs = os.path.realpath(root)
    cand = path if os.path.isabs(path) else os.path.join(root_abs, path)
    cand = os.path.realpath(cand)
    if cand == root_abs or cand.startswith(root_abs + os.sep):
        return cand
    return None


def _build_file_index(files: Dict, root: str) -> Dict[str, Dict]:
    """coverage json 的 files key 可能是相對/絕對/帶 ./ → 建 realpath → entry 映射。"""
    idx = {}
    for key, entry in files.items():
        ap = key if os.path.isabs(key) else os.path.join(os.path.realpath(root), key)
        idx[os.path.realpath(ap)] = entry
    return idx


def _result(status: str, error: Optional[str] = None,
            per_target: Optional[List[Dict]] = None,
            fully_covered: bool = False) -> Dict:
    return {'tool_status': status, 'fully_covered': fully_covered,
            'per_target': per_target or [], 'error': error}


def _valid_arc(arc) -> bool:
    """防 coverage json 未來版本格式異動：只接受 [int, int] 形狀的 arc。

    排除 bool：JSON 的 true/false 是 int 子類，若混進 arc 會被誤當成行號 1/0。
    """
    return (isinstance(arc, (list, tuple)) and len(arc) == 2
            and all(isinstance(x, int) and not isinstance(x, bool) for x in arc))


def _ast_body_start_line(abs_path: str, name: str,
                         line_start: int, line_end: int) -> Optional[int]:
    """Return the first body-statement line of the named function/method.

    Finds the FunctionDef/AsyncFunctionDef whose name matches and whose lineno
    is within [line_start, line_end], picking the closest to line_start when
    multiple match.  Searches ClassDef bodies via ast.walk (handles methods).

    Returns node.body[0].lineno (the first real statement — after the signature,
    default args, and annotations, which all have lineno < body[0].lineno for
    multi-line signatures).

    Returns None on any failure (file unreadable, parse error, no matching node,
    empty body).  Caller must treat None as fail-closed (reject).
    """
    try:
        with open(abs_path, encoding='utf-8', errors='replace') as fh:
            source = fh.read()
        tree = ast.parse(source, filename=abs_path)
    except Exception:
        return None

    candidates = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == name and line_start <= node.lineno <= line_end:
                candidates.append(node)
    if not candidates:
        return None
    # Pick the node whose lineno is closest to line_start (usually the exact match).
    best = min(candidates, key=lambda n: abs(n.lineno - line_start))
    if not best.body:
        return None
    return best.body[0].lineno


def measure_branch_coverage(project_path: str,
                            test_targets: List[str],
                            coverage_targets: List[Dict]) -> Dict:
    """量測 coverage_targets 各函式行範圍內的分支覆蓋。

    Returns: {
        'tool_status': 'ok' | 'tests_failed' | 'no_targets'
                       | 'invalid_targets' | 'test_run_error'
                       | 'schema_error' | 'unavailable',
        'fully_covered': bool,
        'per_target': [{'file_path','name','line_start','line_end',
                        'missing_branches':[{'from','to'}],'n_total','n_covered'}],
        'error': str | None,
    }

    狀態分流原則（對齊「工具確認、非 LLM 宣稱」）：除 `unavailable`（coverage 套件
    缺失＝真正 infra，上游 fail-open）外，所有非 `ok` 狀態都代表「無法工具確認全覆蓋」，
    上游一律確定性退件（fail-closed）。
    """
    # target 行範圍非法 → 確定性失敗（先於任何子行程；不依賴 coverage 安裝）。
    bad = _invalid_targets(coverage_targets)
    if bad:
        return _result('invalid_targets', f'coverage target 行範圍不合法：{bad}')
    if not _coverage_available():
        return _result('unavailable', 'coverage 套件未安裝')
    if not test_targets:
        # 沒有可跑的測試＝無法確認覆蓋 → fail-closed（gate 已先擋空 derive，這是防呆）。
        return _result('test_run_error', '無可量測的 test_targets')

    root = os.path.realpath(project_path)
    safe_tests = []
    for t in test_targets:
        canon = _canonical_in_root(root, t)
        if canon and os.path.isfile(canon):
            safe_tests.append(canon)
    if not safe_tests:
        # 宣告的測試檔不存在/不在根目錄 → 跑不了＝無法確認 → fail-closed。
        return _result('test_run_error', 'test_targets 不在專案根目錄下或不存在')

    with tempfile.TemporaryDirectory() as tmp:
        data_file = os.path.join(tmp, '.coverage')
        json_file = os.path.join(tmp, 'cov.json')
        env = dict(os.environ, COVERAGE_FILE=data_file)
        try:
            run = subprocess.run(
                [sys.executable, '-m', 'coverage', 'run', '--branch',
                 '--data-file', data_file, '-m', 'pytest', '-q', *safe_tests],
                cwd=root, env=env, capture_output=True, text=True,
                timeout=_TIMEOUT_SEC,
            )
        except subprocess.TimeoutExpired:
            # 逾時＝沒跑完＝無法確認覆蓋 → fail-closed（非 infra fail-open）。
            return _result('test_run_error', f'pytest 逾時（>{_TIMEOUT_SEC}s）')

        rc = run.returncode
        tail = _sanitize((run.stdout or '')[-500:] + (run.stderr or '')[-500:])
        # pytest exit codes: 0=全過, 1=有測試失敗, 5=未收集到測試, 2/3/4=中斷/內部/用法錯
        if rc == 1:
            return _result('tests_failed', f'測試未通過 (rc=1): {tail}')
        if rc == 5:
            return _result('no_targets', f'未收集到任何測試 (rc=5): {tail}')
        if rc != 0:
            # rc 2/3/4：pytest 中斷/內部錯/用法錯 → 沒有可信覆蓋結果 → fail-closed。
            return _result('test_run_error', f'pytest 異常 (rc={rc}): {tail}')

        try:
            rep = subprocess.run(
                [sys.executable, '-m', 'coverage', 'json',
                 '--data-file', data_file, '-o', json_file],
                cwd=root, env=env, capture_output=True, text=True,
                timeout=_TIMEOUT_SEC,
            )
        except subprocess.TimeoutExpired:
            return _result('test_run_error', 'coverage json 產製逾時')
        if rep.returncode != 0 or not os.path.exists(json_file):
            return _result('test_run_error',
                           f'coverage json 產製失敗: {_sanitize((rep.stderr or "")[-300:])}')

        try:
            with open(json_file, encoding='utf-8') as fh:
                data = json.load(fh)
        except (ValueError, OSError) as e:
            return _result('test_run_error', f'coverage json 解析失敗: {_sanitize(str(e))}')

    if not isinstance(data, dict) or not isinstance(data.get('files'), dict):
        return _result('schema_error', 'coverage json 格式非預期（缺 files dict）')

    file_index = _build_file_index(data['files'], root)
    return _attribute_targets(file_index, coverage_targets, root)


def _attribute_targets(file_index: Dict[str, Dict],
                       coverage_targets: List[Dict],
                       root: str) -> Dict:
    """把 branch arc 依「行範圍」歸因到各 target，回 _result(...)。

    前置條件：coverage_targets 行範圍已由 _invalid_targets 驗過（line_start/line_end
    為正整數且 le>=ls），故此處直接取用、不再用 `or` 退化。

    防護（codex 審查）：coverage json 的 branch 欄位若非 `[[int,int], ...]`（版本格式
    異動、欄位缺漏），**不可**靜默過濾成空集合而誤判全覆蓋——回 'schema_error' 確定性退件。

    n_total==0（branchless）的額外驗證：無分支不代表函式有被執行。必須確認
    executed_lines 裡至少有一 **函式體** 行落在 (line_start, line_end] 內，才算真正呼叫到。
    （line_start 即 `def` 行，Python import 時就會執行，不算呼叫證明；
     body 行 ls+1..le 只有實際呼叫才會出現在 executed_lines。）
    若 executed_lines 不是 list（欄位缺失或格式異動）→ schema_error。
    若函式體完全未執行 → no_targets（fail-closed，不允許假綠）。
    """
    per_target = []
    for t in coverage_targets:
        fp = t.get('file_path') or ''
        ls = t['line_start']
        le = t['line_end']
        canon = _canonical_in_root(root, fp)
        entry = file_index.get(canon) if canon else None
        if entry is None:
            return _result('no_targets', f'target 檔未被測試執行（未覆蓋）: {fp}')
        # 用 .get(key) 不帶預設：欄位缺失 → None → 非 list → schema_error。
        # 不可用 .get(key, []) 把「缺欄位」當成「無未覆蓋分支」而誤判全覆蓋（假綠）。
        mb = entry.get('missing_branches')
        eb = entry.get('executed_branches')
        if (not isinstance(mb, list) or not isinstance(eb, list)
                or not all(_valid_arc(a) for a in mb)
                or not all(_valid_arc(a) for a in eb)):
            return _result('schema_error',
                           f'coverage json 分支格式非預期（{fp}）：'
                           'missing/executed_branches 應為 [[int,int], ...]')
        in_range = lambda arc: ls <= arc[0] <= le
        missing = [a for a in mb if in_range(a)]
        executed = [a for a in eb if in_range(a)]
        n_total = len(missing) + len(executed)

        # n_total==0: branchless function — must verify execution via executed_lines.
        # A file appearing in coverage data only proves it was imported, not called.
        # The `def` line (ls) is always executed at import time and does NOT prove
        # the function body ran.
        #
        # AST-based body-start line (D2g fix):
        # A multi-line function signature with a side-effecting default arg executes
        # that default-arg line at import time — it falls in (ls, le] but is NOT in
        # the function body.  We use AST to find the first real body-statement line
        # (node.body[0].lineno) so that only lines >= body_start count as execution
        # evidence.  This closes the multiline-signature false-green.
        #
        # Fail-closed invariants:
        # - executed_lines not a list → schema_error
        # - AST lookup fails (file unreadable / parse error / no matching node /
        #   empty body) → schema_error (reject; when in doubt, reject)
        # - body_start_line <= line_start (single-physical-line function,
        #   e.g. `def f(): return 1`) → no_targets (D2h: line coverage cannot
        #   distinguish import-time def-execution from an actual call; fail-closed)
        # - no executed line >= body_start_line in [body_start, le] → no_targets
        if n_total == 0:
            el = entry.get('executed_lines')
            if not isinstance(el, list):
                return _result('schema_error',
                               f'coverage json 格式非預期（{fp}）：'
                               'executed_lines 應為 list（branchless 函式需執行證明）')
            name = t.get('name') or fp
            # AST: find the first body-statement line so default-arg lines are excluded.
            body_start = _ast_body_start_line(canon, name, ls, le) if canon else None
            if body_start is None:
                return _result(
                    'schema_error',
                    f'無法確認函式體起始行（AST 解析失敗或找不到函式定義）: {fp}::{name}',
                )
            # D2h: single-physical-line function (`def f(): return 1`).
            # body_start <= ls means FunctionDef.lineno == body[0].lineno — the
            # def and its body are on the same physical line.  Coverage marks that
            # line executed at import time (defining f runs the def line), so we
            # CANNOT distinguish "def executed at import" from "f() actually called".
            # Line coverage genuinely cannot prove a call here → fail-closed.
            if body_start <= ls:
                return _result(
                    'no_targets',
                    f'單行 branchless 函式無法以行覆蓋證明執行(def 與 body 同行),'
                    f'退回人工確認: {fp}::{name}',
                )
            # Execution evidence: at least one executed line in [body_start, le].
            # (body_start > ls is guaranteed here — multi-line function only.)
            executed_in_range = any(
                isinstance(ln, int) and not isinstance(ln, bool)
                and body_start <= ln <= le
                for ln in el
            )
            if not executed_in_range:
                return _result(
                    'no_targets',
                    f'target 函式未被測試執行(無分支且函式體未覆蓋): {fp}::{name}',
                )

        per_target.append({
            'file_path': fp, 'name': t.get('name'),
            'line_start': ls, 'line_end': le,
            'missing_branches': [{'from': a[0], 'to': a[1]} for a in missing],
            'covered_branches': [{'from': a[0], 'to': a[1]} for a in executed],
            'n_total': n_total,
            'n_covered': len(executed),
        })

    fully = all(not pt['missing_branches'] for pt in per_target)
    return _result('ok', None, per_target=per_target, fully_covered=fully)


_MARKER_RE = re.compile(r'^\s*TEST_TARGETS:\s*(.+)$', re.MULTILINE)
_MAX_TEST_TARGETS = 50  # marker 與後備啟發式共用的測試檔上限


def _is_test_path(path: str) -> bool:
    from servers.recipes import is_test_file
    return is_test_file(path)


def derive_test_targets(project_path: str,
                        executor_result: Optional[str],
                        coverage_targets: List[Dict]) -> List[str]:
    """決定要餵給 coverage 的測試檔（相對專案根）。

    1. 優先：從 executor 輸出解析 `TEST_TARGETS:` marker（逗號/空白分隔）。
    2. 後備：用各 coverage_target 的檔名 stem，找 test_<stem>.py / <stem>_test.py。
    只保留「存在且為測試命名」的路徑。回傳去重排序後的相對路徑清單（可能為空）。
    """
    root = os.path.realpath(project_path)
    found: List[str] = []

    # 1. marker（同樣設上限，避免異常巨量 marker 撐爆 pytest 命令列）
    for m in _MARKER_RE.findall(executor_result or ''):
        for raw in re.split(r'[,\s]+', m.strip()):
            if not raw:
                continue
            canon = _canonical_in_root(root, raw)
            if canon and os.path.isfile(canon) and _is_test_path(raw):
                rel = os.path.relpath(canon, root)
                if rel not in found:
                    found.append(rel)
                    if len(found) >= _MAX_TEST_TARGETS:
                        break
        if len(found) >= _MAX_TEST_TARGETS:
            break
    if found:
        return sorted(found)

    # 2. 後備：stem 啟發式（限縮——剪掉 build/dist/site-packages 等噪音目錄、設候選上限）
    stems = set()
    for t in coverage_targets:
        fp = t.get('file_path') or ''
        s = os.path.splitext(os.path.basename(fp))[0]
        if s:
            stems.add(s)
    wanted = set()
    for s in stems:
        wanted.add(f'test_{s}.py')
        wanted.add(f'{s}_test.py')
    if not wanted:
        return []
    _PRUNE = {'.git', '.hg', '.svn', '.venv', 'venv', 'env', '__pycache__',
              'node_modules', 'build', 'dist', '.tox', '.eggs', '.mypy_cache',
              '.pytest_cache', 'site-packages'}
    _MAX_FALLBACK = _MAX_TEST_TARGETS
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _PRUNE]
        for fn in filenames:
            if fn in wanted:
                rel = os.path.relpath(os.path.join(dirpath, fn), root)
                if rel not in found:
                    found.append(rel)
                    if len(found) >= _MAX_FALLBACK:
                        return sorted(found)
    # 後備找不到 → 回空清單；由 run_coverage_gate 決定確定性退件（要求 executor 回報 TEST_TARGETS）
    return sorted(found)


def format_coverage_summary(per_target: List[Dict]) -> List[str]:
    """人類可見的分支覆蓋率摘要：每個 target 列出**每一條分支**（✓/✗）。

    讓跑 /han:unit-test 的人不只看到「率」，還能逐條核對邏輯：總共有幾條分支、
    哪幾條走到（✓）、哪幾條沒走到（✗）。格式：
      📊 分支覆蓋 <file>::<name> 1/4（共 4 條分支）❌
         ✓ L2→3
         ✗ L2→4
         ✗ L4→5
         ✗ L4→6
    全覆蓋時標頭結尾為 ✅、所有分支皆 ✓。
    covered_branches 用 .get 取（缺欄位時降級為只列未覆蓋），讓不帶該欄位的
    舊資料/測試樁不致爆掉。
    """
    lines = []
    for pt in per_target:
        n_cov, n_tot = pt['n_covered'], pt['n_total']
        if n_tot == 0:
            # M2: branchless function — neutral display; gate still proceeds (not a fail)
            lines.append(
                f"📊 分支覆蓋 {pt['file_path']}::{pt['name']} "
                f"〇 無分支 (n/a)"
            )
            continue
        mark = '✅' if not pt['missing_branches'] else '❌'
        lines.append(
            f"📊 分支覆蓋 {pt['file_path']}::{pt['name']} "
            f"{n_cov}/{n_tot}（共 {n_tot} 條分支）{mark}"
        )
        # 逐條列出：已覆蓋 ✓ 在前、未覆蓋 ✗ 在後，並依行號排序便於對照原始碼。
        branches = [(a, True) for a in pt.get('covered_branches', [])]
        branches += [(a, False) for a in pt['missing_branches']]
        branches.sort(key=lambda x: (x[0]['from'], x[0]['to']))
        for arc, covered in branches:
            lines.append(f"   {'✓' if covered else '✗'} L{arc['from']}→{arc['to']}")
    return lines


def format_missing_issues(per_target: List[Dict]) -> List[str]:
    """把有未覆蓋分支的 target 轉成人類可讀 issue 字串（給 finish_validation）。"""
    issues = []
    for pt in per_target:
        if not pt['missing_branches']:
            continue
        arcs = ', '.join(f"{a['from']}→{a['to']}" for a in pt['missing_branches'])
        issues.append(
            f"{pt['file_path']} 函式 {pt['name']} (L{pt['line_start']}-{pt['line_end']})："
            f"分支未覆蓋 {arcs}（{len(pt['missing_branches'])} 條未覆蓋 / 共 {pt['n_total']} 條）"
        )
    return issues
