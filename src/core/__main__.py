"""Support ``python -m core`` as an alias for the ``ossia`` CLI.

Usage:
    python -m core              Start backend + TUI
    python -m core --help       Show help
"""

import sys

from core.cli import main

if __name__ == "__main__":
    sys.exit(main())
