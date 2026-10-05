"""Shared flight-log discovery.

The pipeline writes flight logs under several names depending on which
stage produced them:

- ``flight_log_<zone>_UTM.txt``          georeference module (zone = UTM zone)
- ``flight_log<suffix>_UTM.txt``         per-zone copies from Batch Directory
  (suffix may be empty, giving ``flight_log_UTM.txt``)
- ``flight_log.txt``                     legacy runs

Every consumer that needs to locate a flight log on disk must go through
:func:`find_flight_log` so the naming conventions stay in one place.

Every trajectory is imported in WGS84 UTM, in the zone named by the log's
own filename tag (``flight_log_19T_UTM.txt``). A log whose name carries no
zone tag can still be found, but it is refused wherever a trajectory is
imported (:func:`require_utm_zone`).
"""

from __future__ import annotations

import glob
import os
import re

# MGRS latitude bands C..M lie south of the equator, N..X north (I and O
# are never used). The band letter rides along in the flight-log filename
# (utm.from_latlon's zone letter), so the EPSG code can be derived instead
# of hand-edited per cruise in FlightLogParams.xml.
_SOUTH_BANDS = set('CDEFGHJKLM')
_NORTH_BANDS = set('NPQRSTUVWX')

_ZONE_IN_NAME = re.compile(r'_(\d{1,2})([C-HJ-NP-X])_UTM\.txt$', re.IGNORECASE)


def assert_one_zone(paths, context: str) -> tuple[int, str] | None:
    """The single UTM zone shared by every flight log in ``paths``.

    Returns the (zone, band) they agree on, or None when none of them
    carries a zone tag (such a log cannot be imported; see
    :func:`require_utm_zone`). Raises ValueError when they DISAGREE -
    including a mix of tagged and untagged names.

    Determinism is not correctness: picking "the first" log out of a
    mixed-zone directory georeferences the other cruise's imagery ~2,000 km
    away, silently, under a CRS derived from the wrong filename
    (audit 2026-08-07).
    """
    seen: dict[tuple[int, str] | None, list[str]] = {}
    for path in paths:
        seen.setdefault(utm_zone_from_flight_log_name(path), []).append(path)
    if len(seen) <= 1:
        return next(iter(seen), None) if seen else None
    detail = '; '.join(
        f'{"no zone tag" if z is None else str(z[0]) + z[1]}: '
        + ', '.join(os.path.basename(p) for p in sorted(files))
        for z, files in sorted(seen.items(),
                               key=lambda kv: (kv[0] is not None, kv[0])))
    raise ValueError(
        f'{context} holds flight logs of DISAGREEING UTM zones '
        f'({detail}). Refusing to pick one: {_WRONG_ZONE}. Keep exactly '
        'one survey zone per directory, or pass the intended log explicitly.')


def find_flight_log(*directories: str | None) -> str | None:
    """Return the first flight log found across the candidate directories.

    All directories are searched for current-format ``flight_log*_UTM.txt``
    logs before any is searched for a legacy ``flight_log.txt``, so a
    georeferenced log always wins over a stale legacy one. Multiple UTM
    logs in one directory resolve to the lexicographically first for
    determinism - but only after :func:`assert_one_zone` has verified they
    all name the SAME zone; disagreement raises ValueError rather than
    silently choosing. Returns ``None`` when nothing matches.
    """
    valid = [d for d in directories if d and os.path.isdir(d)]

    for directory in valid:
        matches = sorted(glob.glob(os.path.join(directory, 'flight_log*_UTM.txt')))
        if matches:
            assert_one_zone(matches, directory)
            return matches[0]

    for directory in valid:
        legacy = os.path.join(directory, 'flight_log.txt')
        if os.path.isfile(legacy):
            return legacy

    return None


def utm_zone_from_flight_log_name(path: str) -> tuple[int, str] | None:
    """(zone number, band letter) parsed from a flight-log filename like
    ``flight_log_53N_UTM.txt`` / ``flight_log_NA167_H2075_53N_UTM.txt``,
    or None when the name carries no zone tag."""
    match = _ZONE_IN_NAME.search(os.path.basename(path))
    if not match:
        return None
    zone = int(match.group(1))
    band = match.group(2).upper()
    if not 1 <= zone <= 60 or band not in _SOUTH_BANDS | _NORTH_BANDS:
        return None
    return zone, band


def require_utm_zone(flight_log_path: str) -> tuple[int, str]:
    """(zone, band) from a flight log's filename tag, or ValueError.

    The importer declares the trajectory's coordinate system from this
    tag. A name without it leaves nothing to declare but the template's
    placeholder zone, so it is refused instead of imported.
    """
    zone_band = utm_zone_from_flight_log_name(flight_log_path)
    if zone_band is None:
        raise ValueError(
            f'Flight log "{os.path.basename(flight_log_path)}" carries no UTM '
            'zone tag. Name it flight_log_<zone><band>_UTM.txt (for example '
            'flight_log_19T_UTM.txt) so its coordinate system can be derived '
            f'from the filename. Refusing to import it: {_WRONG_ZONE}.')
    return zone_band


def validate_utm_zone(zone, band) -> tuple[int, str]:
    """(zone, BAND) after checking both are real, or ValueError.

    utm_zone_from_flight_log_name validates what it PARSES; the writers
    below took whatever they were handed, so a typo produced a
    plausible-looking but nonexistent CRS with exit code 0 - and an
    unknown band silently fell into the NORTHERN branch, turning a
    southern-hemisphere typo into a northern EPSG (audit 2026-08-07).
    """
    try:
        zone_int = int(zone)
    except (TypeError, ValueError):
        raise ValueError(f'invalid UTM zone {zone!r} - must be an '
                         'integer 1-60') from None
    band_up = str(band).upper()
    if not 1 <= zone_int <= 60 or band_up not in _SOUTH_BANDS | _NORTH_BANDS:
        raise ValueError(
            f'invalid UTM zone {zone_int}{band_up} - zone must be 1-60 and '
            'the MGRS latitude band one of C-X excluding I and O. Take both '
            'from the flight log filename via utm_zone_from_flight_log_name, '
            'never from a hand-edited template.')
    return zone_int, band_up


def crs_for_flight_log(path: str | None) -> str | None:
    """``'EPSG:32654'`` for a zone-tagged flight log, else None.

    The publish path needs this: georeferenced OBJ/FBX exports carry raw
    UTM metres (ModelExportParamsOBJ_NiraParts sets
    MvsExportIsGeoreferenced with scale 1.0 and no offset), and uploading
    them without a CRS puts the asset in the wrong part of the world
    (audit 2026-08-07).
    """
    if not path:
        return None
    zone_band = utm_zone_from_flight_log_name(path)
    if zone_band is None:
        return None
    return f'EPSG:{epsg_for_utm_zone(*zone_band)}'


def epsg_for_utm_zone(zone: int, band: str) -> int:
    """EPSG code for a WGS84 UTM zone: 326xx north, 327xx south."""
    zone, band = validate_utm_zone(zone, band)
    return (32700 if band in _SOUTH_BANDS else 32600) + zone


_WRONG_ZONE = (
    "a trajectory imported under the wrong coordinate system is placed in "
    "the wrong part of the world with exit code 0 and no warning")


def _read_params_template(template_path: str) -> str:
    """Text of a FlightLogParams template, or a NAMED FileNotFoundError.

    Named instead of a raw FileNotFoundError (missing file) or
    PermissionError (a DIRECTORY passed as the template) out of open(),
    so the operator is told WHICH file the pipeline wanted
    (audit 2026-08-07).
    """
    if not os.path.isfile(template_path):
        raise FileNotFoundError(
            f'FlightLogParams template not found (or is not a file): '
            f'{template_path!r}. Expected Metadata/FlightLogParams.xml or an '
            'explicit template with the same entries.')
    with open(template_path, encoding='utf-8') as f:
        return f.read()


def params_template_frame(template_path: str) -> str:
    """``'utm'`` when a FlightLogParams XML declares a UTM coordinate
    system, else ``'unknown'``."""
    content = _read_params_template(template_path)
    proj_m = re.search(
        r'<entry key="CoordinateSystemFlightLog" value="([^"]*)"', content)
    type_m = re.search(
        r'<entry key="CoordinateSystemFlightLogType" value="([^"]*)"', content)
    proj = proj_m.group(1) if proj_m else ''
    crs_type = (type_m.group(1) if type_m else '').lower()
    if '+proj=utm' in proj or crs_type.startswith('epsg:'):
        return 'utm'
    return 'unknown'


def write_flight_log_params(template_path: str, output_path: str,
                            zone: int, band: str) -> str:
    """Copy the FlightLogParams template with its coordinate-system pair
    rewritten for WGS84 UTM ``zone``/``band``. Returns output_path.

    RealityScan mis-imports every trajectory silently when the params XML
    declares the wrong zone, so the zone must come from the flight log
    itself (:func:`require_utm_zone`), never a hand-edited template.
    """
    zone, band = validate_utm_zone(zone, band)
    content = _read_params_template(template_path)
    epsg = epsg_for_utm_zone(zone, band)
    south = ' +south' if band in _SOUTH_BANDS else ''
    hemisphere = 'S' if south else 'N'
    proj = f'+proj=utm +zone={zone}{south} +datum=WGS84 +units=m +no_defs'
    crs_type = f'epsg:{epsg} - WGS 84 / UTM zone {zone}{hemisphere}'

    content = re.sub(
        r'(<entry key="CoordinateSystemFlightLog" value=")[^"]*("/>)',
        lambda m: m.group(1) + proj + m.group(2), content)
    content = re.sub(
        r'(<entry key="CoordinateSystemFlightLogType" value=")[^"]*("/>)',
        lambda m: m.group(1) + crs_type + m.group(2), content)

    # A bare relative output name has no dirname; makedirs('') raises
    # WinError 3 (audit 2026-08-07).
    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8', newline='') as f:
        f.write(content)
    return output_path
