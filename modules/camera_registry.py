"""Camera registry, loaded from cameras.json: the two-camera Sony ILX-LR1
rig captured with Wild Sync, and the Voyis Discovery Stereo.

``modules/cameras.json`` is the single source for:

- the physical cameras (``ilx_left``, ``ilx_right``, ``voyis_left``,
  ``voyis_right``) and their calibration: calibration image size, the focal
  length the calibration was solved at (unknown for the Voyis), the 35
  mm-equivalent focal, the normalised principal point, the distortion model
  and its coefficients, the sensor size (unknown for the Voyis), and one
  calibration group and one lens-distortion group per camera;
- the filename families (Wild Sync ``Cam1_...`` and ``Cam2_...``, which say
  which node is which eye and carry the mount of each; Voyis
  ``image_left_...`` and ``image_right_...``, which carry no mount);
- the stereo rigs, including ``node_assignment_confirmed``;
- the default flight-log prior accuracies, held by exactly one rig.

The file is loaded and validated when this module is imported. A missing or
mistyped field, a family naming an unknown camera, or a camera outside every
rig raises :class:`RegistryError` naming the entry, so no stage runs on a
half-read registry. Which calibration RealityScan actually receives is
decided in ``modules/calibration_sidecars.py``.

Standard library only, so tests and tools can import it without the
pipeline's other dependencies.
"""
from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

SCHEMA_VERSION = 2

CAMERAS_JSON = os.path.join(
    os.path.dirname(os.path.realpath(__file__)), 'cameras.json')

# The pipeline aligns with RealityScan's global Division model (owner,
# 2026-10-07). sfmDistortionModel is global and all-or-nothing for an
# adjustable calibration (verified on an earlier dataset), so an adjustable
# camera may only declare Division; its measured calibration
# (opencv_distortion_k1_k2_p1_p2_k3) stays Brown. 'perspective' (no lens
# distortion) is a pinhole camera with a LOCKED calibration: a locked
# calibration overrides the global model (verified 2026-10-07 on the Voyis).
DISTORTION_MODELS = frozenset({'division', 'perspective'})

# 'approximate' = a starting value that alignment adjusts (the ILX-LR1: the
# conversion of its measured calibration to RealityScan's conventions is
# not verified). 'exact' = locked, and only with the perspective model and
# zero coefficients (the Voyis: rectified images and the manufacturer's
# calibration).
CALIBRATION_PRIORS = frozenset({'approximate', 'exact'})
FIXED_MODEL, FIXED_PRIOR = 'perspective', 'exact'

_KEY_RE = re.compile(r'^[a-z0-9_]+$')
_GROUP_RE = re.compile(r'^\d+$')


class RegistryError(ValueError):
    """cameras.json is malformed or inconsistent."""


@dataclass(frozen=True)
class Camera:
    """One physical camera and the calibration it may be given."""
    key: str                                  # also the batch subfolder name
    calibration_group: str                    # one per physical camera
    lens_distortion_group: str                # one per physical camera
    calibration_prior: str                    # 'approximate' | 'exact'
    distortion_model: str                     # 'division' | 'perspective'
    calibration_image_size: tuple[int, int]   # (width, height) in pixels
    calibration_focal_mm: float | None        # lens focal at calibration;
                                              # None for a locked pinhole camera
    sensor_size_mm: tuple[float, float] | None  # (width, height); None likewise
    focal_length_35mm: float                  # RealityScan normalised focal
    principal_point_u: float                  # RealityScan normalised offsets
    principal_point_v: float
    opencv_distortion: tuple[float, float, float, float, float]  # k1 k2 p1 p2 k3

    @property
    def calibration_aspect(self) -> float:
        width, height = self.calibration_image_size
        return width / height

    @property
    def fixed_calibration(self) -> bool:
        """True for a camera whose calibration is delivered locked on every
        image (a pinhole camera: perspective model, exact prior)."""
        return self.calibration_prior == FIXED_PRIOR


@dataclass(frozen=True)
class Mount:
    """How a camera sits on the vehicle.

    ``down_tilt_deg``: tilt below the vehicle's forward axis (90 = looking
    straight down). ``yaw_offset_deg``: direction of the image top relative
    to the heading. ``pitch_accuracy_deg``: accuracy claimed for the pitch
    prior. ``lever_arm_m``: (forward, right, down) offset in metres from the
    navigation reference, or None when it has not been measured - callers
    then use a zero offset and log a notice, never an invented value.
    """
    down_tilt_deg: float
    pitch_accuracy_deg: float
    yaw_offset_deg: float = 0.0
    lever_arm_m: tuple[float, float, float] | None = None


@dataclass(frozen=True)
class Family:
    """A filename family: which camera a file belongs to, and its mount
    (None for a family Wild Sync Intake never reads, such as the Voyis)."""
    name: str
    pattern: str
    camera: str
    mount: Mount | None


@dataclass(frozen=True)
class Rig:
    """A set of cameras with one node-to-eye assignment switch.

    While ``node_assignment_confirmed`` is False, calibration that depends on
    the eye (principal point, distortion) is never applied.
    """
    key: str
    eyes: tuple[tuple[str, str], ...]         # ((eye, camera key), ...)
    node_assignment_confirmed: bool
    prior_accuracy: 'PriorAccuracy | None'    # on exactly one rig

    @property
    def cameras(self) -> tuple[str, ...]:
        return tuple(camera for _eye, camera in self.eyes)


@dataclass(frozen=True)
class PriorAccuracy:
    """Flight-log prior accuracies of a rig (metres, degrees): X and Y
    share ``position_m`` (a real track only; a static fix gets no position
    and the 100 m no-position accuracy of modules/flight_logs.py),
    ``altitude_m`` applies when a depth is recorded, and yaw, pitch and roll
    share ``orientation_deg``."""
    position_m: float
    altitude_m: float
    orientation_deg: float


@dataclass(frozen=True)
class Registry:
    cameras: Mapping[str, Camera]
    families: tuple[Family, ...]
    rigs: Mapping[str, Rig]
    prior_accuracy: PriorAccuracy
    _matchers: tuple[tuple[re.Pattern[str], Family], ...] = field(
        default=(), repr=False, compare=False)

    def family(self, filename: str) -> Family | None:
        """The first family whose pattern matches the file name, or None.

        Only the base name is matched, case-insensitively, so every file of
        a camera (review JPEG, card JPEG, RAW, sidecar) resolves alike.
        """
        name = os.path.basename(filename)
        for pattern, fam in self._matchers:
            if pattern.search(name):
                return fam
        return None

    def identify(self, filename: str) -> Camera | None:
        fam = self.family(filename)
        return None if fam is None else self.cameras[fam.camera]

    def rig_for(self, camera_key: str) -> Rig:
        for rig in self.rigs.values():
            if camera_key in rig.cameras:
                return rig
        raise RegistryError(f'camera {camera_key!r} belongs to no rig')


# --------------------------------------------------------------- loading

def _is_number(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _entries(section) -> dict:
    """A JSON object without its '_' comment keys."""
    return {k: v for k, v in section.items() if not k.startswith('_')}


class _Loader:
    def __init__(self, source: str):
        self.source = source

    def fail(self, where: str, message: str):
        raise RegistryError(f'{self.source}: {where}: {message}')

    def obj(self, value, where: str) -> dict:
        if not isinstance(value, dict):
            self.fail(where, f'must be an object, got {type(value).__name__}')
        return value

    def require(self, spec: dict, key: str, where: str):
        if key not in spec:
            self.fail(where, f'required field {key!r} is missing')
        return spec[key]

    def number(self, spec: dict, key: str, where: str, *,
               positive: bool = False, default=None) -> float:
        if default is not None and key not in spec:
            return float(default)
        value = self.require(spec, key, where)
        if not _is_number(value) or (positive and value <= 0):
            kind = 'a positive number' if positive else 'a finite number'
            self.fail(f'{where}.{key}', f'must be {kind}, got {value!r}')
        return float(value)

    def text(self, spec: dict, key: str, where: str) -> str:
        value = self.require(spec, key, where)
        if not isinstance(value, str) or not value:
            self.fail(f'{where}.{key}', f'must be a non-empty string, got {value!r}')
        return value

    def choice(self, spec: dict, key: str, where: str, allowed) -> str:
        value = self.text(spec, key, where)
        if value not in allowed:
            self.fail(f'{where}.{key}',
                      f'must be one of {sorted(allowed)}, got {value!r}')
        return value

    def group(self, spec: dict, key: str, where: str) -> str:
        value = self.text(spec, key, where)
        if not _GROUP_RE.match(value):
            self.fail(f'{where}.{key}',
                      f'must be a non-negative integer as a string, got {value!r}')
        return value

    def numbers(self, spec: dict, key: str, where: str, count: int) -> tuple:
        value = self.require(spec, key, where)
        if (not isinstance(value, list) or len(value) != count
                or not all(_is_number(v) for v in value)):
            self.fail(f'{where}.{key}',
                      f'must be a list of {count} finite numbers, got {value!r}')
        return tuple(float(v) for v in value)

    def sensor_size(self, spec: dict, where: str) -> tuple[float, float]:
        width, height = self.numbers(spec, 'sensor_size_mm', where, 2)
        if width <= 0 or height <= 0:
            self.fail(f'{where}.sensor_size_mm',
                      f'must be [width, height] in positive millimetres, got '
                      f'{spec["sensor_size_mm"]!r}')
        return width, height

    def camera(self, key: str, spec) -> Camera:
        where = f'cameras[{key!r}]'
        if not _KEY_RE.match(key):
            self.fail(where, 'camera keys are lower-case letters, digits and _')
        spec = self.obj(spec, where)
        size = self.require(spec, 'resolution', where)
        if (not isinstance(size, list) or len(size) != 2
                or not all(isinstance(v, int) and not isinstance(v, bool)
                           and v > 0 for v in size)):
            self.fail(f'{where}.resolution',
                      f'must be [width, height] in whole pixels, got {size!r}')
        model = self.choice(spec, 'distortion_model', where, DISTORTION_MODELS)
        coefficients = self.numbers(
            spec, 'opencv_distortion_k1_k2_p1_p2_k3', where, 5)
        if coefficients[2] or coefficients[3]:
            self.fail(f'{where}.opencv_distortion_k1_k2_p1_p2_k3',
                      'the prior sidecar writes the measured calibration as '
                      'Brown3, which has no tangential terms, but p1/p2 are '
                      'non-zero')
        prior = self.choice(spec, 'calibration_prior', where, CALIBRATION_PRIORS)
        lens_prior = self.choice(spec, 'lens_distortion_prior', where,
                                 CALIBRATION_PRIORS)
        fixed = model == FIXED_MODEL
        for field_name, value in (('calibration_prior', prior),
                                  ('lens_distortion_prior', lens_prior)):
            if fixed != (value == FIXED_PRIOR):
                self.fail(f'{where}.{field_name}',
                          f'{FIXED_PRIOR!r} goes with distortion_model '
                          f'{FIXED_MODEL!r} and only with it, got {value!r} '
                          f'with {model!r}')
        if fixed and any(coefficients):
            self.fail(f'{where}.opencv_distortion_k1_k2_p1_p2_k3',
                      f'a {FIXED_MODEL} camera has no lens distortion, so its '
                      f'coefficients must all be 0, got {list(coefficients)}')
        unknown_ok = fixed
        return Camera(
            key=key,
            calibration_group=self.group(spec, 'calibration_group', where),
            lens_distortion_group=self.group(spec, 'lens_distortion_group', where),
            calibration_prior=prior,
            distortion_model=model,
            calibration_image_size=(size[0], size[1]),
            calibration_focal_mm=(
                None if unknown_ok and 'calibration_focal_mm' not in spec
                else self.number(spec, 'calibration_focal_mm', where,
                                 positive=True)),
            sensor_size_mm=(
                None if unknown_ok and 'sensor_size_mm' not in spec
                else self.sensor_size(spec, where)),
            focal_length_35mm=self.number(spec, 'focal_length_35mm', where,
                                          positive=True),
            principal_point_u=self.number(spec, 'principal_point_u', where),
            principal_point_v=self.number(spec, 'principal_point_v', where),
            opencv_distortion=coefficients,
        )

    def mount(self, spec, where: str) -> Mount:
        spec = self.obj(spec, where)
        lever = spec.get('lever_arm_m')
        if lever is not None:
            lever = self.numbers(spec, 'lever_arm_m', where, 3)
        return Mount(
            down_tilt_deg=self.number(spec, 'down_tilt_deg', where),
            pitch_accuracy_deg=self.number(spec, 'pitch_accuracy_deg', where,
                                           positive=True),
            yaw_offset_deg=self.number(spec, 'yaw_offset_deg', where, default=0.0),
            lever_arm_m=lever,
        )

    def family(self, index: int, spec, cameras: Mapping[str, Camera]) -> Family:
        where = f'families[{index}]'
        spec = self.obj(spec, where)
        name = self.text(spec, 'family', where)
        where = f'families[{name!r}]'
        pattern = self.text(spec, 'pattern', where)
        try:
            re.compile(pattern, re.IGNORECASE)
        except re.error as exc:
            self.fail(f'{where}.pattern', f'is not a valid regular expression: {exc}')
        camera = self.text(spec, 'camera', where)
        if camera not in cameras:
            self.fail(f'{where}.camera', f'names camera {camera!r}, which is not '
                      f'in cameras ({sorted(cameras)})')
        # A camera with a fixed calibration (the Voyis) never passes through
        # Wild Sync Intake and has no mount; every other family needs one.
        mount = spec.get('mount')
        if mount is None and not cameras[camera].fixed_calibration:
            self.fail(where, "required field 'mount' is missing")
        return Family(name, pattern, camera,
                      None if mount is None
                      else self.mount(mount, f'{where}.mount'))

    def rig(self, key: str, spec, cameras: Mapping[str, Camera]) -> Rig:
        where = f'rigs[{key!r}]'
        spec = self.obj(spec, where)
        confirmed = self.require(spec, 'node_assignment_confirmed', where)
        if not isinstance(confirmed, bool):
            self.fail(f'{where}.node_assignment_confirmed',
                      f'must be true or false, got {confirmed!r}')
        eyes = _entries(self.obj(self.require(spec, 'eyes', where), f'{where}.eyes'))
        if not eyes:
            self.fail(f'{where}.eyes', 'names no cameras')
        pairs = []
        for eye, entry in sorted(eyes.items()):
            camera = self.text(self.obj(entry, f'{where}.eyes[{eye!r}]'), 'camera',
                               f'{where}.eyes[{eye!r}]')
            if camera not in cameras:
                self.fail(f'{where}.eyes[{eye!r}].camera',
                          f'names camera {camera!r}, which is not in cameras')
            pairs.append((eye, camera))
        accuracy = spec.get('prior_accuracy')
        if accuracy is not None:
            accuracy = self.accuracy(accuracy, f'{where}.prior_accuracy')
        return Rig(key, tuple(pairs), confirmed, accuracy)

    def accuracy(self, spec, where: str) -> PriorAccuracy:
        spec = self.obj(spec, where)
        return PriorAccuracy(
            position_m=self.number(spec, 'position_m', where, positive=True),
            altitude_m=self.number(spec, 'altitude_m', where, positive=True),
            orientation_deg=self.number(spec, 'orientation_deg', where,
                                        positive=True),
        )

    def load(self, data) -> Registry:
        data = self.obj(data, 'top level')
        version = data.get('schema_version')
        if version != SCHEMA_VERSION:
            self.fail('schema_version', f'must be {SCHEMA_VERSION}, got {version!r}')

        cameras_spec = _entries(self.obj(self.require(data, 'cameras', 'top level'),
                                         'cameras'))
        if not cameras_spec:
            self.fail('cameras', 'no cameras are defined')
        cameras = {key: self.camera(key, spec) for key, spec in cameras_spec.items()}
        for attr in ('calibration_group', 'lens_distortion_group'):
            seen: dict[str, str] = {}
            for camera in cameras.values():
                value = getattr(camera, attr)
                if value in seen:
                    self.fail(f'cameras[{camera.key!r}].{attr}',
                              f'{value!r} is also used by {seen[value]!r}; each '
                              'physical camera needs its own group')
                seen[value] = camera.key

        families_spec = self.require(data, 'families', 'top level')
        if not isinstance(families_spec, list) or not families_spec:
            self.fail('families', 'must be a non-empty list')
        families = tuple(self.family(i, spec, cameras)
                         for i, spec in enumerate(families_spec))
        names = [f.name for f in families]
        for name in names:
            if names.count(name) > 1:
                self.fail(f'families[{name!r}]', 'is defined more than once')

        rigs_spec = _entries(self.obj(self.require(data, 'rigs', 'top level'), 'rigs'))
        rigs = {key: self.rig(key, spec, cameras) for key, spec in rigs_spec.items()}
        for camera in sorted({f.camera for f in families}):
            owners = [rig.key for rig in rigs.values() if camera in rig.cameras]
            if len(owners) != 1:
                self.fail(f'cameras[{camera!r}]',
                          f'must belong to exactly one rig, found {owners or "none"}')

        carriers = [rig for rig in rigs.values() if rig.prior_accuracy is not None]
        if len(carriers) != 1:
            self.fail('rigs', 'exactly one rig carries the flight-log prior '
                      'accuracies (prior_accuracy); found '
                      f'{[rig.key for rig in carriers] or "none"}')
        accuracy = carriers[0].prior_accuracy
        matchers = tuple((re.compile(f.pattern, re.IGNORECASE), f) for f in families)
        return Registry(MappingProxyType(cameras), families,
                        MappingProxyType(rigs), accuracy, matchers)


def load_registry(path: str = CAMERAS_JSON) -> Registry:
    """Load and validate a cameras.json; RegistryError when it is unusable."""
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except OSError as exc:
        raise RegistryError(f'{path}: cannot be read: {exc}') from exc
    except ValueError as exc:
        raise RegistryError(f'{path}: is not valid JSON: {exc}') from exc
    return _Loader(path).load(data)


REGISTRY: Registry = load_registry()

CAMERAS: Mapping[str, Camera] = REGISTRY.cameras
FAMILIES: Mapping[str, Family] = MappingProxyType(
    {f.name: f for f in REGISTRY.families})
FAMILY_CAMERA: Mapping[str, str] = MappingProxyType(
    {f.name: f.camera for f in REGISTRY.families})
RIGS: Mapping[str, Rig] = REGISTRY.rigs
PRIOR_ACCURACY: PriorAccuracy = REGISTRY.prior_accuracy


def family(filename: str) -> str | None:
    """Name of the filename family a file belongs to ('cam1', 'cam2'), or None."""
    fam = REGISTRY.family(filename)
    return None if fam is None else fam.name


def identify(filename: str) -> Camera | None:
    """Physical camera a file belongs to, or None when it is not recognised."""
    return REGISTRY.identify(filename)


def mount_for(filename: str) -> Mount | None:
    """Mount of the camera a file belongs to, or None when not recognised."""
    fam = REGISTRY.family(filename)
    return None if fam is None else fam.mount


def rig_for(camera_key: str) -> Rig:
    """The rig a camera belongs to; RegistryError for an unknown camera."""
    return REGISTRY.rig_for(camera_key)
