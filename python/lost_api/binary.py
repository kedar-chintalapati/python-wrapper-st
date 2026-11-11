"""Locating, building and invoking the LOST executable."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Sequence

from .errors import (
    LostBinaryNotFoundError,
    LostBuildError,
    LostTimeoutError,
    execution_error,
)

CATALOG_FILENAME = "bright-star-catalog.tsv"

# Wheels ship a prebuilt executable, its shared libraries and the catalog here.
BUNDLED_DIR = Path(__file__).resolve().parent / "_bundled"


def _looks_like_repo(path: Path) -> bool:
    return (path / "Makefile").is_file() and (path / "src" / "main.cpp").is_file()


def _checkout_root() -> Path | None:
    # python/lost_api/binary.py -> repository root, when used from a source checkout
    candidate = Path(__file__).resolve().parents[2]
    return candidate if _looks_like_repo(candidate) else None


class LostBinary:
    """A LOST executable plus the star catalog it reads.

    Resolution order:

    * executable: ``path`` argument, ``$LOST_BINARY``, an explicitly chosen repository's ``lost``,
      the copy bundled in an installed wheel, the ``lost`` of the checkout containing this package
      (built on demand when ``auto_build`` is true), then ``lost`` on ``$PATH``;
    * repository (explicit): ``repo_root`` argument, then ``$LOST_REPO_ROOT``;
    * catalog: ``catalog_path`` argument, ``$LOST_BSC_PATH``, then the catalog that belongs to the
      chosen executable (bundled or repository), then the same file next to the executable.

    The catalog is always passed to LOST through ``LOST_BSC_PATH``, so runs do not depend on the
    working directory.
    """

    def __init__(
        self,
        path: str | os.PathLike | None = None,
        *,
        repo_root: str | os.PathLike | None = None,
        catalog_path: str | os.PathLike | None = None,
        auto_build: bool = True,
    ) -> None:
        self._explicit_path = Path(path).expanduser().resolve() if path else None
        explicit_repo = repo_root or os.environ.get("LOST_REPO_ROOT")
        self._repo_is_explicit = bool(explicit_repo)
        self.repo_root = (Path(explicit_repo).expanduser().resolve() if explicit_repo
                          else _checkout_root())
        self._explicit_catalog = Path(catalog_path).expanduser().resolve() if catalog_path else None
        self.auto_build = auto_build
        self._lock = threading.Lock()
        self._sha256: str | None = None

    # -- resolution -------------------------------------------------------------------------

    @property
    def path(self) -> Path:
        """Path to the executable, building it first if needed and allowed."""
        with self._lock:
            return self._resolve_path()

    def _resolve_path(self) -> Path:
        if self._explicit_path is not None:
            if not self._explicit_path.is_file():
                raise LostBinaryNotFoundError(f"LOST executable not found: {self._explicit_path}")
            return self._explicit_path
        env = os.environ.get("LOST_BINARY")
        if env:
            env_path = Path(env).expanduser().resolve()
            if not env_path.is_file():
                raise LostBinaryNotFoundError(f"$LOST_BINARY points to a missing file: {env_path}")
            return env_path
        bundled = BUNDLED_DIR / "lost"
        if bundled.is_file() and not self._repo_is_explicit:
            return bundled
        if self.repo_root is not None:
            repo_binary = self.repo_root / "lost"
            if repo_binary.is_file():
                return repo_binary
            if self.auto_build:
                self._build_locked()
                return repo_binary
        on_path = shutil.which("lost")
        if on_path:
            return Path(on_path).resolve()
        hint = (
            f"Run `make release` in {self.repo_root}" if self.repo_root
            else "Pass path=..., set $LOST_BINARY, or set $LOST_REPO_ROOT to a LOST checkout"
        )
        raise LostBinaryNotFoundError(f"Could not find the LOST executable. {hint}.")

    @property
    def catalog_path(self) -> Path:
        candidates: list[Path] = []
        if self._explicit_catalog is not None:
            candidates.append(self._explicit_catalog)
        elif os.environ.get("LOST_BSC_PATH"):
            candidates.append(Path(os.environ["LOST_BSC_PATH"]).expanduser().resolve())
        else:
            executable = self.path
            if executable.parent == BUNDLED_DIR:
                candidates.append(BUNDLED_DIR / CATALOG_FILENAME)
            elif self.repo_root is not None:
                candidates.append(self.repo_root / CATALOG_FILENAME)
            candidates.append(executable.parent / CATALOG_FILENAME)
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        raise LostBinaryNotFoundError(
            "Could not find the bright star catalog (looked in: "
            + ", ".join(str(c) for c in candidates)
            + "). Pass catalog_path=... or set $LOST_BSC_PATH."
        )

    @property
    def sha256(self) -> str:
        """Hash of the executable, used to key cached databases."""
        if self._sha256 is None:
            self._sha256 = _file_sha256(self.path)
        return self._sha256

    @property
    def is_bundled(self) -> bool:
        """True when the executable is the prebuilt copy shipped inside an installed wheel."""
        return self.path.parent == BUNDLED_DIR

    # -- building ---------------------------------------------------------------------------

    def build(self, *, jobs: int | None = None) -> Path:
        """Run ``make release`` in the repository and return the executable path."""
        with self._lock:
            self._build_locked(jobs=jobs)
            self._sha256 = None
            return self.repo_root / "lost"  # type: ignore[operator]

    def _build_locked(self, jobs: int | None = None) -> None:
        if self.repo_root is None or not _looks_like_repo(self.repo_root):
            raise LostBuildError(f"Not a LOST source checkout: {self.repo_root}")
        if shutil.which("make") is None:
            raise LostBuildError("`make` is not installed, so LOST cannot be built automatically.")
        command = ["make", "release", f"-j{jobs or os.cpu_count() or 1}"]
        completed = subprocess.run(
            command, cwd=self.repo_root, capture_output=True, text=True, stdin=subprocess.DEVNULL
        )
        output = completed.stdout + completed.stderr
        if completed.returncode != 0 or not (self.repo_root / "lost").is_file():
            tail = "\n".join(output.splitlines()[-25:])
            raise LostBuildError(
                f"`{' '.join(command)}` failed in {self.repo_root}. If an earlier debug build left "
                f"objects behind, run `make clean` first.\n{tail}",
                output,
            )

    # -- running ----------------------------------------------------------------------------

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: str | os.PathLike | None = None,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run ``lost <args>``; raise a :class:`~lost_api.errors.LostExecutionError` on failure."""
        command = [str(self.path), *map(str, args)]
        env = os.environ.copy()
        env["LOST_BSC_PATH"] = str(self.catalog_path)
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                errors="replace",
                stdin=subprocess.DEVNULL,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise LostTimeoutError(
                command, None, _text(exc.stdout), _text(exc.stderr),
                message=f"LOST did not finish within {timeout} seconds",
            ) from None
        if completed.returncode != 0:
            raise execution_error(command, completed.returncode, completed.stdout, completed.stderr)
        return completed

    def help_text(self, command: str = "pipeline") -> str:
        """The man page LOST prints for ``lost <command> --help``."""
        return self.run([command, "--help"]).stdout

    def __repr__(self) -> str:
        where = self._explicit_path or self.repo_root
        return f"LostBinary({str(where)!r})"


def _text(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
