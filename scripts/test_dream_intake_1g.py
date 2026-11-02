#!/usr/bin/env python3
"""weekly_goals_1g parses the narrative's week blocks."""
import datetime
import importlib.util
import sys
import tempfile
from pathlib import Path

sys.argv = ["x"]
spec = importlib.util.spec_from_file_location("intake", Path(__file__).with_name("dream-intake.py"))
intake = importlib.util.module_from_spec(spec)
spec.loader.exec_module(intake)

DOC = """## 2026-10 (October)

### Weekly arc

- **Oct 11 – Oct 17** —
\t- i9 
\t\t- 2000分
- **Oct 18 – Oct 24** —
\t- i9
\t\t- 2500分 
\t- epcn 
\t\t- 1 friend
 - **Oct 25 – Oct 31** —

### Themes

-
"""


def test_current_and_empty_next():
    d = Path(tempfile.mkdtemp())
    (d / "2026.10 2g+1g-narrative.md").write_text(DOC)
    r = intake._weekly_goals_1g(today=datetime.date(2026, 10, 23), root=d)
    assert r["current"]["start"] == "2026-10-18"
    assert any("2500" in l for l in r["current"]["lines"])
    assert not any("Themes" in l for l in r["current"]["lines"])
    assert r["next"]["start"] == "2026-10-25" and r["next_is_empty"] is True


if __name__ == "__main__":
    test_current_and_empty_next()
    print("1 passed")
