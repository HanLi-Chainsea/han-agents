import os
from typing import Dict, List, Optional

# Use defusedxml to prevent XXE/billion-laughs attacks
try:
    from defusedxml.ElementTree import parse as ET_parse
except ImportError:
    # Fallback if defusedxml not available (though it should be)
    from xml.etree.ElementTree import parse as ET_parse

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_JDIR = os.path.join(_BASE, 'reference', 'tools', 'jacoco')
JACOCO_AGENT = os.path.join(_JDIR, 'jacocoagent.jar')
JACOCO_CLI = os.path.join(_JDIR, 'jacococli.jar')
JACOCO_INIT = os.path.join(_JDIR, 'jacoco-init.gradle')


def _result(status: str, error: Optional[str] = None,
            per_target: Optional[List[Dict]] = None,
            fully_covered: bool = False) -> Dict:
    """Match the signature from servers/coverage.py."""
    return {'tool_status': status, 'fully_covered': fully_covered,
            'per_target': per_target or [], 'error': error}


def parse_jacoco_xml(xml_path: str, coverage_targets: List[Dict], source_root: str) -> Dict:
    """Parse JaCoCo XML report to per-target branch coverage.

    Args:
        xml_path: Path to JaCoCo XML report
        coverage_targets: List of {'file_path', 'name', 'line_start', 'line_end'}
        source_root: Source root directory (for resolving file paths)

    Returns:
        {'tool_status', 'fully_covered', 'per_target', 'error'}
        tool_status: 'ok' | 'schema_error' | 'no_targets' | 'test_run_error'
    """
    # Validate targets
    if not coverage_targets:
        return _result('no_targets', 'No coverage targets provided')

    try:
        tree = ET_parse(xml_path)
        root = tree.getroot()
    except (FileNotFoundError, Exception) as e:
        return _result('test_run_error', f'Failed to parse JaCoCo XML: {e}')

    # Build a map: source filename -> sourcefile element
    # <sourcefile name="Classify.java"> contains <line> elements
    sourcefile_map = {}
    for sf in root.findall('.//sourcefile'):
        name = sf.get('name')
        if name:
            sourcefile_map[name] = sf

    # Check if any target's class exists but has no sourcefile (schema_error)
    # Build class map: class name -> sourcefilename from class element
    class_sourcefilename_map = {}
    for cls in root.findall('.//class'):
        cls_name = cls.get('name')
        src_name = cls.get('sourcefilename')
        if cls_name:
            class_sourcefilename_map[cls_name] = src_name

    per_target = []

    for target in coverage_targets:
        file_path = target.get('file_path', '')
        name = target.get('name', '')
        line_start = target.get('line_start')
        line_end = target.get('line_end')

        # Extract source filename from file_path (e.g., "src/main/java/demo/Classify.java" -> "Classify.java")
        source_filename = os.path.basename(file_path)

        # Find the sourcefile element
        sourcefile = sourcefile_map.get(source_filename)
        if sourcefile is None:
            # Check if any class references this source file but it's not in sourcefile list
            # This is a schema error - class present but no sourcefile data
            for cls_name, src_name in class_sourcefilename_map.items():
                if src_name == source_filename:
                    # Class exists but no sourcefile element = schema_error
                    return _result('schema_error',
                                 f'Target {file_path}: class has no sourcefile data')

            # Target file/method entirely absent from XML
            return _result('no_targets', f'Target file not found in coverage: {file_path}')

        # Collect branch data from lines within range
        covered_branches = []
        missing_branches = []

        for line_elem in sourcefile.findall('line'):
            line_nr_str = line_elem.get('nr')
            if line_nr_str is None:
                continue

            try:
                line_nr = int(line_nr_str)
            except ValueError:
                continue

            # Only include lines within target's range
            if not (line_start <= line_nr <= line_end):
                continue

            # Get missed/covered branch counts
            mb_str = line_elem.get('mb')
            cb_str = line_elem.get('cb')

            # Both attributes must be present for valid branch data
            if mb_str is None or cb_str is None:
                continue

            try:
                mb = int(mb_str)
                cb = int(cb_str)
            except ValueError:
                continue

            # If no branches on this line, skip
            if mb == 0 and cb == 0:
                continue

            # Create entries for covered slots (0 to cb-1)
            for slot_idx in range(cb):
                covered_branches.append({'from': line_nr, 'to': slot_idx})

            # Create entries for missed slots (0 to mb-1)
            for slot_idx in range(mb):
                missing_branches.append({'from': line_nr, 'to': slot_idx})

        # If the sourcefile exists but has no BRANCH data for this target's range, schema_error
        if not covered_branches and not missing_branches:
            # Check if sourcefile has any lines at all
            lines_in_file = sourcefile.findall('line')
            if lines_in_file:
                # Has lines but none with branch data in this target's range = schema_error
                return _result('schema_error',
                             f'Target {name} ({file_path}): no branch data in coverage')
            else:
                # No lines in sourcefile at all
                return _result('schema_error',
                             f'Target {file_path}: sourcefile has no line data')

        n_total = len(covered_branches) + len(missing_branches)
        n_covered = len(covered_branches)

        per_target.append({
            'file_path': file_path,
            'name': name,
            'line_start': line_start,
            'line_end': line_end,
            'missing_branches': missing_branches,
            'covered_branches': covered_branches,
            'n_total': n_total,
            'n_covered': n_covered,
        })

    # Determine fully_covered
    fully_covered = all(not pt['missing_branches'] for pt in per_target)

    return _result('ok', None, per_target=per_target, fully_covered=fully_covered)
