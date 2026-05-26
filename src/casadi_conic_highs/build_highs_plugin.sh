#!/usr/bin/env bash
# Builds libcasadi_conic_highs against a pre-built HiGHS release binary.
#
# Usage:
#   tools/casadi_plugins/highs/build_highs_plugin.sh [HIGHS_VERSION]
#
# HIGHS_VERSION defaults to the value pinned below. Override to test other
# combinations. The CasADi version is always auto-detected from the active
# Python interpreter so the source clone matches the installed wheel exactly.
#
# Output: <WORK_DIR>/plugin-install-highs<V>-casadi<V>/lib/libcasadi_conic_highs.so (Linux)
#                                                      libcasadi_conic_highs.dll (Windows)
#
# Environment variables respected:
#   WORK_DIR     Scratch directory for downloads and builds. Default: ./ci-work
#   CASADI_ROOT  Path to CasADi install. Auto-detected from the active Python
#                interpreter if unset (matches CMakeLists.txt behaviour).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HIGHS_VERSION="${1:-1.14.0}"

# Always detect from the wheel — never hardcode — so the source clone matches.
CASADI_VERSION="$(python -c 'import casadi; print(casadi.__version__)')"

WORK_DIR="${WORK_DIR:-$(pwd)/ci-work}"
# Version-stamped subdirectories prevent stale cache hits when versions change.
HIGHS_DIR="${WORK_DIR}/highs-${HIGHS_VERSION}"
CASADI_SRC_DIR="${WORK_DIR}/casadi-src-${CASADI_VERSION}"
PLUGIN_BUILD_DIR="${WORK_DIR}/plugin-build-highs${HIGHS_VERSION}-casadi${CASADI_VERSION}"
PLUGIN_INSTALL_DIR="${WORK_DIR}/plugin-install-highs${HIGHS_VERSION}-casadi${CASADI_VERSION}"

OS="$(uname -s)"

echo "==> Downloading HiGHS ${HIGHS_VERSION}..."
mkdir -p "${HIGHS_DIR}"

if [[ "${OS}" == "Linux" ]]; then
    HIGHS_ARCHIVE="highs-${HIGHS_VERSION}-x86_64-linux-gnu-static-apache.tar.gz"
elif [[ "${OS}" == "Darwin" ]]; then
    HIGHS_ARCHIVE="highs-${HIGHS_VERSION}-arm-apple-static-apache.tar.gz"
else
    # Windows (Git Bash / MSYS2)
    HIGHS_ARCHIVE="highs-${HIGHS_VERSION}-x86_64-windows-mit.zip"
fi

HIGHS_URL="https://github.com/ERGO-Code/HiGHS/releases/download/v${HIGHS_VERSION}/${HIGHS_ARCHIVE}"
HIGHS_ARCHIVE_PATH="${WORK_DIR}/${HIGHS_ARCHIVE}"

if [[ ! -f "${HIGHS_ARCHIVE_PATH}" ]]; then
    curl -fsSL -o "${HIGHS_ARCHIVE_PATH}" "${HIGHS_URL}"
fi

if [[ ! -d "${HIGHS_DIR}/include" ]]; then
    if [[ "${HIGHS_ARCHIVE}" == *.zip ]]; then
        unzip -q -o "${HIGHS_ARCHIVE_PATH}" -d "${HIGHS_DIR}"
    else
        tar -xzf "${HIGHS_ARCHIVE_PATH}" -C "${HIGHS_DIR}"
    fi
fi

echo "==> Cloning CasADi ${CASADI_VERSION} source..."
if [[ ! -d "${CASADI_SRC_DIR}/.git" ]]; then
    _casadi_remote="https://github.com/casadi/casadi.git"
    # Fetch both bare and v-prefixed tag in one round-trip.
    # -F: literal match; $: anchored so 3.7.2 cannot match 3.7.20.
    _ls=""
    _ls=$(git ls-remote --tags "${_casadi_remote}" \
        "refs/tags/${CASADI_VERSION}" "refs/tags/v${CASADI_VERSION}" 2>&1) \
        || { echo "ERROR: git ls-remote failed (network issue?): ${_ls}" >&2; exit 1; }
    if echo "${_ls}" | grep -qF "refs/tags/${CASADI_VERSION}$"; then
        CASADI_TAG="${CASADI_VERSION}"
    elif echo "${_ls}" | grep -qF "refs/tags/v${CASADI_VERSION}$"; then
        CASADI_TAG="v${CASADI_VERSION}"
    else
        echo "ERROR: No CasADi tag found for version '${CASADI_VERSION}'" \
             "(tried '${CASADI_VERSION}' and 'v${CASADI_VERSION}')" >&2
        exit 1
    fi
    git clone --depth 1 --branch "${CASADI_TAG}" "${_casadi_remote}" "${CASADI_SRC_DIR}"
fi

echo "==> Checking CasADi/HiGHS API compatibility..."
_casadi_root="${CASADI_ROOT:-$(python -c 'import casadi, os; print(os.path.dirname(casadi.__file__))')}"
set +e
python "${SCRIPT_DIR}/check_compatibility.py" \
    --casadi-src-dir "${CASADI_SRC_DIR}" \
    --highs-dir      "${HIGHS_DIR}" \
    --casadi-root    "${_casadi_root}"
_compat_exit=$?
set -e
if [[ ${_compat_exit} -eq 1 ]]; then
    echo "ERROR: API compatibility check failed — aborting build." >&2
    exit 1
fi
# Exit 2 means interface diff warning; build continues.

echo "==> Building CasADi HiGHS plugin..."
mkdir -p "${PLUGIN_BUILD_DIR}"

CMAKE_ARGS=(
    -S "${SCRIPT_DIR}"
    -B "${PLUGIN_BUILD_DIR}"
    -DHIGHS_ROOT="${HIGHS_DIR}"
    -DCASADI_SRC_DIR="${CASADI_SRC_DIR}"
    -DCMAKE_BUILD_TYPE=Release
    -DCMAKE_INSTALL_PREFIX="${PLUGIN_INSTALL_DIR}"
)

# Provide CASADI_ROOT explicitly if already known; otherwise let CMake auto-detect.
if [[ -n "${CASADI_ROOT:-}" ]]; then
    CMAKE_ARGS+=(-DCASADI_ROOT="${CASADI_ROOT}")
fi

if [[ ! -f "${PLUGIN_BUILD_DIR}/CMakeCache.txt" ]]; then
    cmake "${CMAKE_ARGS[@]}"
fi
cmake --build "${PLUGIN_BUILD_DIR}" --config Release
cmake --install "${PLUGIN_BUILD_DIR}" --config Release

PLUGIN_LIB_DIR="${PLUGIN_INSTALL_DIR}/lib"
# Linux release is static-only; this copy is a no-op there but needed on Windows/macOS.
HIGHS_LIBS=()
for lib_dir in "${HIGHS_DIR}/lib" "${HIGHS_DIR}/bin"; do
    if [[ -d "${lib_dir}" ]]; then
        while IFS= read -r -d '' f; do
            HIGHS_LIBS+=("${f}")
        done < <(find "${lib_dir}" \( -name "libhighs*.so*" -o -name "libhighs*.dylib" -o -name "highs*.dll" \) -print0 2>/dev/null || true)
    fi
done
if [[ "${#HIGHS_LIBS[@]}" -eq 0 ]]; then
    echo "==> Note: no HiGHS shared library found (static-only release); none copied."
else
    cp -v "${HIGHS_LIBS[@]}" "${PLUGIN_LIB_DIR}/"
fi

echo ""
echo "==> Plugin built successfully."
echo "    RTCTOOLS_EXTRA_CASADIPATH=${PLUGIN_LIB_DIR}"
echo ""
echo "    To run the end-to-end test:"
echo "      RTCTOOLS_EXTRA_CASADIPATH=${PLUGIN_LIB_DIR} \\"
echo "      pytest tests/optimization/test_highs_plugin_e2e.py -v"
echo ""
echo "    Or from the testbench repo root:"
echo "      WORK_DIR=./ci-work bash tools/casadi_plugins/highs/build_highs_plugin.sh"

# Write the plugin lib path to a file so CI steps can source it without
# re-deriving the versioned directory name.
echo "${PLUGIN_LIB_DIR}" > "${WORK_DIR}/plugin-lib-path.txt"
