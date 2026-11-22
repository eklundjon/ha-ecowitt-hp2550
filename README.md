# Ecowitt HP2550 Console

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/docs/faq/custom_repositories/)
[![HA Version](https://img.shields.io/badge/Home%20Assistant-2025.4+-blue.svg?logo=homeassistant)](https://www.home-assistant.io)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A Home Assistant integration that controls the **backlight** of an Ambient
Weather / Ecowitt HP2550 weather console over the local network: a `light`
(on/off, brightness) and, on current firmware, switches for auto-brightness, the
backlight schedule and dark mode.

The console has no network backlight control of its own. This talks to a small
firmware patch that repurposes custom-server fields as a command/status mailbox
on the console's port-45000 API. So the integration only does anything useful
once that patch is on the console - see Requirements. Everything stays on the
LAN; there's no cloud and no vendor server in the loop.

This is an unofficial integration, not affiliated with or endorsed by Ambient
Weather or Ecowitt.

## Requirements

- An HP2550 console (or a rebrand of it - see Supported devices) running the
  **backlight-over-IP firmware patch**. Without the patch the integration still
  connects and adds the entity, but the backlight won't respond to it. The patch
  and its build tooling are in
  [ecowitt-hp2550-backlight](https://github.com/eklundjon/ecowitt-hp2550-backlight).
- Home Assistant 2025.4 or newer.
- The console reachable on the LAN. The port-45000 API is served by the
  console's ESP WiFi module, so the console has to be associated to WiFi for any
  of this to work.
- If the console's custom-server upload is enabled, its protocol set to
  **Ecowitt** ("Same as AmbWeather" on Ambient consoles), not Wunderground. In
  Wunderground mode the console sends the Station ID and Station Key upstream as
  credentials, and those fields are the mailbox. With the upload disabled, either
  protocol works.

note: set a DHCP reservation for the console. The integration keys on the MAC and
updates the stored IP when the console re-registers, but a stable address is one
less thing to think about.

## Two firmware generations

The integration works out which one it's talking to from where the console
publishes its status, so there's nothing to configure.

- **v2** (the new protocol): status goes in the custom server's password field.
  The status is the console's actual state, so the entities follow keypad
  changes and the console's own schedule and auto-brightness.
- **v1** (the published patch, v0.1.0 of this integration): status goes in the
  second upload path and only records the last command. You get the light and
  nothing else, and it stays `assumed_state`.

Use v2 firmware. v1 was essentially a proof of concept, and this release's v1
support is hypothetical: it keeps v0.1.0's behaviour and is covered by unit
tests, but nobody has run this release against a v1 console. If you're on v1,
upgrade the patch.

Note that downgrading a console from v2 to v1 leaves the v2 status in the
password field (the WiFi module keeps it across reflashes, and v1 never writes
it), so the integration would keep treating the console as v2. Clear the custom
server's password before going back to v1.

### v1 caveat: turn off auto-brightness and the schedule

On v1 the console's own **auto-brightness** and **backlight schedule** override
what the integration sets - auto-brightness owns the level, the schedule owns
on/off. With either enabled the commands are advisory, and nothing reports the
console's own changes back. For control that sticks, turn both off in the
console's backlight settings. v2 doesn't have this problem: it reports both and
lets you switch them from HA.

## Install

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=eklundjon&repository=ha-ecowitt-hp2550&category=integration)

The button adds this repository to HACS on your instance; then install
"Ecowitt HP2550 Console" and restart Home Assistant. Or add it by hand:

1. HACS → three-dot menu → Custom repositories.
2. Add `https://github.com/eklundjon/ha-ecowitt-hp2550`, category **Integration**.
3. Install "Ecowitt HP2550 Console", then restart Home Assistant.
4. Settings → Devices & services → Add integration → "Ecowitt HP2550 Console",
   and enter the console's IP address or hostname.

Brand icons only appear after a Home Assistant restart; clearing the browser
cache isn't enough.

## What you get

On v2 firmware:

| Entity | Does |
|---|---|
| `light` Backlight | On/off (BP) and brightness (BL). The console's level 0-100 maps to HA brightness 1-255; level 0 is the dimmest *lit* level, so HA never shows a lit panel as off. |
| `switch` Auto brightness | Turns the console's auto-brightness on/off. While it's on, the light's brightness is unknown (the console reports `A`, not a level). |
| `switch` Backlight schedule | Enables/disables the console's backlight schedule. |
| `switch` Dark mode | On = dark background. Only created if the console reports it (newer builds). |

Behaviours worth knowing:

- Setting a brightness turns auto-brightness off (that's what the console does),
  and doesn't change power. Calling `light.turn_on` with a brightness on a dark
  panel sends the level, then power on.
- Turning auto-brightness off restores the last numeric level the integration
  saw, or 50 if it hasn't seen one. The console doesn't report the saved level
  while auto is on, on purpose: under auto it doesn't drive anything (the
  console's own keypad shows the same - press brightness under auto and it
  jumps, then snaps back), so reporting it would imply a control that doesn't
  exist.
- Commands that wouldn't change anything are skipped, since each one costs a
  record push and delays the console's next custom-server upload.
- A v1 console publishes no status until it has acted on a command, so a fresh
  one shows the light as unknown. Commands still work and are sent as v1.
- The state is read from the console, so it's authoritative: no `assumed_state`.
- Each command is confirmed before the next one goes out: the integration waits
  for the console to echo the command's sequence number, retrying failed reads
  and rewriting the command (at most 3 writes) if it didn't land. A command that
  isn't confirmed within 20 s raises an error in HA that names what failed.
  - This means a service call can take up to 20 s, or 40 s for `light.turn_on`
    with a brightness on a dark panel (two commands). Automations that call these
    back to back wait for each one.
  - A command that needed a retry still succeeds, but logs one warning saying
    what went wrong first.
- Round-trip latency is usually 5-10 s - the console polls the mailbox, it isn't
  pushed.

On v1 firmware you get the light only, as before: on/off and brightness (0-100
shown as 0-255), `assumed_state`, seeded from the console's last status report.

### Stock-console behaviours you'll notice

These come from the console's own firmware, not the patch (details in the
protocol notes in the patch repo):

- While someone is on the **Customized server** screen the patch stands down, and
  leaving it (or its ~1-1.5 min timeout) loses any command sent during the visit.
  The integration rewrites the command if the visit ends within its 20 s window;
  otherwise the command fails and you'll need to send it again.
- The **backlight menu** writes its settings back when you leave it, undoing
  backlight, power and schedule changes sent while it was open.
- Values set over IP live in the console's RAM. They normally don't survive a
  reboot, but a keypad change triggers the console's own settings save, which
  stores whatever is current at that moment, IP-set values included.
- Each command delays the console's next custom-server upload by up to one
  interval.
- The keypad's 0-10 brightness is the level / 10, so levels between steps are
  real and visibly distinct.
- The console's **WiFi module** answers one client at a time and takes at most
  five connections. Another port-45000 client that holds connections open (e.g.
  a script that connects once and reuses the connection) can make the
  integration's exchanges fail, which shows up as warnings and slower commands.

The **v1** patch writes the console's Station ID and second upload path (the
mailbox), and doing so **breaks the console's custom-server data upload** -
confirmed on hardware. So this is incompatible with pushing the console's own
weather data to Home Assistant through its custom server (the standard `ecowitt`
integration or ecowitt2mqtt): the first backlight command leaves that upload
POSTing to an empty path. The v2 patch keeps its status out of the upload path
and is meant to coexist with the upload by design, but that hasn't been
confirmed on hardware. Use this only if your weather data reaches HA another
way (e.g. a separate GW-series gateway) or not from the console at all. The
[patch repo](https://github.com/eklundjon/ecowitt-hp2550-backlight) has the
mechanism and a planned fix.

## Supported devices

Verified means someone ran it on that hardware and firmware; inferred means it's
the same firmware and expected to work, but unconfirmed. A report for an
unlisted device (include the console's firmware version) is welcome.

| Device | Brand | Firmware | Status |
|---|---|---|---|
| HP2550 (WS-5000) | Ambient Weather | V2.0.4, v2 patch | Verified, 2026-11 |
| HP2550 (WS-5000) | Ambient Weather | V2.0.4, v1 patch | Verified with v0.1.0 only; not tested with this release |
| HP2550 | Ecowitt | same V2.0.4 image | Inferred - one brand-selected firmware carries both the `HP2550` and `HP2550_AMB` model strings |
| HP2560 | Ecowitt / Ambient | same image | Inferred - the firmware's device table declares both HP2550 and HP2560 |

The patch is pinned to a specific firmware build (fixed code offsets and a
checksum), so a console on a different version needs the patch re-ported even
when the hardware matches. That, not the brand, is the real constraint.

## How it works

The patch hooks the console's display loop and, every few seconds, reads a
command from the custom-server Station ID (`<VERB> <value> #<seq>`) and writes a
status report back. The console acts on a command once, when its `<seq>` differs
from the one in the status.

- v2 verbs: `BL 0-100` (level, turns auto off), `BL A` (auto on), `BP 0/1`
  (power), `BS 0/1` (schedule), `DM 0/1` (dark mode). Status, in the password
  field: `BP 1 BL A BS 0 DM 1 #7`. Pairs can come in any order and the
  integration ignores ones it doesn't know.
- v1 verbs: `BL 0` off, `BL 1` on, `BL 2-100` level. Status, in the second upload
  path: `BL <on> <level> #<seq>`.

This integration is the other end: it writes commands and reads status over the
port-45000 framed API, connecting directly to the console's TCP port - that port
is the only way in, so there's nothing to abstract behind a transport layer.

## Diagnostics

Download diagnostics from the device page when reporting a problem. The file has
the raw mailbox slots and the parsed status, with the MAC and host redacted.

## Development

    uv venv --python 3.14 .venv-test
    uv pip install --python .venv-test/bin/python -r requirements_test.txt
    .venv-test/bin/python -m pytest

The tests never talk to a console. CI also runs them against the declared
minimum, Home Assistant 2025.4.
