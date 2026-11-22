"""Tests for protocol.py: parsing, command building, mapping, confirm-then-send."""

from __future__ import annotations

import pytest

from custom_components.ecowitt_hp2550 import protocol as p
from custom_components.ecowitt_hp2550.protocol import (
    GEN_V1,
    GEN_V2,
    Hp2550Client,
    Hp2550Error,
    Hp2550Rejected,
)

# --- status parsing ---------------------------------------------------------


def test_v2_status_basic():
    s = p.parse_status_v2("BP 1 BL A BS 0 #7")
    assert s == p.Hp2550Status(
        generation=GEN_V2, on=True, level=None, seq=7, auto=True,
        schedule=False, display_mode=None)


def test_v2_status_numeric_level_and_dm():
    s = p.parse_status_v2("BP 0 BL 100 BS 1 DM 1 #255")
    assert (s.on, s.level, s.auto, s.schedule, s.display_mode, s.seq) == (
        False, 100, False, True, True, 255)


def test_v2_status_level_zero_is_lit():
    s = p.parse_status_v2("BP 1 BL 0 BS 0 #1")
    assert s.on and s.level == 0 and not s.auto


def test_v2_status_any_order_and_unknown_verbs():
    s = p.parse_status_v2("XX 9 BS 1 BL 40 ZZ A BP 1 #3")
    assert (s.on, s.level, s.schedule, s.seq) == (True, 40, True, 3)


def test_v2_status_applied_vs_rejected_marker():
    applied = p.parse_status_v2("BP 1 BL 5 BS 0 #9")
    rejected = p.parse_status_v2("BP 1 BL 5 BS 0 !9")
    assert (applied.seq, applied.rejected) == (9, False)
    assert (rejected.seq, rejected.rejected) == (9, True)
    assert rejected.level == 5 and rejected.on  # state fields are still valid


def test_v2_status_seq_zero():
    assert p.parse_status_v2("BP 1 BL 5 BS 0 #0").seq == 0


@pytest.mark.parametrize("text", [
    "", "   ", "hello", "BL 1 50 #3", "BP 1 BL A BS 0", "BP 2 BL 5 #1",
    "BP 1 BL 101 #1", "BP 1 BL x #1", "BL 5 BS 0 #1", "BP 1 #1",
])
def test_v2_status_rejects(text):
    assert p.parse_status_v2(text) is None


def test_v1_status():
    s = p.parse_status_v1("BL 1 40 #12")
    assert s == p.Hp2550Status(generation=GEN_V1, on=True, level=40, seq=12)
    assert p.parse_status_v1("") is None
    assert p.parse_status_v1("/ECOWITT1?") is None


def test_generation_detected_from_content():
    assert p.parse_status("BP 1 BL 5 BS 0 #2", "").generation == GEN_V2
    assert p.parse_status("", "BL 0 30 #4").generation == GEN_V1
    assert p.parse_status("", "") is None
    # A v2 status wins if both are somehow present.
    assert p.parse_status("BP 1 BL 5 BS 0 #2", "BL 0 30 #4").generation == GEN_V2
    # An unrelated password doesn't hide a v1 status.
    assert p.parse_status("hunter2", "BL 0 30 #4").generation == GEN_V1


def test_accept_status_gates_on_pending_seq():
    s = p.parse_status_v2("BP 1 BL 5 BS 0 #8")
    assert p.accept_status(s, None)
    assert p.accept_status(s, 8)
    assert not p.accept_status(s, 9)


# --- commands and seq -------------------------------------------------------


@pytest.mark.parametrize(("verb", "value", "text"), [
    ("BL", 0, "BL 0 #5"), ("BL", 100, "BL 100 #5"), ("BL", "A", "BL A #5"),
    ("BP", 0, "BP 0 #5"), ("BP", 1, "BP 1 #5"), ("BS", 1, "BS 1 #5"),
    ("DM", 0, "DM 0 #5"),
])
def test_v2_command_strings(verb, value, text):
    assert p.command_string(verb, value, 5, GEN_V2) == text


@pytest.mark.parametrize(("verb", "value"), [
    ("BL", 101), ("BL", "B"), ("BL", -1), ("BP", 2), ("BS", "A"), ("DM", 5), ("ZZ", 1),
])
def test_v2_command_rejects(verb, value):
    with pytest.raises(ValueError):
        p.command_string(verb, value, 5, GEN_V2)


@pytest.mark.parametrize("seq", [0, -1, 256, 1000])
def test_command_rejects_bad_seq(seq):
    with pytest.raises(ValueError):
        p.command_string("BP", 1, seq, GEN_V2)
    with pytest.raises(ValueError):
        p.command_string("BL", 1, seq, GEN_V1)


def test_command_rejects_non_ascii_digits():
    with pytest.raises(ValueError):
        p.command_string("BL", "\u0663", 5, GEN_V2)


def test_v1_command_strings():
    assert p.command_string("BL", 0, 3, GEN_V1) == "BL 0 #3"
    assert p.command_string("BL", 1, 3, GEN_V1) == "BL 1 #3"
    assert p.command_string("BL", 55, 3, GEN_V1) == "BL 55 #3"
    for verb, value in (("BP", 1), ("BL", "A"), ("BL", 101)):
        with pytest.raises(ValueError):
            p.command_string(verb, value, 3, GEN_V1)


def test_next_seq_wraps_and_skips_zero():
    assert p.next_seq("") == 1
    assert p.next_seq("garbage") == 1
    assert p.next_seq("BL 5 #1") == 2
    assert p.next_seq("BL 5 #254") == 255
    assert p.next_seq("BL 5 #255") == 1
    assert p.next_seq("BL 5 #0") == 1


def test_next_seq_avoids_status_seq():
    assert p.next_seq("BL 5 #6", avoid=7) == 8
    assert p.next_seq("BL 5 #254", avoid=255) == 1
    assert p.next_seq("BL 5 #6", avoid=3) == 7


# --- brightness mapping -----------------------------------------------------


def test_v2_mapping_endpoints_and_never_zero():
    assert p.level_to_brightness(0, GEN_V2) == 1
    assert p.level_to_brightness(100, GEN_V2) == 255
    assert p.brightness_to_level(1, GEN_V2) == 0
    assert p.brightness_to_level(255, GEN_V2) == 100
    assert p.brightness_to_level(0, GEN_V2) == 0  # clamped


def test_v2_mapping_round_trips_every_level():
    for level in range(101):
        b = p.level_to_brightness(level, GEN_V2)
        assert 1 <= b <= 255
        assert p.brightness_to_level(b, GEN_V2) == level


def test_v2_mapping_is_monotonic():
    levels = [p.brightness_to_level(b, GEN_V2) for b in range(1, 256)]
    assert levels == sorted(levels)


def test_v1_mapping():
    assert p.brightness_to_level(1, GEN_V1) == 2
    assert p.brightness_to_level(255, GEN_V1) == 100
    assert p.level_to_brightness(100, GEN_V1) == 255
    assert p.level_to_brightness(0, GEN_V1) == 1


# --- framing (unchanged behaviour) -----------------------------------------


def test_frame_and_payload():
    f = p.frame(0x26)
    assert f == b"\xff\xff\x26\x03" + bytes([(0x26 + 3) & 0xFF])
    assert p.payload(0x26, b"\xff\xff\x26\x05ab\x00") == b"ab"
    with pytest.raises(Hp2550Error):
        p.payload(0x26, b"\xff\xff\x27\x03\x00")


def test_custom_record_round_trip():
    rec = {"id": "BL 1 #2", "key": "BP 1 BL A BS 0 #2", "host": "127.0.0.1",
           "port": 80, "interval": 60, "type": 0, "active": 1}
    assert p.parse_custom(p.build_custom(rec)) == rec


# --- confirm-then-send against a fake console ------------------------------


class FakeConsole(Hp2550Client):
    """A client whose transport is an in-memory console record."""

    def __init__(self, record, path2="", ack_after=0, drop_writes=0, reject=False,
                 faults=None):
        super().__init__("test")
        self.reject = reject
        # Scripted transport faults: a list of (cmd, landed) consumed in order
        # as those commands are sent. Each raises a timeout-style Hp2550Error;
        # landed=True means a write took effect before its reply was lost.
        self.faults = list(faults or [])
        self.exchanges: list[int] = []
        self.record = dict(record)
        self.path2 = path2
        self.ack_after = ack_after  # status reads before the echo appears
        self.drop_writes = drop_writes  # writes the console loses
        self.writes: list[str] = []
        self._reads_since_write = 0
        self._acted: str | None = None

    async def _transact(self, cmd, data=b""):
        self.exchanges.append(cmd)
        if self.faults and self.faults[0][0] == cmd:
            _, landed = self.faults.pop(0)
            if landed and cmd == p.CMD_WRITE_CUSTOMIZED:
                await self._apply(data)
            raise Hp2550Error(f"0x{cmd:02X} to test failed: TimeoutError")
        if cmd == p.CMD_READ_CUSTOMIZED:
            self._reads_since_write += 1
            if self.record["key"] and self._acted and self._reads_since_write > self.ack_after:
                seq = self._acted.rsplit("#", 1)[1]
                mark = "!" if self.reject else "#"
                self.record["key"] = f"BP 1 BL 30 BS 0 {mark}{seq}"
            return p.build_custom(self.record)
        if cmd == p.CMD_READ_USR_PATH:
            return p._lstr("/path1?") + p._lstr(self.path2)
        if cmd == p.CMD_WRITE_CUSTOMIZED:
            if self.drop_writes:
                self.writes.append(p.parse_custom(data)["id"])
                self.drop_writes -= 1
                return b""
            await self._apply(data)
            return b""
        raise AssertionError(cmd)

    async def _apply(self, data):
        new = p.parse_custom(data)
        self.writes.append(new["id"])
        self.record["id"] = new["id"]
        self._acted = new["id"]
        self._reads_since_write = 0


V2_RECORD = {"id": "BL 5 #4", "key": "BP 0 BL 30 BS 0 #4", "host": "h",
             "port": 80, "interval": 60, "type": 0, "active": 1}


async def test_read_status_v2_and_v1():
    c = FakeConsole(V2_RECORD)
    assert (await c.read_status()).generation == GEN_V2
    v1 = dict(V2_RECORD, key="")
    c = FakeConsole(v1, path2="BL 1 20 #9")
    s = await c.read_status()
    assert (s.generation, s.level, s.seq) == (GEN_V1, 20, 9)
    assert await FakeConsole(dict(V2_RECORD, key="")).read_status() is None


async def test_send_command_waits_for_echo():
    c = FakeConsole(V2_RECORD, ack_after=2)
    status = await c.send_command("BP", 1, GEN_V2, timeout=1, poll=0.001)
    assert c.writes == ["BP 1 #5"]
    assert status.seq == 5
    assert c.pending_seq is None


async def test_send_command_skips_status_seq():
    # Command slot reloaded to #2 while status says #3: #3 would be ignored.
    rec = dict(V2_RECORD, id="BL 5 #2", key="BP 0 BL 30 BS 0 #3")
    c = FakeConsole(rec)
    await c.send_command("BP", 1, GEN_V2, timeout=1, poll=0.001)
    assert c.writes == ["BP 1 #4"]


async def test_send_command_resends_once_with_same_seq():
    c = FakeConsole(V2_RECORD, drop_writes=1)
    await c.send_command("BS", 1, GEN_V2, timeout=0.02, poll=0.005)
    assert c.writes == ["BS 1 #5", "BS 1 #5"]


async def test_send_command_gives_up_after_max_writes():
    c = FakeConsole(V2_RECORD, drop_writes=5)
    with pytest.raises(Hp2550Error, match="after 3 writes"):
        await c.send_command("BS", 1, GEN_V2, timeout=1, poll=0.001)
    assert c.writes == ["BS 1 #5"] * 3
    assert c.pending_seq is None


async def test_send_command_v1_uses_path2_echo():
    rec = dict(V2_RECORD, id="BL 1 #8", key="")
    c = FakeConsole(rec, path2="BL 1 40 #8")
    orig = c._transact

    async def transact(cmd, data=b""):
        out = await orig(cmd, data)
        if cmd == p.CMD_WRITE_CUSTOMIZED:
            c.path2 = "BL 0 40 #9"
        return out

    c._transact = transact
    s = await c.send_command("BL", 0, GEN_V1, timeout=1, poll=0.001)
    assert c.writes == ["BL 0 #9"]
    assert (s.generation, s.on, s.seq) == (GEN_V1, False, 9)


async def test_commands_are_serialized():
    c = FakeConsole(V2_RECORD)
    import asyncio

    await asyncio.gather(
        c.send_command("BP", 1, GEN_V2, timeout=1, poll=0.001),
        c.send_command("BS", 1, GEN_V2, timeout=1, poll=0.001),
    )
    assert c.writes == ["BP 1 #5", "BS 1 #6"]


async def test_rejection_is_a_hard_failure_without_resend():
    c = FakeConsole(V2_RECORD, reject=True)
    with pytest.raises(Hp2550Rejected) as err:
        await c.send_command("BS", 1, GEN_V2, timeout=1, poll=0.001)
    assert c.writes == ["BS 1 #5"]  # no resend
    assert err.value.status.rejected and err.value.status.seq == 5
    assert c.pending_seq is None


async def test_applied_echo_is_success():
    c = FakeConsole(V2_RECORD)
    status = await c.send_command("BS", 1, GEN_V2, timeout=1, poll=0.001)
    assert not status.rejected


async def test_out_of_domain_value_never_reaches_the_wire():
    c = FakeConsole(V2_RECORD)
    with pytest.raises(ValueError):
        await c.send_command("BL", 101, GEN_V2, timeout=1, poll=0.001)
    assert c.writes == []


async def test_send_command_reads_the_record_once_before_writing():
    c = FakeConsole(V2_RECORD)
    await c.send_command("BP", 1, GEN_V2, timeout=1, poll=0.001)
    assert c.exchanges[:2] == [p.CMD_READ_CUSTOMIZED, p.CMD_WRITE_CUSTOMIZED]


# --- surviving a dropped exchange --------------------------------------------

W, R = p.CMD_WRITE_CUSTOMIZED, p.CMD_READ_CUSTOMIZED


async def test_lost_write_is_rewritten(caplog):
    # The Nov 21 failure: the write's reply never came and the console didn't act.
    c = FakeConsole(V2_RECORD, faults=[(W, False)])
    status = await c.send_command("BP", 0, GEN_V2, timeout=1, poll=0.001)
    assert status.seq == 5
    assert c.writes == ["BP 0 #5"]  # the faulted write never took effect
    assert c.exchanges.count(W) == 2
    text = caplog.text
    assert "write 1 of 3 failed" in text and "TimeoutError" in text
    assert "it was lost" in text
    assert "confirmed after 2 write(s); 1 failed exchange(s)" in text


async def test_write_whose_reply_was_lost_is_not_rewritten(caplog):
    c = FakeConsole(V2_RECORD, faults=[(W, True)])
    status = await c.send_command("BP", 0, GEN_V2, timeout=1, poll=0.001)
    assert status.seq == 5
    assert c.exchanges.count(W) == 1
    assert "it landed" in caplog.text


async def test_failed_first_read_is_retried():
    c = FakeConsole(V2_RECORD, faults=[(R, False)])
    status = await c.send_command("BP", 1, GEN_V2, timeout=1, poll=0.001)
    assert status.seq == 5 and c.writes == ["BP 1 #5"]


async def test_failed_status_reads_are_tolerated():
    # The first read succeeds (the head fault is a write); then the write and
    # the next two status reads fail.
    c = FakeConsole(V2_RECORD, faults=[(W, False), (R, False), (R, False)])
    status = await c.send_command("BS", 1, GEN_V2, timeout=1, poll=0.001)
    assert status.seq == 5


async def test_reverted_slot_is_rewritten_with_the_same_seq():
    c = FakeConsole(V2_RECORD, ack_after=3)
    orig = c._transact
    reverted = False

    async def transact(cmd, data=b""):
        nonlocal reverted
        out = await orig(cmd, data)
        if cmd == W and not reverted:
            # A screen exit puts the old command back before the console acts.
            reverted = True
            c.record["id"] = V2_RECORD["id"]
            c._acted = None
        return out

    c._transact = transact
    status = await c.send_command("BS", 1, GEN_V2, timeout=1, poll=0.001)
    assert c.writes == ["BS 1 #5", "BS 1 #5"]
    assert status.seq == 5


async def test_console_that_never_answers_still_raises():
    faults = [(R, False)] * 1000
    c = FakeConsole(V2_RECORD, faults=faults)
    with pytest.raises(Hp2550Error, match="record never read") as err:
        await c.send_command("BP", 0, GEN_V2, timeout=0.05, poll=0.001)
    assert "TimeoutError" in str(err.value)
    assert str(err.value).count("first record read") == 1
    assert c.writes == [] and c.pending_seq is None


def test_summary_collapses_runs_of_identical_failures():
    from custom_components.ecowitt_hp2550.protocol import _summary

    assert _summary([]) == ""
    assert _summary(["a: x", "a: x", "a: x", "b: y", "a: x"]) == (
        "; 5 failed exchange(s): a: x (3 times); b: y; a: x")


async def test_repeated_first_read_failures_are_listed_once(caplog):
    c = FakeConsole(V2_RECORD, faults=[(R, False)] * 3)
    await c.send_command("BP", 0, GEN_V2, timeout=1, poll=0.001)
    confirmed = [r.getMessage() for r in caplog.records if "confirmed after" in r.getMessage()]
    assert len(confirmed) == 1
    assert "3 failed exchange(s)" in confirmed[0]
    assert confirmed[0].count("first record read") == 1
    assert "(3 times)" in confirmed[0]


async def test_every_write_lost_raises_with_each_attempt_named():
    c = FakeConsole(V2_RECORD, faults=[(W, False)] * 3)
    with pytest.raises(Hp2550Error) as err:
        await c.send_command("BP", 0, GEN_V2, timeout=1, poll=0.001)
    msg = str(err.value)
    assert "after 3 writes" in msg
    for n in (1, 2, 3):
        assert f"write {n} of 3" in msg


async def test_total_time_is_bounded_by_timeout():
    import asyncio
    import time

    c = FakeConsole(V2_RECORD)
    orig = c._transact

    async def hang_on_write(cmd, data=b""):
        if cmd == W:
            await asyncio.sleep(10)  # an exchange that never finishes
        return await orig(cmd, data)

    c._transact = hang_on_write
    t0 = time.monotonic()
    with pytest.raises(Hp2550Error, match="within 0.1 s"):
        await c.send_command("BP", 0, GEN_V2, timeout=0.1, poll=0.001)
    assert time.monotonic() - t0 < 0.5
    assert c.pending_seq is None


# --- transport: error text and the checksum wait ----------------------------


def test_describe_names_the_type():
    assert p.describe(TimeoutError()) == "TimeoutError"
    assert p.describe(OSError(101, "Network unreachable")) == (
        "OSError: [Errno 101] Network unreachable")


def _serve(monkeypatch, reply: bytes | None):
    """Stand in for the console's socket: every connection receives `reply`.

    The stream is left open after the reply (no EOF), as the ESP may do, so a
    read for more bytes only ends on a timeout.
    """
    import asyncio

    class Writer:
        def write(self, data):
            pass

        async def drain(self):
            pass

        def close(self):
            pass

        async def wait_closed(self):
            pass

    async def open_connection(host, port):
        reader = asyncio.StreamReader()
        if reply is not None:
            reader.feed_data(reply)
        return reader, Writer()

    monkeypatch.setattr(p.asyncio, "open_connection", open_connection)


async def test_full_frame_does_not_wait_for_a_tail(monkeypatch):
    import time

    mac = bytes(range(6))
    body = bytes([p.CMD_READ_MAC, 3 + len(mac)]) + mac
    _serve(monkeypatch, b"\xff\xff" + body + bytes([sum(body) & 0xFF]))
    t0 = time.monotonic()
    assert await Hp2550Client("127.0.0.1").read_mac() == "00:01:02:03:04:05"
    assert time.monotonic() - t0 < p.TAIL_TIMEOUT


async def test_frame_without_checksum_still_parses(monkeypatch):
    mac = bytes(range(6))
    _serve(monkeypatch, b"\xff\xff" + bytes([p.CMD_READ_MAC, 9]) + mac)
    assert await Hp2550Client("127.0.0.1").read_mac() == "00:01:02:03:04:05"


async def test_read_timeout_names_the_type_and_command(monkeypatch):
    monkeypatch.setattr(p, "TIMEOUT", 0.1)
    _serve(monkeypatch, None)
    with pytest.raises(Hp2550Error, match=r"0x2A to 127\.0\.0\.1 failed: TimeoutError"):
        await Hp2550Client("127.0.0.1").read_custom()
