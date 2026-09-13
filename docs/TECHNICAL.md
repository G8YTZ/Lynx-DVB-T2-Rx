# Technical Guide - Lynx DVB-T2 Receiver

How it works, why it's DVB-T2 only, why 1.35, 1.7 and 2.0 MHz, and how a £20
TV tuner was talked into receiving amateur-width channels.

## 1. Design goals
* **Cheap to build**: a Raspberry Pi and the Raspberry Pi TV HAT (Sony CXD2880).
* **Robust on a vertical antenna**, in multipath, for newcomers without beams.
* **Narrow enough for 70cm**, and compatible with what's already on the air:
  the Portsdown 4 transmitter (DVB-T2 option) and the Knucker/Ryde receivers.
* **An appliance**: boots to a picture, driven from the TV remote.

## 2. Background: FEC, DVB-T and DVB-T2
Skip this if you already know it. It is here because the settings on the
transmitter - FEC, constellation, guard interval - are the difference between a
picture that survives a fluttering path and one that breaks up, and they are
easier to choose if you know what they do.

### 2.1 What forward error correction is
Send the bits you want, plus some carefully chosen extra bits, and the receiver
can work out what the original was even when some of it arrived wrong. No
retransmission, no return path - hence *forward*. The cost is that some of your
bit rate carries the check bits instead of the picture. "FEC 1/2" means half of
what you transmit is your data; "3/4" means three quarters, so more picture and
less protection.

It is everywhere, usually invisibly:

* **The compact disc** (1982) used cross-interleaved Reed-Solomon coding, with
  the data spread across the disc so that a scratch destroys a little of many
  blocks rather than all of a few - the interleaving mattered as much as the
  coding. That is why a scratched CD still plays.
* **Hard disks and SSDs** correct errors on every read; flash memory could not
  work at all without strong LDPC coding.
* **QR codes** use Reed-Solomon, which is why one still scans with a coffee
  ring across it.
* **Deep space**: Voyager used convolutional coding with Viterbi decoding, then
  Reed-Solomon on top - the same combination broadcast television later used.
* **Mobile phones, Wi-Fi, satellite links, DAB, DVB** - all of it.

Two ideas recur, and both are in DVB-T2:
* **Interleaving**: spread the data out in time and frequency, so a burst of
  interference damages a little of many blocks instead of destroying a few.
* **Soft decision**: the demodulator reports not just "this looks like a 1" but
  how confident it is, and the decoder uses that confidence. It is worth
  several dB, and it is why modern codes get so close to the theoretical limit.

### 2.2 What DVB-T does
DVB-T (1997) carries the picture on thousands of low-rate carriers at once
(OFDM) instead of one fast one. Each carrier is slow enough that a reflection
arriving late overlaps only slightly, and a **guard interval** - a copy of the
end of each symbol placed in front of it - absorbs what remains. Reflections
stop being a problem to be equalised away and become extra signal.

Its error correction is a 1990s pair: convolutional coding with Viterbi
decoding inside, Reed-Solomon outside to clean up what is left. Good, and by
the standards of the time excellent, but a few dB short of what is possible.

### 2.3 What DVB-T2 adds
DVB-T2 (2009) keeps OFDM and replaces almost everything else. It carries about
50% more data in the same channel at the same robustness - or the same data
with several dB more margin, which is what matters to us.

* **LDPC + BCH coding.** Low-density parity-check codes with soft-decision
  decoding come within about 1 dB of the Shannon limit, where DVB-T's coding is
  perhaps 3 dB away. This is the single biggest gain.
* **Rotated constellations.** The constellation is rotated and the I and Q
  components are transmitted separately, at different times and on different
  carriers. If a fade destroys one, the other still carries enough to recover
  the symbol - worth a lot on a fading path, and free.
* **Time interleaving across a whole frame.** DVB-T interleaves across a
  symbol; DVB-T2 spreads a FEC block across an entire T2 frame, up to 250 ms
  here. A flutter that would wipe out a run of symbols instead costs a few bits
  from many blocks, which the LDPC repairs. This is why waving the transmit
  antenna about does not break the picture.
* **More pilot patterns**, chosen to suit the guard interval, so fewer carriers
  are spent on reference signals.
* **A native 1.7 MHz mode**, which is what makes narrowband amateur DATV
  possible with broadcast silicon (DVB-T's narrowest standard channel is 5 MHz).

The cost is decoding effort - LDPC decoding is far more work than Viterbi -
which is why DVB-T2 arrived when silicon could afford it, and why a £20 TV HAT
doing it at all is remarkable.

### 2.4 Choosing the settings
| Setting | What it trades |
|---|---|
| **Constellation** QPSK / 16QAM / 64QAM | Bits per symbol against robustness. QPSK carries 2 bits and needs about 2 dB C/N at FEC 1/2; 64QAM carries 3 times as much and needs about 11 dB more. On a weak path, QPSK. |
| **FEC** 1/2 ... 5/6 | Protection against bit rate. 1/2 is the most robust, 5/6 the fastest. Each step up costs roughly 1-2 dB of margin. |
| **Guard interval** 1/32 ... 1/4 | Echo tolerance against bit rate. Longer guard = longer echoes tolerated, fewer bits. |
| **Bandwidth** | Bit rate against spectrum - and against noise: doubling the bandwidth doubles the noise the receiver takes in, so it costs 3 dB. |

**Guard interval, in numbers.** The guard is a fraction of the symbol, and a
2K symbol at 1.7 MHz is about 1.1 ms, so:

| Guard | Length at 1.7 MHz | Echo tolerated | Cost |
|---|---|---|---|
| 1/32 | ~35 us | ~10 km path difference | - |
| 1/16 | ~69 us | ~21 km | 3% of the bit rate |
| 1/8 | ~139 us | ~42 km | 9% |
| 1/4 | ~278 us | ~83 km | 20% |

**1/8 is more robust than 1/16** - it tolerates twice the echo delay - but it
only helps against *echoes*. It does nothing for a weak signal; if you are
short of margin rather than fighting reflections, spend the bits on a lower FEC
instead. At these bandwidths even 1/32 copes with a 10 km echo, so 1/16 is a
sensible default and 1/8 is worth it in bad multipath.

The OSD's **margin** figure (6.7) is the practical guide: it shows how many dB
of C/N you have in hand for the mode actually being transmitted.

## 3. Why DVB-T2 only
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
(section 5) we could override every bandwidth-dependent setting in the Sony
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

## 4. Why 1.35, 1.7 and 2.0 MHz
| Setting | Sample rate | Occupied | TS capacity* | TV HAT | Knucker |
|---|---|---|---|---|---|
| **1350 kHz** | 1.542857 MS/s | 1.28 MHz | 0.90 Mb/s | patched driver | no (Ryde) |
| **1700 kHz** | 1.845070 MS/s (131/71 MHz) | 1.54 MHz | 1.16 Mb/s | **stock driver** | Ryde yes |
| **2000 kHz** | 2.285714 MS/s | 1.90 MHz | 1.41 Mb/s | patched driver | Portsdown and Ryde yes |

\* QPSK, LDPC 1/2, guard 1/8, 2K FFT, as sent by the Portsdown DVB-T2 option.

* **1700** is standard DVB-T2 1.7 MHz. It works with every T2 receiver
  without modification, so it's the default and the recommended channel. Its
  sample clock is 131/71 MHz (1.845 MS/s), not 8/7 x 1.7 MHz. Under the usual
  amateur convention (sample rate = 8/7 x bandwidth) that clock is a
  **1.614 MHz** signal, which is why the same standard channel is also quoted
  as 1614 - for example in the Ryde's release notes. **1700 here and 1614
  there are the same transmission.** A modulator that applies the 8/7 rule
  gets within 0.03% of the standard clock when set to 1614.
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

## 5. The driver hack
### 5.1 Background
The Linux driver for the TV HAT's Sony CXD2880 accepts only broadcast
bandwidths (1.7, 5, 6, 7, 8 MHz) and frequencies from 174 MHz. Reading the
driver source showed that, for every mode Sony supports, the demodulator's
**nominal-rate register is simply 192 / (sample rate in MS/s)** - checked
against all five of Sony's tables, bit for bit. The other per-bandwidth
settings are a small bandwidth code and a tuner IF-filter setting. So in
principle a non-standard bandwidth is just a different number in one register.

### 5.2 The patch
`driver/cxd2880-nb` is the kernel driver with two changes:
1. **Lower frequency limit 174 -> 40 MHz.** The front end tunes well below its
   specification: it hears 146.5 MHz, and in principle down to 40 MHz.
2. **Runtime overrides** in `/sys/module/cxd2880/parameters/`, all "off" by
   default, so the driver behaves exactly as stock until one is set:

| Parameter | Effect |
|---|---|
| `nb_fs_hz` | signal sample clock in Hz; the driver writes 192/Fs to the nominal-rate register (0 = off) |
| `nb_if_bw` | tuner IF filter code (0 = 5/6 MHz, 1 = 7, 2 = 8, 3 = 1.7 MHz; -1 = off) |
| `nb_offset_khz` | read-only: the carrier offset the demodulator measured, so the receiver can show where the signal really is |
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

### 5.3 The 1.31 MHz wall
Locking was tested at 2.0, 1.75, 1.7, 1.5 and 1.35 MHz - all fine - but
**1.28 MHz and below would not lock**, however strong the signal. The cut-off
is sharp: a register value of 124 (1.35 MHz) locks, 131 (1.28 MHz) doesn't.
The nominal-rate register has only **7 bits for its whole-number part**, so
the largest value is 127.99, which means a sample rate of at least 1.5 MS/s:
a **1.3125 MHz** channel. The experimental parameters above were added to hunt
for a hidden extra bit; none has got past the limit so far.

### 5.4 2m
With the lower limit at 40 MHz the TV HAT hears signals on 146.5 MHz, but the
1.31 MHz wall means it can't use the 1 MHz 2m slot. T2 lock on 2m at 1.35 MHz
or wider has not been tried; for the 1 MHz slot the Knucker is the receiver.

### 5.5 DKMS
The patched module is packaged for DKMS (`driver/install_driver.sh`), so a
kernel update rebuilds it automatically instead of silently reverting to the
stock driver.

## 6. Receiver software
    TV HAT (CXD2880)
       |  /dev/dvb/adapter0
    t2rx (C) --- tune, lock, hold back to the first keyframe, TS to a pipe, status
       |  pipe
    t2rxd.py (Python, GLib main loop)
       |- GStreamer: fdsrc ! tsparse ! tsdemux
       |     video: h264parse ! v4l2h264dec ! kmssink (zero-copy, video plane)
       |     audio: decodebin ! audioconvert ! audioresample ! alsasink (HDMI)
       |- OSD: osd.py renders the panel (Pillow) -> osdplane.py -> OSD plane
       |- status page: osd.py -> fb.py -> /dev/fb0 (when there is no picture)
       |- cec.py: cec-ctl - name, active source, remote keys
       |- control socket /run/t2rx.sock <- t2rx-ctl

### 6.1 t2rx
A small C program using the Linux DVB API directly. It sets up DVB-T2 at the
preset frequency, passes every PID to the DVR device and copies the transport
stream to its output (stdout; `-u` sends UDP instead). Until the first H.264
keyframe it passes only the tables (PAT, PMT, SDT): the **keyframe gate**
(`tsgate.h`) reads the PAT and PMT to find the video PID, holds each video PES
start, and opens at the start of the first PES that carries an SPS. It retunes every 10 s until lock, and exits with status 2
if lock is lost for `loss_seconds`, so the supervisor can restart the player
cleanly. Twice a second it writes the status file:

    state=LOCK sig=-77.6 cnr=28.3 rate=1.10 mod=QPSK fec=1/2 gi=1/8 fft=2K per=0

`mod`, `fec`, `gi` and `fft` are what the transmitter is signalling (read back
from the demodulator), so the OSD shows the real mode on air.

### 6.2 The player, and four lessons
* **Delay versus clean sound.** Fed through a pipe, the player queued everything
  that arrived while the decoder waited for the first keyframe, then played from
  the start of that queue: the keyframe wait became permanent delay (8.4 s
  measured). Making it a live source (UDP, 1.3) cut that to 2 s but broke the
  sound: the T2 demodulator delivers data in bursts about a T2 frame (250 ms)
  apart, a live source timestamps by arrival, and the sound output kept
  resyncing - a gap, then a catch-up. The answer (1.5) is a pipe, timed by the
  stream's own timestamps, plus the tuner's keyframe gate, so there is no
  backlog to play late. Delay is now little more than the pipeline's own. A
  transmitter keyframe every 1-2 s shortens the wait for the first picture.
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

### 6.2a Transmitter timing, and the start cushion
A receiver that plays in real time depends on the transmitter's timestamps
keeping real time too (MPEG allows +/-30 ppm for the programme clock). Measured
from the air with `tools/ptsdrift.py` against the Pi's network-set clock:

| Portsdown 4 source | Programme clock | Notes |
|---|---|---|
| Test card | -13 ppm | fine |
| ATEM 1080p25 via Elgato Cam Link | about -15 ppm | fine |
| Logitech C920 webcam, 15 fps | **-19,600 ppm (2% slow)** | picture and sound together; frames stamped at a nominal 1/15 s while the camera delivers fewer |

A 2%-slow stream starves any player that keeps real time (VLC-based receivers
such as the Ryde follow it, with the sound 2% flat). This is a transmitter
fault, independent of the modulation (DVB-S2 too); a transmitter should take
its timestamps from a real clock - ideally GPS-disciplined.

Even a good transmitter needs a **start cushion**: the T2 demodulator delivers
data in bursts about 250 ms apart, and a Portsdown multiplexes its audio up to
~1.4 s behind the matching video. So the player starts paused and plays once
`start_buffer_ms` (1.5 s) is stored; if a fast transmitter builds the store
past `max_buffer_ms` (8 s) the player restarts.

### 6.3 Sound on a single core
The Pi Zero W has one CPU core, so anything that briefly takes it (an OSD
redraw, a background job) can leave the sound card empty for a moment - a
break-up you mostly hear on speech. Three defences:
* a **sound buffer** (`audio_buffer_ms`, 200 ms; raise it if needed - the
  picture is delayed by the same amount to keep lip-sync). Measured on the Zero,
  the sound card never ran low: the gaps heard in 1.3/1.4 came from resyncs,
  not starvation (6.2);
* the OSD is redrawn at most every 2 s, on a thread that lowers its own
  priority (nice 15), while the service runs at nice -5;
* **headroom**: `audio_volume` 0.8 before conversion to 16-bit, because AAC
  decoding can overshoot full scale on loud peaks and clip.
Measured: the Zero ran at 46 C with no throttling (`vcgencmd get_throttled` =
0x0), so heat was not the cause; a heatsink is still sensible under the HAT.

### 6.3a Display
* **Status page**: drawn with Pillow straight onto the framebuffer (16- or
  32-bit), with only the live card redrawn each second (cheap on a Zero). The
  text console is unbound from the framebuffer while the receiver owns it.
* **Picture**: `kmssink` puts the decoder's frames straight onto a hardware
  overlay plane (zero-copy), above the status page, scaled by the display
  hardware at no CPU cost.
* **OSD on its own plane** (`osdplane.py`): the receiver opens the DRM device
  itself, hands the same file descriptor to `kmssink` (so both work under one
  DRM master), gives `kmssink` the lower overlay plane and puts the OSD - an
  ARGB8888 dumb buffer, premultiplied alpha - on a higher one. The display
  hardware (VC4 HVS) mixes them; the CPU only works when the panel is redrawn
  (only when a shown value changes, at most once a second).

  Why: blending the OSD onto every frame on a single-core Pi Zero W means
  copying each frame out of the decoder and drawing on it. Measured with
  `tools/zero_bench.sh` on the same recording: ordinary frames 49% CPU,
  + blended OSD 79% - enough to starve the audio. Zero-copy video with the OSD
  on a plane measured 51-66% *while redrawing twice a second on purpose*.
  If no spare plane is found the receiver falls back to blending
  (`osd_plane = off` forces it).
* **Picture shape** (blended mode only). `kmssink` works out the screen's pixel shape from the
  monitor's reported physical size (EDID, in mm), snapped to a few standard
  values. Some monitors report a size that snaps wrongly: one test monitor
  claimed 350 x 190 mm, which snapped to 16:15 PAL pixels and made the picture
  6.7% too narrow. With `scale = fix` (and `osd_plane = off`) the receiver
  calculates the same snapped value and labels the picture with it, so the two
  cancel. Zero-copy frames can't be relabelled, so on such a monitor the plane
  mode shows the picture slightly narrow; on a monitor that reports its size
  properly (most TVs) there is no difference.

### 6.4 HDMI-CEC
`cec-ctl` registers the receiver as a Playback device with an OSD name (up to
14 characters, default *Lynx DVB-T2 Rx*) so the TV lists it by name, and sends
Image View On + Active Source at start-up (One Touch Play). A monitor process
answers the TV's menu-status, power-status and active-source requests (without
these many TVs won't pass the arrow keys) and turns remote key presses into
actions, ignoring auto-repeat.

### 6.5 Files and control
| Path | What |
|---|---|
| `/opt/t2rx/` | program |
| `/etc/t2rx/t2rx.conf` | settings |
| `/etc/t2rx/presets.conf` | presets 1-9 |
| `/var/lib/t2rx/state.json` | last preset (restored at boot) |
| `/run/t2rx.status` | live tuner status (above) |
| `/var/log/t2rx.log` | log (rotated at 1 MB) |
| `/run/t2rx.sock` | control: `status`, `preset N`, `next`, `prev`, `osd`, `back`, `reload`, `update` (use `t2rx-ctl`) |

The service (`t2rx.service`) waits for the TV HAT, runs as root (the display,
CEC monitor and driver overrides need it) and restarts on failure. The
installer masks PipeWire/WirePlumber: on images that have them they grab the
HDMI sound device ("Device or resource busy").

### 6.5a Web control
`web.py` is Python's own `http.server` on a low-priority daemon thread: no
framework, no dependencies, and idle (a socket waiting) until someone connects,
so it costs nothing while receiving. Every request is answered from the
receiver's state and turned into the same actions the remote uses, applied on
the main loop via `GLib.idle_add`. Unauthenticated by design, for a home LAN;
`web = off` disables it. The page itself is one small HTML file with no images
that polls `/status` every 2 s.

Preset edits rewrite `/etc/t2rx/presets.conf` through a temporary file and
`os.replace`, so an interrupted write can't leave it empty or half-written.

### 6.6 Updates
Checks run once a minute after boot, and hourly after that. (A GLib timer keeps
repeating while its callback returns True, so the boot check must return False -
as first written it asked GitHub every 60 seconds, which is exactly the sixty
unauthenticated requests an hour GitHub allows per IP address: the receiver
exhausted its own quota, and every check then quietly found nothing.)

`update.py` asks GitHub for the tag list (a pushed tag is enough; no release need
be published) with an `If-None-Match` header, so an unchanged answer comes back
304 and costs nothing against the sixty-requests-an-hour limit; the limit itself
is recognised and waited out, and failures are logged rather than passed off as
"no update". It takes the newest (a minute after
boot, then daily, in a background thread so nothing stalls if the network is
slow or absent) and compares it with the running version. With `updates = auto`
the receiver installs it only while nothing is being received - never during a
transmission - by `git fetch`, `git checkout <tag>` and `install.sh --no-boot`
in the checkout it was installed from, then exits so systemd restarts it into
the new version. `notify` waits for OK on the remote; `off` disables it.

### 6.7 Margin
Margin = measured C/N minus the C/N the signalled mode needs, from typical
DVB-T2 figures (e.g. QPSK 1/2 about 2 dB, 16QAM 1/2 about 7 dB, 64QAM 2/3
about 14 dB). Treat it as a guide: green 3 dB or more, amber 0-3 dB, red below.

## 7. Testing and compatibility
* **Receiver:** Raspberry Pi Zero W v1.1, Raspberry Pi OS Lite (32-bit). This
  is the only board tested; nothing has been tried on other Pis yet.
* **Transmitter:** Portsdown 4 with the DVB-T2 option (`dvb_t2_stack`, built on
  the GNU Radio gr-dtv DVB-T2 code), LimeSDR Mini, 436 MHz.
* **Results:** locked at 1.7 MHz with the stock driver and 1.35 / 1.5 / 1.75 /
  2.0 MHz with the patch; C/N 27-29 dB at about -78 dBm; picture and sound on
  HDMI; lock in about 2 s.
* **Alongside:** the Ryde (Knucker) receives the same 1.7 and 2.0 MHz signals,
  but not 1.35. The Portsdown's own Knucker receive locks at 1.0 and 2.0 MHz
  but not 1.7 - under investigation.

## 8. Limitations and next steps
* Pi Zero v1: H.264 only (no H.265 decoder); about 2 s from lock plus the wait
  for the transmitter's next keyframe.
* No audio level meter (PPM) or tuning eye yet - planned for a Pi Zero 2 W
  version.
* Next transmitter: the BATC Muntjac, for a low-cost DVB-T2 transmit path.

## 9. Credits
* DVB-T2 transmit: GNU Radio gr-dtv (Ron Economos W6RZ and contributors), and
  the Portsdown 4 (Dave G8GKQ, Charles G4GUO).
* The project started from a question by Gareth G4XAT about the Pi TV HAT.
* Receiver software and driver patch: Justin G8YTZ. GPLv3; driver GPL-2.0.
