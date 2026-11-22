"""Async client for the HP2550 console's port-45000 API and backlight mailbox.

No Home Assistant imports, so it can be run on its own against a console.

The backlight patch turns custom-server fields into a mailbox. The client
writes a command into the Station ID, "<VERB> <value> #<seq>", and the console
acts on a new sequence number within a few seconds. Two firmware generations
are supported, told apart by where the status report turns up:

- v2: the status is in the password field of the custom-server record,
  "BP <0|1> BL <0-100|A> BS <0|1> [DM <0|1>] #<seq>". Commands are BL n,
  BL A, BP 0/1, BS 0/1 and DM 0/1. See PROTOCOL_v2.md in the firmware notes.
- v1 (the published patch): the status is in the second upload path,
  "BL <on> <level> #<seq>". Commands are BL 0 (off), BL 1 (on) and BL 2-100.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import struct
from dataclasses import dataclass
from typing import Any

_LOGGER = logging.getLogger(__name__)

PORT = 45000

CMD_READ_MAC = 0x26
CMD_READ_CUSTOMIZED = 0x2A
CMD_WRITE_CUSTOMIZED = 0x2B
CMD_READ_USR_PATH = 0x51

TIMEOUT = 5.0  # seconds, for the connect and for each read
TAIL_TIMEOUT = 0.5  # the console sometimes omits the trailing checksum byte

# A command has this long, end to end, to be written and echoed. The console
# echoes within 6-12 s when nothing goes wrong, which leaves room for one lost
# exchange (up to TIMEOUT) and a rewrite.
COMMAND_TIMEOUT = 20.0
CONFIRM_POLL = 2.0  # seconds between status reads while waiting
MAX_WRITES = 3  # writes of one command before giving up on it

STATUS_PATH_INDEX = 1  # v1: the status report lives in the second upload path

GEN_V1 = 1
GEN_V2 = 2

# Level 0-100 on the console; HA brightness is 1-255.
LEVEL_MAX = 100

_V1_STATUS_RE = re.compile(r"BL\s+(\d+)\s+(\d+)\s+#(\d+)")
_SEQ_RE = re.compile(r"#(\d+)")
_V2_COMMAND_RE = re.compile(r"^B[A-Z] \S+ #\d+$")


class Hp2550Error(Exception):
    """A connection or protocol error talking to the console."""


class Hp2550Rejected(Hp2550Error):
    """The console acknowledged our seq with !<seq>: it refused the command.

    Nothing changed on the console. `status` is the status that carried the
    rejection, so the caller can still adopt the (unchanged) state.
    """

    def __init__(self, command: str, status: Hp2550Status) -> None:
        super().__init__(f"the console rejected '{command}'")
        self.status = status


@dataclass(frozen=True, slots=True)
class Hp2550Status:
    """The console's backlight status report.

    On v2 this is the console's actual state. On v1 it is a record of the last
    command acted on, which the console's own schedule and auto-brightness can
    make stale.
    """

    generation: int  # GEN_V1 or GEN_V2
    on: bool
    level: int | None  # 0-100; None while auto-brightness is on (v2)
    seq: int
    rejected: bool = False  # v2: the last token was !<seq>, the command was refused
    auto: bool = False  # v2 only
    schedule: bool | None = None  # v2 only
    display_mode: bool | None = None  # v2 only, and only if the firmware reports DM


def describe(err: BaseException) -> str:
    """Name an exception's type as well as its text.

    A read timeout's str() is empty, so the type is the only thing that tells
    it apart from a reset or a short read in the log.
    """
    text = str(err)
    return f"{type(err).__name__}: {text}" if text else type(err).__name__


def _is_uint(text: str) -> bool:
    """True for a non-empty string of ASCII digits."""
    return text.isascii() and text.isdigit()


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


def parse_status_v2(text: str) -> Hp2550Status | None:
    """Parse a v2 status from the password field, or None if it isn't one.

    Pairs may come in any order and unknown verbs are ignored, so the console
    can add pairs later. A status needs BP, BL and the trailing #<seq>.
    """
    tokens = (text or "").split()
    pairs: dict[str, str] = {}
    seq: int | None = None
    rejected = False
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok[0] in "#!":
            if _is_uint(tok[1:]):
                seq = int(tok[1:])
                rejected = tok[0] == "!"
            i += 1
        elif i + 1 < len(tokens) and tokens[i + 1][0] not in "#!":
            pairs.setdefault(tok, tokens[i + 1])
            i += 2
        else:
            i += 1
    if seq is None or "BP" not in pairs or "BL" not in pairs:
        return None
    if pairs["BP"] not in ("0", "1"):
        return None
    bl = pairs["BL"]
    if bl == "A":
        level: int | None = None
        auto = True
    elif _is_uint(bl) and int(bl) <= LEVEL_MAX:
        level = int(bl)
        auto = False
    else:
        return None

    def flag(verb: str) -> bool | None:
        value = pairs.get(verb)
        return {"0": False, "1": True}.get(value) if value is not None else None

    return Hp2550Status(
        generation=GEN_V2,
        on=pairs["BP"] == "1",
        level=level,
        seq=seq,
        rejected=rejected,
        auto=auto,
        schedule=flag("BS"),
        display_mode=flag("DM"),
    )


def parse_status_v1(path: str) -> Hp2550Status | None:
    """Parse a v1 'BL <on> <level> #<seq>' status from path2, or None."""
    m = _V1_STATUS_RE.search(path or "")
    if not m:
        return None
    return Hp2550Status(
        generation=GEN_V1,
        on=bool(int(m.group(1))),
        level=int(m.group(2)),
        seq=int(m.group(3)),
    )


def parse_status(password: str, path2: str) -> Hp2550Status | None:
    """Detect the generation from what is present and parse the status.

    A v2 status in the password field wins; otherwise a v1 status in path2.
    """
    return parse_status_v2(password) or parse_status_v1(path2)


def accept_status(status: Hp2550Status, pending_seq: int | None) -> bool:
    """Seq-gated reconciliation: should a polled status replace our state?

    While a command is in flight, a status that doesn't carry its seq still
    describes the state before the command, so adopting it would revert what
    we just asked for.
    """
    return pending_seq is None or status.seq == pending_seq


def level_to_brightness(level: int, generation: int) -> int:
    """Console level 0-100 -> HA brightness 1-255, never 0 for a lit panel.

    On v2 level 0 is the dimmest lit level, so it maps to 1. v1 has no level 0
    in practice, but the same floor keeps a lit panel from reading as off.
    """
    if generation == GEN_V2:
        return 1 + round(max(0, min(LEVEL_MAX, level)) * 254 / LEVEL_MAX)
    return max(1, round(level * 255 / LEVEL_MAX))


def brightness_to_level(brightness: int, generation: int) -> int:
    """HA brightness 1-255 -> console level.

    v2: 1 -> 0 and 255 -> 100, so level_to_brightness round-trips.
    v1: clamped to 2-100, since BL 0 and BL 1 mean off and on there.
    """
    brightness = max(1, min(255, brightness))
    if generation == GEN_V2:
        return round((brightness - 1) * LEVEL_MAX / 254)
    return max(2, min(LEVEL_MAX, round(brightness * LEVEL_MAX / 255)))


def next_seq(current_id: str, avoid: int | None = None) -> int:
    """Pick the next command sequence number: 1-255, 8-bit, never 0.

    The console ignores a command whose seq equals the one in its status, so
    when the status seq is known pass it as `avoid` to step past it. (This can
    happen when the command slot was reloaded to an older value than the
    status, e.g. after leaving the Customized screen.)
    """
    m = _SEQ_RE.search(current_id or "")
    cur = int(m.group(1)) if m else 0
    seq = (cur % 255) + 1
    if avoid is not None and seq == avoid:
        seq = (seq % 255) + 1
    return seq


def command_string(verb: str, value: int | str, seq: int, generation: int = GEN_V2) -> str:
    """Build a '<VERB> <value> #<seq>' command string, validating the pair.

    v1 only has BL, taking 0 (off), 1 (on) or 2-100 (level).
    """
    value = str(value)
    if not 1 <= seq <= 255:
        raise ValueError(f"seq must be 1-255, not {seq}")
    if generation == GEN_V1:
        if verb != "BL" or not _is_uint(value) or int(value) > LEVEL_MAX:
            raise ValueError(f"not a v1 command: {verb} {value}")
    elif verb == "BL":
        if value != "A" and not (_is_uint(value) and int(value) <= LEVEL_MAX):
            raise ValueError(f"BL takes 0-100 or A, not {value}")
    elif verb in ("BP", "BS", "DM"):
        if value not in ("0", "1"):
            raise ValueError(f"{verb} takes 0 or 1, not {value}")
    else:
        raise ValueError(f"unknown verb {verb}")
    return f"{verb} {value} #{seq}"


def _summary(failures: list[str]) -> str:
    """A suffix listing failed exchanges, or nothing if there were none."""
    if not failures:
        return ""
    return f"; {len(failures)} failed exchange(s): " + "; ".join(failures)


def redactable_id(text: str) -> bool:
    """True if a Station ID doesn't look like a mailbox command (may be a credential)."""
    return not _V2_COMMAND_RE.match(text or "")


class Hp2550Client:
    """Talks the port-45000 framed API over a direct TCP connection.

    The console's API is reachable only on the ESP module's own TCP port, so
    this connects directly rather than through a transport-abstraction layer.
    A lock serializes transactions: the port handles one exchange at a time.
    """

    def __init__(self, host: str) -> None:
        self._host = host
        self._lock = asyncio.Lock()
        self._cmd_lock = asyncio.Lock()
        self.pending_seq: int | None = None

    async def _transact(self, cmd: int, data: bytes = b"") -> bytes:
        pkt = frame(cmd, data)
        async with self._lock:
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self._host, PORT), TIMEOUT)
            except (TimeoutError, OSError) as err:
                raise Hp2550Error(
                    f"connect to {self._host}:{PORT} failed: {describe(err)}") from err
            try:
                writer.write(pkt)
                await writer.drain()
                buf = b""
                # A full frame is resp[3] + 2 bytes including the checksum.
                # Read until all but the checksum is here; then, only if the
                # checksum didn't come with it, wait briefly for it (the
                # console sometimes leaves it off).
                while len(buf) < 4 or len(buf) < buf[3] + 1:
                    chunk = await asyncio.wait_for(reader.read(256), TIMEOUT)
                    if not chunk:
                        break
                    buf += chunk
                if len(buf) >= 4 and len(buf) == buf[3] + 1:
                    with contextlib.suppress(TimeoutError):
                        buf += await asyncio.wait_for(reader.read(256), TAIL_TIMEOUT)
            except (TimeoutError, OSError) as err:
                raise Hp2550Error(
                    f"0x{cmd:02X} to {self._host} failed: {describe(err)}") from err
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
        """Read the backlight status: v2 from the password, else v1 from path2.

        Returns None if neither is present (the console hasn't published one).
        """
        return (await self._read_state())[1]

    async def _read_state(self) -> tuple[dict[str, Any], Hp2550Status | None]:
        """Read the custom record and the status it (or path2, on v1) carries."""
        record = await self.read_custom()
        status = parse_status_v2(record["key"])
        if status is None:
            status = parse_status_v1((await self.read_paths())[STATUS_PATH_INDEX])
        return record, status

    async def _write_command(self, record: dict[str, Any], command: str) -> None:
        """Write a command string into the Station ID of a freshly read record."""
        await self._transact(CMD_WRITE_CUSTOMIZED, build_custom(dict(record, id=command)))

    async def send_command(
        self,
        verb: str,
        value: int | str,
        generation: int,
        *,
        timeout: float = COMMAND_TIMEOUT,
        poll: float = CONFIRM_POLL,
        max_writes: int = MAX_WRITES,
    ) -> Hp2550Status:
        """Send one command and wait for the console to echo its seq.

        Every `poll` seconds this reads the record, which carries both the
        command slot and (on v2) the status, and acts on what it finds:

        - the status echoes our seq: done (or Hp2550Rejected for !<seq>);
        - the slot doesn't hold our command: (re)write it. That covers a write
          that was lost on the way, and a slot reverted by the console (leaving
          the Customized screen, or a status push carrying an older slot).
          A rewrite uses the same seq, which the console ignores if it has
          already acted on it;
        - the slot holds our command: the console hasn't picked it up yet, so
          keep waiting.

        A failed exchange (a dropped reply, a refused connection) is logged
        and the loop carries on, so one lost exchange doesn't lose the command.
        Commands are serialized: the next isn't written until this one is
        echoed, because a second write inside a poll cycle can be overwritten.

        Raises Hp2550Error if the command isn't echoed within `timeout` seconds
        in total (the whole call, including the first read), or after
        `max_writes` writes. Returns the status that carried the echo. The echo
        confirms the console saw the command; check the state fields for its
        effect.
        """
        async with self._cmd_lock:
            command: str | None = None
            writes = 0
            failures: list[str] = []  # one entry per failed exchange
            failed_write: int | None = None  # index in failures, until a read shows its fate

            def note(step: str, err: Hp2550Error) -> None:
                failures.append(f"{step}: {err}")
                _LOGGER.warning("%s: %s failed: %s", command or f"{verb} {value}", step, err)

            try:
                async with asyncio.timeout(timeout):
                    state = None
                    while state is None:
                        try:
                            state = await self._read_state()
                        except Hp2550Error as err:
                            note("first record read", err)
                            await asyncio.sleep(poll)
                    record, known = state
                    avoid = known.seq if known and known.generation == GEN_V2 else None
                    seq = next_seq(record["id"], avoid)
                    command = command_string(verb, value, seq, generation)
                    self.pending_seq = seq
                    while True:
                        if state is not None:
                            record, status = state
                            if failed_write is not None:
                                # Did the failed write reach the console anyway?
                                fate = "it landed" if record["id"] == command else "it was lost"
                                failures[failed_write] += f", {fate}"
                                _LOGGER.warning("%s: after the failed write %d, %s",
                                                command, writes, fate)
                                failed_write = None
                            if status is not None and status.seq == seq:
                                if status.rejected:
                                    # Refused, nothing changed; resending can't help.
                                    raise Hp2550Rejected(command, status)
                                if failures or writes > 1:
                                    _LOGGER.warning("%s: confirmed after %d write(s)%s",
                                                    command, writes, _summary(failures))
                                return status
                            if record["id"] != command:
                                if writes >= max_writes:
                                    raise Hp2550Error(
                                        f"no confirmation of '{command}' after {writes} writes"
                                        + _summary(failures))
                                writes += 1
                                if writes > 1:
                                    # Don't log a slot that may be a credential.
                                    held = ("(not a command)" if redactable_id(record["id"])
                                            else f"'{record['id']}'")
                                    _LOGGER.warning("%s: command slot holds %s, rewriting "
                                                    "(write %d of %d)", command, held,
                                                    writes, max_writes)
                                try:
                                    await self._write_command(record, command)
                                except Hp2550Error as err:
                                    note(f"write {writes} of {max_writes}", err)
                                    failed_write = len(failures) - 1
                        await asyncio.sleep(poll)
                        try:
                            state = await self._read_state()
                        except Hp2550Error as err:
                            note("status read", err)
                            state = None
            except TimeoutError as err:
                if failed_write is not None:
                    failures[failed_write] += ", fate unknown"
                what = f"'{command}'" if command else f"{verb} {value} (record never read)"
                raise Hp2550Error(
                    f"no confirmation of {what} within {timeout:g} s, {writes} write(s)"
                    + _summary(failures)) from err
            finally:
                self.pending_seq = None

    async def read_raw(self) -> dict[str, Any]:
        """Return the raw custom record and upload paths, for diagnostics."""
        record = await self.read_custom()
        paths = await self.read_paths()
        return {"custom_record": record, "paths": list(paths)}
