#!/usr/bin/env python3
"""Health command for the autofix-selftest lane: exit 0 when value.txt says ok."""
import sys
from pathlib import Path

sys.exit(0 if (Path(__file__).parent / "value.txt").read_text().strip() == "ok" else 1)
