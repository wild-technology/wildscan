"""sbom.spdx.json is well formed, self-consistent and in step with the project."""
from __future__ import annotations

import json
import hashlib
import subprocess
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


def test_windows_delivery_licence_evidence_matches_the_inventory():
    sbom = _sbom()
    provenance = json.loads(sbom['creationInfo']['comment'])
    assert provenance['platform'] == 'Windows'
    assert provenance['python'].startswith('3.13.')
    bundle = REPO / 'third_party_licenses'
    manifest = json.loads((bundle / 'manifest.json').read_text(encoding='utf-8'))
    assert manifest['inventory_identity'] == sbom['documentNamespace'].rsplit('/', 1)[-1]
    evidence = []
    for package in sbom['packages']:
        if package.get('externalRefs'):
            evidence.extend(json.loads(package['comment']).get('licence_evidence', []))
    assert sorted(evidence, key=lambda x: (x['package'], x['path'])) == \
        sorted(manifest['files'], key=lambda x: (x['package'], x['path']))
    tracked = set(subprocess.check_output(
        ['git', 'ls-files', '-z', 'third_party_licenses'], cwd=REPO
    ).decode('utf-8').split('\0'))
    assert {'third_party_licenses/' + record['path'] for record in evidence} <= tracked
    for record in evidence:
        path = Path(record['path'])
        assert not path.is_absolute() and '..' not in path.parts
        data = (bundle / path).read_bytes()
        assert len(data) == record['bytes']
        assert hashlib.sha256(data).hexdigest() == record['sha256']


def test_optional_windows_constraints_match_recorded_package_versions():
    expected = {p['name']: p['versionInfo'] for p in _sbom()['packages']
                if p['name'] != 'wildscan' and any(
                    r.get('referenceType') == 'purl' for r in p.get('externalRefs', []))}
    constraints = {}
    for line in (REPO / 'constraints-windows-py313.txt').read_text().splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        req = Requirement(line)
        constraints[canonicalize_name(req.name)] = str(req.specifier).removeprefix('==')
    assert constraints == expected
