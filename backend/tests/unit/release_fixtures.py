"""Structurally valid minimal media fixtures for release-asset tests.

The first version of these tests used fabricated media (a zero-filled ``.mp4``
and an eight-byte PNG header) and they passed, which is exactly the defect
being corrected. Positive tests now build files that a real parser accepts:
a genuine PNG with a CRC-correct IHDR and real pixel data, a JPEG with a real
start-of-frame record, and an ISO base media container whose boxes tile the
file. Nothing here fabricates a *recording*; these are structural fixtures for
the validator, never release evidence.
"""

from __future__ import annotations

import random
import struct
import zlib

__all__ = ["make_iso_bmff", "make_jpeg", "make_png"]


def _png_chunk(chunk_type: bytes, body: bytes) -> bytes:
    return (
        struct.pack(">I", len(body))
        + chunk_type
        + body
        + struct.pack(">I", zlib.crc32(chunk_type + body) & 0xFFFFFFFF)
    )


def make_png(width: int = 1280, height: int = 720, seed: int = 11) -> bytes:
    """A real 8-bit RGB PNG with deterministic non-compressible pixel data."""
    rng = random.Random(seed)
    raw = bytearray()
    for _ in range(height):
        raw.append(0)  # filter type 0 (None) for this scanline
        raw.extend(rng.randbytes(width * 3))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(bytes(raw), 1))
        + _png_chunk(b"IEND", b"")
    )


def make_jpeg(width: int = 1280, height: int = 720, padding: int = 32 * 1024) -> bytes:
    """A JPEG whose marker stream carries a valid SOF0 dimensions record."""
    # JFIF APP0: the declared length 16 covers the 2 length bytes plus a
    # 14-byte payload (identifier, version, units, densities, thumbnail size).
    app0 = (
        b"\xff\xe0"
        + struct.pack(">H", 16)
        + b"JFIF\x00"
        + b"\x01\x02"
        + b"\x00"
        + struct.pack(">HH", 1, 1)
        + b"\x00\x00"
    )
    sof0 = (
        b"\xff\xc0"
        + struct.pack(">H", 17)
        + bytes([8])
        + struct.pack(">HH", height, width)
        + bytes([3, 1, 0x22, 0, 2, 0x11, 1, 3, 0x11, 1])
    )
    comment = b"\xff\xfe" + struct.pack(">H", padding + 2) + b"S" * padding
    return b"\xff\xd8" + app0 + sof0 + comment + b"\xff\xd9"


def _box(box_type: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body) + 8) + box_type + body


def make_iso_bmff(total_bytes: int = 2 * 1024 * 1024, brand: bytes = b"isom") -> bytes:
    """An MP4/MOV container: ftyp, moov and a padded mdat that tile the file."""
    ftyp = _box(b"ftyp", brand + struct.pack(">I", 512) + brand + b"mp42")
    moov = _box(b"moov", _box(b"mvhd", b"\x00" * 100))
    overhead = len(ftyp) + len(moov) + 8
    payload = max(1, total_bytes - overhead)
    return ftyp + moov + _box(b"mdat", b"\x00" * payload)
