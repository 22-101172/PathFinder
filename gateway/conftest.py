"""
conftest.py — pytest path configuration for the gateway package.

Adds the repository root to sys.path before any test import runs, so tests can
import `gateway.*` and `engines.*` using the repo-root package layout.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
