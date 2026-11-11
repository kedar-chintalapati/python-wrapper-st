"""Builds wheels that contain a compiled LOST executable.

Building a wheel (``pip wheel .``) compiles LOST from this checkout in a scratch directory and
places the executable and star catalog in ``lost_api/_bundled``. With ``LOST_API_BUNDLE_LIBS=1`` on
Linux, every shared library the executable needs (except the core C library) is copied to
``lost_api/_bundled/lib`` and the executable is pointed at those copies, so the wheel runs on
machines without cairo. Editable installs (``pip install -e .``) skip all of this and use the
checkout's own ``./lost``. Set ``LOST_API_SKIP_BINARY=1`` to build a pure-Python wheel.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py
from setuptools.dist import Distribution

try:
    from setuptools.command.bdist_wheel import bdist_wheel
except ImportError:  # setuptools < 70
    from wheel.bdist_wheel import bdist_wheel

ROOT = Path(__file__).resolve().parent
CATALOG = "bright-star-catalog.tsv"

# Libraries every glibc Linux system provides; everything else is bundled.
_SYSTEM_LIBS = (
    "linux-vdso.so", "ld-linux", "libc.so.", "libm.so.", "libpthread.so.", "libdl.so.",
    "librt.so.", "libutil.so.", "libresolv.so.",
)


def _skip_binary() -> bool:
    return os.environ.get("LOST_API_SKIP_BINARY") == "1"


class BuildPyWithLost(build_py):
    def run(self) -> None:
        super().run()
        if getattr(self, "editable_mode", False) or _skip_binary():
            return
        workdir = Path(self.get_finalized_command("build").build_temp) / "lost-src"
        bundle = Path(self.build_lib) / "lost_api" / "_bundled"
        build_lost(workdir, bundle)


class PlatformWheel(bdist_wheel):
    """Tag wheels for the platform only (``py3-none-<platform>``): ``lost`` is a separate
    executable, so one wheel serves every Python 3 version."""

    def finalize_options(self) -> None:
        super().finalize_options()
        self.root_is_pure = _skip_binary()

    def get_tag(self) -> tuple[str, str, str]:
        if _skip_binary():
            return super().get_tag()
        _, _, platform = super().get_tag()
        return "py3", "none", platform


class BinaryDistribution(Distribution):
    def has_ext_modules(self) -> bool:
        return not _skip_binary()


def build_lost(workdir: Path, bundle: Path) -> None:
    """Compile LOST in ``workdir`` (a clean copy of the sources) and fill ``bundle``."""
    shutil.rmtree(workdir, ignore_errors=True)
    workdir.mkdir(parents=True)
    build_artifacts = shutil.ignore_patterns("*.o", "*.d", "man-*.h", "*.txt")
    shutil.copy2(ROOT / "Makefile", workdir)
    shutil.copy2(ROOT / CATALOG, workdir)
    shutil.copytree(ROOT / "src", workdir / "src", ignore=build_artifacts)
    shutil.copytree(ROOT / "vendor", workdir / "vendor", ignore=build_artifacts)
    (workdir / "documentation").mkdir()
    for man in (ROOT / "documentation").glob("*.man"):
        shutil.copy2(man, workdir / "documentation")

    jobs = str(os.cpu_count() or 1)
    print(f"building LOST in {workdir}", flush=True)
    subprocess.run(["make", "release", f"-j{jobs}"], cwd=workdir, check=True)

    shutil.rmtree(bundle, ignore_errors=True)
    bundle.mkdir(parents=True)
    executable = bundle / "lost"
    shutil.copy2(workdir / "lost", executable)
    executable.chmod(0o755)
    shutil.copy2(workdir / CATALOG, bundle / CATALOG)
    if os.environ.get("LOST_API_BUNDLE_LIBS") == "1" and sys.platform.startswith("linux"):
        bundle_linux_libraries(executable)


def bundle_linux_libraries(executable: Path) -> None:
    """Copy the executable's non-system shared libraries next to it and set its RPATH."""
    libdir = executable.parent / "lib"
    libdir.mkdir(exist_ok=True)
    output = subprocess.run(["ldd", str(executable)], check=True, capture_output=True,
                            text=True).stdout
    copied = []
    for line in output.splitlines():
        name, arrow, rest = line.strip().partition(" => ")
        if not arrow or name.startswith(_SYSTEM_LIBS):
            continue
        source = rest.split(" (")[0].strip()
        if not source or source == "not found":
            raise RuntimeError(f"{executable} needs {name}, which was not found:\n{output}")
        target = libdir / name
        shutil.copyfile(os.path.realpath(source), target)
        target.chmod(0o755)
        copied.append(target)
    patchelf = _find_patchelf()
    subprocess.run([patchelf, "--set-rpath", "$ORIGIN/lib", str(executable)], check=True)
    for library in copied:
        subprocess.run([patchelf, "--set-rpath", "$ORIGIN", str(library)], check=True)
    print(f"bundled {len(copied)} shared libraries into {libdir}", flush=True)


def _find_patchelf() -> str:
    candidates = [shutil.which("patchelf"),
                  str(Path(sysconfig.get_path("scripts")) / "patchelf")]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise RuntimeError("patchelf is required to bundle libraries (pip install patchelf)")


setup(
    cmdclass={"build_py": BuildPyWithLost, "bdist_wheel": PlatformWheel},
    distclass=BinaryDistribution,
)
