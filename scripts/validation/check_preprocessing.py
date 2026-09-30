#!/usr/bin/env python3
"""Validate preprocessing on a small copy of a survey image dataset.

Checks folder and filename preservation, parity with the canonical transform,
reuse of completed outputs, and the CLAHE pixel change. Source images remain
untouched. A supplied work directory must be empty and separate from the
dataset; otherwise the check uses a temporary directory.

Usage:
    py -3.13 scripts/validation/check_preprocessing.py --dataset D:/survey/zone
"""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
import tempfile
from contextlib import ExitStack
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import cv2
import numpy as np

from module_base.parameter import Parameter
from module_base.settings_store import SettingsStore
from modules.harvest_guard import assert_harvestable
from modules.preprocess_images.preprocess_images import (
    IMAGE_EXTENSIONS, JPEG_QUALITY, PreprocessImages, build_transform)

DEFAULT_DATASET = r'M:\NA173_H2103a\batched_images_by_zone\zone_9'
PER_CAMERA = 20


def sample_cameras(dataset: str, limit: int = 2) -> list[str]:
    """Sample camera folders, falling back to images in the dataset root."""
    subdirs = [d for d in sorted(os.listdir(dataset))
               if os.path.isdir(os.path.join(dataset, d))
               and any(f.lower().endswith(IMAGE_EXTENSIONS)
                       for f in os.listdir(os.path.join(dataset, d)))]
    return subdirs[:limit] or ['']


def stage_input(dataset: str, cameras: list[str], input_dir: str) -> int:
    staged = 0
    for cam in cameras:
        src_dir = os.path.join(dataset, cam) if cam else dataset
        dst_dir = os.path.join(input_dir, cam) if cam else input_dir
        os.makedirs(dst_dir, exist_ok=True)
        names = [f for f in sorted(os.listdir(src_dir))
                 if f.lower().endswith(IMAGE_EXTENSIONS)][:PER_CAMERA]
        for name in names:
            path = Path(src_dir) / name
            if path.is_symlink() or path.is_junction():
                raise ValueError(f'dataset image is an alias: {path}')
            shutil.copy2(path, dst_dir)
        staged += len(names)
    return staged


def prepare_work_dir(dataset: Path, work_dir: Path) -> Path:
    """Require a real, empty output tree disjoint from the source dataset."""
    for path in (dataset.absolute(), work_dir.absolute()):
        if any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)):
            raise ValueError(f'validation path passes through an alias: {path}')
    source, target = dataset.resolve(), work_dir.resolve()
    if source.is_relative_to(target) or target.is_relative_to(source):
        raise ValueError('dataset and work directory must not overlap')
    if work_dir.exists() and not work_dir.is_dir():
        raise ValueError(f'work directory is not a directory: {work_dir}')
    if work_dir.is_dir() and any(work_dir.iterdir()):
        raise ValueError(f'work directory is nonempty; choose a fresh directory: {work_dir}')
    work_dir.mkdir(parents=True, exist_ok=True)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--dataset',
                        default=SettingsStore().get('zone9_test', 'dataset_dir', DEFAULT_DATASET),
                        help='dataset to sample images from')
    parser.add_argument('--work-dir',
                        help='empty workspace for results (default: a temporary directory)')
    args = parser.parse_args(argv)
    dataset = Path(args.dataset)
    if not dataset.is_dir():
        parser.error(f'dataset not found: {dataset} (pass --dataset)')

    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger('check_preprocessing')
    with ExitStack() as cleanup:
        work = Path(args.work_dir) if args.work_dir else Path(cleanup.enter_context(
            tempfile.TemporaryDirectory(prefix='rs_preprocess_check_')))
        try:
            assert_harvestable(str(dataset), logger)
            work_dir = prepare_work_dir(dataset, work)
        except (OSError, ValueError, RuntimeError) as exc:
            parser.error(str(exc))
        cameras = sample_cameras(str(dataset))
        input_dir = work_dir / 'input'
        try:
            expected_count = stage_input(str(dataset), cameras, str(input_dir))
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
        assert expected_count, f'no images staged from {dataset}'
        logger.info('Staged %d images from cameras: %s', expected_count,
                    ', '.join(c or '<root>' for c in cameras))

        module = PreprocessImages(logger)
        params = module.get_parameters()
        params['output_dir'] = Parameter('Output Directory', 'o', 'output_dir', str, str(work_dir))
        params['pre_input_image_dir'].set_value(str(input_dir))
        module.set_params(params)
        ok, message = module.validate_parameters()
        assert ok, message

        result = module.run()
        module.finish()
        assert result['Success'], result
        assert result['Processed'] == expected_count, result
        assert result['Failed'] == 0, result
        out_dir = Path(result['Output Directory'])
        for cam in cameras:
            assert {p.name for p in (input_dir / cam).iterdir()} == {
                p.name for p in (out_dir / cam).iterdir()}, f'{cam or "<root>"}: filenames not preserved'
        print('folder mirroring + filename preservation: OK')

        first_cam = cameras[0]
        cam_in, cam_out = input_dir / first_cam, out_dir / first_cam
        sample = sorted(cam_in.iterdir())[0]
        transform = build_transform({'clahe_clip': 2.0, 'clahe_tile': 8})
        expected = transform(cv2.imread(str(sample)))
        encoded, buffer = cv2.imencode(sample.suffix, expected,
                                      [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        assert encoded
        assert buffer.tobytes() == (cam_out / sample.name).read_bytes(), \
            'module output differs from canonical transform'
        print('byte parity with canonical transform: OK')

        rerun = module.run()
        module.finish()
        assert rerun['Processed'] == 0 and rerun['Skipped (already done)'] == expected_count, rerun
        print('idempotent rerun (all skipped): OK')

        original = cv2.imread(str(sample))
        processed = cv2.imread(str(cam_out / sample.name))
        assert original.shape == processed.shape
        mean_delta = np.abs(original.astype(int) - processed.astype(int)).mean()
        assert mean_delta > 1, f'CLAHE barely changed the image (mean delta {mean_delta:.3f})'
        print(f'CLAHE altered pixels (mean delta {mean_delta:.1f}): OK')
        print('ALL PREPROCESS MODULE TESTS PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
