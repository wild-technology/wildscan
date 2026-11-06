"""Read-only discovery of imagery, navigation and camera inputs.

Session planning and UI code consume these inventories without preparing data
or starting processing stages.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from modules import camera_registry
from modules.wildsync_intake.intake import (
    IntakeError, find_run_directories, image_variant, node_directories,
    split_run_paths,
)

VIDEO_EXTS = {".mov", ".mp4"}



NAV_HINTS = ("datatable", "nav", "flight", "rumi")



NAV_EXTS = {".csv", ".txt", ".tsv"}



IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}



@dataclass(frozen=True)
class CameraCount:
    """One camera node folder of a run: frame files per image variant."""
    node: str
    camera: str                  # camera key from modules/cameras.json, or ""
    card: int = 0
    review: int = 0
    raw: int = 0
    hidden_files: int = 0

    def summary_line(self) -> str:
        who = (f"{self.node} ({self.camera})" if self.camera
               else f"{self.node} (matches no camera in modules/cameras.json)")
        return (f"{who}: {self.card:,} card, {self.review:,} review, "
                f"{self.raw:,} RAW (RAW is not an input)")



@dataclass(frozen=True)
class RunInfo:
    """A detected Wild Sync run directory."""
    path: Path
    has_run_json: bool
    cameras: tuple[CameraCount, ...] = ()

    @property
    def run_id(self) -> str:
        return self.path.name



@dataclass
class RunDataScan:
    """What the run-directory field actually holds - drives the preview."""
    runs: list[RunInfo] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def summary_lines(self) -> list[str]:
        lines = []
        for run in self.runs:
            lines.append(f"run {run.run_id}"
                         + ("" if run.has_run_json else " (no run.json)"))
            lines += [f"  {cam.summary_line()}" for cam in run.cameras]
        lines += self.problems
        if not lines:
            lines.append("nothing recognised yet - point me at a Wild Sync "
                         "run directory")
        return lines



def scan_run_cameras(run_dir: str | Path) -> tuple[CameraCount, ...]:
    """Frame files per camera node and image variant of one run directory.

    Node folders, variants and frame suffixes come from the intake module's
    own helpers; hidden files (names starting with '.', such as macOS '._*'
    copies) are never frames, exactly as in the intake.
    """
    counts = []
    for node_dir in node_directories(str(run_dir)):
        node = os.path.basename(node_dir)
        family = next((f for f in camera_registry.REGISTRY.families
                       if f.name.lower() == node.lower()), None)
        found = {"card": 0, "review": 0, "raw": 0}
        hidden = 0
        for name in os.listdir(node_dir):
            if name.startswith("."):
                hidden += 1
                continue
            variant = image_variant(name)
            if variant in found and os.path.isfile(os.path.join(node_dir, name)):
                found[variant] += 1
        counts.append(CameraCount(node, family.camera if family else "",
                                  hidden_files=hidden, **found))
    return tuple(counts)



def scan_run_data(location: str | Path) -> RunDataScan:
    """Read-only census of the run-directory field: each ';'-separated path
    is a Wild Sync run directory or a folder of them (the intake's own
    rule). Anything else is reported as a problem."""
    scan = RunDataScan()
    seen: set[Path] = set()
    for path in split_run_paths(str(location)):
        try:
            runs = find_run_directories([path])
        except IntakeError as exc:
            scan.problems.append(str(exc))
            continue
        for run in runs:
            run_path = Path(run)
            if run_path in seen:
                continue
            seen.add(run_path)
            scan.runs.append(RunInfo(run_path,
                                     (run_path / "run.json").is_file(),
                                     scan_run_cameras(run_path)))
    return scan



OFFICIAL_CAMERAS = {
    "Z": "Zeuss 24mm rectilinear zoom (Standard Science Camera)",
    "C": "Cinema (fisheye; 16mm) - Widefield Camera Array",
    "P": "Port (fisheye; 16mm) - Widefield Camera Array",
    "S": "Starboard (fisheye; 16mm) - Widefield Camera Array",
    "U": "Upper (fisheye; 16mm)",
    "M": "Mid (fisheye; 16mm)",
    "L": "Lower (24mm zoom rectilinear lens)",
}



@dataclass
class CameraScan:
    """Camera identities parsed from the imagery filenames."""
    known: dict = field(default_factory=dict)     # family -> (count, example)
    unknown: dict = field(default_factory=dict)   # prefix -> (count, example)

    def summary_lines(self) -> list[str]:
        from modules import camera_registry
        lines = []
        for fam, (count, example) in sorted(self.known.items()):
            cam = camera_registry.CAMERAS.get(
                camera_registry.FAMILY_CAMERA.get(fam, ""), None)
            desc = cam.key if cam else fam
            lines.append(f"camera {desc}: {count:,} images "
                         f"({fam}, e.g. {example}) - priors on file")
        for prefix, (count, example) in sorted(self.unknown.items()):
            suggestion = OFFICIAL_CAMERAS.get(prefix.upper()[:1])
            hint = f" - looks like {suggestion}" if suggestion else ""
            lines.append(f"UNRECOGNISED camera '{prefix}': {count:,} images "
                         f"(e.g. {example}) - optional camera notes{hint}")
        return lines



def scan_image_cameras(image_dir: str | Path, sample_per_dir: int = 200) -> CameraScan:
    """Parse camera identities from filenames: the registry decides KNOWN
    (priors on file); anything else groups by its leading alpha prefix and
    becomes a question."""
    import re as _re

    from modules import camera_registry
    scan = CameraScan()
    if not str(image_dir).strip():
        return scan
    root = Path(image_dir)
    if not root.is_dir():
        return scan
    prefix_re = _re.compile(r"^([A-Za-z]+)")
    for dirpath, _dirs, files in os.walk(root):
        taken = 0
        for name in files:
            if Path(name).suffix.lower() not in IMAGE_EXTS:
                continue
            taken += 1
            if taken > sample_per_dir:
                break
            fam = camera_registry.family(name)
            if fam:
                count, example = scan.known.get(fam, (0, name))
                scan.known[fam] = (count + 1, example)
            else:
                m = prefix_re.match(name)
                prefix = (m.group(1) if m else "?").lower()
                count, example = scan.unknown.get(prefix, (0, name))
                scan.unknown[prefix] = (count + 1, example)
    return scan



@dataclass
class RawDataScan:
    """What the data location actually holds - drives prefills."""
    videos: list[Path] = field(default_factory=list)
    nav_files: list[Path] = field(default_factory=list)
    image_count: int = 0
    image_dirs: list[Path] = field(default_factory=list)
    utm_logs: list[Path] = field(default_factory=list)

    def summary_lines(self) -> list[str]:
        lines = []
        if self.videos:
            lines.append(f"{len(self.videos)} video(s): "
                         + ", ".join(v.name for v in self.videos[:3])
                         + (" ..." if len(self.videos) > 3 else ""))
        if self.nav_files:
            lines.append(f"{len(self.nav_files)} nav candidate(s): "
                         + ", ".join(n.name for n in self.nav_files[:3]))
        if self.image_count:
            lines.append(f"{self.image_count:,} images in "
                         f"{len(self.image_dirs)} folder(s)")
        if self.utm_logs:
            lines.append(f"georeferenced flight log present: "
                         f"{self.utm_logs[0].name}")
        if not lines:
            lines.append("nothing recognised yet - point me at the cruise "
                         "data folder")
        return lines



def scan_raw_data(location: str | Path) -> RawDataScan:
    """Read-only census of a cruise data folder (bounded depth, cheap)."""
    scan = RawDataScan()
    if not location or isinstance(location, str) and not location.strip():
        return scan
    root = Path(location)
    if not root.is_dir():
        return scan
    image_dirs: dict[Path, int] = {}
    for dirpath, dirnames, files in os.walk(root):
        depth = len(Path(dirpath).relative_to(root).parts)
        if depth >= 3:
            dirnames[:] = []
        images_here = 0
        for name in files:
            ext = Path(name).suffix.lower()
            low = name.lower()
            path = Path(dirpath) / name
            if ext in VIDEO_EXTS:
                scan.videos.append(path)
            elif ext in NAV_EXTS and any(h in low for h in NAV_HINTS):
                if low.startswith("flight_log") and low.endswith("_utm.txt"):
                    scan.utm_logs.append(path)
                else:
                    scan.nav_files.append(path)
            elif ext in IMAGE_EXTS:
                images_here += 1
        if images_here:
            image_dirs[Path(dirpath)] = images_here
    scan.image_count = sum(image_dirs.values())
    scan.image_dirs = sorted(image_dirs, key=lambda p: -image_dirs[p])
    # Prefer *final_datatable* nav files, mirroring geoall's own preference.
    scan.nav_files.sort(key=lambda p: ("final_datatable" not in p.name.lower(),
                                       p.name.lower()))
    return scan



def scan_processed_data(location: str | Path) -> dict[str, list[Path]]:
    """A results-shaped location where ROVDataConcat already ran: find its
    datatables and any georeferenced flight logs (read-only)."""
    out: dict[str, list[Path]] = {"datatables": [], "utm_logs": []}
    root = Path(location)
    if not root.is_dir():
        return out
    for dirpath, dirnames, files in os.walk(root):
        depth = len(Path(dirpath).relative_to(root).parts)
        if depth >= 3:
            dirnames[:] = []
        for name in files:
            low = name.lower()
            if low.endswith(".csv") and "datatable" in low:
                out["datatables"].append(Path(dirpath) / name)
            elif low.startswith("flight_log") and low.endswith("_utm.txt"):
                out["utm_logs"].append(Path(dirpath) / name)
    # geoall's own preference: *final_datatable* first.
    out["datatables"].sort(key=lambda p: ("final_datatable" not in p.name.lower(),
                                          p.name.lower()))
    return out
