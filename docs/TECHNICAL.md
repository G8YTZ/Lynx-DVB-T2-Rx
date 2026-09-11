# Technical Guide - Lynx DVB-T2 Receiver

How it works, why it's DVB-T2 only, why 1.35, 1.7 and 2.0 MHz, and how a £20
TV tuner was talked into receiving amateur-width channels.

## 1. Design goals
* **Cheap to build**: a Raspberry Pi and the Raspberry Pi TV HAT (Sony CXD2880).
* **Robust on a vertical antenna**, in multipath, for newcomers without beams.
* **Narrow enough for 70cm**, and compatible with what's already on the air:
  the Portsdown 4 transmitter (DVB-T2 option) and the Knucker/Ryde receivers.
* **An appliance**: boots to a picture, driven from the TV remote.

## 2. Why DVB-T2 only
### What DVB-T2 brings
* **LDPC + BCH coding** - several dB more robust than DVB-T's convolutional
  and Reed-Solomon coding at the same bit rate, or more bit rate at the same
  robustness.
* **Rotated constellations and time interleaving** - symbols are spread across
  carriers and across the whole T2 frame (about 250 ms here), so a fade or a
  reflection costs a little of many packets, which the LDPC puts right, instead
  of all of a few. In testing, waving the transmit antenna about didn't cost a
  frame.
* **A native narrow mode.** DVB-T2 includes a standard **1.7 MHz** channel,
  supported by broadcast silicon (the TV HAT, cheap USB sticks) as-is. DVB-T's
  narrowest standard channel is 5 MHz.
* **Fast lock**: the TV HAT locks in about **2 seconds**.

### Why not narrowband DVB-T as well
The Portsdown already transmits DVB-T at 1-4 MHz by slowing a standard signal
down, so we tried hard to make the TV HAT receive it. With the patched driver
(section 4) we could override every bandwidth-dependent setting in the Sony
demodulator's DVB-T path: the sample-clock ratio, the bandwidth code, the tuner
IF filter, the timing-offset register (0x7d), symbol sync (0x71) and both notch
filters (0x72, 0x6b).

* A self-test on a real 8 MHz broadcast mux proved the overrides work, and
  showed the **timing offset** is the setting that matters.
* But below Sony's 5 MHz minimum the demodulator never found the signal at
  all - not even OFDM symbol sync - at 4, 3 or 1 MHz, across sweeps of the
  timing offset, filters and bandwidth codes.

Narrowband DVB-T on the CXD2880 is a dead end. DVB-T2, whose narrow processing
Sony did build (for 1.7 MHz), stretches easily - so the receiver is DVB-T2 only.

## 3. Why 1.35, 1.7 and 2.0 MHz
| Setting | Sample rate | Occupied | TS capacity* | TV HAT | Knucker |
|---|---|---|---|---|---|
| **1350 kHz** | 1.542857 MS/s | 1.28 MHz | 0.90 Mb/s | patched driver | no (Ryde) |
| **1700 kHz** | 1.845070 MS/s (131/71 MHz) | 1.54 MHz | 1.16 Mb/s | **stock driver** | Ryde yes |
| **2000 kHz** | 2.285714 MS/s | 1.90 MHz | 1.41 Mb/s | patched driver | Portsdown and Ryde yes |

\* QPSK, LDPC 1/2, guard 1/8, 2K FFT, as sent by the Portsdown DVB-T2 option.

* **1700** is standard DVB-T2 1.7 MHz. It works with every T2 receiver
  without modification, so it's the default and the recommended channel. Note
  that it uses the special T2 clock of 131/71 MHz, not 8/7 x 1.7 MHz. That is
  why some receivers are set to "1614" to fake it; use **1700** here.
* **2000** follows the usual amateur rule (sample rate = 8/7 x bandwidth). It
  carries the most, and the Knucker receives it on both the Portsdown and the
  Ryde.
* **1350** is the narrowest the TV HAT can go (see 4.3). It suits tight spots,
  such as sitting alongside the 437 MHz 333 kS DVB-S2 activity.

Below 1350 the Knucker still works (it has received T2 at 1000 kHz), but the
TV HAT cannot.

**Band plan:** in the UK the 70cm experimental DATV/data segment (436-438 MHz)
is shared with the satellite service, which has priority. Check the current
RSGB band plan and coordinate before operating.

## 4. The driver hack
### 4.1 Background
The Linux driver for the TV HAT's Sony CXD2880 accepts only broadcast
bandwidths (1.7, 5, 6, 7, 8 MHz) and frequencies from 174 MHz. Reading the
driver source showed that, for every mode Sony supports, the demodulator's
**nominal-rate register is simply 192 / (sample rate in MS/s)** - checked
against all five of Sony's tables, bit for bit. The other per-bandwidth
settings are a small bandwidth code and a tuner IF-filter setting. So in
principle a non-standard bandwidth is just a different number in one register.

### 4.2 The patch
`driver/cxd2880-nb` is the kernel driver with two changes:
1. **Lower frequency limit 174 -> 40 MHz.** The front end tunes well below its
   specification: it hears 146.5 MHz, and in principle down to 40 MHz.
2. **Runtime overrides** in `/sys/module/cxd2880/parameters/`, all "off" by
   default, so the driver behaves exactly as stock until one is set:

| Parameter | Effect |
|---|---|
| `nb_fs_hz` | signal sample clock in Hz; the driver writes 192/Fs to the nominal-rate register (0 = off) |
| `nb_if_bw` | tuner IF filter code (0 = 5/6 MHz, 1 = 7, 2 = 8, 3 = 1.7 MHz; -1 = off) |
| `nb_reg4a` | demodulator bandwidth code (-1 = off) |
| `nb_nomi_shift`, `nb_reg15`, `nb_poke_reg`/`nb_poke_val` | experimental: hunting for more nominal-rate range |
| `nb_t_gtdofst`, `nb_t_sst`, `nb_t_notch`, `nb_t_notch2` | DVB-T timing offset, symbol sync and notch overrides (the narrowband DVB-T experiments) |

The receiver tunes the driver's standard 1.7 MHz DVB-T2 mode and, for 1350 and
2000, sets the clock and filter before each tune (and clears them on exit):

| Channel | `nb_fs_hz` | `nb_if_bw` | Register value |
|---|---|---|---|
| 1700 | 0 (stock) | -1 (stock) | 104.06 |
| 2000 | 2285714 | 0 (5/6 MHz filter) | 84.00 |
| 1350 | 1542857 | 3 (1.7 MHz filter) | 124.44 |

### 4.3 The 1.31 MHz wall
Locking was tested at 2.0, 1.75, 1.7, 1.5 and 1.35 MHz - all fine - but
**1.28 MHz and below would not lock**, however strong the signal. The cut-off
is sharp: a register value of 124 (1.35 MHz) locks, 131 (1.28 MHz) doesn't.
The nominal-rate register has only **7 bits for its whole-number part**, so
the largest value is 127.99, which means a sample rate of at least 1.5 MS/s:
a **1.3125 MHz** channel. The experimental parameters above were added to hunt
for a hidden extra bit; none has got past the limit so far.

### 4.4 2m
With the lower limit at 40 MHz the TV HAT hears signals on 146.5 MHz, but the
1.31 MHz wall means it can't use the 1 MHz 2m slot. T2 lock on 2m at 1.35 MHz
or wider has not been tried; for the 1 MHz slot the Knucker is the receiver.

### 4.5 DKMS
The patched module is packaged for DKMS (`driver/install_driver.sh`), so a
kernel update rebuilds it automatically instead of silently reverting to the
stock driver.

## 5. Receiver software
    TV HAT (CXD2880)
       |  /dev/dvb/adapter0
    t2rx (C) --- tune, lock, whole TS to a pipe, status -> /run/t2rx.status
       |  pipe
    t2rxd.py (Python, GLib main loop)
       |- GStreamer: fdsrc ! tsparse ! tsdemux
       |     video: h264parse ! v4l2h264dec ! I420 ! [capssetter] ! gdkpixbufoverlay ! kmssink
       |     audio: decodebin ! audioconvert ! audioresample ! alsasink (HDMI)
       |- OSD: osd.py renders the panel (Pillow) -> gdkpixbufoverlay
       |- status page: osd.py -> fb.py -> /dev/fb0 (when there is no picture)
       |- cec.py: cec-ctl - name, active source, remote keys
       |- control socket /run/t2rx.sock <- t2rx-ctl

### 5.1 t2rx
A small C program using the Linux DVB API directly. It sets up DVB-T2 at the
preset frequency, passes every PID to the DVR device and copies the transport
stream to its output. It retunes every 10 s until lock, and exits with status 2
if lock is lost for `loss_seconds`, so the supervisor can restart the player
cleanly. Twice a second it writes the status file:

    state=LOCK sig=-77.6 cnr=28.3 rate=1.10 mod=QPSK fec=1/2 gi=1/8 fft=2K per=0

`mod`, `fec`, `gi` and `fft` are what the transmitter is signalling (read back
from the demodulator), so the OSD shows the real mode on air.

### 5.2 The player, and three lessons
* **Keyframes and queues.** After lock the decoder must wait for the next
  keyframe, which can be seconds away. With size-limited queues the audio queue
  filled first, blocked the demultiplexer and starved the video: a silent
  deadlock (locked, no picture, no error). The queues are now **time-limited
  and leaky** (drop the oldest beyond 2 s), so nothing can block and the delay
  can't build up.
* **DMABuf frames.** The Pi's decoder offers frames as zero-copy DMA buffers
  (`memory:DMABuf`, `DMA_DRM`) that only the display can take. Anything in
  between (the pixel-shape fix, the OSD overlay) fails to negotiate. The chain
  asks the decoder for ordinary **I420** frames instead.
* **The service name.** `tsdemux` doesn't report the SDT, so a `tsparse`
  ahead of it does. The callsign on the OSD is the transmitted *service name*
  (a Portsdown sends the callsign there), with the provider below it.

**Safe mode:** if the player fails three times in a row while locked, the
receiver drops the OSD and pixel-shape stages and plays the plain chain, and
logs why. A display problem never costs the picture.

### 5.3 Display
* **Status page**: drawn with Pillow straight onto the framebuffer (16- or
  32-bit), with only the live card redrawn each second (cheap on a Zero). The
  text console is unbound from the framebuffer while the receiver owns it.
* **Picture**: `kmssink` puts the video on a hardware overlay plane above the
  status page, scaled by the display hardware at no CPU cost.
* **OSD**: an RGBA panel blended onto the decoded frames at video resolution,
  and re-rendered only when a value changes.
* **Picture shape.** `kmssink` works out the screen's pixel shape from the
  monitor's reported physical size (EDID, in mm), snapped to a few standard
  values. Some monitors report a size that snaps wrongly: one test monitor
  claimed 350 x 190 mm, which snapped to 16:15 PAL pixels and made the picture
  6.7% too narrow. With `scale = fix` the receiver calculates the same
  snapped value and labels the picture with it, so the two cancel. On a monitor
  that reports its size properly this does nothing.

### 5.4 HDMI-CEC
`cec-ctl` registers the receiver as a Playback device with an OSD name (up to
14 characters, default *Lynx DVB-T2 Rx*) so the TV lists it by name, and sends
Image View On + Active Source at start-up (One Touch Play). A monitor process
answers the TV's menu-status, power-status and active-source requests (without
these many TVs won't pass the arrow keys) and turns remote key presses into
actions, ignoring auto-repeat.

### 5.5 Files and control
| Path | What |
|---|---|
| `/opt/t2rx/` | program |
| `/etc/t2rx/t2rx.conf` | settings |
| `/etc/t2rx/presets.conf` | presets 1-9 |
| `/var/lib/t2rx/state.json` | last preset (restored at boot) |
| `/run/t2rx.status` | live tuner status (above) |
| `/var/log/t2rx.log` | log (rotated at 1 MB) |
| `/run/t2rx.sock` | control: `status`, `preset N`, `next`, `prev`, `osd`, `back`, `reload` (use `t2rx-ctl`) |

The service (`t2rx.service`) waits for the TV HAT, runs as root (the display,
CEC monitor and driver overrides need it) and restarts on failure.

### 5.6 Margin
Margin = measured C/N minus the C/N the signalled mode needs, from typical
DVB-T2 figures (e.g. QPSK 1/2 about 2 dB, 16QAM 1/2 about 7 dB, 64QAM 2/3
about 14 dB). Treat it as a guide: green 3 dB or more, amber 0-3 dB, red below.

## 6. Testing and compatibility
* **Receiver:** Raspberry Pi Zero W v1.1, Raspberry Pi OS Lite (32-bit). The
  only board tested so far.
* **Transmitter:** Portsdown 4 with the DVB-T2 option (`dvb_t2_stack`, built on
  the GNU Radio gr-dtv DVB-T2 code), LimeSDR Mini, 436 MHz.
* **Results:** locked at 1.7 MHz with the stock driver and 1.35 / 1.5 / 1.75 /
  2.0 MHz with the patch; C/N 27-29 dB at about -78 dBm; picture and sound on
  HDMI; lock in about 2 s.
* **Alongside:** the Ryde (Knucker) receives the same 1.7 and 2.0 MHz signals,
  but not 1.35. The Portsdown's own Knucker receive locks at 1.0 and 2.0 MHz
  but not 1.7 - under investigation.

## 7. Limitations and next steps
* Pi Zero v1: H.264 only (no H.265 decoder); about 2 s from lock plus the wait
  for the transmitter's next keyframe.
* No audio level meter (PPM) or tuning eye yet - planned for a Pi Zero 2 W
  version.
* Next transmitter: the BATC Muntjac, for a low-cost DVB-T2 transmit path.

## 8. Credits
* DVB-T2 transmit: GNU Radio gr-dtv (Ron Economos W6RZ and contributors), and
  the Portsdown 4 (Dave G8GKQ, Charles G4GUO).
* The project started from a question by Gareth G4XAT about the Pi TV HAT.
* Receiver software and driver patch: Justin G8YTZ. GPLv3; driver GPL-2.0.
