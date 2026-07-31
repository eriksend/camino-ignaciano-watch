"""Shared fixtures.

The autouse no_network fixture is the important one: these tests must never touch
the network. Without it a test could silently start depending on a live service
and then "pass" for the wrong reason — or fail months later when that service
changes, which is exactly the confusion this suite exists to avoid.
"""
from __future__ import annotations

import datetime as dt
import os
import struct
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("test attempted a network call")

    import requests
    monkeypatch.setattr(requests, "get", boom)
    monkeypatch.setattr(requests.Session, "get", boom)


@pytest.fixture
def now():
    return dt.datetime(2026, 8, 1, 12, 0, tzinfo=dt.timezone.utc)


def make_tiff(values, width, height, endian="<", bits=32, fmt=3, strips=1):
    """Build a minimal uncompressed single-band GeoTIFF in memory.

    Mirrors what MapServer returns for the EFFIS FWI layer. `strips` > 1
    exercises the multi-strip reassembly path, which is the case a MapServer
    upgrade would plausibly change.
    """
    e = endian
    payload = struct.pack(e + f"{len(values)}f", *values)
    rows_per_strip = max(1, height // strips)
    chunks, offs, counts = [], [], []
    row_bytes = width * 4
    pos = 0
    while pos < len(payload):
        chunk = payload[pos:pos + row_bytes * rows_per_strip]
        chunks.append(chunk)
        counts.append(len(chunk))
        pos += len(chunk)
    tags = [(256, 3, 1, width), (257, 3, 1, height), (258, 3, 1, bits),
            (259, 3, 1, 1), (277, 3, 1, 1), (339, 3, 1, fmt),
            (278, 3, 1, rows_per_strip)]
    n_tags = len(tags) + 2                      # + StripOffsets, StripByteCounts
    header = 8
    ifd_size = 2 + n_tags * 12 + 4
    arrays_at = header + ifd_size
    off_arr_size = 4 * len(chunks) if len(chunks) > 1 else 0
    cnt_arr_size = 4 * len(counts) if len(counts) > 1 else 0
    data_at = arrays_at + off_arr_size + cnt_arr_size
    cur = data_at
    for c in chunks:
        offs.append(cur)
        cur += len(c)

    out = bytearray()
    out += (b"II" if e == "<" else b"MM") + struct.pack(e + "HI", 42, header)
    entries = bytearray()
    entries += struct.pack(e + "H", n_tags)
    for tag, typ, cnt, val in tags:
        entries += struct.pack(e + "HHI", tag, typ, cnt)
        entries += struct.pack(e + "HH", val, 0) if typ == 3 else struct.pack(e + "I", val)
    for tag, arr in ((273, offs), (279, counts)):
        entries += struct.pack(e + "HHI", tag, 4, len(arr))
        if len(arr) == 1:
            entries += struct.pack(e + "I", arr[0])
        else:
            entries += struct.pack(e + "I",
                                   arrays_at if tag == 273 else arrays_at + off_arr_size)
    entries += struct.pack(e + "I", 0)
    out += entries
    if len(offs) > 1:
        out += struct.pack(e + f"{len(offs)}I", *offs)
        out += struct.pack(e + f"{len(counts)}I", *counts)
    for c in chunks:
        out += c
    return bytes(out)
