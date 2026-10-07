from __future__ import annotations

import math
import os
import shutil
import time
from collections.abc import Mapping

from module_base.parameter import Parameter
from module_base.rs_module import RSModule

from .. import (
    align_fingerprint,
    calibration_sidecars,
    camera_registry,
    component_manifest,
)
from ..flight_logs import find_flight_log, require_utm_zone, write_flight_log_params
from ..image_exts import PROCESSABLE_IMAGE_EXTS
from ..wildsync_intake.intake import (
    MANIFEST_NAME,
    RAW_IMAGES,
    calibration_modes,
    load_manifest,
    starting_focals,
)
from .realityscan_cli import METADATA_DIR, RealityScanCLI, set_project_save_env

# Component/scene files as exported by RealityScan (legacy RealityCapture
# extensions still accepted so older outputs keep working).
COMPONENT_EXTENSIONS = ('.rsalign', '.rcalign')


def flight_log_params_template(metadata_dir: str,
                               explicit: str | None = None) -> str:
    """FlightLogParams template for an alignment: the explicit choice when
    given, else the repository's UTM template. Either way its
    coordinate-system entries are rewritten for the flight log's own UTM
    zone before import (write_flight_log_params)."""
    return explicit or os.path.join(metadata_dir, "FlightLogParams.xml")


SCENE_EXTENSIONS = ('.rsproj', '.rcproj')

# Folder in a zone's output where sidecars this pipeline did not write are
# moved before calibration sidecars are written beside the images.
PRE_EXISTING_SIDECARS = 'pre_existing_sidecars'


def small_scene_min_component_size(configured: int, image_count: int) -> int:
    """Export threshold (cameras) for a scene of ``image_count`` images.

    A scene at least as large as ``configured`` keeps it. A smaller scene
    exports components holding at least half of its images, never fewer
    than 2 cameras: ``min(configured, max(2, ceil(n / 2)))``. Equal to the
    image count, one camera that failed to register meant no export and a
    failed zone.
    """
    if image_count <= 0 or image_count >= configured:
        return configured
    return min(configured, max(2, math.ceil(image_count / 2)))


class RealityScanAlignment(RSModule):
    def __init__(self, logger):
        super().__init__("RealityScan Alignment", logger)
        self.cli = RealityScanCLI(logger)

    def get_parameters(self) -> dict[str, Parameter]:
        additional_params = {}

        additional_params['rs_input_image_dir'] = Parameter(
            name='Input Image Folder',
            cli_short='r_i',
            cli_long='r_input',
            type=str,
            default_value=None,
            description='Directory containing the images to align; a batched '
                        'ROOT (holding zone_* subfolders) is EXPANDED into '
                        'one alignment scene per zone',
            prompt_user=True,
            disable_when_module_active='Batch Directory'
        )

        additional_params['rs_project_label'] = Parameter(
            name='Project Label',
            cli_short='r_p',
            cli_long='r_project_label',
            type=str,
            default_value='',
            description='dataset label for the RC_projects daily save schema (e.g. site_run1); empty disables daily saves',
            prompt_user=True
        )

        additional_params['rs_display_output'] = Parameter(
            name='Display Output',
            cli_short='r_d',
            cli_long='r_display_output',
            type=bool,
            default_value=False,
            description='Whether to display the RealityScan output',
            prompt_user=True
        )

        additional_params['rs_flight_log_path'] = Parameter(
            name='Flight Log Path',
            cli_short='r_f',
            cli_long='r_flight_log',
            type=str,
            default_value='',
            description=('Optional flight log file. Leave blank to discover '
                         'the input folder or each zone\'s own log; without '
                         'a matching log, alignment runs without navigation '
                         'priors - except in a Wild Sync intake workspace, '
                         'where a zone without its log fails.'),
            prompt_user=True,
            disable_when_module_active=['Batch Directory', 'Wild Sync Intake']
        )

        additional_params['rs_min_component_size'] = Parameter(
            name='Min Component Size (cameras)',
            cli_short='r_mcs',
            cli_long='r_min_component_size',
            type=int,
            default_value=50,
            description=('Smallest component (in cameras) exported after a '
                         'zone align. A scene with fewer images than this '
                         'exports components holding at least half of its '
                         'images instead (never fewer than 2 cameras), so a '
                         'small dataset still exports when a few cameras fail '
                         'to register. CONSEQUENCE: '
                         'components under this threshold are silently '
                         'discarded before the merge or model stage ever '
                         'sees them - for thin features that fragment into '
                         'small pockets, lower this (e.g. 10-20) or the '
                         'feature vanishes from the deliverable with no '
                         'error.'),
            prompt_user=False
        )

        additional_params['rs_flight_log_params'] = Parameter(
            name='Flight Log Params XML',
            cli_short='r_flp',
            cli_long='r_flight_log_params',
            type=str,
            default_value=None,
            description=('Explicit FlightLogParams XML template. Default: '
                         'Metadata/FlightLogParams.xml. Its coordinate-system '
                         'entries are rewritten for the UTM zone in the '
                         "flight log's filename tag (e.g. "
                         'flight_log_19T_UTM.txt) before every import; a log '
                         'without that tag is refused.'),
            prompt_user=False
        )

        return {**super().get_parameters(), **additional_params}

    @staticmethod
    def __superseded_path(output_folder: str) -> str:
        """Where a previous run's zone folder is moved to.

        OUTSIDE aligned_components/, deliberately. Keeping it as a sibling
        (``zone_1.superseded_<stamp>``) preserved the data but left its
        ``*.rsalign.manifest.json`` files inside the tree that
        component_analysis.load_manifests walks RECURSIVELY and that the
        workspace census iterates as zone folders - measured on a fixture:
        the census read "2 components / 2,400 cameras across 2 zones" for
        one re-aligned zone, and the merge saw two manifests for one
        .rsalign (the export names repeat, so the stale manifest's path
        resolves to the NEW file). rmtree could not do that; the
        rename-aside introduced it.
        """
        normalized = os.path.normpath(output_folder)
        components_root = os.path.dirname(normalized)
        workspace = os.path.dirname(components_root) or components_root
        return os.path.join(
            workspace, 'superseded',
            f'{os.path.basename(components_root)}_{os.path.basename(normalized)}'
            f'_{time.strftime("%Y%m%d-%H%M%S")}')

    def __check_and_create_folder(self, path):
        """
        Checks if a folder exists, if not, creates it.
        """
        if not os.path.isdir(path):
            os.makedirs(path)
            self.logger.info(f"Created folder: {path}")

    def __get_flight_log_path(self, batch_path=None):
        """
        Returns the path to the flight log file (or None when none exists).

        All on-disk discovery goes through flight_logs.find_flight_log so
        the intake's flight_log_<zone><band>_UTM.txt naming and the
        per-zone copies from Batch Directory are both found.
        """

        # batched images: each zone folder carries its own flight log copy
        if batch_path is not None:
            return find_flight_log(batch_path)

        # if the flight log path is specified, use that
        if 'rs_flight_log_path' in self.params:
            explicit = self.params['rs_flight_log_path'].get_value()
            if explicit:
                return explicit

        # Standalone inputs own their navigation; do not borrow a log from
        # another image tree under the output workspace. Chained after Wild
        # Sync Intake, the input is this workspace's own imagery (often the
        # preprocessed copies, which keep the file names), and the intake's
        # log in raw_images names it.
        output_dir = self.params['output_dir'].get_value()
        if 'rs_input_image_dir' in self.params:
            input_dir = self.params['rs_input_image_dir'].get_value()
            if 'ws_run_dirs' in self.params:
                return find_flight_log(
                    input_dir, os.path.join(output_dir, "raw_images"))
            return find_flight_log(input_dir)

        return find_flight_log(os.path.join(output_dir, "raw_images"), output_dir)

    def __align_zone(self, input_folder, output_folder, scene_name,
                     flight_log_path, flight_log_params_path,
                     display_output=False, min_component_size=50,
                     calibration_modes: Mapping[str, str] | None = None,
                     require_flight_log: bool = False,
                     starting_focals: Mapping[str, float] | None = None):
        """Align one zone as one scene via AlignZone.bat and export ALL its
        components (>= min_component_size cameras) plus a registration
        census. No model generation here: models are built once, on the
        merged component (GenerateModel.bat).

        ``calibration_modes`` ({camera key: prior | groups | off}, from the
        intake manifest) and ``starting_focals`` ({camera key: starting
        35 mm-equivalent focal}, from the same manifest; a ``groups``
        sidecar carries it as an initial focal): when any camera's mode
        writes a sidecar, the decided sidecar is written beside every image
        of that camera, an
        .rscmd that adds every image (with its sidecar where it has one) is
        written to the output folder, and AlignZone.bat adds the images by
        executing it instead of -addFolder. The sidecar hygiene after the
        run then restores the same decided sidecars. Without such a mode the
        zone is aligned exactly as without calibration delivery. Whether
        RealityScan imports these -addImageWithCalibration lines as written
        is not validated here.

        All RealityScan execution goes through RealityScanCLI, which handles
        instance locking, progress monitoring, error detection, and verified
        instance shutdown (see realityscan_cli.py).
        """

        if not input_folder:
            raise ValueError("Input folder is not specified")
        if not output_folder or not os.fspath(output_folder).strip():
            raise ValueError("Output folder is not specified")

        # The workflow runs from SCRIPTS_DIR; filesystem arguments belong to
        # this driver's caller, while scene names remain ordinary tokens.
        input_folder = os.path.abspath(input_folder)
        output_folder = os.path.abspath(output_folder)
        if flight_log_path:
            flight_log_path = os.path.abspath(flight_log_path)
        if flight_log_params_path:
            flight_log_params_path = os.path.abspath(flight_log_params_path)

        if not os.path.isdir(input_folder):
            raise ValueError(f"Input folder {input_folder} is not a directory")

        sidecar_modes = sidecar_focals = None
        if calibration_modes and any(
                mode in calibration_sidecars.SIDECAR_MODES
                for mode in calibration_modes.values()):
            sidecar_modes = dict(calibration_modes)
            # Only a groups sidecar carries the observed starting focal
            # (a prior sidecar carries the calibration's own).
            sidecar_focals = {
                key: (starting_focals or {}).get(key)
                for key, mode in sidecar_modes.items() if mode == 'groups'
            } or None
            missing = sorted(key for key, focal in (sidecar_focals or {}).items()
                             if focal is None)
            if missing:
                raise ValueError(
                    f'No starting focal for camera(s) {", ".join(missing)} '
                    'decided groups: RealityScan would align them without a '
                    'starting focal length. Re-run Wild Sync Intake.')
            if os.environ.get('RS_ALIGN_POOL_DIR'):
                raise ValueError(
                    'Calibration sidecars cannot be delivered with the pool '
                    'layout (RS_ALIGN_POOL_DIR is set): the sidecars would '
                    'have to live beside the shared pool images. Batch with '
                    'the copy layout, or re-run the intake with calibration '
                    'off.')

        # A scene smaller than the export threshold could never export a
        # component, however well it aligned.
        configured_min_component_size = min_component_size
        image_paths = self.__image_paths(input_folder)
        lowered = small_scene_min_component_size(min_component_size,
                                                 len(image_paths))
        if lowered != min_component_size:
            self.logger.info(
                'Scene %s has %d image(s), fewer than the configured minimum '
                'component size (configured %d) - exporting components of '
                '>= %d cameras (at least half the scene, never fewer than 2)',
                scene_name, len(image_paths), min_component_size, lowered)
            min_component_size = lowered

        # Every trajectory is imported in the UTM zone its own filename
        # names; refuse an untagged log before anything on disk is moved.
        if flight_log_path and os.path.isfile(flight_log_path):
            require_utm_zone(flight_log_path)
        elif require_flight_log:
            # The intake writes a log row for every image it takes in, so
            # a zone of an intake workspace without one was not prepared
            # from it; aligning it without priors would be a different run.
            raise ValueError(
                f'No flight log for {input_folder} (looked for: '
                f'{flight_log_path or "none"}) although this workspace has a '
                'Wild Sync intake, which writes one for every image. Re-run '
                'Batch Directory (or the intake) so the zone carries its log; '
                'the zone was not aligned.')

        hygiene_root = os.path.abspath(os.environ.get('RS_ALIGN_POOL_DIR') or input_folder)
        target_root = os.path.normcase(os.path.realpath(output_folder))
        for source in (input_folder, hygiene_root):
            source_root = os.path.normcase(os.path.realpath(source))
            try:
                shared_root = os.path.commonpath((source_root, target_root))
            except ValueError:
                shared_root = None
            if shared_root in (source_root, target_root):
                raise ValueError('Alignment input and output trees must not overlap')

        # A re-run must start from a clean zone folder: stale exports would
        # be indistinguishable from this run's (exportLatestComponents
        # reuses names like "Component 0.rsalign") and would poison the
        # files_before/after diff below.
        #
        # But the previous run's tree holds its SAVED PROJECT and every
        # exported .rsalign - GPU-hours of work - and the only backup, the
        # dated RC_projects copy, is off by default (rs_project_label
        # defaults to ''). Deleting that on a logger.warning was the one
        # unguarded destructive step in the align path,
        # so a tree carrying deliverables is RENAMED aside instead: same
        # clean-slate premise, no data loss, one os.rename and no copy cost.
        # Input fingerprint for THIS run: whatever AlignZone.bat will
        # actually apply (RS_ALIGN_PARAMS override, else the canonical
        # template). Built before the supersede step so a retry can be
        # told apart from a re-run: "same inputs, redoing" vs "inputs
        # CHANGED, previous components were built differently".
        current_fp = align_fingerprint.build_fingerprint(
            flight_log_path or None,
            flight_log_params_path or None,
            os.path.abspath(os.environ.get('RS_ALIGN_PARAMS')
                            or os.path.join(METADATA_DIR, 'AlignmentParams.xml')),
            min_component_size,
            rs_executable=self.cli.find_executable(),
            calibration=sidecar_modes,
            starting_focals=sidecar_focals)

        if os.path.isdir(output_folder) and os.listdir(output_folder):
            prev_fp = align_fingerprint.read_fingerprint(output_folder)
            changes = align_fingerprint.diff_fingerprints(prev_fp, current_fp)
            if changes:
                self.logger.warning(
                    'RETRY WITH CHANGED INPUTS for %s - the previous '
                    'components were built from DIFFERENT inputs:\n  - %s\n'
                    'Results are not comparable run-to-run; never merge '
                    'components built from different navigation or frames.',
                    output_folder, '\n  - '.join(changes))
            elif prev_fp:
                self.logger.info(
                    'Re-run with IDENTICAL inputs (nav, frame, settings '
                    'unchanged) for %s - redoing the zone from scratch.',
                    output_folder)
            # Sidecars moved aside by an earlier run are the user's data:
            # they are kept like a project, never cleared.
            keepers = [f for f in os.listdir(output_folder)
                       if f.endswith(COMPONENT_EXTENSIONS + SCENE_EXTENSIONS)
                       or f == PRE_EXISTING_SIDECARS]
            if keepers:
                superseded = self.__superseded_path(output_folder)
                os.makedirs(os.path.dirname(superseded), exist_ok=True)
                os.rename(output_folder, superseded)
                self.logger.warning(
                    'Previous run left %d project/component file(s) or '
                    'moved-aside sidecar folder(s) in %s - moved the whole '
                    'folder to %s instead of deleting it. Remove it yourself '
                    'once you no longer need it.',
                    len(keepers), output_folder, superseded)
            else:
                self.logger.warning('Clearing previous exports in %s (no '
                                    'project or component files in it)',
                                    output_folder)
                shutil.rmtree(output_folder)
        self.__check_and_create_folder(output_folder)

        # Calibration delivery writes <stem>.xmp beside every image of a
        # sidecar-mode camera. A sidecar already there that this pipeline
        # did not write (a user's pose prior, an edited calibration) is
        # MOVED under the zone output first, never overwritten.
        if sidecar_modes:
            self.__move_pre_existing_sidecars(image_paths, input_folder,
                                              output_folder, sidecar_modes)

        # The pipeline WRITES INTO the folder it is given. Any pose-bearing
        # .xmp still beside an image is imported by RealityScan as a pose
        # prior; RealityScan's pose export then overwrites it, the identity
        # harvest moves those exports into <output>/identity_r*, and
        # sanitize_and_census rewrites or deletes whatever pose sidecars
        # remain. The original content is not kept. Say so once, loudly,
        # before the run.
        # Pool layout (RS_ALIGN_POOL_DIR): the zone folder holds only an
        # .imagelist + flight log; exportXMP drops sidecars beside the
        # POOL images, so every sidecar sweep (pre-align warning, the
        # .bat harvest, sanitize, regeneration) targets the pool root
        # instead of the zone folder. Unset = legacy behavior.
        pose_sidecars = 0
        unreadable = []
        for root, _dirs, files in os.walk(hygiene_root):
            for name in files:
                if not name.lower().endswith('.xmp'):
                    continue
                path = os.path.join(root, name)
                try:
                    with open(path, encoding='utf-8', errors='replace') as fh:
                        if 'xcr:Position' in fh.read():
                            pose_sidecars += 1
                except OSError as exc:
                    unreadable.append(f'{path} ({exc})')
        if unreadable:
            # Not provably free of a pose, and RealityScan may import it:
            # an unreadable sidecar is treated as a foreign one that could
            # not be moved aside.
            self.logger.warning('%d sidecar(s) in %s could not be read: %s',
                                len(unreadable), hygiene_root,
                                '; '.join(unreadable))
            raise ValueError(
                f'{len(unreadable)} unreadable .xmp sidecar(s) in '
                f'{hygiene_root} (e.g. {unreadable[0]}) - RealityScan could '
                'import them as priors with their images. Make them readable '
                'or remove them, then re-run; the zone was not aligned.')
        if pose_sidecars:
            self.logger.warning(
                'HEADS UP: %s contains %d pose-bearing .xmp sidecar(s) beside '
                'images that get no calibration sidecar. RealityScan imports '
                'them as pose priors for this alignment; its pose export then '
                'overwrites them, the identity harvest moves the exports into '
                '%s, and the hygiene after the run rewrites or deletes any '
                'that remain. Their content is not preserved: copy the folder '
                'first if those sidecars are yours.',
                hygiene_root, pose_sidecars,
                os.path.join(output_folder, 'identity_r0'))

        if flight_log_path is None or not os.path.isfile(flight_log_path):
            # Without an intake (refused above with one), never fail the
            # run, but never be silent about it either: aligning without a
            # trajectory is a materially different run.
            self.logger.warning(
                'No flight log found for %s (looked for: %s) - aligning WITHOUT '
                'georeferencing priors', input_folder, flight_log_path or 'none')
            flight_log_path = ""

        if flight_log_params_path is None or not os.path.isfile(flight_log_params_path) or flight_log_path == "":
            flight_log_params_path = ""

        log_dir = os.path.join(os.path.dirname(os.path.dirname(output_folder)), "logs")

        # The params XML declares the trajectory's coordinate system. The
        # template's zone is only a placeholder, so regenerate it from the
        # zone tag in the flight log's own filename
        # (flight_log_53N_UTM.txt -> EPSG:32653).
        if flight_log_path and flight_log_params_path:
            zone, band = require_utm_zone(flight_log_path)
            generated = os.path.join(log_dir, f'FlightLogParams_{zone}{band}.xml')
            flight_log_params_path = write_flight_log_params(
                flight_log_params_path, generated, zone, band)
            self.logger.info(f'Flight log CRS: UTM zone {zone}{band} '
                             f'(params: {generated})')

        # A camera switched to off must not inherit the calibration sidecars
        # an earlier prior/groups run left beside its images.
        removed, foreign = calibration_sidecars.remove_calibration_sidecars(
            hygiene_root, calibration_modes)
        if removed:
            self.logger.info(
                'Removed %d calibration sidecar(s) left by an earlier run for '
                'camera(s) now decided off in %s', removed, hygiene_root)
        if foreign:
            self.logger.warning(
                '%d sidecar(s) beside images of camera(s) with calibration '
                'off in %s were not written by this pipeline and are left in '
                'place; RealityScan will still read them', foreign,
                hygiene_root)

        args = [input_folder, output_folder, flight_log_path,
                flight_log_params_path, scene_name, str(min_component_size)]
        if sidecar_modes:
            args.append(self.__write_calibration_inputs(
                image_paths, output_folder, scene_name, sidecar_modes,
                sidecar_focals))

        files_before = set(os.listdir(output_folder))

        result = self.cli.run_batch_script(
            'AlignZone.bat', args, log_dir, display_output)

        # Sidecar hygiene even on failure: the in-session identity loop
        # normally harvests every pose sidecar into identity_c<K> folders,
        # but a partial run may leave pose sidecars beside the images,
        # which would poison the next attempt as auto-imported priors.
        # The registration census now comes from the manifests
        # (harvested sidecars), not from this sweep.
        # With calibration delivery both hygiene calls get the decided
        # modes, so they rewrite or restore exactly the sidecars written
        # before the run and never delete one of them.
        leftover, _restored, _removed = calibration_sidecars.sanitize_and_census(
            hygiene_root, sidecar_modes, sidecar_focals)
        if leftover:
            self.logger.warning(
                '%d pose sidecars were left beside the images (partial '
                'identity harvest?) - restored/removed for hygiene',
                leftover)

        # RUNS ON EVERY EXIT PATH. The identity harvest MOVES pose sidecars
        # out of the image tree and never re-exports the last-peeled
        # component's, leaving those images with NO calibration prior at
        # all; sanitize_and_census above only touches sidecars that are
        # STILL PRESENT, so it cannot repair that. This call used to sit
        # after the failure and no-component returns below, i.e. it was
        # skipped precisely when the operator re-runs, leaving images
        # ungrouped. Idempotent: (0, 0) on a complete tree.
        created, _no_camera = calibration_sidecars.ensure_calibration_sidecars(
            hygiene_root, sidecar_modes, sidecar_focals)
        if created:
            self.logger.info(
                'Restored %d calibration sidecar(s) displaced by the identity '
                'harvest in %s', created, hygiene_root)

        if not result.success:
            self.logger.error(f"RealityScan workflow failed for {input_folder}: "
                              f"{result.errors or f'exit code {result.return_code}'} (log: {result.log_path})")
            return {'Success': False, 'Component Count': 0,
                    'Registered Cameras': 0}, {'Success': False}

        component_files = [f for f in os.listdir(output_folder)
                           if f not in files_before and f.endswith(COMPONENT_EXTENSIONS)]

        # Verify the saved scene exists and is non-empty instead of
        # trusting the workflow's exit status alone.
        scene_path = os.path.join(output_folder, f"{scene_name}.rsproj")
        scene_success = os.path.isfile(scene_path) and os.path.getsize(scene_path) > 0
        if not scene_success:
            self.logger.error(f'Project file "{scene_path}" was not created')

        if not component_files:
            self.logger.error(
                'RealityScan finished, but no component of >= %d cameras '
                '(threshold used for this scene; configured %d) was '
                'exported for %s (%d image(s) in the scene): no group of that '
                'many cameras aligned together. Check the alignment log %s; '
                'to keep smaller components, lower Min Component Size '
                '(--r_min_component_size).',
                min_component_size, configured_min_component_size,
                input_folder, len(image_paths), result.log_path)
            return {'Success': False, 'Component Count': 0,
                    'Registered Cameras': 0}, {'Success': scene_success}

        # Per-component identity: manifests built from the identity_c<K>
        # harvest folders written by AlignZone.bat's in-session loop (the
        # only place stem-named sidecars exist).
        # The registration census = sum of manifest camera counts. A
        # manifest failure does not fail the zone: alignment succeeded and
        # manifests are bookkeeping, but errors are logged loudly because
        # the merge stage depends on them.
        manifest_paths = []
        if scene_success:
            manifest_paths = self.capture_component_identities(
                input_folder, output_folder, scene_name, flight_log_path)
        # Fingerprint travels with the exports: nav-aware resume
        # (align_fingerprint.matches_current) and merge-stage unanimity
        # checks key off this file, not off mere file existence.
        try:
            align_fingerprint.write_fingerprint(output_folder, current_fp)
        except OSError as exc:
            self.logger.warning('Could not write %s: %s',
                                align_fingerprint.FINGERPRINT_NAME, exc)
        # (The calibration-sidecar repair ran above, on every exit path;
        # this call covers the input folder when it differs from the
        # hygiene root.)
        calibration_sidecars.ensure_calibration_sidecars(input_folder,
                                                         sidecar_modes,
                                                         sidecar_focals)

        registered = 0
        for mp in manifest_paths:
            try:
                registered += component_manifest.load_manifest(mp).get('camera_count', 0)
            except Exception as exc:
                self.logger.warning('Could not read manifest %s: %s', mp, exc)

        self.logger.info(
            'Zone %s: %d component(s) exported, %d cameras registered '
            '(census from %d manifest(s))',
            scene_name, len(component_files), registered, len(manifest_paths))

        component_data = {
            'Success': True,
            'Component Count': len(component_files),
            'Component Files': [os.path.join(output_folder, f) for f in component_files],
            'Registered Cameras': registered,
            'Manifests': manifest_paths,
        }
        scene_data = {'Success': scene_success, 'Scene Path': scene_path}
        return component_data, scene_data

    # Bounded per-component identity loop: zones fragment into 2-5
    # components; 20 is a generous ceiling against a pathological scene.
    MAX_IDENTITY_COMPONENTS = 20

    def capture_component_identities(self, input_folder, output_folder,
                                     scene_name, flight_log_path):
        """Build manifests from the identity_r<K> harvest folders written
        by AlignZone.bat's in-session identity loop.

        Public because drivers that invoke AlignZone.bat directly (the
        tests/ PD cells) must reuse THIS implementation - a component
        without a manifest is refused by the feature-aware merge.

        Naming rule:
        -exportXMP writes STEM-named sidecars, while
        -exportXMPForSelectedComponent is always ORDINAL - so
        per-component membership comes from SUCCESSIVE DIFFERENCE: lap K
        harvests the stems of ALL components still in the scene
        (identity_r<K>), then the maximal component <scene>_c<K> is
        exported and deleted. members(c<K>) = stems(r<K>) - stems(r<K+1>).
        The harvest also displaced the calibration sidecars beside the
        images (pose exports overwrite <stem>.xmp and the harvest MOVES
        them); calibration_sidecars.ensure_calibration_sidecars puts the
        decided ones back.
        """
        # stem -> (image basename, full path), one walk of the zone tree
        stem_to_image = {}
        for root, _dirs, files in os.walk(input_folder):
            for f in files:
                if f.lower().endswith(('.jpg', '.jpeg', '.png', '.heif')):
                    stem_to_image.setdefault(
                        os.path.splitext(f)[0].lower(),
                        (f, os.path.join(root, f)))

        def harvest_stems(index):
            d = os.path.join(output_folder, f'identity_r{index}')
            if not os.path.isdir(d):
                return None
            return {os.path.splitext(f)[0].lower()
                    for f in os.listdir(d) if f.lower().endswith('.xmp')}

        manifest_paths = []
        for index in range(self.MAX_IDENTITY_COMPONENTS):
            stems_now = harvest_stems(index)
            if not stems_now:  # missing dir or empty harvest = exhaustion
                break
            component_name = f'{scene_name}_c{index}'
            rsalign_path = os.path.join(output_folder, component_name + '.rsalign')
            if not os.path.isfile(rsalign_path):
                self.logger.warning(
                    'Harvest r%d exists but %s was not exported - the '
                    'identity loop ended mid-lap; skipping this manifest',
                    index, os.path.basename(rsalign_path))
                break

            stems_next = harvest_stems(index + 1) or set()
            member_stems = stems_now - stems_next
            if not member_stems:
                self.logger.error(
                    'Successive difference for %s is empty (r%d == r%d) - '
                    'component deletion may not have taken effect; stopping',
                    component_name, index, index + 1)
                break

            members = []
            for stem in sorted(member_stems):
                entry = stem_to_image.get(stem)
                if entry is None:
                    members.append(stem)
                    continue
                members.append(entry[0])

            bbox = component_manifest.bbox_from_flight_log(
                flight_log_path or None, members)
            if flight_log_path and bbox is None:
                self.logger.warning(
                    'No flight-log rows matched the %d member(s) of %s - '
                    'manifest bbox_utm will be null', len(members), component_name)

            manifest = component_manifest.build_manifest(
                zone=scene_name, component=component_name,
                rsalign_path=rsalign_path, images=members, bbox_utm=bbox)
            manifest_paths.append(component_manifest.write_manifest(manifest))
            self.logger.info(
                'Manifest %s: %d camera(s), bbox %s',
                os.path.basename(manifest_paths[-1]), len(members),
                'null' if bbox is None else
                '[%.1f, %.1f, %.1f, %.1f]' % tuple(bbox))

        if not manifest_paths:
            self.logger.error(
                'No usable identity harvests under %s - manifests '
                'unavailable for zone %s', output_folder, scene_name)
        return manifest_paths

    @staticmethod
    def zone_subfolders(root):
        """The zone directories of a BATCHED ROOT, or [] when ``root`` is
        itself one scene's image folder.

        A folder counts as a zone container when at least one immediate
        child directory is named ``zone_*`` or carries its own
        ``flight_log*_UTM.txt``. Without this, an align-only resume - the
        exact state default_enabled() produces once batching is done -
        passed the batched root as rs_input_image_dir and -addFolder's
        recursion fused EVERY zone into ONE alignment scene, silently
        nullifying the zoning with identical logs and exit status.
        """
        if not os.path.isdir(root):
            return []
        zones = []
        for name in sorted(os.listdir(root)):
            path = os.path.join(root, name)
            if not os.path.isdir(path):
                continue
            try:
                has_log = bool(find_flight_log(path))
            except ValueError:
                # Mixed-zone logs in that directory: it is emphatically a
                # zone folder. The refusal belongs to the align step,
                # which reports it per zone, not to this cheap probe.
                has_log = True
            if name.lower().startswith('zone_') or has_log:
                zones.append(path)
        return zones

    @staticmethod
    def __collect_images(image_folder):
        """All image files under the folder, recursively - a batch zone
        keeps its images in per-camera subfolders."""
        images = []
        for root, _dirs, files in os.walk(image_folder):
            images.extend(f for f in files if f.lower().endswith((".png", ".heif", ".jpg", ".jpeg")))
        return images

    @staticmethod
    def __image_paths(image_folder: str) -> list[str]:
        """Absolute paths of the image files under the folder, recursively,
        sorted."""
        return sorted(
            os.path.join(root, name)
            for root, _dirs, files in os.walk(os.path.abspath(image_folder))
            for name in files
            if os.path.splitext(name)[1].lower() in PROCESSABLE_IMAGE_EXTS)

    def __move_pre_existing_sidecars(self, image_paths: list[str],
                                     input_folder: str, output_folder: str,
                                     modes: Mapping[str, str]) -> None:
        """Move sidecars this pipeline did not write out of the way of the
        calibration sidecars, into <output_folder>/pre_existing_sidecars."""
        destination = os.path.join(output_folder, PRE_EXISTING_SIDECARS)
        moved = calibration_sidecars.move_foreign_sidecars(
            image_paths, modes, input_folder, destination)
        if moved:
            self.logger.warning(
                '%d pre-existing sidecar(s) beside the images in %s were not '
                'written by this pipeline (pose priors, edited or foreign '
                'files). They were MOVED, unchanged and with their relative '
                'paths, to %s before the calibration sidecars were written; '
                'nothing was overwritten or deleted.',
                len(moved), input_folder, destination)

    def __write_calibration_inputs(self, image_paths: list[str],
                                   output_folder: str, scene_name: str,
                                   modes: Mapping[str, str],
                                   focals: Mapping[str, float] | None) -> str:
        """Write the decided sidecar beside each image and the .rscmd that
        adds every image of the scene; returns the .rscmd path."""
        entries = calibration_sidecars.write_sidecars(image_paths, modes, focals)
        rscmd = calibration_sidecars.write_rscmd(
            os.path.join(output_folder, f'{scene_name}.rscmd'), entries)
        with_sidecar = sum(1 for _image, xmp in entries if xmp is not None)
        self.logger.info(
            'Calibration delivery for %s (%s): %d of %d image(s) added with a '
            'calibration sidecar, command file %s', scene_name,
            ', '.join(f'{key}={mode}'
                      + (f' (initial focal {focals[key]:g} mm 35mm-eq.)'
                         if focals and key in focals else '')
                      for key, mode in sorted(modes.items())),
            with_sidecar, len(entries), rscmd)
        if with_sidecar < len(entries):
            self.logger.warning(
                '%d image(s) of %s are added without a calibration sidecar '
                '(no known camera, or calibration off for their camera)',
                len(entries) - with_sidecar, scene_name)
        return rscmd

    def intake_calibration(self) -> tuple[dict[str, str], dict[str, float]] | None:
        """({camera key: decided calibration mode}, {camera key: starting
        35 mm-equivalent focal}) from this workspace's Wild Sync intake
        manifest, ``<output_dir>/raw_images/wildsync_intake.json``.

        Both are read, never recomputed: the intake decided them on the
        original images the operator handed in. Returns None when there is
        no manifest and the intake is not part of this run. Raises
        ValueError when the manifest is unreadable, incomplete or of an
        older schema, lacks a camera's starting focal, or is missing
        although the intake is part of this run.
        """
        raw_dir = os.path.join(self.params['output_dir'].get_value(), RAW_IMAGES)
        manifest = load_manifest(raw_dir)
        if manifest is None:
            if 'ws_run_dirs' in self.params:
                raise ValueError(
                    f'Wild Sync Intake is part of this run but '
                    f'{os.path.join(raw_dir, MANIFEST_NAME)} does not exist')
            return None
        return calibration_modes(manifest), starting_focals(manifest)

    def intake_calibration_modes(self) -> dict[str, str] | None:
        """{camera key: decided calibration mode} from the intake manifest
        (:meth:`intake_calibration`), or None without one."""
        calibration = self.intake_calibration()
        return None if calibration is None else calibration[0]

    def run(self):
        # Parameters are validated by the orchestrator before run()
        output_dir = os.path.join(self.params['output_dir'].get_value(), "aligned_components")
        display_output = self.params['rs_display_output'].get_value()

        # An explicit rs_flight_log_params wins; otherwise the repository's
        # UTM template. Either way __align_zone rewrites its zone from each
        # flight log's own filename tag.
        explicit_flp = None
        if 'rs_flight_log_params' in self.params:
            explicit_flp = self.params['rs_flight_log_params'].get_value() or None
        if explicit_flp and not os.path.isfile(explicit_flp):
            self.logger.error(
                'rs_flight_log_params does not exist: %s - falling back to '
                'Metadata/FlightLogParams.xml', explicit_flp)
            explicit_flp = None
        params_template = flight_log_params_template(METADATA_DIR, explicit_flp)

        # Calibration delivery follows the intake's per-camera decision. An
        # unreadable manifest, or a missing one when the intake is part of
        # this run, fails the stage before anything is aligned: aligning
        # without the decided calibration would be a different run.
        try:
            calibration = self.intake_calibration()
        except ValueError as exc:
            self.logger.error('%s - nothing was aligned.', exc)
            return {'Success': False}
        modes, focals = calibration if calibration is not None else (None, None)
        manifest_path = os.path.join(self.params['output_dir'].get_value(),
                                     RAW_IMAGES, MANIFEST_NAME)
        # With an intake in the picture every zone must carry its flight
        # log: a missing one fails the zone instead of aligning it without
        # priors.
        intake_present = modes is not None or 'ws_run_dirs' in self.params
        if modes is None:
            self.logger.info('No Wild Sync intake manifest at %s - aligning '
                             'without calibration sidecars', manifest_path)
        else:
            self.logger.info(
                'Calibration delivery decided by %s: %s', manifest_path,
                ', '.join(f'{key}={mode} ('
                          + (f'starting focal {focals[key]:g} mm 35mm-eq.'
                             if key in focals else 'no starting focal') + ')'
                          for key, mode in sorted(modes.items()))
                or 'no cameras')

        process_data = []
        skipped_zones = []

        def queue_folder_to_process(local_input_folder, local_output_dir, local_flight_log_path, local_flight_log_params_path, local_display_output):
            """Queue the folder TREE as one alignment scene.

            RealityScan's -addFolder imports subfolders recursively, so a
            zone's per-camera subfolders must NOT be queued as separate
            alignments - doing so splits every mixed-camera zone into
            per-camera scenes that can never co-register.
            """
            if not os.path.isdir(local_input_folder):
                raise ValueError(f"Input folder {local_input_folder} is not a directory")

            # Pool layout (RS_ALIGN_POOL_DIR): a pool zone folder holds NO
            # images - only a .imagelist of canonical pool paths (+ zone
            # flight log). The no-images refusal below is a copy-layout
            # guard; in pool mode a folder carrying a .imagelist is a
            # valid zone (without this, every pool zone was refused with
            # "No images found" although AlignZone.bat supports it).
            pool_zone_ok = bool(os.environ.get('RS_ALIGN_POOL_DIR')) and any(
                n.lower().endswith('.imagelist')
                for n in os.listdir(local_input_folder))

            if not self.__collect_images(local_input_folder) and not pool_zone_ok:
                # A skipped zone must still land in the tally: it used to
                # appear in neither 'Components' nor 'Zones Failed', so
                # 'Component Count' silently excluded it.
                self.logger.error(
                    'No images found under %s - the zone is SKIPPED and '
                    'counted as a failure', local_input_folder)
                skipped_zones.append((local_input_folder, 'no images found'))
                return

            process_data.append({
                'input_folder': local_input_folder,
                'output_dir': local_output_dir,
                'flight_log_path': local_flight_log_path,
                'flight_log_params_path': local_flight_log_params_path,
                'display_output': local_display_output
            })

        def resolve_and_queue(local_input_folder, batch_path):
            """Resolve this folder's flight log, then queue it - recording a
            zone-level FAILURE if either step refuses.

            find_flight_log now REFUSES a directory whose logs disagree on
            UTM zone (or mix tagged and untagged names). That ValueError
            escaped run() as an unhandled traceback out of main.py on two
            of the three call sites here: the batcher grew the matching
            catch, the aligner did not.
            A frame disagreement must fail the ZONE rather than align it
            with no trajectory, so it lands in skipped_zones and therefore
            in 'Zones Failed'.
            """
            try:
                local_flight_log_path = self.__get_flight_log_path(batch_path)
            except ValueError as exc:
                self.logger.error(
                    '%s The zone is SKIPPED and counted as a failure - pass '
                    'the intended log explicitly, or keep one dataset/zone '
                    'per directory.', exc)
                skipped_zones.append(
                    (local_input_folder, 'flight logs disagree on UTM zone'))
                return
            try:
                queue_folder_to_process(
                    local_input_folder, output_dir, local_flight_log_path,
                    params_template, display_output)
            except Exception as e:
                self.logger.error(f"Error queueing folder to process: {e}")
                skipped_zones.append((local_input_folder, str(e)))

        # single folder input (not running after batched images module)
        if 'rs_input_image_dir' in self.params:
            input_folder = self.params['rs_input_image_dir'].get_value()
            # A supplied BATCHED ROOT is expanded per zone, exactly as the
            # chained branch below does. Handing the whole root to
            # -addFolder recurses into every zone and produces ONE fused
            # scene - the zoning silently gone.
            zone_dirs = self.zone_subfolders(input_folder)
            if zone_dirs:
                self.logger.info(
                    '%s is a batched ROOT (%d zone folder(s)) - aligning one '
                    'scene per zone, not one fused scene',
                    input_folder, len(zone_dirs))
                for zone_dir in zone_dirs:
                    resolve_and_queue(zone_dir, zone_dir)
            else:
                resolve_and_queue(input_folder, None)
        # running after batched images module
        else:
            batch_directory = os.path.join(self.params['output_dir'].get_value(), "batched_images_by_zone")
            if not os.path.isdir(batch_directory):
                self.logger.error(
                    f"Batched images directory not found: {batch_directory}. "
                    "Run the Batch Directory module first (or supply an input "
                    "image folder directly).")
                return {'Success': False}
            batch_folders = [f for f in os.listdir(batch_directory) if os.path.isdir(os.path.join(batch_directory, f))]

            for batch_folder in batch_folders:
                batch_input_folder = os.path.join(batch_directory, batch_folder)
                resolve_and_queue(batch_input_folder, batch_input_folder)

        if modes is None:
            registry_images = sum(
                1 for data in process_data
                for name in self.__collect_images(data['input_folder'])
                if camera_registry.identify(name) is not None)
            if registry_images:
                self.logger.warning(
                    '%d image(s) to align are named like registry cameras, '
                    'but there is no Wild Sync intake manifest at %s - '
                    'calibration delivery is OFF for this run', registry_images,
                    manifest_path)

        output_data = {}
        output_data['Success'] = True
        output_data['Output Directory'] = output_dir
        output_data['Component Count'] = len(process_data)
        output_data['Components'] = {}
        output_data['Scenes'] = {}

        bar = self._initialize_loading_bar(len(process_data), "Aligning Batches")

        # process the data sequentially - each run gets exclusive use of the
        # RealityScan instance (enforced by RealityScanCLI's lock) and the
        # instance is verified to have shut down before the next run starts
        for data in process_data:
            # local names: do not shadow the module-level output_dir above
            input_folder = data['input_folder']
            item_flight_log_path = data['flight_log_path']
            item_flight_log_params_path = data['flight_log_params_path']
            item_display_output = data['display_output']

            # Daily RC_projects save schema: RC_projects one level up from
            # the zone image directory, {label}_{zone}_YYYYMMDD.
            project_label = ''
            if 'rs_project_label' in self.params:
                project_label = (self.params['rs_project_label'].get_value() or '').strip()
            if project_label:
                set_project_save_env(os.path.dirname(os.path.normpath(input_folder)),
                                     project_label)
            else:
                os.environ.pop('RS_PROJECTS_DIR', None)

            # Each zone exports into its own subfolder: components stay
            # importable from their ORIGINAL export location (relocated
            # .rsalign imports hang forever) and zones cannot
            # clobber each other's exports.
            zone_name = os.path.basename(os.path.normpath(input_folder))
            zone_output_dir = os.path.join(output_dir, zone_name)
            scene_path = os.path.join(zone_output_dir, zone_name + ".rsproj")

            try:
                min_comp = 50
                if 'rs_min_component_size' in self.params:
                    min_comp = int(self.params['rs_min_component_size'].get_value())
                component_data, scene_data = self.__align_zone(
                    input_folder, zone_output_dir, zone_name,
                    item_flight_log_path, item_flight_log_params_path,
                    item_display_output, min_component_size=min_comp,
                    calibration_modes=modes,
                    require_flight_log=intake_present,
                    starting_focals=focals)
                output_data['Components'][zone_output_dir] = component_data
                output_data['Scenes'][scene_path] = scene_data
            except Exception as e:
                self.logger.error(f"Error aligning images: {e}")
                # A raising zone must land in the tally as a FAILURE, not
                # vanish from it. Before this, a zone that raised (wedged
                # instance, sidecar OSError after a successful align) was
                # counted neither as succeeded nor failed - nine raising
                # zones out of ten still reported 'Zones Failed: 0' and
                # exit 0.
                output_data['Components'][zone_output_dir] = {
                    'Success': False, 'Error': str(e)}

            self._update_loading_bar(bar, 1)

        # Overall success must reflect the per-zone outcomes: this module
        # previously reported Success=True with every zone failed, which
        # made a fully failed alignment run look complete (and exit 0).
        # Zones skipped for holding no images are recorded here too - they
        # used to appear in no tally at all.
        for skipped, reason in skipped_zones:
            output_data['Components'].setdefault(
                os.path.join(output_dir,
                             os.path.basename(os.path.normpath(skipped))),
                {'Success': False, 'Error': reason, 'Skipped': True})
        component_results = list(output_data['Components'].values())
        succeeded = sum(1 for c in component_results if c.get('Success'))
        output_data['Zones Succeeded'] = succeeded
        output_data['Zones Failed'] = len(component_results) - succeeded
        output_data['Component Count'] = len(process_data)
        # ANY failed zone fails the run. 'Zones Failed: 9' with one success
        # used to return Success=True and exit 0 - a merged deliverable
        # missing nine tenths of the dataset, reported as a completed stage.
        if not component_results or output_data['Zones Failed'] > 0:
            output_data['Success'] = False
            if succeeded:
                self.logger.error(
                    '%d of %d zone(s) FAILED - the run is marked unsuccessful '
                    'so the missing zones cannot be merged in silently. The '
                    '%d successful zone(s) are on disk and a re-run resumes '
                    'from them.', output_data['Zones Failed'],
                    len(component_results), succeeded)

        return output_data

    def validate_parameters(self) -> tuple[bool, str | None]:
        success, message = super().validate_parameters()
        if not success:
            return success, message

        if not 'rs_display_output' in self.params:
            return False, 'Display output parameter not found'

        # fail fast if RealityScan itself cannot be found
        try:
            executable = self.cli.find_executable()
            self.logger.info(f"Using RealityScan executable: {executable}")
        except FileNotFoundError as e:
            return False, str(e)

        # No overwrite prompt here: zones write into their own subfolders
        # of aligned_components, so other zones' existing exports are
        # expected, and an input() prompt stalls unattended runs (it also
        # crashed with EOFError under a non-interactive stdin). Stale
        # per-zone exports are cleared in __align_zone instead.
        output_dir = os.path.join(self.params['output_dir'].get_value(), 'aligned_components')
        if not os.path.isdir(output_dir):
            os.makedirs(output_dir)

        return True, None
