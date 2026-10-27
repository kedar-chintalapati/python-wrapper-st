"""Parsing LOST's key/value output into result objects."""

from __future__ import annotations

import math
import unittest

from lost_api import Attitude
from lost_api.results import (
    parse_attitude,
    parse_attitude_comparison,
    parse_centroid_comparison,
    parse_centroids,
    parse_key_values,
    parse_star_id_comparison,
    parse_timing,
)

ACTUAL = """\
num_actual_centroids 3
actual_centroid_0_x 118.017350112
actual_centroid_0_y 122.9
actual_centroid_0_id 2061
actual_centroid_1_x 5
actual_centroid_1_y 6.5
actual_centroid_2_x 7
actual_centroid_2_y 8
actual_centroid_2_id 1790
actual_centroid_0_expected_id_0 2061
actual_centroid_2_expected_id_0 1851
actual_centroid_2_expected_id_1 1790
"""


class KeyValueTests(unittest.TestCase):
    def test_types(self) -> None:
        data = parse_key_values("a 1\nb 2.5\nc -nan\nd inf\ne hello world\n\n")
        self.assertEqual(data["a"], 1)
        self.assertIsInstance(data["a"], int)
        self.assertEqual(data["b"], 2.5)
        self.assertTrue(math.isnan(data["c"]))
        self.assertEqual(data["d"], math.inf)
        self.assertEqual(data["e"], "hello world")


class CentroidParsingTests(unittest.TestCase):
    def test_ids_and_expected_ids(self) -> None:
        centroids = parse_centroids(parse_key_values(ACTUAL), "actual")
        self.assertEqual([c.index for c in centroids], [0, 1, 2])
        self.assertAlmostEqual(centroids[0].x, 118.017350112)
        self.assertEqual(centroids[0].catalog_id, 2061)
        self.assertTrue(centroids[0].correctly_identified)
        self.assertIsNone(centroids[1].catalog_id)
        self.assertIsNone(centroids[1].correctly_identified)
        self.assertEqual(centroids[2].expected_catalog_ids, (1851, 1790))
        self.assertTrue(centroids[2].correctly_identified)

    def test_other_prefixes_are_ignored(self) -> None:
        self.assertEqual(parse_centroids(parse_key_values(ACTUAL), "input"), ())


class AttitudeTests(unittest.TestCase):
    KNOWN = parse_key_values(
        "attitude_known 1\nattitude_ra 88\nattitude_de 7\nattitude_roll 0\n"
        "attitude_i 0.04\nattitude_j -0.04\nattitude_k 0.69\nattitude_real -0.72\n"
    )

    def test_known(self) -> None:
        attitude = parse_attitude(self.KNOWN)
        self.assertEqual(attitude.ra_deg, 88)
        self.assertEqual(attitude.quaternion, (-0.72, 0.04, -0.04, 0.69))

    def test_unknown_and_prefix(self) -> None:
        self.assertIsNone(parse_attitude(parse_key_values("attitude_known 0\n")))
        self.assertIsNone(parse_attitude(self.KNOWN, "expected_"))

    def test_angle_between_attitudes(self) -> None:
        identity = Attitude(0, 0, 0, (1.0, 0.0, 0.0, 0.0))
        quarter_turn = Attitude(0, 0, 0, (math.cos(math.pi / 4), 0.0, 0.0, math.sin(math.pi / 4)))
        self.assertAlmostEqual(identity.angle_to(quarter_turn), 90.0)
        self.assertAlmostEqual(quarter_turn.angle_to(identity), 90.0)
        # q and -q are the same rotation
        negated = Attitude(0, 0, 0, tuple(-x for x in quarter_turn.quaternion))
        self.assertAlmostEqual(quarter_turn.angle_to(negated), 0.0, places=5)


class ComparisonParsingTests(unittest.TestCase):
    def test_attitude_comparison_converts_radians_and_nan(self) -> None:
        comparison = parse_attitude_comparison(parse_key_values(
            "attitude_error_mean 0.01\nattitude_availability 0.5\nattitude_error_rate 0.25\n"))
        self.assertAlmostEqual(comparison.mean_error_deg, math.degrees(0.01))
        none_correct = parse_attitude_comparison(parse_key_values(
            "attitude_error_mean -nan\nattitude_availability 0\nattitude_error_rate 0\n"))
        self.assertIsNone(none_correct.mean_error_deg)
        self.assertIsNone(parse_attitude_comparison({}))

    def test_star_id_comparison_single_and_batch(self) -> None:
        single = parse_star_id_comparison(parse_key_values(
            "starid_num_correct 4\nstarid_num_incorrect 0\nstarid_num_total 5\n"
            "starid_num_images_correct 1\nstarid_num_images_incorrect 0\n"))
        self.assertEqual((single.num_correct, single.num_total), (4, 5))
        batch = parse_star_id_comparison(parse_key_values(
            "starid_num_images_correct 7\nstarid_num_images_incorrect 1\n"))
        self.assertEqual(batch.num_images_correct, 7)
        self.assertIsNone(batch.num_correct)

    def test_centroid_comparison(self) -> None:
        comparison = parse_centroid_comparison(parse_key_values(
            "centroids_num_correct 5\ncentroids_num_extra 2\ncentroids_mean_error 0.25\n"))
        self.assertEqual((comparison.num_correct, comparison.num_extra), (5, 2))
        self.assertEqual(comparison.mean_error_px, 0.25)

    def test_timing(self) -> None:
        timing = parse_timing(parse_key_values(
            "starid_average_ns 2000000\nstarid_min_ns 1\nstarid_max_ns 3\nstarid_95%_ns 3\n"
            "total_average_ns 5\n"))
        self.assertEqual(set(timing), {"starid"})
        self.assertEqual(timing["starid"].average_ms, 2.0)


if __name__ == "__main__":
    unittest.main()
