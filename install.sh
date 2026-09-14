#!/bin/bash
# install.sh - Lynx DVB-T2 Receiver: turn a Raspberry Pi + TV HAT into a receiver appliance.
#   ./install.sh            install, start now and at every boot
#   ./install.sh --no-boot  install only (start with: sudo systemctl start t2rx)
# For 1350 and 2000 kHz also run driver/install_driver.sh (patched Sony driver, DKMS).
set -e
cd "$(dirname "$0")"

echo "== Packages"
sudo apt-get install -y --no-install-recommends git >/dev/null 2>&1 || true
sudo apt-get update
sudo apt-get install -y --no-install-recommends gcc libc6-dev v4l-utils dvb-tools \
  python3-gi python3-pil python3-numpy fonts-dejavu-core \
  gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 gir1.2-gst-plugins-bad-1.0 gir1.2-gdkpixbuf-2.0 \
  gstreamer1.0-tools gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \
  gstreamer1.0-libav gstreamer1.0-alsa

echo "== Program -> /opt/t2rx"
sudo mkdir -p /opt/t2rx /etc/t2rx /var/lib/t2rx
# a running tuner program makes its own file unwritable ("text file busy"), which
# happens if someone is capturing a stream by hand while an update installs
sudo pkill -x t2rx 2>/dev/null && sleep 1
gcc -O2 -Wall -o src/t2rx src/t2rx.c
sudo install -m 755 src/t2rx src/t2rxd.py /opt/t2rx/
sudo install -m 644 src/osd.py src/fb.py src/cec.py src/osdplane.py src/update.py src/web.py /opt/t2rx/
sudo install -m 755 src/t2rx-ctl /usr/local/bin/t2rx-ctl

echo "== Settings -> /etc/t2rx (existing files are kept)"
[ -f /etc/t2rx/t2rx.conf ] || sudo install -m 644 config/t2rx.conf /etc/t2rx/
[ -f /etc/t2rx/presets.conf ] || sudo install -m 644 config/presets.conf /etc/t2rx/
# upgrade old defaults (anything you changed yourself is left alone)
sudo sed -i 's/^cec_name = G8YTZ T2 Rx$/cec_name = Lynx DVB-T2 Rx/; s/^osd_timeout = 8$/osd_timeout = 15/' /etc/t2rx/t2rx.conf
grep -q "^osd_plane" /etc/t2rx/t2rx.conf || echo "osd_plane = auto" | sudo tee -a /etc/t2rx/t2rx.conf >/dev/null
# strip any hand-added ALSA options from the audio line (buffer is its own setting)
sudo sed -i 's/^\(audio = [^ ]*\) .*$/\1/' /etc/t2rx/t2rx.conf
sudo sed -i 's/^audio_buffer_ms = 1000$/audio_buffer_ms = 200/' /etc/t2rx/t2rx.conf
# the pixel-shape correction was only ever for monitors that report their size
# wrongly; on a television it makes the picture too wide, which shows as bars
sudo sed -i 's/^scale = fix$/scale = kms/' /etc/t2rx/t2rx.conf
# 1.9.11/1.9.12 could force a mode; the display knows better
sudo sed -i 's/^hdmi = \(prefer1080\|1920x1080@60\)$/hdmi = auto/' /etc/t2rx/t2rx.conf
for kv in "audio_buffer_ms = 200" "audio_volume = 0.8" "osd_interval = 2" \
          "start_buffer_ms = 1500" "max_buffer_ms = 8000" "updates = auto" \
          "web = on" "web_port = 8080" "hdmi = auto" "decoder = auto" \
          "deinterlace = auto" "stall_secs = 6"; do
  grep -q "^${kv%% *}" /etc/t2rx/t2rx.conf || echo "$kv" | sudo tee -a /etc/t2rx/t2rx.conf >/dev/null
done

# the receiver runs as root but the checkout belongs to the user: let git use it
sudo git config --global --add safe.directory "$(pwd)" 2>/dev/null || true
git config --global --add safe.directory "$(pwd)" 2>/dev/null || true

echo "== Service"
sudo systemctl disable --now t2box 2>/dev/null || true      # early test version
sudo install -m 644 config/t2rx.service /etc/systemd/system/t2rx.service
sudo systemctl daemon-reload

echo "== Desktop sound servers off (they grab the HDMI sound)"
sudo systemctl --global mask pipewire.socket pipewire.service pipewire-pulse.socket \
  pipewire-pulse.service wireplumber.service 2>/dev/null || true
systemctl --user stop pipewire.socket pipewire.service pipewire-pulse.socket \
  pipewire-pulse.service wireplumber.service 2>/dev/null || true

echo "== Boot to the receiver, not a desktop"
sudo systemctl set-default multi-user.target
CMD=/boot/firmware/cmdline.txt; [ -f "$CMD" ] || CMD=/boot/cmdline.txt
if [ -f "$CMD" ]; then
  grep -q "consoleblank=0" "$CMD" || sudo sed -i '1 s/$/ consoleblank=0/' "$CMD"

  # HDMI output. A Pi Zero cannot drive 4K, so on a 4K set we ask for 1080p and
  # let the television upscale - otherwise it may pick an odd mode and letterbox.
  # Set hdmi = auto in t2rx.conf to leave the mode to the television instead.
  # The display decides. Any forced mode from an earlier install is removed, so
  # the Pi negotiates with the monitor or television as any other source would.
  HDMI=$(sed -n 's/^hdmi *= *//p' /etc/t2rx/t2rx.conf 2>/dev/null | tr -d ' ')
  HDMI=${HDMI:-auto}
  sudo sed -i '1 s/ *video=HDMI-A-1:[^ ]*//g' "$CMD"
  if [ "$HDMI" != "auto" ]; then
    sudo sed -i "1 s/\$/ video=HDMI-A-1:$HDMI/" "$CMD"
    echo "HDMI mode: forced to $HDMI by t2rx.conf"
  else
    echo "HDMI mode: chosen by the display$( [ -e /sys/class/graphics/fb0/virtual_size ] \
      && echo " (now $(tr ',' 'x' < /sys/class/graphics/fb0/virtual_size))" )"
  fi
fi

if [ "$1" != "--no-boot" ]; then
  sudo systemctl enable t2rx
  sudo systemctl stop lightdm 2>/dev/null || true
  sudo systemctl restart t2rx
fi

echo
[ -e /dev/dvb/adapter0/frontend0 ] && echo "TV HAT: found" || echo "TV HAT: NOT FOUND (check it is fitted; reboot)"
[ -e /sys/module/cxd2880/parameters/nb_fs_hz ] && echo "Patched driver: present (1350 / 1700 / 2000 kHz)" \
  || echo "Patched driver: not installed - 1700 kHz only (run driver/install_driver.sh for 1350 and 2000)"
gst-inspect-1.0 v4l2h264dec >/dev/null 2>&1 && echo "Hardware H.264 decoder: OK" || echo "Hardware H.264 decoder: NOT FOUND"
[ -e /dev/cec0 ] && echo "HDMI-CEC: OK" || echo "HDMI-CEC: not found"
echo
echo "Presets: /etc/t2rx/presets.conf   Settings: /etc/t2rx/t2rx.conf   Log: /var/log/t2rx.log"
echo "Control: t2rx-ctl status | preset N | tune MHz kHz | save N [name] | delete N | update"
IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo "Web:     http://${IP:-<this-pi>}:8080/"
