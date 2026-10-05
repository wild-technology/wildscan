"""Camera registry for the two-camera Sony ILX-LR1 rig, loaded from cameras.json.

``modules/cameras.json`` is the single source for:

- the two physical cameras (``ilx_left``, ``ilx_right``) and their
  calibration: calibration image size, the focal length the calibration was
  solved at, the 35 mm-equivalent focal, the normalised principal point, the
  distortion model and its measured coefficients, and one calibration group
  and one lens-distortion group per camera;
- the Wild Sync filename families (``Cam1_...`` and ``Cam2_...``), which say
  which node is which eye, and the mount of each;
- the stereo rig, including ``node_assignment_confirmed``;
- the default flight-log prior accuracies.

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
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

SCHEMA_VERSION = 2

CAMERAS_JSON = os.path.join(
    os.path.dirname(os.path.realpath(__file__)), 'cameras.json')

# The pipeline aligns with RealityScan's global Brown3 model
# (sfmDistortionModel is global and all-or-nothing, docs/rs-reference/13
# section 5.4), so a camera may only declare that model.
DISTORTION_MODELS = frozenset({'brown3'})

# 'approximate' = a starting value that alignment adjusts. The calibration
# is never declared fixed: its conversion to RealityScan's coefficient and
# principal-point conventions is not verified (see calibration_sidecars).
CALIBRATION_PRIORS = frozenset({'approximate'})

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
    calibration_prior: str                    # 'approximate'
    distortion_model: str                     # 'brown3'
    calibration_image_size: tuple[int, int]   # (width, height) in pixels
    calibration_focal_mm: float               # lens focal length at calibration
    focal_length_35mm: float                  # RealityScan normalised focal
    principal_point_u: float                  # RealityScan normalised offsets
    principal_point_v: float
    opencv_distortion: tuple[float, float, float, float, float]  # k1 k2 p1 p2 k3

    @property
    def calibration_aspect(self) -> float:
        width, height = self.calibration_image_size
        return width / height


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
    """A filename family: which camera a file belongs to, and its mount."""
    name: str
    pattern: str
    camera: str
    mount: Mount


@dataclass(frozen=True)
class Rig:
    """A set of cameras with one node-to-eye assignment switch.

    While ``node_assignment_confirmed`` is False, calibration that depends on
    the eye (principal point, distortion) is never applied.
    """
    key: str
    eyes: tuple[tuple[str, str], ...]         # ((eye, camera key), ...)
    node_assignment_confirmed: bool

    @property
    def cameras(self) -> tuple[str, ...]:
        return tuple(camera for _eye, camera in self.eyes)


@dataclass(frozen=True)
class PriorAccuracy:
    """Default flight-log prior accuracies (metres, degrees)."""
    x_m: float
    y_m: float
    alt_m: float
    yaw_deg: float
    roll_deg: float


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
        if model == 'brown3' and (coefficients[2] or coefficients[3]):
            self.fail(f'{where}.opencv_distortion_k1_k2_p1_p2_k3',
                      'brown3 has no tangential terms, but p1/p2 are non-zero')
        self.choice(spec, 'lens_distortion_prior', where, CALIBRATION_PRIORS)
        return Camera(
            key=key,
            calibration_group=self.group(spec, 'calibration_group', where),
            lens_distortion_group=self.group(spec, 'lens_distortion_group', where),
            calibration_prior=self.choice(spec, 'calibration_prior', where,
                                          CALIBRATION_PRIORS),
            distortion_model=model,
            calibration_image_size=(size[0], size[1]),
            calibration_focal_mm=self.number(spec, 'calibration_focal_mm', where,
                                             positive=True),
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
        return Family(name, pattern, camera,
                      self.mount(self.require(spec, 'mount', where), f'{where}.mount'))

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
        return Rig(key, tuple(pairs), confirmed)

    def accuracy(self, defaults) -> PriorAccuracy:
        defaults = self.obj(defaults, 'defaults')
        pos = self.obj(self.require(defaults, 'position_accuracy_m', 'defaults'),
                       'defaults.position_accuracy_m')
        ori = self.obj(self.require(defaults, 'orientation_accuracy_deg', 'defaults'),
                       'defaults.orientation_accuracy_deg')
        return PriorAccuracy(
            x_m=self.number(pos, 'x', 'defaults.position_accuracy_m', positive=True),
            y_m=self.number(pos, 'y', 'defaults.position_accuracy_m', positive=True),
            alt_m=self.number(pos, 'alt', 'defaults.position_accuracy_m', positive=True),
            yaw_deg=self.number(ori, 'yaw', 'defaults.orientation_accuracy_deg',
                                positive=True),
            roll_deg=self.number(ori, 'roll', 'defaults.orientation_accuracy_deg',
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

        accuracy = self.accuracy(self.require(data, 'defaults', 'top level'))
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
