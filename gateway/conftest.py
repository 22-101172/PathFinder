"""
conftest.py — pytest path configuration for the gateway package.

Adds gateway/ to sys.path before any test import runs, so test files can
do `from student_context_provider import ...` without package prefixes.
This mirrors how the gateway process itself resolves imports at runtime.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
