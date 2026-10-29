"""Launcher so the CLI works without installing the package.

Usage (from an Anaconda Prompt, any folder):
    python scripts/run.py init-db
    python scripts/run.py run --source fixture
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from oekokonto_harvester.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
