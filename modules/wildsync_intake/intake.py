"""Wild Sync run directories turned into the inputs of the alignment chain.

A Wild Sync run directory (``<run_id>/``) holds one folder per camera node
(``cam1/``, ``cam2/``). Each node folder has a ``flight_log.csv`` and, per
frame, files that share one frame id::

    Cam1_20260820_192542.42.jpg        review JPEG
    Cam1_20260820_192542.42.card.JPG   card JPEG
    Cam1_20260820_192542.42.ARW        RAW
    Cam1_20260820_192542.42.xmp        Wild Sync metadata sidecar

The frame id is the file name without the suffix
``(.card)?.(jpg|jpeg|arw|xmp)`` (:func:`frame_id`). It contains a dot
itself, so ``os.path.splitext`` must never be applied to a name that has
already lost its extension.

:func:`run_intake`:

1. reads every node's ``flight_log.csv`` strictly (exact header, numeric
   cells finite or missing, an empty cell is a missing value);
2. joins each row to the chosen image variant by frame id and copies that
   image, unchanged, to ``<workspace>/raw_images/<camera key>/``;
3. writes one 13-column flight log for every camera,
   ``raw_images/flight_log_<zone><band>_UTM.txt``
   (:func:`modules.flight_logs.write_flight_log`);
4. decides the calibration delivery per camera from the original images
   (:func:`modules.calibration_sidecars.decide_calibration`), and records
   the focal length observed on them and the starting focal RealityScan
   gets (:func:`modules.calibration_sidecars.observe_focal`); a focal that
   differs between images of one camera, or is missing, fails the intake
   unless the operator gives the focal length (``focal_override_mm``). The
   decision is made here, on the originals the operator handed in, and
   recorded once; later stages read it rather than re-deciding it on the
   preprocessed copies (which keep the EXIF);
5. records all of it in ``raw_images/wildsync_intake.json``, which later
   stages read (:func:`load_manifest`, :func:`calibration_modes`,
   :func:`starting_focals`).

The run directories are only read. Wild Sync's own ``.xmp`` sidecars are
never copied: the calibration sidecar of a review JPEG has the same name
(:func:`modules.calibration_sidecars.sidecar_path`).

RAW (``.ARW``) is not an input format of this pipeline: preprocessing and
batching handle JPEG and PNG only.

Standard library only; Pillow is imported when image geometry is read.
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import math
import os
import re
import shutil
import tempfile
import time
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from .. import camera_registry
from ..calibration_sidecars import (
    DECIDED_MODES,
    CalibrationDecision,
    CalibrationRefused,
    FocalRefused,
    ObservedFocal,
    decide_calibration,
    is_own_sidecar,
    observe_focal,
    read_image_geometry,
    sidecar_path,
)
from ..calibration_sidecars import MODES as CALIBRATION_MODES
from ..camera_registry import Camera, Mount, Registry
from ..flight_logs import (
    FlightLogRow,
    flight_log_name,
    realityscan_orientation,
    validate_utm_zone,
    write_flight_log,
)

logger = logging.getLogger(__name__)

FLIGHT_LOG_CSV = 'flight_log.csv'

# Wild Sync's flight_log.csv header, exactly (Wild Sync docs/PROTOCOL.md).
FLIGHT_LOG_CSV_HEADER = (
    'filename', 'datetime', 'lat', 'long', 'xutm', 'yutm', 'utm_zone',
    'depth_from_xplore9', 'pitch', 'roll', 'yaw', 'heading_mag_xplore',
    'heading_imu', 'ax_g', 'ay_g', 'az_g', 'gx_dps', 'gy_dps', 'gz_dps',
    'imu_temp_c', 'capture_source', 'time_source', 'time_err_ms')

TEXT_COLUMNS = frozenset(
    {'filename', 'datetime', 'utm_zone', 'capture_source', 'time_source'})
NUMERIC_COLUMNS = tuple(c for c in FLIGHT_LOG_CSV_HEADER
                        if c not in TEXT_COLUMNS)

RAW_IMAGES = 'raw_images'
MANIFEST_NAME = 'wildsync_intake.json'
# 2: every camera's calibration entry carries the observed EXIF focal and
# the starting focal RealityScan gets (starting_focals). A schema-1
# manifest has neither and is refused, never read as if it had them.
MANIFEST_SCHEMA = 2
# Manifest status while an intake is copying; only 'complete' is usable.
STATUS_IN_PROGRESS = 'in_progress'

VARIANTS = ('card', 'review')
HEADING_SOURCES = ('auto', 'heading_imu', 'yaw', 'heading_mag_xplore')

# A run whose positions all lie within this distance of each other (bounding
# box diagonal, metres) carries one static fix, not a track.
STATIC_FIX_SPREAD_M = 1.0

DEFAULT_STATIC_POSITION_ACCURACY_M = 1000.0
DEFAULT_MIN_MATCH_PCT = 80.0
DEFAULT_TIME_ERR_WARN_MS = 50.0
DEFAULT_SURFACE_ALTITUDE_M = 0.0
# Altitude accuracy written for a row whose altitude is the surface-altitude
# fallback (no depth): wide, like the static-fix position accuracy, so the
# fallback cannot pin a submerged camera to the surface.
DEFAULT_SURFACE_ALTITUDE_ACCURACY_M = 1000.0

_FRAME_SUFFIX = re.compile(r'(\.card)?\.(jpe?g|arw|xmp)$', re.IGNORECASE)
_ZONE_TAG = re.compile(r'^(\d{1,2})([A-Za-z])$')
_MGRS_BANDS = 'CDEFGHJKLMNPQRSTUVWX'
_TEMP_PREFIX = '.wildsync_'
_IGNORED_FILES = frozenset({'thumbs.db', 'desktop.ini'})


class IntakeError(ValueError):
    """The run directories cannot be taken in as given."""


# ------------------------------------------------------------ file names

def frame_id(name: str) -> str | None:
    """Frame id of a Wild Sync file name, or None without a known suffix.

    ``Cam1_20260820_192542.42.card.JPG`` -> ``Cam1_20260820_192542.42``.
    """
    base = os.path.basename(name)
    match = _FRAME_SUFFIX.search(base)
    if match is None or match.start() == 0:
        return None
    return base[:match.start()]


def image_variant(name: str) -> str | None:
    """``'card'``, ``'review'``, ``'raw'`` or ``'sidecar'`` for a Wild Sync
    file name, else None."""
    match = _FRAME_SUFFIX.search(os.path.basename(name))
    if match is None or match.start() == 0:
        return None
    ext = match.group(2).lower()
    card = match.group(1) is not None
    if ext in ('jpg', 'jpeg'):
        return 'card' if card else 'review'
    if card:
        return None
    return 'raw' if ext == 'arw' else 'sidecar'


# ------------------------------------------------------------ options

@dataclass(frozen=True)
class IntakeOptions:
    """Everything the intake can be told, with the pipeline defaults."""
    variant: str = 'card'
    position_accuracy_m: float = camera_registry.PRIOR_ACCURACY.x_m
    altitude_accuracy_m: float = camera_registry.PRIOR_ACCURACY.alt_m
    orientation_accuracy_deg: float = camera_registry.PRIOR_ACCURACY.yaw_deg
    heading_source: str = 'auto'
    declination_deg: float = 0.0
    surface_altitude_m: float = DEFAULT_SURFACE_ALTITUDE_M
    surface_altitude_accuracy_m: float = DEFAULT_SURFACE_ALTITUDE_ACCURACY_M
    static_position_accuracy_m: float = DEFAULT_STATIC_POSITION_ACCURACY_M
    min_match_pct: float = DEFAULT_MIN_MATCH_PCT
    time_err_warn_ms: float = DEFAULT_TIME_ERR_WARN_MS
    calibration: str = 'auto'
    focal_asserted: bool = False
    focal_override_mm: float | None = None

    def problems(self) -> list[str]:
        """Every invalid option, as a message; empty when all are valid."""
        out: list[str] = []
        variant = str(self.variant).strip().lower()
        if variant in ('raw', 'arw', '.arw'):
            out.append('image variant RAW (.ARW) is not an input format of '
                       'this pipeline (preprocessing and batching handle JPEG '
                       'and PNG only); choose card or review')
        elif variant not in VARIANTS:
            out.append(f'image variant must be one of {", ".join(VARIANTS)}, '
                       f'got {self.variant!r}')
        if self.heading_source not in HEADING_SOURCES:
            out.append(f'heading source must be one of '
                       f'{", ".join(HEADING_SOURCES)}, got '
                       f'{self.heading_source!r}')
        if self.calibration not in CALIBRATION_MODES:
            out.append(f'calibration mode must be one of '
                       f'{", ".join(CALIBRATION_MODES)}, got '
                       f'{self.calibration!r}')
        if not isinstance(self.focal_asserted, bool):
            out.append('the focal-length assertion must be true or false')
        override = self.focal_override_mm
        if override is not None:
            if isinstance(override, bool) \
                    or not isinstance(override, (int, float)) \
                    or not (math.isfinite(override) and override > 0):
                out.append('focal override (mm) must be a positive number, '
                           f'got {override!r}')
            if self.focal_asserted is True:
                out.append('give either the focal override or the '
                           'calibration focal-length assertion, not both')
            if self.calibration == 'off':
                out.append('a focal override reaches RealityScan only '
                           'through a calibration sidecar, and calibration '
                           'off writes none; choose auto, groups or prior, '
                           'or drop the override')
        for label, value, lowest, strict in (
                ('position accuracy (m)', self.position_accuracy_m, 0.0, True),
                ('altitude accuracy (m)', self.altitude_accuracy_m, 0.0, True),
                ('orientation accuracy (deg)', self.orientation_accuracy_deg,
                 0.0, True),
                ('static-fix position accuracy (m)',
                 self.static_position_accuracy_m, 0.0, True),
                ('surface altitude accuracy (m)',
                 self.surface_altitude_accuracy_m, 0.0, True),
                ('time error warning threshold (ms)', self.time_err_warn_ms,
                 0.0, False),
                ('minimum match rate (%)', self.min_match_pct, 0.0, False),
                ('magnetic declination (deg)', self.declination_deg, None,
                 False),
                ('surface altitude (m)', self.surface_altitude_m, None,
                 False)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) \
                    or not math.isfinite(value):
                out.append(f'{label} must be a finite number, got {value!r}')
            elif lowest is not None and (value <= lowest if strict
                                         else value < lowest):
                out.append(f'{label} must be {"above" if strict else "at least"} '
                           f'{lowest:g}, got {value!r}')
        if isinstance(self.min_match_pct, (int, float)) \
                and not isinstance(self.min_match_pct, bool) \
                and self.min_match_pct > 100:
            out.append(f'minimum match rate (%) must be at most 100, got '
                       f'{self.min_match_pct!r}')
        return out


# ------------------------------------------------------- flight_log.csv

@dataclass(frozen=True)
class LogRow:
    """One parsed flight_log.csv row. Numbers are finite floats or None."""
    path: str
    line: int
    filename: str
    utm_zone: tuple[int, str] | None
    capture_source: str
    time_source: str
    numbers: Mapping[str, float | None]

    def number(self, column: str) -> float | None:
        return self.numbers[column]


def _header_problem(header: Sequence[str]) -> str:
    unknown = [c for c in header if c not in FLIGHT_LOG_CSV_HEADER]
    missing = [c for c in FLIGHT_LOG_CSV_HEADER if c not in header]
    parts = []
    if unknown:
        parts.append('unknown column(s) ' + ', '.join(repr(c) for c in unknown))
    if missing:
        parts.append('missing column(s) ' + ', '.join(missing))
    if not parts:
        parts.append('columns repeated or out of order')
    return '; '.join(parts)


def read_flight_log_csv(path: str) -> list[LogRow]:
    """Every row of a Wild Sync ``flight_log.csv``, or IntakeError.

    The header must be exactly :data:`FLIGHT_LOG_CSV_HEADER`. An empty cell
    is a missing value. A numeric cell that does not parse as a number makes
    the log unreadable; one that parses to a non-finite value (nan, inf) is
    a missing value. ``utm_zone`` must be a zone number and MGRS latitude
    band (``19T``) or empty. Blank lines are skipped.
    """
    rows: list[LogRow] = []
    try:
        with open(path, encoding='utf-8-sig', newline='') as stream:
            reader = csv.reader(stream)
            header = next(reader, None)
            if header is None:
                raise IntakeError(f'{path}: empty file, expected the Wild Sync '
                                  'flight_log.csv header')
            if tuple(header) != FLIGHT_LOG_CSV_HEADER:
                raise IntakeError(
                    f'{path}: not a Wild Sync flight_log.csv header '
                    f'({_header_problem(header)}). Expected exactly: '
                    + ','.join(FLIGHT_LOG_CSV_HEADER))
            for cells in reader:
                if not cells or all(not c.strip() for c in cells):
                    continue
                rows.append(_parse_row(path, reader.line_num, cells))
    except IntakeError:
        raise
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise IntakeError(f'{path}: cannot read the flight log: {exc}') from exc
    return rows


def _parse_row(path: str, line: int, cells: list[str]) -> LogRow:
    where = f'{path}: line {line}'
    if len(cells) != len(FLIGHT_LOG_CSV_HEADER):
        raise IntakeError(f'{where} has {len(cells)} cells, expected '
                          f'{len(FLIGHT_LOG_CSV_HEADER)}')
    cell = dict(zip(FLIGHT_LOG_CSV_HEADER, (c.strip() for c in cells)))
    if not cell['filename']:
        raise IntakeError(f'{where} has an empty filename')
    numbers: dict[str, float | None] = {}
    for column in NUMERIC_COLUMNS:
        text = cell[column]
        if not text:
            numbers[column] = None
            continue
        try:
            value = float(text)
        except ValueError:
            raise IntakeError(f'{where}: {column} {text!r} is not a '
                              'number') from None
        numbers[column] = value if math.isfinite(value) else None
    zone = None
    if cell['utm_zone']:
        match = _ZONE_TAG.match(cell['utm_zone'])
        try:
            if match is None:
                raise ValueError
            zone = validate_utm_zone(int(match.group(1)), match.group(2))
        except ValueError:
            raise IntakeError(
                f'{where}: utm_zone {cell["utm_zone"]!r} is not a UTM zone '
                'number with its MGRS latitude band (for example 19T)') from None
    return LogRow(path, line, cell['filename'], zone, cell['capture_source'],
                  cell['time_source'], numbers)


# ------------------------------------------------------- run directories

def split_run_paths(value) -> list[str]:
    """Run directory paths from one parameter value, separated by ``;`` or
    line breaks; surrounding quotes and spaces are removed."""
    if value is None:
        return []
    out = []
    for part in re.split(r'[;\r\n]', str(value)):
        part = part.strip().strip('"\'').strip()
        if part:
            out.append(part)
    return out


def node_directories(run_dir: str) -> list[str]:
    """A run's camera node folders: direct subfolders holding a
    ``flight_log.csv``, sorted by name."""
    try:
        names = sorted(os.listdir(run_dir))
    except OSError:
        return []
    return [os.path.join(run_dir, name) for name in names
            if os.path.isfile(os.path.join(run_dir, name, FLIGHT_LOG_CSV))]


def find_run_directories(paths: Iterable[str]) -> list[str]:
    """Absolute run directories for the given paths, in order.

    Each path is a run directory or a folder whose direct subfolders
    include run directories (taken in name order). A path that is neither
    raises IntakeError; a run named twice is taken once.
    """
    runs: list[str] = []
    seen: set[str] = set()
    for path in paths:
        if not os.path.isdir(path):
            raise IntakeError(f'Wild Sync run directory not found: {path}')
        if node_directories(path):
            candidates = [path]
        else:
            candidates = [os.path.join(path, name)
                          for name in sorted(os.listdir(path))
                          if node_directories(os.path.join(path, name))]
            if not candidates:
                raise IntakeError(
                    f'{path} is neither a Wild Sync run directory (camera '
                    f'node folders with a {FLIGHT_LOG_CSV}, e.g. cam1/'
                    f'{FLIGHT_LOG_CSV}) nor a folder of run directories')
        for candidate in candidates:
            key = os.path.normcase(os.path.realpath(candidate))
            if key not in seen:
                seen.add(key)
                runs.append(os.path.abspath(candidate))
    if not runs:
        raise IntakeError('no Wild Sync run directory was given')
    return runs


def check_workspace(workspace: str, run_dirs: Iterable[str]) -> None:
    """IntakeError when the workspace is a run directory or lies inside one:
    the intake never writes into a run directory."""
    if not workspace or not str(workspace).strip():
        raise IntakeError('no workspace (output directory) was given')
    target = os.path.normcase(os.path.realpath(workspace))
    for run in run_dirs:
        source = os.path.normcase(os.path.realpath(run))
        try:
            shared = os.path.commonpath((source, target))
        except ValueError:
            continue
        if shared == source:
            raise IntakeError(
                f'the workspace {workspace} lies inside the run directory '
                f'{run}; Wild Sync run directories are only read')


# --------------------------------------------------------------- planning

@dataclass(frozen=True)
class Frame:
    """One image to take in: its log row and where it comes from."""
    run_dir: str
    node: str
    camera: str
    mount: Mount
    source: str
    name: str
    row: LogRow


@dataclass
class NodeReport:
    node: str
    camera: str
    log_rows: int = 0
    variant_images: int = 0
    matched: int = 0
    unmatched_rows: list | None = None
    unmatched_images: list | None = None
    hidden_files: int = 0

    def as_dict(self) -> dict:
        return {'node': self.node, 'camera': self.camera,
                'log_rows': self.log_rows,
                'variant_images': self.variant_images,
                'matched': self.matched,
                'unmatched_rows': list(self.unmatched_rows or ()),
                'unmatched_images': list(self.unmatched_images or ()),
                'hidden_files_ignored': self.hidden_files}


@dataclass
class RunPlan:
    run_dir: str
    frames: list
    nodes: list
    positions: list

    @property
    def run_id(self) -> str:
        return os.path.basename(os.path.normpath(self.run_dir))

    def position_spread_m(self) -> float | None:
        """Bounding-box diagonal of every row position in the run."""
        if not self.positions:
            return None
        xs = [p[0] for p in self.positions]
        ys = [p[1] for p in self.positions]
        return math.hypot(max(xs) - min(xs), max(ys) - min(ys))

    def static_fix(self) -> bool:
        spread = self.position_spread_m()
        return spread is not None and spread < STATIC_FIX_SPREAD_M


def _position(row: LogRow) -> tuple[float, float, tuple[int, str]] | None:
    x, y = row.number('xutm'), row.number('yutm')
    if x is None or y is None or row.utm_zone is None:
        return None
    return x, y, row.utm_zone


def plan_run(run_dir: str, variant: str,
             registry: Registry | None = None) -> RunPlan:
    """Join every node's log rows to the run's ``variant`` images.

    IntakeError for an unreadable log, a row naming a file that is not a
    Wild Sync image name or that belongs to another camera than its node
    folder, two rows for one frame, or two images of one variant for one
    frame.
    """
    registry = registry or camera_registry.REGISTRY
    frames: list[Frame] = []
    nodes: list[NodeReport] = []
    positions: list[tuple[float, float]] = []
    for node_dir in node_directories(run_dir):
        node = os.path.basename(node_dir)
        log_path = os.path.join(node_dir, FLIGHT_LOG_CSV)
        rows = read_flight_log_csv(log_path)

        images: dict[str, str] = {}
        hidden = 0
        for name in sorted(os.listdir(node_dir)):
            if name.startswith('.'):
                # Hidden files are never Wild Sync frames; a copy made on
                # macOS adds an AppleDouble '._<name>' beside every file.
                hidden += 1
                continue
            if image_variant(name) != variant \
                    or not os.path.isfile(os.path.join(node_dir, name)):
                continue
            key = frame_id(name).lower()
            if key in images:
                raise IntakeError(
                    f'{node_dir}: two {variant} images for frame '
                    f'{frame_id(name)}: {images[key]} and {name}')
            images[key] = name

        node_family = next((f for f in registry.families
                            if f.name.lower() == node.lower()), None)
        report = NodeReport(node, node_family.camera if node_family else '',
                            log_rows=len(rows),
                            variant_images=len(images),
                            unmatched_rows=[], unmatched_images=[],
                            hidden_files=hidden)
        named: dict[str, LogRow] = {}
        for row in rows:
            fid = frame_id(row.filename)
            if fid is None:
                raise IntakeError(
                    f'{log_path}: line {row.line}: {row.filename!r} is not a '
                    'Wild Sync image name (.jpg, .card.JPG, .ARW)')
            family = registry.family(row.filename)
            if family is None:
                raise IntakeError(
                    f'{log_path}: line {row.line}: {row.filename!r} matches no '
                    'camera family in modules/cameras.json')
            if family.name.lower() != node.lower():
                raise IntakeError(
                    f'{log_path}: line {row.line}: {row.filename!r} belongs to '
                    f'camera family {family.name} but sits in node folder '
                    f'{node}; the run directory is inconsistent')
            key = fid.lower()
            if key in named:
                raise IntakeError(
                    f'{log_path}: lines {named[key].line} and {row.line} both '
                    f'name frame {fid}')
            named[key] = row
            position = _position(row)
            if position is not None:
                positions.append(position[:2])
            name = images.get(key)
            if name is None:
                report.unmatched_rows.append(row.filename)
                continue
            frames.append(Frame(run_dir, node, family.camera, family.mount,
                                os.path.join(node_dir, name), name, row))
            report.matched += 1
        report.unmatched_images = [name for key, name in images.items()
                                   if key not in named]
        nodes.append(report)
    return RunPlan(os.path.abspath(run_dir), frames, nodes, positions)


# ------------------------------------------------------ prior conversions

def select_heading(row: LogRow, source: str = 'auto'
                   ) -> tuple[float | None, str | None]:
    """(heading, column it came from) for one row, or (None, None).

    ``auto`` takes ``heading_imu`` and, where it is empty, ``yaw``. Any
    other source names the one column to use.
    """
    columns = ('heading_imu', 'yaw') if source == 'auto' else (source,)
    for column in columns:
        value = row.number(column)
        if value is not None:
            return value, column
    return None, None


def orientation_prior(heading_deg, pitch_deg, roll_deg, mount: Mount,
                      declination_deg: float = 0.0
                      ) -> tuple[float | None, float | None, float | None]:
    """(yaw, pitch, roll) flight-log prior for one image, or three Nones.

    ::

        yaw   = (heading + declination + mount yaw offset) mod 360
        pitch = 90 + (IMU pitch - mount down tilt)
        roll  = IMU roll

    RealityScan's pitch is 0 for a camera looking straight down, so the
    nominal nadir mount (down tilt 90) gives pitch = IMU pitch. A missing
    heading, pitch or roll gives no orientation prior for the image (three
    empty cells), never zeros: the three angles are one rotation.

    This follows RealityScan's orientation convention as implemented in
    :func:`modules.flight_logs.realityscan_orientation` and has not been
    validated for the Wild Sync IMU axes; the wide default orientation
    accuracy is what keeps an error in it from dominating the solve.
    """
    yaw, pitch, roll = realityscan_orientation(
        heading_deg, pitch_deg, roll_deg,
        down_tilt_deg=mount.down_tilt_deg, declination_deg=declination_deg,
        yaw_offset_deg=mount.yaw_offset_deg)
    if yaw is None or pitch is None or roll is None:
        return None, None, None
    return yaw, pitch, roll


def altitude_prior(depth_m: float | None, surface_altitude_m: float,
                   lever_arm_down_m: float = 0.0) -> float:
    """Flight-log altitude: ``-abs(depth) - lever arm down`` when the depth
    is known, else the configured surface altitude (0.0 = camera at the sea
    surface)."""
    if depth_m is None:
        return float(surface_altitude_m)
    return -abs(depth_m) - lever_arm_down_m


def lever_arm_offset(lever_arm_m, heading_deg: float | None
                     ) -> tuple[float, float] | None:
    """(east, north) offset in metres of a camera mounted ``lever_arm_m`` =
    (forward, right, down) from the navigation reference, for a vehicle
    heading clockwise from north. None when a horizontal offset exists but
    the heading is unknown."""
    forward, right = lever_arm_m[0], lever_arm_m[1]
    if forward == 0 and right == 0:
        return 0.0, 0.0
    if heading_deg is None:
        return None
    h = math.radians(heading_deg)
    return (forward * math.sin(h) + right * math.cos(h),
            forward * math.cos(h) - right * math.sin(h))


def mgrs_band(latitude: float) -> str | None:
    """MGRS latitude band letter of a latitude, or None outside -80..84."""
    if not -80.0 <= latitude <= 84.0:
        return None
    return _MGRS_BANDS[min(int((latitude + 80.0) // 8), len(_MGRS_BANDS) - 1)]


def utm_zone_number(longitude: float) -> int:
    """Standard UTM zone number of a longitude (the Norway and Svalbard
    exceptions are not applied)."""
    return int(((longitude + 180.0) % 360.0) // 6) + 1


def _south(band: str) -> bool:
    return band < 'N'


@dataclass(frozen=True)
class RowPrior:
    """One flight-log row and what went into it."""
    row: FlightLogRow
    heading_column: str | None
    has_orientation: bool
    has_depth: bool
    has_position: bool
    lever_arm_skipped: bool


def flight_log_prior(frame: Frame, options: IntakeOptions,
                     static_fix: bool) -> RowPrior:
    """The 13-column row for one image (see :func:`orientation_prior` and
    :func:`altitude_prior`). A static-fix run gets the static position
    accuracy; an image without depth gets the surface-altitude accuracy for
    its fallback altitude; an image without heading, pitch or roll gets no
    orientation cells; one without a UTM position gets no position cells."""
    row = frame.row
    mount = frame.mount
    heading, column = select_heading(row, options.heading_source)
    yaw, pitch, roll = orientation_prior(
        heading, row.number('pitch'), row.number('roll'), mount,
        options.declination_deg)
    has_orientation = yaw is not None

    lever = mount.lever_arm_m or (0.0, 0.0, 0.0)
    true_heading = (None if heading is None
                    else heading + options.declination_deg)
    offset = lever_arm_offset(lever, true_heading)
    lever_arm_skipped = offset is None
    if offset is None:
        offset = (0.0, 0.0)

    position = _position(row)
    if position is None:
        x = y = x_acc = y_acc = None
    else:
        x, y = position[0] + offset[0], position[1] + offset[1]
        x_acc = y_acc = (options.static_position_accuracy_m if static_fix
                         else options.position_accuracy_m)
    depth = row.number('depth_from_xplore9')
    alt = altitude_prior(depth, options.surface_altitude_m, lever[2])
    alt_acc = (options.altitude_accuracy_m if depth is not None
               else options.surface_altitude_accuracy_m)

    accuracy = options.orientation_accuracy_deg
    log_row = FlightLogRow(
        frame.name, x, y, alt, x_acc, y_acc, alt_acc,
        yaw, pitch, roll,
        accuracy if has_orientation else None,
        mount.pitch_accuracy_deg if has_orientation else None,
        accuracy if has_orientation else None)
    return RowPrior(log_row, column if has_orientation else None,
                    has_orientation, depth is not None, position is not None,
                    lever_arm_skipped)


# ---------------------------------------------------------------- writing

def _sha256(path: str) -> str:
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _check_existing(source: str, destination: str) -> None:
    """IntakeError unless ``destination`` is a plain file identical to
    ``source`` (a re-run of the same intake)."""
    if os.path.islink(destination) or not os.path.isfile(destination):
        raise IntakeError(f'{destination} exists and is not a plain file')
    if os.path.getsize(destination) != os.path.getsize(source) \
            or _sha256(destination) != _sha256(source):
        raise IntakeError(
            f'{destination} already exists with different content than '
            f'{source}; use a fresh workspace')


def _copy_image(source: str, destination: str) -> None:
    """Copy ``source`` (bytes and timestamps) through a temporary file, so
    an interrupted copy never leaves a partial image under the final name."""
    directory = os.path.dirname(destination)
    os.makedirs(directory, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=directory, prefix=_TEMP_PREFIX,
                                    suffix='.part')
    os.close(handle)
    try:
        shutil.copy2(source, temp)
        os.replace(temp, destination)
    finally:
        if os.path.exists(temp):
            os.remove(temp)


def _write_text_atomically(path: str, write) -> None:
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=directory, prefix=_TEMP_PREFIX,
                                    suffix='.part')
    os.close(handle)
    try:
        write(temp)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.remove(temp)


def _write_json(path: str, data: dict) -> None:
    def write(temp):
        with open(temp, 'w', encoding='utf-8', newline='\n') as stream:
            json.dump(data, stream, indent=2)
            stream.write('\n')
    _write_text_atomically(path, write)


def _own_sidecar(path: str, sidecar_cameras: Mapping[str, Camera]) -> bool:
    """True when ``path`` is the calibration sidecar of a planned image and
    holds, byte for byte, one the alignment stage writes for its camera."""
    camera = sidecar_cameras.get(os.path.normcase(os.path.abspath(path)))
    if camera is None:
        return False
    with open(path, 'rb') as stream:
        return is_own_sidecar(stream.read(), camera)


def _unexpected_files(raw_dir: str, planned: set[str],
                      sidecar_cameras: Mapping[str, Camera] | None = None
                      ) -> list[str]:
    """Files under ``raw_dir`` this intake did not plan. The calibration
    sidecars the alignment stage writes beside a planned image (when it is
    pointed at raw_images directly) are not counted and are left as they
    are; ``sidecar_cameras`` maps each planned image's sidecar path
    (normcase, absolute) to the image's camera."""
    extras = []
    for root, _dirs, files in os.walk(raw_dir):
        for name in files:
            if name.startswith('.') or name.lower() in _IGNORED_FILES:
                continue
            full = os.path.join(root, name)
            path = os.path.normcase(os.path.abspath(full))
            if path in planned or _own_sidecar(full, sidecar_cameras or {}):
                continue
            extras.append(os.path.relpath(full, raw_dir))
    return sorted(extras)


# --------------------------------------------------------------- the run

@dataclass(frozen=True)
class IntakeResult:
    manifest: dict
    manifest_path: str
    flight_log_path: str


def _examples(names: Sequence[str], limit: int = 3) -> str:
    shown = ', '.join(names[:limit])
    return shown + (f' (+{len(names) - limit} more)' if len(names) > limit
                    else '')


def _calibration_record(camera: Camera, decision: CalibrationDecision,
                        observed: ObservedFocal) -> dict:
    """The manifest entry of one camera: the calibration decision, the
    focal observed on its original images, and the 35 mm-equivalent focal
    RealityScan starts from in the decided mode - the observed (or
    override) focal in a ``groups`` sidecar, the calibration's focal in a
    ``prior`` sidecar, and with ``off`` the images' own EXIF focal, which
    RealityScan reads from the (preprocessed) images."""
    record = {**decision.as_dict(), **observed.as_dict()}
    if decision.mode == 'prior':
        record['starting_focal_35mm'] = camera.focal_length_35mm
        record['starting_focal_source'] = 'calibration'
    elif decision.mode == 'off':
        record['starting_focal_35mm'] = observed.exif_focal_35mm
        record['starting_focal_source'] = 'exif'
    return record


def run_intake(run_paths: Sequence[str], workspace: str,
               options: IntakeOptions | None = None,
               log: logging.Logger | None = None,
               registry: Registry | None = None) -> IntakeResult:
    """Take one or more Wild Sync run directories into ``workspace``.

    Fails (IntakeError) before anything is written on: an unreadable log, no
    matched image, a match rate below ``options.min_match_pct``, a duplicate
    image name, mixed UTM zones (or a zone whose hemisphere contradicts the
    rows' latitude), no UTM position at all, an explicitly requested
    calibration prior that cannot apply, an EXIF focal length that differs
    between images of one camera or is missing (without a focal
    override), files in ``raw_images`` that this
    intake did not plan or that differ from their source. Warns, and records in the manifest, on: a static
    fix, empty depth, images without an orientation prior or a position,
    ``time_err_ms`` above the threshold, unmatched rows or images, and a
    calibration that falls back to groups.
    """
    log = log or logger
    options = options or IntakeOptions()
    registry = registry or camera_registry.REGISTRY
    problems = options.problems()
    if problems:
        raise IntakeError('; '.join(problems))
    variant = str(options.variant).strip().lower()

    run_dirs = find_run_directories(run_paths)
    check_workspace(workspace, run_dirs)
    workspace = os.path.abspath(workspace)
    raw_dir = os.path.join(workspace, RAW_IMAGES)

    warnings: list[str] = []
    notices: list[str] = []

    def warn(message: str) -> None:
        warnings.append(message)
        log.warning('%s', message)

    plans = [plan_run(run, variant, registry) for run in run_dirs]
    frames = [frame for plan in plans for frame in plan.frames]

    # ---- matching gates
    matched = len(frames)
    unmatched_rows = sum(len(n.unmatched_rows) for p in plans for n in p.nodes)
    unmatched_images = sum(len(n.unmatched_images)
                           for p in plans for n in p.nodes)
    known = matched + unmatched_rows + unmatched_images
    rate = 100.0 * matched / known if known else 0.0
    if matched == 0:
        raise IntakeError(
            f'no {variant} image matched a flight-log row in '
            f'{", ".join(run_dirs)} ({unmatched_rows} row(s) without an '
            f'image, {unmatched_images} image(s) without a row). Check the '
            'image variant and that the images were copied off the cards.')
    if rate < options.min_match_pct:
        raise IntakeError(
            f'only {matched} of {known} frames ({rate:.1f}%) have both a '
            f'flight-log row and a {variant} image, below the '
            f'{options.min_match_pct:g}% floor ({unmatched_rows} row(s) '
            f'without an image, {unmatched_images} image(s) without a row). '
            'Lower the minimum match rate to accept this deliberately.')
    for plan in plans:
        for node in plan.nodes:
            if node.unmatched_rows:
                warn(f'{plan.run_id}/{node.node}: {len(node.unmatched_rows)} '
                     f'flight-log row(s) have no {variant} image: '
                     + _examples(node.unmatched_rows))
            if node.unmatched_images:
                warn(f'{plan.run_id}/{node.node}: '
                     f'{len(node.unmatched_images)} {variant} image(s) are '
                     'named by no flight-log row and are not taken in: '
                     + _examples(node.unmatched_images))

    # ---- names and zones
    by_name: dict[str, list[Frame]] = {}
    for frame in frames:
        by_name.setdefault(frame.name.lower(), []).append(frame)
    duplicates = [group for group in by_name.values() if len(group) > 1]
    if duplicates:
        raise IntakeError(
            'image names must be unique across the runs taken in together; '
            + '; '.join(f'{g[0].name} in ' + ', '.join(f.run_dir for f in g)
                        for g in duplicates[:3]))
    zones = sorted({frame.row.utm_zone for frame in frames
                    if _position(frame.row) is not None})
    if len(zones) > 1:
        raise IntakeError(
            'the matched rows lie in more than one UTM zone ('
            + ', '.join(f'{z}{b}' for z, b in zones)
            + '); take in runs of one zone together')
    if not zones:
        raise IntakeError(
            'no matched flight-log row carries a UTM position (xutm, yutm, '
            'utm_zone); the flight log needs a zone to be imported')
    zone, band = zones[0]
    band_mismatch = []
    for frame in frames:
        lat, lon = frame.row.number('lat'), frame.row.number('long')
        if lat is None or lon is None or _position(frame.row) is None:
            continue
        expected = mgrs_band(lat)
        if expected is not None and _south(expected) != _south(band):
            raise IntakeError(
                f'{frame.row.path}: line {frame.row.line}: utm_zone '
                f'{zone}{band} is in the other hemisphere than latitude '
                f'{lat:g}; a trajectory imported in the wrong hemisphere is '
                'placed in the wrong part of the world')
        if expected != band or utm_zone_number(lon) != zone:
            band_mismatch.append(frame.name)
    if band_mismatch:
        warn(f'{len(band_mismatch)} row(s) give lat/long outside UTM zone '
             f'{zone}{band} (e.g. {band_mismatch[0]}); xutm/yutm are used as '
             'given')

    # ---- calibration, from the original images
    cameras_in_use = [key for key in registry.cameras
                      if any(f.camera == key for f in frames)]
    decisions = {}
    for key in cameras_in_use:
        camera = registry.cameras[key]
        geometries = []
        unreadable = []
        for frame in frames:
            if frame.camera != key:
                continue
            try:
                geometries.append(read_image_geometry(frame.source))
            except (OSError, ValueError, SyntaxError) as exc:
                # Pillow: OSError (incl. UnidentifiedImageError) for an
                # unreadable file, ValueError/SyntaxError for a corrupt one.
                unreadable.append(f'{frame.name} ({exc})')
        if unreadable:
            warn(f'{key}: {len(unreadable)} image(s) could not be read for '
                 'the calibration decision: ' + _examples(unreadable))
        try:
            observed = observe_focal(camera, geometries,
                                     options.focal_override_mm)
            decision = decide_calibration(
                camera, registry.rig_for(key), geometries,
                options.calibration, options.focal_asserted,
                options.focal_override_mm)
        except (CalibrationRefused, FocalRefused) as exc:
            raise IntakeError(str(exc)) from None
        decisions[key] = _calibration_record(camera, decision, observed)
        for message in decision.warnings:
            warn(message)

    # ---- priors
    static_runs = {plan.run_dir: plan.static_fix() for plan in plans}
    priors = [flight_log_prior(frame, options, static_runs[frame.run_dir])
              for frame in frames]
    for plan in plans:
        if static_runs[plan.run_dir]:
            spread = plan.position_spread_m()
            warn(f'{plan.run_id}: every row carries the same position '
                 f'(spread {spread:.3f} m over {len(plan.positions)} rows): '
                 'a static fix, not a track. Position accuracy '
                 f'{options.static_position_accuracy_m:g} m is written for '
                 f'its images instead of {options.position_accuracy_m:g} m, '
                 'so the fix cannot constrain the solve.')
    total = len(priors)
    no_depth = sum(1 for p in priors if not p.has_depth)
    if no_depth:
        warn(f'{no_depth} of {total} images have no depth: altitude written '
             f'as the surface altitude {options.surface_altitude_m:g} m '
             '(camera at the sea surface); altitude accuracy '
             f'{options.surface_altitude_accuracy_m:g} m is written for them '
             f'instead of {options.altitude_accuracy_m:g} m, so the fallback '
             'cannot pin the cameras to the surface')
    no_orientation = sum(1 for p in priors if not p.has_orientation)
    if no_orientation:
        warn(f'{no_orientation} of {total} images lack heading, pitch or '
             'roll: no orientation prior is written for them')
    no_position = sum(1 for p in priors if not p.has_position)
    if no_position:
        warn(f'{no_position} of {total} images carry no UTM position: '
             'written without a position prior (Batch Directory zones only '
             'images with a position)')
    lever_skipped = sum(1 for p in priors if p.lever_arm_skipped)
    if lever_skipped:
        warn(f'{lever_skipped} image(s) have no heading, so the horizontal '
             'lever arm could not be applied to their position')
    late = [f.row.number('time_err_ms') for f in frames
            if (f.row.number('time_err_ms') or 0.0) > options.time_err_warn_ms]
    if late:
        warn(f'{len(late)} image(s) carry time_err_ms above '
             f'{options.time_err_warn_ms:g} ms (largest {max(late):g} ms)')
    hidden_files = sum(n.hidden_files for p in plans for n in p.nodes)
    if hidden_files:
        notices.append(f'{hidden_files} hidden file(s) (names starting with '
                       '".", such as macOS "._" AppleDouble files) in the '
                       'camera folders were ignored')
        log.info('%s', notices[-1])
    unmeasured = [key for key in cameras_in_use
                  if any(f.camera == key and f.mount.lever_arm_m is None
                         for f in frames)]
    if unmeasured:
        notices.append(f'lever arm not measured for {", ".join(unmeasured)}: '
                       'zero offset used')
        log.info('%s', notices[-1])

    # ---- workspace
    log_name = flight_log_name(zone, band)
    log_path = os.path.join(raw_dir, log_name)
    manifest_path = os.path.join(raw_dir, MANIFEST_NAME)
    destinations = {frame.name: os.path.join(raw_dir, frame.camera, frame.name)
                    for frame in frames}
    planned = {os.path.normcase(os.path.abspath(p)) for p in
               (*destinations.values(), log_path, manifest_path)}
    sidecar_cameras = {
        os.path.normcase(os.path.abspath(sidecar_path(destinations[f.name]))):
            registry.cameras[f.camera] for f in frames}
    if os.path.isdir(raw_dir):
        extras = _unexpected_files(raw_dir, planned, sidecar_cameras)
        if extras:
            raise IntakeError(
                f'{raw_dir} holds {len(extras)} file(s) this intake did not '
                f'plan ({_examples(extras)}); use a fresh workspace')
    pending = []
    for frame in frames:
        if os.path.lexists(destinations[frame.name]):
            _check_existing(frame.source, destinations[frame.name])
        else:
            pending.append(frame)

    os.makedirs(raw_dir, exist_ok=True)
    # Never leave a manifest for other images, and never leave none: until
    # the final manifest replaces it, this marker tells every later stage
    # (load_manifest) that raw_images holds an unfinished intake.
    _write_json(manifest_path, {
        'schema': MANIFEST_SCHEMA,
        'status': STATUS_IN_PROGRESS,
        'started_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'sources': [plan.run_dir for plan in plans],
    })
    for frame in pending:
        _copy_image(frame.source, destinations[frame.name])
    copied, reused = len(pending), len(frames) - len(pending)
    rows_written = 0

    def write_log(temp):
        nonlocal rows_written
        rows_written = write_flight_log(temp, [p.row for p in priors])
    _write_text_atomically(log_path, write_log)

    heading_counts = Counter(p.heading_column or 'none' for p in priors)
    manifest = {
        'schema': MANIFEST_SCHEMA,
        'status': 'complete',
        'written_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'variant': variant,
        'sources': [{
            'run_dir': plan.run_dir,
            'run_id': plan.run_id,
            'static_fix': static_runs[plan.run_dir],
            'position_spread_m': plan.position_spread_m(),
            'nodes': [node.as_dict() for node in plan.nodes],
        } for plan in plans],
        'flight_log': log_name,
        'flight_log_rows': rows_written,
        'utm_zone': f'{zone}{band}',
        'cameras': {key: {
            'images': sum(1 for f in frames if f.camera == key),
            'folder': key,
        } for key in cameras_in_use},
        'images': {'matched': matched, 'copied': copied, 'reused': reused,
                   'unmatched_rows': unmatched_rows,
                   'unmatched_images': unmatched_images,
                   'match_rate_pct': round(rate, 3),
                   'min_match_rate_pct': options.min_match_pct},
        'static_fix': any(static_runs.values()),
        'position': {
            'accuracy_m': options.position_accuracy_m,
            'static_fix_accuracy_m': options.static_position_accuracy_m,
            'static_fix_spread_m': STATIC_FIX_SPREAD_M,
            'images_without_position': no_position,
        },
        'altitude': {
            'accuracy_m': options.altitude_accuracy_m,
            'surface_altitude_m': options.surface_altitude_m,
            'images_from_depth': total - no_depth,
            'images_at_surface_altitude': no_depth,
            'surface_altitude_accuracy_m': options.surface_altitude_accuracy_m,
            'images_with_surface_altitude_accuracy': no_depth,
        },
        'orientation': {
            'heading_source': options.heading_source,
            'heading_columns_used': dict(sorted(heading_counts.items())),
            'declination_deg': options.declination_deg,
            'yaw_roll_accuracy_deg': options.orientation_accuracy_deg,
            'pitch_accuracy_deg': {
                key: next(f.mount.pitch_accuracy_deg for f in frames
                          if f.camera == key) for key in cameras_in_use},
            'images_without_orientation': no_orientation,
            'convention': ('yaw = heading + declination + mount yaw offset; '
                           'pitch = 90 + IMU pitch - mount down tilt '
                           '(0 = nadir); roll = IMU roll. Not validated for '
                           'the Wild Sync IMU axes.'),
        },
        'time': {
            'time_err_warn_ms': options.time_err_warn_ms,
            'images_above': len(late),
            'capture_source': dict(sorted(Counter(
                f.row.capture_source or 'none' for f in frames).items())),
            'time_source': dict(sorted(Counter(
                f.row.time_source or 'none' for f in frames).items())),
        },
        'calibration': {key: decisions[key] for key in cameras_in_use},
        'notices': notices,
        'warnings': warnings,
    }
    _write_json(manifest_path, manifest)
    log.info('Wild Sync intake: %d image(s) from %d run(s) (%d copied, %d '
             'already present), flight log %s with %d row(s)', matched,
             len(plans), copied, reused, log_name, rows_written)
    return IntakeResult(manifest, manifest_path, log_path)


# ------------------------------------------------------------- consumers

def load_manifest(raw_images_dir: str) -> dict | None:
    """The intake manifest in ``raw_images_dir``, or None when there is none.

    ValueError when it exists but is not a complete manifest of this
    schema: a later stage must never act on a partial intake. That includes
    the in-progress marker :func:`run_intake` writes before copying, which
    stays when the intake is interrupted.
    """
    path = os.path.join(raw_images_dir, MANIFEST_NAME)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding='utf-8') as stream:
            data = json.load(stream)
    except (OSError, ValueError) as exc:
        raise ValueError(f'{path}: unreadable intake manifest: {exc}') from exc
    if isinstance(data, dict) and data.get('status') == STATUS_IN_PROGRESS:
        raise ValueError(
            f'{path}: not a complete schema-{MANIFEST_SCHEMA} Wild Sync intake '
            'manifest - the intake into this workspace did not finish '
            f'(started {data.get("started_utc", "at an unknown time")}); '
            'raw_images may hold only part of the images and no final flight '
            'log. Re-run Wild Sync Intake on the same workspace')
    if isinstance(data, dict) and data.get('schema') == 1:
        raise ValueError(
            f'{path}: a schema-1 Wild Sync intake manifest, written before '
            'the intake recorded the focal length observed on each camera; '
            f'this version reads schema {MANIFEST_SCHEMA} only, so '
            'RealityScan would get no starting focal. Re-run Wild Sync '
            'Intake on the same workspace (the copied images are reused)')
    if not isinstance(data, dict) or data.get('schema') != MANIFEST_SCHEMA \
            or data.get('status') != 'complete':
        raise ValueError(f'{path}: not a complete schema-{MANIFEST_SCHEMA} '
                         'Wild Sync intake manifest')
    return data


def calibration_modes(manifest: Mapping) -> dict[str, str]:
    """{camera key: decided calibration mode} from an intake manifest."""
    out: dict[str, str] = {}
    for key, decision in (manifest.get('calibration') or {}).items():
        mode = decision.get('mode') if isinstance(decision, Mapping) else None
        if key not in camera_registry.CAMERAS or mode not in DECIDED_MODES:
            raise ValueError(f'intake manifest: invalid calibration entry '
                             f'{key!r}: {decision!r}')
        out[key] = mode
    return out


def starting_focals(manifest: Mapping) -> dict[str, float]:
    """{camera key: starting 35 mm-equivalent focal} from an intake
    manifest - the focal a ``groups`` sidecar gives RealityScan.

    ValueError when a camera's entry has no positive finite
    ``starting_focal_35mm``: no camera may reach RealityScan without one.
    """
    out: dict[str, float] = {}
    for key, decision in (manifest.get('calibration') or {}).items():
        focal = (decision.get('starting_focal_35mm')
                 if isinstance(decision, Mapping) else None)
        if key not in camera_registry.CAMERAS or isinstance(focal, bool) \
                or not isinstance(focal, (int, float)) \
                or not (math.isfinite(focal) and focal > 0):
            raise ValueError(f'intake manifest: camera {key!r} has no usable '
                             f'starting focal (starting_focal_35mm '
                             f'{focal!r}); re-run Wild Sync Intake')
        out[key] = float(focal)
    return out
