#Requires -Version 5.1
<#
.SYNOPSIS
    Builds libcasadi_conic_highs against HiGHS using MSYS2 MinGW64.

.DESCRIPTION
    Windows local development build of the CasADi HiGHS plugin.

    The CasADi wheel is built with MinGW/GCC, so the plugin must also be
    built with MinGW to share the same C++ ABI.  This script delegates the
    actual build to build_highs_plugin.sh running inside MSYS2 MinGW64, after
    installing the matching HiGHS package from the MSYS2 package database.

    WARNING -- NOT ABI-compatible with the official casadi PyPI wheel.
    MSYS2's MinGW64 toolchain (GCC ~16.x at time of writing) does not match
    the GCC 11.2.0 dockcross/MXE toolchain CasADi's own CI uses to build its
    published Windows wheels. A plugin built by this script will only load
    reliably against a CasADi also built/installed via MSYS2 -- NOT against
    `pip install casadi`. This caused a real, silently-broken plugin DLL
    earlier in this project's history (wrong GCC ABI, fell back to CasADi's
    bundled HiGHS instead of failing loudly). The dockcross-based build in
    `.github/workflows/build_highs.yml` (`build-windows` job) is the only
    build path guaranteed to match the wheels most users actually install;
    use this script only when developing/testing against a fully
    MSYS2-based CasADi environment, never to produce a distributable wheel.

    Prerequisites:

      1. MSYS2 (https://www.msys2.org/)
         Install the x86_64 release and run the initial update:
           pacman -Syu
         The script installs the HiGHS MinGW package automatically via pacman;
         no manual package installation is needed beyond the base MSYS2 setup.

      2. Python virtual environment with casadi
         Activate the project venv before running this script:
           .\.venv\Scripts\Activate.ps1
         The venv must have casadi installed (uv sync satisfies this).

    Output: <WorkDir>\plugin-install-highs<V>-casadi<V>\lib\libcasadi_conic_highs.dll

.PARAMETER HighsVersion
    HiGHS release to build against. Must match the version available in the
    MSYS2 mingw-w64-x86_64-highs package. Defaults to 1.15.1.

.PARAMETER WorkDir
    Scratch directory for the build. Defaults to .\ci-work.

.PARAMETER Msys2Root
    Path to the MSYS2 installation root. Defaults to C:\msys64.

.EXAMPLE
    .\build_highs_plugin.ps1

.EXAMPLE
    .\build_highs_plugin.ps1 -WorkDir C:\tmp\ci-work
#>
param(
    [string] $HighsVersion = "1.15.1",
    [string] $WorkDir      = "",
    [string] $Msys2Root    = "C:\msys64"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Bash = Join-Path $Msys2Root "usr\bin\bash.exe"

if (-not (Test-Path $Bash)) {
    Write-Error "MSYS2 bash not found at $Bash. Install MSYS2 from https://www.msys2.org/"
    exit 1
}

# ---------------------------------------------------------------------------
# Detect Python and CasADi version
# ---------------------------------------------------------------------------

$Python = (Get-Command python -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue)
if (-not $Python) {
    Write-Error "python not found on PATH. Activate your venv or install Python."
    exit 1
}

$CasadiVersion = & $Python -c "import casadi; print(casadi.__version__)"
if ($LASTEXITCODE -ne 0) {
    Write-Error "Could not import casadi. Is the venv activated?"
    exit 1
}

$CasadiRoot = & $Python -c "import casadi, os; print(os.path.dirname(casadi.__file__))"
if ($LASTEXITCODE -ne 0) {
    Write-Error "Could not locate casadi package directory."
    exit 1
}

if (-not $WorkDir) {
    $WorkDir = Join-Path (Get-Location) "ci-work"
}
$WorkDir = [System.IO.Path]::GetFullPath($WorkDir)

Write-Host "==> CasADi $CasadiVersion at $CasadiRoot"
Write-Host "==> HiGHS $HighsVersion (from MSYS2 mingw64 package)"
Write-Host "==> MSYS2 at $Msys2Root"
Write-Host "==> Work dir: $WorkDir"

# ---------------------------------------------------------------------------
# Install required MSYS2 MinGW64 packages
# ---------------------------------------------------------------------------

# All build tools are installed unconditionally with --needed so this is
# idempotent: a no-op when already present, installs when missing.
Write-Host "==> Installing MSYS2 MinGW64 build tools via pacman..."
$env:MSYSTEM = "MINGW64"
& $Bash -lc "pacman -S --noconfirm --needed mingw-w64-x86_64-git mingw-w64-x86_64-cmake mingw-w64-x86_64-ninja mingw-w64-x86_64-gcc mingw-w64-x86_64-highs 2>&1"
if ($LASTEXITCODE -ne 0) {
    Write-Error "pacman failed. Check your MSYS2 installation."
    exit 1
}

# ---------------------------------------------------------------------------
# Verify installed HiGHS version matches requested
# ---------------------------------------------------------------------------

$env:MSYSTEM = "MINGW64"
$installedVersion = (& $Bash -lc "pacman -Q mingw-w64-x86_64-highs 2>/dev/null | awk '{print `$2}' | cut -d- -f1").Trim()
if ($installedVersion -and $installedVersion -ne $HighsVersion) {
    Write-Host "WARNING: Requested HiGHS $HighsVersion but MSYS2 has $installedVersion."
    Write-Host "         Proceeding with installed version $installedVersion."
    $HighsVersion = $installedVersion
}

# ---------------------------------------------------------------------------
# Convert paths to MSYS2 POSIX format for use in bash
# ---------------------------------------------------------------------------

function ConvertTo-PosixPath([string]$path) {
    # C:\foo\bar -> /c/foo/bar
    $path = $path.Replace('\', '/')
    if ($path -match '^([A-Za-z]):(.*)') {
        $drive = $Matches[1].ToLower()
        $rest  = $Matches[2]
        return "/$drive$rest"
    }
    return $path
}

$PosixScriptDir  = ConvertTo-PosixPath $ScriptDir
$PosixWorkDir    = ConvertTo-PosixPath $WorkDir
$PosixCasadiRoot = ConvertTo-PosixPath $CasadiRoot

# ---------------------------------------------------------------------------
# Write a build helper script and execute it under MSYS2 bash
# ---------------------------------------------------------------------------

$helperScript = Join-Path $WorkDir "build_plugin_helper.sh"
New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null

$helperContent = @"
#!/bin/bash
set -euo pipefail
export PATH="/mingw64/bin:/usr/bin:`$PATH"
export PATH="$PosixCasadiRoot/../../../Scripts:`$PATH"

WORK_DIR="$PosixWorkDir"
CASADI_ROOT="$PosixCasadiRoot"
HIGHS_ROOT="/mingw64"

SCRIPT_DIR="$PosixScriptDir"
HIGHS_VERSION="$HighsVersion"
CASADI_VERSION="`$(python -c 'import casadi; print(casadi.__version__)')"

CASADI_SRC_DIR="`${WORK_DIR}/casadi-src-`${CASADI_VERSION}"
PLUGIN_BUILD_DIR="`${WORK_DIR}/plugin-build-highs`${HIGHS_VERSION}-casadi`${CASADI_VERSION}"
PLUGIN_INSTALL_DIR="`${WORK_DIR}/plugin-install-highs`${HIGHS_VERSION}-casadi`${CASADI_VERSION}"

# Clone CasADi source
echo "==> Cloning CasADi `${CASADI_VERSION} source..."
if [ ! -d "`${CASADI_SRC_DIR}/.git" ]; then
    remote="https://github.com/casadi/casadi.git"
    ls_out=`$(git ls-remote --tags "`${remote}" "refs/tags/`${CASADI_VERSION}" "refs/tags/v`${CASADI_VERSION}" 2>&1) \
        || { echo "ERROR: git ls-remote failed (network issue?): `${ls_out}" >&2; exit 1; }
    if printf '%s\n' "`${ls_out}" | awk -v t="refs/tags/`${CASADI_VERSION}" '`$2==t{found=1} END{exit !found}'; then
        casadi_tag="`${CASADI_VERSION}"
    elif printf '%s\n' "`${ls_out}" | awk -v t="refs/tags/v`${CASADI_VERSION}" '`$2==t{found=1} END{exit !found}'; then
        casadi_tag="v`${CASADI_VERSION}"
    else
        echo "ERROR: No CasADi tag found for version '`${CASADI_VERSION}'" \
             "(tried '`${CASADI_VERSION}' and 'v`${CASADI_VERSION}')" >&2
        exit 1
    fi
    git clone --depth 1 --branch "`${casadi_tag}" "`${remote}" "`${CASADI_SRC_DIR}"
fi

# Compatibility check
echo "==> Checking CasADi/HiGHS API compatibility..."
set +e
python "`${SCRIPT_DIR}/check_compatibility.py" \
    --casadi-src-dir "`${CASADI_SRC_DIR}" \
    --highs-dir      "`${HIGHS_ROOT}" \
    --casadi-root    "`${CASADI_ROOT}"
compat_exit=`$?
set -e
if [ `${compat_exit} -eq 1 ]; then
    echo "ERROR: API compatibility check failed -- aborting build." >&2
    exit 1
fi

# CMake build
echo "==> Building CasADi HiGHS plugin..."
mkdir -p "`${PLUGIN_BUILD_DIR}"

if [ ! -f "`${PLUGIN_BUILD_DIR}/CMakeCache.txt" ]; then
    cmake \
        -G Ninja \
        -S "`${SCRIPT_DIR}" \
        -B "`${PLUGIN_BUILD_DIR}" \
        -DHIGHS_ROOT="`${HIGHS_ROOT}" \
        -DCASADI_SRC_DIR="`${CASADI_SRC_DIR}" \
        -DCASADI_ROOT="`${CASADI_ROOT}" \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX="`${PLUGIN_INSTALL_DIR}"
fi

cmake --build "`${PLUGIN_BUILD_DIR}" --config Release
cmake --install "`${PLUGIN_BUILD_DIR}" --config Release

# Copy HiGHS DLL alongside the plugin
PLUGIN_LIB_DIR="`${PLUGIN_INSTALL_DIR}/lib"
for dll in /mingw64/bin/libhighs.dll /mingw64/bin/highs.dll; do
    if [ -f "`${dll}" ]; then
        echo "    Copying `$(basename `${dll})"
        cp "`${dll}" "`${PLUGIN_LIB_DIR}/"
    fi
done

echo ""
echo "==> Plugin built successfully."
echo "    RTCTOOLS_EXTRA_CASADIPATH=`${PLUGIN_LIB_DIR}"
echo ""
echo "    To run the end-to-end test:"
echo "      uv run pytest tests/optimization/test_highs_plugin_e2e.py -v"
echo ""

# Write Windows-style path for PowerShell callers
win_path="`$(cygpath -w "`${PLUGIN_LIB_DIR}")"
printf '%s' "`${win_path}" > "`${WORK_DIR}/plugin-lib-path.txt"
"@

[System.IO.File]::WriteAllText($helperScript, $helperContent, (New-Object System.Text.UTF8Encoding $false))

Write-Host "==> Running build inside MSYS2 MinGW64..."
$env:MSYSTEM = "MINGW64"
& $Bash -l (ConvertTo-PosixPath $helperScript)
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# ---------------------------------------------------------------------------
# Report the install path
# ---------------------------------------------------------------------------

$pathFile = Join-Path $WorkDir "plugin-lib-path.txt"
if (Test-Path $pathFile) {
    $PluginLibDir = (Get-Content $pathFile -Raw).Trim()
    Write-Host ""
    Write-Host "==> Plugin built successfully."
    Write-Host "    Plugin DLL directory: $PluginLibDir"
    Write-Host ""
    Write-Host "    To use the plugin, set:"
    Write-Host "      `$env:RTCTOOLS_EXTRA_CASADIPATH='$PluginLibDir'"
    Write-Host ""
    Write-Host "    To run the end-to-end test:"
    Write-Host "      `$env:RTCTOOLS_EXTRA_CASADIPATH='$PluginLibDir'"
    Write-Host "      uv run pytest tests/optimization/test_highs_plugin_e2e.py -v"
}
