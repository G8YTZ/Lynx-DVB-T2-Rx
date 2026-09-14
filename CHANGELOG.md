# Changelog

## 1.9.29
* An update no longer fails if the tuner program happens to be running by hand -
  capturing a stream while an update installs made the binary unwritable ("text
  file busy"), and the update gave up. The installer now stops it first.
* When an install does fail, the log says why: it picks out the lines that look
  like a complaint rather than printing the tail of apt's output.

## 1.9.28
* The log is now bounded: it trims itself to the most recent entries once it
  reaches half a megabyte. It had no limit at all, which on a receiver left
  running for years on an SD card would eventually have mattered.

## 1.9.27
* **A frozen picture is now noticed.** Until now the receiver could not tell a
  playing picture from a stopped one: the signal stays locked, the stream keeps
  arriving and the player reports no error, so nothing happened. It now counts
  decoded frames, and restarts the player if none arrive for `stall_secs`
  (6 by default; 0 turns it off). Seen on a Pi Zero decoding 1080 when a scene
  change produces a large intra frame.
* After three stalls it will try software decoding, but only on a machine with
  the cores for it - never on a Zero, where that would make matters worse.

## 1.9.26
* An exception in the player's message handler no longer disappears. GLib
  swallows anything thrown in a callback, which left the receiver deaf to
  whatever message came next - including the error it was meant to act on. It is
  now caught, logged with the last few lines of the traceback, and the receiver
  carries on. (A 32-bit Pi was reporting "Python int too large to convert to C
  long" from somewhere in there; this is how we find out where.)
* Control characters are stripped from the log. Text from git, or a service name
  in another character set, was making the log a "binary file" as far as grep
  was concerned.

## 1.9.25
* **Software decoding, for streams the hardware will not take.** A Pi's hardware
  decoder handles progressive H.264 up to level 4.0. A repeater fed from
  broadcast equipment may send 1080i, or 1080p50 at level 4.2 - VK3RTV in
  Australia sends one of each - and neither decodes, which looks exactly like
  "LOCKED - waiting for picture". The receiver now falls back to software after
  two failures (`decoder = auto`), deinterlacing as it goes, and says so in the
  log. `decoder = sw` forces it, `hw` prevents it.
* This needs a Pi 4 or 5 for HD: a Zero or a Pi 3 has not the cores for 1080
  software decoding. It does, though, mean a Pi 5 can now be used - it has no
  hardware H.264 decoder at all.

## 1.9.24
* tsinfo.py: 1088 lines is no longer reported as a problem. H.264 codes in
  16-line macroblocks, so 1080 becomes 1088 and a cropping flag trims it; some
  encoders (SR-Systems boards among them) leave the flag out, and every decoder
  then reports 1088. A Pi decodes it perfectly well.

## 1.9.23
* tsinfo.py now reads the H.264 parameters as well as the codec: picture size,
  profile, level, and whether the coding is interlaced. Those are what decide
  whether a Pi's hardware decoder will take it - interlaced H.264 and anything
  above level 4.0 will not decode, and both look exactly like "LOCKED - waiting
  for picture" from the outside.

## 1.9.22
* tools/tsinfo.py: lists the services in a captured multiplex, their names, and
  the codecs inside each - and says whether a Pi can decode the video. Plain
  Python, no ffmpeg needed, which matters on a Pi with a small card.

## 1.9.21
* **More than one service in a multiplex.** A repeater in Australia transmits two
  programmes on one 7 MHz channel, and the receiver took whichever it found
  first - so it could sit on "LOCKED - waiting for picture" with a perfectly good
  signal. It now reads the PAT and SDT, lists what the multiplex carries, and
  lets you choose: **left and right** on the remote, buttons on the web page, or
  `service = N` in a preset. The choice is remembered in the preset, and the
  service names are shown on the status page.

## 1.9.20
* Fix: the updater runs as root, and its fetches left root-owned files in the
  checkout - after which git refused to work there as the ordinary user
  ("insufficient permission for adding an object to repository database"). The
  updater now hands the checkout back to whoever owns it.

## 1.9.18
* **5, 6, 7 and 8 MHz added**, for the countries where amateur DATV uses
  full-width channels. They are standard DVB-T2 modes needing no driver patch -
  only 1350 and 2000 kHz do. Available as presets and in the tune panel.

## 1.9.17
* **It now shows the frequency the signal is actually on.** A tuner set to a
  narrow bandwidth will happily pull in a carrier some way off the frequency it
  was asked for - a Ryde tuned to 436 locked a transmission on 437, and this
  receiver does the same - so the display was showing where it had looked rather
  than where the signal was. The patched driver now exposes the demodulator's
  measured carrier offset (`nb_offset_khz`), and the OSD shows the true
  frequency, in amber with a note when it differs from the preset by more than
  50 kHz. Requires the patched driver; without it nothing changes.

## 1.9.16
* **The real cause of the update trouble.** The check meant to run once, a minute
  after boot, was written as a repeating timer - so the receiver asked GitHub
  every 60 seconds, which is exactly the sixty-an-hour limit. It exhausted its
  own quota, and every check then quietly found nothing. It now runs once at
  boot and hourly after that.

## 1.9.15
* **Update checks no longer run into GitHub's request limit.** Sixty requests an
  hour are allowed per address, and a busy day of releases used them up - after
  which every check silently found nothing. Now: one conditional request per
  check (a 304 "unchanged" reply costs no quota at all), the limit is recognised
  and waited out rather than hammered, and a failed check says so in the log
  instead of looking like "no update".
* Checks hourly rather than every four hours.

## 1.9.14
* **You can now retune while watching.** The preset list only existed on the
  status page, so with a picture on screen there was no way to see where you
  were in it or to reach "Tune...". BACK now brings up a compact preset list over
  the picture, exactly as it goes back to the channel list on a television;
  Up/Down move through it, Tune... sits at the end, and BACK again hides it.
  Stepping presets with the arrows brings the list up too.

## 1.9.13
* The display decides the resolution. Any forced HDMI mode is removed at install
  (including the ones 1.9.11 and 1.9.12 could add), so the Pi negotiates with the
  monitor or television like any other source. `hdmi` in t2rx.conf now defaults to
  `auto`, and is only there to override a display that chooses badly.

## 1.9.11
* Any forced HDMI mode from an earlier install is removed rather than added to -
  a fixed mode left over from a small monitor was letterboxing the picture on a
  4K television.
* `scale = fix` is migrated to `kms` on update: the pixel-shape correction was
  only ever for monitors that report their physical size wrongly, and on a
  television it makes the picture too wide - which also shows as bars.

## 1.9.10
* Technical Guide: new background section on forward error correction (from CDs
  and hard disks to deep space), what DVB-T does, what DVB-T2 adds and why it is
  better, and how to choose constellation, FEC, guard interval and bandwidth -
  including what the guard interval actually buys, in microseconds and km.

## 1.9.9
* Fix: the preset highlight moved and then jumped back. Between a preset change
  and the retune 0.4 s later, the page was redrawn from the details of the old
  channel; the selection is now always drawn from the current one.

## 1.9.8
* Fix: automatic updates still failed with git's "dubious ownership" - 1.9.2
  wrote the exception into root's global config, which git didn't pick up. Every
  git command now carries the exception itself (`git -c safe.directory=...`),
  which cannot be missed.
* Updates are checked every 4 hours instead of once a day.

## 1.9.7
* **Remote lag fixed properly.** Every keypress redrew the whole 1920x1080
  status page in Python and wrote all 4 MB of it to the framebuffer - on a Pi
  Zero that is most of a second, so the remote felt laggy and the tune panel
  seemed unselectable. Stepping through presets now redraws only the preset
  list, and editing in the tune panel only the panel: about a fifth of the
  pixels and a fraction of the drawing.

## 1.9.6
* **Fix: it was possible to get stuck in the tune panel.** While the panel is
  open it takes every key - and because the panel could be invisible (1.9.5),
  the receiver looked completely dead: the remote did nothing, and the web page
  did nothing either, because web commands went through the same path. Now the
  web page and t2rx-ctl close the panel and act directly, and the panel closes
  itself after 90 s with no keys.

## 1.9.5
* Fix: the screen appeared frozen - changing preset or opening the tune panel
  did nothing visible, though the log showed the keys working. When the player
  stops, kmssink leaves its last frame on the video plane, and that frame
  covered the status page and the tune panel underneath. The video plane is now
  turned off with it.

## 1.9.4
* The update message now stays on screen for the whole install and through the
  restart: the page shows "Installing update vX", the step it has reached
  (Downloading / Unpacking / Installing / Restarting) and "do not switch off".
  The receiver no longer retunes behind it, and the screen isn't blanked when
  it restarts into the new version.

## 1.9.3
* Fix: the tune panel opened but could stay invisible - if the OSD had been
  hidden with BACK, the panel inherited that. It now turns the OSD back on,
  stays up until you leave it, and is drawn both over the picture and on the
  status page.

## 1.9.2
* **Remote lag fixed.** Every question the TV asked (power, menu, active source)
  started a new `cec-ctl` process to answer it; on a single-core Pi a flurry of
  those delayed keypresses by seconds. Keys are now handled before anything
  else, replies are rate-limited to one of each kind every 5 s and never run
  more than one at a time.
* Stepping through presets with the arrows no longer stops and restarts the
  tuner and player on every press - it waits 0.4 s for you to settle.
* OK also responds to the Play key, which some remotes send instead.

## 1.9.1
* Fix: automatic updates failed with git's "dubious ownership" error - the
  receiver runs as root while the checkout belongs to the user. The updater now
  marks the checkout safe for git (and install.sh does too), and forces the
  checkout so local edits can't block an update.

## 1.9
* **Tuning works with arrows and OK alone** - many TV and AV remotes (a Yamaha
  here) have no keypad and never pass the colour keys over CEC. **Tune...** now
  sits after the last preset, so scrolling down reaches it. Then: change each
  digit of the frequency with Up/Down and step through with OK, choose the
  bandwidth with Up/Down, and pick the preset to store it in (or "no"). A
  keypad still works where there is one, and 0 / Red / Menu still jump straight
  to the panel.

## 1.8.4
* Tuning is now on the **0** key: presets are 1-9 and every remote has a 0,
  whereas many TVs and AV receivers keep the colour keys for themselves and
  never send Red over CEC. Red and Menu still work where they are passed
  through. The on-screen footer says so.

## 1.8.3
* README brought up to date: tuning from the remote, the web page and
  webhooks, and automatic updates.

## 1.8.2
* Fix: the update check only asked for the latest GitHub *release*, which does
  not exist until one is published, so pushed tags were never seen. It now
  reads the tag list as well and takes whichever is newer - pushing a tag is
  enough.

## 1.8.1
* Fix: Up and Down were the wrong way round for presets. Up now moves up the
  on-screen preset list (to a lower number) and Down moves down it; CH+/CH-
  still follow the numbers, as on a TV. Bandwidth in the tune panel matches.

## 1.8
* **Tune and store from the remote.** Red (or Menu) opens a tuning panel: type
  the frequency on the keypad (437250 = 437.250 MHz), Up/Down for 1350 / 1700 /
  2000 kHz, OK to tune, then 1-9 to store it in that preset - or BACK to keep
  it just for now. Presets are written to `/etc/t2rx/presets.conf` safely.
* **Control web page and webhooks** on port 8080 (`web`, `web_port`): a small
  status and control page, plus plain URLs for automation - `/status`,
  `/preset/N`, `/next`, `/prev`, `/osd`, `/tune?freq=&bw=`,
  `/save?slot=&freq=&bw=&name=`, `/delete?slot=`, `/update`. No dependencies,
  idle until asked, on a low-priority thread.
* `t2rx-ctl tune | save | delete` as well.

## 1.7
* **Software updates, like a TV.** Checks GitHub for a new release a minute
  after boot and once a day, shows it on the status page, and (with
  `updates = auto`) installs it when nothing is being received - never during a
  transmission. `updates = notify` shows it and waits for OK on the remote;
  `off` disables the check. Manual: `t2rx-ctl update`.

## 1.6
* **Start cushion.** The player starts paused and plays once 1.5 s of stream is
  stored (`start_buffer_ms`). 1.5 started with almost nothing in hand, so the
  sound glitched every few seconds until a cushion built up - or never, with a
  slow transmitter. The cushion covers the T2 demodulator's frame bursts, the
  transmitter's audio/video mux offset (measured up to 1.4 s on a Portsdown)
  and small clock differences. About 1.5 s more delay.
* If a fast transmitter builds the store past `max_buffer_ms` (8 s) the player
  restarts with a normal cushion, so the delay can't grow without limit.
* tools/: `ptsdrift.py` (transmitter clock and audio/video offset against the
  Pi's clock) and `ptsjumps.py` (irregular timestamp steps).

## 1.5
* **Sound gaps fixed, delay kept low.** 1.3's live UDP feed timed the audio by
  arrival, and the T2 demodulator delivers data in frame-sized bursts (about
  250 ms), so the sound output kept resyncing: gaps, then a catch-up. 1.5 goes
  back to the pipe feed (timing from the stream's own timestamps, as r4, which
  had clean sound) and removes the delay differently: the tuner holds the
  stream back until the first H.264 keyframe (`tsgate.h`), so the player never
  stores seconds of undecodable video. Measured joining mid-GOP: delay ~0 s
  beyond the pipeline's own (1.2: 8.4 s, 1.3: 2.0 s).
* Sound buffer back to 200 ms (`audio_buffer_ms`); queues are size-limited and
  never drop data. The installer tidies hand-edited `audio` lines.

## 1.4
* Sound: 1 s audio buffer (`audio_buffer_ms`, was ALSA's 0.2 s) so short CPU
  peaks on a Pi Zero can't break it up; `audio_volume` 0.8 leaves headroom for
  AAC overshoot on loud peaks (clipping crackle).
* OSD redrawn at most every 2 s (`osd_interval`), on a low-priority thread, so
  a redraw can never hold up the audio. Service runs at Nice -5.
* Heatsink recommended (the TV HAT sits over the Pi's chip).

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
