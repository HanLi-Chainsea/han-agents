import os

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_JDIR = os.path.join(_BASE, 'reference', 'tools', 'jacoco')
JACOCO_AGENT = os.path.join(_JDIR, 'jacocoagent.jar')
JACOCO_CLI = os.path.join(_JDIR, 'jacococli.jar')
JACOCO_INIT = os.path.join(_JDIR, 'jacoco-init.gradle')
