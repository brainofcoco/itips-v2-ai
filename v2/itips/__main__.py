"""Entry point: `python -m itips`."""

from __future__ import annotations

import sys

from dotenv import load_dotenv

# Load .env BEFORE importing anything that reads settings, because the
# settings dataclasses snapshot env vars at import time.
load_dotenv()


def main() -> int:
    from itips.app import run

    return run()


if __name__ == "__main__":
    sys.exit(main())
