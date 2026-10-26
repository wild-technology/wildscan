"""Declared dependencies exclude releases with known vulnerabilities.

That requirements.txt equals the pyproject.toml lists is checked, with
normalised names, in tests/test_packaging_requirements.py.
"""
from pathlib import Path
import tomllib

from packaging.requirements import Requirement
import pytest


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
