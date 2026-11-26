"""Zero-dependency pure-Python QR Code (Byte mode, ECC-L, Version 1..10) PNG generator."""
from __future__ import annotations

import struct
import zlib

# Capacity in bytes for Error Correction Level L, Versions 1..10
_CAP_L = {1: 17, 2: 32, 3: 53, 4: 78, 5: 106, 6: 134, 7: 154, 8: 192, 9: 230, 10: 271}
# (total_codewords, ec_per_block, g1_blocks, g1_data_cw, g2_blocks, g2_data_cw)
_RS_L = {
    1: (26, 7, 1, 19, 0, 0),
    2: (44, 10, 1, 34, 0, 0),
    3: (70, 15, 1, 55, 0, 0),
    4: (100, 20, 1, 80, 0, 0),
    5: (134, 26, 1, 108, 0, 0),
    6: (172, 18, 2, 68, 0, 0),
    7: (196, 20, 2, 78, 0, 0),
    8: (242, 24, 2, 97, 0, 0),
    9: (292, 30, 2, 116, 0, 0),
    10: (346, 18, 2, 68, 2, 69),
}
_ALIGN_POS = {
    1: [],
    2: [6, 18],
    3: [6, 22],
    4: [6, 26],
    5: [6, 30],
    6: [6, 34],
    7: [6, 22, 38],
    8: [6, 24, 42],
    9: [6, 26, 46],
    10: [6, 28, 50],
}

_GF_EXP = [0] * 512
_GF_LOG = [0] * 256
_x = 1
for _i in range(255):
    _GF_EXP[_i] = _x
    _GF_LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _GF_EXP[_i] = _GF_EXP[_i - 255]


def _gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _GF_EXP[_GF_LOG[a] + _GF_LOG[b]]


def _rs_generator(degree: int) -> list[int]:
    poly = [1]
    for i in range(degree):
        nxt = [0] * (len(poly) + 1)
        factor = _GF_EXP[i]
        for j, coef in enumerate(poly):
            nxt[j] ^= coef
            nxt[j + 1] ^= _gf_mul(coef, factor)
        poly = nxt
    return poly


def _rs_remainder(data: list[int], degree: int) -> list[int]:
    gen = _rs_generator(degree)
    res = list(data) + [0] * degree
    for i in range(len(data)):
        coef = res[i]
        if coef != 0:
            for j in range(1, len(gen)):
                res[i + j] ^= _gf_mul(gen[j], coef)
    return res[len(data):]


def _encode_data(payload: bytes) -> tuple[int, list[int]]:
    length = len(payload)
    version = next((v for v in range(1, 11) if length <= _CAP_L[v]), None)
    if version is None:
        raise ValueError("QR payload exceeds 271 bytes")

    total_cw, ec_cw, b1, d1, b2, d2 = _RS_L[version]
    total_data_cw = b1 * d1 + b2 * d2
    bits: list[int] = []

    def push(val: int, width: int) -> None:
        for shift in range(width - 1, -1, -1):
            bits.append((val >> shift) & 1)

    push(0b0100, 4)  # Byte mode
    push(length, 8 if version <= 9 else 16)
    for byte in payload:
        push(byte, 8)

    cap_bits = total_data_cw * 8
    push(0, min(4, cap_bits - len(bits)))
    while len(bits) % 8 != 0:
        bits.append(0)
    data_cw = [
        int("".join(str(b) for b in bits[i : i + 8]), 2)
        for i in range(0, len(bits), 8)
    ]
    pad_bytes = (0xEC, 0x11)
    idx = 0
    while len(data_cw) < total_data_cw:
        data_cw.append(pad_bytes[idx % 2])
        idx += 1

    blocks_data: list[list[int]] = []
    blocks_ec: list[list[int]] = []
    pos = 0
    for count, size in ((b1, d1), (b2, d2)):
        for _ in range(count):
            blk = data_cw[pos : pos + size]
            pos += size
            blocks_data.append(blk)
            blocks_ec.append(_rs_remainder(blk, ec_cw))

    codewords: list[int] = []
    max_d = max(len(b) for b in blocks_data)
    for col in range(max_d):
        for blk in blocks_data:
            if col < len(blk):
                codewords.append(blk[col])
    for col in range(ec_cw):
        for ec in blocks_ec:
            codewords.append(ec[col])
    return version, codewords


def _make_matrix(version: int, codewords: list[int]) -> list[list[int]]:
    n = 17 + 4 * version
    grid = [[-1] * n for _ in range(n)]
    reserved = [[False] * n for _ in range(n)]

    def set_mod(r: int, c: int, val: int, lock: bool = True) -> None:
        grid[r][c] = val
        if lock:
            reserved[r][c] = True

    def draw_finder(r0: int, c0: int) -> None:
        for dr in range(-1, 8):
            for dc in range(-1, 8):
                r, c = r0 + dr, c0 + dc
                if 0 <= r < n and 0 <= c < n:
                    on = (
                        0 <= dr <= 6
                        and 0 <= dc <= 6
                        and (
                            dr in (0, 6)
                            or dc in (0, 6)
                            or (2 <= dr <= 4 and 2 <= dc <= 4)
                        )
                    )
                    set_mod(r, c, 1 if on else 0)

    draw_finder(0, 0)
    draw_finder(0, n - 7)
    draw_finder(n - 7, 0)

    # Alignment patterns
    centers = _ALIGN_POS[version]
    for r0 in centers:
        for c0 in centers:
            if reserved[r0][c0]:
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    on = max(abs(dr), abs(dc)) in (0, 2)
                    set_mod(r0 + dr, c0 + dc, 1 if on else 0)

    # Timing patterns
    for i in range(8, n - 8):
        if not reserved[6][i]:
            set_mod(6, i, 1 - (i % 2))
        if not reserved[i][6]:
            set_mod(i, 6, 1 - (i % 2))

    # Dark module + format/version placeholders
    set_mod(4 * version + 9, 8, 1)
    for i in range(9):
        if i != 6:
            reserved[8][i] = True
            reserved[i][8] = True
    for i in range(8):
        reserved[8][n - 1 - i] = True
        reserved[n - 1 - i][8] = True
    if version >= 7:
        for i in range(6):
            for j in range(3):
                reserved[n - 11 + j][i] = True
                reserved[i][n - 11 + j] = True

    # Place data bits
    bitstream = []
    for cw in codewords:
        for shift in range(7, -1, -1):
            bitstream.append((cw >> shift) & 1)
    bit_idx = 0
    col = n - 1
    up = True
    while col > 0:
        if col == 6:
            col -= 1
        rows = range(n - 1, -1, -1) if up else range(n)
        for r in rows:
            for dc in (0, -1):
                c = col + dc
                if not reserved[r][c]:
                    bit = bitstream[bit_idx] if bit_idx < len(bitstream) else 0
                    bit_idx += 1
                    # Mask pattern 0: (r + c) % 2 == 0
                    grid[r][c] = bit ^ (1 if (r + c) % 2 == 0 else 0)
        up = not up
        col -= 2

    # Format info for ECC-L (01) + Mask 0 (000) -> 5 bits = 01000
    fmt_bits = _bch_format(0b01000)
    for i in range(6):
        grid[8][i] = fmt_bits[i]
        grid[n - 1 - i][8] = fmt_bits[i]
    grid[8][7] = fmt_bits[6]
    grid[n - 7][8] = fmt_bits[6]
    grid[8][8] = fmt_bits[7]
    grid[7][8] = fmt_bits[8]
    for i in range(6):
        grid[5 - i][8] = fmt_bits[9 + i]
    for i in range(8):
        grid[8][n - 8 + i] = fmt_bits[7 + i]

    if version >= 7:
        ver_bits = _bch_version(version)
        k = 0
        for i in range(6):
            for j in range(3):
                bit = ver_bits[17 - k]
                grid[n - 11 + j][i] = bit
                grid[i][n - 11 + j] = bit
                k += 1

    return grid


def _bch_format(data5: int) -> list[int]:
    d = data5 << 10
    gen = 0b10100110111
    for i in range(14, 9, -1):
        if (d >> i) & 1:
            d ^= gen << (i - 10)
    val = ((data5 << 10) | d) ^ 0b101010000010010
    return [(val >> i) & 1 for i in range(14, -1, -1)]


def _bch_version(version: int) -> list[int]:
    d = version << 12
    gen = 0b1111100100101
    for i in range(17, 11, -1):
        if (d >> i) & 1:
            d ^= gen << (i - 12)
    val = (version << 12) | d
    return [(val >> i) & 1 for i in range(17, -1, -1)]


def encode_qr_matrix(text: str) -> list[list[int]]:
    version, codewords = _encode_data(text.encode("utf-8"))
    return _make_matrix(version, codewords)


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def render_qr_png(text: str, scale: int = 8, border: int = 4) -> bytes:
    matrix = encode_qr_matrix(text)
    size = len(matrix)
    dim = (size + 2 * border) * scale
    raw_rows = bytearray()
    white_row = bytes([0] + [255] * dim)
    for _ in range(border * scale):
        raw_rows.extend(white_row)
    for row in matrix:
        line = bytearray([0] + [255] * (border * scale))
        for cell in row:
            px = 0 if cell else 255
            line.extend([px] * scale)
        line.extend([255] * (border * scale))
        for _ in range(scale):
            raw_rows.extend(line)
    for _ in range(border * scale):
        raw_rows.extend(white_row)

    ihdr = struct.pack(">IIBBBBB", dim, dim, 8, 0, 0, 0, 0)  # 8-bit grayscale
    idat = zlib.compress(bytes(raw_rows), level=6)
    return b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", idat) + _png_chunk(b"IEND", b"")
