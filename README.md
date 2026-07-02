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


<img width="1293" height="571" alt="afbeelding" src="https://github.com/user-attachments/assets/a3737b58-934f-41f5-ab8b-c19e576d8609" />



<img width="1101" height="607" alt="afbeelding" src="https://github.com/user-attachments/assets/659d85e1-e535-4226-8f96-ddbd76ea0134" />


<img width="1632" height="820" alt="afbeelding" src="https://github.com/user-attachments/assets/99ee426b-e685-439a-8aee-888f3d0c763c" />


<img width="816" height="352" alt="afbeelding" src="https://github.com/user-attachments/assets/d63ceec8-7ae9-41d6-a782-39587ae4739d" />


<img width="1138" height="660" alt="afbeelding" src="https://github.com/user-attachments/assets/de68e8e7-22ab-4628-88f9-40dd38af8ca9" />


<img width="1117" height="832" alt="afbeelding" src="https://github.com/user-attachments/assets/1e648a96-a4dc-41c8-a51d-8bfe4de1a807" />


<img width="1078" height="910" alt="afbeelding" src="https://github.com/user-attachments/assets/6c1f06d3-0a09-49c8-bca4-7c491e92fe1c" />

<img width="1423" height="798" alt="afbeelding" src="https://github.com/user-attachments/assets/57f8b225-894b-4b16-a614-81f4e15c323d" />

