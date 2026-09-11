# Changelog

## 1.3
* **Much less delay.** The tuner now hands the stream to the player over UDP on
  localhost, a live source. Before, the player stored everything received while
  it waited for the first keyframe and then played from the start of that store,
  so the keyframe wait became permanent delay. Measured joining a stream mid-GOP
  (8 s to the next keyframe): end-to-end delay down from 8.4 s to 2.0 s.

## 1.2
* **OSD on its own display plane.** The video now goes to the screen zero-copy
  (straight from the hardware decoder) and the OSD sits on a second hardware
  plane, mixed by the display hardware - no per-frame CPU. On a Pi Zero W the
  1.1 method (copy every frame, blend the OSD) used ~80% CPU and starved the
  audio; the plane method fixes the sound break-up. Falls back to blending if
  the display has no spare plane (`osd_plane = off` forces the old method).
* OSD redrawn only when a shown value changes, at most once a second, and
  drawn at the monitor's full resolution (sharper).
* Default `scale = kms` (the shape fix only applies to the blended OSD).
* Installer masks the desktop sound servers (PipeWire/WirePlumber), which could
  grab the HDMI sound.
* tools/: `zero_bench.sh` (CPU of each video path) and `osd_plane_test.py`.

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
