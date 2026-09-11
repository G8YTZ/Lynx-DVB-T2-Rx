#!/bin/bash
# install_driver.sh - patched Sony CXD2880 (TV HAT) driver via DKMS.
# Adds 1350 and 2000 kHz DVB-T2 (runtime overrides, off by default = stock
# behaviour) and a 40 MHz lower frequency limit. DKMS rebuilds it
# automatically when the kernel is updated. Build takes ~15 min on a Pi Zero.
set -e
cd "$(dirname "$0")"
V=4
sudo apt-get install -y dkms
sudo apt-get install -y "linux-headers-$(uname -r)" || {
  echo "Could not install linux-headers-$(uname -r); install your kernel's headers and re-run"; exit 1; }
sudo rm -f "/lib/modules/$(uname -r)/updates/cxd2880.ko"        # earlier hand-installed copy
sudo dkms remove -m cxd2880-nb -v $V --all >/dev/null 2>&1 || true
sudo rm -rf /usr/src/cxd2880-nb-$V && sudo cp -r cxd2880-nb /usr/src/cxd2880-nb-$V
sudo dkms install -m cxd2880-nb -v $V
echo "Installed. Reboot, then check: ls /sys/module/cxd2880/parameters/ (should list nb_fs_hz)"
