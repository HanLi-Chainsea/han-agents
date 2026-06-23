import os
import zipfile


def test_jacoco_tooling_bundled():
    from servers import coverage_java as cj

    for p in (cj.JACOCO_AGENT, cj.JACOCO_CLI, cj.JACOCO_INIT):
        assert os.path.isfile(p), p
    assert zipfile.is_zipfile(cj.JACOCO_AGENT)  # real jar
    assert zipfile.is_zipfile(cj.JACOCO_CLI)
    assert "javaagent" in open(cj.JACOCO_INIT).read()
