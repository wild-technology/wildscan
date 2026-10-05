"""requirements.txt and the declared package list agree with pyproject.toml."""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

REPO = Path(__file__).resolve().parents[1]


def _project() -> dict:
    return tomllib.loads((REPO / 'pyproject.toml').read_text(encoding='utf-8'))


def _normalised(requirements: list[Requirement]) -> dict[str, tuple[str, str]]:
    result = {}
    for requirement in requirements:
        name = canonicalize_name(requirement.name)
        assert name not in result, f'{name} is declared twice'
        result[name] = (str(requirement.specifier), str(requirement.marker))
    return result


def _requirements_txt() -> list[Requirement]:
    parsed = []
    for line in (REPO / 'requirements.txt').read_text(encoding='utf-8-sig').splitlines():
        line = re.split(r'\s+#', line, maxsplit=1)[0].strip()
        if line and not line.startswith('#'):
            parsed.append(Requirement(line))
    return parsed


def test_requirements_txt_equals_pyproject_runtime_and_dev_lists():
    project = _project()['project']
    expected = _normalised([Requirement(value) for value in project['dependencies']
                            + project['optional-dependencies']['dev']])
    actual = _normalised(_requirements_txt())
    assert actual == expected, {
        'missing': sorted(expected.keys() - actual.keys()),
        'extra': sorted(actual.keys() - expected.keys()),
        'different': sorted(name for name in expected.keys() & actual.keys()
                            if expected[name] != actual[name]),
    }


def test_compatibility_extras_only_repeat_runtime_dependencies():
    project = _project()['project']
    runtime = _normalised([Requirement(value) for value in project['dependencies']])
    extras = project['optional-dependencies']
    for extra in extras.keys() - {'dev'}:
        for name, declared in _normalised([Requirement(v) for v in extras[extra]]).items():
            assert runtime.get(name) == declared, (extra, name)


def test_packaging_is_declared_for_the_test_suite():
    project = _project()['project']
    names = {canonicalize_name(Requirement(value).name)
             for value in project['optional-dependencies']['dev']}
    assert 'packaging' in names


def test_declared_packages_match_importable_package_directories():
    declared = set(_project()['tool']['setuptools']['packages'])
    found = set()
    for init in REPO.glob('**/__init__.py'):
        relative = init.parent.relative_to(REPO)
        if any(part in {'tests', 'scripts', '.venv', 'build', 'dist'}
               or part.startswith('.') or part.endswith('.egg-info')
               for part in relative.parts):
            continue
        found.add('.'.join(relative.parts))
    assert declared == found, {
        'declared_but_missing': sorted(declared - found),
        'present_but_undeclared': sorted(found - declared),
    }
    for package in declared:
        directory = REPO.joinpath(*package.split('.'))
        assert any(directory.glob('*.py')), package


def test_package_data_patterns_match_files():
    data = _project()['tool']['setuptools']['package-data']
    for package, patterns in data.items():
        directory = REPO.joinpath(*package.split('.'))
        for pattern in patterns:
            assert list(directory.glob(pattern)), (package, pattern)
