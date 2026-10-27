"""Result objects returned by :class:`~lost_api.LostRunner`.

Catalog IDs are LOST's catalog "names": Yale Bright Star Catalog (HR) numbers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Union

from .config import DatabaseConfig

if TYPE_CHECKING:
    from .images import GrayImage

Scalar = Union[int, float, str]


@dataclass(frozen=True)
class Attitude:
    """A spacecraft attitude in the form LOST reports it.

    ``ra_deg``/``de_deg``/``roll_deg`` are the camera boresight's right ascension, declination and
    roll. ``quaternion`` is ``(real, i, j, k)``.
    """

    ra_deg: float
    de_deg: float
    roll_deg: float
    quaternion: tuple[float, float, float, float]

    def angle_to(self, other: "Attitude") -> float:
        """Smallest rotation angle, in degrees, between this attitude and ``other``."""
        a = _normalized(self.quaternion)
        b = _normalized(other.quaternion)
        dot = min(1.0, abs(sum(x * y for x, y in zip(a, b))))
        return math.degrees(2 * math.acos(dot))


def _normalized(q: tuple[float, float, float, float]) -> tuple[float, ...]:
    norm = math.sqrt(sum(x * x for x in q))
    if norm == 0:
        raise ValueError("zero quaternion")
    return tuple(x / norm for x in q)


@dataclass(frozen=True)
class Centroid:
    """A star position in image coordinates (pixels, origin at the top-left corner).

    ``catalog_id`` is the catalog star this centroid was identified as (for actual centroids) or
    truly is (for generated input/expected centroids); ``None`` when unknown or a false star.
    ``expected_catalog_ids`` lists the acceptable true identities of an actual centroid on a
    generated image (there can be more than one when catalog stars are close together).
    """

    index: int
    x: float
    y: float
    catalog_id: int | None = None
    expected_catalog_ids: tuple[int, ...] = ()

    @property
    def correctly_identified(self) -> bool | None:
        """Whether ``catalog_id`` is an acceptable identity, or ``None`` if that can't be judged."""
        if self.catalog_id is None or not self.expected_catalog_ids:
            return None
        return self.catalog_id in self.expected_catalog_ids


@dataclass(frozen=True)
class CentroidComparison:
    """How well detected centroids matched the generated truth (averaged over images)."""

    num_correct: float
    num_extra: float
    mean_error_px: float | None


@dataclass(frozen=True)
class StarIdComparison:
    """How well star identification matched the truth.

    Per-star counts are only reported by LOST for single-image runs. An image counts as correct
    when at least one star is correctly identified and none incorrectly.
    """

    num_images_correct: int
    num_images_incorrect: int
    num_correct: int | None = None
    num_incorrect: int | None = None
    num_total: int | None = None


@dataclass(frozen=True)
class AttitudeComparison:
    """Attitude accuracy over the run's images.

    ``availability`` is the fraction of images with an attitude within the comparison threshold,
    ``error_rate`` the fraction with an attitude outside it. ``mean_error_deg`` averages only the
    correct ones and is ``None`` when there are none.
    """

    mean_error_deg: float | None
    availability: float
    error_rate: float


@dataclass(frozen=True)
class TimingStats:
    """Wall-clock time of one pipeline stage, in nanoseconds, over the run's images."""

    average_ns: int
    min_ns: int
    max_ns: int
    p95_ns: int

    @property
    def average_ms(self) -> float:
        return self.average_ns / 1e6


@dataclass(frozen=True)
class DatabaseResult:
    path: Path
    config: DatabaseConfig
    cached: bool
    command: tuple[str, ...] = ()
    stderr: str = ""

    @property
    def size_bytes(self) -> int:
        return self.path.stat().st_size


@dataclass(frozen=True)
class _RunResult:
    command: tuple[str, ...]
    output_dir: Path
    stdout: str
    stderr: str
    metrics: Mapping[str, Scalar]
    centroid_comparison: CentroidComparison | None
    star_id_comparison: StarIdComparison | None
    attitude_comparison: AttitudeComparison | None
    timing: Mapping[str, TimingStats]

    @property
    def command_line(self) -> str:
        """The LOST invocation as a shell command, for reproducing a run by hand."""
        import shlex

        return " ".join(shlex.quote(part) for part in self.command)


@dataclass(frozen=True)
class PipelineResult(_RunResult):
    """Everything LOST reported about one image.

    ``source`` is ``"generated"`` or ``"image"``. ``image_path`` is the generated raw image (when
    pixels were rendered) or the input PNG. ``plots`` maps names such as ``"input"``,
    ``"expected"`` and ``"output"`` to annotated PNGs when plots were requested.
    """

    source: str = "generated"
    image_path: Path | None = None
    plots: Mapping[str, Path] = field(default_factory=dict)
    expected_centroids: tuple[Centroid, ...] = ()
    input_centroids: tuple[Centroid, ...] = ()
    actual_centroids: tuple[Centroid, ...] = ()
    attitude: Attitude | None = None
    attitude_attempted: bool = False
    expected_attitude: Attitude | None = None

    @property
    def identified_stars(self) -> tuple[Centroid, ...]:
        """Actual centroids that star identification matched to a catalog star."""
        return tuple(c for c in self.actual_centroids if c.catalog_id is not None)

    @property
    def attitude_error_deg(self) -> float | None:
        """Angle between the estimated and true attitude, for generated images."""
        if self.attitude is None or self.expected_attitude is None:
            return None
        return self.attitude.angle_to(self.expected_attitude)

    def load_image(self) -> "Any":
        """The raw grayscale image: a ``numpy`` array if numpy is installed, else a GrayImage."""
        from .images import read_png

        if self.image_path is None:
            raise FileNotFoundError("This run has no image (centroids-only generation).")
        image = read_png(self.image_path)
        return image.to_numpy() if _numpy_available() else image

    def load_gray_image(self) -> "GrayImage":
        from .images import read_png

        if self.image_path is None:
            raise FileNotFoundError("This run has no image (centroids-only generation).")
        return read_png(self.image_path)


@dataclass(frozen=True)
class BatchResult(_RunResult):
    """Aggregate statistics from one LOST process over ``num_images`` generated images."""

    num_images: int = 0
    mean_num_expected_centroids: float | None = None
    mean_num_input_centroids: float | None = None
    mean_num_actual_centroids: float | None = None


def _numpy_available() -> bool:
    try:
        import numpy  # noqa: F401
    except ImportError:
        return False
    return True


# --------------------------------------------------------------------------------------------
# Parsing LOST's "key value" output files
# --------------------------------------------------------------------------------------------


def coerce_scalar(text: str) -> Scalar:
    text = text.strip()
    try:
        return int(text)
    except ValueError:
        pass
    try:
        # LOST prints "-nan"/"nan"/"inf" for undefined statistics
        return float(text)
    except ValueError:
        return text


def parse_key_values(text: str) -> dict[str, Scalar]:
    result: dict[str, Scalar] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        key, _, value = line.partition(" ")
        result[key] = coerce_scalar(value)
    return result


def read_key_values(path: Path) -> dict[str, Scalar]:
    if not path.is_file():
        return {}
    return parse_key_values(path.read_text(encoding="utf-8", errors="replace"))


def _float(value: Any) -> float | None:
    if value is None or isinstance(value, str):
        return None
    number = float(value)
    return None if math.isnan(number) else number


def parse_centroids(data: Mapping[str, Scalar], prefix: str) -> tuple[Centroid, ...]:
    stem = f"{prefix}_centroid_"
    raw: dict[int, dict[str, Any]] = {}
    for key, value in data.items():
        if not key.startswith(stem):
            continue
        index_text, _, name = key[len(stem):].partition("_")
        if not index_text.isdigit():
            continue
        entry = raw.setdefault(int(index_text), {"expected": {}})
        if name in ("x", "y", "id"):
            entry[name] = value
        elif name.startswith("expected_id_") and name[len("expected_id_"):].isdigit():
            entry["expected"][int(name[len("expected_id_"):])] = int(value)
    centroids = []
    for index in sorted(raw):
        entry = raw[index]
        if "x" not in entry or "y" not in entry:
            continue
        catalog_id = entry.get("id")
        centroids.append(Centroid(
            index=index,
            x=float(entry["x"]),
            y=float(entry["y"]),
            catalog_id=int(catalog_id) if catalog_id is not None else None,
            expected_catalog_ids=tuple(entry["expected"][j] for j in sorted(entry["expected"])),
        ))
    return tuple(centroids)


def parse_attitude(data: Mapping[str, Scalar], prefix: str = "") -> Attitude | None:
    if not data.get(f"{prefix}attitude_known"):
        return None
    values = [_float(data.get(f"{prefix}attitude_{name}"))
              for name in ("ra", "de", "roll", "real", "i", "j", "k")]
    if any(v is None for v in values):
        return None
    ra, de, roll, real, i, j, k = values  # type: ignore[misc]
    return Attitude(ra_deg=ra, de_deg=de, roll_deg=roll, quaternion=(real, i, j, k))


def parse_centroid_comparison(data: Mapping[str, Scalar]) -> CentroidComparison | None:
    if "centroids_num_correct" not in data:
        return None
    return CentroidComparison(
        num_correct=float(data["centroids_num_correct"]),
        num_extra=float(data.get("centroids_num_extra", 0)),
        mean_error_px=_float(data.get("centroids_mean_error")),
    )


def parse_star_id_comparison(data: Mapping[str, Scalar]) -> StarIdComparison | None:
    if "starid_num_images_correct" not in data:
        return None

    def optional_int(key: str) -> int | None:
        return int(data[key]) if key in data else None

    return StarIdComparison(
        num_images_correct=int(data["starid_num_images_correct"]),
        num_images_incorrect=int(data.get("starid_num_images_incorrect", 0)),
        num_correct=optional_int("starid_num_correct"),
        num_incorrect=optional_int("starid_num_incorrect"),
        num_total=optional_int("starid_num_total"),
    )


def parse_attitude_comparison(data: Mapping[str, Scalar]) -> AttitudeComparison | None:
    if "attitude_availability" not in data:
        return None
    mean_error_rad = _float(data.get("attitude_error_mean"))
    return AttitudeComparison(
        mean_error_deg=math.degrees(mean_error_rad) if mean_error_rad is not None else None,
        availability=float(data["attitude_availability"]),
        error_rate=float(data.get("attitude_error_rate", 0)),
    )


def parse_timing(data: Mapping[str, Scalar]) -> dict[str, TimingStats]:
    timing: dict[str, TimingStats] = {}
    for stage in ("centroiding", "starid", "attitude", "total"):
        keys = [f"{stage}_{suffix}_ns" for suffix in ("average", "min", "max", "95%")]
        if all(key in data for key in keys):
            timing[stage] = TimingStats(*(int(data[key]) for key in keys))
    return timing
