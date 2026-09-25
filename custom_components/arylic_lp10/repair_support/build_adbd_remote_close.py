"""Build the LP10 adbd remote_close fix for the captured firmware binary.

This is intentionally tied to one SHA-256 and ELF layout. It never connects to
or changes a player. See README.md before considering device use.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import struct


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "evidence/adbd-analysis/adbd-master-bedroom"
OUTPUT = ROOT / "patches/adbd-remote-close-candidate"
EXPECTED_SHA256 = "764d9681ddd37d2f3318207067268b22b47b745703cb4daf17f9500ce0e2475b"

BASE = 0x10000
REMOTE_CLOSE = 0x17E2C
CODE_CAVE = 0x27578
CLOSE_PLT = 0x11D38
LOAD_PHDR = 3


def word(data: bytearray, address: int, value: int) -> None:
    struct.pack_into("<I", data, address - BASE, value)


def branch(address: int, target: int) -> int:
    displacement = target - (address + 8)
    if displacement % 4 or not -(1 << 25) <= displacement < (1 << 25):
        raise ValueError("ARM branch target out of range or unaligned")
    return 0xEA000000 | ((displacement // 4) & 0xFFFFFF)


def patch_bytes(original: bytes) -> bytes:
    """Return a patched copy of the one supported vendor executable."""
    data = bytearray(original)
    digest = hashlib.sha256(data).hexdigest()
    if digest != EXPECTED_SHA256:
        raise ValueError(f"Unexpected source SHA-256: {digest}")
    if data[:4] != b"\x7fELF" or data[4:7] != b"\x01\x01\x01":
        raise ValueError("Expected little-endian ELF32")
    if struct.unpack_from("<H", data, 18)[0] != 40:
        raise ValueError("Expected ARM ELF")
    if struct.unpack_from("<II", data, REMOTE_CLOSE - BASE) != (
        0xE5900018,  # ldr r0, [r0, #0x18] (wrong: t->fd)
        branch(REMOTE_CLOSE + 4, CLOSE_PLT),
    ):
        raise ValueError("remote_close differs from captured binary")

    phoff = struct.unpack_from("<I", data, 28)[0]
    phentsize = struct.unpack_from("<H", data, 42)[0]
    ph = phoff + LOAD_PHDR * phentsize
    if struct.unpack_from("<IIIIIIII", data, ph) != (
        1, 0, BASE, BASE, 0x17578, 0x17578, 5, 0x10000
    ):
        raise ValueError("Executable PT_LOAD differs from captured binary")

    cave_offset = CODE_CAVE - BASE
    code = [
        0xE5903054,  # ldr   r3, [r0, #0x54]  ; fd = t->sfd
        0xE3730001,  # cmn   r3, #1           ; fd == -1?
        0x012FFF1E,  # bxeq  lr               ; already closed
        0xE3E02000,  # mvn   r2, #0           ; -1
        0xE5802054,  # str   r2, [r0, #0x54]  ; t->sfd = -1
        0xE1A00003,  # mov   r0, r3           ; fd argument
        branch(CODE_CAVE + 24, CLOSE_PLT),  # tail-call close@plt
    ]
    cave_end = cave_offset + 4 * len(code)
    if cave_end > 0x17EE8 or any(data[cave_offset:cave_end]):
        raise ValueError("No intact code cave before next PT_LOAD")

    word(data, REMOTE_CLOSE, branch(REMOTE_CLOSE, CODE_CAVE))
    for index, instruction in enumerate(code):
        struct.pack_into("<I", data, cave_offset + index * 4, instruction)
    # Extend the existing RX mapping over the new instructions. The next
    # PT_LOAD starts at offset 0x17ee8, well beyond the new end 0x17594.
    struct.pack_into("<II", data, ph + 16, cave_end, cave_end)

    return bytes(data)


def main() -> None:
    original = SOURCE.read_bytes()
    data = patch_bytes(original)
    if OUTPUT.exists() and OUTPUT.read_bytes() != data:
        raise ValueError(f"Refusing to overwrite different candidate: {OUTPUT}")
    OUTPUT.write_bytes(data)
    print(f"Source SHA-256:    {hashlib.sha256(original).hexdigest()}")
    print(f"Candidate SHA-256: {hashlib.sha256(data).hexdigest()}")
    print(f"Candidate:        {OUTPUT}")


if __name__ == "__main__":
    main()
