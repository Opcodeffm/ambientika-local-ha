"""Protocol and asyncio test helpers."""

from __future__ import annotations

from collections import deque


def status_frame(serial: str = "aabbccddeeff", *, length: int = 21) -> bytes:
    """Build a valid 19- or 21-byte status frame."""
    frame = bytes.fromhex("0100" + serial) + bytes(
        [
            1,  # AUTO
            2,  # HIGH
            1,  # NORMAL humidity target
            20,  # temperature
            55,  # humidity
            2,  # FAIR air quality
            1,  # humidity alarm
            0,  # filter good
            0,  # no night alarm
            0,  # master
            1,  # last mode AUTO
        ]
    )
    if length == 19:
        return frame
    if length == 21:
        return frame + bytes([3, 195])
    raise ValueError("unsupported test status length")


def firmware_frame(serial: str = "aabbccddeeff") -> bytes:
    """Build a valid firmware frame."""
    return bytes.fromhex("0300" + serial) + bytes([0, 0, 28, 0, 1, 22, 2, 1, 0, 0])


class ChunkReader:
    """Minimal StreamReader replacement yielding predetermined chunks."""

    def __init__(self, *chunks: bytes) -> None:
        self._chunks = deque(chunks)

    async def read(self, _size: int) -> bytes:
        return self._chunks.popleft() if self._chunks else b""


class WaitingReader:
    """Reader that never produces a first frame."""

    async def read(self, _size: int) -> bytes:
        import asyncio

        await asyncio.Event().wait()
        return b""


class FakeWriter:
    """Minimal StreamWriter replacement for connection tests."""

    def __init__(self, ip: str = "192.0.2.10") -> None:
        self.ip = ip
        self.closed = False
        self.writes: list[bytes] = []

    def get_extra_info(self, name: str):
        if name == "peername":
            return (self.ip, 12345)
        return None

    def write(self, data: bytes) -> None:
        self.writes.append(bytes(data))

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None

    def is_closing(self) -> bool:
        return self.closed
