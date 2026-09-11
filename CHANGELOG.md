# Changelog

## 1.1
* Fix: no picture on the Pi - the hardware decoder's DMABuf frames couldn't pass
  the pixel-shape fix and OSD stages; the chain now asks for I420 frames.
* Safe mode: after three player failures while locked, play without the OSD.
* Full GStreamer error text in the log.
* Full OSD shown for 15 s (was 8).
* Renamed Lynx DVB-T2 Receiver; CEC name "Lynx DVB-T2 Rx".
* Long status messages fitted inside the status card.

## 1.0
* First release: boot-to-picture receiver, Lynx-style OSD and status page,
  HDMI-CEC remote and identification, presets, 1350 / 1700 / 2000 kHz,
  patched driver as a DKMS package.
