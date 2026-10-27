"""Python API for the LOST star tracker: image generation and the star-tracking pipeline.

Quick start::

    from lost_api import LostRunner, GenerationConfig, CentroidConfig, StarIdConfig, AttitudeConfig

    with LostRunner() as lost:
        truth = lost.generate(GenerationConfig(false_stars=20, seed=7))
        print(len(truth.input_centroids), truth.expected_attitude)

        result = lost.run_generated(
            GenerationConfig(seed=7),
            centroid=CentroidConfig(),
            star_id=StarIdConfig(),
            attitude=AttitudeConfig(),
        )
        print(result.attitude, result.attitude_error_deg)

See README.md for the full guide.
"""

from .binary import LostBinary
from .config import (
    ATTITUDE_ALGORITHMS,
    CENTROID_ALGORITHMS,
    STAR_ID_ALGORITHMS,
    AttitudeConfig,
    Camera,
    CentroidConfig,
    ComparisonConfig,
    DatabaseConfig,
    GenerationConfig,
    StarIdConfig,
)
from .errors import (
    LostBinaryNotFoundError,
    LostBuildError,
    LostConfigError,
    LostError,
    LostExecutionError,
    LostTimeoutError,
    LostUnsupportedOptionError,
)
from .images import GrayImage, read_png, write_png
from .results import (
    Attitude,
    AttitudeComparison,
    BatchResult,
    Centroid,
    CentroidComparison,
    DatabaseResult,
    PipelineResult,
    StarIdComparison,
    TimingStats,
)
from .runner import LostRunner, LostWarning

__version__ = "0.2.0"

__all__ = [
    "ATTITUDE_ALGORITHMS",
    "CENTROID_ALGORITHMS",
    "STAR_ID_ALGORITHMS",
    "Attitude",
    "AttitudeComparison",
    "AttitudeConfig",
    "BatchResult",
    "Camera",
    "Centroid",
    "CentroidComparison",
    "CentroidConfig",
    "ComparisonConfig",
    "DatabaseConfig",
    "DatabaseResult",
    "GenerationConfig",
    "GrayImage",
    "LostBinary",
    "LostBinaryNotFoundError",
    "LostBuildError",
    "LostConfigError",
    "LostError",
    "LostExecutionError",
    "LostRunner",
    "LostTimeoutError",
    "LostUnsupportedOptionError",
    "LostWarning",
    "PipelineResult",
    "StarIdComparison",
    "StarIdConfig",
    "TimingStats",
    "read_png",
    "write_png",
]
