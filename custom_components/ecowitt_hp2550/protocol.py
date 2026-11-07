"""Async client for the HP2550 console's port-45000 API and backlight mailbox.

No Home Assistant imports, so it can be run on its own against a console.

The backlight patch turns two custom-server fields into a mailbox. The client
writes a command into the Station ID, "BL <v> #<seq>", where v is 0 (off),
1 (on at the saved level) or 2-100 (on at that level). The console acts on a new
sequence number within a few seconds and reports the result in the second
upload path, "BL <on> <level> #<seq>".
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
import re
import struct
from typing import Any

PORT = 45000

CMD_READ_MAC = 0x26
CMD_READ_CUSTOMIZED = 0x2A
CMD_WRITE_CUSTOMIZED = 0x2B
CMD_READ_USR_PATH = 0x51

TIMEOUT = 5.0  # seconds, for the connect and for each read
TAIL_TIMEOUT = 0.5  # the console sometimes omits the trailing checksum byte

STATUS_PATH_INDEX = 1  # the status report lives in the second upload path

_STATUS_RE = re.compile(r"BL\s+(\d+)\s+(\d+)\s+#(\d+)")
_SEQ_RE = re.compile(r"#(\d+)")


class Hp2550Error(Exception):
    """A connection or protocol error talking to the console."""


@dataclass(frozen=True, slots=True)
class Hp2550Status:
    """The console's report of the last backlight command it acted on."""

    on: bool
    level: int  # 0-100
    seq: int


def frame(cmd: int, data: bytes = b"") -> bytes:
    """Build a request: FF FF, command, size, data, 8-bit sum of command..data."""
    size = 3 + len(data)
    if size > 0xFF:
        raise Hp2550Error("payload too long for the 1-byte size field")
    body = bytes([cmd, size]) + data
    return b"\xff\xff" + body + bytes([sum(body) & 0xFF])


def payload(cmd: int, resp: bytes) -> bytes:
    """Return the data bytes of a response after checking its framing."""
    if len(resp) < 4 or resp[:2] != b"\xff\xff" or resp[2] != cmd:
        raise Hp2550Error(f"bad response to 0x{cmd:02X}: {resp.hex(' ')}")
    end = resp[3] + 1  # data is resp[4:end]; a checksum, if sent, is resp[end]
    if len(resp) < end:
        raise Hp2550Error(f"short response to 0x{cmd:02X}: {resp.hex(' ')}")
    return resp[4:end]


def _pstr(b: bytes, i: int) -> tuple[str, int]:
    n = b[i]
    if i + 1 + n > len(b):
        raise Hp2550Error("string runs past the end of the data")
    return b[i + 1 : i + 1 + n].decode("latin1"), i + 1 + n


def _lstr(s: str) -> bytes:
    b = s.encode("latin1")
    if len(b) > 0xFF:
        raise Hp2550Error("string too long for its 1-byte length")
    return bytes([len(b)]) + b


def parse_custom(d: bytes) -> dict[str, Any]:
    """Decode the custom-server record (data of a 0x2A response)."""
    try:
        sid, i = _pstr(d, 0)
        key, i = _pstr(d, i)
        host, i = _pstr(d, i)
        port, interval = struct.unpack_from(">HH", d, i)
        typ, active = d[i + 4], d[i + 5]
    except (IndexError, struct.error) as err:
        raise Hp2550Error(f"malformed custom record: {d.hex(' ')}") from err
    return {"id": sid, "key": key, "host": host, "port": port,
            "interval": interval, "type": typ, "active": active}


def build_custom(c: dict[str, Any]) -> bytes:
    """Encode a custom-server record for a 0x2B write."""
    return (_lstr(c["id"]) + _lstr(c["key"]) + _lstr(c["host"])
            + struct.pack(">HHBB", c["port"], c["interval"], c["type"], c["active"]))


def parse_paths(d: bytes) -> tuple[str, str]:
    """Decode the two upload paths (data of a 0x51 response)."""
    p1, i = _pstr(d, 0)
    p2, _ = _pstr(d, i)
    return p1, p2


def parse_status(path: str) -> Hp2550Status | None:
    """Parse a 'BL <on> <level> #<seq>' status report, or None if absent."""
    m = _STATUS_RE.search(path or "")
    if not m:
        return None
    return Hp2550Status(on=bool(int(m.group(1))), level=int(m.group(2)),
                        seq=int(m.group(3)))


def next_seq(current_id: str) -> int:
    """Pick the next command sequence number: 1-255, 8-bit, never 0."""
    m = _SEQ_RE.search(current_id or "")
    cur = int(m.group(1)) if m else 0
    return (cur % 255) + 1


def command_string(value: int, seq: int) -> str:
    """Build a 'BL <v> #<seq>' command string."""
    return f"BL {value} #{seq}"


class Hp2550Client:
    """Talks the port-45000 framed API over a direct TCP connection.

    The console's API is reachable only on the ESP module's own TCP port, so
    this connects directly rather than through a transport-abstraction layer.
    A lock serializes transactions: the port handles one exchange at a time.
    """

    def __init__(self, host: str) -> None:
        self._host = host
        self._lock = asyncio.Lock()

    async def _transact(self, cmd: int, data: bytes = b"") -> bytes:
        pkt = frame(cmd, data)
        async with self._lock:
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self._host, PORT), TIMEOUT)
            except (OSError, asyncio.TimeoutError) as err:
                raise Hp2550Error(f"connect to {self._host}:{PORT} failed: {err}") from err
            try:
                writer.write(pkt)
                await writer.drain()
                buf = b""
                # A full frame is resp[3] + 2 bytes. Keep reading until we have
                # that much; then wait briefly for the optional checksum byte.
                while len(buf) < 4 or len(buf) < buf[3] + 1:
                    chunk = await asyncio.wait_for(reader.read(256), TIMEOUT)
                    if not chunk:
                        break
                    buf += chunk
                with contextlib.suppress(asyncio.TimeoutError):
                    while True:
                        chunk = await asyncio.wait_for(reader.read(256), TAIL_TIMEOUT)
                        if not chunk:
                            break
                        buf += chunk
            except (OSError, asyncio.TimeoutError) as err:
                raise Hp2550Error(f"read from {self._host} failed: {err}") from err
            finally:
                writer.close()
                with contextlib.suppress(OSError, asyncio.TimeoutError):
                    await asyncio.wait_for(writer.wait_closed(), TIMEOUT)
            return payload(cmd, buf)

    async def read_mac(self) -> str:
        """Return the console's MAC as 'aa:bb:cc:dd:ee:ff' (its stable id)."""
        d = await self._transact(CMD_READ_MAC)
        if len(d) < 6:
            raise Hp2550Error(f"short MAC response: {d.hex(' ')}")
        return ":".join(f"{b:02x}" for b in d[:6])

    async def read_custom(self) -> dict[str, Any]:
        return parse_custom(await self._transact(CMD_READ_CUSTOMIZED))

    async def read_paths(self) -> tuple[str, str]:
        return parse_paths(await self._transact(CMD_READ_USR_PATH))

    async def read_status(self) -> Hp2550Status | None:
        """Read the backlight status report from the second upload path."""
        paths = await self.read_paths()
        return parse_status(paths[STATUS_PATH_INDEX])

    async def send_command(self, value: int) -> int:
        """Write a 'BL <value> #<seq>' command, keeping all other record fields.

        Returns the sequence number used, so a caller can tell which status
        report corresponds to this command.
        """
        record = await self.read_custom()
        seq = next_seq(record["id"])
        record["id"] = command_string(value, seq)
        await self._transact(CMD_WRITE_CUSTOMIZED, build_custom(record))
        return seq
