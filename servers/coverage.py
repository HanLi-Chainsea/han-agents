"""分支覆蓋率量測（單一職責：純量測，不碰 DB / facade）。

用 `python -m coverage run --branch` 跑指定測試，產 json，再用「行範圍」把
branch arc 歸因到本次 target 函式。非侵入：隔離 data file、不寫專案設定檔。

已知限制（v1，刻意）：行範圍會把 target 範圍內的巢狀 function/lambda 分支也算入，
屬偏保守的過度涵蓋（要求多測、不會漏算）。AST 精確 scope 留待 v2。
"""

import json
import os
import subprocess
import sys
import tempfile
from typing import Dict, List, Optional

_TIMEOUT_SEC = 300


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
    """防 coverage json 未來版本格式異動：只接受 [int, int] 形狀的 arc。"""
    return (isinstance(arc, (list, tuple)) and len(arc) == 2
            and isinstance(arc[0], int) and isinstance(arc[1], int))


def measure_branch_coverage(project_path: str,
                            test_targets: List[str],
                            coverage_targets: List[Dict]) -> Dict:
    """量測 coverage_targets 各函式行範圍內的分支覆蓋。

    Returns: {
        'tool_status': 'ok' | 'tests_failed' | 'no_targets' | 'unavailable',
        'fully_covered': bool,
        'per_target': [{'file_path','name','line_start','line_end',
                        'missing_branches':[{'from','to'}],'n_total','n_covered'}],
        'error': str | None,
    }
    """
    if not _coverage_available():
        return _result('unavailable', 'coverage 套件未安裝')
    if not test_targets:
        return _result('unavailable', '無可量測的 test_targets')

    root = os.path.realpath(project_path)
    safe_tests = []
    for t in test_targets:
        canon = _canonical_in_root(root, t)
        if canon and os.path.isfile(canon):
            safe_tests.append(canon)
    if not safe_tests:
        return _result('unavailable', 'test_targets 不在專案根目錄下或不存在')

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
            return _result('unavailable', f'pytest 逾時（>{_TIMEOUT_SEC}s）')

        rc = run.returncode
        tail = ((run.stdout or '')[-500:] + (run.stderr or '')[-500:]).strip()[-400:]
        # pytest exit codes: 0=全過, 1=有測試失敗, 5=未收集到測試, 2/3/4=中斷/內部/用法錯
        if rc == 1:
            return _result('tests_failed', f'測試未通過 (rc=1): {tail}')
        if rc == 5:
            return _result('no_targets', f'未收集到任何測試 (rc=5): {tail}')
        if rc != 0:
            return _result('unavailable', f'pytest 異常 (rc={rc}): {tail}')

        try:
            rep = subprocess.run(
                [sys.executable, '-m', 'coverage', 'json',
                 '--data-file', data_file, '-o', json_file],
                cwd=root, env=env, capture_output=True, text=True,
                timeout=_TIMEOUT_SEC,
            )
        except subprocess.TimeoutExpired:
            return _result('unavailable', 'coverage json 產製逾時')
        if rep.returncode != 0 or not os.path.exists(json_file):
            return _result('unavailable', f'coverage json 產製失敗: {(rep.stderr or "")[-300:]}')

        try:
            with open(json_file, encoding='utf-8') as fh:
                data = json.load(fh)
        except (ValueError, OSError) as e:
            return _result('unavailable', f'coverage json 解析失敗: {e}')

    if not isinstance(data, dict) or not isinstance(data.get('files'), dict):
        return _result('unavailable', 'coverage json 格式非預期（缺 files dict）')

    file_index = _build_file_index(data['files'], root)

    per_target = []
    for t in coverage_targets:
        fp = t.get('file_path') or ''
        ls = t.get('line_start') or 0
        le = t.get('line_end') or ls
        canon = _canonical_in_root(root, fp)
        entry = file_index.get(canon) if canon else None
        if entry is None:
            return _result('no_targets', f'target 檔未被測試執行（未覆蓋）: {fp}')
        in_range = lambda arc: ls <= arc[0] <= le
        missing = [a for a in entry.get('missing_branches', []) if _valid_arc(a) and in_range(a)]
        executed = [a for a in entry.get('executed_branches', []) if _valid_arc(a) and in_range(a)]
        per_target.append({
            'file_path': fp, 'name': t.get('name'),
            'line_start': ls, 'line_end': le,
            'missing_branches': [{'from': a[0], 'to': a[1]} for a in missing],
            'n_total': len(missing) + len(executed),
            'n_covered': len(executed),
        })

    fully = all(not pt['missing_branches'] for pt in per_target)
    return _result('ok', None, per_target=per_target, fully_covered=fully)
