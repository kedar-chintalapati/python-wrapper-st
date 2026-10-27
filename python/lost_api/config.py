"""Typed configuration for each part of LOST.

Every field that corresponds to a LOST command-line option records that option in its field
metadata (``metadata["flag"]``). Defaults match LOST's own defaults in ``src/*-options.hpp`` unless
the field says otherwise; ``python/tests/test_config.py`` checks this against the C++ headers, so a
new or changed upstream option shows up as a test failure instead of silent drift.

All configs are immutable. Derive variants with :func:`dataclasses.replace`::

    base = GenerationConfig(false_stars=50)
    noisy = dataclasses.replace(base, read_noise_stddev=0.2)
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Iterator, Mapping, Union

from .errors import LostConfigError

CENTROID_ALGORITHMS = {
    "cog": "center of gravity",
    "iwcog": "iterative weighted center of gravity",
    "dummy": "random centroids (for testing)",
}
STAR_ID_ALGORITHMS = {
    "py": "pyramid",
    "gv": "geometric voting",
    "dummy": "random identifications (for testing)",
}
ATTITUDE_ALGORITHMS = {
    "dqm": "Davenport Q method",
    "triad": "TRIAD",
    "quest": "QUEST",
}

_INT32_MIN, _INT32_MAX = -(2**31), 2**31 - 1


def _flag(name: str, default: Any, **extra: Any) -> Any:
    """A dataclass field that maps to the LOST option ``--name``.

    ``lost_default`` records LOST's own default when the Python default deliberately differs
    (``None`` there means the two are not comparable, e.g. an algorithm name that LOST leaves
    unset).
    """
    return field(default=default, metadata={"flag": name, **extra})


def format_value(value: Any) -> str:
    """Format a Python value the way LOST's option parsers expect."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, os.PathLike):
        return os.fspath(value)
    return str(value)


def _flag_args(config: Any) -> list[str]:
    """``--flag value`` pairs for every flag-mapped field that is not ``None``."""
    args: list[str] = []
    for f in fields(config):
        flag = f.metadata.get("flag")
        value = getattr(config, f.name)
        if flag is None or value is None:
            continue
        args.extend([f"--{flag}", format_value(value)])
    return args


def _extra_args(options: Mapping[str, Any]) -> list[str]:
    args: list[str] = []
    for name, value in options.items():
        flag = name if name.startswith("--") else f"--{name}"
        args.append(flag)
        if value is not None:
            args.append(format_value(value))
    return args


class _Validator:
    """Collects every problem with a config so one error reports all of them."""

    def __init__(self, owner: str) -> None:
        self.owner = owner
        self.problems: list[str] = []

    def check(self, condition: bool, message: str) -> None:
        if not condition:
            self.problems.append(message)

    def number(self, name: str, value: Any, *, minimum: float | None = None,
               maximum: float | None = None, exclusive_minimum: bool = False,
               integer: bool = False, optional: bool = False) -> None:
        if value is None:
            self.check(optional, f"{name} is required")
            return
        kind = "an integer" if integer else "a number"
        if isinstance(value, bool) or not isinstance(value, (int, float)) or (
            integer and not isinstance(value, int)
        ):
            self.problems.append(f"{name} must be {kind}, got {value!r}")
            return
        if not math.isfinite(value):
            self.problems.append(f"{name} must be finite, got {value!r}")
            return
        if minimum is not None:
            if exclusive_minimum and value <= minimum:
                self.problems.append(f"{name} must be > {minimum}, got {value!r}")
            elif not exclusive_minimum and value < minimum:
                self.problems.append(f"{name} must be >= {minimum}, got {value!r}")
        if maximum is not None and value > maximum:
            self.problems.append(f"{name} must be <= {maximum}, got {value!r}")

    def boolean(self, name: str, value: Any) -> None:
        self.check(isinstance(value, bool), f"{name} must be True or False, got {value!r}")

    def choice(self, name: str, value: Any, choices: Mapping[str, str]) -> None:
        self.check(value in choices, f"{name} must be one of {sorted(choices)}, got {value!r}")

    def done(self) -> None:
        if self.problems:
            raise LostConfigError(f"Invalid {self.owner}: " + "; ".join(self.problems))


# --------------------------------------------------------------------------------------------
# Camera
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Camera:
    """Camera optics. Set either ``fov_deg`` or both ``focal_length_mm`` and ``pixel_size_um``.

    With nothing set, LOST's default horizontal field of view of 20 degrees is used. The image
    resolution comes from the PNG for real images and from :class:`GenerationConfig` for
    generated ones.
    """

    fov_deg: float | None = _flag("fov", None, lost_default=20)
    focal_length_mm: float | None = _flag("focal-length", None, lost_default=0)
    pixel_size_um: float | None = _flag("pixel-size", None, lost_default=-1)

    def __post_init__(self) -> None:
        v = _Validator("Camera")
        uses_optics = self.focal_length_mm is not None or self.pixel_size_um is not None
        v.check(not (self.fov_deg is not None and uses_optics),
                "set either fov_deg or focal_length_mm + pixel_size_um, not both")
        if uses_optics:
            v.number("focal_length_mm", self.focal_length_mm, minimum=0, exclusive_minimum=True)
            v.number("pixel_size_um", self.pixel_size_um, minimum=0, exclusive_minimum=True)
        v.number("fov_deg", self.fov_deg, minimum=0, maximum=179.999, exclusive_minimum=True,
                 optional=True)
        v.done()

    @classmethod
    def from_fov(cls, fov_deg: float) -> "Camera":
        return cls(fov_deg=fov_deg)

    @classmethod
    def from_optics(cls, focal_length_mm: float, pixel_size_um: float) -> "Camera":
        return cls(focal_length_mm=focal_length_mm, pixel_size_um=pixel_size_um)

    @property
    def uses_optics(self) -> bool:
        return self.focal_length_mm is not None

    def focal_length_px(self, x_resolution: int) -> float:
        """Focal length in pixels, exactly as LOST computes it."""
        if self.uses_optics:
            return self.focal_length_mm * 1000 / self.pixel_size_um  # type: ignore[operator]
        fov = math.radians(self.fov_deg if self.fov_deg is not None else 20.0)
        return x_resolution / 2 / math.tan(fov / 2)

    def fov_x_deg(self, x_resolution: int) -> float:
        return math.degrees(2 * math.atan(x_resolution / 2 / self.focal_length_px(x_resolution)))

    def fov_diagonal_deg(self, x_resolution: int, y_resolution: int) -> float:
        half_diagonal = math.hypot(x_resolution, y_resolution) / 2
        return math.degrees(2 * math.atan(half_diagonal / self.focal_length_px(x_resolution)))

    def to_args(self) -> list[str]:
        if self.uses_optics:
            return ["--focal-length", format_value(float(self.focal_length_mm)),
                    "--pixel-size", format_value(float(self.pixel_size_um))]
        if self.fov_deg is not None:
            return ["--fov", format_value(float(self.fov_deg))]
        return []


# --------------------------------------------------------------------------------------------
# Image generation
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class GenerationConfig:
    """Synthetic star-field settings (the ``--generate-*`` options).

    Magnitudes follow the astronomical convention: smaller is brighter. ``false_max_mag`` is the
    *brightest* false star and must be <= ``false_min_mag``.

    ``seed`` drives both the noise and (with ``random_attitudes``) the attitude. LOST uses C++'s
    ``std::default_random_engine``, which treats seed 0 like seed 1, so start seed ranges at 1.

    LOST's default brightness settings render only the brightest few stars above the centroiding
    threshold at 1024x1024; raise ``zero_mag_photons`` or ``exposure`` for denser star fields.

    ``extra_options`` passes additional ``--generate-*`` style options through verbatim, for LOST
    forks with extra generator features, e.g.
    ``extra_options={"generate-obstruct": 2, "generate-obstructInFrame": True}``.
    """

    x_resolution: int = _flag("generate-x-resolution", 1024)
    y_resolution: int = _flag("generate-y-resolution", 1024)
    centroids_only: bool = _flag("generate-centroids-only", False)
    zero_mag_photons: float = _flag("generate-zero-mag-photons", 20000.0)
    saturation_photons: float = _flag("generate-saturation-photons", 150.0)
    spread_stddev: float = _flag("generate-spread-stddev", 1.0)
    shot_noise: bool = _flag("generate-shot-noise", True)
    dark_current: float = _flag("generate-dark-current", 0.1)
    read_noise_stddev: float = _flag("generate-read-noise-stddev", 0.05)
    ra_deg: float = _flag("generate-ra", 88.0)
    de_deg: float = _flag("generate-de", 7.0)
    roll_deg: float = _flag("generate-roll", 0.0)
    random_attitudes: bool = _flag("generate-random-attitudes", False)
    blur_ra_deg: float = _flag("generate-blur-ra", 0.0)
    blur_de_deg: float = _flag("generate-blur-de", 0.0)
    blur_roll_deg: float = _flag("generate-blur-roll", 0.0)
    exposure: float = _flag("generate-exposure", 0.2)
    readout_time: float = _flag("generate-readout-time", 0.0)
    oversampling: int = _flag("generate-oversampling", 4)
    false_stars: int = _flag("generate-false-stars", 0)
    false_min_mag: float = _flag("generate-false-min-mag", 8.0)
    false_max_mag: float = _flag("generate-false-max-mag", 1.0)
    perturb_centroids_stddev: float = _flag("generate-perturb-centroids", 0.0)
    cutoff_mag: float = _flag("generate-cutoff-mag", 6.0)
    seed: int = _flag("generate-seed", 394859)
    time_based_seed: bool = _flag("generate-time-based-seed", False)
    #: Only emitted when set; needs a LOST build with ``--generate-interesting-threshold``.
    interesting_threshold: float | None = _flag("generate-interesting-threshold", None,
                                                lost_default=0.05)
    extra_options: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        v = _Validator("GenerationConfig")
        v.number("x_resolution", self.x_resolution, minimum=1, integer=True)
        v.number("y_resolution", self.y_resolution, minimum=1, integer=True)
        for name in ("centroids_only", "shot_noise", "random_attitudes", "time_based_seed"):
            v.boolean(name, getattr(self, name))
        v.number("zero_mag_photons", self.zero_mag_photons, minimum=0)
        v.number("saturation_photons", self.saturation_photons, minimum=0, exclusive_minimum=True)
        v.number("spread_stddev", self.spread_stddev, minimum=0, exclusive_minimum=True)
        v.number("dark_current", self.dark_current, minimum=0)
        v.number("read_noise_stddev", self.read_noise_stddev, minimum=0)
        v.number("ra_deg", self.ra_deg)
        v.number("de_deg", self.de_deg, minimum=-90, maximum=90)
        v.number("roll_deg", self.roll_deg)
        for name in ("blur_ra_deg", "blur_de_deg", "blur_roll_deg"):
            v.number(name, getattr(self, name))
        v.number("exposure", self.exposure, minimum=0, exclusive_minimum=True)
        v.number("readout_time", self.readout_time, minimum=0)
        v.number("oversampling", self.oversampling, minimum=1, integer=True)
        if isinstance(self.oversampling, int) and self.oversampling >= 1:
            root = math.isqrt(self.oversampling)
            v.check(root * root == self.oversampling,
                    f"oversampling must be a perfect square (1, 4, 9, ...), got {self.oversampling}")
        v.number("false_stars", self.false_stars, minimum=0, integer=True)
        v.number("false_min_mag", self.false_min_mag)
        v.number("false_max_mag", self.false_max_mag)
        if all(isinstance(m, (int, float)) for m in (self.false_min_mag, self.false_max_mag)):
            v.check(self.false_max_mag <= self.false_min_mag,
                    "false_max_mag (brightest false star) must be <= false_min_mag (dimmest)")
        v.number("perturb_centroids_stddev", self.perturb_centroids_stddev, minimum=0)
        v.number("cutoff_mag", self.cutoff_mag)
        v.number("seed", self.seed, minimum=_INT32_MIN, maximum=_INT32_MAX, integer=True)
        v.number("interesting_threshold", self.interesting_threshold, minimum=0,
                 exclusive_minimum=True, optional=True)
        v.check(isinstance(self.extra_options, Mapping), "extra_options must be a mapping")
        v.done()

    @property
    def has_image(self) -> bool:
        """False for ``centroids_only`` generation, which skips rendering pixels."""
        return not self.centroids_only

    def to_args(self, num_images: int = 1) -> list[str]:
        return ["--generate", str(num_images), *_flag_args(self), *_extra_args(self.extra_options)]


# --------------------------------------------------------------------------------------------
# Pipeline stages
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CentroidConfig:
    """Centroid detection: find star positions in an image."""

    algorithm: str = _flag("centroid-algo", "cog", lost_default=None)
    #: Number of random centroids produced by the ``dummy`` algorithm.
    dummy_stars: int | None = _flag("centroid-dummy-stars", None, lost_default=5)
    #: Discard centroids dimmer than this (LOST's centroid magnitude units).
    magnitude_filter: float | None = _flag("centroid-mag-filter", None, lost_default=-1)
    #: Keep only this many of the brightest centroids.
    brightest_only: int | None = _flag("centroid-filter-brightest", None, lost_default=-1)

    def __post_init__(self) -> None:
        v = _Validator("CentroidConfig")
        v.choice("algorithm", self.algorithm, CENTROID_ALGORITHMS)
        v.number("dummy_stars", self.dummy_stars, minimum=0, integer=True, optional=True)
        v.number("magnitude_filter", self.magnitude_filter, minimum=0, exclusive_minimum=True,
                 optional=True)
        v.number("brightest_only", self.brightest_only, minimum=1, integer=True, optional=True)
        v.done()

    def to_args(self) -> list[str]:
        return _flag_args(self)


DatabaseSpec = Union[str, os.PathLike, "DatabaseConfig", None]


@dataclass(frozen=True)
class StarIdConfig:
    """Star identification: match centroids to catalog stars.

    ``database`` is a path to an existing database file, a :class:`DatabaseConfig` (built and
    cached on first use), or ``None`` for the runner's default database.
    """

    algorithm: str = _flag("star-id-algo", "py", lost_default=None)
    database: DatabaseSpec = None
    angular_tolerance_deg: float = _flag("angular-tolerance", 0.04)
    false_stars_estimate: int = _flag("false-stars-estimate", 500)
    max_mismatch_probability: float = _flag("max-mismatch-probability", 0.001)

    def __post_init__(self) -> None:
        v = _Validator("StarIdConfig")
        v.choice("algorithm", self.algorithm, STAR_ID_ALGORITHMS)
        v.check(self.database is None or isinstance(self.database, (str, os.PathLike, DatabaseConfig)),
                f"database must be a path, a DatabaseConfig or None, got {self.database!r}")
        v.number("angular_tolerance_deg", self.angular_tolerance_deg, minimum=0,
                 exclusive_minimum=True)
        v.number("false_stars_estimate", self.false_stars_estimate, minimum=0, integer=True)
        v.number("max_mismatch_probability", self.max_mismatch_probability, minimum=0,
                 maximum=1, exclusive_minimum=True)
        v.done()

    def to_args(self, database_path: Path) -> list[str]:
        return ["--database", str(database_path), *_flag_args(self)]


@dataclass(frozen=True)
class AttitudeConfig:
    """Attitude estimation from identified stars."""

    algorithm: str = _flag("attitude-algo", "dqm", lost_default=None)

    def __post_init__(self) -> None:
        v = _Validator("AttitudeConfig")
        v.choice("algorithm", self.algorithm, ATTITUDE_ALGORITHMS)
        v.done()

    def to_args(self) -> list[str]:
        return _flag_args(self)


@dataclass(frozen=True)
class ComparisonConfig:
    """Thresholds used when scoring a generated image's results against the truth."""

    #: An actual centroid within this many pixels of an expected one counts as correct.
    centroid_threshold_px: float = _flag("centroid-compare-threshold", 2.0)
    #: An attitude within this many degrees of the truth counts as correct.
    attitude_threshold_deg: float = _flag("attitude-compare-threshold", 1.0)

    def __post_init__(self) -> None:
        v = _Validator("ComparisonConfig")
        v.number("centroid_threshold_px", self.centroid_threshold_px, minimum=0,
                 exclusive_minimum=True)
        v.number("attitude_threshold_deg", self.attitude_threshold_deg, minimum=0,
                 exclusive_minimum=True)
        v.done()

    def to_args(self) -> list[str]:
        return _flag_args(self)


# --------------------------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DatabaseConfig:
    """Star-identification database (``lost database``).

    Defaults follow the recipe in LOST's README rather than the bare CLI defaults, because the CLI
    defaults do not produce a usable database. ``kvector_max_distance_deg`` should be at least the
    widest star-pair separation the camera can see (roughly its diagonal field of view).
    """

    max_stars: int = _flag("max-stars", 5000, lost_default=10000)
    #: Exclude stars dimmer than this magnitude. ``None`` keeps every catalog star.
    min_mag: float | None = _flag("min-mag", None, lost_default=100)
    min_separation_deg: float = _flag("min-separation", 0.08)
    kvector_min_distance_deg: float = _flag("kvector-min-distance", 0.2, lost_default=0.5)
    kvector_max_distance_deg: float = _flag("kvector-max-distance", 15.0)
    kvector_distance_bins: int = _flag("kvector-distance-bins", 10000)
    swap_integer_endianness: bool = _flag("swap-integer-endianness", False)
    swap_decimal_endianness: bool = _flag("swap-decimal-endianness", False)

    def __post_init__(self) -> None:
        v = _Validator("DatabaseConfig")
        v.number("max_stars", self.max_stars, minimum=1, integer=True)
        v.number("min_mag", self.min_mag, optional=True)
        v.number("min_separation_deg", self.min_separation_deg, minimum=0)
        v.number("kvector_min_distance_deg", self.kvector_min_distance_deg, minimum=0)
        v.number("kvector_max_distance_deg", self.kvector_max_distance_deg, minimum=0,
                 maximum=180, exclusive_minimum=True)
        if not v.problems:
            v.check(self.kvector_min_distance_deg < self.kvector_max_distance_deg,
                    "kvector_min_distance_deg must be < kvector_max_distance_deg")
        v.number("kvector_distance_bins", self.kvector_distance_bins, minimum=1, integer=True)
        v.boolean("swap_integer_endianness", self.swap_integer_endianness)
        v.boolean("swap_decimal_endianness", self.swap_decimal_endianness)
        v.done()

    def to_args(self, output_path: str | os.PathLike) -> list[str]:
        return ["database", "--kvector", *_flag_args(self), "--output", os.fspath(output_path)]

    def cache_key(self, *salts: str) -> str:
        """Stable identifier for a database built from this config (plus binary/catalog salts)."""
        payload = json.dumps({"args": self.to_args("-"), "salts": list(salts)}, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def flag_fields() -> Iterator[tuple[type, str, str, Any]]:
    """Yield ``(config class, field name, LOST flag, Python default)`` for every mapped option."""
    for cls in (Camera, GenerationConfig, CentroidConfig, StarIdConfig, AttitudeConfig,
                ComparisonConfig, DatabaseConfig):
        for f in fields(cls):
            if "flag" in f.metadata:
                yield cls, f.name, f.metadata["flag"], f.default
