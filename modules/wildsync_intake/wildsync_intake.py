"""Wild Sync Intake: the first module of the main.py chain.

Takes one or more Wild Sync run directories into ``<output>/raw_images``:
the chosen image variant of both cameras, one combined 13-column flight log
and the intake manifest. The work is done by :mod:`.intake`; this module
holds the parameters and reports the outcome.
"""
from __future__ import annotations

from module_base.parameter import Parameter
from module_base.rs_module import RSModule

from .. import camera_registry
from ..calibration_sidecars import MODES as CALIBRATION_MODES
from .intake import (DEFAULT_MIN_MATCH_PCT, DEFAULT_STATIC_POSITION_ACCURACY_M,
                     DEFAULT_SURFACE_ALTITUDE_M, DEFAULT_TIME_ERR_WARN_MS,
                     HEADING_SOURCES, VARIANTS, IntakeError, IntakeOptions,
                     check_workspace, find_run_directories, run_intake,
                     split_run_paths)

_PRIOR = camera_registry.PRIOR_ACCURACY


class WildSyncIntake(RSModule):

    def __init__(self, logger):
        super().__init__('Wild Sync Intake', logger)

    def get_parameters(self) -> dict[str, Parameter]:
        additional_params = {}

        additional_params['ws_run_dirs'] = Parameter(
            name='Wild Sync Run Directories',
            cli_short='w_i',
            cli_long='w_input',
            type=str,
            default_value=None,
            description=('Wild Sync run directory (holding cam1/ and cam2/ '
                         'with their flight_log.csv), or a folder of run '
                         'directories; separate several with ";". Only read, '
                         'never modified'),
            prompt_user=True
        )

        additional_params['ws_variant'] = Parameter(
            name='Image Variant',
            cli_short='w_v',
            cli_long='w_variant',
            type=str,
            default_value='card',
            description=('Which image of each frame to take in: '
                         + ' or '.join(VARIANTS) + ' (card = the camera\'s '
                         'full-size JPEG, review = the small review JPEG). '
                         'RAW (.ARW) is not an input format of this pipeline'),
            prompt_user=True
        )

        additional_params['ws_calibration'] = Parameter(
            name='Calibration Mode',
            cli_short='w_cal',
            cli_long='w_calibration',
            type=str,
            default_value='auto',
            description=('Calibration delivered to RealityScan per camera: '
                         + ', '.join(CALIBRATION_MODES) + '. auto applies the '
                         'stored calibration as a prior only when the images '
                         'match it (aspect ratio, EXIF focal length, confirmed '
                         'node assignment) and otherwise gives each camera '
                         'its own calibration group'),
            prompt_user=True
        )

        additional_params['ws_assert_focal'] = Parameter(
            name='Assert Calibration Focal Length',
            cli_short='w_af',
            cli_long='w_assert_focal',
            type=bool,
            default_value=False,
            description=('Assert that the lenses were at the calibration focal '
                         'length when the EXIF focal length is missing or '
                         'differs'),
            prompt_user=False
        )

        additional_params['ws_declination_deg'] = Parameter(
            name='Magnetic Declination (deg)',
            cli_short='w_d',
            cli_long='w_declination',
            type=float,
            default_value=0.0,
            description=('Magnetic declination at the site in degrees, east '
                         'positive, added to the heading (0 = use the heading '
                         'as given)'),
            prompt_user=True
        )

        additional_params['ws_heading_source'] = Parameter(
            name='Heading Source',
            cli_short='w_hs',
            cli_long='w_heading_source',
            type=str,
            default_value='auto',
            description=('flight_log.csv column used for the heading: '
                         + ', '.join(HEADING_SOURCES) + ' (auto = heading_imu, '
                         'else yaw)'),
            prompt_user=False
        )

        additional_params['ws_pos_accuracy_m'] = Parameter(
            name='Position Accuracy (m)',
            cli_short='w_pa',
            cli_long='w_pos_accuracy',
            type=float,
            default_value=_PRIOR.x_m,
            description=('X/Y position accuracy written for every image, in '
                         'metres (end-to-end uncertainty, not the receiver '
                         'specification)'),
            prompt_user=False
        )

        additional_params['ws_static_pos_accuracy_m'] = Parameter(
            name='Static-Fix Position Accuracy (m)',
            cli_short='w_spa',
            cli_long='w_static_pos_accuracy',
            type=float,
            default_value=DEFAULT_STATIC_POSITION_ACCURACY_M,
            description=('X/Y accuracy written instead when every row of a run '
                         'carries the same position (a static fix), so it '
                         'cannot constrain the solve'),
            prompt_user=False
        )

        additional_params['ws_alt_accuracy_m'] = Parameter(
            name='Altitude Accuracy (m)',
            cli_short='w_aa',
            cli_long='w_alt_accuracy',
            type=float,
            default_value=_PRIOR.alt_m,
            description='Altitude accuracy written for every image, in metres',
            prompt_user=False
        )

        additional_params['ws_surface_alt_m'] = Parameter(
            name='Surface Altitude (m)',
            cli_short='w_sa',
            cli_long='w_surface_altitude',
            type=float,
            default_value=DEFAULT_SURFACE_ALTITUDE_M,
            description=('Altitude written when the depth cell is empty '
                         '(0.0 = camera at the sea surface)'),
            prompt_user=False
        )

        additional_params['ws_orientation_accuracy_deg'] = Parameter(
            name='Orientation Accuracy (deg)',
            cli_short='w_oa',
            cli_long='w_orientation_accuracy',
            type=float,
            default_value=_PRIOR.yaw_deg,
            description=('Yaw and roll accuracy written for every image, in '
                         'degrees; pitch accuracy comes from the camera mount '
                         'in modules/cameras.json'),
            prompt_user=False
        )

        additional_params['ws_min_match_pct'] = Parameter(
            name='Minimum Match Rate (%)',
            cli_short='w_mr',
            cli_long='w_min_match_rate',
            type=float,
            default_value=DEFAULT_MIN_MATCH_PCT,
            description=('Fail when fewer than this percent of frames have both '
                         'a flight-log row and an image of the chosen variant '
                         '(0 disables the floor)'),
            prompt_user=False
        )

        additional_params['ws_time_err_warn_ms'] = Parameter(
            name='Time Error Warning (ms)',
            cli_short='w_te',
            cli_long='w_time_err_warn',
            type=float,
            default_value=DEFAULT_TIME_ERR_WARN_MS,
            description=('Warn when a row\'s time_err_ms is above this many '
                         'milliseconds (nothing is dropped)'),
            prompt_user=False
        )

        return {**super().get_parameters(), **additional_params}

    def _value(self, key: str, default):
        param = (self.params or {}).get(key)
        value = None if param is None else param.get_value()
        return default if value is None else value

    def intake_options(self) -> IntakeOptions:
        """The options the parameters describe (defaults where unset)."""
        defaults = IntakeOptions()

        def number(key, default):
            value = self._value(key, default)
            try:
                return float(value)
            except (TypeError, ValueError):
                return value          # reported by IntakeOptions.problems

        assert_focal = self._value('ws_assert_focal', False)
        if isinstance(assert_focal, str):
            assert_focal = assert_focal.strip().lower() in (
                'true', 't', 'yes', 'y', '1')
        return IntakeOptions(
            variant=str(self._value('ws_variant', defaults.variant)).strip().lower(),
            position_accuracy_m=number('ws_pos_accuracy_m',
                                       defaults.position_accuracy_m),
            altitude_accuracy_m=number('ws_alt_accuracy_m',
                                       defaults.altitude_accuracy_m),
            orientation_accuracy_deg=number('ws_orientation_accuracy_deg',
                                            defaults.orientation_accuracy_deg),
            heading_source=str(self._value(
                'ws_heading_source', defaults.heading_source)).strip().lower(),
            declination_deg=number('ws_declination_deg',
                                   defaults.declination_deg),
            surface_altitude_m=number('ws_surface_alt_m',
                                      defaults.surface_altitude_m),
            static_position_accuracy_m=number(
                'ws_static_pos_accuracy_m',
                defaults.static_position_accuracy_m),
            min_match_pct=number('ws_min_match_pct', defaults.min_match_pct),
            time_err_warn_ms=number('ws_time_err_warn_ms',
                                    defaults.time_err_warn_ms),
            calibration=str(self._value(
                'ws_calibration', defaults.calibration)).strip().lower(),
            focal_asserted=bool(assert_focal))

    def run_paths(self) -> list[str]:
        return split_run_paths(self._value('ws_run_dirs', ''))

    def run(self):
        try:
            result = run_intake(self.run_paths(),
                                self.params['output_dir'].get_value(),
                                self.intake_options(), self.logger)
        except (IntakeError, OSError) as exc:
            self.logger.error('Wild Sync intake failed: %s', exc)
            return {'Success': False, 'Failure': str(exc)}
        manifest = result.manifest
        images = manifest['images']
        return {
            'Success': True,
            'Runs': ', '.join(s['run_id'] for s in manifest['sources']),
            'Variant': manifest['variant'],
            'Images': images['matched'],
            'Copied': images['copied'],
            'Already Present': images['reused'],
            'Unmatched Rows': images['unmatched_rows'],
            'Unmatched Images': images['unmatched_images'],
            'Match Rate %': images['match_rate_pct'],
            'Flight Log': result.flight_log_path,
            'Flight Log Rows': manifest['flight_log_rows'],
            'Static Fix': manifest['static_fix'],
            'Calibration': {key: decision['mode'] for key, decision
                            in manifest['calibration'].items()},
            'Warnings': len(manifest['warnings']),
            'Manifest': result.manifest_path,
        }

    def validate_parameters(self) -> tuple[bool, str | None]:
        success, message = super().validate_parameters()
        if not success:
            return success, message
        problems = self.intake_options().problems()
        if problems:
            return False, '; '.join(problems)
        try:
            runs = find_run_directories(self.run_paths())
            check_workspace(self.params['output_dir'].get_value(), runs)
        except IntakeError as exc:
            return False, str(exc)
        return True, None
