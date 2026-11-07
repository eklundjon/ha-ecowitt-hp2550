# Ecowitt HP2550 Console

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/docs/faq/custom_repositories/)
[![HA Version](https://img.shields.io/badge/Home%20Assistant-2025.4+-blue.svg?logo=homeassistant)](https://www.home-assistant.io)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A Home Assistant integration that controls the **backlight** of an Ambient
Weather / Ecowitt HP2550 weather console over the local network, as a `light`
entity (on/off and brightness).

The console has no network backlight control of its own. This talks to a small
firmware patch that repurposes two unused custom-server fields as a
command/status mailbox on the console's port-45000 API. So the integration only
does anything useful once that patch is on the console - see Requirements.
Everything stays on the LAN; there's no cloud and no vendor server in the loop.

This is an unofficial integration, not affiliated with or endorsed by Ambient
Weather or Ecowitt.

## Requirements

- An HP2550 console (or a rebrand of it - see Supported devices) running the
  **backlight-over-IP firmware patch**. Without the patch the integration still
  connects and adds the entity, but the backlight won't respond to it. The patch
  and its build tooling are published separately (repo forthcoming).
- Home Assistant 2025.4 or newer.
- The console reachable on the LAN. The port-45000 API is served by the
  console's ESP WiFi module, so the console has to be associated to WiFi for any
  of this to work.

note: set a DHCP reservation for the console. The integration keys on the MAC and
updates the stored IP when the console re-registers, but a stable address is one
less thing to think about.

## The thing that trips people up

The console's own **auto-brightness** and **backlight schedule** override
whatever the integration sets - auto-brightness owns the level, the schedule owns
on/off. With either enabled, the integration's commands are advisory: the
backlight jumps to your setting, then the console pulls it back on the next
frame. For control that sticks, turn both off in the console's display/backlight
settings.

The light is marked `assumed_state` for this reason. HA can't confirm where the
backlight actually is - the console can change it with no command, and nothing
reports that back - so the entity tracks what it last commanded or last read,
and says so.

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

One `light` entity for the backlight:

- on/off, and brightness. The console's level is 0-100; HA shows it as 0-255.
- `assumed_state`, as above. On setup the state is seeded from the console's last
  status report, then tracked optimistically.
- Round-trip latency is a few seconds - the console polls the mailbox, it isn't
  pushed, so a command takes a poll cycle to land and report back.

The integration is the sole writer of the console's Station ID and second upload
path (the two mailbox fields). If you also point the console at a real
custom-server upload, that will collide - pick one.

## Supported devices

Verified means someone ran it on that hardware and firmware; inferred means it's
the same firmware and expected to work, but unconfirmed. A report for an
unlisted device (include the console's firmware version) is welcome.

| Device | Brand | Firmware | Status |
|---|---|---|---|
| HP2550 (WS-5000) | Ambient Weather | V2.0.4, patched | Verified, 2026-11 |
| HP2550 | Ecowitt | same V2.0.4 image | Inferred - one brand-selected firmware carries both the `HP2550` and `HP2550_AMB` model strings |
| HP2560 | Ecowitt / Ambient | same image | Inferred - the firmware's device table declares both HP2550 and HP2560 |

The patch is pinned to a specific firmware build (fixed code offsets and a
checksum), so a console on a different version needs the patch re-ported even
when the hardware matches. That, not the brand, is the real constraint.

## How it works

The patch hooks the console's display loop and, a few times a second, reads a
command out of the custom-server Station ID (`BL <v> #<seq>`, where `v` is 0 for
off, 1 for on at the saved level, or 2-100 for a level) and writes a status
report (`BL <on> <level> #<seq>`) into the second upload path. This integration
is the other end: it writes commands and reads status over the port-45000 framed
API, connecting directly to the console's TCP port - that port is the only way
in, so there's nothing to abstract behind a transport layer.
