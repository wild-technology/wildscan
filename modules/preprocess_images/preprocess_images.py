"""Pre-alignment image preprocessing for underwater imagery.

Canonical implementation of the CLAHE / white-balance transforms; the
manual check (scripts/validation/check_preprocessing.py) imports from here
rather than maintaining its own copy.

Defaults (CLAHE clip 2.0, 8x8 tiles, no white balance) come from an A/B
comparison on an image subset: without CLAHE alignment produced no
component, CLAHE 2.0/8x8 registered the most images, every neighboring
clip/tile setting scored lower, and adding gray-world white balance
lowered registration.

Originals are never modified: processed copies are written to
<output_dir>/preprocessed_images with the input's folder structure and the
same filenames (flight-log matching relies on the names). The current
pipeline aligns and textures the processed copies; originals are retained.

A JPEG copy keeps the original's EXIF block (focal length, camera make and
model, capture time), so RealityScan can read the focal length from the
copy when no calibration sidecar is written. The pixels are encoded by
OpenCV exactly as before; the original APP1 EXIF segment is then inserted
into the encoded file unchanged (:func:`with_exif`), except that the
Orientation tag is set to 1 because OpenCV has already applied it to the
pixels (:func:`upright_exif`).
"""

from __future__ import annotations

import hashlib
import json
import os
import struct
import tempfile
from concurrent.futures import ProcessPoolExecutor

import cv2
import numpy as np
from PIL import Image

from module_base.parameter import Parameter
from module_base.rs_module import RSModule

from ..harvest_guard import assert_harvestable
from ..image_exts import ALL_IMAGE_EXTS

IMAGE_EXTENSIONS = ('.jpg', '.jpeg', '.png')
JPEG_EXTENSIONS = ('.jpg', '.jpeg')
JPEG_QUALITY = 95
MANIFEST_NAME = 'preprocessed_images.manifest.json'
# Recorded in the manifest settings: outputs written before the EXIF was
# kept carry none, so they are never reused as if they did.
EXIF_POLICY = 'original APP1 EXIF kept, Orientation set to 1'

_EXIF_HEADER = b'Exif\x00\x00'
# Pillow's format names for a JPEG file: plain JPEG, or JPEG with Sony's
# Multi-Picture (MPO) extension, which every ILX-LR1 card JPEG carries.
_JPEG_FORMATS = frozenset({'JPEG', 'MPO'})
_SOI = b'\xff\xd8'
_APP0 = b'\xff\xe0'
_APP1 = b'\xff\xe1'
_ORIENTATION = 0x0112
_TIFF_SHORT = 3


def source_exif(path: str) -> bytes | None:
    """The EXIF payload of a JPEG's APP1 segment (``Exif\\0\\0`` + TIFF), read
    with Pillow; None for a JPEG without EXIF or another format. Sony card
    JPEGs are reported by Pillow as ``MPO``; they are accepted."""
    with Image.open(path) as image:
        if image.format not in _JPEG_FORMATS:
            return None
        exif = image.info.get('exif')
    if not exif or not exif.startswith(_EXIF_HEADER):
        return None
    return exif


def upright_exif(exif: bytes) -> bytes:
    """``exif`` with an IFD0 Orientation tag set to 1 (no rotation): OpenCV
    applies the orientation to the pixels when it reads the image, so a
    copied tag would rotate the copy a second time. Every other byte is
    kept. ValueError for an EXIF block whose TIFF header or IFD0 is
    malformed."""
    tiff = len(_EXIF_HEADER)
    try:
        endian = {b'II': '<', b'MM': '>'}[exif[tiff:tiff + 2]]
        ifd0 = tiff + struct.unpack_from(endian + 'I', exif, tiff + 4)[0]
        (count,) = struct.unpack_from(endian + 'H', exif, ifd0)
        for index in range(count):
            entry = ifd0 + 2 + 12 * index
            tag, kind, values = struct.unpack_from(endian + 'HHI', exif, entry)
            if tag == _ORIENTATION and kind == _TIFF_SHORT and values == 1:
                if struct.unpack_from(endian + 'H', exif, entry + 8)[0] == 1:
                    return exif
                out = bytearray(exif)
                struct.pack_into(endian + 'H', out, entry + 8, 1)
                return bytes(out)
    except (KeyError, struct.error) as exc:
        raise ValueError(f'malformed EXIF block: {exc!r}') from exc
    return exif


def with_exif(jpeg: bytes, exif: bytes) -> bytes:
    """``jpeg`` with ``exif`` inserted as an APP1 segment right after the
    start-of-image marker and the JFIF APP0 segment, if there is one; the
    encoded image data is not touched."""
    if not jpeg.startswith(_SOI):
        raise ValueError('not a JPEG stream (no start-of-image marker)')
    if len(exif) + 2 > 0xFFFF:
        raise ValueError(f'EXIF block of {len(exif)} bytes does not fit one '
                         'APP1 segment')
    position = len(_SOI)
    if jpeg[position:position + 2] == _APP0:
        (length,) = struct.unpack_from('>H', jpeg, position + 2)
        position += 2 + length
    segment = _APP1 + struct.pack('>H', len(exif) + 2) + exif
    return jpeg[:position] + segment + jpeg[position:]


def _file_signature(path: str) -> dict:
    stat = os.stat(path)
    with open(path, 'rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns, 'sha256': digest}


def _save_manifest(path: str, manifest: dict) -> None:
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                                         dir=os.path.dirname(path),
                                         suffix='.tmp', delete=False) as stream:
            temp_path = stream.name
            json.dump(manifest, stream, indent=2)
        os.replace(temp_path, path)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)


def gray_world_white_balance(img: np.ndarray) -> np.ndarray:
    result = img.astype(np.float32)
    means = result.reshape(-1, 3).mean(axis=0)
    overall = means.mean()
    for c in range(3):
        if means[c] > 1e-6:
            result[:, :, c] *= overall / means[c]
    return np.clip(result, 0, 255).astype(np.uint8)


def clahe_lab(img: np.ndarray, clip: float, tile: int) -> np.ndarray:
    """CLAHE on the L channel in LAB space: enhances local contrast without
    shifting color, which is what matters for feature matching on
    underwater imagery."""
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=clip, tileGridSize=(tile, tile))
    l_channel = clahe.apply(l_channel)
    return cv2.cvtColor(cv2.merge((l_channel, a_channel, b_channel)), cv2.COLOR_LAB2BGR)


def build_transform(params: dict):
    """Returns a BGR->BGR callable for these parameters, or None when no
    step is enabled (= byte-for-byte copy)."""
    steps = []
    if params.get('white_balance'):
        steps.append(gray_world_white_balance)
    if params.get('clahe_clip'):
        clip = float(params['clahe_clip'])
        tile = int(params.get('clahe_tile', 8))
        steps.append(lambda img, c=clip, t=tile: clahe_lab(img, c, t))

    if not steps:
        return None

    def transform(img):
        for step in steps:
            img = step(img)
        return img

    return transform


def _process_one(job: tuple[str, str, float, int, bool]) -> str | None:
    """Worker: read src, apply the transform, write dst - a JPEG with the
    source's EXIF (:func:`with_exif`). Returns the source path on failure so
    the parent can log it (module methods are not picklable, so this is a
    top-level function rebuilding the transform). A source whose EXIF
    cannot be carried over is a failure, never a copy without it."""
    src, dst, clahe_clip, clahe_tile, white_balance = job
    transform = build_transform({'clahe_clip': clahe_clip,
                                 'clahe_tile': clahe_tile,
                                 'white_balance': white_balance})
    temp_path = None
    try:
        exif = None
        if os.path.splitext(dst)[1].lower() in JPEG_EXTENSIONS:
            exif = source_exif(src)
            if exif is not None:
                exif = upright_exif(exif)
        image = cv2.imread(src, cv2.IMREAD_COLOR)
        if image is None:
            return src
        if transform is not None:
            image = transform(image)
        with tempfile.NamedTemporaryFile(dir=os.path.dirname(dst),
                                         suffix=os.path.splitext(dst)[1],
                                         delete=False) as stream:
            temp_path = stream.name
        if not cv2.imwrite(temp_path, image, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]):
            return src
        if exif is not None:
            with open(temp_path, 'rb') as stream:
                encoded = stream.read()
            with open(temp_path, 'wb') as stream:
                stream.write(with_exif(encoded, exif))
        os.replace(temp_path, dst)
    except (OSError, ValueError, cv2.error):
        # OSError includes Pillow's UnidentifiedImageError; ValueError is
        # an EXIF block that cannot be carried over.
        return src
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)
    return None


class PreprocessImages(RSModule):

    def __init__(self, logger):
        super().__init__("Preprocess Images", logger)

    def get_parameters(self) -> dict[str, Parameter]:
        additional_params = {}

        additional_params['pre_input_image_dir'] = Parameter(
            name='Preprocess Input Folder',
            cli_short='p_i',
            cli_long='p_input',
            type=str,
            default_value=None,
            description='Directory containing the images to preprocess',
            prompt_user=True,
            disable_when_module_active='Wild Sync Intake'
        )

        additional_params['pre_clahe_clip'] = Parameter(
            name='CLAHE Clip Limit',
            cli_short='p_c',
            cli_long='p_clahe_clip',
            type=float,
            default_value=2.0,
            description='CLAHE clip limit (0 disables CLAHE; 2.0 registered best in an A/B comparison)',
            prompt_user=False
        )

        additional_params['pre_clahe_tile'] = Parameter(
            name='CLAHE Tile Size',
            cli_short='p_t',
            cli_long='p_clahe_tile',
            type=int,
            default_value=8,
            description='CLAHE tile grid size (NxN)',
            prompt_user=False
        )

        additional_params['pre_white_balance'] = Parameter(
            name='Gray-World White Balance',
            cli_short='p_wb',
            cli_long='p_white_balance',
            type=bool,
            default_value=False,
            description='Apply gray-world white balance before CLAHE (it reduced registration in an A/B comparison - off by default)',
            prompt_user=False
        )

        additional_params['pre_workers'] = Parameter(
            name='Preprocess Workers',
            cli_short='p_w',
            cli_long='p_workers',
            type=int,
            default_value=0,
            description='Parallel worker processes (0 = cpu count)',
            prompt_user=False
        )

        return {**super().get_parameters(), **additional_params}

    def __get_input_dir(self) -> str:
        if 'pre_input_image_dir' in self.params:
            return self.params['pre_input_image_dir'].get_value()
        return os.path.join(self.params['output_dir'].get_value(), 'raw_images')

    def get_output_dir(self) -> str:
        return os.path.join(self.params['output_dir'].get_value(), 'preprocessed_images')

    def __collect_jobs(self, input_dir: str, output_dir: str,
                       clip: float, tile: int, wb: bool, records: dict):
        """One (src, dst, ...) job per image, mirroring the input's folder
        structure. Only outputs with matching source and content provenance
        are reused."""
        jobs = []
        skipped = 0
        signatures = {}
        for root, _dirs, files in os.walk(input_dir):
            rel = os.path.relpath(root, input_dir)
            dest_root = output_dir if rel == '.' else os.path.join(output_dir, rel)
            for name in files:
                if not name.lower().endswith(IMAGE_EXTENSIONS):
                    continue
                src = os.path.join(root, name)
                dst = os.path.join(dest_root, name)
                if os.path.islink(dst) or os.path.isjunction(dst):
                    raise ValueError(f'Preprocess destination is a file alias: {dst}')
                key = os.path.relpath(src, input_dir)
                signature = _file_signature(src)
                signatures[key] = signature
                record = records.get(key, {})
                if not isinstance(record, dict):
                    record = {}
                if (record.get('source') == signature and os.path.isfile(dst)
                        and record.get('output') == _file_signature(dst)):
                    skipped += 1
                    continue
                os.makedirs(dest_root, exist_ok=True)
                jobs.append((src, dst, clip, tile, wb))
        return jobs, skipped, signatures

    def run(self):
        input_dir = self.__get_input_dir()
        output_dir = self.get_output_dir()
        clip = float(self.params['pre_clahe_clip'].get_value())
        tile = int(self.params['pre_clahe_tile'].get_value())
        wb = bool(self.params['pre_white_balance'].get_value())
        # ProcessPoolExecutor on Windows caps max_workers at 61
        workers = min(int(self.params['pre_workers'].get_value()) or os.cpu_count() or 1, 61)

        if not clip and not wb:
            self.logger.warning('No preprocessing steps enabled (clip=0, white balance off) - nothing to do')
            return {'Success': False}

        source_root = os.path.normcase(os.path.realpath(input_dir))
        target_root = os.path.normcase(os.path.realpath(output_dir))
        try:
            shared_root = os.path.commonpath((source_root, target_root))
        except ValueError:
            shared_root = None
        if shared_root in (source_root, target_root):
            return {'Success': False,
                    'Failure': 'Preprocess input and output trees must not overlap'}
        if os.path.islink(output_dir) or os.path.isjunction(output_dir):
            return {'Success': False, 'Failure':
                    'Preprocess output must be a real directory, without aliases'}
        try:
            assert_harvestable(output_dir, self.logger)
        except RuntimeError as exc:
            return {'Success': False, 'Failure': f'Unsafe preprocessing output: {exc}'}
        manifest_path = os.path.join(os.path.dirname(output_dir), MANIFEST_NAME)
        settings = {'input_dir': source_root, 'clahe_clip': clip,
                    'clahe_tile': tile, 'white_balance': wb,
                    'jpeg_quality': JPEG_QUALITY, 'opencv': cv2.__version__,
                    'exif': EXIF_POLICY}
        manifest = {'schema': 1, 'status': 'in_progress', 'settings': settings, 'images': {}}
        try:
            with open(manifest_path, encoding='utf-8') as stream:
                previous = json.load(stream)
            if (previous.get('schema') == 1 and previous.get('settings') == settings
                    and isinstance(previous.get('images'), dict)):
                manifest['images'] = previous['images']
        except (OSError, ValueError, AttributeError):
            pass
        try:
            os.makedirs(output_dir, exist_ok=True)
            _save_manifest(manifest_path, manifest)
            jobs, skipped, signatures = self.__collect_jobs(
                input_dir, output_dir, clip, tile, wb, manifest['images'])
            # Source removals must not leave old images in the tree handed
            # to batching. Preserve those copies and require a fresh output.
            extras = [os.path.relpath(os.path.join(root, name), output_dir)
                      for root, _dirs, files in os.walk(output_dir)
                      for name in files if os.path.splitext(name)[1].lower() in ALL_IMAGE_EXTS
                      and os.path.relpath(os.path.join(root, name), output_dir)
                      not in signatures]
            if extras:
                manifest['status'] = 'failed'
                _save_manifest(manifest_path, manifest)
                return {'Success': False, 'Failure': 'Preprocess output contains '
                        f'{len(extras)} image(s) absent from the input; use a fresh output directory'}
            manifest['images'] = {key: value for key, value in manifest['images'].items()
                                  if key in signatures}
            _save_manifest(manifest_path, manifest)
        except (OSError, ValueError) as exc:
            manifest['status'] = 'failed'
            try:
                _save_manifest(manifest_path, manifest)
            except OSError:
                pass
            return {'Success': False, 'Failure': f'Cannot prepare preprocessing: {exc}'}
        self.logger.info('Preprocessing %d images (%d already done) with CLAHE clip=%g tile=%dx%d wb=%s',
                         len(jobs), skipped, clip, tile, tile, wb)

        failures = []
        if jobs:
            bar = self._initialize_loading_bar(len(jobs), 'Preprocessing Images')
            try:
                with ProcessPoolExecutor(max_workers=workers) as pool:
                    for index, (job, failed_src) in enumerate(zip(
                            jobs, pool.map(_process_one, jobs, chunksize=16)), 1):
                        src, dst = job[:2]
                        if failed_src:
                            failures.append(failed_src)
                        else:
                            key = os.path.relpath(src, input_dir)
                            manifest['images'][key] = {
                                'source': signatures[key], 'output': _file_signature(dst)}
                        if index % 128 == 0:
                            _save_manifest(manifest_path, manifest)
                        self._update_loading_bar(bar, 1)
            except (OSError, ValueError, RuntimeError) as exc:
                manifest['status'] = 'failed'
                try:
                    _save_manifest(manifest_path, manifest)
                except OSError:
                    pass
                return {'Success': False, 'Failure': f'Preprocessing failed: {exc}'}

        success = not failures and len(jobs) + skipped > 0
        manifest['status'] = 'complete' if success else 'failed'
        try:
            _save_manifest(manifest_path, manifest)
        except OSError as exc:
            return {'Success': False, 'Failure': f'Cannot record preprocessing result: {exc}'}

        for src in failures[:10]:
            self.logger.warning('Unreadable or unwritable image skipped: %s', src)
        if len(failures) > 10:
            self.logger.warning('...and %d more failures', len(failures) - 10)

        return {
            'Success': success,
            'Processed': len(jobs) - len(failures),
            'Skipped (already done)': skipped,
            'Failed': len(failures),
            'CLAHE': f'clip {clip:g}, {tile}x{tile} tiles' if clip else 'off',
            'White Balance': wb,
            'Output Directory': output_dir,
        }

    def validate_parameters(self) -> tuple[bool, str | None]:
        success, message = super().validate_parameters()
        if not success:
            return success, message

        input_dir = self.__get_input_dir()
        # When chained after Wild Sync Intake the input folder (raw_images)
        # is produced at runtime, so only validate an explicitly given
        # directory.
        if 'pre_input_image_dir' in self.params:
            # Unattended runs never see the prompt, so the value can arrive
            # as None; say which flag is missing instead of raising
            # TypeError out of os.path.isdir.
            if input_dir is None:
                return False, ('Preprocess input directory not set - pass '
                               '-p_i/--p_input (no console to prompt on)')
            if not os.path.isdir(input_dir):
                return False, f'Preprocess input directory does not exist: {input_dir}'

        clip = float(self.params['pre_clahe_clip'].get_value())
        if clip < 0:
            return False, 'CLAHE clip limit must be >= 0'
        tile = int(self.params['pre_clahe_tile'].get_value())
        if tile < 1:
            return False, 'CLAHE tile size must be >= 1'
        if int(self.params['pre_workers'].get_value()) < 0:
            return False, 'Preprocess workers must be >= 0'

        return True, None
