"""Calibration delivery to RealityScan for the ILX-LR1 cameras.

RealityScan takes per-image calibration only from XMP sidecars. The
delivery that has worked from the command line is an ``.rscmd`` command
file that adds every image with its sidecar named explicitly
(``-addImageWithCalibration "<image>" "<xmp>"``); when checked, the
``-setPriorCalibrationGroup`` / ``-setPriorLensGroup`` commands returned
success from the delegated command line without changing the groups.
Sidecars use the ``xcr`` 1.1 attribute form of the sample sidecar in the
RealityScan 2.2 CLI help and never carry a pose, so
nothing written here can become a pose prior on a later add.

For each camera one of three modes applies (:func:`decide_calibration`):

``prior``
    Focal length, principal point and the measured distortion
    coefficients, as an initial (adjustable) prior in the camera's own
    calibration and distortion group. Only when the rig's node assignment is
    confirmed, the images have the calibration's aspect ratio, and their lens
    focal length matches the calibration's.
``groups``
    The camera's own calibration and distortion group, the Brown3 model and
    the focal length observed on the original images (EXIF, or the
    operator's override) as an initial (adjustable) 35 mm-equivalent focal:
    RealityScan solves each physical camera's intrinsics separately,
    starting from that focal. The fallback whenever ``prior`` is not
    applicable.
``off``
    No sidecars; calibration sidecars this pipeline wrote for the camera in
    an earlier run are removed before alignment. RealityScan reads the focal
    length from the images' own EXIF, which preprocessing keeps.

Whatever the mode, RealityScan receives a starting focal length for every
image and refines it during alignment (:func:`observe_focal`).

The registry values follow the conversion in
:func:`intrinsics_to_xmp_values`. Two parts of it are not verified against
RealityScan, which is why the prior is never locked or exact: the radius
normalisation of the distortion coefficients (the Brown slot order itself is
documented by RealityScan's shipped export templates), and the normalisation
of the principal point for an image that is not square.

The hygiene functions (:func:`sanitize_and_census`,
:func:`ensure_calibration_sidecars`, :func:`remove_calibration_sidecars`)
take the decided modes and never write any other sidecar form; without modes
they create and remove nothing.
"""
from __future__ import annotations

import logging
import math
import os
import re
import statistics
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from . import camera_registry
from .camera_registry import Camera, Rig
from .image_exts import PROCESSABLE_IMAGE_EXTS

logger = logging.getLogger(__name__)

MODES = ('auto', 'prior', 'groups', 'off')      # what may be requested
DECIDED_MODES = ('prior', 'groups', 'off')      # what a decision can be
SIDECAR_MODES = ('prior', 'groups')             # modes that write a sidecar

# Relative difference allowed between the image and calibration aspect
# ratios (width / height).
ASPECT_TOLERANCE = 0.01

# Difference allowed between the EXIF lens focal length and the focal
# length the calibration was solved at, in millimetres.
FOCAL_TOLERANCE_MM = 0.5

# Difference allowed between the EXIF focal lengths of the images of one
# camera, in millimetres; a larger one means the zoom changed during the run.
FOCAL_SPREAD_TOLERANCE_MM = 0.5

# RealityScan's FocalLength35mm is the focal scaled to a 36 mm image width.
FILM_WIDTH_MM = 36.0

XCR_NAMESPACE = 'http://www.capturingreality.com/ns/xcr/1.1#'

# XMP token for each registry prior level. 'approximate' is RealityScan's
# "Approximate" (adjusted during alignment); its XMP token is inferred to be
# 'initial', the value RealityScan's own sample sidecar carries, and the
# documented token set is initial / exact / locked (RealityScan 2.2 CLI
# help, XMP export).
_PRIOR_TOKEN = {'approximate': 'initial'}


class CalibrationRefused(ValueError):
    """An explicitly requested prior cannot be applied safely."""


class FocalRefused(ValueError):
    """No single starting focal length can be given for a camera."""


# ------------------------------------------------------------- inputs

@dataclass(frozen=True)
class ImageGeometry:
    """Pixel size, EXIF lens focal length and EXIF 35 mm-equivalent focal
    length (mm, None if absent) of an image."""
    width: int
    height: int
    focal_mm: float | None = None
    focal_35mm_film: float | None = None


def _positive(value) -> float | None:
    """A positive finite float from an EXIF value, else None (EXIF writes 0
    for an unknown focal length)."""
    try:
        number = float(value) if value is not None else None
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    if number is None or not (math.isfinite(number) and number > 0):
        return None
    return number


def _exif_tag(exif, tag: int):
    value = exif.get_ifd(0x8769).get(tag)       # Exif IFD
    return exif.get(tag) if value is None else value


def read_image_geometry(path: str) -> ImageGeometry:
    """Size, EXIF ``FocalLength`` and EXIF ``FocalLengthIn35mmFilm`` of an
    image file, read with Pillow.

    Read from the original image, where the calibration decision is made.
    The size is the stored pixel size; the EXIF orientation tag is not
    applied.
    """
    from PIL import Image

    with Image.open(path) as image:
        width, height = image.size
        exif = image.getexif()
        focal = _exif_tag(exif, 0x920A)          # FocalLength
        focal_35mm = _exif_tag(exif, 0xA405)     # FocalLengthIn35mmFilm
    return ImageGeometry(int(width), int(height), _positive(focal),
                         _positive(focal_35mm))


# ----------------------------------------------------------- decision

@dataclass(frozen=True)
class CalibrationDecision:
    """The calibration mode for one camera, and what it was based on."""
    camera: str
    requested: str
    mode: str                                   # prior | groups | off
    reason: str
    warnings: tuple[str, ...]
    image_sizes: tuple[tuple[int, int], ...]    # distinct sizes seen
    exif_focals_mm: tuple[float | None, ...]    # distinct focals seen
    calibration_image_size: tuple[int, int]
    calibration_focal_mm: float
    node_assignment_confirmed: bool
    focal_asserted: bool

    def as_dict(self) -> dict:
        """JSON-ready form, for the intake manifest."""
        return {
            'camera': self.camera,
            'requested': self.requested,
            'mode': self.mode,
            'reason': self.reason,
            'warnings': list(self.warnings),
            'image_sizes': [list(size) for size in self.image_sizes],
            'exif_focals_mm': list(self.exif_focals_mm),
            'calibration_image_size': list(self.calibration_image_size),
            'calibration_focal_mm': self.calibration_focal_mm,
            'node_assignment_confirmed': self.node_assignment_confirmed,
            'focal_asserted': self.focal_asserted,
        }


def _size(size: tuple[int, int]) -> str:
    return f'{size[0]}x{size[1]}'


def _mm(value: float | None) -> str:
    return 'none' if value is None else f'{value:g} mm'


def decide_calibration(camera: Camera, rig: Rig,
                       geometries: Iterable[ImageGeometry],
                       requested: str = 'auto',
                       focal_asserted: bool = False,
                       focal_override_mm: float | None = None
                       ) -> CalibrationDecision:
    """Calibration mode for ``camera`` over a set of its images.

    ``prior`` needs all three conditions: the rig's node assignment is
    confirmed; every image's aspect ratio matches the calibration's within
    ASPECT_TOLERANCE; and every image's EXIF focal length matches the
    calibration focal within FOCAL_TOLERANCE_MM, or ``focal_asserted`` says
    the lens was at that focal length. ``focal_override_mm``, the
    operator's focal length for every image, is checked in place of the
    EXIF focal lengths (which the decision still records).

    ``requested``: ``auto`` gives ``prior`` when all conditions hold and
    ``groups`` otherwise, with a warning naming each failed condition and
    both values. ``groups`` and ``off`` are honoured as given. ``prior`` is
    honoured with a warning when only the focal check fails, and raises
    CalibrationRefused when the node assignment is unconfirmed or the aspect
    ratio differs (or no image size is known).
    """
    if requested not in MODES:
        raise ValueError(f'calibration mode must be one of {MODES}, '
                         f'got {requested!r}')
    geometries = list(geometries)
    sizes = tuple(sorted({(g.width, g.height) for g in geometries}))
    focals = tuple(sorted({g.focal_mm for g in geometries},
                          key=lambda f: (f is None, f or 0.0)))
    cal_size = camera.calibration_image_size
    cal_focal = camera.calibration_focal_mm

    def decision(mode: str, reason: str, warnings=()) -> CalibrationDecision:
        return CalibrationDecision(
            camera.key, requested, mode, reason, tuple(warnings), sizes, focals,
            cal_size, cal_focal, rig.node_assignment_confirmed, focal_asserted)

    if requested == 'off':
        return decision('off', 'calibration delivery was switched off')
    if requested == 'groups':
        return decision('groups', 'calibration groups only, as requested')

    failures: list[str] = []
    if not rig.node_assignment_confirmed:
        failures.append(
            f'the node assignment of rig {rig.key} is not confirmed '
            '(node_assignment_confirmed is false in modules/cameras.json)')
    if not sizes:
        failures.append('no image size is known')
    for size in sizes:
        if size[0] <= 0 or size[1] <= 0:
            failures.append(f'image size {_size(size)} is not a pixel size')
            continue
        if abs(size[0] / size[1] - camera.calibration_aspect) \
                > ASPECT_TOLERANCE * camera.calibration_aspect:
            failures.append(
                f'image {_size(size)} (aspect {size[0] / size[1]:.3f}) vs '
                f'calibration {_size(cal_size)} (aspect '
                f'{camera.calibration_aspect:.3f})')
    focal_failures: list[str] = []
    if not focal_asserted:
        checked, label = ((focals, 'EXIF focal') if focal_override_mm is None
                          else ((focal_override_mm,), 'focal override'))
        for focal in checked:
            if focal is None or abs(focal - cal_focal) > FOCAL_TOLERANCE_MM:
                focal_failures.append(
                    f'{label} {_mm(focal)} vs calibration {_mm(cal_focal)}')

    if requested == 'prior':
        if failures:
            raise CalibrationRefused(
                f'{camera.key}: the calibration prior cannot be applied: '
                + '; '.join(failures))
        warnings = [f'{camera.key}: prior applied as requested although '
                    + '; '.join(focal_failures)] if focal_failures else []
        return decision('prior', 'calibration prior, as requested', warnings)

    failures += focal_failures
    if failures:
        detail = '; '.join(failures)
        return decision(
            'groups', f'calibration not applicable: {detail}',
            [f'{camera.key}: calibration groups only - {detail}'])
    if focal_asserted:
        focal_note = 'focal length asserted'
    elif focal_override_mm is not None:
        focal_note = f'focal override matches {_mm(cal_focal)}'
    else:
        focal_note = f'EXIF focal matches {_mm(cal_focal)}'
    return decision(
        'prior',
        f'node assignment confirmed; image aspect matches calibration '
        f'{_size(cal_size)}; {focal_note}')


# ------------------------------------------------------- starting focal

@dataclass(frozen=True)
class ObservedFocal:
    """The focal length observed on one camera's original images, and the
    35 mm-equivalent focal a ``groups`` sidecar gives RealityScan to start
    from."""
    camera: str
    exif_focal_mm: float | None          # median EXIF FocalLength
    exif_focal_35mm: float | None        # its 35 mm-equivalent
    exif_focal_35mm_from: str | None     # how exif_focal_35mm was obtained
    images: int                          # images read
    images_without_exif_focal: int
    focal_override_mm: float | None
    starting_focal_35mm: float           # what a groups sidecar carries
    starting_focal_source: str           # 'exif' | 'override'

    def as_dict(self) -> dict:
        """JSON-ready form, for the intake manifest."""
        return {
            'exif_focal_mm': self.exif_focal_mm,
            'exif_focal_35mm': self.exif_focal_35mm,
            'exif_focal_35mm_from': self.exif_focal_35mm_from,
            'images_without_exif_focal': self.images_without_exif_focal,
            'focal_override_mm': self.focal_override_mm,
            'starting_focal_35mm': self.starting_focal_35mm,
            'starting_focal_source': self.starting_focal_source,
        }


def focal_35mm_from_sensor(camera: Camera, focal_mm: float) -> float:
    """35 mm-equivalent of a lens focal length on ``camera``'s sensor:
    ``focal x 36 / sensor width`` (the scale of RealityScan's
    FocalLength35mm)."""
    return focal_mm * FILM_WIDTH_MM / camera.sensor_size_mm[0]


def _check_spread(camera: Camera, values: list[float], what: str) -> None:
    low, high = min(values), max(values)
    if high - low > FOCAL_SPREAD_TOLERANCE_MM:
        raise FocalRefused(
            f'{camera.key}: the EXIF {what} differs between images of one '
            f'camera: {low:g} mm and {high:g} mm (more than '
            f'{FOCAL_SPREAD_TOLERANCE_MM:g} mm apart - was the zoom changed '
            'during the run?). Take the runs in separately, or pass the '
            'focal length of every image explicitly (--w_focal_override, mm)')


def observe_focal(camera: Camera, geometries: Iterable[ImageGeometry],
                  focal_override_mm: float | None = None) -> ObservedFocal:
    """The focal length of ``camera``'s original images and the starting
    35 mm-equivalent focal of its ``groups`` sidecar.

    The EXIF 35 mm-equivalent is ``FocalLengthIn35mmFilm`` when every image
    with a focal length carries it, else ``FocalLength x 36 / sensor
    width`` (the registry's ``sensor_size_mm``). ``focal_override_mm``
    replaces the EXIF focal length as the starting value (converted from
    the sensor width the same way).

    Without an override, FocalRefused when the EXIF focal lengths (or their
    35 mm-equivalents) of the images differ by more than
    FOCAL_SPREAD_TOLERANCE_MM, or when an image carries no EXIF focal
    length: RealityScan would then get no single starting focal.
    """
    if focal_override_mm is not None and (
            isinstance(focal_override_mm, bool)
            or not isinstance(focal_override_mm, (int, float))
            or not (math.isfinite(focal_override_mm) and focal_override_mm > 0)):
        raise ValueError(f'focal override must be a positive number of '
                         f'millimetres, got {focal_override_mm!r}')
    geometries = list(geometries)
    with_focal = [g for g in geometries if g.focal_mm is not None]
    missing = len(geometries) - len(with_focal)
    exif_mm = exif_35mm = exif_35mm_from = None
    if with_focal:
        focals = [g.focal_mm for g in with_focal]
        films = [g.focal_35mm_film for g in with_focal
                 if g.focal_35mm_film is not None]
        every_film = len(films) == len(with_focal)
        if focal_override_mm is None:
            _check_spread(camera, focals, 'focal length')
            if every_film:
                _check_spread(camera, films, '35 mm-equivalent focal length')
        exif_mm = float(statistics.median(focals))
        if every_film:
            exif_35mm = float(statistics.median(films))
            exif_35mm_from = 'FocalLengthIn35mmFilm'
        else:
            exif_35mm = focal_35mm_from_sensor(camera, exif_mm)
            exif_35mm_from = (f'FocalLength x {FILM_WIDTH_MM:g} / sensor width '
                              f'{camera.sensor_size_mm[0]:g} mm')
    if focal_override_mm is not None:
        starting = focal_35mm_from_sensor(camera, float(focal_override_mm))
        source = 'override'
        focal_override_mm = float(focal_override_mm)
    elif missing or exif_35mm is None:
        raise FocalRefused(
            f'{camera.key}: {missing} of {len(geometries)} image(s) carry no '
            'EXIF focal length, so RealityScan would get no starting focal '
            'for them. Pass the focal length explicitly (--w_focal_override, '
            'mm)')
    else:
        starting, source = exif_35mm, 'exif'
    return ObservedFocal(camera.key, exif_mm, exif_35mm, exif_35mm_from,
                         len(geometries), missing, focal_override_mm,
                         starting, source)


# ------------------------------------------------------------ sidecars

def intrinsics_to_xmp_values(intrinsics, resolution) -> dict:
    """RealityScan's normalised calibration values from a 3x3 pixel matrix.

      FocalLength35mm = fx / width * 36
      PrincipalPointU = (cx - width / 2) / width
      PrincipalPointV = (cy - height / 2) / height

    This is the conversion behind the registry values. It matched
    RealityScan's own exported sidecars for square images only; for a
    non-square image the divisor of PrincipalPointV (height, or the same
    scale as the focal) is not established.
    """
    fx = float(intrinsics[0][0])
    cx = float(intrinsics[0][2])
    cy = float(intrinsics[1][2])
    w, h = float(resolution[0]), float(resolution[1])
    return {
        'focal35': fx / w * 36.0,
        'ppu': (cx - w / 2.0) / w,
        'ppv': (cy - h / 2.0) / h,
    }


def rs_distortion_coefficients(opencv) -> tuple[float, ...]:
    """OpenCV (k1, k2, p1, p2, k3) in RealityScan's six-slot order
    (k1, k2, k3, k4, t1, t2): RealityScan's t1 is OpenCV's p2 and its t2 is
    p1; k4 has no OpenCV counterpart
    here and is 0. The slot order comes from RealityScan's shipped export
    templates; the radius normalisation is inferred, not verified."""
    k1, k2, p1, p2, k3 = (float(v) for v in opencv)
    return (k1, k2, k3, 0.0, p2, p1)


def _num(value: float) -> str:
    """Fixed-point, at most ten decimals, no exponent, no trailing zeros."""
    text = f'{float(value) + 0.0:.10f}'.rstrip('0').rstrip('.')
    return '0' if text in ('', '-0') else text


def _check_focal(camera: Camera, focal_35mm) -> float:
    if focal_35mm is None or isinstance(focal_35mm, bool) \
            or not isinstance(focal_35mm, (int, float)) \
            or not (math.isfinite(focal_35mm) and focal_35mm > 0):
        raise ValueError(
            f'{camera.key}: a groups sidecar needs the starting 35 mm-'
            f'equivalent focal length, got {focal_35mm!r}')
    return float(focal_35mm)


def sidecar_xmp(camera: Camera, mode: str,
                focal_35mm: float | None = None) -> str:
    """Exact sidecar text for ``camera`` in ``mode`` ('prior' or 'groups').

    ``groups`` needs ``focal_35mm``, the starting 35 mm-equivalent focal
    (:func:`observe_focal`), which it writes as an initial (adjustable)
    value; ``prior`` writes the calibration's own focal and ignores it.
    """
    if mode not in SIDECAR_MODES:
        raise ValueError(f'mode {mode!r} writes no sidecar')
    if mode == 'groups':
        return _sidecar_text(camera, mode,
                             _num(_check_focal(camera, focal_35mm)))
    return _sidecar_text(camera, mode, None)


def _sidecar_text(camera: Camera, mode: str, focal_text: str | None) -> str:
    if mode == 'groups':
        attributes = [
            (f'xcr:CalibrationPrior="{_PRIOR_TOKEN["approximate"]}"'
             f' xcr:CalibrationGroup="{camera.calibration_group}"'),
            (f'xcr:DistortionGroup="{camera.lens_distortion_group}"'
             f' xcr:DistortionModel="{camera.distortion_model}"'),
            f'xcr:FocalLength35mm="{focal_text}" xcr:Skew="0"',
        ]
    else:
        coefficients = ' '.join(
            _num(c) for c in rs_distortion_coefficients(camera.opencv_distortion))
        attributes = [
            (f'xcr:CalibrationPrior="{_PRIOR_TOKEN[camera.calibration_prior]}"'
             f' xcr:CalibrationGroup="{camera.calibration_group}"'),
            (f'xcr:DistortionGroup="{camera.lens_distortion_group}"'
             f' xcr:DistortionModel="{camera.distortion_model}"'),
            f'xcr:DistortionCoeficients="{coefficients}"',
            f'xcr:FocalLength35mm="{_num(camera.focal_length_35mm)}" xcr:Skew="0"',
            ('xcr:AspectRatio="1"'
             f' xcr:PrincipalPointU="{_num(camera.principal_point_u)}"'),
            f'xcr:PrincipalPointV="{_num(camera.principal_point_v)}"',
        ]
    lines = [
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">',
        '  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">',
        '    <rdf:Description xcr:Version="4"',
        *(f'       {line}' for line in attributes),
        f'       xmlns:xcr="{XCR_NAMESPACE}">',
        '    </rdf:Description>',
        '  </rdf:RDF>',
        '</x:xmpmeta>',
    ]
    return '\n'.join(lines) + '\n'


def sidecar_path(image_path: str) -> str:
    """``<stem>.xmp`` beside the image: RealityScan binds a sidecar to the
    image name minus its last extension (``X.card.JPG`` -> ``X.card.xmp``)."""
    return os.path.splitext(image_path)[0] + '.xmp'


def _write(path: str, text: str) -> None:
    with open(path, 'w', encoding='utf-8', newline='') as f:
        f.write(text)


def _check_modes(modes: Mapping[str, str]) -> None:
    for key, mode in modes.items():
        if mode not in DECIDED_MODES:
            raise ValueError(f'calibration mode for {key!r} must be one of '
                             f'{DECIDED_MODES}, got {mode!r}')


_FOCAL_SLOT = '\x00'


def _legacy_groups_xmp(camera: Camera) -> str:
    """The ``groups`` sidecar this module wrote before it carried a focal
    length; still recognised as the pipeline's own, so a re-run replaces or
    removes it instead of treating it as foreign."""
    return '\n'.join([
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">',
        '  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">',
        '    <rdf:Description xcr:Version="4"',
        (f'       xcr:CalibrationGroup="{camera.calibration_group}"'
         f' xcr:DistortionGroup="{camera.lens_distortion_group}"'),
        f'       xcr:DistortionModel="{camera.distortion_model}"',
        f'       xmlns:xcr="{XCR_NAMESPACE}">',
        '    </rdf:Description>',
        '  </rdf:RDF>',
        '</x:xmpmeta>',
    ]) + '\n'


def _groups_pattern(camera: Camera) -> re.Pattern[bytes]:
    """The ``groups`` sidecar of ``camera`` with any starting focal."""
    template = re.escape(
        _sidecar_text(camera, 'groups', _FOCAL_SLOT).encode('utf-8'))
    slot = re.escape(_FOCAL_SLOT.encode('utf-8'))
    return re.compile(template.replace(slot, rb'(\d+(?:\.\d+)?)'))


def is_own_sidecar(content: bytes, camera: Camera) -> bool:
    """True when ``content`` is byte for byte a sidecar this module writes
    for ``camera``: its ``prior`` sidecar, a ``groups`` sidecar with any
    positive starting focal (:func:`sidecar_xmp`), or the earlier
    ``groups`` form without a focal."""
    if content in (sidecar_xmp(camera, 'prior').encode('utf-8'),
                   _legacy_groups_xmp(camera).encode('utf-8')):
        return True
    match = _groups_pattern(camera).fullmatch(content)
    return match is not None and float(match.group(1)) > 0


def _free_path(path: str) -> str:
    """``path``, or ``<stem>.<n>.xmp`` with the lowest ``n`` not in use."""
    if not os.path.lexists(path):
        return path
    stem, ext = os.path.splitext(path)
    n = 1
    while os.path.lexists(f'{stem}.{n}{ext}'):
        n += 1
    return f'{stem}.{n}{ext}'


def move_foreign_sidecars(image_paths: Iterable[str],
                          modes: Mapping[str, str], image_root: str,
                          destination: str) -> list[tuple[str, str]]:
    """Move every existing sidecar that :func:`write_sidecars` would
    replace and that this module did not write into ``destination``.

    Only images whose camera mode writes a sidecar are considered. A
    ``<stem>.xmp`` beside such an image whose bytes are not one of
    :func:`sidecar_xmp`'s outputs for that camera (a pose prior, an edited
    calibration, a file from another tool) is moved to the same path
    relative to ``image_root`` under ``destination``; an existing file
    there is never replaced (``<stem>.<n>.xmp`` instead). The pipeline's
    own sidecars, of any mode, stay and are overwritten by
    :func:`write_sidecars`. Returns ``(source, moved to)`` pairs.
    """
    _check_modes(modes)
    image_root = os.path.abspath(image_root)
    moved: list[tuple[str, str]] = []
    for image in sorted(os.path.abspath(p) for p in image_paths):
        camera = camera_registry.identify(os.path.basename(image))
        if camera is None or modes.get(camera.key) not in SIDECAR_MODES:
            continue
        xmp = sidecar_path(image)
        if not os.path.isfile(xmp):
            continue
        try:
            with open(xmp, 'rb') as f:
                content = f.read()
        except OSError as exc:
            # Unreadable here is not provably ours, and RealityScan may
            # still import it: treat it as foreign and move it.
            logger.warning('Unreadable sidecar %s (%s) - treated as not '
                           'written by this pipeline and moved aside',
                           xmp, exc)
            content = None
        if content is not None and is_own_sidecar(content, camera):
            continue
        relative = os.path.relpath(xmp, image_root)
        if relative.startswith(os.pardir):
            raise ValueError(f'{xmp} is not under {image_root}')
        target = _free_path(os.path.join(destination, relative))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        try:
            os.rename(xmp, target)
        except OSError as exc:
            raise ValueError(
                f'cannot move the sidecar {xmp} out of the way of the '
                f'calibration sidecar ({exc}); it would be imported with its '
                'image. Make it readable or remove it, then re-run.') from exc
        moved.append((xmp, target))
    return moved


def write_sidecars(image_paths: Iterable[str],
                   modes: Mapping[str, str],
                   focals: Mapping[str, float] | None = None
                   ) -> list[tuple[str, str | None]]:
    """Write the decided sidecar beside each image.

    ``modes`` maps camera key to its decided mode, ``focals`` camera key to
    the starting 35 mm-equivalent focal of a ``groups`` camera (one without
    it is refused, ValueError, before anything is written). Returns
    ``(image, xmp)``
    pairs as absolute paths, sorted by image path; ``xmp`` is None for an
    image that gets no sidecar (mode ``off``, no decision, or a file of no
    known camera). Two images that would share one sidecar name are refused
    before anything is written.
    """
    _check_modes(modes)
    plan: list[tuple[str, str | None, str | None]] = []
    claimed: dict[str, str] = {}
    for image in sorted(os.path.abspath(p) for p in image_paths):
        camera = camera_registry.identify(os.path.basename(image))
        mode = modes.get(camera.key) if camera is not None else None
        if mode not in SIDECAR_MODES:
            plan.append((image, None, None))
            continue
        xmp = sidecar_path(image)
        key = os.path.normcase(xmp).lower()
        if key in claimed:
            raise ValueError(f'{claimed[key]} and {image} would share the '
                             f'sidecar {xmp}')
        claimed[key] = image
        plan.append((image, xmp,
                     sidecar_xmp(camera, mode, (focals or {}).get(camera.key))))
    for _image, xmp, text in plan:
        if xmp is not None:
            _write(xmp, text)
    return [(image, xmp) for image, xmp, _text in plan]


# ---------------------------------------------------------------- rscmd

def _rscmd_path(path: str) -> str:
    path = os.path.abspath(path)
    if any(c in path for c in '"\r\n') or '$(' in path:
        raise ValueError(f'path cannot be written to an .rscmd file '
                         f'(quote, line break or "$(" in it): {path!r}')
    return path


def rscmd_text(entries: Iterable[tuple[str, str | None]]) -> str:
    """``.rscmd`` text adding every image: ``-addImageWithCalibration`` with
    its sidecar where there is one, a plain ``-add`` otherwise.

    Absolute quoted paths, one command per line, CRLF line endings, sorted
    by image path; an image listed twice is refused.
    """
    commands: dict[str, str] = {}
    for image, xmp in entries:
        image = _rscmd_path(image)
        key = os.path.normcase(image).lower()
        if key in commands:
            raise ValueError(f'image listed twice: {image}')
        if xmp is None:
            commands[key] = f'-add "{image}"'
        else:
            commands[key] = (f'-addImageWithCalibration "{image}" '
                             f'"{_rscmd_path(xmp)}"')
    ordered = [commands[key] for key in sorted(commands)]
    return ''.join(line + '\r\n' for line in ordered)


def write_rscmd(path: str, entries: Iterable[tuple[str, str | None]]) -> str:
    """Write :func:`rscmd_text` to ``path`` (UTF-8, CRLF); returns the
    absolute path."""
    text = rscmd_text(entries)
    path = os.path.abspath(path)
    out_dir = os.path.dirname(path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    _write(path, text)
    return path


# -------------------------------------------------------------- hygiene

def ensure_calibration_sidecars(image_root: str,
                                modes: Mapping[str, str] | None = None,
                                focals: Mapping[str, float] | None = None
                                ) -> tuple[int, int]:
    """Write the decided sidecar for every image under ``image_root`` that
    has none.

    RealityScan's pose exports overwrite ``<stem>.xmp`` and the identity
    harvest moves them away, leaving those images without their sidecar;
    this puts the decided one back. Only cameras whose mode in ``modes`` is
    ``prior`` or ``groups`` get one (``groups`` with its starting focal from
    ``focals``); with no modes nothing is written.

    Returns (created, images of no known camera).
    """
    if not modes:
        return 0, 0
    _check_modes(modes)
    created = unknown = 0
    for root, _dirs, files in os.walk(image_root):
        present = {name.lower() for name in files}
        for filename in sorted(files):
            if os.path.splitext(filename)[1].lower() not in PROCESSABLE_IMAGE_EXTS:
                continue
            sidecar = sidecar_path(filename)
            if sidecar.lower() in present:
                continue
            camera = camera_registry.identify(filename)
            if camera is None:
                unknown += 1
                continue
            mode = modes.get(camera.key)
            if mode not in SIDECAR_MODES:
                continue
            _write(os.path.join(root, sidecar),
                   sidecar_xmp(camera, mode, (focals or {}).get(camera.key)))
            present.add(sidecar.lower())
            created += 1
    if created:
        logger.info('Restored %d calibration sidecar(s) under %s',
                    created, image_root)
    if unknown:
        logger.warning('%d image(s) of no known camera left without a '
                       'calibration sidecar', unknown)
    return created, unknown


def remove_calibration_sidecars(image_root: str,
                                modes: Mapping[str, str] | None = None
                                ) -> tuple[int, int]:
    """Remove this pipeline's calibration sidecars of cameras decided ``off``.

    A sidecar left by an earlier ``prior`` or ``groups`` run would still be
    imported beside its image once calibration is off. Only a sidecar that
    is byte for byte one this module writes for that image's camera
    (:func:`is_own_sidecar`) is removed; any other sidecar of an ``off``
    camera (a pose, an edited or foreign file) is left in place and counted. Cameras of any other mode,
    and images of no known camera, are never touched.

    Returns (removed, left in place).
    """
    if not modes:
        return 0, 0
    _check_modes(modes)
    off = {key for key, mode in modes.items() if mode == 'off'}
    if not off:
        return 0, 0
    removed = kept = 0
    for root, _dirs, files in os.walk(image_root):
        by_lower = {name.lower(): name for name in files}
        for filename in sorted(files):
            if os.path.splitext(filename)[1].lower() not in PROCESSABLE_IMAGE_EXTS:
                continue
            camera = camera_registry.identify(filename)
            if camera is None or camera.key not in off:
                continue
            sidecar = by_lower.get(sidecar_path(filename).lower())
            if sidecar is None:
                continue
            path = os.path.join(root, sidecar)
            with open(path, 'rb') as f:
                content = f.read()
            if is_own_sidecar(content, camera):
                os.remove(path)
                del by_lower[sidecar.lower()]
                removed += 1
                continue
            kept += 1
    return removed, kept


def sanitize_and_census(image_root: str,
                        modes: Mapping[str, str] | None = None,
                        focals: Mapping[str, float] | None = None
                        ) -> tuple[int, int, int]:
    """Count pose-bearing sidecars under ``image_root`` and remove their pose.

    RealityScan's XMP export is the registration census (only registered
    cameras get a pose), but a pose sidecar left beside an image becomes a
    pose prior on the next add of that image, so none may survive the
    census. Each one is rewritten to the decided calibration sidecar when
    its camera's mode in ``modes`` is ``prior`` or ``groups`` (``groups``
    with its starting focal from ``focals``), and deleted otherwise.
    Sidecars without a pose (including the ones this module writes) are
    never touched.

    Returns (pose sidecars found, rewritten, deleted for no known camera);
    ordinal names (00000.xmp, written for imported components) are deleted
    without being counted as unknown.
    """
    modes = modes or {}
    _check_modes(modes)
    pose_count = restored = cleared = removed = 0
    removed_examples: list[str] = []
    unreadable: list[str] = []
    for root, _dirs, files in os.walk(image_root):
        for filename in files:
            if not filename.lower().endswith('.xmp'):
                continue
            path = os.path.join(root, filename)
            try:
                with open(path, encoding='utf-8', errors='replace') as f:
                    content = f.read()
            except OSError as exc:
                # Left in place and reported: the alignment stage refuses
                # (or moves aside) an unreadable sidecar before the next add.
                unreadable.append(f'{path} ({exc})')
                continue
            if 'xcr:Position' not in content:
                continue
            pose_count += 1
            camera = camera_registry.identify(filename)
            mode = modes.get(camera.key) if camera is not None else None
            if mode in SIDECAR_MODES:
                _write(path, sidecar_xmp(camera, mode,
                                         (focals or {}).get(camera.key)))
                restored += 1
                continue
            os.remove(path)
            if camera is not None:
                cleared += 1
            elif not os.path.splitext(filename)[0].isdigit():
                removed += 1
                if len(removed_examples) < 3:
                    removed_examples.append(path)
    if cleared:
        logger.info('sanitize: %d pose sidecar(s) deleted (no calibration '
                    'sidecar decided for their camera)', cleared)
    if removed:
        logger.warning('sanitize: %d pose sidecar(s) of no known camera '
                       'deleted (e.g. %s)', removed, removed_examples)
    if unreadable:
        logger.warning('sanitize: %d sidecar(s) could not be read and were '
                       'left in place unchecked (a pose in them would become '
                       'a prior on the next add): %s', len(unreadable),
                       '; '.join(unreadable))
    return pose_count, restored, removed
