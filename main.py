"""Main utilities for the Geo Insulation blog agent."""

from __future__ import annotations

import logging
import os

from geo_blog.cli import main

if __name__ == "__main__":
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO)
    main()
