import os
import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT   = Path(__file__).parent
PLUGIN_DIR  = REPO_ROOT / "ci-work" / "plugin-install-highs1.15.1-casadi3.7.2" / "lib"
EXAMPLE_DIR = REPO_ROOT / "examples" / "pumped_hydropower_system"

if sys.platform == "win32":
    import ctypes
    MSYS2_BIN = Path(os.environ.get("MSYS2_ROOT", r"C:\msys64")) / "mingw64" / "bin"
    ctypes.WinDLL(str(MSYS2_BIN / "zlib1.dll"))
    ctypes.WinDLL(str(MSYS2_BIN / "libwinpthread-1.dll"))
    ctypes.WinDLL(str(MSYS2_BIN / "libgcc_s_seh-1.dll"))
    ctypes.WinDLL(str(PLUGIN_DIR / "libhighs.dll"))

os.environ["RTCTOOLS_EXTRA_CASADIPATH"] = str(PLUGIN_DIR)

sys.path.insert(0, str(EXAMPLE_DIR / "src"))

from rtctools.util import run_optimization_problem

with patch("rtctools.util.run_optimization_problem"):
    from example import PumpStorage

run_optimization_problem(PumpStorage, base_folder=str(EXAMPLE_DIR))
