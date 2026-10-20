#!/usr/bin/env python3
"""
Validate ~/i446-monorepo/config/tasks.json against the live Neon spreadsheet.

Catches:
- Domain.fen_header pointing to a column that doesn't exist on 0分
- Habit.neon_header pointing to a column that doesn't exist on 0n / 1n+
- Habit.domain not registered
- Alias collisions across habits

Run after editing tasks.json by hand, or after regen-neon-cols.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / "i446-monorepo/lib"))
import registry

errs = registry.validate()
if not errs:
    print("Registry consistent with live spreadsheet ✓")
    sys.exit(0)
print(f"{len(errs)} inconsistenc{'y' if len(errs) == 1 else 'ies'}:")
for e in errs:
    print(f"  ✗ {e}")
sys.exit(1)
