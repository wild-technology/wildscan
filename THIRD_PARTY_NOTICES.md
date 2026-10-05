# Third-party notices

Wild Scan is released under the MIT licence (see `LICENSE`). This file lists
the third-party software and data it depends on or carries. The machine-readable
equivalent is `sbom.spdx.json` (SPDX 2.3).

## External tool requirement: RealityScan 2.2

Wild Scan drives RealityScan 2.2 (Epic Games, Inc.) through its command line.
RealityScan is commercial software that the user installs separately under
Epic Games' own licence. It is not packaged with, bundled in or redistributed
by Wild Scan, and it is not a Python dependency.

## Runtime dependencies

Direct runtime dependencies as declared in `pyproject.toml`, with the versions
resolved in a clean virtual environment (`pip install -e ".[dev]"`) when this
file was written. Licences are the declared licences read from each installed
distribution's metadata; where the metadata does not state a licence
unambiguously it is recorded as NOASSERTION rather than inferred. Transitive
dependencies are not listed; consult each package's own metadata. Some binary
wheels (for example numpy, scipy, opencv-python) bundle further third-party
components and ship their own licence files inside the installed package.

| Package | Version | Declared licence | Metadata source |
| --- | --- | --- | --- |
| boto3 | 1.43.108 | Apache-2.0 | License field |
| geopandas | 1.2.0 | BSD-3-Clause | License-Expression |
| inquirer | 3.4.1 | MIT | License field and classifier |
| matplotlib | 3.11.2 | NOASSERTION (not stated unambiguously in metadata) | License field is custom agreement text; classifier names the Python Software Foundation License; no SPDX expression |
| numpy | 2.5.3 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | License-Expression |
| opencv-python | 5.0.0.93 | Apache-2.0 | License field and classifier |
| pandas | 3.0.6 | BSD-3-Clause | License field text and classifier |
| pillow | 12.3.0 | MIT-CMU | License-Expression |
| pyproj | 3.8.0 | MIT | License-Expression |
| requests | 2.34.2 | Apache-2.0 | License field and classifier |
| rich | 15.0.0 | MIT | License field and classifier |
| scikit-learn | 1.9.1 | BSD-3-Clause | License-Expression |
| scipy | 1.18.1 | BSD-3-Clause | License field text and classifier |
| seaborn | 0.13.2 | NOASSERTION (not stated unambiguously in metadata) | only the classifier 'BSD License' is present; the clause count is not stated in metadata |
| shapely | 2.1.2 | BSD-3-Clause | License field |
| textual | 8.2.8 | MIT | License field and classifier |
| tqdm | 4.70.1 | MPL-2.0 AND MIT | License field |

## Development and test dependencies

Installed only with the `dev` extra. Not needed to run the pipeline.

| Package | Version | Declared licence | Metadata source |
| --- | --- | --- | --- |
| pytest | 9.1.1 | MIT | License-Expression |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | License-Expression |

## Data files derived from RealityScan

These files are in the repository and are in RealityScan's own formats.

- `flightlogs.xml` - the pipeline's 13-column flight-log import format. It is a
  flight-log format definition in the structure of RealityScan's shipped
  `flightlogs.xml`, kept so the entry can be merged into the RealityScan
  installation's flight-log dictionary. It is derived from RealityScan's file.
- `modules/realityscan_interface/RS_CLI/Metadata/*.xml` - RealityScan
  parameter-configuration files (alignment, simplify, smoothing, texturing,
  unwrap and export settings, flight-log import) passed to RealityScan on its
  command line. They use RealityScan's configuration schema and setting keys.

Wild Scan makes no statement here about the terms under which these files may
be redistributed; they remain subject to Epic Games' terms for RealityScan.
