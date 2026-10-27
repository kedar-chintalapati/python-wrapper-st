"""High-level entry point: :class:`LostRunner`."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import threading
import warnings
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from .binary import LostBinary
from .config import (
    AttitudeConfig,
    Camera,
    CentroidConfig,
    ComparisonConfig,
    DatabaseConfig,
    DatabaseSpec,
    GenerationConfig,
    StarIdConfig,
)
from .errors import LostConfigError, LostError
from .images import GrayImage, as_gray_image, png_size, write_png
from .results import (
    BatchResult,
    DatabaseResult,
    PipelineResult,
    parse_attitude,
    parse_attitude_comparison,
    parse_centroid_comparison,
    parse_centroids,
    parse_star_id_comparison,
    parse_timing,
    read_key_values,
)


class LostWarning(UserWarning):
    """A configuration that runs but is probably not what was intended."""


# Files the runner may ask LOST to write into a run directory.
_TEXT_OUTPUTS = (
    "expected_centroids", "input_centroids", "actual_centroids", "attitude", "expected_attitude",
    "compare_centroids", "compare_star_ids", "compare_attitudes", "speed",
)
_PLOTS = ("raw_input", "input", "expected", "output", "centroid_indices")
_INPUT_IMAGE_NAME = "input_image.png"


@dataclass(frozen=True)
class _Stages:
    centroid: CentroidConfig | None = None
    star_id: StarIdConfig | None = None
    attitude: AttitudeConfig | None = None

    @property
    def any(self) -> bool:
        return any((self.centroid, self.star_id, self.attitude))


@dataclass
class _Outputs:
    directory: Path
    args: list[str] = field(default_factory=list)
    files: dict[str, Path] = field(default_factory=dict)

    def request(self, name: str, flag: str, suffix: str = ".txt") -> Path:
        path = self.directory / f"{name}{suffix}"
        self.args.extend([f"--{flag}", str(path)])
        self.files[name] = path
        return path


class LostRunner:
    """Run LOST's image generator, database builder and star-tracking pipeline from Python.

    Each call runs one LOST process in its own directory and returns parsed, typed results.
    Calls are thread-safe and independent, so :meth:`run_generated_many` can run them in parallel.

    Outputs go to ``output_dir`` when a call is given one; otherwise to a fresh directory under the
    runner's ``work_dir`` (a temporary directory by default). Temporary outputs are deleted by
    :meth:`close` or when a ``with LostRunner() as runner:`` block exits, so copy anything you need
    to keep before then.

    Star-identification databases built from a :class:`DatabaseConfig` are cached in
    ``cache_dir`` (default ``$XDG_CACHE_HOME/lost_api`` or ``~/.cache/lost_api``), keyed by the
    config, the LOST executable and the catalog.
    """

    def __init__(
        self,
        binary: LostBinary | str | os.PathLike | None = None,
        *,
        repo_root: str | os.PathLike | None = None,
        catalog_path: str | os.PathLike | None = None,
        auto_build: bool = True,
        work_dir: str | os.PathLike | None = None,
        cache_dir: str | os.PathLike | None = None,
        timeout: float | None = None,
        default_database: DatabaseConfig | None = None,
    ) -> None:
        if isinstance(binary, LostBinary):
            self.binary = binary
        else:
            self.binary = LostBinary(binary, repo_root=repo_root, catalog_path=catalog_path,
                                     auto_build=auto_build)
        self._work_dir = Path(work_dir).expanduser().resolve() if work_dir else None
        self._owns_work_dir = work_dir is None
        self.cache_dir = (Path(cache_dir).expanduser().resolve() if cache_dir
                          else _default_cache_dir())
        self.timeout = timeout
        self.default_database = default_database or DatabaseConfig()
        self._lock = threading.Lock()
        self._database_locks: dict[str, threading.Lock] = {}
        self._catalog_sha256: str | None = None

    def __enter__(self) -> "LostRunner":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def close(self) -> None:
        """Delete the temporary work directory, if this runner created one."""
        with self._lock:
            if self._owns_work_dir and self._work_dir is not None:
                shutil.rmtree(self._work_dir, ignore_errors=True)
                self._work_dir = None

    @property
    def work_dir(self) -> Path:
        with self._lock:
            if self._work_dir is None:
                self._work_dir = Path(tempfile.mkdtemp(prefix="lost_api_"))
            self._work_dir.mkdir(parents=True, exist_ok=True)
            return self._work_dir

    def __repr__(self) -> str:
        return f"LostRunner(binary={self.binary!r}, cache_dir={str(self.cache_dir)!r})"

    # -- databases --------------------------------------------------------------------------

    def build_database(
        self,
        config: DatabaseConfig | None = None,
        output_path: str | os.PathLike | None = None,
        *,
        timeout: float | None = None,
    ) -> DatabaseResult:
        """Build a database. Without ``output_path`` this is :meth:`database` (cached)."""
        config = config or self.default_database
        if output_path is None:
            return self.database(config, timeout=timeout)
        return self._build_database_at(config, Path(output_path).expanduser().resolve(), timeout)

    def database(self, config: DatabaseConfig | None = None, *,
                 timeout: float | None = None) -> DatabaseResult:
        """Return a cached database for ``config``, building it on first use."""
        config = config or self.default_database
        key = config.cache_key(self.binary.sha256, self._catalog_hash())
        path = self.cache_dir / "databases" / f"database-{key}.dat"
        with self._lock:
            lock = self._database_locks.setdefault(key, threading.Lock())
        with lock:
            if path.is_file() and path.stat().st_size > 0:
                return DatabaseResult(path=path, config=config, cached=True)
            return self._build_database_at(config, path, timeout)

    def _build_database_at(self, config: DatabaseConfig, path: Path,
                           timeout: float | None) -> DatabaseResult:
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.partial")
        args = config.to_args(partial)
        try:
            completed = self.binary.run(args, cwd=path.parent, timeout=self._timeout(timeout))
            if not partial.is_file() or partial.stat().st_size == 0:
                raise LostError(f"LOST reported success but wrote no database to {partial}")
            os.replace(partial, path)
        finally:
            partial.unlink(missing_ok=True)
        return DatabaseResult(path=path, config=config, cached=False,
                              command=(str(self.binary.path), *args), stderr=completed.stderr)

    def _catalog_hash(self) -> str:
        if self._catalog_sha256 is None:
            self._catalog_sha256 = hashlib.sha256(self.binary.catalog_path.read_bytes()).hexdigest()
        return self._catalog_sha256

    def _resolve_database(self, spec: DatabaseSpec, camera: Camera,
                          resolution: tuple[int, int] | None) -> Path:
        if spec is None or isinstance(spec, DatabaseConfig):
            config = spec or self.default_database
            if resolution is not None:
                _warn_on_database_mismatch(config, camera, resolution)
            return self.database(config).path
        path = Path(spec).expanduser().resolve()
        if not path.is_file():
            raise LostConfigError(f"Database file not found: {path}")
        return path

    # -- single images ----------------------------------------------------------------------

    def generate(
        self,
        generation: GenerationConfig | None = None,
        camera: Camera | None = None,
        *,
        output_dir: str | os.PathLike | None = None,
        plots: bool = False,
        timeout: float | None = None,
    ) -> PipelineResult:
        """Generate one synthetic image, with its true centroids and attitude."""
        return self.run_generated(generation, camera, output_dir=output_dir, plots=plots,
                                  timeout=timeout)

    def run_generated(
        self,
        generation: GenerationConfig | None = None,
        camera: Camera | None = None,
        *,
        centroid: CentroidConfig | None = None,
        star_id: StarIdConfig | None = None,
        attitude: AttitudeConfig | None = None,
        comparison: ComparisonConfig | None = None,
        output_dir: str | os.PathLike | None = None,
        plots: bool = False,
        timeout: float | None = None,
    ) -> PipelineResult:
        """Generate one image and run the requested stages on it, scored against the truth.

        Stages left as ``None`` are skipped. Without a centroid stage, star identification runs
        on the generated input centroids; without star identification, attitude estimation uses
        the true identities of those centroids.
        """
        generation = generation or GenerationConfig()
        camera = camera or Camera()
        stages = _Stages(centroid, star_id, attitude)
        _check_stages(stages, has_image=generation.has_image, has_input_centroids=True)
        directory = self._run_dir(output_dir, "generated")
        outputs = _Outputs(directory)

        image_path = None
        if generation.has_image:
            image_path = outputs.request("raw_input", "plot-raw-input", ".png")
        outputs.request("input_centroids", "print-input-centroids")
        outputs.request("expected_centroids", "print-expected-centroids")
        outputs.request("expected_attitude", "print-expected-attitude")
        self._request_stage_outputs(outputs, stages, generated=True)
        if plots and generation.has_image:
            outputs.request("input", "plot-input", ".png")
            outputs.request("expected", "plot-expected", ".png")
            if stages.centroid or stages.star_id:
                outputs.request("output", "plot-output", ".png")
                outputs.request("centroid_indices", "plot-centroid-indices", ".png")

        args = ["pipeline", *camera.to_args(), *generation.to_args(1)]
        args += self._stage_args(stages, camera, (generation.x_resolution, generation.y_resolution))
        args += (comparison or ComparisonConfig()).to_args()
        args += outputs.args
        return self._run_single(args, outputs, "generated", image_path, stages, timeout)

    def run_image(
        self,
        image: str | os.PathLike | GrayImage | Any,
        camera: Camera | None = None,
        *,
        centroid: CentroidConfig | None = None,
        star_id: StarIdConfig | None = None,
        attitude: AttitudeConfig | None = None,
        output_dir: str | os.PathLike | None = None,
        plots: bool = False,
        timeout: float | None = None,
    ) -> PipelineResult:
        """Run the requested stages on a real image.

        ``image`` is a PNG path, a :class:`~lost_api.images.GrayImage`, or a 2-D uint8 array
        (written to a temporary PNG for LOST). A real image needs at least the centroid stage.
        """
        camera = camera or Camera()
        stages = _Stages(centroid, star_id, attitude)
        if not stages.any:
            raise LostConfigError("run_image needs at least one stage, e.g. centroid=CentroidConfig()")
        _check_stages(stages, has_image=True, has_input_centroids=False)
        directory = self._run_dir(output_dir, "image")
        image_path = self._materialize_image(image, directory)
        outputs = _Outputs(directory)
        self._request_stage_outputs(outputs, stages, generated=False)
        if plots:
            outputs.request("output", "plot-output", ".png")
            outputs.request("centroid_indices", "plot-centroid-indices", ".png")

        args = ["pipeline", "--png", str(image_path), *camera.to_args()]
        args += self._stage_args(stages, camera, png_size(image_path))
        args += outputs.args
        return self._run_single(args, outputs, "image", image_path, stages, timeout)

    def identify(
        self,
        image: str | os.PathLike | GrayImage | Any,
        camera: Camera | None = None,
        *,
        centroid: CentroidConfig | None = None,
        star_id: StarIdConfig | None = None,
        attitude: AttitudeConfig | None = None,
        output_dir: str | os.PathLike | None = None,
        plots: bool = False,
        timeout: float | None = None,
    ) -> PipelineResult:
        """Run the full star tracker (centroids, star IDs, attitude) on a real image.

        Unset stages use their defaults: center-of-gravity centroiding, pyramid star
        identification with the runner's default database, and Davenport Q attitude estimation.
        """
        return self.run_image(
            image, camera,
            centroid=centroid or CentroidConfig(),
            star_id=star_id or StarIdConfig(),
            attitude=attitude or AttitudeConfig(),
            output_dir=output_dir, plots=plots, timeout=timeout,
        )

    # -- many images ------------------------------------------------------------------------

    def run_generated_many(
        self,
        generations: Iterable[GenerationConfig],
        camera: Camera | None = None,
        *,
        centroid: CentroidConfig | None = None,
        star_id: StarIdConfig | None = None,
        attitude: AttitudeConfig | None = None,
        comparison: ComparisonConfig | None = None,
        output_dir: str | os.PathLike | None = None,
        plots: bool = False,
        max_workers: int | None = 1,
        return_exceptions: bool = False,
        timeout: float | None = None,
    ) -> list[PipelineResult | LostError]:
        """:meth:`run_generated` for each config, with full per-image results, in input order.

        Vary seeds or attitudes with :func:`dataclasses.replace`, e.g.
        ``[replace(base, seed=s) for s in range(100)]``. ``max_workers`` LOST processes run at
        once (``None`` means one per CPU). With ``return_exceptions=True`` a failing image yields
        its :class:`~lost_api.errors.LostError` instead of aborting the whole run.
        """
        configs = list(generations)
        base_dir = Path(output_dir).expanduser().resolve() if output_dir else None
        width = max(4, len(str(len(configs))))
        if star_id is not None and (star_id.database is None
                                    or isinstance(star_id.database, DatabaseConfig)):
            self.database(star_id.database)  # build once up front instead of racing per image

        def one(index: int, generation: GenerationConfig) -> PipelineResult | LostError:
            try:
                return self.run_generated(
                    generation, camera, centroid=centroid, star_id=star_id, attitude=attitude,
                    comparison=comparison, plots=plots, timeout=timeout,
                    output_dir=base_dir / f"image_{index:0{width}d}" if base_dir else None,
                )
            except LostError as exc:
                if return_exceptions:
                    return exc
                raise

        workers = max_workers if max_workers is not None else (os.cpu_count() or 1)
        if workers <= 1 or len(configs) <= 1:
            return [one(i, g) for i, g in enumerate(configs)]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(one, range(len(configs)), configs))

    def run_generated_batch(
        self,
        num_images: int,
        generation: GenerationConfig | None = None,
        camera: Camera | None = None,
        *,
        centroid: CentroidConfig | None = None,
        star_id: StarIdConfig | None = None,
        attitude: AttitudeConfig | None = None,
        comparison: ComparisonConfig | None = None,
        output_dir: str | os.PathLike | None = None,
        timeout: float | None = None,
    ) -> BatchResult:
        """Generate ``num_images`` images in a single LOST process and return aggregate scores.

        Faster than :meth:`run_generated_many` for large evaluations, but LOST reports only
        summary statistics (no per-image centroids, attitudes or images). With
        ``generation.random_attitudes`` each image gets a different random attitude.
        """
        if isinstance(num_images, bool) or not isinstance(num_images, int) or num_images < 1:
            raise LostConfigError(f"num_images must be a positive integer, got {num_images!r}")
        generation = generation or GenerationConfig()
        camera = camera or Camera()
        stages = _Stages(centroid, star_id, attitude)
        _check_stages(stages, has_image=generation.has_image, has_input_centroids=True)
        directory = self._run_dir(output_dir, "batch")
        outputs = _Outputs(directory)
        outputs.request("input_centroids", "print-input-centroids")
        outputs.request("expected_centroids", "print-expected-centroids")
        if stages.centroid or stages.star_id:
            outputs.request("actual_centroids", "print-actual-centroids")
        if stages.centroid:
            outputs.request("compare_centroids", "compare-centroids")
        if stages.star_id:
            outputs.request("compare_star_ids", "compare-star-ids")
        if stages.attitude:
            outputs.request("compare_attitudes", "compare-attitudes")
        if stages.any:
            outputs.request("speed", "print-speed")

        args = ["pipeline", *camera.to_args(), *generation.to_args(num_images)]
        args += self._stage_args(stages, camera, (generation.x_resolution, generation.y_resolution))
        args += (comparison or ComparisonConfig()).to_args()
        args += outputs.args
        _clear_outputs(directory)
        completed = self.binary.run(args, cwd=directory, timeout=self._timeout(timeout))
        data = {name: read_key_values(path) for name, path in outputs.files.items()}
        metrics = _merge(data.values())

        def mean_count(prefix: str) -> float | None:
            value = metrics.get(f"num_{prefix}_centroids")
            return float(value) if isinstance(value, (int, float)) else None

        return BatchResult(
            command=(str(self.binary.path), *args),
            output_dir=directory,
            stdout=completed.stdout,
            stderr=completed.stderr,
            metrics=metrics,
            centroid_comparison=parse_centroid_comparison(metrics),
            star_id_comparison=parse_star_id_comparison(metrics),
            attitude_comparison=parse_attitude_comparison(metrics),
            timing=parse_timing(metrics),
            num_images=num_images,
            mean_num_expected_centroids=mean_count("expected"),
            mean_num_input_centroids=mean_count("input"),
            mean_num_actual_centroids=mean_count("actual"),
        )

    # -- internals --------------------------------------------------------------------------

    def _timeout(self, timeout: float | None) -> float | None:
        return timeout if timeout is not None else self.timeout

    def _run_dir(self, output_dir: str | os.PathLike | None, label: str) -> Path:
        if output_dir is not None:
            path = Path(output_dir).expanduser().resolve()
            path.mkdir(parents=True, exist_ok=True)
            return path
        return Path(tempfile.mkdtemp(prefix=f"{label}-", dir=self.work_dir))

    def _materialize_image(self, image: Any, directory: Path) -> Path:
        if isinstance(image, (str, os.PathLike)):
            path = Path(image).expanduser().resolve()
            if not path.is_file():
                raise LostConfigError(f"Image not found: {path}")
            return path
        path = directory / _INPUT_IMAGE_NAME
        write_png(path, as_gray_image(image))
        return path

    def _stage_args(self, stages: _Stages, camera: Camera,
                    resolution: tuple[int, int] | None) -> list[str]:
        args: list[str] = []
        if stages.centroid:
            args += stages.centroid.to_args()
        if stages.star_id:
            database = self._resolve_database(stages.star_id.database, camera, resolution)
            args += stages.star_id.to_args(database)
        if stages.attitude:
            args += stages.attitude.to_args()
        return args

    @staticmethod
    def _request_stage_outputs(outputs: _Outputs, stages: _Stages, *, generated: bool) -> None:
        if stages.centroid or stages.star_id:
            outputs.request("actual_centroids", "print-actual-centroids")
        if stages.attitude:
            outputs.request("attitude", "print-attitude")
        if generated:
            if stages.centroid:
                outputs.request("compare_centroids", "compare-centroids")
            if stages.star_id:
                outputs.request("compare_star_ids", "compare-star-ids")
            if stages.attitude:
                outputs.request("compare_attitudes", "compare-attitudes")
        if stages.any:
            outputs.request("speed", "print-speed")

    def _run_single(self, args: list[str], outputs: _Outputs, source: str,
                    image_path: Path | None, stages: _Stages,
                    timeout: float | None) -> PipelineResult:
        _clear_outputs(outputs.directory, keep=image_path if source == "image" else None)
        completed = self.binary.run(args, cwd=outputs.directory, timeout=self._timeout(timeout))
        data = {name: read_key_values(path) for name, path in outputs.files.items()
                if name in _TEXT_OUTPUTS}
        metrics = _merge(data.values())
        plots = {name: path for name, path in outputs.files.items()
                 if name in _PLOTS and name != "raw_input" and path.is_file()}
        return PipelineResult(
            command=(str(self.binary.path), *args),
            output_dir=outputs.directory,
            stdout=completed.stdout,
            stderr=completed.stderr,
            metrics=metrics,
            centroid_comparison=parse_centroid_comparison(metrics),
            star_id_comparison=parse_star_id_comparison(metrics),
            attitude_comparison=parse_attitude_comparison(metrics),
            timing=parse_timing(metrics),
            source=source,
            image_path=image_path,
            plots=plots,
            expected_centroids=parse_centroids(data.get("expected_centroids", {}), "expected"),
            input_centroids=parse_centroids(data.get("input_centroids", {}), "input"),
            actual_centroids=parse_centroids(data.get("actual_centroids", {}), "actual"),
            attitude=parse_attitude(data.get("attitude", {})),
            attitude_attempted=stages.attitude is not None,
            expected_attitude=parse_attitude(data.get("expected_attitude", {}), "expected_"),
        )


def _check_stages(stages: _Stages, *, has_image: bool, has_input_centroids: bool) -> None:
    """Reject stage combinations LOST would refuse, with an actionable message."""
    if stages.centroid and not has_image:
        raise LostConfigError(
            "The centroid stage needs pixels, but centroids_only generation renders none. "
            "Drop centroid=... to run star identification on the generated centroids instead."
        )
    if stages.star_id and not (stages.centroid or has_input_centroids):
        raise LostConfigError(
            "Star identification needs centroids: add centroid=CentroidConfig()."
        )
    if stages.attitude and not stages.star_id and (stages.centroid or not has_input_centroids):
        raise LostConfigError(
            "Attitude estimation needs star identities for the detected centroids: add "
            "star_id=StarIdConfig()."
        )


def _warn_on_database_mismatch(config: DatabaseConfig, camera: Camera,
                               resolution: tuple[int, int]) -> None:
    """Warn when the database's star-pair distance range cannot cover what the camera sees."""
    diagonal = camera.fov_diagonal_deg(*resolution)
    half_width = camera.fov_x_deg(resolution[0]) / 2
    if config.kvector_min_distance_deg >= diagonal:
        problem = (f"kvector_min_distance_deg ({config.kvector_min_distance_deg}) is not smaller "
                   f"than the camera's diagonal field of view ({diagonal:.2f} deg), so no star pair "
                   f"in an image can be matched")
    elif config.kvector_max_distance_deg < half_width:
        problem = (f"kvector_max_distance_deg ({config.kvector_max_distance_deg}) is under half "
                   f"the camera's field of view ({2 * half_width:.1f} deg), so most star pairs in "
                   f"an image cannot be matched; consider about {diagonal:.0f}")
    else:
        return
    warnings.warn(f"Database does not suit this camera: {problem}.", LostWarning, stacklevel=5)


def _clear_outputs(directory: Path, keep: Path | None = None) -> None:
    """Remove outputs of an earlier run in a reused directory so they cannot be misread.

    ``keep`` (the run's input image) is never removed, so re-running on a generated image in
    its own output directory is safe.
    """
    for name in (*_TEXT_OUTPUTS, *_PLOTS):
        for suffix in (".txt", ".png"):
            path = directory / f"{name}{suffix}"
            if keep is None or path.resolve() != keep.resolve():
                path.unlink(missing_ok=True)


def _merge(dicts: Iterable[dict]) -> dict:
    merged: dict = {}
    for d in dicts:
        merged.update(d)
    return merged


def _default_cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "lost_api"


__all__: Sequence[str] = ("LostRunner", "LostWarning")
