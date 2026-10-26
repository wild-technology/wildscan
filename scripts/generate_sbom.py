#!/usr/bin/env python3
"""Inventory this interpreter's installed distributions as SPDX 2.3.

Uses importlib.metadata and packaging's requirement/marker/SPDX parsers. No
network requests or package installation. The licence bundle preserves shipped
bytes; its manifest records their hashes. This is an installed-package inventory,
not a complete inventory of native libraries embedded in binary wheels or of the
separately installed RealityScan application.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
import tomllib
from urllib.parse import quote, urlsplit, urlunsplit

try:
    from packaging.licenses import canonicalize_license_expression, InvalidLicenseExpression
    from packaging.markers import default_environment
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name
except ImportError as exc:
    raise SystemExit('SBOM tooling requires packaging>=24.2 in this interpreter; '
                     'install the development tooling first.') from exc

FORMAT = 'wildscan-installed-licenses-1'
DOCUMENT_ID = 'SPDXRef-DOCUMENT'
PROJECT_ID = 'SPDXRef-Package-wildscan'
REPOSITORY = 'https://github.com/wild-technology/wildscan'
TOOL_VERSION = '1.0'
LICENSE_WORD = re.compile(r'^(licen[cs]e|copying|notice|copyright)([._-]|$)', re.I)
SOURCE_SUFFIXES = {'.py', '.toml', '.json', '.xml', '.bat', '.vbs', '.txt'}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def package_id(name: str) -> str:
    return 'SPDXRef-Package-' + canonicalize_name(name)


def safe_url(value: str) -> str:
    """Only retain public-looking HTTP(S) origins, never credentials or queries."""
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in ('https', 'http') or not parsed.hostname:
            return 'NOASSERTION'
        host = parsed.hostname
        if parsed.port:
            host += f':{parsed.port}'
        return urlunsplit((parsed.scheme, host, parsed.path, '', ''))
    except ValueError:
        return 'NOASSERTION'


def _relative_label(value: str) -> str:
    path = PurePosixPath(value.replace('\\', '/'))
    if path.is_absolute() or '..' in path.parts or re.match(r'^[A-Za-z]:', str(path)):
        return path.name
    return str(path)


def _license_files(distribution) -> list[tuple[str, bytes]]:
    """Export only licence/notice payloads from the installed RECORD file list."""
    result = {}
    declared = distribution.metadata.get_all('License-File') or []
    declared_names = {PurePosixPath(value.replace('\\', '/')).name for value in declared}
    for entry in distribution.files or ():
        parts = PurePosixPath(str(entry).replace('\\', '/')).parts
        lower = [part.lower() for part in parts]
        if not (LICENSE_WORD.match(parts[-1]) or parts[-1] in declared_names
                or any(part in ('licenses', 'licences') or part.endswith('.licenses')
                       for part in lower)):
            continue
        path = Path(distribution.locate_file(entry))
        if path.is_file():
            result[_relative_label(str(entry))] = path.read_bytes()
    inline = distribution.metadata.get('License', '').strip()
    # A metadata-only custom licence is still useful supplied evidence.
    if not result and inline:
        result['METADATA-License.txt'] = inline.encode('utf-8')
    return sorted(result.items())


def _decode(data: bytes) -> str:
    try:
        return data.decode('utf-8-sig')
    except UnicodeDecodeError:
        return data.decode('latin-1')


def _license_expression(distribution, files, extracted: dict) -> str:
    name = canonicalize_name(distribution.metadata['Name'])
    expression = distribution.metadata.get('License-Expression', '').strip()
    legacy = distribution.metadata.get('License', '').strip()
    for value in (expression, legacy if '\n' not in legacy else ''):
        if value and value not in ('UNKNOWN', 'NOASSERTION', 'NONE'):
            try:
                normalized = canonicalize_license_expression(value)
                # An upstream LicenseRef without its text is not resolved evidence.
                if 'LicenseRef-' not in normalized:
                    return normalized
            except InvalidLicenseExpression:
                pass
    if not files:
        return 'NOASSERTION'
    text = '\n\n'.join(f'Installed licence file: {label}\n\n{_decode(data)}'
                       for label, data in files)
    identifier = f'LicenseRef-{name}-{digest(text.encode())[:16]}'
    extracted[identifier] = {
        'licenseId': identifier,
        'name': f'{distribution.metadata["Name"]} supplied licence text',
        'extractedText': text,
        'comment': 'Shipped text retained verbatim in the licence bundle. '
                   'A custom identifier avoids inferring a licence from classifiers.',
    }
    return identifier


def _active(requirement, extras, environment) -> bool:
    return requirement.marker is None or any(
        requirement.marker.evaluate({**environment, 'extra': extra})
        for extra in ({''} | set(extras)))


def _dependency_graph(installed, seeds, environment):
    """Resolve markers/extras to a fixed point separately for each scope."""
    categories = {name: set() for name in installed}
    edges = set()
    missing_build = []
    for scope, requirements in seeds.items():
        active_extras = {}
        queue = []

        def add(requirement, parent=None, optional_missing=False):
            if not _active(requirement, {''}, environment) and parent is None:
                return
            name = canonicalize_name(requirement.name)
            if name not in installed:
                if optional_missing:
                    missing_build.append(requirement.name)
                    return
                raise ValueError(f'Active {scope} dependency is not installed: {requirement.name}')
            version = installed[name].version
            if requirement.specifier and not requirement.specifier.contains(version, prereleases=True):
                raise ValueError(f'Installed {name} {version} does not satisfy {requirement.specifier}')
            if parent:
                edges.add((parent, name))
            extras = set(requirement.extras)
            prior = active_extras.get(name)
            if prior is None or not extras <= prior:
                active_extras.setdefault(name, set()).update(extras)
                queue.append(name)
            categories[name].add(scope)

        for value in requirements:
            add(Requirement(value), optional_missing=scope == 'build')
        while queue:
            parent = queue.pop(0)
            for value in installed[parent].requires or ():
                requirement = Requirement(value)
                if _active(requirement, active_extras[parent], environment):
                    add(requirement, parent)
    # Every installed distribution remains visible, including unrelated tools.
    # Base dependency edges are observable even when no project scope reaches it.
    for parent, distribution in installed.items():
        for value in distribution.requires or ():
            requirement = Requirement(value)
            if _active(requirement, {''}, environment):
                child = canonicalize_name(requirement.name)
                if child not in installed:
                    raise ValueError(f'Installed {parent} has a missing active dependency: {child}')
                if requirement.specifier and not requirement.specifier.contains(
                        installed[child].version, prereleases=True):
                    raise ValueError(f'Installed {child} does not satisfy dependency of {parent}')
                edges.add((parent, child))
        if not categories[parent]:
            categories[parent].add('environment')
    return categories, edges, sorted(set(missing_build))


def _source_version(root: Path, project: dict) -> str:
    if project.get('version'):
        return project['version']
    tree = ast.parse((root / 'wildscan' / '__init__.py').read_text(encoding='utf-8'))
    for statement in tree.body:
        if isinstance(statement, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == '__version__'
                for target in statement.targets):
            return str(ast.literal_eval(statement.value))
    raise ValueError('Project source has no literal version')


def source_context(root: Path, source_ref=None) -> dict:
    def git(*args):
        return subprocess.check_output(['git', '-C', str(root), *args],
                                       text=True, stderr=subprocess.DEVNULL).strip()
    head = git('rev-parse', 'HEAD')
    if source_ref and git('rev-parse', f'{source_ref}^{{commit}}') != head:
        raise ValueError('--source-ref must identify the checked-out commit')
    records = []
    for relative in sorted(git('ls-files', '-z').split('\0')):
        if not relative or relative.startswith(('docs/', 'tests/', 'third_party_licenses/')):
            continue
        path = root / relative
        if relative in ('sbom.spdx.json', 'THIRD_PARTY_NOTICES.md'):
            continue
        if path.is_file() and (path.suffix.lower() in SOURCE_SUFFIXES
                               or path.name == 'LICENSE'):
            records.append((relative, digest(path.read_bytes())))
    changed = set(git('diff', '--name-only', 'HEAD', '-z').split('\0'))
    return {
        'repository': REPOSITORY,
        'source_revision': head,
        'source_content_sha256': digest(canonical_json(records).encode()),
        'source_content_scope': 'Tracked code, native workflows, configuration and LICENSE; '
                                'excludes tests, docs, generated SBOM and licence bundle.',
        'source_worktree_modified': any(relative in changed for relative, _sha in records),
        'generator_sha256': digest(Path(__file__).read_bytes()),
    }


def generate_inventory(root: Path, *, distributions=None, environment=None,
                       provenance=None, created=None):
    config = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))
    project = config['project']
    environment = dict(environment or default_environment())
    installed = {}
    for distribution in distributions if distributions is not None else metadata.distributions():
        name = canonicalize_name(distribution.metadata['Name'])
        if name in installed:
            raise ValueError(f'Multiple installed distributions have the name {name}')
        installed[name] = distribution
    if 'wildscan' not in installed:
        raise ValueError('Wild Scan must be installed in this interpreter (editable checkout)')
    version = _source_version(root, project)
    if installed['wildscan'].version != version:
        raise ValueError('Installed Wild Scan version differs from source; refresh the editable install')
    runtime = project.get('dependencies', [])
    dev = project.get('optional-dependencies', {}).get('dev', [])
    build = list(config.get('build-system', {}).get('requires', []))
    build += [name for name in ('pip', 'build', 'wheel') if name in installed]
    seeds = {'runtime': runtime, 'development': dev, 'build': build}
    categories, edges, missing_build = _dependency_graph(installed, seeds, environment)
    categories['wildscan'] = {'source'}
    provenance = dict(provenance or source_context(root))
    provenance.update({
        'format': 'wildscan-installed-inventory-1',
        'python': environment['python_full_version'],
        'implementation': environment['implementation_name'],
        'platform': environment['platform_system'],
        'machine': environment['platform_machine'],
        'markers': environment,
        'project_configuration_sha256': digest((root / 'pyproject.toml').read_bytes()),
        'scope': 'All installed Python distributions, with project runtime/development/build '
                 'closures and environment-only packages. Native libraries embedded in wheels, '
                 'OS packages, CUDA, isolated build environments and external tools are not '
                 'independently inventoried. Licence files supplied by wheels are retained.',
        'build_requirements_not_installed_in_this_interpreter': missing_build,
        'extras_scope': 'Project-declared extras are resolved. Extra selections for unrelated '
                        'installed tools cannot be inferred from installed metadata.',
    })
    extracted = {}
    payloads = {}
    bundle_records = []
    packages = []
    relations = {(DOCUMENT_ID, 'DESCRIBES', PROJECT_ID)}
    for name, distribution in sorted(installed.items()):
        files = _license_files(distribution)
        declared = _license_expression(distribution, files, extracted)
        records = []
        for label, data in files:
            basename = re.sub(r'[^A-Za-z0-9._-]', '-', PurePosixPath(label).name)
            version_path = re.sub(r'[^A-Za-z0-9._-]', '-', distribution.version)
            relative = f'{name}-{version_path}/{digest(data)[:16]}-{basename}'
            payloads[relative] = data
            record = {'package': name, 'version': distribution.version,
                      'source': label, 'path': relative,
                      'sha256': digest(data), 'bytes': len(data)}
            bundle_records.append(record)
            records.append(record)
        comment = {'categories': sorted(categories[name]),
                   'licence_evidence': records,
                   'licence_scope': 'Supplied metadata/text; no independent legal conclusion. '
                                    'Hashes identify licence payloads, not complete wheel archives.'}
        package = {
            'SPDXID': package_id(name), 'name': name, 'versionInfo': distribution.version,
            'downloadLocation': 'NOASSERTION', 'filesAnalyzed': False,
            'licenseDeclared': declared, 'licenseConcluded': 'NOASSERTION',
            'copyrightText': 'NOASSERTION',
            'externalRefs': [{'referenceCategory': 'PACKAGE-MANAGER', 'referenceType': 'purl',
                              'referenceLocator': f'pkg:pypi/{name}@{quote(distribution.version)}'}],
            'comment': canonical_json(comment),
        }
        homepage = safe_url(distribution.metadata.get('Home-page', ''))
        if homepage != 'NOASSERTION':
            package['homepage'] = homepage
        if name == 'wildscan':
            package.update({'downloadLocation': f'git+{REPOSITORY}.git@{provenance["source_revision"]}',
                            'licenseDeclared': 'MIT', 'licenseConcluded': 'MIT',
                            'supplier': 'Organization: Wild Technologies',
                            'homepage': REPOSITORY,
                            'sourceInfo': 'Editable source checkout, not an application binary. '
                                          'Source revision and content identity are in creationInfo.comment.'})
        packages.append(package)
        for category, relationship in (('development', 'DEV_DEPENDENCY_OF'),
                                       ('build', 'BUILD_DEPENDENCY_OF'),
                                       ('runtime', 'RUNTIME_DEPENDENCY_OF')):
            if category in categories[name]:
                relations.add((package_id(name), relationship, PROJECT_ID))
    for parent, child in edges:
        if parent != child:
            relations.add((package_id(parent), 'DEPENDS_ON', package_id(child)))
    for value in runtime:
        req = Requirement(value)
        if _active(req, {''}, environment):
            relations.add((PROJECT_ID, 'DEPENDS_ON', package_id(req.name)))
    for name, identifier, location, comment in (
        ('Python', 'SPDXRef-Prerequisite-Python', 'https://www.python.org/',
         'External Python interpreter; exact running version recorded, not packaged with Wild Scan. '
         'PSF-2.0 identifies the interpreter licence; historical/bundled interpreter components '
         'are outside this installed-distribution inventory.'),
        ('RealityScan', 'SPDXRef-Prerequisite-RealityScan', 'https://www.realityscan.com/',
         'RealityScan 2.2 is installed/licensed separately; not packaged or inventoried here.'),
        ('niraclient', 'SPDXRef-Prerequisite-niraclient', 'https://github.com/NiraOfficial/niraclient',
         'Optional external checkout for Nira publishing only; not packaged or inventoried here.'),
    ):
        package = {'SPDXID': identifier, 'name': name, 'downloadLocation': location,
                   'filesAnalyzed': False, 'licenseDeclared': 'NOASSERTION',
                   'licenseConcluded': 'NOASSERTION', 'copyrightText': 'NOASSERTION',
                   'comment': comment}
        if name == 'RealityScan':
            package['versionInfo'] = '2.2'
        elif name == 'Python':
            package.update({'versionInfo': environment['python_full_version'],
                            'licenseDeclared': 'PSF-2.0'})
        packages.append(package)
        relations.add((PROJECT_ID, 'HAS_PREREQUISITE', identifier))
    identity = digest(canonical_json({'packages': packages, 'relationships': sorted(relations),
                                     'provenance': provenance}).encode())
    document = {
        'spdxVersion': 'SPDX-2.3', 'dataLicense': 'CC0-1.0', 'SPDXID': DOCUMENT_ID,
        'name': f'wildscan-{version}-{environment["platform_system"].lower()}-python-{environment["python_version"]}',
        'documentNamespace': f'{REPOSITORY}/spdx/{identity}',
        'creationInfo': {'created': created or datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                         'creators': ['Organization: Wild Technologies',
                                      f'Tool: wildscan-installed-sbom-{TOOL_VERSION}'],
                         'comment': canonical_json(provenance)},
        'documentDescribes': [PROJECT_ID], 'packages': packages,
        'relationships': [{'spdxElementId': left, 'relationshipType': kind,
                           'relatedSpdxElement': right} for left, kind, right in sorted(relations)],
    }
    if extracted:
        document['hasExtractedLicensingInfos'] = [extracted[key] for key in sorted(extracted)]
    manifest = {'format': FORMAT, 'inventory_identity': identity,
                'provenance': provenance, 'files': bundle_records}
    validate_document(document)
    return document, payloads, manifest


def validate_document(document):
    """Contract checks complement official schema validation in release tooling."""
    ids = {DOCUMENT_ID}
    for package in document['packages']:
        identifier = package['SPDXID']
        if identifier in ids:
            raise ValueError(f'Duplicate SPDX identifier: {identifier}')
        ids.add(identifier)
    licenses = {item['licenseId'] for item in document.get('hasExtractedLicensingInfos', [])}
    for package in document['packages']:
        for identifier in re.findall(r'LicenseRef-[A-Za-z0-9.-]+', package['licenseDeclared']):
            if identifier not in licenses:
                raise ValueError(f'Unresolved supplied licence: {identifier}')
    for relation in document['relationships']:
        if relation['spdxElementId'] not in ids or relation['relatedSpdxElement'] not in ids:
            raise ValueError('Relationship points to an unknown SPDX element')


def _write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent,
                                     delete=False, suffix='.tmp', newline='\n') as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write('\n')
        temporary = Path(stream.name)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def export_inventory(output: Path, license_dir: Path, document, payloads, manifest):
    if license_dir.is_symlink():
        raise ValueError('Licence bundle must be a real directory, not a symbolic link')
    marker = license_dir / 'manifest.json'
    if license_dir.exists() and any(license_dir.iterdir()):
        try:
            previous = json.loads(marker.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise ValueError('Licence directory is not an owned generated bundle; use a fresh directory') from exc
        if previous.get('format') != FORMAT or previous.get('inventory_identity') != manifest['inventory_identity']:
            raise ValueError('Licence directory contains another inventory; use a fresh directory')
    for relative, data in payloads.items():
        target = license_dir / relative
        parts = PurePosixPath(relative).parts
        if target.is_symlink() or any(
                license_dir.joinpath(*parts[:index]).is_symlink()
                for index in range(1, len(parts))):
            raise ValueError('Licence bundle contains a symbolic-link path')
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.read_bytes() != data:
            raise ValueError(f'Existing licence payload differs: {relative}')
        if not target.exists():
            target.write_bytes(data)
    _write_json(marker, manifest)
    _write_json(output, document)


def verify_inventory(existing, expected, license_dir: Path):
    if existing != expected:
        raise ValueError('SBOM is stale for this interpreter/source: regenerate it in the target environment')
    marker = json.loads((license_dir / 'manifest.json').read_text(encoding='utf-8'))
    if marker.get('format') != FORMAT:
        raise ValueError('Licence bundle has no recognized manifest')
    identity = existing['documentNamespace'].rsplit('/', 1)[-1]
    if marker.get('inventory_identity') != identity:
        raise ValueError('Licence bundle belongs to another inventory')
    evidence = []
    for package in existing['packages']:
        try:
            comment = json.loads(package.get('comment', '{}'))
        except ValueError:
            continue
        if isinstance(comment, dict):
            evidence.extend(comment.get('licence_evidence', []))
    if marker.get('files') != evidence:
        raise ValueError('Licence bundle manifest does not match the SBOM evidence')
    expected_paths = {record['path'] for record in evidence} | {'manifest.json'}
    actual_paths = {path.relative_to(license_dir).as_posix()
                    for path in license_dir.rglob('*') if path.is_file()}
    if actual_paths != expected_paths:
        raise ValueError('Licence bundle file membership differs from its manifest')
    for record in marker['files']:
        relative = PurePosixPath(record['path'])
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Unsafe licence bundle path')
        data = (license_dir / str(relative)).read_bytes()
        if digest(data) != record['sha256'] or len(data) != record['bytes']:
            raise ValueError(f'Licence payload has changed: {relative}')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', type=Path, default=Path(__file__).resolve().parents[1])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--output', type=Path)
    mode.add_argument('--verify', type=Path)
    parser.add_argument('--license-dir', required=True, type=Path)
    parser.add_argument('--source-ref', help='Commit/ref of this checkout; defaults to HEAD')
    parser.add_argument('--created', help='UTC ISO8601 timestamp, for reproducible output')
    args = parser.parse_args(argv)
    try:
        existing = json.loads(args.verify.read_text(encoding='utf-8')) if args.verify else None
        created = existing['creationInfo']['created'] if existing else args.created
        if not created and os.environ.get('SOURCE_DATE_EPOCH'):
            created = datetime.fromtimestamp(int(os.environ['SOURCE_DATE_EPOCH']), timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        if created:
            datetime.strptime(created, '%Y-%m-%dT%H:%M:%SZ')
        document, payloads, manifest = generate_inventory(
            args.project_root, provenance=source_context(args.project_root, args.source_ref),
            created=created)
        if existing:
            verify_inventory(existing, document, args.license_dir)
            print(f'Verified {len(document["packages"])} package entries and {len(payloads)} licence payloads.')
        else:
            export_inventory(args.output, args.license_dir, document, payloads, manifest)
            print(f'Generated {len(document["packages"])} package entries and {len(payloads)} licence payloads '
                  f'for {platform.system()} / Python {platform.python_version()}.')
        return 0
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f'SBOM tooling: {exc}\n')


if __name__ == '__main__':
    raise SystemExit(main())
