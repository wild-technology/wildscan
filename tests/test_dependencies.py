"""The documented installation routes resolve the same dependency requirements."""
from pathlib import Path
import re
import tomllib

from packaging.requirements import Requirement
import pytest


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


@pytest.mark.parametrize(('name', 'vulnerable_versions', 'patched_version'), [
    # GHSA-9hjg-9r4m-mvj7 and GHSA-gc5v-m9x4-r6x2.
    ('requests', ('2.32.0', '2.32.3', '2.32.4'), '2.33.0'),
    # GHSA-6497-prx7-gpmq.
    ('geopandas', ('1.1.0', '1.1.1'), '1.1.2'),
    # GHSA-5rjg-fvgr-3xxf and GHSA-h35f-9h28-mq5c.
    ('setuptools', ('77.0.1', '78.1.0', '82.0.1'), '83.0.0'),
])
def test_dependency_requirements_exclude_known_vulnerable_releases(
        name, vulnerable_versions, patched_version):
    repo = Path(__file__).resolve().parents[1]
    config = tomllib.loads((repo / 'pyproject.toml').read_text(encoding='utf-8'))
    requirements = [Requirement(value) for value in
                    config['project']['dependencies'] + config['build-system']['requires']]
    requirement = next(value for value in requirements if value.name == name)
    for version in vulnerable_versions:
        assert version not in requirement.specifier, (name, version)
    assert patched_version in requirement.specifier, (name, patched_version)
