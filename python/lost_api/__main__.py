"""``python -m lost_api``: show which LOST build is in use and run a quick self-test."""

from __future__ import annotations

import sys
import tempfile

from . import (
    AttitudeConfig,
    CentroidConfig,
    GenerationConfig,
    LostError,
    LostRunner,
    StarIdConfig,
    __version__,
)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="lost_api_check_") as tmp:
        try:
            runner = LostRunner(work_dir=tmp, cache_dir=tmp, auto_build=False)
            binary = runner.binary
            print(f"lost_api {__version__}")
            print(f"lost:    {binary.path}{' (bundled)' if binary.is_bundled else ''}")
            print(f"catalog: {binary.catalog_path}")
            result = runner.run_generated(
                GenerationConfig(x_resolution=512, y_resolution=512, seed=7),
                centroid=CentroidConfig(), star_id=StarIdConfig(), attitude=AttitudeConfig(),
            )
        except LostError as exc:
            print(f"self-test FAILED: {exc}")
            return 1
    error = result.attitude_error_deg
    if error is None or error > 1.0:
        print(f"self-test FAILED: attitude error {error} deg on a generated image")
        return 1
    print(f"self-test passed: {len(result.identified_stars)} stars identified, "
          f"attitude error {error:.4f} deg")
    return 0


if __name__ == "__main__":
    sys.exit(main())
