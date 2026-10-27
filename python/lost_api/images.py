"""Reading and writing the 8-bit grayscale images LOST works with.

Only the standard library is required. When Pillow and/or numpy are installed they are used for
speed, and :meth:`GrayImage.to_numpy` becomes available.

Grayscale PNGs, and RGB PNGs whose channels are all equal (which is how LOST saves the images it
generates), are read back exactly. Other color PNGs are converted with the luminosity weights LOST
uses (``0.21 R + 0.71 G + 0.07 B``, after cairo's alpha premultiplication). Pass
``lost_view=True`` to always apply LOST's conversion; because those weights sum to 0.99, LOST sees
every PNG it loads about 1% darker than its stored gray values.
"""

from __future__ import annotations

import os
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Union

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_CHANNELS = {0: 1, 2: 3, 4: 2, 6: 4}  # PNG color type -> samples per pixel (8-bit only)


@dataclass(frozen=True)
class GrayImage:
    """A row-major 8-bit grayscale image."""

    width: int
    height: int
    pixels: bytes

    def __post_init__(self) -> None:
        if self.width < 1 or self.height < 1:
            raise ValueError(f"image dimensions must be positive, got {self.width}x{self.height}")
        if len(self.pixels) != self.width * self.height:
            raise ValueError(
                f"expected {self.width * self.height} pixels for {self.width}x{self.height}, "
                f"got {len(self.pixels)}"
            )

    def __getitem__(self, position: tuple[int, int]) -> int:
        """``image[y, x]``, like a numpy array."""
        y, x = position
        if not (0 <= x < self.width and 0 <= y < self.height):
            raise IndexError(f"pixel ({y}, {x}) is outside the {self.height}x{self.width} image")
        return self.pixels[y * self.width + x]

    @property
    def shape(self) -> tuple[int, int]:
        return (self.height, self.width)

    def to_numpy(self) -> Any:
        import numpy as np

        return np.frombuffer(self.pixels, dtype=np.uint8).reshape(self.height, self.width).copy()

    @classmethod
    def from_array(cls, array: Any) -> "GrayImage":
        """Build from a 2-D (grayscale) or 3-D (RGB/RGBA) array of integers in 0..255."""
        return _image_from_array(array)


ImageLike = Union[GrayImage, str, os.PathLike, Any]


# --------------------------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------------------------


def write_png(path: str | os.PathLike, image: Any) -> Path:
    """Write ``image`` (a GrayImage or a 2-D uint8-compatible array) as an 8-bit grayscale PNG."""
    if not isinstance(image, GrayImage):
        image = _image_from_array(image)
    rows = b"".join(
        b"\x00" + image.pixels[y * image.width:(y + 1) * image.width] for y in range(image.height)
    )
    header = struct.pack(">IIBBBBB", image.width, image.height, 8, 0, 0, 0, 0)
    path = Path(path)
    with path.open("wb") as handle:
        handle.write(_PNG_SIGNATURE)
        handle.write(_chunk(b"IHDR", header))
        handle.write(_chunk(b"IDAT", zlib.compress(rows, 6)))
        handle.write(_chunk(b"IEND", b""))
    return path


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def _image_from_array(array: Any) -> GrayImage:
    try:
        import numpy as np
    except ImportError:
        np = None  # type: ignore[assignment]

    if np is not None and not isinstance(array, (list, tuple)):
        arr = np.asarray(array)
        if arr.dtype.kind == "b":
            arr = arr.astype(np.uint8) * 255
        if arr.dtype.kind == "f":
            raise TypeError(
                "Float images are ambiguous; scale to 0..255 and convert to uint8 first, "
                "e.g. (img * 255).clip(0, 255).astype('uint8')"
            )
        if arr.dtype.kind not in "ui":
            raise TypeError(f"Unsupported image dtype {arr.dtype}")
        if arr.size and (arr.min() < 0 or arr.max() > 255):
            raise ValueError("Image values must be within 0..255")
        arr = arr.astype(np.uint8, copy=False)
        if arr.ndim == 3 and arr.shape[2] in (3, 4):
            height, width, channels = arr.shape
            return _gray_from_samples(width, height, np.ascontiguousarray(arr).tobytes(),
                                      channels, lost_view=False)
        if arr.ndim != 2:
            raise ValueError(f"Expected a 2-D grayscale or 3-D RGB(A) image, got shape {arr.shape}")
        height, width = arr.shape
        return GrayImage(width, height, np.ascontiguousarray(arr).tobytes())

    # plain nested sequences: [[row0...], [row1...], ...]
    rows = [list(row) for row in array]
    if not rows or not rows[0]:
        raise ValueError("Image must not be empty")
    width = len(rows[0])
    if any(len(row) != width for row in rows):
        raise ValueError("All image rows must have the same length")
    flat = [value for row in rows for value in row]
    if any(not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= 255 for v in flat):
        raise ValueError("Image values must be integers within 0..255")
    return GrayImage(width, len(rows), bytes(flat))


def _rgb_to_lost_gray_numpy(arr: Any) -> Any:
    import numpy as np

    rgb = arr[..., :3].astype(np.float64)
    if arr.shape[2] == 4:  # cairo premultiplies by alpha when it loads a PNG
        rgb = np.round(rgb * arr[..., 3:4] / 255.0)
    gray = rgb[..., 0] * 0.21 + rgb[..., 1] * 0.71 + rgb[..., 2] * 0.07
    return np.floor(gray + 0.5).astype(np.uint8)  # C round(): half away from zero


# --------------------------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------------------------


def read_png(path: str | os.PathLike, *, lost_view: bool = False) -> GrayImage:
    """Read a PNG as a grayscale image (see the module docstring for the conversion rules)."""
    path = Path(path)
    try:
        from PIL import Image  # type: ignore[import-not-found]
    except ImportError:
        return _read_png_stdlib(path, lost_view)

    with Image.open(path) as im:
        im.load()
        if im.mode == "L" and not lost_view:
            return GrayImage(im.width, im.height, im.tobytes())
        has_alpha = "A" in im.getbands() or "transparency" in im.info
        converted = im.convert("RGBA" if has_alpha else "RGB")
        channels = len(converted.getbands())
        return _gray_from_samples(converted.width, converted.height, converted.tobytes(),
                                  channels, lost_view)


def _read_png_stdlib(path: Path, lost_view: bool) -> GrayImage:
    data = path.read_bytes()
    if not data.startswith(_PNG_SIGNATURE):
        raise ValueError(f"{path} is not a PNG file")
    offset = len(_PNG_SIGNATURE)
    header: tuple[int, ...] | None = None
    compressed = bytearray()
    while offset + 8 <= len(data):
        (length,) = struct.unpack(">I", data[offset:offset + 4])
        kind = data[offset + 4:offset + 8]
        body = data[offset + 8:offset + 8 + length]
        offset += 12 + length
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            compressed += body
        elif kind == b"IEND":
            break
    if header is None:
        raise ValueError(f"{path} has no IHDR chunk")
    width, height, bit_depth, color_type, _, _, interlace = header
    if bit_depth != 8 or color_type not in _CHANNELS or interlace != 0:
        raise ValueError(
            f"{path}: only non-interlaced 8-bit gray/RGB/RGBA PNGs can be read without Pillow "
            f"(bit depth {bit_depth}, color type {color_type}, interlace {interlace}). "
            f"Install Pillow to read other PNGs."
        )
    channels = _CHANNELS[color_type]
    samples = _unfilter(zlib.decompress(bytes(compressed)), width, height, channels)
    if color_type in (0, 4):  # gray (+ alpha): expand so the shared conversion applies
        out_channels = 3 if color_type == 0 else 4
        if color_type == 0 and not lost_view:
            return GrayImage(width, height, samples)
        expanded = bytearray(width * height * out_channels)
        for c in range(3):
            expanded[c::out_channels] = samples[0::channels]
        if color_type == 4:
            expanded[3::4] = samples[1::2]
        samples, channels = bytes(expanded), out_channels
    return _gray_from_samples(width, height, samples, channels, lost_view)


def _unfilter(raw: bytes, width: int, height: int, bpp: int) -> bytes:
    stride = width * bpp
    if len(raw) != height * (stride + 1):
        raise ValueError("PNG image data has an unexpected length")
    out = bytearray(height * stride)
    previous = bytearray(stride)
    for y in range(height):
        start = y * (stride + 1)
        kind = raw[start]
        line = bytearray(raw[start + 1:start + 1 + stride])
        if kind == 1:  # Sub
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif kind == 2:  # Up
            line = bytearray((a + b) & 0xFF for a, b in zip(line, previous))
        elif kind == 3:  # Average
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + previous[i]) >> 1)) & 0xFF
        elif kind == 4:  # Paeth
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b = previous[i]
                c = previous[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                if pa <= pb and pa <= pc:
                    predictor = a
                elif pb <= pc:
                    predictor = b
                else:
                    predictor = c
                line[i] = (line[i] + predictor) & 0xFF
        elif kind != 0:
            raise ValueError(f"Unknown PNG filter type {kind}")
        out[y * stride:(y + 1) * stride] = line
        previous = line
    return bytes(out)


def _gray_from_samples(width: int, height: int, samples: bytes, channels: int,
                       lost_view: bool) -> GrayImage:
    """Collapse interleaved RGB or RGBA samples to one gray channel."""
    r, g, b = samples[0::channels], samples[1::channels], samples[2::channels]
    opaque = channels == 3 or samples[3::channels] == b"\xff" * (width * height)
    if not lost_view and opaque and r == g == b:
        return GrayImage(width, height, r)  # gray stored as RGB, e.g. LOST's own images
    try:
        import numpy as np
    except ImportError:
        np = None  # type: ignore[assignment]
    if np is not None:
        arr = np.frombuffer(samples, dtype=np.uint8).reshape(height, width, channels)
        return GrayImage(width, height, _rgb_to_lost_gray_numpy(arr).tobytes())
    if channels == 4:
        alpha = samples[3::channels]
        r, g, b = (bytes(int(v * a / 255 + 0.5) for v, a in zip(ch, alpha)) for ch in (r, g, b))
    return GrayImage(width, height, bytes(
        int(rv * 0.21 + gv * 0.71 + bv * 0.07 + 0.5) for rv, gv, bv in zip(r, g, b)
    ))


def png_size(path: str | os.PathLike) -> tuple[int, int] | None:
    """``(width, height)`` from a PNG header, or ``None`` if the file is not a readable PNG."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(24)
    except OSError:
        return None
    if len(head) < 24 or not head.startswith(_PNG_SIGNATURE) or head[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", head[16:24])
    return width, height


def as_gray_image(image: ImageLike) -> GrayImage:
    """Coerce a path, GrayImage or array into a GrayImage."""
    if isinstance(image, GrayImage):
        return image
    if isinstance(image, (str, os.PathLike)):
        return read_png(image)
    return _image_from_array(image)
