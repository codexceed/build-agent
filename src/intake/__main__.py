"""Console entrypoint for the intake package.

A real CLI is wired in a later slice; for now this confirms the package runs.
"""

import logging

from intake import __version__


def main() -> None:
    """Log the package version and exit."""
    logging.basicConfig(level=logging.INFO)
    logging.getLogger(__name__).info("intake %s — no CLI yet (slice 1)", __version__)


if __name__ == "__main__":
    main()
