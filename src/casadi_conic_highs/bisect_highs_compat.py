#!/usr/bin/env python3
"""
Binary-search HiGHS releases to find the compatibility boundary with the
installed CasADi version.

Downloads only the HiGHS headers (via the pre-built release archive), runs
check_compatibility.py at each midpoint, and narrows the search until it
finds the oldest passing and newest failing versions.  A summary table is
printed at the end showing all versions checked.

Exit codes:
  0  Boundary found (some versions fail, some pass).
  1  All tested versions fail (even the newest).
  2  All tested versions pass (even the oldest).

Usage:
    python bisect_highs_compat.py [--min-version X.Y.Z] [--max-version X.Y.Z]
                                  [--casadi-src-dir PATH] [--casadi-root PATH]
                                  [--work-dir PATH]

Example:
    python bisect_highs_compat.py --min-version 1.1.1 --max-version 1.15.1
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
GITHUB_RELEASES_URL = "https://api.github.com/repos/ERGO-Code/HiGHS/releases"
HIGHS_DOWNLOAD_BASE = "https://github.com/ERGO-Code/HiGHS/releases/download/v{version}/{archive}"

# Windows pre-built zips only exist from v1.13.0 onwards. For older versions
# we download the Linux archive; headers are platform-independent.
_WIN_SINCE = (1, 13, 0)


def _parse_ver(version: str) -> tuple[int, ...]:
    return tuple(int(x) for x in version.split("."))


def _archive_candidates(version: str) -> list[str]:
    """Return archive names to try, in preference order."""
    candidates = []
    if _parse_ver(version) >= _WIN_SINCE:
        candidates.append(f"highs-{version}-x86_64-windows-mit.zip")
    # Linux static archive always has the headers; use as fallback on all platforms.
    candidates.append(f"highs-{version}-x86_64-linux-gnu-static-apache.tar.gz")
    return candidates


def _fetch_release_versions(min_ver: tuple, max_ver: tuple) -> list[str]:
    versions = []
    page = 1
    while True:
        url = f"{GITHUB_RELEASES_URL}?per_page=100&page={page}"
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                releases = json.loads(resp.read())
        except (urllib.error.URLError, OSError) as exc:
            print(f"ERROR: Failed to fetch releases from GitHub: {exc}", file=sys.stderr)
            sys.exit(1)
        if not releases:
            break
        for release in releases:
            tag = release.get("tag_name", "").lstrip("v")
            try:
                parts = _parse_ver(tag)
            except ValueError:
                continue
            if len(parts) == 3 and min_ver <= parts <= max_ver:
                versions.append(tag)
        page += 1
    versions.sort(key=_parse_ver)
    return versions


def _download_and_extract(version: str, work_dir: Path) -> Path | None:
    highs_dir = work_dir / f"highs-{version}"
    if highs_dir.is_dir() and (highs_dir / "include").is_dir():
        return highs_dir

    for archive in _archive_candidates(version):
        archive_path = work_dir / archive
        url = HIGHS_DOWNLOAD_BASE.format(version=version, archive=archive)

        if not archive_path.exists():
            print(f"    Downloading {archive}...")
            try:
                with urllib.request.urlopen(url, timeout=60) as resp, \
                        open(archive_path, "wb") as f:
                    shutil.copyfileobj(resp, f)
            except (urllib.error.URLError, OSError):
                continue  # try next candidate

        highs_dir.mkdir(exist_ok=True)
        print(f"    Extracting {archive}...")
        try:
            if archive.endswith(".zip"):
                with zipfile.ZipFile(archive_path) as zf:
                    zf.extractall(highs_dir)
            else:
                with tarfile.open(archive_path) as tf:
                    members = tf.getmembers()
                    prefix = members[0].name.split("/")[0] + "/" if members else ""
                    for member in members:
                        if member.name.startswith(prefix):
                            member.name = member.name[len(prefix):]
                        tf.extract(member, highs_dir)
            if (highs_dir / "include").is_dir():
                return highs_dir
        except Exception:
            pass

        shutil.rmtree(highs_dir, ignore_errors=True)

    print(f"    SKIP: no usable archive found for {version}")
    return None


def _check(version: str, work_dir: Path, casadi_src_dir: Path,
           casadi_root: Path) -> bool | None:
    """Return True=pass, False=fail, None=skip."""
    highs_dir = _download_and_extract(version, work_dir)
    if highs_dir is None:
        return None
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_DIR / "check_compatibility.py"),
            "--casadi-src-dir", str(casadi_src_dir),
            "--highs-dir",      str(highs_dir),
            "--casadi-root",    str(casadi_root),
        ],
        capture_output=True,
    )
    # exit 0 = pass, exit 2 = interface diff warning (signatures still match)
    return result.returncode != 1


_NOT_RELEASED = "not_released"


def _table_row(version: str, outcome: bool | None | str, boundary: bool = False) -> str:
    if outcome == _NOT_RELEASED:
        status = "NOT RELEASED"
        marker = "-"
    elif outcome is None:
        status = "SKIP"
        marker = "?"
    elif outcome:
        status = "PASS"
        marker = "+"
    else:
        status = "FAIL"
        marker = "x"
    flag = " <- boundary" if boundary else ""
    return f"  {marker}  {version:<12} {status}{flag}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-version", default="1.1.1",
                        help="Oldest HiGHS version to consider (default: 1.1.1)")
    parser.add_argument("--max-version", default="1.15.1",
                        help="Newest HiGHS version to consider (default: 1.15.1)")
    parser.add_argument("--casadi-src-dir", type=Path)
    parser.add_argument("--casadi-root", type=Path)
    parser.add_argument("--work-dir", type=Path,
                        default=Path(__file__).parents[4] / "ci-work")
    args = parser.parse_args()

    if args.casadi_root is None:
        import casadi
        args.casadi_root = Path(os.path.dirname(casadi.__file__))

    if args.casadi_src_dir is None:
        import casadi
        casadi_version = casadi.__version__
        args.casadi_src_dir = args.work_dir / f"casadi-src-{casadi_version}"
        if not args.casadi_src_dir.is_dir():
            print(f"ERROR: --casadi-src-dir not specified and "
                  f"{args.casadi_src_dir} does not exist.\n"
                  f"       Run build_highs_plugin.sh once first to clone CasADi source.",
                  file=sys.stderr)
            sys.exit(1)

    min_ver = _parse_ver(args.min_version)
    max_ver = _parse_ver(args.max_version)
    args.work_dir.mkdir(parents=True, exist_ok=True)

    print(f"Fetching HiGHS release list ({args.min_version} - {args.max_version})...")
    versions = _fetch_release_versions(min_ver, max_ver)
    if not versions:
        print("ERROR: No HiGHS releases found in the specified range.", file=sys.stderr)
        sys.exit(1)
    print(f"Found {len(versions)} releases: {versions[0]} ... {versions[-1]}")
    print()

    results: dict[str, bool | None | str] = {}
    # Mark versions requested but not found on GitHub so they appear in the table.
    if versions[-1] != args.max_version:
        results[args.max_version] = _NOT_RELEASED
    iterations = 0

    def probe(ver: str) -> bool | None:
        nonlocal iterations
        if ver in results:
            return results[ver]
        iterations += 1
        print(f"[{iterations}] Checking HiGHS {ver}...")
        outcome = _check(ver, args.work_dir, args.casadi_src_dir, args.casadi_root)
        results[ver] = outcome
        label = {True: "PASS", False: "FAIL", None: "SKIP"}[outcome]
        print(f"    -> {label}")
        return outcome

    # Treat SKIP as FAIL so binary search keeps searching higher versions.
    lo, hi = 0, len(versions) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        outcome = probe(versions[mid])
        if outcome is True:
            hi = mid
        else:
            lo = mid + 1

    probe(versions[lo])
    if lo > 0:
        probe(versions[lo - 1])
    # Always confirm the newest version so it appears in the table.
    probe(versions[-1])

    first_pass = next((v for v in versions if results.get(v) is True), None)
    last_fail  = next((v for v in reversed(versions) if results.get(v) is False), None)

    print()
    print("=" * 50)
    print(f"  HiGHS compatibility with CasADi {args.casadi_root.parent.parent.name}")
    print(f"  Versions checked: {len(results)}  |  Bisect steps: {iterations}")
    print("=" * 50)
    print(f"  {'':2} {'Version':<12} {'Result'}")
    print(f"  {'-'*40}")
    for ver in sorted(results, key=_parse_ver):
        is_boundary = ver == first_pass
        print(_table_row(ver, results[ver], boundary=is_boundary))
    print("=" * 50)

    if first_pass is None:
        print(f"\n[FAIL] All tested versions incompatible (even {versions[-1]} fails).")
        sys.exit(1)

    if last_fail is None:
        print(f"\n[PASS] All tested versions compatible (even {versions[0]} passes).")
        sys.exit(2)

    print(f"\nBoundary: FAIL up to {last_fail}, PASS from {first_pass}")
    sys.exit(0)


if __name__ == "__main__":
    main()
