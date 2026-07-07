"""
rtctools_highs - CasADi HiGHS solver plugin for rtc-tools.

Importing this module prepends the plugin directory to CasADi's plugin search
path, making the HiGHS solver available without manual configuration.

On Windows the wheel is processed by ``delvewheel`` at build time, which
renames transitive DLL dependencies (e.g. ``libhighs-<hash>.dll``) to avoid
conflicts with CasADi's bundled copies. The renamed DLLs land in
``rtctools_highs.libs/``, which is prepended to PATH at import time so that
CasADi's ``LoadLibrary()`` can find them. Not intended for use on macOS.
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

# Set at build time to reflect the HiGHS version this wheel was compiled against.
# TODO: read directly from the compiled plugin binary instead of a source literal
# (tracked as follow-up work, to eliminate the last build-time-injected constant).
__highs_version__ = "1.15.1"

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
    _libs_dir = str(_plugin_dir.parent / "rtctools_highs.libs")
    if os.path.isdir(_libs_dir):
        # CasADi 3.7.x resolves transitive DLL deps via PATH (plain LoadLibrary).
        # Superseded by CasADi #4340 (3.8+), which switches to LoadLibraryEx +
        # LOAD_LIBRARY_SEARCH_USER_DIRS — at that point add_dll_directory() suffices.
        os.environ["PATH"] = os.pathsep.join([_libs_dir, os.environ["PATH"]])
    _dll_dir = os.add_dll_directory(str(_plugin_dir))  # handle must stay alive
elif sys.platform == "linux":
    # Prime glibc's linker cache with our plugin before importing casadi.
    # CasADi caches plugins on first dlopen() and silently falls through to
    # the next search path on failure — without this, its bundled HiGHS 1.10.0
    # wins instead of ours.
    import ctypes as _ctypes

    _plugin_so = _plugin_dir / "libcasadi_conic_highs.so"
    _RTLD_NOW = getattr(_ctypes, "RTLD_NOW", None) or getattr(os, "RTLD_NOW", 2)
    try:
        _ctypes.CDLL(str(_plugin_so), mode=_RTLD_NOW)
    except OSError as _e:
        raise ImportError(
            f"rtctools_highs: cannot load {_plugin_so}: {_e}. "
            "A transitive dependency (libcasadi.so.3.7, libz.so.1, libgomp.so.1, "
            "libstdc++, etc.) could not be resolved. "
            "Install the matching casadi wheel first, or check that all system "
            "libraries required by the wheel are present."
        ) from _e

import casadi  # noqa: E402

# Never stale by construction: reflects whatever casadi package is actually installed.
__casadi_version__ = casadi.__version__

_current_parts = [p for p in casadi.GlobalOptions.getCasadiPath().split(os.pathsep) if p]
_plugin_dir_str = str(_plugin_dir)
if os.path.normcase(os.path.normpath(_plugin_dir_str)) not in {
    os.path.normcase(os.path.normpath(p)) for p in _current_parts
}:
    casadi.GlobalOptions.setCasadiPath(
        os.pathsep.join([_plugin_dir_str] + _current_parts)
    )
