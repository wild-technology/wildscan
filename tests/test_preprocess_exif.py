"""Opted-in JPEG copies keep EXIF; historical generic output is unchanged.

RealityScan reads the focal length from the images it aligns when no
calibration sidecar is written (calibration ``off``), and those are the
preprocessed copies. Pinned here: the copy carries the original EXIF block
(FocalLength, FocalLengthIn35mmFilm, Make, Model, DateTimeOriginal), its
pixels and encoded image data are exactly what OpenCV wrote before EXIF was
kept, a rotated original's Orientation tag is reset because OpenCV already
rotated the pixels, and an EXIF block that cannot be carried over fails the
image instead of producing a copy without it.

Offline; needs OpenCV and Pillow.
"""
from __future__ import annotations

import io
import json
import logging
import struct
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from module_base.parameter import Parameter
from modules.preprocess_images import preprocess_images as pp
from modules.preprocess_images.preprocess_images import (
    JPEG_QUALITY,
    PreprocessImages,
    _process_one,
    build_transform,
    source_exif,
    upright_exif,
    with_exif,
)

EXIF_IFD = 0x8769
TAGS = {                       # (IFD, tag): value, as the ILX-LR1 writes them
    (None, 0x010F): 'SONY',                          # Make
    (None, 0x0110): 'ILX-LR1',                       # Model
    (EXIF_IFD, 0x9003): '2026:08:20 19:25:42',       # DateTimeOriginal
    (EXIF_IFD, 0x920A): 29.0,                        # FocalLength
    (EXIF_IFD, 0xA405): 29,                          # FocalLengthIn35mmFilm
}


def _exif(orientation: int | None = None) -> Image.Exif:
    exif = Image.Exif()
    for (ifd, tag), value in TAGS.items():
        (exif if ifd is None else exif.get_ifd(ifd))[tag] = value
    if orientation is not None:
        exif[0x0112] = orientation
    return exif


def _source(path: Path, orientation: int | None = None, exif=True,
            size=(96, 64)) -> Path:
    """A camera-like JPEG: textured pixels (so CLAHE has work to do) and
    the ILX-LR1's EXIF tags."""
    rng = np.random.default_rng(7)
    pixels = rng.integers(40, 200, (size[1], size[0], 3), dtype=np.uint8)
    image = Image.fromarray(pixels, 'RGB')
    kwargs = {'exif': _exif(orientation)} if exif else {}
    image.save(str(path), quality=90, **kwargs)
    return path


def _before_exif_was_kept(src: Path, dst: Path) -> bytes:
    """What the worker wrote before this change: OpenCV read, the default
    transform (CLAHE 2.0, 8x8), OpenCV write at JPEG_QUALITY."""
    image = build_transform({'clahe_clip': 2.0, 'clahe_tile': 8,
                             'white_balance': False})(
        cv2.imread(str(src), cv2.IMREAD_COLOR))
    assert cv2.imwrite(str(dst), image, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    return dst.read_bytes()


def _without_exif_segment(data: bytes) -> bytes:
    """``data`` minus its first APP1 Exif segment."""
    start = data.index(b'\xff\xe1')
    (length,) = struct.unpack_from('>H', data, start + 2)
    assert data[start + 4:start + 10] == b'Exif\x00\x00'
    return data[:start] + data[start + 2 + length:]


def _tags(path: Path) -> dict:
    with Image.open(path) as image:
        exif = image.getexif()
    return {(ifd, tag): (exif if ifd is None else exif.get_ifd(ifd)).get(tag)
            for ifd, tag in TAGS}


def test_the_copy_keeps_the_exif_and_the_pixels(tmp_path):
    src = _source(tmp_path / 'Cam1_20260820_192542.42.card.JPG')
    dst = tmp_path / 'out.JPG'
    before = _before_exif_was_kept(src, tmp_path / 'before.JPG')
    assert _process_one((str(src), str(dst), 2.0, 8, False, True)) is None
    after = dst.read_bytes()

    # The encoded image is byte for byte the one written before; only the
    # original APP1 EXIF segment was inserted.
    assert _without_exif_segment(after) == before
    assert after == with_exif(before, source_exif(str(src)))
    # So the decoded pixels are identical, through both decoders.
    assert np.array_equal(cv2.imread(str(dst)),
                          cv2.imread(str(tmp_path / 'before.JPG')))
    with Image.open(dst) as a, Image.open(io.BytesIO(before)) as b:
        assert np.array_equal(np.asarray(a), np.asarray(b))

    tags = _tags(dst)
    assert tags == _tags(src)
    for key, value in TAGS.items():
        assert tags[key] == value, key


def test_the_copy_of_a_jpeg_without_exif_is_unchanged(tmp_path):
    src = _source(tmp_path / 'plain.jpg', exif=False)
    assert source_exif(str(src)) is None
    dst = tmp_path / 'out.jpg'
    assert _process_one((str(src), str(dst), 2.0, 8, False, True)) is None
    assert dst.read_bytes() == _before_exif_was_kept(src, tmp_path / 'b.jpg')


def test_a_png_keeps_no_exif_and_its_pixels(tmp_path):
    src = tmp_path / 'frame.png'
    cv2.imwrite(str(src), np.random.default_rng(3).integers(
        0, 255, (32, 32, 3), dtype=np.uint8))
    dst = tmp_path / 'out.png'
    assert _process_one((str(src), str(dst), 2.0, 8, False, True)) is None
    expected = build_transform({'clahe_clip': 2.0, 'clahe_tile': 8})(
        cv2.imread(str(src)))
    assert np.array_equal(cv2.imread(str(dst)), expected)


@pytest.mark.parametrize('orientation', [6, 8, 3])
def test_a_rotated_original_is_written_upright(tmp_path, orientation):
    """OpenCV applies the orientation when it reads: the copy's pixels are
    already upright, so its Orientation must be 1, never the original's."""
    src = _source(tmp_path / 'rotated.jpg', orientation=orientation)
    dst = tmp_path / 'out.jpg'
    before = _before_exif_was_kept(src, tmp_path / 'before.jpg')
    assert _process_one((str(src), str(dst), 2.0, 8, False, True)) is None
    assert _without_exif_segment(dst.read_bytes()) == before
    upright = cv2.imread(str(dst))
    # OpenCV rotated the pixels: a quarter turn swaps width and height.
    assert upright.shape == ((96, 64, 3) if orientation in (6, 8)
                             else (64, 96, 3))
    with Image.open(dst) as image:
        assert image.getexif()[0x0112] == 1
        assert image.size == (upright.shape[1], upright.shape[0])
    assert _tags(dst) == _tags(src)


def test_upright_exif_changes_only_the_orientation_value():
    for byte_order in ('<', '>'):
        raw = source_like_exif(orientation=6, byte_order=byte_order)
        fixed = upright_exif(raw)
        assert len(fixed) == len(raw)
        differing = [i for i, (a, b) in enumerate(zip(raw, fixed)) if a != b]
        assert len(differing) == 1
        assert upright_exif(fixed) == fixed
    plain = source_like_exif(orientation=None, byte_order='<')
    assert upright_exif(plain) == plain


def source_like_exif(orientation: int | None, byte_order: str) -> bytes:
    """A minimal EXIF payload: IFD0 with Make and, optionally, Orientation."""
    mark = b'II' if byte_order == '<' else b'MM'
    entries = [struct.pack(byte_order + 'HHI4s', 0x010F, 2, 4, b'SON\x00')]
    if orientation is not None:
        entries.append(struct.pack(byte_order + 'HHIHH', 0x0112, 3, 1,
                                   orientation, 0))
    ifd = struct.pack(byte_order + 'H', len(entries)) + b''.join(entries) \
        + struct.pack(byte_order + 'I', 0)
    tiff = mark + struct.pack(byte_order + 'HI', 42, 8) + ifd
    return b'Exif\x00\x00' + tiff


@pytest.mark.parametrize('exif', [b'Exif\x00\x00XX\x00\x00',
                                  b'Exif\x00\x00II*\x00\xff\xff\x00\x00'])
def test_a_malformed_exif_block_is_refused(exif):
    with pytest.raises(ValueError, match='malformed EXIF'):
        upright_exif(exif)


def test_an_exif_block_that_cannot_be_carried_over_fails_the_image(
        tmp_path, monkeypatch):
    src = _source(tmp_path / 'broken.jpg')
    dst = tmp_path / 'out.jpg'
    monkeypatch.setattr(pp, 'source_exif',
                        lambda path: b'Exif\x00\x00XX\x00\x00')
    assert _process_one((str(src), str(dst), 2.0, 8, False, True)) == str(src)
    assert not dst.exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ['broken.jpg']


def test_with_exif_refuses_a_non_jpeg_and_an_oversized_block():
    with pytest.raises(ValueError, match='not a JPEG'):
        with_exif(b'\x89PNG', b'Exif\x00\x00')
    with pytest.raises(ValueError, match='does not fit'):
        with_exif(b'\xff\xd8\xff\xd9', b'Exif\x00\x00' + b'\x00' * 0xFFFF)


class _SerialPool:
    def __init__(self, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def map(self, function, jobs, **kwargs):
        return map(function, jobs)


def test_outputs_written_before_exif_was_kept_are_not_reused(tmp_path,
                                                             monkeypatch):
    """A manifest whose settings do not record the EXIF policy describes
    copies without EXIF: they are processed again, not reused."""
    monkeypatch.setattr(pp, 'ProcessPoolExecutor', _SerialPool)
    source = tmp_path / 'source'
    source.mkdir()
    _source(source / 'Cam1_a.card.JPG')
    module = PreprocessImages(logging.getLogger('preprocess-exif-test'))
    module.params = module.get_parameters()
    module.params['pre_input_image_dir'].set_value(str(source))
    module.params['pre_workers'].set_value(1)
    module.params['pre_preserve_exif'].set_value(True)
    module.params['output_dir'] = Parameter('Out', 'o', 'output_dir', str,
                                           str(tmp_path / 'workspace'))
    assert module.run()['Processed'] == 1
    manifest_path = tmp_path / 'workspace' / pp.MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    assert manifest['settings']['exif'] == pp.EXIF_POLICY
    assert module.run()['Skipped (already done)'] == 1
    del manifest['settings']['exif']
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
    result = module.run()
    assert result['Success'] and result['Processed'] == 1
    output = Path(result['Output Directory']) / 'Cam1_a.card.JPG'
    assert _tags(output)[(EXIF_IFD, 0x920A)] == 29.0


def test_source_exif_accepts_the_mpo_format_sony_cards_write(tmp_path, monkeypatch):
    """Pillow reports a Sony ILX-LR1 card JPEG as ``MPO`` (it carries a
    Multi-Picture extension). The EXIF reader must treat it as a JPEG, or every
    card frame would lose its focal length in preprocessing."""
    from PIL import Image
    import modules.preprocess_images.preprocess_images as mod
    src = tmp_path / 'Cam1_20260820_192542.42.card.JPG'
    image = Image.new('RGB', (48, 32), (90, 120, 150))
    exif = Image.Exif()
    exif[0x010F] = 'SONY'
    exif.get_ifd(0x8769)[0xA405] = 29
    image.save(src, 'JPEG', exif=exif.tobytes())
    real_open = Image.open

    class _Mpo:
        def __init__(self, inner):
            self._inner = inner
            self.info = inner.info
            self.format = 'MPO'

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._inner.close()

    monkeypatch.setattr(mod.Image, 'open', lambda path: _Mpo(real_open(path)))
    payload = mod.source_exif(str(src))
    assert payload is not None and payload.startswith(b'Exif')



def _module_for(source: Path, workspace: Path, monkeypatch, *, preserve=False):
    monkeypatch.setattr(pp, 'ProcessPoolExecutor', _SerialPool)
    module = PreprocessImages(logging.getLogger('preprocess-policy-test'))
    module.params = module.get_parameters()
    module.params['pre_input_image_dir'].set_value(str(source))
    module.params['pre_workers'].set_value(1)
    module.params['pre_preserve_exif'].set_value(preserve)
    module.params['output_dir'] = Parameter('Out', 'o', 'output_dir', str, str(workspace))
    return module


@pytest.mark.parametrize('orientation', [None, 6])
def test_historical_five_field_worker_drops_exif_and_matches_original_encoding(
        tmp_path, monkeypatch, orientation):
    src = _source(tmp_path / 'Z004_20260820T192542Z.jpg', orientation=orientation)
    dst = tmp_path / 'output.jpg'
    expected = _before_exif_was_kept(src, tmp_path / 'baseline.jpg')

    def unexpected_exif_read(*_args):
        pytest.fail('Historical generic preprocessing must not read EXIF with Pillow')

    monkeypatch.setattr(pp, 'source_exif', unexpected_exif_read)
    assert _process_one((str(src), str(dst), 2.0, 8, False)) is None
    assert dst.read_bytes() == expected
    with Image.open(dst) as image:
        assert not image.getexif()


def test_generic_module_keeps_original_manifest_and_reuses_legacy_output(
        tmp_path, monkeypatch):
    import os

    source = tmp_path / 'source'
    source.mkdir()
    src = _source(source / 'C002C_20260820T192542Z.jpg')
    workspace = tmp_path / 'workspace'
    output = workspace / 'preprocessed_images'
    output.mkdir(parents=True)
    dst = output / src.name
    baseline = _before_exif_was_kept(src, dst)
    original_mtime = dst.stat().st_mtime_ns
    settings = {
        'input_dir': os.path.normcase(os.path.realpath(source)),
        'clahe_clip': 2.0, 'clahe_tile': 8, 'white_balance': False,
        'jpeg_quality': JPEG_QUALITY, 'opencv': cv2.__version__,
    }
    marker = workspace / pp.MANIFEST_NAME
    marker.write_text(json.dumps({
        'schema': 1, 'status': 'complete', 'settings': settings,
        'images': {src.name: {'source': pp._file_signature(str(src)),
                              'output': pp._file_signature(str(dst))}},
    }), encoding='utf-8')
    module = _module_for(source, workspace, monkeypatch)

    def unexpected_rebuild(*_args):
        pytest.fail('Matching legacy preprocessing must be reused')

    monkeypatch.setattr(pp, '_process_one', unexpected_rebuild)
    result = module.run()
    assert result['Success'] and result['Skipped (already done)'] == 1
    assert result['Processed'] == 0
    assert dst.read_bytes() == baseline
    assert dst.stat().st_mtime_ns == original_mtime
    assert json.loads(marker.read_text(encoding='utf-8'))['settings'] == settings


def test_generic_module_defaults_to_original_encoding_even_for_sony_filenames(
        tmp_path, monkeypatch):
    source = tmp_path / 'source'
    source.mkdir()
    src = _source(source / 'Cam1_20260820_192542.42.card.JPG')
    module = _module_for(source, tmp_path / 'workspace', monkeypatch)
    assert module.params['pre_preserve_exif'].get_value() is False
    result = module.run()
    assert result['Success']
    dst = Path(result['Output Directory']) / src.name
    assert dst.read_bytes() == _before_exif_was_kept(src, tmp_path / 'baseline.jpg')
    settings = json.loads((tmp_path / 'workspace' / pp.MANIFEST_NAME).read_text())['settings']
    assert 'exif' not in settings


def test_explicit_preprocess_flag_enables_exif_without_an_intake(tmp_path, monkeypatch):
    from main import build_arg_parser

    source = tmp_path / 'source'
    source.mkdir()
    src = _source(source / 'Z004_20260820T192542Z.jpg')
    module = _module_for(source, tmp_path / 'workspace', monkeypatch, preserve=True)
    flag = module.get_parameters()['pre_preserve_exif']
    assert flag.cli_long == 'p_preserve_exif' and flag.type is bool
    assert flag.default_value is False
    parsed = build_arg_parser(module.get_parameters()).parse_args([
        '--p_preserve_exif', 'true'])
    assert parsed.p_preserve_exif is True
    result = module.run()
    assert result['Success']
    assert _tags(Path(result['Output Directory']) / src.name) == _tags(src)


def test_real_sony_intake_enables_exif_for_its_own_raw_images(tmp_path, monkeypatch):
    from tests.test_wildsync_intake import build_run
    from modules.wildsync_intake.intake import run_intake

    run = build_run(tmp_path / 'runs')
    workspace = tmp_path / 'workspace'
    run_intake([str(run)], str(workspace))
    source = workspace / 'raw_images'
    src = next(source.rglob('*.JPG'))
    source_before = src.read_bytes()
    module = _module_for(source, workspace, monkeypatch)
    assert module.params['pre_preserve_exif'].get_value() is False
    result = module.run()
    assert result['Success'] and result['Processed'] == 6
    dst = Path(result['Output Directory']) / src.relative_to(source)
    assert _tags(dst) == _tags(src)
    assert source_before == src.read_bytes()
    settings = json.loads((workspace / pp.MANIFEST_NAME).read_text())['settings']
    assert settings['exif'] == pp.EXIF_POLICY


def test_nearby_sony_manifest_does_not_enable_generic_input_exif(tmp_path, monkeypatch):
    from tests.test_wildsync_intake import build_run
    from modules.wildsync_intake.intake import run_intake

    run = build_run(tmp_path / 'runs')
    workspace = tmp_path / 'workspace'
    run_intake([str(run)], str(workspace))
    unrelated = workspace / 'unrelated_images'
    unrelated.mkdir()
    src = _source(unrelated / 'Cam1_20260820_192542.42.card.JPG')
    module = _module_for(unrelated, workspace, monkeypatch)
    result = module.run()
    assert result['Success']
    dst = Path(result['Output Directory']) / src.name
    assert dst.read_bytes() == _before_exif_was_kept(src, tmp_path / 'baseline.jpg')
    settings = json.loads((workspace / pp.MANIFEST_NAME).read_text())['settings']
    assert 'exif' not in settings


@pytest.mark.parametrize('payload', [
    '{invalid-json',
    json.dumps({'schema': 2, 'status': 'in_progress'}),
    json.dumps({'schema': 1, 'status': 'complete'}),
])
@pytest.mark.parametrize('explicit_preserve', [False, True])
def test_invalid_owned_intake_manifest_fails_before_changing_output(
        tmp_path, monkeypatch, payload, explicit_preserve):
    from modules.wildsync_intake.intake import MANIFEST_NAME

    source = tmp_path / 'source'
    source.mkdir()
    _source(source / 'Cam1_a.card.JPG')
    (source / MANIFEST_NAME).write_text(payload, encoding='utf-8')
    workspace = tmp_path / 'workspace'
    output = workspace / 'preprocessed_images'
    output.mkdir(parents=True)
    previous = output / 'prior.jpg'
    previous.write_bytes(b'preserved previous output')
    marker = workspace / pp.MANIFEST_NAME
    marker.write_bytes(b'preserved previous manifest')
    before = {path.relative_to(workspace): path.read_bytes()
              for path in workspace.rglob('*') if path.is_file()}
    module = _module_for(source, workspace, monkeypatch, preserve=explicit_preserve)
    valid, message = module.validate_parameters()
    assert not valid and 'manifest' in message
    result = module.run()
    assert not result['Success'] and 'manifest' in result['Failure']
    assert {path.relative_to(workspace): path.read_bytes()
            for path in workspace.rglob('*') if path.is_file()} == before
