cmake_minimum_required(VERSION 3.15)

# Must be set before project() so it applies to all targets including HiGHS and CasADi
# imports. Matches the /MD runtime used by the CasADi wheel on Windows.
if(MSVC)
    set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreadedDLL")
endif()

project(casadi_conic_highs LANGUAGES CXX)

# ---------------------------------------------------------------------------
# Required inputs
#
#   CASADI_ROOT     Path to the CasADi wheel install directory.
#                   Auto-detected from the active Python interpreter if unset.
#                   Example: .venv/Lib/site-packages/casadi  (Windows)
#                            .venv/lib/python3.x/site-packages/casadi  (Linux)
#
#   CASADI_SRC_DIR  Path to a CasADi source checkout at the tag matching
#                   CASADI_ROOT. Needed for conic_impl.hpp and plugin sources.
#                   Example: /path/to/casadi  (cloned from github.com/casadi/casadi)
#
#   HIGHS_ROOT      Path to a HiGHS install directory containing
#                   lib/cmake/highs/highs-config.cmake (or similar).
#                   Example: /path/to/highs/install
# ---------------------------------------------------------------------------

find_package(Python 3.8 COMPONENTS Interpreter REQUIRED)

if(NOT CASADI_ROOT)
    execute_process(
        COMMAND "${Python_EXECUTABLE}" -c
            "import casadi, os; print(os.path.dirname(casadi.__file__))"
        OUTPUT_VARIABLE CASADI_ROOT
        OUTPUT_STRIP_TRAILING_WHITESPACE
    )
    if(NOT CASADI_ROOT)
        message(FATAL_ERROR "CASADI_ROOT not set and could not be auto-detected. "
            "Pass -DCASADI_ROOT=/path/to/casadi to cmake.")
    endif()
    message(STATUS "Auto-detected CASADI_ROOT: ${CASADI_ROOT}")
endif()

foreach(var CASADI_SRC_DIR HIGHS_ROOT)
    if(NOT DEFINED ${var} OR NOT ${var})
        message(FATAL_ERROR "${var} is required. Pass -D${var}=<path> to cmake.")
    endif()
endforeach()

find_package(casadi CONFIG REQUIRED PATHS "${CASADI_ROOT}/cmake" NO_DEFAULT_PATH)
# The macOS static release tarball bundles libopenblas.a and its own OpenBLAS
# cmake config. highs-config.cmake calls find_dependency(BLAS); pre-satisfy it
# by pointing FindBLAS at the bundled library so no system BLAS is needed.
# On Linux we build HiGHS from source with HIPO=OFF so there is no OpenBLAS
# and highs-config.cmake will not call find_dependency(BLAS) — this block is a
# no-op in that case.  On Windows the MSYS2 package was built without BLAS.
if(EXISTS "${HIGHS_ROOT}/lib/libopenblas.a")
    set(BLAS_LIBRARIES "${HIGHS_ROOT}/lib/libopenblas.a" CACHE FILEPATH "" FORCE)
    set(BLAS_FOUND TRUE CACHE BOOL "" FORCE)
endif()
find_package(highs CONFIG PATHS
    "${HIGHS_ROOT}/lib/cmake/highs"
    "${HIGHS_ROOT}/lib/cmake/HiGHS"
    "${HIGHS_ROOT}/cmake"
    NO_DEFAULT_PATH
)
if(NOT highs_FOUND)
    message(FATAL_ERROR "HiGHS CMake config not found under HIGHS_ROOT=${HIGHS_ROOT}. "
        "Expected one of: lib/cmake/highs/, lib/cmake/HiGHS/, cmake/. "
        "Pass -DHIGHS_ROOT=/path/to/highs/install to cmake.")
endif()

set(PLUGIN_SRC_DIR "${CASADI_SRC_DIR}/casadi/interfaces/highs")
file(COPY
    "${PLUGIN_SRC_DIR}/highs_interface.hpp"
    "${PLUGIN_SRC_DIR}/highs_interface.cpp"
    "${PLUGIN_SRC_DIR}/highs_interface_meta.cpp"
    "${PLUGIN_SRC_DIR}/highs_runtime.hpp"
    DESTINATION "${CMAKE_CURRENT_BINARY_DIR}/src"
)

add_custom_command(
    OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/highs_runtime_str.h"
    COMMAND ${CMAKE_COMMAND}
        -D "OUTPUT=${CMAKE_CURRENT_BINARY_DIR}/highs_runtime_str.h"
        -D "SOURCES=${CMAKE_CURRENT_BINARY_DIR}/src/highs_runtime.hpp"
        -P "${CASADI_SRC_DIR}/casadi/generate_runtime.cmake"
    DEPENDS
        "${CMAKE_CURRENT_BINARY_DIR}/src/highs_runtime.hpp"
        "${CASADI_SRC_DIR}/casadi/generate_runtime.cmake"
    WORKING_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/src"
)

add_custom_target(highs_runtime_str DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/highs_runtime_str.h")

add_library(casadi_conic_highs SHARED
    "${CMAKE_CURRENT_BINARY_DIR}/src/highs_interface.cpp"
    "${CMAKE_CURRENT_BINARY_DIR}/src/highs_interface_meta.cpp"
)
add_dependencies(casadi_conic_highs highs_runtime_str)

include(GenerateExportHeader)
# The path must match the #include in highs_interface.hpp:
#   #include <casadi/interfaces/highs/casadi_conic_highs_export.h>
# CMAKE_CURRENT_BINARY_DIR is already on the include path, so the relative
# path here resolves correctly at compile time.
generate_export_header(casadi_conic_highs
    EXPORT_FILE_NAME "casadi/interfaces/highs/casadi_conic_highs_export.h"
)

target_include_directories(casadi_conic_highs PRIVATE
    "${CMAKE_CURRENT_BINARY_DIR}/src"
    "${CMAKE_CURRENT_BINARY_DIR}"
)
# CASADI_SRC_DIR as SYSTEM so the wheel's casadi/config.h takes priority over
# the source tree (which has config.h.in, not the generated config.h).
target_include_directories(casadi_conic_highs SYSTEM PRIVATE
    "${CASADI_SRC_DIR}"
)

# The CasADi wheel was built with these flags passed directly to the compiler
# (not via config.h). Mirror them here so private headers compile correctly.
# Values come from CASADI_COMPILER_FLAGS in the wheel's casadi/config.h.
file(READ "${CASADI_ROOT}/include/casadi/config.h" _casadi_config_h)
if(_casadi_config_h MATCHES "-DCASADI_VERSION=([0-9]+)")
    set(_casadi_version_int "${CMAKE_MATCH_1}")
else()
    message(FATAL_ERROR "Could not extract CASADI_VERSION from ${CASADI_ROOT}/include/casadi/config.h")
endif()
target_compile_definitions(casadi_conic_highs PRIVATE
    WITH_DL
    CASADI_SNPRINTF=snprintf
    CASADI_WITH_THREAD
    CASADI_WITH_THREADSAFE_SYMBOLICS
    "CASADI_VERSION=${_casadi_version_int}"
)

target_link_libraries(casadi_conic_highs PRIVATE
    casadi::casadi
    highs::highs
)

set_target_properties(casadi_conic_highs PROPERTIES
    CXX_STANDARD 17
    CXX_STANDARD_REQUIRED ON
)
# On Windows the default shared library prefix is empty; force "lib" so CasADi's
# plugin loader finds "libcasadi_conic_highs.dll". Linux/macOS default to "lib".
if(WIN32)
    set_target_properties(casadi_conic_highs PROPERTIES PREFIX "lib")
endif()

# Embed an RPATH so the plugin finds libhighs alongside itself at dlopen time.
# Windows resolves sibling DLLs automatically; no RPATH equivalent needed there.
if(APPLE)
    set_target_properties(casadi_conic_highs PROPERTIES INSTALL_RPATH "@loader_path")
elseif(UNIX)
    # $ORIGIN: plugin's own dir (for any future sibling .so)
    # $ORIGIN/../casadi: CasADi wheel install dir (provides libcasadi.so.3.7)
    set_target_properties(casadi_conic_highs PROPERTIES INSTALL_RPATH "$ORIGIN:$ORIGIN/../casadi")
endif()

if(MSVC)
    target_compile_options(casadi_conic_highs PRIVATE /bigobj)
endif()
if(NOT MSVC)
    target_compile_options(casadi_conic_highs PRIVATE -fvisibility=hidden)
    if(MINGW)
        # Do NOT pass -static-libstdc++/-static-libgcc: the plugin must share
        # the MinGW C++ runtime with CasADi's wheel DLLs to avoid two copies
        # of libstdc++ in the same process (breaks exceptions and typeinfo).
        # Shared linking is the MinGW default; no extra flags needed.
    endif()
endif()

install(TARGETS casadi_conic_highs
    RUNTIME DESTINATION lib   # .dll on Windows
    LIBRARY DESTINATION lib   # .so on Linux/macOS
    ARCHIVE DESTINATION lib   # .lib import library on Windows (MSVC)
)
