"""Runner behaviour that doesn't need a real LOST build, using small fake executables."""

from __future__ import annotations

import os
import stat
import tempfile
import textwrap
import unittest
from pathlib import Path

from lost_api import (
    AttitudeConfig,
    CentroidConfig,
    GenerationConfig,
    LostBinary,
    LostBinaryNotFoundError,
    LostConfigError,
    LostExecutionError,
    LostRunner,
    LostTimeoutError,
    LostUnsupportedOptionError,
    StarIdConfig,
)


@unittest.skipIf(os.name == "nt", "fake executables are shell scripts")
class FakeBinaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="lost_api_fake_"))
        self.catalog = self.dir / "bright-star-catalog.tsv"
        self.catalog.write_text("fake\n", encoding="utf-8")

    def fake(self, body: str) -> LostRunner:
        script = self.dir / "lost"
        script.write_text("#!/bin/sh\n" + textwrap.dedent(body), encoding="utf-8")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        return LostRunner(LostBinary(script, catalog_path=self.catalog), work_dir=self.dir / "work",
                          cache_dir=self.dir / "cache")

    def test_unrecognized_option(self) -> None:
        runner = self.fake("""\
            echo "./lost: unrecognized option '--generate-obstruct'" >&2
            echo "Illegal flag"
            exit 1
        """)
        with self.assertRaises(LostUnsupportedOptionError) as caught:
            runner.generate()
        self.assertEqual(caught.exception.option, "generate-obstruct")
        self.assertIn("--generate-obstruct", str(caught.exception))

    def test_lost_error_lines_become_the_message(self) -> None:
        runner = self.fake("""\
            echo "Reading 10 bytes of database" >&2
            echo "ERROR: One of the pixels had too many photons. Generated image would not be physically accurate, exiting."
            exit 1
        """)
        with self.assertRaises(LostExecutionError) as caught:
            runner.generate()
        error = caught.exception
        self.assertEqual(error.returncode, 1)
        self.assertFalse(error.crashed)
        self.assertIn("too many photons", str(error))
        self.assertIn("--generate", error.command)

    def test_assertion_crash(self) -> None:
        runner = self.fake("kill -ABRT $$\n")
        with self.assertRaises(LostExecutionError) as caught:
            runner.generate()
        self.assertTrue(caught.exception.crashed)
        self.assertIn("SIGABRT", str(caught.exception))

    def test_timeout(self) -> None:
        runner = self.fake("sleep 5\n")
        with self.assertRaises(LostTimeoutError):
            runner.generate(timeout=0.2)

    def test_catalog_is_passed_and_outputs_are_parsed(self) -> None:
        runner = self.fake("""\
            echo "$LOST_BSC_PATH" > catalog_seen
            while [ $# -gt 0 ]; do
              case "$1" in
                --print-expected-attitude) printf 'expected_attitude_known 0\\n' > "$2" ;;
                --print-input-centroids) printf 'num_input_centroids 1\\ninput_centroid_0_x 1.5\\ninput_centroid_0_y 2.5\\ninput_centroid_0_id 42\\n' > "$2" ;;
              esac
              shift
            done
        """)
        result = runner.generate(output_dir=self.dir / "out")
        self.assertEqual((self.dir / "out" / "catalog_seen").read_text().strip(), str(self.catalog))
        self.assertEqual(len(result.input_centroids), 1)
        self.assertEqual(result.input_centroids[0].catalog_id, 42)
        self.assertIsNone(result.expected_attitude)
        self.assertFalse(result.image_path.is_file())  # the fake writes no image

    def test_reused_output_dir_drops_stale_results(self) -> None:
        runner = self.fake("exit 0\n")
        out = self.dir / "reuse"
        out.mkdir()
        (out / "attitude.txt").write_text("attitude_known 1\nattitude_ra 1\n", encoding="utf-8")
        (out / "input_centroids.txt").write_text("input_centroid_0_x 1\ninput_centroid_0_y 1\n")
        result = runner.generate(output_dir=out)
        self.assertEqual(result.input_centroids, ())
        self.assertFalse((out / "attitude.txt").exists())

    def test_missing_catalog(self) -> None:
        script = self.dir / "lost"
        script.write_text("#!/bin/sh\n", encoding="utf-8")
        script.chmod(0o755)
        binary = LostBinary(script, catalog_path=self.dir / "missing.tsv")
        with self.assertRaises(LostBinaryNotFoundError):
            binary.run(["pipeline"])

    def test_missing_binary(self) -> None:
        with self.assertRaises(LostBinaryNotFoundError):
            LostBinary(self.dir / "nope").path

    def test_close_removes_temporary_outputs_only(self) -> None:
        runner = LostRunner(LostBinary(self.dir / "lost", catalog_path=self.catalog),
                            cache_dir=self.dir / "cache")
        work = runner.work_dir
        self.assertTrue(work.is_dir())
        runner.close()
        self.assertFalse(work.exists())
        explicit = LostRunner(LostBinary(self.dir / "lost"), work_dir=self.dir / "keep")
        explicit.work_dir
        explicit.close()
        self.assertTrue((self.dir / "keep").is_dir())


class StageValidationTests(unittest.TestCase):
    """These are rejected before LOST is ever started, so no binary is needed."""

    def setUp(self) -> None:
        self.runner = LostRunner(LostBinary("/nonexistent/lost"), auto_build=False)

    def test_centroids_need_pixels(self) -> None:
        with self.assertRaises(LostConfigError):
            self.runner.run_generated(GenerationConfig(centroids_only=True),
                                      centroid=CentroidConfig())

    def test_attitude_after_centroiding_needs_star_id(self) -> None:
        with self.assertRaises(LostConfigError):
            self.runner.run_generated(centroid=CentroidConfig(), attitude=AttitudeConfig())

    def test_real_images_need_centroiding(self) -> None:
        with self.assertRaises(LostConfigError):
            self.runner.run_image("/nonexistent.png", star_id=StarIdConfig())
        with self.assertRaises(LostConfigError):
            self.runner.run_image("/nonexistent.png")

    def test_missing_image(self) -> None:
        with self.assertRaises(LostConfigError):
            self.runner.run_image("/nonexistent.png", centroid=CentroidConfig())

    def test_batch_size(self) -> None:
        for bad in (0, -1, 1.5, True):
            with self.subTest(bad), self.assertRaises(LostConfigError):
                self.runner.run_generated_batch(bad)


if __name__ == "__main__":
    unittest.main()
