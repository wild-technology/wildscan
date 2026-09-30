"""Compatibility entry point for decimate_images.py."""
import sys

import decimate_images as _implementation

if __name__ == "__main__":
    try:
        sys.exit(_implementation.main())
    except KeyboardInterrupt:
        print("\n\nOperation cancelled by user")
        sys.exit(130)
    except Exception as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        sys.exit(1)
else:
    sys.modules[__name__] = _implementation
