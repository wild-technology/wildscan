# Calibration record

`ilx_lr1_stereo_dive_001.json` is the calibration record of the Sony ILX-LR1
stereo pair: the per-camera checkerboard solve, its quality, the stereo
extrinsics and the list of calibration image pairs.

The pipeline does not read this file. The values it loads are in
`modules/cameras.json`; `tests/test_camera_registry.py` asserts that the two
files agree. Change both together, and only from a new calibration.

| Field | Content |
|---|---|
| `cameras.ilx_left`, `cameras.ilx_right` | calibration and lens-distortion group, prior level (`approximate`), image size 4096 x 3000, 3 x 3 intrinsic matrix in pixels, OpenCV distortion `k1, k2, p1, p2, k3` (k3 and the tangential terms held at 0), the same calibration in RealityScan's normalised form (`focal_length_35mm`, `principal_point_u`, `principal_point_v`), distortion model `brown3`, provenance |
| `rigs.ilx_lr1_stereo` | eyes, image size, nominal focal length 16 mm, stereo baseline 0.225425 m (supplied, not measured: it sets the scale of a solve made in checkerboard-square units), extrinsics left to right in the OpenCV convention, stereo solve quality, the image pairs used |

The calibration images were taken in a pool on 2026-05-18 and solved on
2026-09-30. They carry no EXIF; the 16 mm focal length is as stated for the
session. The focal length was fixed at 16 mm in the solve; a free-focal solve
is recorded in each camera's provenance. The two cameras are not
hardware-synchronised, so the stereo extrinsics are approximate.

The `pattern` fields under `rigs.ilx_lr1_stereo.eyes` (`_left.`, `_right.`)
describe the calibration image names. They are not used to identify Wild Sync
images; the pipeline identifies cameras by the Wild Sync file name families in
`modules/cameras.json`.

None of these values has been validated as a RealityScan prior. When and how
the calibration is delivered to RealityScan is described in
[The ILX-LR1 stereo rig](../docs/ILX-LR1.md#when-the-calibration-is-applied).
