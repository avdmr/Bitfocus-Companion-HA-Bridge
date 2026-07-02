# Bitfocus Companion Bridge 

Custom Home Assistant integration POC for importing Bitfocus Companion page exports as location-based Home Assistant entities.

## Install

Copy `custom_components/bitfocus_companion_bridge` into your Home Assistant `custom_components` directory and restart Home Assistant.

## What this component covers

- Main config entry for one Companion instance.
- Companion page imports as config subentries.
- Page entities are grouped under the correct Companion page subentry.
- Location-based entity identity, for example:
  - `sensor.companion_p1r1c3`
  - `button.companion_p1r1c3`
  - `switch.companion_p1r1c3`
- Read-only live-state backends:
  - Surface mode using an integration-owned observer surface.
  - Subscription API mode using `ADD-SUB` / `SUB-STATE`.
- Button control via Companion HTTP Remote Control location press.
- Switch state derived from Companion rendered visual state.

