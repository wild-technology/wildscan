"""Compatibility entry point for poses_to_flight_log.py."""
import sys

import poses_to_flight_log as _implementation

if __name__ == "__main__":
    sys.exit(_implementation.main())
else:
    sys.modules[__name__] = _implementation
