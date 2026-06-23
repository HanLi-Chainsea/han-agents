import os
import zipfile


def test_jacoco_tooling_bundled():
    from servers import coverage_java as cj

    for p in (cj.JACOCO_AGENT, cj.JACOCO_CLI, cj.JACOCO_INIT):
        assert os.path.isfile(p), p
    assert zipfile.is_zipfile(cj.JACOCO_AGENT)  # real jar
    assert zipfile.is_zipfile(cj.JACOCO_CLI)
    assert "javaagent" in open(cj.JACOCO_INIT).read()


def test_parse_jacoco_branch_partial(tmp_path):
    from servers import coverage_java as cj
    xml = tmp_path / "r.xml"
    xml.write_text('''<?xml version="1.0"?><report name="t">
      <package name="demo"><class name="demo/Classify" sourcefilename="Classify.java">
        <method name="of" desc="(I)Ljava/lang/String;" line="4">
          <counter type="BRANCH" missed="3" covered="1"/></method>
      </class>
      <sourcefile name="Classify.java">
        <line nr="4" mi="0" ci="3" mb="1" cb="1"/>
        <line nr="7" mi="3" ci="0" mb="2" cb="0"/>
      </sourcefile></package></report>''')
    targets = [{'file_path':'src/main/java/demo/Classify.java','name':'of','line_start':4,'line_end':9}]
    res = cj.parse_jacoco_xml(str(xml), targets, 'src/main/java')
    assert res['tool_status'] == 'ok'
    pt = res['per_target'][0]
    assert pt['n_total'] == 4 and pt['n_covered'] == 1     # 1 covered + 3 missed branch slots
    assert res['fully_covered'] is False
    # line 7 has 2 missed branch slots → 2 ✗ entries anchored at line 7
    assert sum(1 for a in pt['missing_branches'] if a['from']==7) == 2


def test_invalid_target_line_range_is_fail_closed(tmp_path):
    """A target with line_start=None must return tool_status='invalid_targets',
    not crash with TypeError, and fully_covered must be False (no 假綠)."""
    from servers import coverage_java as cj
    xml = tmp_path / "r.xml"
    xml.write_text('''<?xml version="1.0"?><report name="t">
      <package name="demo"><class name="demo/Classify" sourcefilename="Classify.java">
      </class>
      <sourcefile name="Classify.java">
        <line nr="4" mi="0" ci="3" mb="1" cb="1"/>
      </sourcefile></package></report>''')
    # line_start is None — invalid target
    targets = [{'file_path': 'src/main/java/demo/Classify.java', 'name': 'of',
                'line_start': None, 'line_end': 9}]
    res = cj.parse_jacoco_xml(str(xml), targets, 'src/main/java')
    assert res['tool_status'] == 'invalid_targets', f"expected invalid_targets, got {res['tool_status']}"
    assert res['fully_covered'] is False
    assert res['error'] is not None


def test_absent_target_is_no_targets(tmp_path):
    """Valid XML whose sourcefile does not contain the target file at all
    must return tool_status='no_targets' and fully_covered is False."""
    from servers import coverage_java as cj
    xml = tmp_path / "r.xml"
    # Sourcefile is 'Other.java', not 'Classify.java'
    xml.write_text('''<?xml version="1.0"?><report name="t">
      <package name="demo"><class name="demo/Other" sourcefilename="Other.java">
      </class>
      <sourcefile name="Other.java">
        <line nr="10" mi="0" ci="2" mb="0" cb="2"/>
      </sourcefile></package></report>''')
    targets = [{'file_path': 'src/main/java/demo/Classify.java', 'name': 'of',
                'line_start': 4, 'line_end': 9}]
    res = cj.parse_jacoco_xml(str(xml), targets, 'src/main/java')
    assert res['tool_status'] == 'no_targets', f"expected no_targets, got {res['tool_status']}"
    assert res['fully_covered'] is False


def test_class_present_no_branch_data_is_schema_error(tmp_path):
    """A target class is present in the XML but the sourcefile lines in the
    target range carry no branch data (mb/cb absent) -> tool_status='schema_error',
    fully_covered is False."""
    from servers import coverage_java as cj
    xml = tmp_path / "r.xml"
    # Classify.java present; lines in range 4-9 have mi/ci only, no mb/cb attributes
    xml.write_text('''<?xml version="1.0"?><report name="t">
      <package name="demo"><class name="demo/Classify" sourcefilename="Classify.java">
        <method name="of" desc="(I)Ljava/lang/String;" line="4"/>
      </class>
      <sourcefile name="Classify.java">
        <line nr="4" mi="0" ci="3"/>
        <line nr="6" mi="1" ci="0"/>
      </sourcefile></package></report>''')
    targets = [{'file_path': 'src/main/java/demo/Classify.java', 'name': 'of',
                'line_start': 4, 'line_end': 9}]
    res = cj.parse_jacoco_xml(str(xml), targets, 'src/main/java')
    assert res['tool_status'] == 'schema_error', f"expected schema_error, got {res['tool_status']}"
    assert res['fully_covered'] is False
