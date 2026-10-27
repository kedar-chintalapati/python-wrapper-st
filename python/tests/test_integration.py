"""End-to-end tests against a real LOST build.

Skipped unless a LOST executable can be found (see LostBinary). They never build LOST themselves;
run ``make release`` first. Set ``LOST_TEST_IMAGE`` to a copy of img_7660.png (from LOST's
README) to also check the real-image example.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
import warnings
from dataclasses import replace
from pathlib import Path

from lost_api import (
    AttitudeConfig,
    Camera,
    CentroidConfig,
    DatabaseConfig,
    GenerationConfig,
    LostBinary,
    LostConfigError,
    LostError,
    LostRunner,
    LostUnsupportedOptionError,
    StarIdConfig,
)

# A field LOST's defaults identify reliably (Orion, as in the README).
ORION = GenerationConfig(x_resolution=512, y_resolution=512, false_stars=10, seed=12)
FULL = dict(centroid=CentroidConfig(), star_id=StarIdConfig(), attitude=AttitudeConfig())


def _find_binary() -> LostBinary | None:
    binary = LostBinary(auto_build=False)
    try:
        binary.path
        binary.catalog_path
    except LostError:
        return None
    return binary


BINARY = _find_binary()


@unittest.skipIf(BINARY is None, "no LOST executable found; run `make release`")
class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="lost_api_it_"))
        cls.runner = LostRunner(BINARY, work_dir=cls.tmp / "work", cache_dir=cls.tmp / "cache")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_database_is_cached(self) -> None:
        config = DatabaseConfig(max_stars=1000)
        first = self.runner.database(config)
        second = self.runner.database(config)
        self.assertTrue(first.path.is_file())
        self.assertGreater(first.size_bytes, 0)
        self.assertEqual(first.path, second.path)
        self.assertTrue(second.cached)
        explicit = self.runner.build_database(config, self.tmp / "explicit.dat")
        self.assertFalse(explicit.cached)
        self.assertEqual(explicit.path.read_bytes(), first.path.read_bytes())

    def test_generate_reports_truth_at_full_precision(self) -> None:
        result = self.runner.generate(replace(ORION, ra_deg=88.123456789), plots=True)
        image = result.load_gray_image()
        self.assertEqual(image.shape, (512, 512))
        self.assertGreater(max(image.pixels), 0)
        self.assertGreater(len(result.input_centroids), 5)
        self.assertGreater(len(result.expected_centroids), 5)
        self.assertAlmostEqual(result.expected_attitude.ra_deg, 88.123456789, places=7)
        self.assertEqual(set(result.plots), {"input", "expected"})
        self.assertTrue(any(c.catalog_id for c in result.expected_centroids))

    def test_full_pipeline_on_generated_image(self) -> None:
        result = self.runner.run_generated(ORION, **FULL)
        self.assertIsNotNone(result.attitude)
        self.assertLess(result.attitude_error_deg, 0.1)
        self.assertGreaterEqual(len(result.identified_stars), 3)
        self.assertTrue(all(c.correctly_identified for c in result.identified_stars))
        self.assertEqual(result.star_id_comparison.num_incorrect, 0)
        self.assertGreater(result.centroid_comparison.num_correct, 0)
        self.assertGreater(result.attitude_comparison.availability, 0)
        self.assertIn("total", result.timing)

    def test_star_id_on_generated_centroids(self) -> None:
        result = self.runner.run_generated(
            replace(ORION, centroids_only=True, perturb_centroids_stddev=0.1),
            star_id=StarIdConfig(), attitude=AttitudeConfig(),
        )
        self.assertIsNone(result.image_path)
        self.assertGreater(len(result.identified_stars), 5)
        self.assertTrue(all(c.correctly_identified for c in result.identified_stars))
        self.assertLess(result.attitude_error_deg, 0.1)

    def test_attitude_from_true_identities(self) -> None:
        result = self.runner.run_generated(ORION, attitude=AttitudeConfig(algorithm="quest"))
        self.assertLess(result.attitude_error_deg, 1e-3)

    def test_identify_accepts_an_image_in_memory(self) -> None:
        truth = self.runner.generate(ORION)
        result = self.runner.identify(truth.load_gray_image(), plots=True)
        self.assertEqual(result.source, "image")
        self.assertLess(result.attitude.angle_to(truth.expected_attitude), 0.1)
        self.assertTrue(result.plots["output"].is_file())

    def test_identify_in_the_generated_output_dir_keeps_the_image(self) -> None:
        truth = self.runner.generate(ORION, output_dir=self.tmp / "same_dir")
        result = self.runner.identify(truth.image_path, output_dir=truth.output_dir)
        self.assertTrue(truth.image_path.is_file())
        self.assertIsNotNone(result.attitude)

    def test_batch(self) -> None:
        batch = self.runner.run_generated_batch(
            4, replace(ORION, random_attitudes=True), **FULL)
        self.assertEqual(batch.num_images, 4)
        sid = batch.star_id_comparison
        self.assertLessEqual(sid.num_images_correct + sid.num_images_incorrect, 4)
        self.assertTrue(0 <= batch.attitude_comparison.availability <= 1)
        self.assertGreater(batch.mean_num_input_centroids, 0)

    def test_many_preserves_order_in_parallel(self) -> None:
        configs = [replace(ORION, roll_deg=float(r)) for r in (0, 30, 60, 90)]
        parallel = self.runner.run_generated_many(configs, max_workers=4, **FULL)
        rolls = [round(r.expected_attitude.roll_deg) % 360 for r in parallel]
        self.assertEqual(rolls, [0, 30, 60, 90])
        self.assertTrue(all(r.attitude_error_deg < 0.1 for r in parallel))

    def test_many_can_return_exceptions(self) -> None:
        configs = [ORION, replace(ORION, extra_options={"generate-not-an-option": 1})]
        results = self.runner.run_generated_many(configs, return_exceptions=True, max_workers=2)
        self.assertIsNotNone(results[0].expected_attitude)
        self.assertIsInstance(results[1], LostUnsupportedOptionError)

    def test_interesting_threshold_is_supported(self) -> None:
        result = self.runner.generate(replace(ORION, interesting_threshold=0.001))
        self.assertTrue(result.image_path.is_file())

    def test_unsuitable_database_warns(self) -> None:
        narrow = DatabaseConfig(max_stars=1000, kvector_max_distance_deg=2)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            self.runner.run_generated(ORION, star_id=StarIdConfig(database=narrow))
        self.assertTrue(any("most star pairs" in str(w.message) for w in caught))

    def test_missing_database_file(self) -> None:
        with self.assertRaises(LostConfigError):
            self.runner.run_generated(ORION, star_id=StarIdConfig(database=self.tmp / "none.dat"))

    @unittest.skipUnless(os.environ.get("LOST_TEST_IMAGE"), "set LOST_TEST_IMAGE to img_7660.png")
    def test_readme_real_image(self) -> None:
        result = self.runner.identify(
            os.environ["LOST_TEST_IMAGE"],
            Camera.from_optics(focal_length_mm=49, pixel_size_um=22.2),
            centroid=CentroidConfig(magnitude_filter=5),
            star_id=StarIdConfig(angular_tolerance_deg=0.05, false_stars_estimate=1000,
                                 max_mismatch_probability=0.0001),
        )
        # README: Astrometry.net agrees with RA 17, DE 63, roll 12 (integer degrees)
        attitude = result.attitude
        self.assertEqual((int(attitude.ra_deg), int(attitude.de_deg), int(attitude.roll_deg)),
                         (17, 63, 12))


if __name__ == "__main__":
    unittest.main()
