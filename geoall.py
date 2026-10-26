"""Compatibility entry point for georeference_survey.py."""
import sys

import georeference_survey as _implementation

if __name__ == "__main__":
    sys.exit(_implementation.main())
else:
    sys.modules[__name__] = _implementation
