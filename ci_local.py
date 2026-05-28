"""
Reproduce the Windows CI build steps locally.

Usage:
    python ci_local.py [--skip-build]

--skip-build: skip the cmake build and staging steps (use if already built).
"""
import argparse
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).parent
DIST = ROOT / "dist"
REPAIRED = DIST / "repaired"
SRC = ROOT / "src" / "rtctools_highs"

MSYS2_BIN = Path(os.environ.get("MSYS2_ROOT", r"C:\msys64")) / "mingw64" / "bin"


def run(cmd, **kwargs):
    print(f"\n>>> {cmd if isinstance(cmd, str) else ' '.join(str(c) for c in cmd)}")
    result = subprocess.run(cmd, shell=isinstance(cmd, str), **kwargs)
    if result.returncode != 0:
        print(f"FAILED (exit {result.returncode})")
        sys.exit(result.returncode)


def clean():
    for f in DIST.glob("*.whl"):
        f.unlink()
    for f in SRC.glob("*.dll"):
        f.unlink()
    if REPAIRED.exists():
        shutil.rmtree(REPAIRED)
    print("Cleaned dist/ and staged DLLs.")


def build_plugin():
    env = os.environ.copy()
    env["PATH"] = str(MSYS2_BIN) + os.pathsep + str(ROOT / ".venv" / "Scripts") + os.pathsep + env.get("PATH", "")
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        print("ERROR: pwsh/powershell not found on PATH")
        sys.exit(1)
    run([pwsh, "-Command", ". .venv\\Scripts\\Activate.ps1; & pwsh src/casadi_conic_highs/build_highs_plugin.ps1"], env=env)


def _plugin_dir() -> Path:
    marker = ROOT / "ci-work" / "plugin-lib-path.txt"
    if not marker.exists():
        print(f"ERROR: {marker} not found — run without --skip-build first")
        sys.exit(1)
    return Path(marker.read_text().strip())


def stage_artifacts():
    plugin_dir = _plugin_dir()
    dlls = list(plugin_dir.glob("libcasadi_conic_highs*.dll"))
    if not dlls:
        print(f"ERROR: no libcasadi_conic_highs*.dll in {plugin_dir}")
        sys.exit(1)
    for dll in dlls:
        shutil.copy(dll, SRC / dll.name)
        print(f"Staged: {dll.name}")


def build_wheel():
    run(["uv", "build", "--wheel"])


def repair_wheel():
    plugin_dir = _plugin_dir()
    if not MSYS2_BIN.exists():
        print(f"ERROR: MSYS2 MinGW64 bin not found at {MSYS2_BIN}")
        sys.exit(1)
    whl = next(DIST.glob("*.whl"))
    REPAIRED.mkdir(parents=True, exist_ok=True)
    run([
        "uv", "run", "delvewheel", "repair",
        "--analyze-existing",
        "--exclude", "libcasadi.dll",
        "--add-path", str(plugin_dir),
        "--add-path", str(MSYS2_BIN),
        "--wheel-dir", str(REPAIRED),
        str(whl),
    ])
    whl.unlink()
    repaired = next(REPAIRED.glob("*.whl"))
    shutil.move(str(repaired), str(DIST / repaired.name))
    shutil.rmtree(REPAIRED)


def retag_wheel():
    whl = next(DIST.glob("*.whl"))
    run([
        "uv", "run", "python", "-m", "wheel", "tags",
        "--python-tag", "cp310.cp311.cp312.cp313.cp314",
        "--abi-tag", "none",
        "--platform-tag", "win_amd64",
        "--remove", str(whl),
    ])


def verify_wheel():
    whl = next(DIST.glob("*win_amd64*.whl"))
    names = zipfile.ZipFile(whl).namelist()
    basenames = [Path(n).name for n in names]

    renamed = [n for n in basenames if n.startswith("libhighs-")]
    bare = [n for n in basenames if n == "libhighs.dll"]
    casadi = [n for n in basenames if n == "libcasadi.dll"]

    ok = True
    if not renamed:
        print("FAIL: libhighs-<hash>.dll not found — delvewheel rename may have been skipped")
        ok = False
    if bare:
        print(f"FAIL: un-renamed libhighs.dll found: {bare}")
        ok = False
    if casadi:
        print(f"FAIL: libcasadi.dll must not be vendored: {casadi}")
        ok = False
    if not ok:
        sys.exit(1)
    print(f"OK: libhighs renamed correctly -> {renamed}")


def install_and_test():
    whl = next(DIST.glob("*win_amd64*.whl"))
    # Strip MSYS2 from PATH to verify the wheel is self-contained.
    env = os.environ.copy()
    env["PATH"] = os.pathsep.join(
        p for p in env.get("PATH", "").split(os.pathsep)
        if "msys" not in p.lower() and "mingw" not in p.lower()
    )
    venv_python = ROOT / ".venv" / "Scripts" / "python.exe"
    run(["uv", "pip", "install", str(whl), "rtc-tools"], env=env)
    rtc_src = ROOT / "rtc-tools-src"
    if not rtc_src.exists():
        rtc_version = subprocess.check_output(
            ["uv", "run", "python", "-c", "import rtctools; print(rtctools.__version__)"],
            text=True, env=env
        ).strip()
        run(["git", "clone", "--depth", "1", "--branch", rtc_version,
             "https://github.com/rtc-tools/rtc-tools.git", str(rtc_src)], env=env)
    env["RTCTOOLS_EXAMPLE_DIR"] = str(rtc_src / "examples" / "pumped_hydropower_system")
    run([str(venv_python), "-m", "pytest", "tests/", "tests/e2e/", "-v"], env=env)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-cmake", action="store_true",
                        help="Skip cmake build; re-stage from existing ci-work output.")
    args = parser.parse_args()

    clean()
    if not args.skip_cmake:
        build_plugin()
    stage_artifacts()
    build_wheel()
    repair_wheel()
    retag_wheel()
    verify_wheel()
    install_and_test()
    print("\nAll CI steps passed.")


if __name__ == "__main__":
    main()
