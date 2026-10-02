# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Every relay drop is followed by a check of the rig's own link (#521).

The rig's tunnel to the Fly relay drops in clusters: seven drops on
2026-09-22, five of them in half an hour, one of them a failed DNS lookup on
the rig's side. Each is logged as "relay connection lost ... no close frame
received or sent", and that sentence is the same whether the rig's internet
went, the relay went, or a NAT between them forgot the flow. So each drop
now logs one more line, a check of the rig's side taken right after it:

  * is the default gateway reachable (the LAN and the router),
  * does DNS resolve the relay's host (the rig's view of the internet; the
    host is not printed, and nor is any address),
  * how long before the drop the last frame came from the relay (a relay
    that went quiet long before the drop is a dead path; one that spoke a
    second before is a cut).

The check runs on threads of its own, is bounded by ``_LINK_CHECK_BOUND_S``,
and never delays the re-dial. The probes are injected here: no case touches
the network. The system probes' parsing is graded on canned text.

MUTATIONS RUN, 2026-09-29, each in a private scratch copy of server/
(scratchpad/H4-H4-RELAY-mut in the session scratchpad), from a byte backup
restored with its sha256 checked. The observed failure is recorded in the
case it turned red.

  M1 "check removed"             ``run`` no longer calls ``_after_drop``.
  M2 "check awaited before the re-dial"
                                 ``run`` awaits the check task it started
                                 before it sleeps and re-dials.
  M3 "probes on the loop"        ``_probe`` calls the probe inline on the
                                 event loop instead of on a thread.
  M4 "frame clock never read"    the line always says "no relay frame this
                                 session".
  M5 "lifespan client unprobed"  ``run_relay_client`` builds the client
                                 without ``SYSTEM_LINK_PROBES``.
  M6 "gateway byte order"        ``_gateway_from_proc_route`` reads the
                                 kernel's little-endian word big-endian.
  M7 "an exit code is a reply"   ``_ping`` on Windows trusts exit code 0
                                 without an echo reply's ``TTL=``.
  M8 "On-link read as a gateway" ``_gateway_from_route_print`` returns the
                                 third column without checking it is an
                                 address.
  M9 "a failed lookup reads as unknown"
                                 ``_system_dns_probe`` answers None, not
                                 False, when the lookup fails.
  M10 "a second check over the first"
                                 ``_after_drop`` starts a new check while
                                 the previous one is still out.
  M11 "no thread cap"            ``_probe`` starts a thread whatever the
                                 count of probes still out.
  M12 "drop moment is now"       ``_after_drop`` measures the frame age to
                                 ``time.monotonic()`` instead of to the
                                 recorded end of the read (added by the
                                 verifier; it survived M1-M11's cases).
"""
from __future__ import annotations

import asyncio
import contextlib
import re
import subprocess
import threading
import time

import pytest

import astrodeck.remote.relay_client as rc
from astrodeck.config import RemoteConfig
from astrodeck.remote.protocol import CONTROL_STREAM_ID, FrameType, encode_frame
from astrodeck.remote.relay_client import LinkProbes, RelayClient

from _deadline import wait_until

TEST_DEVICE_TOKEN = "t" * 43
RELAY_HOST = "relay.test"
CFG = RemoteConfig(enabled=True, relay_url=f"wss://{RELAY_HOST}/scope",
                   device_token=TEST_DEVICE_TOKEN, home_id="home-1")
CHECK = "relay link check"


async def _noop_app(scope, receive, send):  # pragma: no cover - never reached
    pass


class _SessionThatDrops:
    """A relay socket that ACKs the HELLO, stays quiet for ``quiet_s`` and
    then dies the way the rig's drops do, through the async iterator."""

    def __init__(self, quiet_s: float, dropped: dict):
        self.quiet_s = quiet_s
        self.dropped = dropped

    async def send(self, data):
        pass

    async def close(self):
        pass

    def __aiter__(self):
        async def _gen():
            yield encode_frame(FrameType.HELLO_ACK, CONTROL_STREAM_ID,
                               {"ok": True})
            await asyncio.sleep(self.quiet_s)
            self.dropped["at"] = time.monotonic()
            raise ConnectionError("no close frame received or sent")
            yield b""  # pragma: no cover - makes this a generator
        return _gen()


def _checks(bus_lines) -> list[str]:
    return [m for lvl, m, src in bus_lines if CHECK in m]


def _the_check(bus_lines) -> str:
    """The one link-check line the drop wrote, or a failure that shows the
    whole log when there is not exactly one."""
    lines = _checks(bus_lines)
    assert len(lines) == 1, (
        f"expected one {CHECK!r} line after the drop, got {len(lines)}: "
        f"{bus_lines}")
    return lines[0]


async def _one_drop(bus_lines, probes, *, first,
                    until=lambda lines, seen: _checks(lines)) -> dict:
    """Run the supervisor through ONE failure (``first`` is what the first
    dial does) and a second dial that parks forever, until ``until`` holds.
    Returns what the dials recorded."""
    seen: dict = {"dials": 0}
    park = asyncio.Event()

    async def connect(url):
        seen["dials"] += 1
        if seen["dials"] == 1:
            return await first(seen)
        seen["redial_at"] = time.monotonic()
        seen["checks_at_redial"] = len(_checks(bus_lines))
        await park.wait()               # the second session never ends

    client = RelayClient(_noop_app, lambda: CFG, connect=connect,
                         link_probes=probes)
    task = asyncio.create_task(client.run())
    try:
        # A wall-clock deadline (#610): 1000 x sleep(0.01) is 10 s on Linux
        # but 15.6 s on Windows (sleep rounds up to the 15.6 ms timer
        # there), so a round count gives the two platforms different real
        # patience.
        await wait_until(lambda: until(bus_lines, seen), timeout_s=18.0,
                         interval_s=0.01)
    finally:
        client.stop()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    return seen


@pytest.fixture
def fast_redial(monkeypatch):
    monkeypatch.setattr(rc, "_backoff_delay", lambda attempt: 0.0)


@pytest.mark.parametrize("gateway,dns", [(True, False), (False, True)],
                         ids=["gateway-up-dns-down", "gateway-down-dns-up"])
def test_a_drop_logs_one_link_check_line_with_each_field(
        bus_lines, fast_redial, gateway, dns):
    """A session that ACKs, is quiet for 0.3 s and drops: one info line
    for gen=1 with the gateway answer, the DNS answer and the age of the
    last relay frame. Parametrized both ways round so a word cannot be a
    constant. The DNS probe is asked about the relay's host, and the line
    names neither the host nor an address.

    RED under M1 "check removed" (each case). Observed:
        AssertionError: expected one 'relay link check' line after the
        drop, got 0: [('info', 'relay authenticated (gen=1)', 'remote'),
        ('warning', 'relay connection lost gen=1 after 0.3s
        (ConnectionError: no close frame received or sent); retrying
        local-only', 'remote')]
        assert 0 == 1
    RED under M4 "frame clock never read" (each case). Observed, the
    first; the second the same with "reachable no" and "resolves yes":
        AssertionError: the line must say how long before the drop the
        relay last spoke: relay link check gen=1: default gateway
        reachable yes; DNS for the relay host resolves no; no relay frame
        this session
        assert None
    """
    asked: list = []

    def dns_probe(host, timeout_s):
        asked.append(host)
        return dns

    probes = LinkProbes(gateway=lambda timeout_s: gateway, dns=dns_probe)

    async def first(seen):
        return _SessionThatDrops(0.3, seen)

    asyncio.run(_one_drop(bus_lines, probes, first=first))
    line = _the_check(bus_lines)
    (entry,) = [e for e in bus_lines if e[1] == line]
    assert entry[0] == "info" and entry[2] == "remote", entry
    assert line.startswith(f"{CHECK} gen=1: "), line
    word = {True: "yes", False: "no"}
    assert f"default gateway reachable {word[gateway]};" in line, line
    assert f"DNS for the relay host resolves {word[dns]};" in line, line
    age = re.search(r"last relay frame (\d+\.\d)s before the drop", line)
    assert age, ("the line must say how long before the drop the relay last "
                 "spoke: " + line)
    assert 0.2 <= float(age.group(1)) < 5.0, line
    assert asked == [RELAY_HOST], asked
    assert RELAY_HOST not in line, line
    assert not re.search(r"\d+\.\d+\.\d+\.\d+", line), line
    # It follows the drop's own line, which it explains.
    order = [m for _lvl, m, _src in bus_lines]
    lost = next(i for i, m in enumerate(order) if "relay connection lost" in m)
    assert lost < order.index(line), order


def test_a_dial_that_fails_is_checked_too_with_no_frame_to_age(
        bus_lines, fast_redial):
    """The 2026-09-22 cluster held a ``gaierror`` on the DIAL. A dial that
    never reached the relay is checked the same way, and says it had no
    frame to measure from rather than a number.

    RED under M1 "check removed". Observed:
        AssertionError: expected one 'relay link check' line after the
        drop, got 0: [('warning', 'relay connection lost gen=1 after 0.0s
        (OSError: getaddrinfo failed); retrying local-only', 'remote')]
        assert 0 == 1
    """
    probes = LinkProbes(gateway=lambda t: True, dns=lambda host, t: False)

    async def first(seen):
        raise OSError("getaddrinfo failed")

    asyncio.run(_one_drop(bus_lines, probes, first=first))
    line = _the_check(bus_lines)
    assert line.endswith("no relay frame this session"), line


class _SessionSlowToClose(_SessionThatDrops):
    """The same drop, but closing the dead socket takes ``close_s``: the
    teardown ``_serve_once`` runs after the read ends (cancelling the lane
    tasks for up to ``_TASK_TEARDOWN_TIMEOUT_S``, then ``ws.close()``)."""

    def __init__(self, quiet_s: float, dropped: dict, close_s: float):
        super().__init__(quiet_s, dropped)
        self.close_s = close_s

    async def close(self):
        await asyncio.sleep(self.close_s)


def test_the_frame_age_stops_at_the_drop_not_after_the_teardown(
        bus_lines, fast_redial):
    """The age is how long the relay had been silent WHEN THE READ ENDED.
    The teardown after it is the rig's own work, and counting it would make
    a cut a second after a PING read as a relay that went quiet. Quiet for
    0.3 s, then a close that takes 1.5 s: the line says 0.3, not 1.8.

    Added by the H4-RELAY verifier: the mutant below survived every other
    case, because each fake closes at once.

    RED under M12 "drop moment is now" (``_after_drop`` measures to
    ``time.monotonic()`` even when the read's end was recorded). Observed:
        AssertionError: the age must stop at the drop, not run on through
        the 1.5 s teardown: relay link check gen=1: default gateway
        reachable yes; DNS for the relay host resolves yes; last relay
        frame 1.8s before the drop
        assert 1.8 < 1.0
    """
    probes = LinkProbes(gateway=lambda t: True, dns=lambda host, t: True)

    async def first(seen):
        return _SessionSlowToClose(0.3, seen, close_s=1.5)

    asyncio.run(_one_drop(bus_lines, probes, first=first))
    line = _the_check(bus_lines)
    age = re.search(r"last relay frame (\d+\.\d)s before the drop", line)
    assert age, line
    assert float(age.group(1)) < 1.0, (
        "the age must stop at the drop, not run on through the 1.5 s "
        "teardown: " + line)


def test_control_a_hanging_dns_probe_does_not_delay_the_redial(
        bus_lines, fast_redial, monkeypatch):
    """CONTROL: the probe the 2026-09-22 cluster would have hit, a DNS
    lookup that does not come back. The re-dial goes at once, before the
    check has an answer; the check then reports the DNS probe as no answer
    at its bound, and the gateway's answer beside it.

    RED under M2 "check awaited before the re-dial". Observed:
        AssertionError: the re-dial waited 2.00 s on the link check (bound
        2 s): the check must never delay it
        assert (2.0 < (2.0 / 2))
    RED under M3 "probes on the loop". Observed (the hung probe held the
    whole loop until its own 10 s wait ran out):
        AssertionError: the re-dial waited 10.02 s on the link check (bound
        2 s): the check must never delay it
        assert (10.016000000061467 < (2.0 / 2))
    RED under M1 "check removed", with the dial-failure case's message and
    this case's log (after 0.0s, ConnectionError).
    """
    monkeypatch.setattr(rc, "_LINK_CHECK_BOUND_S", 2.0)
    release = threading.Event()

    def hanging_dns(host, timeout_s):
        release.wait(10.0)          # a lookup that does not come back
        return True

    probes = LinkProbes(gateway=lambda t: True, dns=hanging_dns)

    async def first(seen):
        return _SessionThatDrops(0.0, seen)

    try:
        # Wait for BOTH the check and the re-dial, so a re-dial that came
        # late is measured below rather than never seen.
        seen = asyncio.run(_one_drop(
            bus_lines, probes, first=first,
            until=lambda lines, seen: _checks(lines) and "redial_at" in seen))
    finally:
        release.set()
    assert "redial_at" in seen, f"no re-dial happened: {bus_lines}"
    waited = seen["redial_at"] - seen["at"]
    assert waited < rc._LINK_CHECK_BOUND_S / 2 and seen["checks_at_redial"] == 0, (
        f"the re-dial waited {waited:.2f} s on the link check (bound "
        f"{rc._LINK_CHECK_BOUND_S:g} s): the check must never delay it")
    line = _the_check(bus_lines)
    assert "default gateway reachable yes;" in line, line
    assert "DNS for the relay host resolves no (no answer in 2s);" in line, line


def test_a_drop_inside_the_previous_check_says_it_was_skipped(
        bus_lines, fast_redial, monkeypatch):
    """One check at a time. The first drop's DNS probe hangs, the re-dial
    fails at once, and that second drop lands while the first check is
    still out: it says so in its own line and starts no second set of
    probes. The first check still reports in full at its bound.

    RED under M10 "a second check over the first". Observed:
        AssertionError: ['relay link check gen=1: default gateway reachable
        yes; DNS for the relay host resolves no (no answer in 1s); last
        relay frame 0.0s before the drop', 'relay link check gen=2: default
        gateway reachable yes; DNS for the relay host resolves no (no
        answer in 1s); no relay frame this session']
        assert 'relay link check gen=2: default gateway reachable yes; DNS
        for the relay host resolves no (no answer in 1s); no relay frame
        this session' == "relay link check gen=2: skipped, the previous
        drop's check is still waiting on its probes; no relay frame this
        session"
    RED under M2 "check awaited before the re-dial" with the same two
    lines (the first check was over before the second drop), and under M3
    "probes on the loop":
        AssertionError: assert 'DNS for the relay host resolves no (no
        answer in 1s)' in 'relay link check gen=1: default gateway
        reachable yes; DNS for the relay host resolves yes; last relay
        frame 0.0s before the drop'
    """
    monkeypatch.setattr(rc, "_LINK_CHECK_BOUND_S", 1.0)
    release = threading.Event()
    asked: list = []

    def hanging_dns(host, timeout_s):
        asked.append(host)
        release.wait(10.0)
        return True

    probes = LinkProbes(gateway=lambda t: True, dns=hanging_dns)
    seen: dict = {"dials": 0}
    park = asyncio.Event()

    async def connect(url):
        seen["dials"] += 1
        if seen["dials"] == 1:
            return _SessionThatDrops(0.0, seen)
        if seen["dials"] == 2:
            raise OSError("getaddrinfo failed")
        await park.wait()

    async def scenario():
        client = RelayClient(_noop_app, lambda: CFG, connect=connect,
                             link_probes=probes)
        task = asyncio.create_task(client.run())
        try:
            # A wall-clock deadline (#610): see ``_one_drop`` above for why
            # a round count of sub-0.1 s sleeps is platform-dependent.
            await wait_until(lambda: len(_checks(bus_lines)) >= 2,
                             timeout_s=18.0, interval_s=0.01)
        finally:
            client.stop()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    try:
        asyncio.run(scenario())
    finally:
        release.set()
    lines = _checks(bus_lines)
    by_gen = {g: [ln for ln in lines if ln.startswith(f"{CHECK} gen={g}:")]
              for g in (1, 2)}
    assert len(by_gen[1]) == 1 and len(by_gen[2]) == 1, lines
    assert by_gen[2][0] == (
        f"{CHECK} gen=2: skipped, the previous drop's check is still "
        "waiting on its probes; no relay frame this session"), lines
    assert "DNS for the relay host resolves no (no answer in 1s)" in by_gen[1][0]
    assert asked == [RELAY_HOST], f"a second set of probes ran: {asked}"


def test_probes_are_not_started_past_the_thread_cap(
        bus_lines, fast_redial, monkeypatch):
    """With the cap reached (0 here, so at once), a probe is not started and
    answers unknown. getaddrinfo cannot be cancelled, so without the cap a
    link that hangs every lookup would add threads for as long as it
    lasted.

    RED under M11 "no thread cap". Observed:
        AssertionError: a probe ran past the cap: ['gw', 'dns']
        assert ['gw', 'dns'] == []
    """
    monkeypatch.setattr(rc, "_LINK_PROBE_THREADS_MAX", 0)
    ran: list = []
    probes = LinkProbes(gateway=lambda t: ran.append("gw") or True,
                        dns=lambda host, t: ran.append("dns") or True)

    async def first(seen):
        return _SessionThatDrops(0.0, seen)

    asyncio.run(_one_drop(bus_lines, probes, first=first))
    line = _the_check(bus_lines)
    assert ran == [], f"a probe ran past the cap: {ran}"
    assert ("default gateway reachable unknown (earlier probes are still "
            "out); DNS for the relay host resolves unknown (earlier probes "
            "are still out)") in line, line


def test_the_lifespan_client_carries_the_system_probes(monkeypatch):
    """The production path: the client the app's lifespan starts is the
    one with the system probes. Every other case injects its own, so
    without this one the check could be graded everywhere and run nowhere.
    Nothing is dialed: the connect parks.

    RED under M5 "lifespan client unprobed". Observed:
        AssertionError: the lifespan's client runs no link check
        assert None is LinkProbes(gateway=<function
        test_the_lifespan_client_carries_the_system_probes.<locals>.<lambda>
        at 0x000002D56D462020>, dns=<function ...<lambda> at
        0x000002D56D461F80>)
         +  where None = <astrodeck.remote.relay_client.RelayClient object
        at 0x000002D51EE35D90>._link_probes
    """
    marker = LinkProbes(gateway=lambda t: None, dns=lambda h, t: None)
    monkeypatch.setattr(rc, "SYSTEM_LINK_PROBES", marker)
    monkeypatch.setattr(rc, "_current_client", None)

    async def park(self, url):
        await asyncio.Event().wait()

    monkeypatch.setattr(rc.RelayClient, "_default_connect", park)

    async def scenario():
        client = await rc.run_relay_client(_noop_app, lambda: CFG)
        try:
            assert client._link_probes is marker, (
                "the lifespan's client runs no link check")
        finally:
            client.stop()

    asyncio.run(scenario())


# ------------------------------------------------ the system probes' parsing

#: /proc/net/route as a Linux board prints it: a default route through
#: 192.168.1.1 (0101A8C0 little-endian) on wlan0, and the LAN's own route.
_PROC_ROUTE = (
    "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask"
    "\t\tMTU\tWindow\tIRTT\n"
    "wlan0\t00000000\t0101A8C0\t0003\t0\t0\t600\t00000000\t0\t0\t0\n"
    "wlan0\t0001A8C0\t00000000\t0001\t0\t0\t600\t00FFFFFF\t0\t0\t0\n"
)


def test_the_proc_route_parser_finds_the_default_gateway():
    """RED under M6 "gateway byte order". Observed:
        AssertionError: assert '1.1.168.192' == '192.168.1.1'
    """
    assert rc._gateway_from_proc_route(_PROC_ROUTE) == "192.168.1.1"
    # CONTROL: a table with no default route answers "" (no gateway, so the
    # link is down), not an address.
    lan_only = "\n".join(_PROC_ROUTE.splitlines()[::2]) + "\n"
    assert rc._gateway_from_proc_route(lan_only) == ""


def test_the_route_print_parser_finds_the_default_gateway():
    """A route whose gateway is "On-link" (the LAN's own) is not a default
    gateway to ping.

    RED under M8 "On-link read as a gateway". Observed:
        AssertionError: assert 'On-link' == ''
    """
    text = (
        "IPv4 Route Table\n"
        "===================================================================\n"
        "Active Routes:\n"
        "Network Destination        Netmask          Gateway       Interface  Metric\n"
        "          0.0.0.0          0.0.0.0      192.168.1.1    192.168.1.50     25\n"
        "===================================================================\n"
    )
    assert rc._gateway_from_route_print(text) == "192.168.1.1"
    on_link = text.replace("192.168.1.1 ", "On-link     ")
    assert rc._gateway_from_route_print(on_link) == ""
    assert rc._gateway_from_route_print("") == ""


def test_windows_ping_needs_an_echo_reply(monkeypatch):
    """Windows ping exits 0 when a router answers "Destination host
    unreachable" for the target, so only an echo reply's TTL= counts.

    RED under M7 "an exit code is a reply". Observed:
        AssertionError: an 'unreachable' answer read as reachable
        assert True is False
    """
    answers = {
        "reply": b"Reply from 192.168.1.1: bytes=32 time=1ms TTL=64\r\n",
        "unreachable": b"Reply from 192.168.1.50: Destination host "
                       b"unreachable.\r\n",
    }
    monkeypatch.setattr(rc.sys, "platform", "win32")
    for key, out in answers.items():
        monkeypatch.setattr(
            rc.subprocess, "run",
            lambda *a, _out=out, **k: subprocess.CompletedProcess(
                a, 0, stdout=_out, stderr=b""))
        got = rc._ping("192.168.1.1", 1.0)
        if key == "reply":
            assert got is True, key
        else:
            assert got is False, "an 'unreachable' answer read as reachable"


def test_the_dns_probe_says_no_on_a_failed_lookup(monkeypatch):
    """A lookup that fails is a no, the finding the 2026-09-22 gaierror
    was, not an unknown.

    RED under M9 "a failed lookup reads as unknown". Observed:
        AssertionError: assert None is False
    """
    def fail(*a, **k):
        raise rc.socket.gaierror(11001, "getaddrinfo failed")

    monkeypatch.setattr(rc.socket, "getaddrinfo", fail)
    assert rc._system_dns_probe(RELAY_HOST, 1.0) is False
    monkeypatch.setattr(rc.socket, "getaddrinfo",
                        lambda *a, **k: [("family", "type", 6, "", ("x", 0))])
    assert rc._system_dns_probe(RELAY_HOST, 1.0) is True
