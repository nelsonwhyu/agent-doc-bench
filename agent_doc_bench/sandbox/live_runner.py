"""Entry point run inside the sandbox subprocess for live-mode BLPAPI execution.

Installs blpapi_live_shim's instrumentation before running the generated
script, and always flushes the resulting metadata (event types, message
counts, elapsed time) — even if the script raises — so execution_scorer.py
never needs the script's own stdout/stderr, which may contain real market
data, to know what happened.
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from blpapi_live_shim import install  # noqa: E402

_HERE = Path(__file__).parent
_METADATA_PATH = _HERE / "_bench_metadata.json"

recorder = install(_METADATA_PATH)
try:
    runpy.run_path(str(_HERE / "generated.py"), run_name="__main__")
finally:
    recorder.flush(_METADATA_PATH)
