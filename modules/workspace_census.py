"""Workspace model: pure artifact-census logic, no UI, no subprocesses.

Lives in modules rather than wildscan because run_models.py needs the
census without dragging in the TUI package, and the layering rule is that wildscan may import modules, never the reverse.
wildscan.workspace remains as a re-export shim for compatibility.

Everything the app shows is derived from artifacts the canonical pipeline
already writes - the same signals the unattended drivers use to resume:

    raw_images/wildsync_intake.json Wild Sync Intake manifest
    preprocessed_images/            CLAHE output
    batched_images_by_zone/         zoning + batch_inputs.json fingerprint
    aligned_components/<zone>/      *.rsalign + *.rsalign.manifest.json
    <merge>/merge_report.json       merge terminal + EVALUATION_READY.txt
    <merge>/assembly/*.rsproj       the assembly project
    models_report.json              modelled components (run_models.py -
                                    the CURRENT writer; the two
                                    names below are legacy and kept
                                    readable so old workspaces still
                                    census)
    final_report.json               modelled components (legacy)
    fused_models_report.json        fused-component models + measured scale
    exports/<comp>/{obj,fbx,ply}    deliverable exports

Detection is deliberately read-only and cheap (no image hashing) so opening
a workspace is instant even on a NAS.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .align_fingerprint import (component_input_fingerprint,
                                model_input_fingerprint, project_state)
from .harvest_guard import assert_harvestable
from .wildsync_intake.intake import load_manifest

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".heif"}

STAGE_ORDER = [
    "intake", "preprocess", "batch",
    "align", "merge", "model", "export", "publish",
]

STAGE_TITLES = {
    "intake": "Wild Sync Intake",
    "preprocess": "Preprocess (CLAHE)",
    "batch": "Batch into Zones",
    "align": "Align Zones",
    "merge": "Merge Components",
    "model": "Generate Models",
    "export": "Export Deliverables",
    "publish": "Publish (Cesium / Nira)",
}


@dataclass
class StageStatus:
    key: str
    status: str = "pending"            # pending | partial | done | blocked
    summary: str = ""                  # one-line human summary
    details: list[str] = field(default_factory=list)

    @property
    def title(self) -> str:
        return STAGE_TITLES.get(self.key, self.key)


@dataclass
class ComponentInfo:
    key: str
    cameras: Optional[int] = None
    scale: Optional[float] = None
    scale_status: str = ""
    modelled: bool = False
    model_minutes: Optional[float] = None
    exported: list[str] = field(default_factory=list)


def _count_images(root: Path) -> int:
    if not root.is_dir():
        return 0
    n = 0
    for _dir, _sub, files in os.walk(root):
        n += sum(1 for f in files if Path(f).suffix.lower() in IMAGE_EXTS)
    return n


def _find_flight_logs(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(p for p in root.rglob("flight_log*_UTM.txt"))


# Model-report filenames, newest convention FIRST. run_models.py writes
# models_report.json (both modes); the other two are retired
# driver names, still read so an old workspace censuses correctly. Adding
# models_report.json here is what stops the portal re-ticking a finished
# model stage on every resume.
MODEL_REPORT_NAMES = ("models_report.json", "final_report.json",
                      "fused_models_report.json")


def _load_json(path: Path) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _records(report: dict, *keys: str) -> list[dict]:
    """The list of dict records under the first present key.

    _load_json guards I/O and JSON errors but not SHAPE: a report whose
    ``clusters`` is a dict (or whose entries are strings) used to crash the
    census with ``AttributeError: 'str' object has no attribute 'get'``
    Anything that is not a dict record is dropped.
    """
    for key in keys:
        value = report.get(key)
        if value:
            if isinstance(value, dict):
                value = list(value.values())
            if isinstance(value, list):
                return [r for r in value if isinstance(r, dict)]
    return []


class Workspace:
    """A results root as the pipeline understands it."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    # ------------------------------------------------------------ locations
    @property
    def raw_images(self) -> Path:
        return self.root / "raw_images"

    @property
    def preprocessed(self) -> Path:
        return self.root / "preprocessed_images"

    @property
    def batched(self) -> Path:
        return self.root / "batched_images_by_zone"

    @property
    def aligned(self) -> Path:
        return self.root / "aligned_components"

    @property
    def exports(self) -> Path:
        return self.root / "exports"

    def merge_dirs(self) -> list[Path]:
        """Merge outputs, newest report last. Any directory carrying a
        merge_report.json counts - names vary (merged*, nonhull,
        final_assembly)."""
        if not self.root.is_dir():
            return []
        hits = [p.parent for p in self.root.glob("*/merge_report.json")]
        return sorted(hits, key=lambda p: (p / "merge_report.json").stat().st_mtime)

    def latest_merge(self) -> Optional[Path]:
        dirs = self.merge_dirs()
        return dirs[-1] if dirs else None

    def assembly_project(self) -> Optional[Path]:
        merge = self.latest_merge()
        if not merge:
            return None
        for cand in sorted((merge / "assembly").glob("*.rsproj")):
            return cand
        return None

    # ------------------------------------------------------------ detection
    def detect(self) -> dict[str, StageStatus]:
        statuses = {key: getattr(self, f"_detect_{key}")() for key in STAGE_ORDER}
        interrupted = _load_json(self.root / 'interrupted_stage.json')
        stages = interrupted.get('stages')
        if interrupted.get('cancelled') is True and isinstance(stages, list):
            for key in stages:
                if not isinstance(key, str) or key not in statuses:
                    continue
                prior = statuses[key]
                statuses[key] = StageStatus(
                    key, 'blocked' if prior.status in ('pending', 'blocked')
                    else 'partial', 'interrupted run - retry required',
                    [prior.summary, *prior.details])
        return statuses

    def _detect_intake(self) -> StageStatus:
        """Status of the Wild Sync Intake stage from its manifest in
        raw_images/. A manifest that does not load as complete is never
        'done': a later stage must not act on a partial intake."""
        try:
            manifest = load_manifest(str(self.raw_images))
        except ValueError as exc:
            return StageStatus("intake", "partial",
                               "intake manifest is not complete", [str(exc)])
        if manifest is None:
            return StageStatus("intake", "pending",
                               "no Wild Sync intake manifest")
        try:
            matched = manifest["images"]["matched"]
            runs = len(manifest["sources"])
            variant = manifest["variant"]
        except (KeyError, TypeError) as exc:
            return StageStatus("intake", "partial",
                               "intake manifest is missing a field",
                               [f"missing {exc}"])
        return StageStatus("intake", "done",
                           f"{matched:,} {variant} images from {runs} run(s)")

    def _detect_preprocess(self) -> StageStatus:
        """Check completion and file metadata; the producer verifies SHA.

        The census stays inexpensive for image trees on a NAS. Content edits
        preserving both size and mtime require the producer's full check.
        """
        marker = self.root / 'preprocessed_images.manifest.json'
        manifest = _load_json(marker)
        try:
            if self.preprocessed.is_symlink() or self.preprocessed.is_junction():
                raise RuntimeError('preprocessed_images is a directory alias')
            assert_harvestable(str(self.preprocessed), logging.getLogger(__name__))
            leaves = [Path(root) / name
                      for root, _dirs, files in os.walk(self.preprocessed)
                      for name in files]
            if any(path.is_symlink() or path.is_junction()
                   for path in leaves):
                raise RuntimeError('preprocessed_images contains a file alias')
            images = {os.path.relpath(path, self.preprocessed): path
                      for path in leaves if path.suffix.lower() in IMAGE_EXTS}
            n = len(images)
            records = manifest.get('images')
            settings = manifest.get('settings')
            if not (manifest.get('schema') == 1
                    and manifest.get('status') == 'complete'
                    and isinstance(records, dict) and records
                    and isinstance(settings, dict)):
                if n or marker.exists():
                    return StageStatus('preprocess', 'partial',
                                       'preprocessing has no verified completion - retry required')
                return StageStatus('preprocess', 'pending',
                                   'not run (align falls back to raw imagery)')
            input_dir = settings.get('input_dir')
            if not isinstance(input_dir, str) or not os.path.isdir(input_dir):
                return StageStatus('preprocess', 'partial',
                                   'preprocessing source is unavailable - retry required')
            source_root = os.path.normcase(os.path.realpath(input_dir))
            output_root = os.path.normcase(os.path.realpath(self.preprocessed))
            try:
                common = os.path.commonpath((source_root, output_root))
            except ValueError:
                common = None
            if common in (source_root, output_root):
                raise RuntimeError('preprocessing input and output trees overlap')
            sources = {os.path.relpath(Path(root) / name, input_dir):
                       Path(root) / name
                       for root, _dirs, files in os.walk(input_dir)
                       for name in files if Path(name).suffix.lower()
                       in ('.jpg', '.jpeg', '.png')}
            if set(records) != set(sources) or set(records) != set(images):
                return StageStatus('preprocess', 'partial',
                                   'preprocessing image membership changed - retry required')
            for key, record in records.items():
                if not isinstance(record, dict):
                    return StageStatus('preprocess', 'partial',
                                       'preprocessing provenance is invalid - retry required')
                for kind, path in (('source', sources[key]), ('output', images[key])):
                    signature = record.get(kind)
                    stat = path.stat()
                    if not (isinstance(signature, dict) and signature.get('sha256')
                            and signature.get('size') == stat.st_size
                            and signature.get('mtime_ns') == stat.st_mtime_ns):
                        return StageStatus('preprocess', 'partial',
                                           f'preprocessing {kind} changed - retry required')
            return StageStatus('preprocess', 'done',
                               f'{n:,} verified CLAHE images in preprocessed_images/')
        except (OSError, RuntimeError) as exc:
            return StageStatus('preprocess', 'blocked',
                               f'cannot verify preprocessing: {exc}')

    def _detect_batch(self) -> StageStatus:
        if not self.batched.is_dir():
            return StageStatus("batch", "pending", "no batched_images_by_zone/")
        zones = sorted(p for p in self.batched.iterdir() if p.is_dir())
        marker = self.batched / "batch_inputs.json"
        details = []
        empty = []
        total = 0
        for z in zones:
            n = _count_images(z)
            total += n
            if n == 0:
                empty.append(z.name)
            has_log = bool(_find_flight_logs(z))
            details.append(f"{z.name}: {n:,} images"
                           + ("" if has_log else "  [no flight log]"))
        # Zone folders that hold NO images are the flight-log/disk filename
        # mismatch artifact - the batcher created the folders and copied
        # nothing. Counting folders alone reported that 'done' and handed
        # empty trees to alignment.
        if zones and total == 0:
            return StageStatus("batch", "blocked",
                               f"{len(zones)} zone folders but ZERO images "
                               "copied - the flight log's filenames do not "
                               "match any file in the input tree", details)
        if zones and empty:
            return StageStatus("batch", "partial",
                               f"{len(zones)} zones, but {len(empty)} hold no "
                               f"images: {', '.join(empty[:5])}", details)
        if zones and marker.is_file():
            return StageStatus("batch", "done",
                               f"{len(zones)} zones, fingerprint present",
                               details)
        if zones:
            return StageStatus("batch", "partial",
                               f"{len(zones)} zones but NO batch_inputs.json "
                               "fingerprint - provenance unknown", details)
        return StageStatus("batch", "pending", "zone folders absent")

    def _detect_align(self) -> StageStatus:
        if not self.aligned.is_dir():
            return StageStatus("align", "pending", "no aligned_components/")
        zones = sorted(p for p in self.aligned.iterdir() if p.is_dir())
        details, comp_total, cam_total = [], 0, 0
        for z in zones:
            manifests = sorted(z.glob("*.rsalign.manifest.json"))
            cams = 0
            for m in manifests:
                cams += _load_json(m).get("camera_count") or 0
            comp_total += len(manifests)
            cam_total += cams
            details.append(f"{z.name}: {len(manifests)} component(s), "
                           f"{cams:,} cameras")
        batched_zones = ([p.name for p in self.batched.iterdir() if p.is_dir()]
                         if self.batched.is_dir() else [])
        aligned_names = {z.name for z in zones if list(z.glob('*.rsalign'))}
        missing = [z for z in batched_zones if z not in aligned_names]
        if comp_total and not missing:
            return StageStatus("align", "done",
                               f"{comp_total} components / {cam_total:,} "
                               f"cameras across {len(zones)} zones", details)
        if comp_total:
            return StageStatus("align", "partial",
                               f"{comp_total} components; zones not aligned: "
                               f"{', '.join(missing)}", details)
        return StageStatus("align", "pending", "no components exported")

    def _detect_merge(self) -> StageStatus:
        merge = self.latest_merge()
        if not merge:
            return StageStatus("merge", "pending", "no merge_report.json")
        report = _load_json(merge / "merge_report.json")
        finals = [c for rec in _records(report, "clusters")
                  for c in _records(rec, "final_components")]
        gate = merge / "EVALUATION_READY.txt"
        cams = sum(c.get("camera_count") or 0 for c in finals)
        # The gate file alone is not proof: merge_zones used to write it
        # BEFORE checking the assembly workflow's result, so a failed
        # assembly left a document declaring a terminal state for a project
        # that was never saved. Require the recorded
        # workflow_success too - absent (older reports) still counts.
        assembly = report.get("assembly")
        assembly_ok = (not isinstance(assembly, dict)
                       or assembly.get("workflow_success") is not False)
        if finals and gate.is_file() and not assembly_ok:
            return StageStatus("merge", "blocked",
                               f"{merge.name}: EVALUATION_READY present but "
                               "the assembly workflow FAILED - the assembly "
                               "project was never saved")
        if finals and gate.is_file():
            return StageStatus("merge", "done",
                               f"{merge.name}: {len(finals)} final "
                               f"component(s), {cams:,} cameras",
                               [c.get("key", "?") for c in finals])
        if finals:
            return StageStatus("merge", "partial",
                               f"{merge.name}: report exists but no "
                               "EVALUATION_READY gate")
        return StageStatus("merge", "partial", f"{merge.name}: no finals yet")

    def _detect_model(self) -> StageStatus:
        recorded: set[str] = set()
        for name in MODEL_REPORT_NAMES:
            report = _load_json(self.root / name)
            for m in _records(report, "models", "components"):
                if m.get('success') is True and isinstance(m.get('component'), str):
                    recorded.add(m['component'])
        report, finals, verified = self._model_completion()
        dated = report.get('dated_copy')
        if (finals and set(verified) == set(finals) and isinstance(dated, dict)
                and dated.get('success') is True):
            return StageStatus('model', 'done',
                               f'{len(verified)} of {len(finals)} components modelled',
                               sorted(key.split('/')[-1] for key in verified))
        if recorded or _records(report, 'models'):
            return StageStatus('model', 'partial',
                               f'{len(verified)} of {len(finals)} components verified '
                               'for the current assembly - retry required', sorted(recorded))
        return StageStatus('model', 'pending', 'no model reports')

    def _model_completion(self) -> tuple[dict, dict, dict]:
        """Current model records shared by the stage census and result rows."""
        merge = self.latest_merge()
        project = self.assembly_project()
        finals = {}
        verified = {}
        report = _load_json(self.root / 'models_report.json')
        if merge and project:
            rep = _load_json(merge / 'merge_report.json')
            finals = {c['key']: c for rec in _records(rep, 'clusters')
                      for c in _records(rec, 'final_components')
                      if isinstance(c.get('key'), str) and c['key']}
            logs = sorted(merge.glob('flight_log*_UTM.txt')) + \
                sorted((merge / 'assembly').glob('flight_log*_UTM.txt'))
            if logs:
                from .realityscan_interface.realityscan_cli import METADATA_DIR, SCRIPTS_DIR
                try:
                    inputs = model_input_fingerprint(
                        merge / 'merge_report.json', str(logs[0]),
                        os.path.join(SCRIPTS_DIR, 'GenerateModel.bat'), METADATA_DIR)
                    if (report.get('inputs') == inputs
                            and report.get('project_state') == project_state(project)):
                        for model in _records(report, 'models'):
                            key = model.get('component_key')
                            if (not isinstance(key, str) or key not in finals
                                    or model.get('mode') != 'workspace'
                                    or model.get('success') is not True
                                    or model.get('status') != 'pass'):
                                continue
                            saved = model.get('component_input')
                            current = component_input_fingerprint(
                                finals[key].get('rsalign', ''), hash_content=False)
                            if (isinstance(saved, dict) and saved.get('sha256')
                                    and current['bytes'] is not None
                                    and all(saved.get(k) == value
                                            for k, value in current.items())):
                                verified[key] = model
                except (OSError, TypeError, ValueError):
                    pass
        return report, finals, verified

    def _detect_export(self) -> StageStatus:
        if not self.exports.is_dir():
            return StageStatus("export", "pending", "no exports/")
        comps = sorted(p for p in self.exports.iterdir() if p.is_dir())
        details = []
        exported: set[str] = set()
        for c in comps:
            kinds = [k.name for k in c.iterdir()
                     if k.is_dir() and any(k.iterdir())]
            if kinds:
                exported.add(c.name)
                details.append(f"{c.name}: {', '.join(sorted(kinds))}")
        # The denominator is what the MERGE declared final, not what
        # exports/ happens to contain: measuring exports against itself
        # reported "1 of 1" for a 1-of-6 export.
        expected = set()
        merge = self.latest_merge()
        if merge:
            rep = _load_json(merge / "merge_report.json")
            expected = {c.get("key", "").split("/")[-1]
                        for rec in _records(rep, "clusters")
                        for c in _records(rec, "final_components")}
            expected.discard("")
        if details:
            missing = sorted(expected - exported)
            if missing:
                return StageStatus(
                    "export", "partial",
                    f"{len(exported)} of {len(expected)} component(s) "
                    f"exported; missing: {', '.join(missing)}", details)
            if len(details) < max(len(comps), 1):
                return StageStatus("export", "partial",
                                   f"{len(details)} of {len(comps)} export "
                                   "folder(s) hold deliverables", details)
            return StageStatus("export", "done",
                               f"{len(details)} component(s) exported", details)
        return StageStatus("export", "pending", "exports/ is empty")

    def _detect_publish(self) -> StageStatus:
        from .publish_fingerprint import publication_source_matches

        report = _load_json(self.root / "publish_report.json")
        assets = _records(report, "assets")
        if assets:
            by_component = {a['component']: a for a in assets
                            if isinstance(a.get('component'), str)
                            and a['component']}
            requested = report.get('requested_components')
            expected = {c for c in requested if isinstance(c, str) and c} \
                if isinstance(requested, list) else set(by_component)
            if self.exports.is_dir():
                expected.update(
                    c.name for c in self.exports.iterdir()
                    if c.is_dir() and (c / 'obj').is_dir()
                    and any(p.is_file() and p.suffix.lower() == '.obj'
                            and p.stat().st_size > 0
                            for p in (c / 'obj').iterdir()))
            configured = report.get('destinations')
            destinations = {d for d in configured if d in ('cesium', 'nira')} \
                if isinstance(configured, list) else {
                    d for a in assets for d in ('cesium', 'nira') if d in a}
            completed = set()
            details = []
            for component in sorted(expected):
                asset = by_component.get(component, {})
                outstanding = []
                for destination in sorted(destinations):
                    result = asset.get(destination)
                    if not (isinstance(result, dict)
                            and result.get('success') is True
                            and not result.get('dry_run')):
                        outstanding.append(destination)
                source = asset.get('source')
                source_current = (publication_source_matches(
                    self.exports / component / 'obj', source)
                    and isinstance(source, dict)
                    and set(source.get('destinations', [])) == destinations)
                if not source_current:
                    outstanding.append('current source export')
                if destinations and not outstanding and not report.get('dry_run'):
                    completed.add(component)
                else:
                    details.append(f'{component}: outstanding '
                                   + (', '.join(outstanding) or 'publication'))
            if len(by_component) != len(assets):
                details.append('publication report has missing or duplicate '
                               'component identities')
            complete = bool(expected) and completed == expected \
                and len(by_component) == len(assets)
            return StageStatus("publish",
                               "done" if complete else "partial",
                               f"{len(completed)} of {len(expected)} "
                               "component(s) published to all requested "
                               "destinations", details)
        if self.exports.is_dir() and any(self.exports.iterdir()):
            return StageStatus("publish", "pending",
                               "exports ready - needs CESIUM_ION_TOKEN "
                               "and/or NIRACLIENT_DIR")
        return StageStatus("publish", "pending", "nothing exported yet")

    # ------------------------------------------------------------ inventory
    def components(self) -> list[ComponentInfo]:
        """Merged final components joined with scale verdicts, model results
        and export presence - the results-browser table."""
        merge = self.latest_merge()
        if not merge:
            return []
        report = _load_json(merge / "merge_report.json")
        scales = report.get("input_scales")
        scales = scales if isinstance(scales, dict) else {}
        out: dict[str, ComponentInfo] = {}
        for rec in _records(report, "clusters"):
            for c in _records(rec, "final_components"):
                key = c.get("key", "?")
                name = key.split("/")[-1]
                v = scales.get(key)
                v = v if isinstance(v, dict) else {}
                out[name] = ComponentInfo(
                    key=name, cameras=c.get("camera_count"),
                    scale=v.get("median"), scale_status=v.get("status", ""))
        _, _, verified = self._model_completion()
        for key, model in verified.items():
            name = key.split('/')[-1]
            if name in out:
                out[name].modelled = True
                out[name].model_minutes = model.get("duration_min")
                if model.get("scale") is not None:
                    out[name].scale = model.get("scale")
                    out[name].scale_status = model.get("status", "pass")
        if self.exports.is_dir():
            for name, info in out.items():
                comp_dir = self.exports / name
                if comp_dir.is_dir():
                    info.exported = sorted(
                        k.name for k in comp_dir.iterdir()
                        if k.is_dir() and any(k.iterdir()))
        return sorted(out.values(), key=lambda c: -(c.cameras or 0))
