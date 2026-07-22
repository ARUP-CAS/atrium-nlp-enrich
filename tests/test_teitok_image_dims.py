"""
tests/test_teitok_image_dims.py
===============================
Targeted tests for ``teitok_alto._read_image_dimensions`` — the stdlib binary
header reader that recovers (width, height) from PNG / JPEG / TIFF without
Pillow. It was the one sizeable block of teitok_alto.py left uncovered by
test_teitok_preservation.py, and header parsing is exactly the kind of code
that benefits from explicit format fixtures.
"""

import struct

from teitok_alto import _read_image_dimensions


def _png(width, height):
    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR" + struct.pack(">II", width, height)


def _jpeg(width, height):
    # SOI + SOF0 segment carrying height then width (big-endian).
    return (
        b"\xff\xd8"
        + b"\xff\xc0"
        + struct.pack(">H", 17)  # segment length (unused on the return path)
        + b"\x08"  # sample precision
        + struct.pack(">H", height)
        + struct.pack(">H", width)
        + b"\x03\x01\x22\x00"  # component bytes (ignored)
    )


def test_png_dimensions(tmp_path):
    p = tmp_path / "page.png"
    p.write_bytes(_png(1240, 1754))
    assert _read_image_dimensions(p) == (1240, 1754)


def test_jpeg_dimensions(tmp_path):
    p = tmp_path / "page.jpg"
    p.write_bytes(_jpeg(800, 600))
    assert _read_image_dimensions(p) == (800, 600)


def test_missing_file_returns_none(tmp_path):
    assert _read_image_dimensions(tmp_path / "does_not_exist.png") is None


def test_non_image_returns_none(tmp_path):
    p = tmp_path / "notimage.txt"
    p.write_bytes(b"just some plain text, definitely not an image header")
    assert _read_image_dimensions(p) is None


def test_truncated_jpeg_returns_none(tmp_path):
    p = tmp_path / "trunc.jpg"
    p.write_bytes(b"\xff\xd8\xff")  # SOI + a lone marker byte, no segment
    assert _read_image_dimensions(p) is None
