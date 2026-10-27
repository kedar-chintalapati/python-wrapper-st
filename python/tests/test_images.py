"""PNG reading and writing without third-party packages."""

from __future__ import annotations

import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from lost_api import GrayImage, read_png, write_png
from lost_api.images import _read_png_stdlib, as_gray_image, png_size

try:
    import numpy
except ImportError:
    numpy = None


def lost_gray(r: int, g: int, b: int) -> int:
    """LOST's SurfaceToGrayscaleImage: luminosity weights, then C round()."""
    return int(r * 0.21 + g * 0.71 + b * 0.07 + 0.5)


def encode_png(width: int, height: int, samples: bytes, color_type: int, filter_type: int) -> bytes:
    """A reference encoder that applies one PNG filter type to every row."""
    bpp = {0: 1, 2: 3, 4: 2, 6: 4}[color_type]
    stride = width * bpp
    previous = bytes(stride)
    rows = bytearray()
    for y in range(height):
        line = samples[y * stride:(y + 1) * stride]
        out = bytearray()
        for i, value in enumerate(line):
            a = line[i - bpp] if i >= bpp else 0
            b = previous[i]
            c = previous[i - bpp] if i >= bpp else 0
            if filter_type == 0:
                predictor = 0
            elif filter_type == 1:
                predictor = a
            elif filter_type == 2:
                predictor = b
            elif filter_type == 3:
                predictor = (a + b) >> 1
            else:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                predictor = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
            out.append((value - predictor) & 0xFF)
        rows += bytes([filter_type]) + out
        previous = line

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    header = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(bytes(rows)))
            + chunk(b"IEND", b""))


class PngTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="lost_api_png_"))
        self.pixels = bytes((x * 37 + y * 91) % 256 for y in range(5) for x in range(7))

    def test_round_trip(self) -> None:
        image = GrayImage(7, 5, self.pixels)
        path = write_png(self.dir / "a.png", image)
        self.assertEqual(png_size(path), (7, 5))
        self.assertEqual(_read_png_stdlib(path, False), image)
        self.assertEqual(read_png(path), image)

    def test_every_filter_type_decodes(self) -> None:
        for filter_type in range(5):
            with self.subTest(filter_type=filter_type):
                path = self.dir / f"f{filter_type}.png"
                path.write_bytes(encode_png(7, 5, self.pixels, 0, filter_type))
                self.assertEqual(_read_png_stdlib(path, False).pixels, self.pixels)

    def test_equal_rgb_reads_exactly_and_lost_view_darkens(self) -> None:
        rgb = bytes(v for p in self.pixels for v in (p, p, p))
        path = self.dir / "rgb.png"
        path.write_bytes(encode_png(7, 5, rgb, 2, 4))
        self.assertEqual(_read_png_stdlib(path, False).pixels, self.pixels)
        self.assertEqual(_read_png_stdlib(path, True).pixels,
                         bytes(lost_gray(p, p, p) for p in self.pixels))

    def test_color_uses_lost_luminosity(self) -> None:
        rgb = bytes([200, 100, 50]) * 4
        path = self.dir / "color.png"
        path.write_bytes(encode_png(2, 2, rgb, 2, 1))
        self.assertEqual(_read_png_stdlib(path, False).pixels, bytes([lost_gray(200, 100, 50)]) * 4)

    def test_alpha_is_premultiplied(self) -> None:
        gray_alpha = bytes([200, 128]) * 4
        path = self.dir / "ga.png"
        path.write_bytes(encode_png(2, 2, gray_alpha, 4, 3))
        p = int(200 * 128 / 255 + 0.5)
        self.assertEqual(_read_png_stdlib(path, False).pixels, bytes([lost_gray(p, p, p)]) * 4)

    def test_nested_lists(self) -> None:
        image = as_gray_image([[0, 1, 2], [3, 4, 255]])
        self.assertEqual((image.width, image.height, image[1, 2]), (3, 2, 255))
        with self.assertRaises(ValueError):
            as_gray_image([[0, 256]])
        with self.assertRaises(ValueError):
            as_gray_image([[0, 1], [2]])

    def test_gray_image_validation(self) -> None:
        with self.assertRaises(ValueError):
            GrayImage(2, 2, b"\x00")
        with self.assertRaises(IndexError):
            GrayImage(1, 1, b"\x00")[0, 1]

    def test_not_a_png(self) -> None:
        path = self.dir / "x.png"
        path.write_bytes(b"nope")
        self.assertIsNone(png_size(path))
        with self.assertRaises(ValueError):
            _read_png_stdlib(path, False)

    @unittest.skipIf(numpy is None, "numpy not installed")
    def test_numpy_round_trip(self) -> None:
        array = numpy.arange(35, dtype=numpy.uint8).reshape(5, 7)
        path = write_png(self.dir / "np.png", array)
        numpy.testing.assert_array_equal(read_png(path).to_numpy(), array)
        with self.assertRaises(TypeError):
            write_png(self.dir / "f.png", array.astype(float))


if __name__ == "__main__":
    unittest.main()
