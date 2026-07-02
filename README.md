# Bitfocus Companion Bridge 

_Custom Home Assistant integration for importing Bitfocus Companion page exports as Home Assistant entities._

This integration can automatically create Home Assistant **button**, **switch**, and **sensor entities** from exported Companion pages. A config flow guides you through the setup process and allows Companion buttons and sensor states to be used in Home Assistant automations.


<img width="1101" height="607" alt="afbeelding" src="https://github.com/user-attachments/assets/659d85e1-e535-4226-8f96-ddbd76ea0134" />
<img width="1423" height="798" alt="afbeelding" src="https://github.com/user-attachments/assets/57f8b225-894b-4b16-a614-81f4e15c323d" />



## Install

Copy `custom_components/bitfocus_companion_bridge` into your Home Assistant `custom_components` directory and restart Home Assistant.

## Features
Main config entry for one Companion instance.
Companion page imports as config subentries.
Imported page entities are grouped under the correct Companion page subentry.
Location-based entity IDs, for example:
sensor.companion_p1r1c3
button.companion_p1r1c3
switch.companion_p1r1c3
Read-only live-state backends:
Surface mode using an integration-owned observer surface.
Subscription API mode using ADD-SUB / SUB-STATE.
Button control through Companion HTTP Remote Control location press.
Switch state derived from the rendered visual state of Companion buttons.

## Manual

**1. Add the Companion device IP**

Add the IP address of your Bitfocus Companion instance.

<img width="1045" height="910" alt="afbeelding" src="https://github.com/user-attachments/assets/fba59146-0acc-4779-93c5-8ad9a50ae1e9" />

**2. Export a Companion page**

Export a Companion page as JSON or YAML.
<img width="1293" height="571" alt="afbeelding" src="https://github.com/user-attachments/assets/a3737b58-934f-41f5-ab8b-c19e576d8609" />

**3. Import the page**

Import the exported page file and set the correct page number.
<img width="1117" height="832" alt="afbeelding" src="https://github.com/user-attachments/assets/1e648a96-a4dc-41c8-a51d-8bfe4de1a807" />

**4A: Surface mode(more secure)**

Set the observer surface page restriction to the correct page.

This is only required when using surface mode and is the more secure option.
<img width="1632" height="820" alt="afbeelding" src="https://github.com/user-attachments/assets/99ee426b-e685-439a-8aee-888f3d0c763c" />

_OR_

**4B: Subscription API**

Enable the Button Subscription API in Companion.

This is only required when using subscription mode.
<img width="816" height="352" alt="afbeelding" src="https://github.com/user-attachments/assets/d63ceec8-7ae9-41d6-a782-39587ae4739d" />

**5. Choose which entities to import**

Choose whether to import sensors, buttons, and optionally buttons as switches.

When importing buttons as switches, the integration attempts to detect Companion buttons with visual feedback and use their rendered visual state as the switch state.
<img width="1078" height="910" alt="afbeelding" src="https://github.com/user-attachments/assets/6c1f06d3-0a09-49c8-bca4-7c491e92fe1c" />

**Re-importing Pages**

You can delete imported entities or re-import pages.

It is recommended to delete existing imported entities before re-importing a page to avoid stale or duplicate entities.
<img width="1138" height="660" alt="afbeelding" src="https://github.com/user-attachments/assets/de68e8e7-22ab-4628-88f9-40dd38af8ca9" />
