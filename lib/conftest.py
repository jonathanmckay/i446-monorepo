"""Keep the -1g prefill rejection state out of ~ during tests.

neon_blocks.zerog_prefill() consults ~/.local/state/jm/1g-prefill-rejects.json
by default and the -1g form writes to it; without this redirect a run of the
form-driving tests would record real rejections and the untouched-prefill
tests would start seeing the next goal (2026-11-09). Set at import so every
subprocess inherits it; neon_blocks reads the env var at call time.
"""
import os
import tempfile

os.environ.setdefault("JM_1G_PREFILL_REJECTS",
                      os.path.join(tempfile.mkdtemp(prefix="jm-test-1g-rejects-"), "rejects.json"))
