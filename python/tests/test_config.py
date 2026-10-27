"""Config validation, argument building, and drift checks against LOST's C++ option headers."""

from __future__ import annotations

import math
import re
import unittest
from dataclasses import fields, replace
from pathlib import Path

from lost_api import (
    AttitudeConfig,
    Camera,
    CentroidConfig,
    ComparisonConfig,
    DatabaseConfig,
    GenerationConfig,
    LostConfigError,
    StarIdConfig,
)
from lost_api.config import flag_fields, format_value

REPO_ROOT = Path(__file__).resolve().parents[2]
HEADERS = [REPO_ROOT / "src" / "pipeline-options.hpp", REPO_ROOT / "src" / "database-options.hpp"]

# Options the runner sets itself instead of exposing as config fields.
RUNNER_FLAGS = {"png", "generate", "database", "output", "kvector"}
RUNNER_FLAG_PREFIXES = ("plot-", "print-", "compare-")

_OPTION = re.compile(r'LOST_CLI_OPTION\(\s*"([^"]+)"\s*,\s*[^,]+,\s*\w+\s*,\s*([^,]+?)\s*,')


def header_options() -> dict[str, str]:
    options: dict[str, str] = {}
    for header in HEADERS:
        text = header.read_text(encoding="utf-8")
        for match in _OPTION.finditer(re.sub(r"\s*\n\s*", " ", text)):
            options[match.group(1)] = match.group(2)
    return options


def parse_cpp_default(text: str):
    text = text.strip()
    inner = re.fullmatch(r"DECIMAL\((.*)\)", text)
    if inner:
        text = inner.group(1)
    if text in ("true", "false"):
        return text == "true"
    if text.startswith('"'):
        return text.strip('"')
    return float(text)


@unittest.skipUnless(all(h.is_file() for h in HEADERS), "LOST C++ sources not available")
class HeaderDriftTests(unittest.TestCase):
    def test_every_lost_option_is_handled(self) -> None:
        mapped = {flag for _, _, flag, _ in flag_fields()}
        unhandled = [
            name for name in header_options()
            if name not in mapped and name not in RUNNER_FLAGS
            and not name.startswith(RUNNER_FLAG_PREFIXES)
        ]
        self.assertEqual(unhandled, [], "New LOST options need a config field (or a runner rule)")

    def test_every_python_flag_exists_in_lost(self) -> None:
        options = header_options()
        unknown = [flag for _, _, flag, _ in flag_fields() if flag not in options]
        self.assertEqual(unknown, [])

    def test_python_defaults_match_lost_defaults(self) -> None:
        options = header_options()
        for cls, name, flag, default in flag_fields():
            metadata = next(f.metadata for f in fields(cls) if f.name == name)
            expected = metadata.get("lost_default", default)
            if expected is None:
                continue
            with self.subTest(f"{cls.__name__}.{name} (--{flag})"):
                actual = parse_cpp_default(options[flag])
                if isinstance(expected, bool):
                    self.assertEqual(actual, expected)
                else:
                    self.assertTrue(math.isclose(float(actual), float(expected)),
                                    f"LOST default {actual!r} != Python {expected!r}")


class CameraTests(unittest.TestCase):
    def test_default_is_lost_default_fov(self) -> None:
        self.assertEqual(Camera().to_args(), [])
        self.assertAlmostEqual(Camera().fov_x_deg(1024), 20.0)

    def test_fov_args(self) -> None:
        self.assertEqual(Camera.from_fov(15).to_args(), ["--fov", "15.0"])

    def test_optics_args_and_focal_length(self) -> None:
        camera = Camera.from_optics(49, 22.2)
        self.assertEqual(camera.to_args(), ["--focal-length", "49.0", "--pixel-size", "22.2"])
        self.assertAlmostEqual(camera.focal_length_px(1000), 49 * 1000 / 22.2)

    def test_focal_length_matches_lost_formula(self) -> None:
        self.assertAlmostEqual(Camera(fov_deg=20).focal_length_px(1024),
                               1024 / 2 / math.tan(math.radians(10)))

    def test_rejects_mixed_and_partial_settings(self) -> None:
        with self.assertRaises(LostConfigError):
            Camera(fov_deg=20, focal_length_mm=49, pixel_size_um=22.2)
        with self.assertRaises(LostConfigError):
            Camera(focal_length_mm=49)
        with self.assertRaises(LostConfigError):
            Camera(fov_deg=0)


class GenerationConfigTests(unittest.TestCase):
    def test_args_include_every_setting_and_image_count(self) -> None:
        args = GenerationConfig(seed=5).to_args(3)
        self.assertEqual(args[:2], ["--generate", "3"])
        self.assertIn("--generate-seed", args)
        self.assertEqual(args[args.index("--generate-seed") + 1], "5")
        self.assertEqual(args[args.index("--generate-shot-noise") + 1], "true")

    def test_interesting_threshold_only_when_set(self) -> None:
        self.assertNotIn("--generate-interesting-threshold", GenerationConfig().to_args())
        args = GenerationConfig(interesting_threshold=0.01).to_args()
        self.assertEqual(args[args.index("--generate-interesting-threshold") + 1], "0.01")

    def test_extra_options_pass_through(self) -> None:
        args = GenerationConfig(extra_options={
            "generate-obstruct": 2, "--generate-obstructInFrame": True, "generate-flag": None,
        }).to_args()
        self.assertEqual(args[-5:], ["--generate-obstruct", "2", "--generate-obstructInFrame",
                                     "true", "--generate-flag"])

    def test_validation_reports_every_problem(self) -> None:
        with self.assertRaises(LostConfigError) as caught:
            GenerationConfig(oversampling=3, de_deg=91, false_max_mag=9, exposure=0)
        message = str(caught.exception)
        for fragment in ("oversampling", "de_deg", "false_max_mag", "exposure"):
            self.assertIn(fragment, message)

    def test_type_checks(self) -> None:
        for bad in ({"x_resolution": 10.5}, {"shot_noise": 1}, {"seed": 2**31},
                    {"read_noise_stddev": float("nan")}, {"false_stars": True}):
            with self.subTest(bad), self.assertRaises(LostConfigError):
                GenerationConfig(**bad)

    def test_replace_revalidates(self) -> None:
        with self.assertRaises(LostConfigError):
            replace(GenerationConfig(), spread_stddev=-1)

    def test_has_image(self) -> None:
        self.assertTrue(GenerationConfig().has_image)
        self.assertFalse(GenerationConfig(centroids_only=True).has_image)


class StageConfigTests(unittest.TestCase):
    def test_algorithm_choices(self) -> None:
        for cls in (CentroidConfig, StarIdConfig, AttitudeConfig):
            with self.subTest(cls.__name__), self.assertRaises(LostConfigError):
                cls(algorithm="nope")

    def test_centroid_args_skip_unset(self) -> None:
        self.assertEqual(CentroidConfig().to_args(), ["--centroid-algo", "cog"])
        self.assertEqual(CentroidConfig(magnitude_filter=5, brightest_only=10).to_args(),
                         ["--centroid-algo", "cog", "--centroid-mag-filter", "5",
                          "--centroid-filter-brightest", "10"])

    def test_star_id_args(self) -> None:
        args = StarIdConfig(algorithm="gv").to_args(Path("/tmp/db.dat"))
        self.assertEqual(args[:4], ["--database", "/tmp/db.dat", "--star-id-algo", "gv"])
        with self.assertRaises(LostConfigError):
            StarIdConfig(database=42)  # type: ignore[arg-type]
        with self.assertRaises(LostConfigError):
            StarIdConfig(max_mismatch_probability=0)

    def test_comparison_args(self) -> None:
        self.assertEqual(ComparisonConfig().to_args(),
                         ["--centroid-compare-threshold", "2.0",
                          "--attitude-compare-threshold", "1.0"])


class DatabaseConfigTests(unittest.TestCase):
    def test_args(self) -> None:
        args = DatabaseConfig().to_args("/tmp/out.dat")
        self.assertEqual(args[:2], ["database", "--kvector"])
        self.assertEqual(args[-2:], ["--output", "/tmp/out.dat"])
        self.assertNotIn("--min-mag", args)
        self.assertIn("--min-mag", DatabaseConfig(min_mag=6).to_args("x"))

    def test_cache_key(self) -> None:
        base = DatabaseConfig()
        self.assertEqual(base.cache_key("a"), DatabaseConfig().cache_key("a"))
        self.assertNotEqual(base.cache_key("a"), base.cache_key("b"))
        self.assertNotEqual(base.cache_key("a"), DatabaseConfig(max_stars=10).cache_key("a"))
        self.assertEqual(hash(base), hash(DatabaseConfig()))

    def test_validation(self) -> None:
        with self.assertRaises(LostConfigError):
            DatabaseConfig(kvector_min_distance_deg=20, kvector_max_distance_deg=10)
        with self.assertRaises(LostConfigError):
            DatabaseConfig(max_stars=0)


class FormatValueTests(unittest.TestCase):
    def test_round_trip_precision(self) -> None:
        value = 0.1 + 0.2
        self.assertEqual(float(format_value(value)), value)

    def test_bools_and_paths(self) -> None:
        self.assertEqual(format_value(True), "true")
        self.assertEqual(format_value(False), "false")
        self.assertEqual(format_value(Path("/a/b")), "/a/b")


if __name__ == "__main__":
    unittest.main()
