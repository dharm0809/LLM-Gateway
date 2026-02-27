# Ensure walacor-core is on path when running tests from repo (e.g. PYTHONPATH=Gateway/src pytest Gateway/tests)
import sys
from pathlib import Path

_root = Path(__file__).resolve().parents[2]  # Walcor repo root when tests are in Gateway/tests
_walacor_core_src = _root / "walacor-core" / "src"
if _walacor_core_src.exists() and str(_walacor_core_src) not in sys.path:
    sys.path.insert(0, str(_walacor_core_src))
