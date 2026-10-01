"""The documented installation routes resolve the same dependency requirements."""
from pathlib import Path
import re
import tomllib

from packaging.requirements import Requirement


def test_requirements_matches_project_and_dev_dependencies():
    repo = Path(__file__).resolve().parents[1]
    project = tomllib.loads((repo / 'pyproject.toml').read_text(encoding='utf-8'))['project']
    expected = {Requirement(value) for value in project['dependencies']
                + project['optional-dependencies']['dev']}
    actual = set()
    for line in (repo / 'requirements.txt').read_text(encoding='utf-8-sig').splitlines():
        line = re.split(r'\s+#', line, maxsplit=1)[0].strip()
        if line and not line.startswith('#'):
            actual.add(Requirement(line))
    assert actual == expected, {
        'missing': sorted(str(value) for value in expected - actual),
        'extra': sorted(str(value) for value in actual - expected),
    }
