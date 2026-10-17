"""Entry point: python -m runtime <change-request.json> <product_root>."""

import sys

from .runtime import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
