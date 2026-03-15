import os
import sys
from pathlib import Path

# WeasyPrint needs Homebrew shared libs (pango, cairo) on macOS.
if sys.platform == "darwin" and not os.environ.get("DYLD_FALLBACK_LIBRARY_PATH"):
    _brew_lib = Path("/opt/homebrew/lib")
    if _brew_lib.exists():
        os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = str(_brew_lib)
