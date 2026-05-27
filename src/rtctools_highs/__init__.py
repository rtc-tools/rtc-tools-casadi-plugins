"""
rtctools_highs - CasADi HiGHS solver plugin for rtc-tools.

Importing this module prepends the plugin directory to CasADi's plugin search
path, making the HiGHS solver available without manual configuration.

On Windows the wheel is processed by ``delvewheel`` at build time, which
renames transitive DLL dependencies (e.g. ``libhighs-<hash>.dll``) to avoid
conflicts with CasADi's bundled copies and injects a loader shim into this
file. Not intended for use on macOS.
"""
import os
import sys
from importlib.metadata import PackageNotFoundError as _PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path

try:
    __version__ = _pkg_version("rtctools-highs")
except _PackageNotFoundError:
    __version__ = "unknown"

# These are set at build time and reflect the versions this wheel was built against.
# Single source of truth: pyproject.toml (via hatchling build hooks or manual bump).
__highs_version__ = "1.14.0"
__casadi_version__ = "3.7.2"

if sys.platform == "darwin":
    raise ImportError(
        "rtctools_highs does not provide a macOS wheel; "
        "the CasADi bundled HiGHS will be used instead."
    )

_plugin_dir = Path(__file__).parent

_suffix = {"win32": ".dll", "linux": ".so"}.get(sys.platform, ".so")
if not any(_plugin_dir.glob(f"libcasadi_conic_highs*{_suffix}")):
    raise ImportError(
        f"rtctools_highs is installed but the compiled plugin binary was not found in "
        f"{_plugin_dir}. The package may have been installed from source without building "
        f"the plugin. Install a binary wheel instead."
    )

if sys.platform == "win32":
    # delvewheel injects a loader shim at the top of this file at repair time;
    # the shim covers rtctools_highs.libs/ (renamed transitive deps, e.g.
    # libhighs-<hash>.dll) via both PATH and os.add_dll_directory.
    # This call covers the package directory itself (libcasadi_conic_highs.dll)
    # for any future CasADi that uses LoadLibraryEx with user-dirs search.
    _dll_dir = os.add_dll_directory(str(_plugin_dir))  # handle must stay alive

import casadi  # noqa: E402

_current_parts = [p for p in casadi.GlobalOptions.getCasadiPath().split(os.pathsep) if p]
_plugin_dir_str = str(_plugin_dir)
if os.path.normcase(os.path.normpath(_plugin_dir_str)) not in {
    os.path.normcase(os.path.normpath(p)) for p in _current_parts
}:
    casadi.GlobalOptions.setCasadiPath(
        os.pathsep.join([_plugin_dir_str] + _current_parts)
    )
