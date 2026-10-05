"""sbom.spdx.json is well formed, self-consistent and in step with the project."""
from __future__ import annotations

import json
import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

import wildscan

REPO = Path(__file__).resolve().parents[1]
DOCUMENT_ID = 'SPDXRef-DOCUMENT'


def _sbom() -> dict:
    return json.loads((REPO / 'sbom.spdx.json').read_text(encoding='utf-8'))


def _project() -> dict:
    return tomllib.loads((REPO / 'pyproject.toml').read_text(encoding='utf-8'))['project']


def _by_name(sbom: dict) -> dict[str, dict]:
    return {canonicalize_name(package['name']): package for package in sbom['packages']}


def test_document_has_required_fields():
    sbom = _sbom()
    assert sbom['spdxVersion'] == 'SPDX-2.3'
    assert sbom['dataLicense'] == 'CC0-1.0'
    assert sbom['SPDXID'] == DOCUMENT_ID
    assert sbom['name']
    assert sbom['documentNamespace'].startswith('https://')
    assert sbom['creationInfo']['created'].endswith('Z')
    assert sbom['creationInfo']['creators']
    assert sbom['documentDescribes']


def test_spdx_ids_are_unique_and_relationships_reference_them():
    sbom = _sbom()
    ids = [package['SPDXID'] for package in sbom['packages']]
    assert len(ids) == len(set(ids))
    known = set(ids) | {DOCUMENT_ID}
    for described in sbom['documentDescribes']:
        assert described in known, described
    for relationship in sbom['relationships']:
        assert relationship['spdxElementId'] in known, relationship
        assert relationship['relatedSpdxElement'] in known, relationship
        assert relationship['relationshipType'], relationship


def test_every_package_has_the_fields_spdx_requires():
    for package in _sbom()['packages']:
        for key in ('name', 'downloadLocation', 'licenseConcluded',
                    'licenseDeclared', 'copyrightText'):
            assert package.get(key), (package['SPDXID'], key)
        assert package['filesAnalyzed'] is False, package['SPDXID']


def test_root_package_is_wildscan_at_the_current_version():
    sbom = _sbom()
    (root_id,) = sbom['documentDescribes']
    root = next(p for p in sbom['packages'] if p['SPDXID'] == root_id)
    assert root['name'] == 'wildscan'
    assert root['versionInfo'] == wildscan.__version__
    assert root['licenseDeclared'] == 'MIT'


def test_runtime_dependencies_have_entries_and_depends_on_relationships():
    sbom = _sbom()
    (root_id,) = sbom['documentDescribes']
    packages = _by_name(sbom)
    depends_on = {r['relatedSpdxElement'] for r in sbom['relationships']
                  if r['spdxElementId'] == root_id and r['relationshipType'] == 'DEPENDS_ON'}
    for value in _project()['dependencies']:
        name = canonicalize_name(Requirement(value).name)
        assert name in packages, f'{name} has no SBOM package entry'
        assert packages[name]['SPDXID'] in depends_on, name


def test_dev_dependencies_are_marked_as_dev_dependencies():
    sbom = _sbom()
    (root_id,) = sbom['documentDescribes']
    packages = _by_name(sbom)
    dev_of = {r['spdxElementId'] for r in sbom['relationships']
              if r['relatedSpdxElement'] == root_id
              and r['relationshipType'] == 'DEV_DEPENDENCY_OF'}
    for value in _project()['optional-dependencies']['dev']:
        name = canonicalize_name(Requirement(value).name)
        assert packages[name]['SPDXID'] in dev_of, name


def test_realityscan_is_an_external_prerequisite_not_a_dependency():
    sbom = _sbom()
    realityscan = next(p for p in sbom['packages'] if p['name'] == 'RealityScan')
    related = [r['relationshipType'] for r in sbom['relationships']
               if r['relatedSpdxElement'] == realityscan['SPDXID']]
    assert related == ['HAS_PREREQUISITE']
    assert 'not packaged' in realityscan['comment']


def test_listed_versions_satisfy_the_declared_floors():
    packages = _by_name(_sbom())
    for value in _project()['dependencies']:
        requirement = Requirement(value)
        version = packages[canonicalize_name(requirement.name)]['versionInfo']
        assert requirement.specifier.contains(version), (requirement.name, version)
