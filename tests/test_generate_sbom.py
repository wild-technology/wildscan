"""Installed SPDX inventory, dependency scopes and supplied licence evidence."""
from __future__ import annotations

import copy
from email.message import Message
import hashlib
import json
from pathlib import Path

from packaging.markers import default_environment
import pytest

from scripts import generate_sbom as sbom

CREATED = '2026-10-26T00:00:00Z'
PROVENANCE = {'source_revision': 'a' * 40, 'source_content_sha256': 'b' * 64,
              'source_worktree_modified': False}


class Distribution:
    def __init__(self, root, name, version='1.0', requires=(), expression='MIT',
                 license_text=b'MIT License\nCopyright example\n', legacy=None):
        self.metadata = Message()
        self.metadata['Name'] = name
        self.metadata['Version'] = version
        if expression is not None:
            self.metadata['License-Expression'] = expression
        if legacy:
            self.metadata['License'] = legacy
        self.version = version
        self.requires = list(requires)
        self.root = root
        self.files = []
        if license_text is not None:
            relative = f'{name}-{version}.dist-info/licenses/LICENSE.txt'
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(license_text)
            self.files.append(relative)

    def locate_file(self, relative):
        return self.root / relative


@pytest.fixture
def project(tmp_path):
    root = tmp_path / 'source'
    root.mkdir()
    (root / 'pyproject.toml').write_text('''
[build-system]
requires = ["setuptools>=80"]
[project]
name = "wildscan"
version = "2.0.0"
dependencies = ["camera-lib[fast]>=1; sys_platform == 'win32'", "shared>=2"]
[project.optional-dependencies]
dev = ["pytest>=8"]
''', encoding='utf-8')
    library = tmp_path / 'private-user-location' / 'site-packages'
    distributions = [
        Distribution(library, 'wildscan', '2.0.0'),
        Distribution(library, 'camera-lib', requires=[
            'shared>=2', 'win-helper; sys_platform == "win32"',
            'speedup; extra == "fast"', 'docs-only; extra == "docs"']),
        Distribution(library, 'shared', '2.4'),
        Distribution(library, 'win-helper'),
        Distribution(library, 'speedup'),
        Distribution(library, 'pytest', '9.1', requires=['shared>=2', 'dev-util']),
        Distribution(library, 'dev-util'),
        Distribution(library, 'setuptools', '83.0', requires=['shared>=2']),
        Distribution(library, 'pip', '26.2'),
        Distribution(library, 'unused-tool', license_text=None, expression=None),
    ]
    environment = default_environment()
    environment.update({'platform_system': 'Windows', 'platform_machine': 'AMD64',
                        'sys_platform': 'win32', 'os_name': 'nt',
                        'python_full_version': '3.13.9', 'python_version': '3.13'})
    return root, distributions, environment


def generate(project, **kwargs):
    root, distributions, environment = project
    return sbom.generate_inventory(root, distributions=distributions,
                                   environment=environment, provenance=PROVENANCE,
                                   created=CREATED, **kwargs)


def by_name(document):
    return {package['name']: package for package in document['packages']}


def test_all_installed_packages_and_transitive_scopes_are_inventoried(project):
    document, payloads, manifest = generate(project)
    packages = by_name(document)
    assert set(packages) == {dist.metadata['Name'] for dist in project[1]} | {
        'RealityScan', 'niraclient', 'Python'}
    assert json.loads(packages['camera-lib']['comment'])['categories'] == ['runtime']
    assert json.loads(packages['speedup']['comment'])['categories'] == ['runtime']
    assert json.loads(packages['dev-util']['comment'])['categories'] == ['development']
    assert json.loads(packages['setuptools']['comment'])['categories'] == ['build']
    assert json.loads(packages['shared']['comment'])['categories'] == [
        'build', 'development', 'runtime']
    assert json.loads(packages['unused-tool']['comment'])['categories'] == ['environment']
    assert packages['unused-tool']['licenseDeclared'] == 'NOASSERTION'
    relationships = {(r['spdxElementId'], r['relationshipType'], r['relatedSpdxElement'])
                     for r in document['relationships']}
    assert (sbom.package_id('camera-lib'), 'DEPENDS_ON', sbom.package_id('speedup')) in relationships
    assert (sbom.package_id('dev-util'), 'DEV_DEPENDENCY_OF', sbom.PROJECT_ID) in relationships
    assert (sbom.package_id('setuptools'), 'BUILD_DEPENDENCY_OF', sbom.PROJECT_ID) in relationships
    assert 'docs-only' not in packages
    assert len(payloads) == len(manifest['files'])


def test_platform_markers_do_not_label_other_platform_packages_as_runtime(project):
    root, distributions, environment = project
    environment.update({'platform_system': 'Darwin', 'sys_platform': 'darwin', 'os_name': 'posix'})
    document, _payloads, _manifest = generate((root, distributions, environment))
    assert json.loads(by_name(document)['camera-lib']['comment'])['categories'] == ['environment']
    provenance = json.loads(document['creationInfo']['comment'])
    assert provenance['platform'] == 'Darwin'
    assert provenance['python'] == '3.13.9'
    assert 'Darwin' not in document['name']  # normalized lowercase identity
    assert 'darwin' in document['name']


def test_custom_matplotlib_and_seaborn_licences_have_full_text_and_hashes(project):
    root, distributions, environment = project
    for name in ('matplotlib', 'seaborn'):
        distributions.append(Distribution(distributions[0].root, name, expression=None,
            license_text=f'Custom supplied terms for {name}\nDo not guess from classifiers.\n'.encode()))
    document, payloads, manifest = generate((root, distributions, environment))
    extracted = {item['licenseId']: item['extractedText']
                 for item in document['hasExtractedLicensingInfos']}
    for name in ('matplotlib', 'seaborn'):
        package = by_name(document)[name]
        assert package['licenseDeclared'].startswith(f'LicenseRef-{name}-')
        assert f'Custom supplied terms for {name}' in extracted[package['licenseDeclared']]
        record = next(record for record in manifest['files'] if record['package'] == name)
        assert hashlib.sha256(payloads[record['path']]).hexdigest() == record['sha256']
    assert 'private-user-location' not in json.dumps(document)
    assert 'private-user-location' not in json.dumps(manifest)


def test_ids_namespaces_and_fixed_timestamp_are_deterministic(project):
    first = generate(project)
    root, distributions, environment = project
    second = generate((root, list(reversed(distributions)), environment))
    assert first == second
    assert first[0]['creationInfo']['created'] == CREATED
    sbom.validate_document(first[0])


def test_external_interpreter_and_tools_are_prerequisites_not_bundled(project):
    document, _payloads, _manifest = generate(project)
    packages = by_name(document)
    assert packages['Python']['versionInfo'] == '3.13.9'
    assert packages['Python']['licenseDeclared'] == 'PSF-2.0'
    assert packages['RealityScan']['versionInfo'] == '2.2'
    for name in ('Python', 'RealityScan', 'niraclient'):
        package = packages[name]
        assert 'not packaged' in package['comment']
        assert [r['relationshipType'] for r in document['relationships']
                if r['relatedSpdxElement'] == package['SPDXID']] == ['HAS_PREREQUISITE']


def test_missing_active_dependency_cannot_produce_a_complete_inventory(project):
    root, distributions, environment = project
    distributions = [distribution for distribution in distributions
                     if distribution.metadata['Name'] != 'speedup']
    with pytest.raises(ValueError, match='not installed: speedup'):
        generate((root, distributions, environment))


def test_missing_isolated_build_requirement_is_reported_not_fabricated(project):
    root, distributions, environment = project
    distributions = [distribution for distribution in distributions
                     if distribution.metadata['Name'] != 'setuptools']
    document, _payloads, _manifest = generate((root, distributions, environment))
    assert 'setuptools' not in by_name(document)
    provenance = json.loads(document['creationInfo']['comment'])
    assert provenance['build_requirements_not_installed_in_this_interpreter'] == ['setuptools']


def test_export_and_verify_check_licence_payload_and_manifest_integrity(project, tmp_path):
    document, payloads, manifest = generate(project)
    output = tmp_path / 'out' / 'sbom.spdx.json'
    bundle = tmp_path / 'out' / 'third_party_licenses'
    sbom.export_inventory(output, bundle, document, payloads, manifest)
    sbom.verify_inventory(document, document, bundle)
    changed = copy.deepcopy(document)
    changed['packages'][0]['versionInfo'] = 'changed'
    with pytest.raises(ValueError, match='stale'):
        sbom.verify_inventory(document, changed, bundle)
    record = manifest['files'][0]
    (bundle / record['path']).write_bytes(b'modified terms')
    with pytest.raises(ValueError, match='payload has changed'):
        sbom.verify_inventory(document, document, bundle)


def test_bundle_manifest_cannot_hide_a_missing_licence(project, tmp_path):
    document, payloads, manifest = generate(project)
    output = tmp_path / 'sbom.json'
    bundle = tmp_path / 'licenses'
    sbom.export_inventory(output, bundle, document, payloads, manifest)
    manifest['files'].pop()
    (bundle / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(ValueError, match='does not match the SBOM'):
        sbom.verify_inventory(document, document, bundle)


def test_generation_preserves_an_unowned_existing_directory(project, tmp_path):
    document, payloads, manifest = generate(project)
    bundle = tmp_path / 'licenses'
    bundle.mkdir()
    (bundle / 'important.txt').write_bytes(b'preserve')
    with pytest.raises(ValueError, match='not an owned generated bundle'):
        sbom.export_inventory(tmp_path / 'sbom.json', bundle, document, payloads, manifest)
    assert (bundle / 'important.txt').read_bytes() == b'preserve'


def test_url_provenance_omits_credentials_local_paths_and_query_secrets():
    assert sbom.safe_url('https://name:password@example.org/project?token=private#data') == 'https://example.org/project'
    assert sbom.safe_url('file:///Users/private/source') == 'NOASSERTION'
    assert sbom._relative_label('/Users/private/site-packages/LICENSE') == 'LICENSE'
    assert sbom._relative_label('../../../private/LICENSE') == 'LICENSE'


def test_licence_collector_excludes_python_modules_caches_and_binaries(tmp_path):
    distribution = Distribution(tmp_path / 'site-packages', 'packaging', '26.3')
    retained = {
        'packaging-26.3.dist-info/licenses/vendor/terms.rst': b'Full nested licence terms.\n',
        'packaging-26.3.dist-info/licenses/vendor/THIRD_PARTY_TERMS': b'Full attribution.\n',
        'packaging/fonts/LICENSE_DEJAVU': b'Supplied font licence.\n',
    }
    excluded = {
        'packaging/licenses/__init__.py': b'def canonicalize_license_expression(): pass\n',
        'packaging/licenses/_spdx.py': b'LICENSES = {}\n',
        'packaging/licenses/__pycache__/__init__.cpython-313.pyc': b'\xa7\r\r\n\x00bytecode',
        'packaging/licenses/README.md': b'Implementation documentation, not licence text.\n',
        'packaging-26.3.dist-info/licenses/vendor/helper.py': b'import os\n',
        'packaging-26.3.dist-info/licenses/vendor/helper.pyd': b'MZ\x00binary',
        'packaging-26.3.dist-info/licenses/vendor/payload.txt': b'not text\x00binary',
        'packaging-26.3.dist-info/licenses/__pycache__/LICENSE.txt': b'cache artifact',
    }
    for relative, payload in (retained | excluded).items():
        path = distribution.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        distribution.files.append(relative)
    files = dict(sbom._license_files(distribution))
    assert set(files) == set(retained) | {
        'packaging-26.3.dist-info/licenses/LICENSE.txt'}
    for relative, payload in retained.items():
        assert files[relative] == payload
    assert not any(relative.endswith(('.py', '.pyc', '.pyd')) for relative in files)


def test_explicit_license_file_metadata_preserves_unusual_text_names(tmp_path):
    distribution = Distribution(tmp_path / 'site-packages', 'example', license_text=None)
    distribution.metadata['License-File'] = 'custom/TERMS.agreement'
    relative = 'example-1.0.dist-info/licenses/custom/TERMS.agreement'
    path = distribution.root / relative
    path.parent.mkdir(parents=True)
    path.write_bytes(b'Explicit supplied licence terms with a nonstandard filename.\n')
    distribution.files.append(relative)
    assert dict(sbom._license_files(distribution)) == {relative: path.read_bytes()}
