# Third-party notices

Wild Scan is released under the MIT licence (see `LICENSE`). Third-party packages retain their own licences.

## Inventory and evidence

[`sbom.spdx.json`](sbom.spdx.json) is the SPDX 2.3 inventory generated in a clean **Windows / Python 3.13.16 / AMD64** environment during [Windows checks](https://github.com/wild-technology/wildscan/actions/runs/40266672247). It records exact installed versions, transitive dependency relationships and runtime, development and build scopes.

The source revision recorded by that run is [`60b5fe0`](https://github.com/wild-technology/wildscan/commit/60b5fe08a82f48e73d3b8a42532d62e2c3330ae4). The snapshot describes that tested environment, rather than the historical survey-processing environment or every future installation.

[`third_party_licenses/`](third_party_licenses/) contains the licence and notice files supplied by the installed distributions. Its [`manifest.json`](third_party_licenses/manifest.json) records their SHA-256 hashes. Custom `LicenseRef` values preserve supplied text when metadata provides no unambiguous SPDX expression; no licence is guessed from a generic classifier. Git preserves the evidence files byte for byte.

This is a package-level Python inventory. Wheel-supplied licence notices are retained, including notices for bundled native components where upstream supplies them. Embedded native libraries are not separately decomposed; operating-system packages, CUDA/drivers, isolated build environments and separately installed tools are outside that inventory.

## Installed Python packages

| Package | Version | Declared licence / supplied evidence | Scope |
| --- | --- | --- | --- |
| [ansicon](https://pypi.org/project/ansicon/1.89.0/) | 1.89.0 | [Supplied licence text](third_party_licenses/ansicon-1.89.0/) | runtime |
| [blessed](https://pypi.org/project/blessed/1.52.0/) | 1.52.0 | MIT | runtime |
| [boto3](https://pypi.org/project/boto3/1.43.118/) | 1.43.118 | Apache-2.0 | runtime |
| [botocore](https://pypi.org/project/botocore/1.43.118/) | 1.43.118 | Apache-2.0 | runtime |
| [build](https://pypi.org/project/build/1.6.1/) | 1.6.1 | MIT | build |
| [certifi](https://pypi.org/project/certifi/2026.10.23/) | 2026.10.23 | MPL-2.0 | runtime |
| [charset-normalizer](https://pypi.org/project/charset-normalizer/3.5.2/) | 3.5.2 | MIT | runtime |
| [cloudpickle](https://pypi.org/project/cloudpickle/3.1.2/) | 3.1.2 | BSD-3-Clause | runtime |
| [colorama](https://pypi.org/project/colorama/0.4.6/) | 0.4.6 | [Supplied licence text](third_party_licenses/colorama-0.4.6/) | build, development, runtime |
| [contourpy](https://pypi.org/project/contourpy/1.4.0/) | 1.4.0 | BSD-3-Clause | runtime |
| [cycler](https://pypi.org/project/cycler/0.12.1/) | 0.12.1 | [Supplied licence text](third_party_licenses/cycler-0.12.1/) | runtime |
| [editor](https://pypi.org/project/editor/2.0.1/) | 2.0.1 | MIT | runtime |
| [fonttools](https://pypi.org/project/fonttools/4.67.0/) | 4.67.0 | MIT | runtime |
| [geopandas](https://pypi.org/project/geopandas/1.2.0/) | 1.2.0 | BSD-3-Clause | runtime |
| [idna](https://pypi.org/project/idna/3.20/) | 3.20 | BSD-3-Clause | runtime |
| [iniconfig](https://pypi.org/project/iniconfig/2.3.1/) | 2.3.1 | MIT | development |
| [inquirer](https://pypi.org/project/inquirer/3.4.1/) | 3.4.1 | MIT | runtime |
| [jinxed](https://pypi.org/project/jinxed/2.1.0/) | 2.1.0 | [Supplied licence text](third_party_licenses/jinxed-2.1.0/) | runtime |
| [jmespath](https://pypi.org/project/jmespath/1.1.0/) | 1.1.0 | MIT | runtime |
| [joblib](https://pypi.org/project/joblib/1.6.0/) | 1.6.0 | BSD-3-Clause | runtime |
| [kiwisolver](https://pypi.org/project/kiwisolver/1.5.1/) | 1.5.1 | [Supplied licence text](third_party_licenses/kiwisolver-1.5.1/) | runtime |
| [linkify-it-py](https://pypi.org/project/linkify-it-py/2.2.0/) | 2.2.0 | MIT | runtime |
| [markdown-it-py](https://pypi.org/project/markdown-it-py/4.2.0/) | 4.2.0 | [Supplied licence text](third_party_licenses/markdown-it-py-4.2.0/) | runtime |
| [matplotlib](https://pypi.org/project/matplotlib/3.11.2/) | 3.11.2 | [Supplied licence text](third_party_licenses/matplotlib-3.11.2/) | runtime |
| [mdit-py-plugins](https://pypi.org/project/mdit-py-plugins/0.6.1/) | 0.6.1 | [Supplied licence text](third_party_licenses/mdit-py-plugins-0.6.1/) | runtime |
| [mdurl](https://pypi.org/project/mdurl/0.1.2/) | 0.1.2 | [Supplied licence text](third_party_licenses/mdurl-0.1.2/) | runtime |
| [narwhals](https://pypi.org/project/narwhals/2.27.1/) | 2.27.1 | MIT | runtime |
| [numpy](https://pypi.org/project/numpy/2.5.4/) | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | runtime |
| [opencv-python](https://pypi.org/project/opencv-python/5.0.0.93/) | 5.0.0.93 | [Supplied licence text](third_party_licenses/opencv-python-5.0.0.93/) | runtime |
| [packaging](https://pypi.org/project/packaging/26.3/) | 26.3 | Apache-2.0 OR BSD-2-Clause | build, development, runtime |
| [pandas](https://pypi.org/project/pandas/3.1.0/) | 3.1.0 | BSD-3-Clause | runtime |
| [pillow](https://pypi.org/project/pillow/13.0.0/) | 13.0.0 | MIT-CMU | runtime |
| [pip](https://pypi.org/project/pip/26.2.2/) | 26.2.2 | MIT | build |
| [platformdirs](https://pypi.org/project/platformdirs/4.13.5/) | 4.13.5 | MIT | runtime |
| [pluggy](https://pypi.org/project/pluggy/1.7.0/) | 1.7.0 | MIT | development |
| [pygments](https://pypi.org/project/pygments/2.21.0/) | 2.21.0 | BSD-2-Clause | development, runtime |
| [pyogrio](https://pypi.org/project/pyogrio/0.13.0/) | 0.13.0 | [Supplied licence text](third_party_licenses/pyogrio-0.13.0/) | runtime |
| [pyparsing](https://pypi.org/project/pyparsing/3.3.3/) | 3.3.3 | MIT | runtime |
| [pyproj](https://pypi.org/project/pyproj/3.8.0/) | 3.8.0 | MIT | runtime |
| [pyproject-hooks](https://pypi.org/project/pyproject-hooks/1.3.3/) | 1.3.3 | MIT | build |
| [pytest](https://pypi.org/project/pytest/9.1.1/) | 9.1.1 | MIT | development |
| [python-dateutil](https://pypi.org/project/python-dateutil/2.9.0.post0/) | 2.9.0.post0 | [Supplied licence text](third_party_licenses/python-dateutil-2.9.0.post0/) | runtime |
| [readchar](https://pypi.org/project/readchar/4.2.2/) | 4.2.2 | [Supplied licence text](third_party_licenses/readchar-4.2.2/) | runtime |
| [requests](https://pypi.org/project/requests/2.34.2/) | 2.34.2 | Apache-2.0 | runtime |
| [rich](https://pypi.org/project/rich/15.0.0/) | 15.0.0 | MIT | runtime |
| [runs](https://pypi.org/project/runs/1.3.0/) | 1.3.0 | MIT | runtime |
| [s3transfer](https://pypi.org/project/s3transfer/0.19.3/) | 0.19.3 | [Supplied licence text](third_party_licenses/s3transfer-0.19.3/) | runtime |
| [scikit-learn](https://pypi.org/project/scikit-learn/1.9.1/) | 1.9.1 | BSD-3-Clause | runtime |
| [scipy](https://pypi.org/project/scipy/1.18.1/) | 1.18.1 | [Supplied licence text](third_party_licenses/scipy-1.18.1/) | runtime |
| [seaborn](https://pypi.org/project/seaborn/0.13.2/) | 0.13.2 | [Supplied licence text](third_party_licenses/seaborn-0.13.2/) | runtime |
| [shapely](https://pypi.org/project/shapely/2.2.0/) | 2.2.0 | BSD-3-Clause | runtime |
| [six](https://pypi.org/project/six/1.17.0/) | 1.17.0 | MIT | runtime |
| [textual](https://pypi.org/project/textual/8.2.8/) | 8.2.8 | MIT | runtime |
| [threadpoolctl](https://pypi.org/project/threadpoolctl/3.7.0/) | 3.7.0 | BSD-3-Clause | runtime |
| [tqdm](https://pypi.org/project/tqdm/4.70.1/) | 4.70.1 | MPL-2.0 AND MIT | runtime |
| [typing-extensions](https://pypi.org/project/typing-extensions/4.16.0/) | 4.16.0 | PSF-2.0 | runtime |
| [tzdata](https://pypi.org/project/tzdata/2026.5/) | 2026.5 | Apache-2.0 | runtime |
| [urllib3](https://pypi.org/project/urllib3/2.8.0/) | 2.8.0 | MIT | runtime |
| [utm](https://pypi.org/project/utm/0.9.0/) | 0.9.0 | [Supplied licence text](third_party_licenses/utm-0.9.0/) | runtime |
| [wcwidth](https://pypi.org/project/wcwidth/0.9.4/) | 0.9.4 | [Supplied licence text](third_party_licenses/wcwidth-0.9.4/) | runtime |
| [xmod](https://pypi.org/project/xmod/1.10.0/) | 1.10.0 | MIT | runtime |

## External prerequisites

| Component | Installation and licence |
| --- | --- |
| Python 3.13.16 | Separately installed interpreter; PSF-2.0 and the notices supplied with that interpreter. |
| RealityScan 2.2 | Separately installed and licensed Epic Games application. No RealityScan executable is redistributed by Wild Scan. |
| niraclient | Optional [external checkout](https://github.com/NiraOfficial/niraclient) for Nira publishing; use its supplied licence and service terms. Not bundled here. |

## Reproducing or refreshing the inventory

The optional [`constraints-windows-py313.txt`](constraints-windows-py313.txt) pins the Python package versions in this snapshot for Windows/Python 3.13. It does not pin RealityScan, CUDA or survey inputs. Standard installation without constraints may resolve newer compatible packages.

From the source checkout in a fresh Windows/Python 3.13 environment:

```powershell
& ".\.venv\Scripts\python.exe" -m pip install -c constraints-windows-py313.txt -e ".[dev]" build
& ".\.venv\Scripts\python.exe" scripts/generate_sbom.py --output inventory/sbom.spdx.json --license-dir inventory/third_party_licenses
& ".\.venv\Scripts\python.exe" scripts/generate_sbom.py --verify inventory/sbom.spdx.json --license-dir inventory/third_party_licenses
```

Use a fresh output directory for a different inventory. Generation reads the current interpreter and checkout without installing packages or making network requests; verification checks that same environment/source and the exported licence hashes. Each Windows CI job generates and verifies a fresh inventory and saves it with the test artifacts. The checked-in snapshot retains the revision and environment that generated it.

## Data files derived from RealityScan

These files are in the repository and are in RealityScan's own formats.

- `flightlogs.xml` - the pipeline's 13-column flight-log import format. It is a
  flight-log format definition in the structure of RealityScan's shipped
  `flightlogs.xml`, kept so the entry can be merged into the RealityScan
  installation's flight-log dictionary. It is derived from RealityScan's file.
- `sensorsdb.xml` - the camera sensor database retained for existing workflows,
  in RealityScan's camera/lens schema. This is reference data, not a Wild Scan
  executable or a replacement for the user's RealityScan installation.
- `modules/realityscan_interface/RS_CLI/Metadata/*.xml` - RealityScan
  parameter-configuration files (alignment, simplify, smoothing, texturing,
  unwrap and export settings, flight-log import) passed to RealityScan on its
  command line. They use RealityScan's configuration schema and setting keys.

Wild Scan makes no statement here about the terms under which these files may
be redistributed; they remain subject to Epic Games' terms for RealityScan.

## Documentation derived from RealityScan

- `docs/rs-reference/` - a reference for RealityScan 2.2's command line
  written for this project. It paraphrases and cites RealityScan 2.2's
  documentation and command-line help (Epic Games, Inc.) and reproduces
  command names, setting keys, option names and short excerpts of
  RealityScan's shipped schema and sample files where they define the
  interface. It contains no RealityScan binaries or copied program files.
  It is not Epic Games' documentation; this file makes no statement about
  redistribution rights in Epic Games' material beyond the above.
